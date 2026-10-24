"""Read-only local ZIP review and explicitly approved, nonexecuting staging.

No network, launcher, emulator or imported game code is executed. Installation
only copies a hash-pinned reviewed archive into a private content root. Existing
versions are retained as rollback directories, never silently deleted.
"""
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
import zipfile

from ...private_credentials import CredentialError, _open_directory
from ..contracts import FeatureError, valid_id


MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_CENTRAL_BYTES = 4 * 1024 * 1024
MAX_RATIO = 200
_WEB = frozenset({".html", ".htm", ".js", ".mjs", ".svg"})
_SUFFIXES = _WEB | frozenset({".css", ".json", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".woff", ".woff2", ".ttf", ".otf", ".txt", ".md", ".csv", ".map",
    ".wav", ".ogg", ".mp3", ".mp4", ".webm"})
_KNOWN = (Path("/home/bgh/Downloads/BMO_Games_v3.zip"),
          Path("/home/bgh/Downloads/BMO_Tetris.html"), Path("/home/bgh/Downloads/Tetris.html"))
_MANIFEST = ".bmo-game-install.json"


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveReview:
    source_path: Path = field(repr=False)
    sha256: str
    archive_bytes: int
    entries: tuple[ArchiveEntry, ...]
    total_bytes: int
    active_web_content: bool
    license_documents: tuple[str, ...]

    def public(self):
        return {"status": "AVAILABLE_FOR_REVIEW", "sha256": self.sha256,
                "archive_bytes": self.archive_bytes, "unpacked_bytes": self.total_bytes,
                "files": [{"name": entry.name, "bytes": entry.size, "sha256": entry.sha256}
                          for entry in self.entries],
                "active_web_content": self.active_web_content,
                "license_documents": list(self.license_documents), "license_review": "NOT_RUN",
                "runtime": "NOT_RUN", "content_executed": False}


def inventory_known_assets(paths=None):
    """Stat only explicitly known paths; no recursion, content read or imports."""
    rows = []
    for index, value in enumerate(_KNOWN if paths is None else paths):
        try:
            info = Path(value).lstat()
            safe = stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_ARCHIVE_BYTES
            rows.append({"candidate": index + 1, "status": "AVAILABLE_FOR_REVIEW" if safe else "UNVERIFIED",
                         "bytes": info.st_size if safe else None})
        except (OSError, TypeError, ValueError):
            rows.append({"candidate": index + 1, "status": "ASSET_PENDING", "bytes": None})
    return {"status": "AVAILABLE_FOR_REVIEW" if any(row["status"] == "AVAILABLE_FOR_REVIEW" for row in rows)
            else "ASSET_PENDING", "candidates": rows, "installed_games_verified": False,
            "runtime": "NOT_RUN", "content_executed": False}


def _safe_name(info):
    name = info.filename
    original = getattr(info, "orig_filename", name)
    if (name != original or type(name) is not str or not name or len(name) > 512
            or name.startswith("/") or any(char in name for char in "\\\0:%?#;$|&<>()")
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise FeatureError("unsafe_game_archive")
    if name.endswith("/"):
        name = name[:-1]
    parts = name.split("/")
    if (not name or any(part in ("", ".", "..") or len(part) > 160 for part in parts)
            or any(part.lower() in (".git", "credentials.json", _MANIFEST) or part.lower().startswith(".env")
                   for part in parts)):
        raise FeatureError("unsafe_game_archive")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if (info.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
            or stat.S_IMODE(mode) & 0o111 and not info.is_dir()
            or kind == stat.S_IFDIR and not info.is_dir()):
        raise FeatureError("unsafe_game_archive")
    if not info.is_dir():
        base = parts[-1].upper()
        license_file = base.startswith(("LICENSE", "COPYING", "NOTICE"))
        suffix = Path(name).suffix.lower()
        if suffix not in _SUFFIXES and not (license_file and suffix in ("", ".license")):
            raise FeatureError("game_file_unsupported")
    return name


def _open_archive(source):
    descriptor = None
    try:
        source = Path(source)
        if not source.is_absolute() or ".." in source.parts:
            raise FeatureError("unsafe_game_archive")
        # Open all parents without following symlinks, without requiring a
        # user's Downloads directory to use private installation permissions.
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for part in source.parts[1:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=parent)
                os.close(parent); parent = following
            descriptor = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                 dir_fd=parent)
        finally:
            os.close(parent)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_ARCHIVE_BYTES:
            raise FeatureError("unsafe_game_archive")
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        return stream, info
    except FeatureError:
        raise
    except (OSError, TypeError, ValueError):
        raise FeatureError("game_asset_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _archive_hash(stream):
    digest, total = hashlib.sha256(), 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ARCHIVE_BYTES:
            raise FeatureError("unsafe_game_archive")
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def _unchanged(stream, before):
    after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise FeatureError("game_asset_changed")


def _extra_without_zip64(raw):
    offset = 0
    while offset < len(raw):
        if offset + 4 > len(raw):
            raise FeatureError("unsafe_game_archive")
        tag, size = struct.unpack_from("<HH", raw, offset)
        offset += 4
        if tag == 1 or offset + size > len(raw):
            raise FeatureError("unsafe_game_archive")
        offset += size


def _preflight_archive(stream):
    """Bound central parsing before ZipFile allocates its ZipInfo list.

    Only single-disk, non-ZIP64 stored/deflated archives are admitted. The
    largest preflight buffer is the bounded EOCD tail (65,577 bytes), rather
    than an attacker-declared directory or number of entry objects.
    """
    reject = lambda: FeatureError("unsafe_game_archive")
    try:
        stream.seek(0, os.SEEK_END)
        archive_size = stream.tell()
        if not 22 <= archive_size <= MAX_ARCHIVE_BYTES:
            raise reject()
        tail_size = min(archive_size, 22 + 65535 + 20)
        stream.seek(archive_size - tail_size)
        tail = stream.read(tail_size)
        end_index = tail.rfind(b"PK\x05\x06")
        if end_index < 0 or end_index + 22 > len(tail):
            raise reject()
        end = struct.unpack("<4s4H2IH", tail[end_index:end_index + 22])
        _, disk, central_disk, disk_count, count, central_size, central_offset, comment_size = end
        end_offset = archive_size - tail_size + end_index
        if (disk != 0 or central_disk != 0 or disk_count != count or not 1 <= count <= MAX_ENTRIES
                or count == 0xffff or central_size == 0xffffffff or central_offset == 0xffffffff
                or not count * 46 <= central_size <= MAX_CENTRAL_BYTES
                or central_offset + central_size != end_offset
                or end_offset + 22 + comment_size != archive_size
                or end_index >= 20 and tail[end_index - 20:end_index - 16] == b"PK\x06\x07"):
            raise reject()
        stream.seek(0)
        if stream.read(4) != b"PK\x03\x04":
            raise reject()
        stream.seek(central_offset)
        records, regions = 0, []
        while stream.tell() < end_offset:
            if records >= count or records >= MAX_ENTRIES or stream.tell() + 46 > end_offset:
                raise reject()
            header = stream.read(46)
            if len(header) != 46:
                raise reject()
            fields = struct.unpack("<4s6H3I5H2I", header)
            signature, needed, method = fields[0], fields[2], fields[4]
            compressed, unpacked = fields[8:10]
            name_size, extra_size, item_comment, item_disk = fields[10:14]
            local_offset = fields[16]
            next_record = stream.tell() + name_size + extra_size + item_comment
            if (signature != b"PK\x01\x02" or needed >= 45 or item_disk != 0
                    or method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or compressed == 0xffffffff or unpacked == 0xffffffff or local_offset == 0xffffffff
                    or not 1 <= name_size <= 512 or next_record > end_offset
                    or local_offset + 30 > central_offset):
                raise reject()
            name = stream.read(name_size)
            _extra_without_zip64(stream.read(extra_size))
            # Local headers must identify the same entry and cannot use a
            # hidden ZIP64 extra field or point payload into the directory.
            stream.seek(local_offset)
            local = stream.read(30)
            if len(local) != 30:
                raise reject()
            local_fields = struct.unpack("<4s5H3I2H", local)
            local_name, local_extra = local_fields[-2:]
            data_end = local_offset + 30 + local_name + local_extra + compressed
            if (local_fields[0] != b"PK\x03\x04" or local_fields[1] >= 45
                    or local_fields[2] != fields[3] or local_fields[3] != method
                    or local_name != name_size or data_end > central_offset
                    or local_fields[7] == 0xffffffff or local_fields[8] == 0xffffffff
                    or stream.read(local_name) != name):
                raise reject()
            _extra_without_zip64(stream.read(local_extra))
            regions.append((local_offset, data_end))
            records += 1
            stream.seek(next_record)
        if stream.tell() != end_offset or records != count:
            raise reject()
        regions.sort()
        if any(previous[1] > following[0] for previous, following in zip(regions, regions[1:])):
            raise reject()
        stream.seek(0)
    except (OSError, ValueError, struct.error, OverflowError):
        raise FeatureError("unsafe_game_archive") from None


def review_archive(source, *, expected_sha256=None):
    if expected_sha256 is not None and (type(expected_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None):
        raise FeatureError("invalid_game_hash")
    stream, before = _open_archive(source)
    try:
        with stream:
            digest = _archive_hash(stream)
            if expected_sha256 is not None and digest != expected_sha256:
                raise FeatureError("game_hash_mismatch")
            _preflight_archive(stream)
            entries, names, files, total = [], set(), set(), 0
            with zipfile.ZipFile(stream) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_ENTRIES:
                    raise FeatureError("unsafe_game_archive")
                for info in infos:
                    name = _safe_name(info)
                    folded = name.casefold()
                    if folded in names or any(parent in files for parent in
                            ("/".join(folded.split("/")[:n]) for n in range(1, len(folded.split("/"))))):
                        raise FeatureError("unsafe_game_archive")
                    names.add(folded)
                    if info.is_dir():
                        continue
                    if any(previous.startswith(folded + "/") for previous in names if previous != folded):
                        raise FeatureError("unsafe_game_archive")
                    files.add(folded)
                    total += info.file_size
                    if (info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES
                            or info.file_size > max(1, info.compress_size) * MAX_RATIO):
                        raise FeatureError("game_archive_too_large")
                    entry_hash, size, prefix = hashlib.sha256(), 0, b""
                    with archive.open(info) as item:
                        while True:
                            chunk = item.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > info.file_size or size > MAX_FILE_BYTES:
                                raise FeatureError("game_archive_too_large")
                            if not prefix:
                                prefix = chunk[:4]
                            entry_hash.update(chunk)
                    if size != info.file_size or prefix.startswith((b"\x7fELF", b"MZ", b"#!")):
                        raise FeatureError("game_executable_rejected")
                    entries.append(ArchiveEntry(name, size, entry_hash.hexdigest()))
            _unchanged(stream, before)
        if not entries:
            raise FeatureError("unsafe_game_archive")
        return ArchiveReview(Path(source), digest, before.st_size, tuple(entries), total,
            any(Path(entry.name).suffix.lower() in _WEB for entry in entries),
            tuple(entry.name for entry in entries if Path(entry.name).name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))))
    except FeatureError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        raise FeatureError("unsafe_game_archive") from None


def _directory(root):
    try:
        path = Path(root)
        if not path.is_absolute() or ".." in path.parts:
            raise FeatureError("unsafe_game_root")
        return _open_directory(path, create=True)
    except (CredentialError, OSError, TypeError, ValueError):
        raise FeatureError("unsafe_game_root") from None


def _stage_file(directory, name):
    parent = os.dup(directory)
    try:
        for part in name.split("/")[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            os.close(parent); parent = following
        return os.open(name.split("/")[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL
                       | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
    finally:
        os.close(parent)


def install_reviewed(review, content_root, pack_id, *, license_id, license_reviewed=False,
                     web_content_reviewed=False, replace_existing=False):
    """Explicit staging only, retaining replaced versions for rollback.

    Approval flags are the human review record, not an automated legal opinion.
    No game is registered as runtime READY and no process/browser is launched.
    """
    if (not isinstance(review, ArchiveReview) or not valid_id(pack_id)
            or type(license_id) is not str or re.fullmatch(r"[A-Za-z0-9.+-]{1,80}", license_id) is None
            or license_reviewed is not True or type(replace_existing) is not bool
            or (review.active_web_content and web_content_reviewed is not True)):
        raise FeatureError("game_review_required")
    fresh = review_archive(review.source_path, expected_sha256=review.sha256)
    if fresh != review:
        raise FeatureError("game_asset_changed")
    root = stage_fd = lock = None
    stage, backup = None, None
    published = moved_old = False
    try:
        root = _directory(content_root)
        lock = os.open(".game-install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                       0o600, dir_fd=root)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise FeatureError("unsafe_game_root")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FeatureError("game_install_busy") from None
        for _ in range(10):
            candidate = "." + pack_id + ".stage-" + secrets.token_hex(12)
            try:
                os.mkdir(candidate, 0o700, dir_fd=root)
            except FileExistsError:
                continue
            stage = candidate
            break
        if stage is None:
            raise FeatureError("game_install_busy")
        stage_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
        stream, before = _open_archive(review.source_path)
        with stream:
            if _archive_hash(stream) != review.sha256:
                raise FeatureError("game_asset_changed")
            _preflight_archive(stream)
            with zipfile.ZipFile(stream) as archive:
                for entry in review.entries:
                    with archive.open(entry.name) as item, os.fdopen(_stage_file(stage_fd, entry.name), "wb") as output:
                        digest, size = hashlib.sha256(), 0
                        while True:
                            chunk = item.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > entry.size:
                                raise FeatureError("game_asset_changed")
                            digest.update(chunk); output.write(chunk)
                        if size != entry.size or digest.hexdigest() != entry.sha256:
                            raise FeatureError("game_asset_changed")
                        output.flush(); os.fsync(output.fileno())
            _unchanged(stream, before)
        manifest = {"version": 1, "pack_id": pack_id, "archive_sha256": review.sha256,
                    "license_id": license_id, "license_reviewed": True,
                    "web_content_reviewed": bool(web_content_reviewed),
                    "runtime_verified": False, "runtime": "NOT_RUN", "content_executed": False,
                    "files": [{"name": entry.name, "sha256": entry.sha256, "bytes": entry.size} for entry in review.entries]}
        with os.fdopen(_stage_file(stage_fd, _MANIFEST), "w", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=True, separators=(",", ":"))
            output.flush(); os.fsync(output.fileno())
        os.fsync(stage_fd)
        try:
            old = os.stat(pack_id, dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            old = None
        if old is not None:
            if not replace_existing:
                raise FeatureError("game_destination_exists")
            if (not stat.S_ISDIR(old.st_mode) or old.st_uid != os.geteuid()
                    or stat.S_IMODE(old.st_mode) != 0o700):
                raise FeatureError("unsafe_game_root")
            # Reserve a new empty name; never replace an existing rollback.
            for _ in range(10):
                candidate = "." + pack_id + ".rollback-" + secrets.token_hex(12)
                try:
                    os.mkdir(candidate, 0o700, dir_fd=root)
                except FileExistsError:
                    continue
                backup = candidate
                break
            if backup is None:
                raise FeatureError("game_install_busy")
        try:
            if old is not None:
                os.rename(pack_id, backup, src_dir_fd=root, dst_dir_fd=root)
                moved_old = True
            os.rename(stage, pack_id, src_dir_fd=root, dst_dir_fd=root)
            published = True
            os.fsync(root)
        except BaseException:
            try:
                # Determine actual directory identities as an interruption may
                # arrive after rename succeeds but before its flag assignment.
                staged_info = os.fstat(stage_fd)
                try:
                    current = os.stat(pack_id, dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    current = None
                identity = lambda value: (value.st_dev, value.st_ino)
                if current is not None and identity(current) == identity(staged_info):
                    os.rename(pack_id, stage, src_dir_fd=root, dst_dir_fd=root)
                    published = False
                    current = None
                elif current is not None and (old is None or identity(current) != identity(old)):
                    raise OSError("unexpected transaction destination")
                if old is not None:
                    previous = os.stat(backup, dir_fd=root, follow_symlinks=False)
                    if identity(previous) == identity(old):
                        if current is not None:
                            raise OSError("ambiguous transaction destination")
                        os.rename(backup, pack_id, src_dir_fd=root, dst_dir_fd=root)
                        moved_old = False
                    elif current is None or identity(current) != identity(old):
                        raise OSError("previous transaction directory unavailable")
                os.fsync(root)
            except OSError:
                # Preserve both the old backup and staged data for manual review.
                stage = None
                raise FeatureError("game_rollback_pending") from None
            raise FeatureError("game_install_rolled_back") from None
        return {"status": "INSTALLED_FOR_REVIEW", "pack_id": pack_id, "archive_sha256": review.sha256,
                "files_installed": len(review.entries), "runtime": "NOT_RUN", "content_executed": False,
                "rollback_available": moved_old, "rollback_id": backup if moved_old else None}
    except FeatureError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        raise FeatureError("game_install_failed") from None
    finally:
        for descriptor in (stage_fd, lock):
            if descriptor is not None:
                os.close(descriptor)
        if root is not None:
            if stage is not None and not published:
                try:
                    shutil.rmtree(stage, dir_fd=root)
                except OSError:
                    pass  # Only our private stage remains for manual review.
            if backup is not None and not moved_old:
                try:
                    os.rmdir(backup, dir_fd=root)
                except OSError:
                    pass
            os.close(root)
