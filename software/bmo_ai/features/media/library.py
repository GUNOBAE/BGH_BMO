"""Explicit local PCM WAV import; no playback, downloads or inferred licence.

Review holds an immutable bounded snapshot. Import publishes a private WAV and
then a private manifest with no-overwrite links. A crash before manifest publish
can leave an unreferenced WAV; it is never playable via the library until a
human reviews it. Retiring an import preserves both audio and metadata.
"""
from dataclasses import asdict, dataclass, field
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import stat
import wave

from ...private_credentials import CredentialError, _open_directory
from ..contracts import FeatureError, valid_id
from .assets import AssetHandle, MediaAsset, _open_root


MAX_IMPORT_BYTES = 64 * 1024 * 1024
MAX_LIBRARY_ITEMS = 128
_META_SUFFIX = ".song.json"


@dataclass(frozen=True, slots=True)
class WavReview:
    sha256: str
    size: int
    duration: float
    channels: int
    rate: int
    _raw: bytes = field(repr=False)

    def public(self):
        return {"status": "AVAILABLE_FOR_REVIEW", "sha256": self.sha256,
                "bytes": self.size, "duration": self.duration,
                "channels": self.channels, "rate": self.rate,
                "media_type": "audio/wav", "license_review": "NOT_RUN",
                "playback": "NOT_RUN", "cue_kind": "none"}


def _root_path(value):
    try:
        path = Path(value)
    except (TypeError, ValueError):
        raise FeatureError("invalid_media_library") from None
    if (not path.is_absolute() or ".." in path.parts
            or any(part in (".config", ".ssh", ".git") for part in path.parts)):
        raise FeatureError("invalid_media_library")
    return path


def _private_root(value, *, create=False):
    try:
        return _open_directory(_root_path(value), create=create)
    except CredentialError:
        raise FeatureError("unsafe_media_library") from None


def _same(a, b):
    return (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns) == (
            b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns)


def _wav_details(raw):
    with wave.open(io.BytesIO(raw), "rb") as wav:
        channels, rate, frames = wav.getnchannels(), wav.getframerate(), wav.getnframes()
        if (wav.getcomptype() != "NONE" or wav.getsampwidth() != 2
                or channels not in (1, 2) or not 8000 <= rate <= 96000
                or not 0 < frames / rate <= 7200
                or len(wav.readframes(frames)) != frames * channels * 2):
            raise FeatureError("invalid_media_wav")
        return frames / rate, channels, rate


def review_wav(source, *, expected_sha256=None):
    """Read only an explicitly selected regular local .wav, with bounded RAM."""
    if expected_sha256 is not None and (type(expected_sha256) is not str
            or len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256)):
        raise FeatureError("invalid_media_hash")
    descriptor = parent = None
    try:
        path = _root_path(source)
        if path.suffix.lower() != ".wav":
            raise FeatureError("media_import_codec_unsupported")
        parent = _open_root(path.parent)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=parent)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 44 <= before.st_size <= MAX_IMPORT_BYTES):
            raise FeatureError("unsafe_media_file")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(before.st_size + 1)
        if len(raw) != before.st_size or not _same(before, os.fstat(descriptor)):
            raise FeatureError("media_changed")
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise FeatureError("media_hash_mismatch")
        duration, channels, rate = _wav_details(raw)
        return WavReview(digest, len(raw), duration, channels, rate, raw)
    except FeatureError:
        raise
    except (OSError, ValueError, EOFError, wave.Error, TypeError):
        raise FeatureError("media_review_failed") from None
    finally:
        for fd in (descriptor, parent):
            if fd is not None:
                os.close(fd)


def _safe_metadata(root, name):
    descriptor = None
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=root)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600
                or not 0 < before.st_size <= 65536):
            raise FeatureError("unsafe_media_metadata")
        raw = os.read(descriptor, 65537)
        if len(raw) != before.st_size or not _same(before, os.fstat(descriptor)):
            raise FeatureError("media_changed")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise FeatureError("invalid_media_metadata")
                result[key] = value
            return result
        data = json.loads(raw, object_pairs_hook=unique)
        if (type(data) is not dict or set(data) != {"version", "asset", "license_reviewed", "license_id"}
                or type(data["version"]) is not int or data["version"] != 1
                or data["license_reviewed"] is not True or not valid_id(data["license_id"])
                or type(data["asset"]) is not dict):
            raise FeatureError("invalid_media_metadata")
        asset = MediaAsset(**data["asset"])
        if (name != asset.id + _META_SUFFIX or asset.media_type != "audio/wav"
                or asset.path != asset.id + "-" + asset.sha256[:12] + ".wav"
                or asset.cue_path is not None):
            raise FeatureError("invalid_media_metadata")
        return asset, raw, before
    except FeatureError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError):
        raise FeatureError("invalid_media_metadata") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _names(root):
    names = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= 1024:
                raise FeatureError("media_library_capacity")
            if entry.name.endswith(_META_SUFFIX):
                identifier = entry.name[:-len(_META_SUFFIX)]
                if not valid_id(identifier) or len(names) >= MAX_LIBRARY_ITEMS:
                    raise FeatureError("invalid_media_metadata")
                names.append(entry.name)
    return sorted(names)


def load_library(content_root):
    """Missing library is empty and is never created or repaired by a read."""
    root = _private_root(content_root)
    if root is None:
        return ()
    try:
        result = []
        for name in _names(root):
            asset, _, _ = _safe_metadata(root, name)
            with AssetHandle(content_root, asset):
                pass
            result.append(asset)
        return tuple(result)
    finally:
        os.close(root)


def _write_stage(root, raw):
    name = ".media-stage-" + secrets.token_hex(12)
    descriptor, created = None, False
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=root)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return name
    except BaseException:
        if created:
            try:
                os.unlink(name, dir_fd=root)
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def install_wav(review, content_root, media_id, *, title, source="user_owned",
                license_id=None, license_reviewed=False):
    """Import one reviewed snapshot. Never replace an existing song or asset.

    This only prepares files; RuntimePolicy/music and volume approval remain
    independent. No output device or media subprocess is opened.
    """
    if (type(review) is not WavReview or license_reviewed is not True
            or not valid_id(license_id) or not valid_id(media_id)):
        raise FeatureError("media_review_required")
    if (not 44 <= len(review._raw) <= MAX_IMPORT_BYTES or len(review._raw) != review.size
            or hashlib.sha256(review._raw).hexdigest() != review.sha256):
        raise FeatureError("media_changed")
    try:
        if _wav_details(review._raw) != (review.duration, review.channels, review.rate):
            raise FeatureError("media_changed")
    except (ValueError, EOFError, wave.Error, TypeError):
        raise FeatureError("invalid_media_wav") from None
    asset = MediaAsset(media_id, title, source, media_id + "-" + review.sha256[:12] + ".wav",
                       review.sha256, "audio/wav", review.duration)
    metadata = json.dumps({"version": 1, "asset": asdict(asset), "license_id": license_id,
                           "license_reviewed": True}, sort_keys=True).encode()
    root = _private_root(content_root, create=True)
    lock = None
    stages, published = [], []
    try:
        lock = os.open(".media-import.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                       0o600, dir_fd=root)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise FeatureError("unsafe_media_library")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FeatureError("media_import_busy") from None
        if len(_names(root)) >= MAX_LIBRARY_ITEMS:
            raise FeatureError("media_library_capacity")
        for name in (asset.path, media_id + _META_SUFFIX):
            try:
                os.stat(name, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FeatureError("media_import_exists")
        available = os.fstatvfs(root).f_bavail * os.fstatvfs(root).f_frsize
        if available < review.size + 1024 * 1024:
            raise FeatureError("media_disk_space_low")
        for name, raw in ((asset.path, review._raw), (media_id + _META_SUFFIX, metadata)):
            stage = _write_stage(root, raw)
            stages.append(stage)
            os.link(stage, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
            published.append((name, os.stat(name, dir_fd=root, follow_symlinks=False).st_ino))
            os.unlink(stage, dir_fd=root)
            stages.remove(stage)
        os.fsync(root)
        return {"status": "IMPORTED_FOR_REVIEW", "media_id": media_id, "sha256": review.sha256,
                "bytes": review.size, "playback": "NOT_RUN", "cue_kind": "none",
                "runtime_enabled": False, "rollback": "retire_manifest_preserve_audio"}
    except BaseException as error:
        for name, inode in reversed(published):
            try:
                if os.stat(name, dir_fd=root, follow_symlinks=False).st_ino == inode:
                    os.unlink(name, dir_fd=root)
            except OSError:
                pass
        if isinstance(error, (KeyboardInterrupt, SystemExit, FeatureError)):
            raise
        raise FeatureError("media_import_failed") from None
    finally:
        for name in stages:
            try:
                os.unlink(name, dir_fd=root)
            except OSError:
                pass
        if lock is not None:
            os.close(lock)
        os.close(root)


def retire_import(content_root, media_id, *, expected_sha256):
    """Hide a matching library entry, retaining its audio and review metadata."""
    if not valid_id(media_id):
        raise FeatureError("invalid_media_manifest")
    root = _private_root(content_root)
    if root is None:
        raise FeatureError("asset_pending")
    try:
        name = media_id + _META_SUFFIX
        asset, _, before = _safe_metadata(root, name)
        if asset.sha256 != expected_sha256:
            raise FeatureError("media_hash_mismatch")
        retired = ".retired-" + media_id + "-" + secrets.token_hex(12) + ".json"
        os.link(name, retired, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
        removed = False
        try:
            current = os.stat(name, dir_fd=root, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise FeatureError("media_changed")
            os.unlink(name, dir_fd=root)
            removed = True
            os.fsync(root)
        except BaseException:
            if not removed:
                os.unlink(retired, dir_fd=root)
            raise
        return {"status": "RETIRED", "media_id": media_id, "audio_preserved": True,
                "metadata_preserved": True, "playback": "NOT_RUN"}
    except FeatureError:
        raise
    except OSError:
        raise FeatureError("media_retire_failed") from None
    finally:
        os.close(root)
