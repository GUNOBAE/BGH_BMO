"""Explicit, silent SuperTux integration; construction never opens a pad/game.

SDL owns game input. BMO observes only an operator-recorded joystick exit chord
and device loss, and retains its existing owned-process cleanup contract. An
owned mapped X11/Xwayland window is a software readiness observation, never a
claim that a person saw the game or verified its physical controls.
"""
from collections import deque
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time

from ...private_credentials import _open_directory, _private_file, _close, CredentialError
from ..contracts import CatalogItem, FeatureError, FeatureEvent, HostResult, request_id_ok, valid_id
from . import OwnedProcessRunner


GAME_ID = "supertux"
EXECUTABLE = "/usr/games/supertux2"
XPROP = "/usr/bin/xprop"
XWININFO = "/usr/bin/xwininfo"
EXIT_HOLD_SECONDS = 1.5
START_TIMEOUT_SECONDS = 15.
_CAPABILITIES = ("native_supertux", "native_pad_exit", "silent_game", "audio_stop")
_PROFILE_FIELDS = {"version", "game_id", "device", "exit_buttons", "provenance"}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _path(value):
    try:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or "\0" in str(path):
            raise ValueError()
        return path
    except (TypeError, ValueError):
        raise FeatureError("native_private_path_required") from None


def _outside_checkout(path):
    # These are private control/profile paths, not a new game-asset importer.
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise FeatureError("native_private_path_unsafe")
        if parent.is_dir() and (parent / ".git").exists():
            raise FeatureError("native_outside_checkout_required")


def _private_directory(path, *, create=False):
    # A missing leaf is permitted during read-only readiness; launch prepares
    # it privately before Popen. Existing ancestors/leaf may never be symlinks.
    path = _path(path)
    _outside_checkout(path)
    try:
        descriptor = _open_directory(path, create=create)
        _close(descriptor)
        return path
    except (OSError, CredentialError):
        raise FeatureError("native_private_directory_unsafe") from None


def default_game_home():
    return Path.home() / ".local/share/bmo/native-games/supertux"


def _executable(path, missing_code):
    """Only fixed distro executables; no shell, PATH search, user-provided argv."""
    try:
        path = Path(path)
        # /usr/games and /usr/bin are the reviewed distribution locations.
        if str(path) not in (EXECUTABLE, XPROP, XWININFO):
            raise FeatureError("native_executable_unsafe")
        for parent in (path.parent, *path.parent.parents):
            info = parent.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o0022):
                raise FeatureError("native_executable_unsafe")
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o6022 or not info.st_mode & 0o111):
            raise FeatureError("native_executable_unsafe")
        return str(path)
    except FileNotFoundError:
        raise FeatureError(missing_code) from None
    except OSError:
        raise FeatureError("native_executable_unsafe") from None


def native_environment(source, game_home):
    """Copy only session metadata needed for silent X11 SDL; no API secrets.

    A dedicated persistent HOME/userdir keeps game saves/config separate from
    BMO credentials. This is environment isolation, not an OS security sandbox.
    No session bus, audio socket, LD_*, PYTHON*, or original HOME is inherited.
    """
    game_home = _private_directory(game_home)
    display = source.get("DISPLAY")
    if type(display) is not str or re.fullmatch(r":[0-9]{1,4}(?:\.[0-9]{1,2})?", display) is None:
        raise FeatureError("native_x11_session_required")
    result = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
              "DISPLAY": display, "HOME": str(game_home),
              "XDG_CONFIG_HOME": str(game_home / "config"),
              "XDG_CACHE_HOME": str(game_home / "cache"),
              "XDG_DATA_HOME": str(game_home / "data"),
              "SDL_VIDEODRIVER": "x11", "SDL_AUDIODRIVER": "dummy"}
    authority = source.get("XAUTHORITY")
    if not authority:
        # Xlib normally falls back to the original ~/.Xauthority. Isolating
        # HOME would silently lose that authentication. Reuse only its checked
        # path when present; do not read/copy its contents or inherit HOME.
        original_home = source.get("HOME") or str(Path.home())
        fallback = _path(original_home) / ".Xauthority"
        try:
            fallback.lstat()
        except FileNotFoundError:
            pass  # Some local Xwayland sessions use peer-credential access.
        except OSError:
            raise FeatureError("native_xauthority_unsafe") from None
        else:
            authority = str(fallback)
    if authority:
        try:
            path = _path(authority)
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o0022):
                raise ValueError()
            result["XAUTHORITY"] = str(path)
        except (OSError, ValueError, FeatureError):
            raise FeatureError("native_xauthority_unsafe") from None
    return result


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise FeatureError("native_pad_profile_invalid")
        result[name] = value
    return result


@dataclass(frozen=True, repr=False)
class NativePadProfile:
    device: str
    exit_buttons: tuple
    provenance: str

    def __post_init__(self):
        if (type(self.device) is not str or len(self.device) > 512
                or re.fullmatch(r"/dev/input/by-id/[A-Za-z0-9_.:+-]+-joystick", self.device) is None
                or type(self.exit_buttons) is not tuple or len(self.exit_buttons) != 2
                or any(type(v) is not int or not 0 <= v <= 255 for v in self.exit_buttons)
                or self.exit_buttons[0] == self.exit_buttons[1]
                or not valid_id(self.provenance) or not self.provenance.startswith("pi_observed_")):
            raise FeatureError("native_pad_profile_invalid")


def load_pad_profile(path):
    if path is None:
        raise FeatureError("native_pad_profile_required")
    path = _path(path)
    _outside_checkout(path)
    directory = descriptor = None
    try:
        directory = _open_directory(path.parent)
        if directory is None:
            raise FeatureError("native_pad_profile_required")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=directory)
        before = os.fstat(descriptor)
        _private_file(before, size_limit=4096)
        raw = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if not raw or len(raw) != before.st_size or identity(before) != identity(after):
            raise FeatureError("native_pad_profile_invalid")
        value = json.loads(raw, object_pairs_hook=_unique)
        if (type(value) is not dict or set(value) != _PROFILE_FIELDS
                or type(value["version"]) is not int or value["version"] != 1
                or value["game_id"] != GAME_ID or type(value["exit_buttons"]) is not list):
            raise FeatureError("native_pad_profile_invalid")
        return NativePadProfile(value["device"], tuple(value["exit_buttons"]), value["provenance"])
    except FileNotFoundError:
        raise FeatureError("native_pad_profile_required") from None
    except FeatureError:
        raise
    except (OSError, CredentialError, UnicodeError, ValueError, TypeError):
        raise FeatureError("native_pad_profile_invalid") from None
    finally:
        _close(descriptor); _close(directory)


def _joystick_target(profile):
    """Metadata-only device check. The device is opened only after admission."""
    try:
        link = Path(profile.device)
        for parent in (link.parent, *link.parent.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o0022:
                raise FeatureError("native_pad_device_unsafe")
        info = link.lstat()
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0:
            raise FeatureError("native_pad_device_unsafe")
        target = link.resolve(strict=True)
        if re.fullmatch(r"/dev/input/js[0-9]{1,3}", str(target)) is None:
            raise FeatureError("native_pad_device_unsafe")
        info = target.lstat()
        if not stat.S_ISCHR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o002:
            raise FeatureError("native_pad_device_unsafe")
        return str(target), (info.st_dev, info.st_ino, info.st_rdev)
    except FileNotFoundError:
        raise FeatureError("native_pad_not_connected") from None
    except OSError:
        raise FeatureError("native_pad_unavailable") from None


class JoystickExitSource:
    """Read Linux joydev button edges; never grab, remap, write or inject input."""
    def __init__(self, profile):
        self._fd = None
        target, identity = _joystick_target(profile)
        try:
            self._fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            info = os.fstat(self._fd)
            if (not stat.S_ISCHR(info.st_mode) or (info.st_dev, info.st_ino, info.st_rdev) != identity
                    or _joystick_target(profile) != (target, identity)):
                raise FeatureError("native_pad_device_changed")
            button_count = bytearray(1)
            # JSIOCGBUTTONS: read-only Linux joydev capability query. A profile
            # cannot make its exit chord reachable by merely asserting numbers.
            fcntl.ioctl(self._fd, 0x80016A12, button_count, True)
            if not button_count[0] or any(index >= button_count[0] for index in profile.exit_buttons):
                raise FeatureError("native_pad_mapping_mismatch")
        except FeatureError:
            self.close()
            raise
        except OSError:
            self.close()
            raise FeatureError("native_pad_unavailable") from None

    def poll(self):
        if self._fd is None:
            raise FeatureError("native_pad_disconnected")
        try:
            raw = os.read(self._fd, 8 * 64)
        except BlockingIOError:
            return ()
        except OSError:
            raise FeatureError("native_pad_disconnected") from None
        if not raw or len(raw) % 8:
            raise FeatureError("native_pad_disconnected")
        return tuple((button, bool(value), bool(kind & 0x80))
                     for _, value, kind, button in struct.iter_unpack("<IhBB", raw)
                     if (kind & 0x7f) == 1)

    def close(self):
        if self._fd is not None:
            descriptor, self._fd = self._fd, None
            os.close(descriptor)


class ExitChord:
    """Only fresh, continuous observed buttons; startup-held buttons cannot exit."""
    def __init__(self, buttons):
        if (type(buttons) is not tuple or len(buttons) != 2 or len(set(buttons)) != 2
                or any(type(v) is not int or not 0 <= v <= 255 for v in buttons)):
            raise FeatureError("native_pad_profile_invalid")
        self.buttons = frozenset(buttons)
        self.held, self.blocked = set(), set()
        self.since = self.last = None
        self.requested = False

    def update(self, events, now):
        if not _number(now) or self.last is not None and now < self.last:
            raise FeatureError("native_clock_invalid")
        self.last = now
        if type(events) not in (tuple, list) or len(events) > 64:
            raise FeatureError("native_pad_input_invalid")
        for event in events:
            if (type(event) not in (tuple, list) or len(event) != 3
                    or type(event[0]) is not int or not 0 <= event[0] <= 255
                    or type(event[1]) is not bool or type(event[2]) is not bool):
                raise FeatureError("native_pad_input_invalid")
            button, pressed, initial = event
            if button not in self.buttons:
                continue
            if initial:
                self.held.discard(button)
                if pressed:
                    self.blocked.add(button)
                else:
                    self.blocked.discard(button)
            elif not pressed:
                self.held.discard(button); self.blocked.discard(button)
            elif button not in self.blocked:
                self.held.add(button)
            if self.held != self.buttons:
                self.since = None
            elif self.since is None:
                self.since = now
        if self.since is not None and now - self.since >= EXIT_HOLD_SECONDS:
            self.requested = True
        return self.requested


class X11WindowProbe:
    """Bounded metadata queries for the active mapped window of the exact child.

    Does not send focus/keys, enumerate titles, inspect environments, or reap a
    child. xprop/xwininfo are distribution binaries, separate from game argv.
    """
    def __init__(self, environment, *, run=None):
        self.environment = dict(environment)
        self._run = run or subprocess.run
        self.window_detected = False

    def _read(self, command):
        try:
            result = self._run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=self.environment, timeout=.25, check=False)
            if result.returncode != 0 or len(result.stdout) > 32768:
                return ""
            return result.stdout.decode("ascii", "replace")
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def __call__(self, pid):
        if type(pid) is not int or pid <= 0:
            return False
        raw = self._read([XPROP, "-root", "_NET_ACTIVE_WINDOW"])
        match = re.fullmatch(r"_NET_ACTIVE_WINDOW\(WINDOW\): window id # (0x[0-9a-fA-F]+)\s*", raw)
        if not match or int(match[1], 16) == 0:
            return False
        window = match[1]
        raw = self._read([XPROP, "-id", window, "_NET_WM_PID"])
        match = re.fullmatch(r"_NET_WM_PID\(CARDINAL\) = ([0-9]+)\s*", raw)
        if not match or int(match[1]) != pid:
            return False
        raw = self._read([XWININFO, "-id", window])
        mapped = re.search(r"^\s*Map State:\s*IsViewable\s*$", raw, re.MULTILINE)
        size = [re.search(r"^\s*" + field + r":\s*([0-9]+)\s*$", raw, re.MULTILINE)
                for field in ("Width", "Height")]
        ready = bool(mapped and all(value and 0 < int(value[1]) <= 16384 for value in size))
        self.window_detected = ready
        return ready


class NativeGameRunner(OwnedProcessRunner):
    """Asynchronous readiness over the existing pinned process-group lifetime."""
    def __init__(self, *, command, environment, pad_factory, window_probe, exit_buttons,
                 clock=time.monotonic, prepare=None, startup_timeout=START_TIMEOUT_SECONDS,
                 stop_timeout=.2):
        if (not callable(pad_factory) or not callable(window_probe) or not callable(clock)
                or prepare is not None and not callable(prepare)
                or not _number(startup_timeout) or not 1 <= startup_timeout <= 30
                or type(environment) is not dict
                or any(type(k) is not str or type(v) is not str or "\0" in k + v
                       for k, v in environment.items())):
            raise FeatureError("invalid_native_runner")
        super().__init__(command, verified=True, ready_probe=window_probe, stop_timeout=stop_timeout)
        self.environment = dict(environment)
        self.pad_factory, self.window_probe, self.clock, self.prepare = pad_factory, window_probe, clock, prepare
        self.exit_buttons, self.ready_timeout = exit_buttons, startup_timeout
        self.capabilities = frozenset(_CAPABILITIES)
        self._pad = self._chord = None
        self._deadline = self._next_probe = None
        self.ready = self.window_detected = self.exit_requested = False
        self.failure_code = "ok"

    @property
    def owns_resources(self):
        return self.process is not None or self._pad is not None

    def start(self, item):
        if self.owns_resources:
            raise FeatureError("runner_busy")
        now = self.clock()
        if not _number(now):
            raise FeatureError("native_clock_invalid")
        self._chord = ExitChord(self.exit_buttons)
        try:
            if self.prepare:
                self.prepare()
            self._pad = self.pad_factory()
            self.process = subprocess.Popen(self.command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.environment,
                start_new_session=True, close_fds=True)
            self._pgid = self.process.pid
            self._paused = False
            self.ready = self.window_detected = self.exit_requested = False
            self.failure_code = "ok"
            self._deadline, self._next_probe = now + self.ready_timeout, now
        except Exception as error:
            self.stop()
            raise FeatureError(error.code if isinstance(error, FeatureError) else "native_spawn_failed") from None

    def poll(self):
        if self.process is None:
            return None
        code = self._exit_status()
        if code is not None:
            self.failure_code = "native_exited" if code == 0 and self.ready else "native_game_crashed"
            return "ended" if code == 0 and self.ready else "failed"
        now = self.clock()
        try:
            if not _number(now):
                raise FeatureError("native_clock_invalid")
            self.exit_requested = self._chord.update(self._pad.poll(), now)
            if not self.ready:
                if now >= self._deadline:
                    raise FeatureError("native_window_timeout")
                if now >= self._next_probe:
                    self._next_probe = now + .2
                    self.ready = self.window_detected = self.window_probe(self.process.pid) is True
        except Exception as error:
            self.failure_code = error.code if isinstance(error, FeatureError) else "native_runtime_failed"
            return "failed"
        return None

    def stop(self):
        error = None
        try:
            super().stop()
        except Exception as caught:
            error = caught
        try:
            if self._pad is not None:
                self._pad.close()
                self._pad = None
        except Exception:
            error = FeatureError("cleanup_failed")
        if error is not None:
            raise FeatureError("cleanup_failed") from None
        self.ready = self.exit_requested = False
        self._deadline = self._next_probe = None

    close = stop


class NativeGamesFeature:
    """One explicitly configured SuperTux, with honest asynchronous state."""
    def __init__(self, *, item, runner_factory=None, clock=time.monotonic, exit_mapping=False):
        if type(item) is not CatalogItem or item.id != GAME_ID or item.simulated:
            raise FeatureError("invalid_native_catalog")
        self._item, self._factory, self.clock = item, runner_factory, clock
        self._runner = None
        self._request, self._generation = "native:idle", 0
        self._attempts, self._events = {}, deque(maxlen=64)
        self._closed = self._started_event = self._exit_event = False
        self._window_detected = False
        self._exit_mapping = exit_mapping or runner_factory is not None and item.readiness == "READY"
        self._state = ("ready" if item.readiness == "READY" else "not_installed"
                       if item.reason_code == "native_supertux_not_installed" else "setup_required")
        self._code = item.reason_code

    @property
    def native_active(self):
        return self._runner is not None

    @property
    def native_foreground(self):
        return self.native_active

    def is_native(self, game_id):
        return game_id == GAME_ID

    def catalog(self):
        return (self._item,)

    def snapshot(self):
        native = dict(enabled=True, state=self._state, code=self._code, active=self.native_active,
            window_detected=self._window_detected, physical_input_verified=False,
            input_owner="supertux_sdl" if self.native_active else "none",
            exit_mapping="reviewed" if self._exit_mapping else "missing",
            exit_hold_seconds=EXIT_HOLD_SECONDS, audio_scope="silent_native",
            master_volume_applies=False, progress_may_be_lost=True)
        return dict(state=self._state, active_id=GAME_ID if self.native_active else None,
            selected_id=GAME_ID, readiness=self._item.readiness, reason_code=self._code,
            capabilities=list(_CAPABILITIES) if self.native_active else [], simulated=False,
            packaged=False, web=None, native=native, physical_verified=False, progress_may_be_lost=True)

    def _event(self, kind, code, *, released=False):
        self._events.append(FeatureEvent(kind, self._generation, self._request, code,
            {"backend": "native", "catalog_id": GAME_ID, "simulated": False,
             "resource_released": released, "physical_input_verified": False}))

    def start(self, catalog_id, *, request_id, mode_generation):
        if not valid_id(catalog_id) or not request_id_ok(request_id) or type(mode_generation) is not int or mode_generation < 0:
            raise FeatureError("invalid_request")
        if self._closed:
            return HostResult(request_id, "rejected", "feature_closed", simulated=False)
        if request_id in self._attempts:
            return HostResult(request_id, "rejected", "duplicate_request" if self._attempts[request_id] == catalog_id
                              else "request_id_conflict", simulated=False)
        if self.native_active:
            return HostResult(request_id, "rejected", "game_busy", simulated=False)
        if catalog_id != GAME_ID or self._item.readiness != "READY":
            return HostResult(request_id, "rejected", "unknown_game" if catalog_id != GAME_ID else self._item.reason_code,
                              simulated=False)
        if len(self._attempts) >= 1024:
            return HostResult(request_id, "rejected", "request_capacity_exceeded", simulated=False)
        self._attempts[request_id] = catalog_id
        self._request, self._generation = request_id, mode_generation
        self._window_detected = self._started_event = self._exit_event = False
        try:
            runner = self._factory(self._item) if self._factory else None
            if runner is None or runner.simulated or runner.verified is not True:
                raise FeatureError("backend_unverified")
            self._runner = runner
            self._state, self._code = "starting", "native_game_starting"
            runner.start(self._item)
            return HostResult(request_id, "accepted", self._code, simulated=False, completion="not_started")
        except Exception as error:
            code = error.code if isinstance(error, FeatureError) else "native_spawn_failed"
            if self._runner is not None:
                try:
                    self._release()
                except FeatureError:
                    return HostResult(request_id, "unknown", "cleanup_failed", simulated=False)
            self._state, self._code = "failed", code
            self._event("game_failed", code, released=True)
            return HostResult(request_id, "failed", code, simulated=False)

    def _release(self):
        try:
            if self._runner is not None:
                self._runner.stop()
                if self._runner.owns_resources:
                    raise FeatureError("cleanup_failed")
        except Exception:
            self._state, self._code = "cleanup_failed", "cleanup_failed"
            raise FeatureError("cleanup_failed") from None
        self._runner = None

    def stop(self, reason="user"):
        if self._runner is None:
            return HostResult(self._request, "succeeded", "already_stopped", simulated=False,
                              completed=True, completion="stopped")
        try:
            self._release()
        except FeatureError:
            return HostResult(self._request, "unknown", "cleanup_failed", simulated=False)
        code = reason if reason in ("user", "game_exit", "pad_disconnected", "mode_change", "shutdown", "ended") else "user"
        self._state, self._code = "exited", "native_exited"
        self._event("game_ended", code, released=True)
        return HostResult(self._request, "succeeded", code, simulated=False, executed=True,
                          completed=True, completion="stopped")

    def poll(self):
        runner = self._runner
        if runner is not None and self._state != "cleanup_failed":
            try:
                terminal = runner.poll()
                self._window_detected = self._window_detected or runner.window_detected
                if terminal is not None:
                    code = runner.failure_code
                    self._release()
                    self._state, self._code = ("exited", "native_exited") if terminal == "ended" else ("failed", code)
                    self._event("game_ended" if terminal == "ended" else "game_failed", self._code, released=True)
                elif runner.exit_requested and not self._exit_event:
                    self._exit_event = True
                    self._event("game_exit_requested", "native_pad_exit")
                elif runner.ready and not self._started_event:
                    self._started_event = True
                    self._state, self._code = "running", "native_window_ready"
                    self._event("game_started", self._code)
            except Exception:
                if self._state == "cleanup_failed":
                    self._event("game_failed", "cleanup_failed")
                else:
                    try:
                        self._release()
                    except FeatureError:
                        self._event("game_failed", "cleanup_failed")
                    else:
                        self._state, self._code = "failed", "native_runtime_failed"
                        self._event("game_failed", self._code, released=True)
        events = tuple(self._events)
        self._events.clear()
        return events

    def game_input(self, logical, pressed=True, now=None):
        # Browser/old-generation pad data never injects into an external game.
        return {"consumed": False, "action": "native_input_owned"}

    def menu_input(self, logical, pressed=True, now=None):
        if not pressed:
            return {"consumed": False, "action": "none"}
        if logical in ("B", "ESC"):
            return {"consumed": True, "action": "return_to_face"}
        return {"consumed": True, "action": "launch" if logical in ("A", "START") and self._item.readiness == "READY" else "select",
                "catalog_id": GAME_ID, "code": self._item.reason_code}

    def pause(self):
        return HostResult(self._request, "unsupported", "pause_unsupported", simulated=False)

    resume = pause

    def close(self):
        result = self.stop("shutdown")
        if result.status == "unknown":
            raise FeatureError("cleanup_failed")
        self._closed = True


def build_native_supertux(*, profile_path=None, clock=time.monotonic, environment=None):
    """Read-only prerequisites now; device/process/profile creation on start only."""
    source = os.environ if environment is None else environment
    # Snapshot only the needed fields; never retain the caller's full environment.
    session = {name: source.get(name) for name in ("DISPLAY", "XAUTHORITY", "HOME")}
    home = default_game_home()
    profile = None
    try:
        binary = _executable(EXECUTABLE, "native_supertux_not_installed")
        _executable(XPROP, "native_x11_tools_missing")
        _executable(XWININFO, "native_x11_tools_missing")
        profile = load_pad_profile(profile_path)
        _joystick_target(profile)
        environment = native_environment(session, home)
        item = CatalogItem(GAME_ID, "SuperTux", "READY", "native_ready_to_launch", _CAPABILITIES, False)
        def factory(_):
            def prepare():
                _executable(EXECUTABLE, "native_supertux_not_installed")
                _executable(XPROP, "native_x11_tools_missing")
                _executable(XWININFO, "native_x11_tools_missing")
                # Recheck the exact consent/exit mapping, not a replaced profile.
                if load_pad_profile(profile_path) != profile:
                    raise FeatureError("native_pad_profile_changed")
                _private_directory(home, create=True)
                _private_directory(home / "userdata", create=True)
            return NativeGameRunner(command=(binary, "--fullscreen", "--disable-sound", "--disable-music",
                "--userdir", str(home / "userdata")), environment=environment,
                pad_factory=lambda: JoystickExitSource(profile), window_probe=X11WindowProbe(environment),
                exit_buttons=profile.exit_buttons, clock=clock, prepare=prepare)
        return NativeGamesFeature(item=item, runner_factory=factory, clock=clock, exit_mapping=True)
    except (FeatureError, OSError) as error:
        code = error.code if isinstance(error, FeatureError) else "native_setup_unavailable"
        readiness = "RUNTIME_MISSING" if code in ("native_supertux_not_installed", "native_x11_tools_missing") else "UNVERIFIED"
        return NativeGamesFeature(item=CatalogItem(GAME_ID, "SuperTux", readiness, code, _CAPABILITIES, False),
                                  clock=clock, exit_mapping=profile is not None)
