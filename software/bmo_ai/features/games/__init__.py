"""Game catalogue and owned runners; construction never launches an app.

Only the coordinator may admit a launch after acquiring mode/audio ownership.
No downloaded game pack, physical pad mapping or external hotkey is implied.
"""
from collections import deque
import math
import os
import signal
import subprocess
import time

from ..contracts import CatalogItem, FeatureError, FeatureEvent, HostResult, request_id_ok, valid_id


_KEYS = frozenset({"UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT", "ESC", "PAD_DISCONNECTED"})


def fixture_catalog():
    """Two synthetic games, deliberately unrelated to Action v0 demo_game."""
    return tuple(CatalogItem(f"fixture_game_{suffix}", f"Synthetic game {suffix.upper()}",
                             "READY", "synthetic_fixture",
                             ("runner_fixture", "normalized_input", "exit_hold", "exit_escape", "audio_stop"), True)
                 for suffix in ("a", "b"))


class FakeGameRunner:
    simulated = verified = True
    capabilities = frozenset({"normalized_input", "exit_hold", "exit_escape", "audio_stop"})

    def __init__(self, *, pausable=False):
        self.capabilities = self.capabilities | ({"pause", "resume"} if pausable else set())
        self.running = self.paused = False
        self.inputs = deque(maxlen=128)
        self._terminal = None

    def start(self, item):
        self.running, self.paused, self._terminal = True, False, None

    def input(self, logical, pressed):
        self.inputs.append((logical, pressed))

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def finish(self, *, crashed=False):
        self._terminal = "failed" if crashed else "ended"

    def poll(self):
        return self._terminal

    def stop(self):
        self.running = self.paused = False
        self._terminal = None

    close = stop


class OwnedProcessRunner:
    """Explicit native/RetroArch adapter; app-specific verification is required.

    command is trusted host configuration, never an intent/catalog field. The
    readiness probe must verify this backend's own startup contract. Tests use
    a harmless Python child; no actual external game is run by the test suite.
    """
    simulated = False

    def __init__(self, command, *, backend="native", verified=False, ready_probe=None,
                 graceful_quit=None, startup_timeout=1.0, stop_timeout=.2, pass_fds=()):
        if (backend not in ("native", "retroarch") or type(verified) is not bool
                or type(command) not in (tuple, list) or not command
                or any(type(v) is not str or not v or len(v) > 4096
                       or any(ord(c) < 32 for c in v) for v in command)):
            raise FeatureError("invalid_runner")
        for duration in (startup_timeout, stop_timeout):
            if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 5:
                raise FeatureError("invalid_runner")
        self.command, self.backend = tuple(command), backend
        self.verified = verified and callable(ready_probe)
        self.ready_probe, self.graceful_quit = ready_probe, graceful_quit
        self.startup_timeout, self.stop_timeout = startup_timeout, stop_timeout
        if type(pass_fds) is not tuple or any(type(fd) is not int or fd < 0 for fd in pass_fds):
            raise FeatureError("invalid_runner")
        self.pass_fds = pass_fds
        self.capabilities = frozenset({"audio_stop", "runner_" + backend})
        # A desktop process does not automatically receive browser pad events.
        self.process = None

    def start(self, item):
        if not self.verified:
            raise FeatureError("backend_unverified")
        if self.process is not None:
            raise FeatureError("runner_busy")
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, pass_fds=self.pass_fds,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"})
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise FeatureError("game_crashed")
                if self.ready_probe(self.process):
                    return
                time.sleep(.005)
            raise FeatureError("startup_timeout")
        except BaseException as error:
            self.stop()
            if isinstance(error, (KeyboardInterrupt, SystemExit, FeatureError)):
                raise
            raise FeatureError("runner_start_failed") from None

    def _signal(self, value):
        try:
            os.killpg(self.process.pid, value)
        except ProcessLookupError:
            pass

    def stop(self):
        process = self.process
        if process is None:
            return
        try:
            if self.graceful_quit is not None and process.poll() is None:
                try:
                    self.graceful_quit(process)
                    process.wait(timeout=self.stop_timeout)
                except Exception:
                    pass
            # The process owns a new session/group; descendants belong to this
            # group even if their parent crashed. Never signal another group.
            self._signal(signal.SIGTERM)
            try:
                process.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                process.wait(timeout=1)
            # A cooperative parent can exit before its descendants.
            self._signal(signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            raise FeatureError("cleanup_failed") from None
        self.process = None

    close = stop

    def poll(self):
        if self.process is None:
            return None
        code = self.process.poll()
        return None if code is None else "ended" if code == 0 else "failed"


class GamesFeature:
    """One admitted game. Terminal events carry the launch generation."""
    def __init__(self, catalog_items=(), runner_factory=None, clock=time.monotonic, fixture=False):
        items = tuple(catalog_items) or (fixture_catalog() if fixture else ())
        if any(type(item) is not CatalogItem for item in items) or len({i.id for i in items}) != len(items):
            raise FeatureError("invalid_catalog")
        self._items, self._factory, self.clock = items, runner_factory, clock
        self._events, self._attempts = deque(maxlen=64), {}
        self._runner = self._active = None
        self._request, self._generation = "games:idle", 0
        self._closed, self._state, self._selected = False, "idle", 0
        self._held, self._hold_since, self._exit_requested = set(), None, False
        self._last_input_time = None

    def catalog(self):
        return self._items

    def snapshot(self):
        return dict(state=self._state, active_id=self._active.id if self._active else None,
                    selected_id=self._items[self._selected].id if self._items else None,
                    readiness="READY" if any(i.readiness == "READY" for i in self._items) else "ASSET_PENDING" if not self._items else self._items[0].readiness,
                    reason_code="ok" if self._items else "game_pack_missing",
                    capabilities=sorted(self._runner.capabilities) if self._runner else [],
                    simulated=self._runner.simulated if self._runner else True,
                    progress_may_be_lost=True)

    def _result(self, status, code="ok", *, completed=False, simulated=None):
        simulated = self._runner.simulated if simulated is None and self._runner else True if simulated is None else simulated
        return HostResult(self._request, status, code, simulated, not simulated and self._active is not None,
                          completed, "simulated" if simulated and completed else "operation_completed" if completed
                          else "running" if self._active else "not_started")

    def start(self, catalog_id, *, request_id, mode_generation):
        if not valid_id(catalog_id) or not request_id_ok(request_id) or type(mode_generation) is not int or mode_generation < 0:
            raise FeatureError("invalid_request")
        if self._closed:
            return HostResult(request_id, "rejected", "feature_closed")
        if request_id in self._attempts:
            code = "duplicate_request" if self._attempts[request_id] == catalog_id else "request_id_conflict"
            return HostResult(request_id, "rejected", code)
        if self._runner is not None:
            return HostResult(request_id, "rejected", "game_busy")
        if len(self._attempts) >= 1024:
            return HostResult(request_id, "rejected", "request_capacity_exceeded")
        item = next((item for item in self._items if item.id == catalog_id), None)
        if item is None or item.readiness != "READY":
            return HostResult(request_id, "rejected", "unknown_game" if item is None else item.reason_code)
        self._attempts[request_id] = catalog_id
        self._request, self._generation = request_id, mode_generation
        runner = None
        try:
            runner = self._factory(item) if self._factory else FakeGameRunner() if item.simulated else None
            if runner is None or not runner.verified:
                raise FeatureError("backend_unverified")
            if bool(runner.simulated) != item.simulated:
                raise FeatureError("runner_mismatch")
            self._runner, self._active = runner, item
            self._state = "starting"
            runner.start(item)
            self._state = "running"
            self._held.clear(); self._hold_since = None; self._exit_requested = False
            self._last_input_time = None
            self._events.append(FeatureEvent("game_started", mode_generation, request_id,
                "synthetic_fixture" if runner.simulated else "process_ready", {"catalog_id": item.id, "simulated": runner.simulated}))
            return self._result("simulated" if runner.simulated else "running", completed=runner.simulated)
        except Exception as error:
            code = error.code if isinstance(error, FeatureError) else "runner_start_failed"
            if runner is not None:
                try:
                    runner.close()
                except Exception:
                    self._runner, self._active, self._state = runner, item, "cleanup_failed"
                    return self._result("unknown", "cleanup_failed")
            self._runner = self._active = None
            self._state = "idle"
            self._events.append(FeatureEvent("game_failed", mode_generation, request_id, code,
                                              {"resource_released": True}))
            return HostResult(request_id, "failed", code)

    def stop(self, reason="user"):
        if self._runner is None:
            return self._result("simulated", "already_stopped", completed=True)
        simulated = self._runner.simulated
        try:
            self._runner.stop()
        except Exception:
            self._state = "cleanup_failed"
            return self._result("unknown", "cleanup_failed")
        self._runner = self._active = None
        self._state = "idle"
        self._held.clear(); self._hold_since = None; self._exit_requested = False
        code = reason if reason in {"user", "game_exit", "pad_disconnected", "mode_change", "shutdown", "game_crashed", "ended"} else "user"
        self._events.append(FeatureEvent("game_failed" if code == "game_crashed" else "game_ended",
            self._generation, self._request, code, {"resource_released": True, "simulated": simulated}))
        return HostResult(self._request, "simulated" if simulated else "succeeded", code, simulated,
                          False, True, "simulated" if simulated else "stopped")

    def _exit(self):
        if not self._exit_requested and self._runner is not None:
            self._exit_requested = True
            self._events.append(FeatureEvent("game_exit_requested", self._generation, self._request))

    def _check_hold(self, now):
        if self._runner and "exit_hold" in self._runner.capabilities and self._hold_since is not None and now - self._hold_since >= 1.2:
            self._exit()

    def game_input(self, logical, pressed=True, now=None):
        now = self.clock() if now is None else now
        if type(logical) is not str or logical.upper() not in _KEYS or type(pressed) is not bool or type(now) not in (int, float) or not math.isfinite(now):
            raise FeatureError("invalid_input")
        key = logical.upper()
        if self._runner is None:
            return {"consumed": False, "action": "none"}
        if self._last_input_time is not None and now < self._last_input_time:
            self._held.clear(); self._hold_since = None
        self._last_input_time = now
        if key == "PAD_DISCONNECTED":
            self.stop("pad_disconnected")
            return {"consumed": True, "action": "stopped"}
        if key == "ESC" and pressed and "exit_escape" in self._runner.capabilities:
            self._exit()
            return {"consumed": True, "action": "exit_requested"}
        if key in ("START", "SELECT"):
            self._held.add(key) if pressed else self._held.discard(key)
            self._hold_since = (now if self._hold_since is None else self._hold_since) if self._held == {"START", "SELECT"} else None
            self._check_hold(now)
        forwarded = "normalized_input" in self._runner.capabilities
        if forwarded:
            self._runner.input(key, pressed)  # In particular, B remains a game control.
        return {"consumed": forwarded, "action": "forwarded" if forwarded else "unverified_input"}

    def menu_input(self, logical, pressed=True, now=None):
        if type(logical) is not str or logical.upper() not in _KEYS or type(pressed) is not bool:
            raise FeatureError("invalid_input")
        key = logical.upper()
        if not pressed:
            return {"consumed": False, "action": "none"}
        if key in ("B", "ESC"):
            return {"consumed": True, "action": "return_to_face"}
        if self._items and key in ("UP", "LEFT", "DOWN", "RIGHT"):
            self._selected = (self._selected + (-1 if key in ("UP", "LEFT") else 1)) % len(self._items)
        item = self._items[self._selected] if self._items else None
        return {"consumed": True, "action": "launch" if key == "A" and item and item.readiness == "READY" else "select",
                "catalog_id": item.id if item else None,
                "code": "ok" if item and item.readiness == "READY" else item.reason_code if item else "game_pack_missing"}

    def pause(self):
        return self._pause(True)

    def resume(self):
        return self._pause(False)

    def _pause(self, pause):
        capability = "pause" if pause else "resume"
        if self._runner is None or capability not in self._runner.capabilities:
            return self._result("unsupported", "pause_unsupported")
        getattr(self._runner, capability)()
        self._state = "paused" if pause else "running"
        return self._result("simulated" if self._runner.simulated else "succeeded", completed=True)

    def poll(self):
        if self._runner is not None:
            try:
                terminal = self._runner.poll()
                if terminal is not None:
                    self.stop("game_crashed" if terminal == "failed" else "ended")
                else:
                    self._check_hold(self.clock())
            except Exception:
                self.stop("game_crashed")
        events = tuple(self._events)
        self._events.clear()
        return events

    def close(self):
        result = self.stop("shutdown")
        if result.status == "unknown":
            raise FeatureError("cleanup_failed")
        self._closed = True
