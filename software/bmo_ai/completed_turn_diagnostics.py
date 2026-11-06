"""Bounded private completed-turn timings; no text, PCM or provider payloads.

The live owner opts in when constructing this store. Importing it and simulated
launches do not touch disk. Storage failure disables this diagnostic only; it
must never fail a conversation, alter provider settings or trigger a retry.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
from threading import RLock
import time

from .turn_diagnostics import CODES, STAGES, _MARKERS, _SPANS


CAPACITY = 32
MAX_BYTES = 262144
_FILE = "completed-turns.json"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MARKER_NAMES = frozenset(_MARKERS.values())
_OUTCOMES = frozenset(("ok", "started", "error", "empty", "cancelled"))
_CODES = frozenset(("ready", "saved", "disabled", "diagnostic_storage_unavailable",
                    "diagnostic_invalid_record", "diagnostic_write_failed"))


def default_directory():
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "bmo" / "turn-diagnostics"


def _git_text(path, limit=4096):
    """Bounded regular metadata only; a FIFO/symlink must never block startup."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid git metadata")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("invalid git metadata")
        return raw.decode("utf-8").strip()
    finally:
        os.close(fd)


def _git_directory(base, value):
    # Git's relative gitdir/commondir legitimately use '..'. Normalize those
    # paths, then reject symlinks in every directory component before reading.
    if not value or any(char in value for char in "\0\r\n"):
        raise ValueError("invalid git directory")
    path = Path(os.path.abspath(base / value))
    for parent in (*reversed(path.parents), path):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("invalid git directory")
    return path


def source_revision():
    """Read normal/worktree HEAD once without subprocess, env or network.

    A linked worktree owns HEAD but its branch refs/packed-refs live in the
    common Git directory. Only a validated 40-hex revision leaves this reader;
    it is provenance, not a statement that the tree or Pi receipt is clean.
    """
    marker = Path(__file__).resolve().parents[2] / ".git"
    try:
        if marker.is_symlink():
            return None
        if marker.is_dir():
            git = _git_directory(marker.parent, marker.name)
        else:
            pointer = _git_text(marker)
            if not pointer.startswith("gitdir: "):
                return None
            git = _git_directory(marker.parent, pointer[8:])
        head = _git_text(git / "HEAD")
        if _SHA.fullmatch(head):
            return head
        if not head.startswith("ref: refs/heads/"):
            return None
        ref = head[5:]
        if (any(part in ("", ".", "..") for part in ref.split("/"))
                or not re.fullmatch(r"refs/heads/[A-Za-z0-9_./-]{1,160}", ref)):
            return None
        common = git
        try:
            pointer = _git_text(git / "commondir")
        except FileNotFoundError:
            pass
        else:
            common = _git_directory(git, pointer)
            # A linked worktree admin directory must belong to this common
            # repository. Arbitrary commondir redirection stays unverified.
            if git.parent != common / "worktrees":
                return None
        path = common / ref
        try:
            _git_directory(common, str(Path(ref).parent))
            value = _git_text(path)
        except FileNotFoundError:
            value = None
        if value is not None:
            return value if _SHA.fullmatch(value) else None
        for line in _git_text(common / "packed-refs", MAX_BYTES).splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1] == ref and _SHA.fullmatch(fields[0]):
                return fields[0]
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def _number(value, upper=180000):
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 <= value <= upper)


def _stage(value):
    if (type(value) is dict and value.get("stage") in STAGES
            and value.get("code") in CODES and value.get("outcome") in _OUTCOMES):
        return {name: value[name] for name in ("stage", "code", "outcome")}
    return None


def numeric_receipt(trace, *, revision, profile, mode, speech_safe, recorded_at):
    """Rebuild an allowlist, never serialize the trace/config object wholesale."""
    if (type(trace) is not dict or trace.get("enabled") is not True
            or type(trace.get("turn")) is not int or not 1 <= trace["turn"] <= 1000000
            or type(trace.get("terminal")) is not str or trace["terminal"] not in CODES
            or profile not in ("baseline", "balanced", "quick")
            or mode not in ("rms", "webrtc") or type(speech_safe) is not bool
            or not _number(recorded_at, 100000000000)):
        return None
    raw = trace.get("markers_ms")
    markers = ({name: round(value, 1) for name, value in raw.items()
                if name in _MARKER_NAMES and _number(value)} if type(raw) is dict else {})
    timings = {}
    for name, (start, end) in _SPANS.items():
        left, right = markers.get(start), markers.get(end)
        measured = left is not None and right is not None and right >= left
        timings[name] = dict(status="MEASURED" if measured else "UNMEASURED",
                             value_ms=round(right - left, 1) if measured else None)
    timings["acoustic_first_sound_ms"] = dict(status="UNMEASURED", value_ms=None)
    return dict(version=2, runtime_revision=revision if type(revision) is str and _SHA.fullmatch(revision) else None,
                recorded_at_unix=int(recorded_at), turn=trace["turn"], terminal=trace["terminal"],
                vad_profile=profile, vad_mode=mode, speech_safe=speech_safe,
                markers_ms=markers, timings=timings, failure=_stage(trace.get("failure")),
                timing_origin="host_input_received" if "host_input_received" in markers else "turn_admission",
                clock="host_monotonic", acoustic_measurement=False,
                contains_audio=False, contains_transcript=False)


def _stored_receipt(value):
    if type(value) is not dict or value.get("version") != 2:
        return None
    rebuilt = numeric_receipt(dict(value, enabled=True), revision=value.get("runtime_revision"),
        profile=value.get("vad_profile"), mode=value.get("vad_mode"),
        speech_safe=value.get("speech_safe"), recorded_at=value.get("recorded_at_unix"))
    return rebuilt if rebuilt == value else None


class CompletedTurnStore:
    def __init__(self, directory=None, *, runtime_revision=None, vad_profile="baseline",
                 vad_mode="rms", speech_safe=True, wall_clock=time.time):
        self._lock = RLock()
        self._fd = self._filefd = None
        self._identity = None
        self._records = []
        self._code, self._enabled = "disabled", False
        self._revision = source_revision() if runtime_revision is None else runtime_revision
        self._profile, self._mode, self._speech_safe = vad_profile, vad_mode, speech_safe
        self._wall_clock = wall_clock
        try:
            path = Path(directory) if directory is not None else default_directory()
            if (not path.is_absolute() or ".." in path.parts
                    or vad_profile not in ("baseline", "balanced", "quick")
                    or vad_mode not in ("rms", "webrtc") or type(speech_safe) is not bool):
                raise ValueError("invalid diagnostic configuration")
            self._fd = self._open_directory(path)
            self._load()
            self._enabled, self._code = True, "ready"
        except (OSError, ValueError, TypeError, UnicodeError):
            self.close()
            self._code = "diagnostic_storage_unavailable"

    @staticmethod
    def _open_directory(path):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("/", flags)
        try:
            for part in path.parts[1:]:
                # Never create diagnostic files anywhere inside a checkout.
                try:
                    os.stat(".git", dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise OSError("checkout destination")
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError("private directory required")
            try:
                os.stat(".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OSError("checkout destination")
            result, fd = fd, None
            return result
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _file_ok(info):
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size > MAX_BYTES):
            raise OSError("private bounded file required")

    def _load(self):
        try:
            fd = os.open(_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._fd)
        except FileNotFoundError:
            return
        try:
            info = os.fstat(fd)
            self._file_ok(info)
            with os.fdopen(os.dup(fd), "rb") as source:
                value = json.loads(source.read(MAX_BYTES + 1))
            if (type(value) is not dict or set(value) != {"version", "records"}
                    or value["version"] != 1 or type(value["records"]) is not list
                    or len(value["records"]) > CAPACITY
                    or any(_stored_receipt(row) is None for row in value["records"])):
                raise ValueError("invalid diagnostic store")
            self._records = value["records"]
            self._identity = (info.st_dev, info.st_ino)
            self._filefd, fd = fd, None
        finally:
            if fd is not None:
                os.close(fd)

    def snapshot(self):
        with self._lock:
            return dict(enabled=self._enabled, code=self._code if self._code in _CODES else "disabled",
                        retained=len(self._records), capacity=CAPACITY,
                        contains_audio=False, contains_transcript=False)

    def record(self, trace):
        with self._lock:
            if not self._enabled:
                return False
            try:
                receipt = numeric_receipt(trace, revision=self._revision, profile=self._profile,
                    mode=self._mode, speech_safe=self._speech_safe, recorded_at=self._wall_clock())
            except Exception:
                receipt = None
            if receipt is None:
                self._code = "diagnostic_invalid_record"
                return False
            records = [*self._records, receipt][-CAPACITY:]
            if not self._write(records):
                return False
            self._records, self._code = records, "saved"
            return True

    def _write(self, records):
        temporary = ".completed-turn-" + secrets.token_hex(12)
        descriptor = None
        try:
            info = os.fstat(self._fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError("directory changed")
            encoded = (json.dumps(dict(version=1, records=records), allow_nan=False,
                                  separators=(",", ":")) + "\n").encode()
            if len(encoded) > MAX_BYTES:
                raise OSError("diagnostic size limit")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=self._fd)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
                new_info = os.fstat(output.fileno())
            try:
                current = os.stat(_FILE, dir_fd=self._fd, follow_symlinks=False)
            except FileNotFoundError:
                if self._identity is not None:
                    raise OSError("destination removed")
            else:
                self._file_ok(current)
                if (current.st_dev, current.st_ino) != self._identity:
                    raise OSError("destination replaced")
            if self._identity is None:
                # Exclusive publication for a new store must not overwrite a
                # concurrently created file between the absence check and write.
                os.link(temporary, _FILE, src_dir_fd=self._fd, dst_dir_fd=self._fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=self._fd)
            else:
                os.replace(temporary, _FILE, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            published = os.open(_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._fd)
            published_info = os.fstat(published)
            if (published_info.st_dev, published_info.st_ino) != (new_info.st_dev, new_info.st_ino):
                os.close(published)
                raise OSError("destination replaced after publication")
            if self._filefd is not None:
                os.close(self._filefd)
            self._filefd = published
            self._identity = (new_info.st_dev, new_info.st_ino)
            os.fsync(self._fd)
            return True
        except (OSError, ValueError, TypeError):
            self._enabled, self._code = False, "diagnostic_write_failed"
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=self._fd)
            except (OSError, TypeError):
                pass

    def close(self):
        with self._lock:
            self._enabled = False
            for attribute in ("_filefd", "_fd"):
                fd = getattr(self, attribute)
                setattr(self, attribute, None)
                if fd is not None:
                    os.close(fd)
