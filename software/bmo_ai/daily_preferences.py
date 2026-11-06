"""Small private user choices shared by successive Candidate launches.

Only explicit settings changes write this store. Construction is read-only and
missing settings leave the launcher's existing defaults intact. This file holds
no device permission, approval, credentials, conversation or audio metadata.
"""
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import secrets
import stat
from threading import RLock

from .private_credentials import CredentialError, _close, _private_file


MAX_BYTES = 4096
_FILE = "preferences.json"
_FIELDS = frozenset(("volume", "subtitle_mode", "presence_mode"))


def default_directory():
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "bmo" / "preferences"


def _validated(values):
    if type(values) is not dict or not set(values) <= _FIELDS:
        raise ValueError("invalid preferences")
    result = deepcopy(values)
    if "volume" in result:
        value = result["volume"]
        if (type(value) is not dict or set(value) != {"level", "muted"}
                or type(value["level"]) not in (int, float)
                or not math.isfinite(value["level"]) or not 0 <= value["level"] <= 1
                or type(value["muted"]) is not bool):
            raise ValueError("invalid volume")
        value["level"] = float(value["level"])
    if "subtitle_mode" in result and (type(result["subtitle_mode"]) is not str
            or result["subtitle_mode"] not in ("off", "en", "ko")):
        raise ValueError("invalid subtitles")
    if "presence_mode" in result and (type(result["presence_mode"]) is not str
            or result["presence_mode"] not in ("normal", "quiet", "sleep", "off")):
        raise ValueError("invalid presence")
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate setting")
        result[key] = value
    return result


def _open_directory(path, *, create=False):
    """The same pinned-directory boundary as other BMO private stores."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in (*path.parts[1:], None):
            try:
                os.stat(".git", dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OSError("checkout destination")
            if part is None:
                break
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
            _close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OSError("private directory required")
        result, fd = fd, None
        return result
    finally:
        _close(fd)


def _read(root):
    if root is None:
        return {}, None
    fd = None
    try:
        fd = os.open(_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root)
        info = os.fstat(fd)
        _private_file(info, size_limit=MAX_BYTES)
        raw = os.read(fd, MAX_BYTES + 1)
        if len(raw) != info.st_size:
            raise ValueError("changed preferences")
        value = json.loads(raw, object_pairs_hook=_unique)
        if (type(value) is not dict or set(value) != {"version", "settings"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError("invalid preference version")
        return _validated(value["settings"]), (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
    except FileNotFoundError:
        return {}, None
    finally:
        _close(fd)


class DailyPreferences:
    def __init__(self, directory=None):
        self._guard = RLock()
        self._values = {}
        self._enabled, self._code = False, "preferences_unavailable"
        self.directory = None
        root = None
        try:
            path = Path(directory) if directory is not None else default_directory()
            if not path.is_absolute() or ".." in path.parts or "\0" in str(path):
                raise ValueError("invalid directory")
            self.directory = path
            root = _open_directory(path)
            self._values, identity = _read(root)
            self._enabled = True
            self._code = "preferences_ready" if identity is not None else "preferences_empty"
        except (OSError, CredentialError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            # Optional persistence must not prevent the face or dialogue.
            self._values = {}
        finally:
            _close(root)

    def load(self):
        with self._guard:
            return deepcopy(self._values)

    def snapshot(self):
        with self._guard:
            return dict(enabled=self._enabled, code=self._code,
                        stored_fields=sorted(self._values), automatic_write=False)

    def update(self, **values):
        with self._guard:
            if not self._enabled:
                return False
            try:
                selected = _validated(values)
            except (ValueError, TypeError, OverflowError, RecursionError):
                self._code = "preferences_invalid_update"
                return False
            if not selected:
                return True
            root = lock = temporary = None
            try:
                root = _open_directory(self.directory, create=True)
                lock = os.open(".preferences.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                               | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=root)
                _private_file(os.fstat(lock), size_limit=0)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                current, identity = _read(root)
                merged = dict(current, **selected)
                raw = (json.dumps(dict(version=1, settings=merged), allow_nan=False,
                                  sort_keys=True, separators=(",", ":")) + "\n").encode()
                if len(raw) > MAX_BYTES:
                    raise ValueError("preferences too large")
                temporary = ".preferences-" + secrets.token_hex(12)
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                # Check again after preparing the replacement; invalid or
                # concurrently substituted files are never silently replaced.
                _, latest = _read(root)
                if latest != identity:
                    raise ValueError("preferences changed")
                if identity is None:
                    os.link(temporary, _FILE, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
                    os.unlink(temporary, dir_fd=root)
                else:
                    os.replace(temporary, _FILE, src_dir_fd=root, dst_dir_fd=root)
                temporary = None
                os.fsync(root)
                self._values, self._code = merged, "preferences_saved"
                return True
            except BlockingIOError:
                self._code = "preferences_busy"
                return False
            except (OSError, CredentialError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
                self._enabled, self._code = False, "preferences_write_failed"
                return False
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=root)
                    except OSError:
                        pass
                _close(lock)
                _close(root)
