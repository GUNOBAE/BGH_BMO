"""Own one Chromium group and an ephemeral profile; never alter a desktop session.

Chromium readiness is the host's face handshake, not merely process creation.
Call poll regularly and mark_ready only after that handshake. There is no
automatic relaunch, user-profile reuse, remote debugging or sandbox bypass.
"""
import math
from collections import deque
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import time
from urllib.parse import urlsplit


_MESSAGES = {
    "browser_invalid_config": "Browser configuration is invalid.",
    "browser_environment_missing": "A usable Wayland session is required.",
    "browser_executable_unsafe": "The Chromium executable is unavailable or unsafe.",
    "native_window_unavailable": "The optional system Qt WebEngine native window is unavailable.",
    "browser_profile_failed": "A private browser profile could not be prepared.",
    "runtime_directory_permissions": "The session runtime directory must be owned by this user with mode 0700. No permissions were changed.",
    "browser_spawn_failed": "The BMO browser could not be started.",
    "browser_start_failed": "The BMO browser exited before face readiness.",
    "browser_start_timeout": "The BMO browser did not become ready in time.",
    "browser_closed": "The BMO browser was closed.",
    "browser_cleanup_failed": "BMO browser cleanup could not be confirmed.",
    "browser_stopped": "The BMO browser was stopped.",
    "browser_ready": "The BMO browser is ready.",
    "browser_starting": "The BMO browser is starting.",
}


class BrowserError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else "browser_invalid_config"
        self.safe_message = _MESSAGES[self.code]
        super().__init__(self.safe_message)


def _directory(path, code):
    descriptor = None
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise BrowserError(code)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", flags)
        for part in path.parts[1:]:
            following = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise BrowserError(code)
        result, descriptor = descriptor, None
        return result
    except (OSError, ValueError, TypeError):
        raise BrowserError(code) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _executable(path):
    parent = descriptor = None
    try:
        path = Path(path)
        if (not path.is_absolute() or ".." in path.parts
                or path.name not in ("chromium", "chromium-browser")):
            raise BrowserError("browser_executable_unsafe")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent = os.open("/", flags)
        for part in path.parts[1:-1]:
            following = os.open(part, flags, dir_fd=parent)
            os.close(parent)
            parent = following
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=parent)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_nlink != 1
                or info.st_mode & 0o6022 or not info.st_mode & 0o111):
            raise BrowserError("browser_executable_unsafe")
        return str(path)
    except (OSError, ValueError, TypeError):
        raise BrowserError("browser_executable_unsafe") from None
    finally:
        for value in (descriptor, parent):
            if value is not None:
                os.close(value)


def _url(value):
    try:
        parsed = urlsplit(value)
        if (type(value) is not str or len(value) > 160 or parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1" or parsed.username is not None or parsed.password is not None
                or not parsed.port or parsed.path not in ("/", "/app", "/desktop")
                or parsed.query or parsed.fragment
                or value != "http://127.0.0.1:" + str(parsed.port) + parsed.path):
            raise BrowserError("browser_invalid_config")
        return value
    except (TypeError, ValueError, AttributeError):
        raise BrowserError("browser_invalid_config") from None


def _finite(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _observe_exit(process):
    """Observe our direct child without releasing its PID/session identity."""
    try:
        options = os.WEXITED | os.WNOWAIT | os.WNOHANG
        result = os.waitid(os.P_PID, process.pid, options)
        return result is not None
    except (OSError, ValueError, AttributeError, NotImplementedError):
        # Do not substitute Popen.poll(): it reaps the leader and permits PID
        # reuse before the group has been cleaned up.
        raise BrowserError("browser_cleanup_failed") from None


def _active_owned_group(pgid, sid):
    """Bounded /proc stat metadata scan; no cmdline, environment or user files."""
    deadline, examined = time.monotonic() + .25, 0
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                examined += 1
                if examined > 8192 or time.monotonic() > deadline:
                    raise BrowserError("browser_cleanup_failed")
                if not entry.name.isascii() or not entry.name.isdigit():
                    continue
                parent = descriptor = None
                try:
                    parent = os.open(entry.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                    descriptor = os.open("stat", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                         dir_fd=parent)
                    raw = os.read(descriptor, 8193)
                    boundary = raw.rfind(b")")
                    if len(raw) > 8192 or boundary < 0 or raw.split(b" ", 1)[0] != entry.name.encode("ascii"):
                        raise BrowserError("browser_cleanup_failed")
                    fields = raw[boundary + 2:].split()
                    state, group, session = fields[0], int(fields[2]), int(fields[3])
                    if group == pgid:
                        if session != sid:
                            raise BrowserError("browser_cleanup_failed")
                        if state not in (b"Z", b"X", b"x"):
                            return True
                except (FileNotFoundError, ProcessLookupError):
                    pass  # An unrelated process may have exited while scanning.
                finally:
                    for value in (descriptor, parent):
                        if value is not None:
                            os.close(value)
        return False
    except (OSError, ValueError, TypeError, IndexError):
        raise BrowserError("browser_cleanup_failed") from None


class DesktopBrowser:
    def __init__(self, url, *, windowed=False, frontend="chromium", executable=None, runtime_root=None, environ=None,
                 process_factory=None, group_signaler=None, clock=time.monotonic,
                 startup_timeout=10.0, stop_timeout=2.0, observer=None, group_probe=None,
                 cleanup_clock=time.monotonic, cleanup_sleep=time.sleep):
        self._url = _url(url)
        if (type(windowed) is not bool or frontend not in ("chromium", "native")
                or not _finite(startup_timeout, 1, 60)
                or not _finite(stop_timeout, .05, 10) or not all(map(callable, (clock, cleanup_clock, cleanup_sleep)))):
            raise BrowserError("browser_invalid_config")
        self.windowed, self.clock, self.frontend = windowed, clock, frontend
        self.startup_timeout, self.stop_timeout = startup_timeout, stop_timeout
        source = os.environ if environ is None else environ
        self._environ = {name: source.get(name) for name in (
            "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "XAUTHORITY")}
        self._executable, self._runtime_root = executable, runtime_root
        self._factory = process_factory or subprocess.Popen
        self._signal_group = group_signaler or os.killpg
        self._observer = observer or _observe_exit
        self._group_probe = group_probe or _active_owned_group
        self._real_observation = observer is None and group_probe is None
        self._cleanup_clock, self._cleanup_sleep = cleanup_clock, cleanup_sleep
        self._process = self._profile_parent = self._profile_name = self._profile_info = None
        self._pgid = None
        self._reaped = False
        self._started = None
        self._state, self._code, self._ready, self._cleaned = "idle", "browser_stopped", False, True
        self._cleanup_stage, self._cleanup_code = "not_started", "not_acquired"
        self._cleanup_trace = deque(maxlen=24)
        self._group_quiet, self._profile_acquired = None, False

    def snapshot(self):
        return {"state": self._state, "code": self._code, "ready": self._ready,
                "frontend": self.frontend, "windowed": self.windowed, "owned_child": self._pgid is not None and not self._cleaned,
                "private_profile": self._profile_name is not None, "cleanup_complete": self._cleaned,
                "automatic_relaunch": False, "cleanup": self.cleanup_snapshot()}

    def cleanup_snapshot(self):
        """Only owned resource categories, never PID/argv/profile/session data."""
        root = "not_acquired" if self._process is None else "released" if self._reaped else "unknown"
        group = "not_acquired" if self._process is None else "released" if self._group_quiet else "unknown"
        profile = ("not_acquired" if not self._profile_acquired else
                   "released" if self._profile_parent is None and self._cleaned else "unknown")
        return {"version": 1, "scope": "owned_bmo_native_window" if self.frontend == "native" else "owned_chromium_only",
                "resources_cleaned": all(item != "unknown" for item in (root, group, profile)),
                "stage": self._cleanup_stage, "code": self._cleanup_code,
                "resources": {"root": root, "process_group": group, "private_profile": profile},
                "trace": [dict(item) for item in self._cleanup_trace]}

    def _cleanup_checkpoint(self, stage, code="pending"):
        self._cleanup_stage, self._cleanup_code = stage, code
        self._cleanup_trace.append({"stage": stage, "code": code})

    def _environment(self, profile):
        source = self._environ
        display = source.get("WAYLAND_DISPLAY")
        runtime = source.get("XDG_RUNTIME_DIR")
        if (type(display) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", display) is None
                or type(runtime) is not str):
            raise BrowserError("browser_environment_missing")
        checked = _directory(runtime, "runtime_directory_permissions")
        os.close(checked)
        result = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                  "WAYLAND_DISPLAY": display, "XDG_RUNTIME_DIR": runtime,
                  "HOME": str(profile), "XDG_CONFIG_HOME": str(profile / "config"),
                  "XDG_CACHE_HOME": str(profile / "cache"), "XDG_DATA_HOME": str(profile / "data")}
        bus = source.get("DBUS_SESSION_BUS_ADDRESS")
        if (type(bus) is str and 1 <= len(bus) <= 4096 and bus.startswith(("unix:path=", "unix:abstract="))
                and ";" not in bus and all(32 <= ord(char) < 127 for char in bus)):
            result["DBUS_SESSION_BUS_ADDRESS"] = bus
        local_display = source.get("DISPLAY")
        if type(local_display) is str and re.fullmatch(r":[0-9]{1,4}(?:\.[0-9]{1,2})?", local_display):
            result["DISPLAY"] = local_display
        # No XAUTHORITY body is read; an explicitly supplied regular owned
        # authority file is passed only as session metadata for VNC setups.
        authority = source.get("XAUTHORITY")
        if type(authority) is str and len(authority) <= 512 and "\0" not in authority:
            try:
                path, info = Path(authority), Path(authority).lstat()
                authority_name = path.name == ".Xauthority" or re.fullmatch(r"(?:xauth|Xauthority)[A-Za-z0-9_.-]{0,80}", path.name)
                if (authority_name and path.is_absolute() and ".." not in path.parts and stat.S_ISREG(info.st_mode)
                        and info.st_uid == os.geteuid() and info.st_nlink == 1 and not info.st_mode & 0o022):
                    result["XAUTHORITY"] = authority
            except OSError:
                pass
        return result

    def _profile(self):
        runtime = self._runtime_root if self._runtime_root is not None else self._environ.get("XDG_RUNTIME_DIR")
        parent = _directory(runtime, "runtime_directory_permissions")
        try:
            for _ in range(10):
                name = "bmo-chromium-" + secrets.token_hex(12)
                try:
                    os.mkdir(name, 0o700, dir_fd=parent)
                except FileExistsError:
                    continue
                self._profile_parent, self._profile_name = parent, name
                self._profile_info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                self._profile_acquired = True
                self._cleaned = False
                return Path(runtime) / name
            raise BrowserError("browser_profile_failed")
        finally:
            if self._profile_parent is None:
                os.close(parent)

    def _cleanup_profile(self):
        if self._profile_parent is None:
            self._cleaned = True
            return
        try:
            info = os.stat(self._profile_name, dir_fd=self._profile_parent, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or (info.st_dev, info.st_ino) != (self._profile_info.st_dev, self._profile_info.st_ino)):
                raise BrowserError("browser_cleanup_failed")
            shutil.rmtree(self._profile_name, dir_fd=self._profile_parent)
            os.close(self._profile_parent)
            self._profile_parent = self._profile_name = self._profile_info = None
            self._cleaned = True
        except (OSError, TypeError, ValueError):
            raise BrowserError("browser_cleanup_failed") from None

    def start(self):
        if self._state in ("starting", "running"):
            return self
        if self._state != "idle":
            raise BrowserError("browser_invalid_config")
        try:
            if self.frontend == "native":
                # Reuse the same owned lifecycle for the off-record Qt WebEngine.
                from .desktop_native_window import command as native_command
                try:
                    arguments = native_command(self._url, windowed=self.windowed)
                except (ValueError, OSError):
                    raise BrowserError("native_window_unavailable") from None
            else:
                candidates = (self._executable,) if self._executable is not None else (
                    "/usr/lib/chromium/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser")
                executable = None
                for candidate in candidates:
                    try:
                        executable = _executable(candidate)
                        break
                    except BrowserError:
                        continue
                if executable is None:
                    raise BrowserError("browser_executable_unsafe")
                arguments = [executable, "--ozone-platform=wayland",
                             "--no-first-run", "--no-default-browser-check",
                             "--disable-session-crashed-bubble", "--disable-background-networking",
                             "--disable-component-update", "--disable-sync",
                             "--disable-extensions", "--mute-audio", "--window-size=800,480",
                             "--app=" + self._url]
                if not self.windowed:
                    arguments.extend(("--kiosk", "--start-fullscreen"))
            profile = self._profile()
            environment = self._environment(profile)
            if self.frontend == "chromium":
                arguments.insert(2, "--user-data-dir=" + str(profile))
            self._process = self._factory(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, env=environment, close_fds=True,
                                          start_new_session=True)
            if type(self._process.pid) is not int or self._process.pid <= 1:
                raise BrowserError("browser_spawn_failed")
            self._pgid = self._process.pid
            if self._real_observation:
                # start_new_session guarantees both identities. Verify before
                # accepting the handle; an unreaped child pins this numeric ID.
                self._observer(self._process)
                if os.getpgid(self._pgid) != self._pgid or os.getsid(self._pgid) != self._pgid:
                    raise BrowserError("browser_cleanup_failed")
            self._started, self._state, self._code = self.clock(), "starting", "browser_starting"
            self.poll()
            if self._state == "failed":
                raise BrowserError(self._code)
            return self
        except BrowserError as error:
            self._state, self._code = "failed", error.code
            if self._process is None:
                self._cleanup_profile()
            raise
        except (OSError, ValueError, TypeError, AttributeError):
            self._state, self._code = "failed", "browser_spawn_failed"
            if self._process is None:
                self._cleanup_profile()
            raise BrowserError(self._code) from None

    def mark_ready(self):
        if self._state != "starting":
            return self._ready and self._state == "running"
        self.poll()
        if self._state != "starting":
            return False
        self._ready, self._state, self._code = True, "running", "browser_ready"
        return True

    def poll(self):
        if self._process is None or self._state in ("stopped", "failed", "exited"):
            return self.snapshot()
        try:
            if self._observer(self._process):
                state, code = (("exited", "browser_closed") if self._ready
                               else ("failed", "browser_start_failed"))
                self._stop(state, code)
            elif not self._ready and self.clock() - self._started >= self.startup_timeout:
                self._stop("failed", "browser_start_timeout")
        except (OSError, ValueError, subprocess.TimeoutExpired, BrowserError):
            self._state, self._code, self._cleaned = "failed", "browser_cleanup_failed", False
        return self.snapshot()

    def _wait_group_quiet(self):
        deadline = self._cleanup_clock() + self.stop_timeout
        while True:
            self._observer(self._process)  # Fail closed if the PID is no longer pinned.
            if not self._group_probe(self._pgid, self._pgid):
                self._group_quiet = True
                return True
            remaining = deadline - self._cleanup_clock()
            if remaining <= 0:
                return False
            self._cleanup_sleep(min(.02, remaining))

    def _wait_root_exit(self):
        # A quiet group scan and waitid becoming waitable are separate kernel
        # observations. One WNOHANG miss must not invent a cleanup failure.
        deadline = self._cleanup_clock() + self.stop_timeout
        while not self._observer(self._process):
            remaining = deadline - self._cleanup_clock()
            if remaining <= 0:
                return False
            self._cleanup_sleep(min(.02, remaining))
        return True

    def _signal_owned_group(self, value):
        # No signal is sent after wait/reap. Until then the direct child pins
        # the new-session leader PID, even when it has already become a zombie.
        if self._reaped or self._pgid is None:
            raise BrowserError("browser_cleanup_failed")
        self._observer(self._process)
        try:
            self._signal_group(self._pgid, value)
        except ProcessLookupError:
            pass

    def _stop(self, state, code):
        try:
            if self._process is not None and not self._reaped:
                self._cleanup_checkpoint("observe_identity")
                if self._pgid is None:
                    raise BrowserError("browser_cleanup_failed")
                self._observer(self._process)
                self._cleanup_checkpoint("inspect_owned_group")
                if self._group_probe(self._pgid, self._pgid):
                    self._cleanup_checkpoint("term_owned_group")
                    self._signal_owned_group(signal.SIGTERM)
                    self._cleanup_checkpoint("wait_owned_group")
                    if not self._wait_group_quiet():
                        self._cleanup_checkpoint("kill_owned_group")
                        self._signal_owned_group(signal.SIGKILL)
                        self._cleanup_checkpoint("wait_owned_group")
                        if not self._wait_group_quiet():
                            raise BrowserError("browser_cleanup_failed")
                else:
                    self._group_quiet = True
                # Do not release the PID before both direct-child exit and an
                # inactive group have been observed. Nonchild zombies are left
                # for their OS parent to reap; no app-global subreaper is set.
                self._cleanup_checkpoint("wait_owned_root")
                if not self._wait_root_exit():
                    raise BrowserError("browser_cleanup_failed")
                self._cleanup_checkpoint("reap_owned_root")
                self._process.wait(timeout=self.stop_timeout)
                self._reaped = True
                self._pgid = None
            self._cleanup_checkpoint("remove_owned_profile")
            self._cleanup_profile()
            self._state, self._code = state, code
            self._cleanup_checkpoint("complete", "resources_cleaned")
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired, BrowserError):
            self._state, self._code, self._cleaned = "failed", "browser_cleanup_failed", False
            self._cleanup_checkpoint(self._cleanup_stage, "unknown")
            raise BrowserError("browser_cleanup_failed") from None

    def stop(self):
        self._stop("stopped", "browser_stopped")
        return self.snapshot()

    close = stop
