"""Read-only launcher checks and fixed startup diagnostics; never repair a session."""
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat

from .desktop_browser import BrowserError, _directory, _executable
from .desktop_runtime import public_code
from .screen_profiles import SCREEN_PROFILES

STAGES = frozenset({"lock", "configuration", "greeting_review", "server_init", "server_start",
    "browser_init", "browser_start", "engine_init", "greeting_init", "owner_loop", "cleanup"})
EXTRA_CODES = frozenset({"desktop_start_failed", "desktop_cleanup_failed", "lock_unavailable",
    "screen_unavailable", "screen_sha256_mismatch", "invalid_port", "face_ready_timeout"})


def failure(stage, error):
    """Never trust arbitrary .code values, exception repr, environment or paths."""
    stage = stage if stage in STAGES else "owner_loop"
    value = getattr(error, "code", None)
    code = value if type(value) is str and value in EXTRA_CODES else public_code(value, "desktop_start_failed")
    return f"BMO desktop failed: stage={stage} code={code}"


def startup_preflight(*, environ=None, face_file=None, screen_profile="r10", lock_path=None,
                      executable_candidates=("/usr/lib/chromium/chromium", "/usr/bin/chromium")):
    """Only metadata/hash/read-only lock probe; no profile/file/port/process creation."""
    source = os.environ if environ is None else environ
    result = {"version": 1, "read_only": True, "credentials_accessed": False,
              "processes_started": False, "files_written": 0, "repairs_performed": False}
    display = source.get("WAYLAND_DISPLAY")
    result["wayland_display_present"] = (type(display) is str and
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", display) is not None)
    descriptor = None
    try:
        descriptor = _directory(source.get("XDG_RUNTIME_DIR"), "runtime_directory_permissions")
        result["runtime_directory"] = "ready"
    except BrowserError as error:
        result["runtime_directory"] = error.code
    finally:
        if descriptor is not None: os.close(descriptor)
    result["chromium"] = "browser_executable_unsafe"
    for candidate in executable_candidates:
        try:
            _executable(candidate)
            result["chromium"] = "ready"
            break
        except BrowserError: pass
    record = SCREEN_PROFILES[screen_profile]
    path = Path(face_file or record["default_path"])
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 2 * 1024 * 1024:
            raise ValueError()
        raw = os.read(descriptor, 2 * 1024 * 1024 + 1)
        result["screen_sha256_matches"] = hashlib.sha256(raw).hexdigest() == record["sha256"]
        result["screen"] = "ready" if result["screen_sha256_matches"] else "screen_sha256_mismatch"
    except (OSError, ValueError, TypeError):
        result["screen"], result["screen_sha256_matches"] = "screen_unavailable", False
    finally:
        if descriptor is not None: os.close(descriptor)
    descriptor = None
    try:
        path = lock_path if lock_path is not None else f"/tmp/bmo-integration-{os.getuid()}.lock"
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result["launcher_lock"] = "available"
        except BlockingIOError: result["launcher_lock"] = "occupied"
    except FileNotFoundError: result["launcher_lock"] = "absent"
    except (OSError, TypeError, ValueError): result["launcher_lock"] = "unsafe_or_unreadable"
    finally:
        if descriptor is not None: os.close(descriptor)
    result["live_devices"] = "NOT_RUN"
    return result
