"""Explicit live/setup credential storage; importing this module performs no IO.

Storage is private plaintext, not encryption. An explicit ``config_dir`` is the
directory containing credentials.json; otherwise use $XDG_CONFIG_HOME/bmo or
~/.config/bmo. Callers must keep this module off help/simulated/status paths.

Only values entered through the secret prompt are persisted. Environment values
remain session-only. Atomic replacement is the commit point: interruption before
replace preserves the old bytes; a failure syncing the directory after replace
can leave the complete new file in place. No underlying exception/path is public.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import getpass
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time
import warnings


MAX_KEY_BYTES = 4096
MAX_STORE_BYTES = 16384
LOCK_TIMEOUT_SECONDS = 30.0
_FILE = "credentials.json"
_LOCK = ".credentials.lock"
_FIELDS = ("openai_api_key", "fish_api_key")
_ENV_FIELDS = ("OPENAI_API_KEY", "FISH_API_KEY")
_PROMPTS = ("OpenAI API key (saved privately for this user): ",
            "Fish API key (saved privately for this user): ")
_ERRORS = {
    "invalid_config": "The credential storage configuration is invalid.",
    "unsafe_directory": "The credential directory is not private and safe to use.",
    "unsafe_file": "The credential file is not private and safe to use.",
    "invalid_store": "Stored credentials could not be validated.",
    "store_unavailable": "Credential storage is unavailable.",
    "store_write_failed": "Credentials could not be saved safely.",
    "credentials_busy": "Another credential setup is in progress. Try again after it finishes.",
    "credentials_missing": "Required credentials are missing. Start live mode in a terminal to enter them.",
    "input_required": "Credential setup requires an interactive terminal.",
    "input_unavailable": "Secret credential input is unavailable.",
    "invalid_key": "The entered credential value is invalid.",
    "cancelled": "Credential setup was cancelled.",
}


class CredentialError(RuntimeError):
    """Only a fixed code and safe message; no credential or filesystem payload."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in _ERRORS else "store_unavailable"
        self.safe_message = _ERRORS[self.code]
        super().__init__(self.safe_message)


def _key(value, *, code="invalid_key"):
    if (type(value) is not str or not value or len(value) > MAX_KEY_BYTES
            or any(not 33 <= ord(char) <= 126 for char in value)):
        raise CredentialError(code)
    try:
        if len(value.encode("utf-8")) > MAX_KEY_BYTES:
            raise CredentialError(code)
    except UnicodeError:
        raise CredentialError(code) from None
    return value


@dataclass(frozen=True, slots=True, repr=False)
class Credentials:
    openai_api_key: str
    fish_api_key: str

    def __post_init__(self):
        for name in _FIELDS:
            _key(getattr(self, name))

    def __repr__(self):
        return "Credentials(openai_api_key=<redacted>, fish_api_key=<redacted>)"


def _close(fd):
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _path(config_dir, environ):
    if config_dir is None:
        base = environ.get("XDG_CONFIG_HOME")
        if base in (None, ""):
            path = Path.home() / ".config" / "bmo"
        elif type(base) is str:
            path = Path(base) / "bmo"
        else:
            raise CredentialError("invalid_config")
    else:
        try:
            raw = os.fspath(config_dir)
            if type(raw) is not str:
                raise CredentialError("invalid_config")
            path = Path(raw)
        except (TypeError, ValueError):
            raise CredentialError("invalid_config") from None
    if not path.is_absolute() or ".." in path.parts or "\0" in str(path):
        raise CredentialError("invalid_config")
    return path


def _directory_ok(fd):
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise CredentialError("unsafe_directory")


def _open_directory(path, *, create=False):
    """Walk using pinned dirfds; never follow a symlink in any component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = None
    try:
        fd = os.open("/", flags)
        for part in path.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass  # A concurrent resolver may have created it.
                child = os.open(part, flags, dir_fd=fd)
            _close(fd)
            fd = child
        _directory_ok(fd)
        result, fd = fd, None
        return result
    except OSError as error:
        code = "unsafe_directory" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "store_unavailable"
        raise CredentialError(code) from None
    finally:
        _close(fd)


def _private_file(info, *, size_limit=MAX_STORE_BYTES):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise CredentialError("unsafe_file")
    if info.st_size > size_limit:
        raise CredentialError("invalid_store")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CredentialError("invalid_store")
        result[key] = value
    return result


def _read_store(directory):
    if directory is None:
        return {}
    fd = None
    try:
        # NONBLOCK prevents a substituted FIFO from hanging before fstat rejects it.
        fd = os.open(_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     dir_fd=directory)
        _private_file(os.fstat(fd))
        with os.fdopen(fd, "rb") as handle:
            fd = None
            raw = handle.read(MAX_STORE_BYTES + 1)
        if len(raw) > MAX_STORE_BYTES:
            raise CredentialError("invalid_store")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (type(value) is not dict or type(value.get("version")) is not int
                or value["version"] != 1 or set(value) - {"version", *_FIELDS}):
            raise CredentialError("invalid_store")
        return {name: _key(value[name], code="invalid_store") for name in _FIELDS if name in value}
    except FileNotFoundError:
        return {}
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise CredentialError("invalid_store") from None
    except OSError as error:
        raise CredentialError("unsafe_file" if error.errno == errno.ELOOP else "store_unavailable") from None
    finally:
        _close(fd)


@contextmanager
def _locked(directory):
    fd = None
    try:
        fd = os.open(_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        _private_file(os.fstat(fd), size_limit=0)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CredentialError("credentials_busy") from None
                time.sleep(.02)
        yield
    except OSError as error:
        raise CredentialError("unsafe_file" if error.errno == errno.ELOOP else "store_unavailable") from None
    finally:
        _close(fd)  # Closing also releases this open-file-description lock.


def _write_store(directory, values):
    """Commit one complete private file, preserving old bytes before replace."""
    temporary, fd = None, None
    try:
        _directory_ok(directory)
        raw = (json.dumps({"version": 1, **values}, ensure_ascii=False,
                          separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > MAX_STORE_BYTES:
            raise CredentialError("invalid_store")
        for _ in range(10):
            candidate = ".credentials." + secrets.token_hex(16) + ".tmp"
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                temporary = candidate
                break
            except FileExistsError:
                continue
        if fd is None:
            raise CredentialError("store_write_failed")
        _private_file(os.fstat(fd))
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            if count <= 0:
                raise CredentialError("store_write_failed")
            offset += count
        os.fsync(fd)
        _close(fd)
        fd = None
        # Reject a destination swapped for an unsafe object during prompting.
        try:
            _private_file(os.stat(_FILE, dir_fd=directory, follow_symlinks=False))
        except FileNotFoundError:
            pass
        os.replace(temporary, _FILE, src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        os.fsync(directory)
    except (OSError, UnicodeError, TypeError, ValueError):
        raise CredentialError("store_write_failed") from None
    finally:
        _close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass


def _prompt(input_secret, prompt):
    try:
        with warnings.catch_warnings():
            # getpass can otherwise fall back to echoed input after termios or
            # /dev/tty fails, even when the caller's stdin initially was a TTY.
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = input_secret(prompt)
    except KeyboardInterrupt:
        raise CredentialError("cancelled") from None
    except Exception:
        raise CredentialError("input_unavailable") from None
    return _key(value)


def resolve_credentials(*, config_dir=None, environ=None, input_secret=None,
                        interactive=None, force_prompt=False) -> Credentials:
    """Resolve env > saved > prompt, serializing only interactive setup.

    force_prompt explicitly replaces both stored keys and returns those entered
    values. No API request validates keys here. Noninteractive resolution never
    creates a directory, file or lock. Stored data can be partial: supplied env
    keys are never copied into it. Every prompt completes before a single commit.
    """
    directory = None
    try:
        source = os.environ if environ is None else environ
        if not hasattr(source, "get") or type(force_prompt) is not bool:
            raise CredentialError("invalid_config")
        path = _path(config_dir, source)
        if interactive is None:
            interactive = bool(sys.stdin.isatty())
        if type(interactive) is not bool or (input_secret is not None and not callable(input_secret)):
            raise CredentialError("invalid_config")
        supplied = {}
        if not force_prompt:
            for field, variable in zip(_FIELDS, _ENV_FIELDS):
                value = source.get(variable)
                if value not in (None, ""):
                    supplied[field] = _key(value)
            if len(supplied) == len(_FIELDS):
                return Credentials(**supplied)
        directory = _open_directory(path)
        stored = _read_store(directory)
        resolved = {**stored, **supplied}
        if not force_prompt and all(name in resolved for name in _FIELDS):
            return Credentials(**resolved)
        if not interactive:
            raise CredentialError("input_required" if force_prompt else "credentials_missing")
        if directory is None:
            directory = _open_directory(path, create=True)
        with _locked(directory):
            # Another setup may have completed while this resolver waited.
            stored = _read_store(directory)
            resolved = {} if force_prompt else {**stored, **supplied}
            persist = {} if force_prompt else dict(stored)
            changed = False
            prompt = getpass.getpass if input_secret is None else input_secret
            for field, label in zip(_FIELDS, _PROMPTS):
                if force_prompt or field not in resolved:
                    value = _prompt(prompt, label)
                    resolved[field] = persist[field] = value
                    changed = True
            if changed:
                _write_store(directory, persist)
            return Credentials(**resolved)
    except CredentialError:
        raise
    except KeyboardInterrupt:
        raise CredentialError("cancelled") from None
    except EOFError:
        raise CredentialError("input_unavailable") from None
    except Exception:
        raise CredentialError("store_unavailable") from None
    finally:
        _close(directory)
