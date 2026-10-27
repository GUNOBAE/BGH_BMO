"""Owner-thread gate for explicitly enabled standard browser gamepad events.

This module does not open HID/evdev/USB. Browser normalization is not physical
verification. Game controls cannot request speech, motors or global actions.
"""
import math
import time

from ..contracts import FeatureError


GAMEPAD_KEYS = frozenset({"UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START",
                          "SELECT", "ESC", "PAD_LOST", "HEARTBEAT", "RELEASE"})


def validate_gamepad(value):
    return (type(value) is dict and set(value) == {"key", "pressed", "sequence", "generation"}
            and type(value["key"]) is str and value["key"] in GAMEPAD_KEYS
            and type(value["pressed"]) is bool and type(value["sequence"]) is int
            and 0 <= value["sequence"] < 2**53
            and type(value["generation"]) is int and 0 <= value["generation"] < 2**53
            and (value["key"] not in ("PAD_LOST", "HEARTBEAT", "RELEASE") or value["pressed"]))


class BrowserGamepadGate:
    WATCHDOG_S = .8

    def __init__(self, router, *, enabled=False, clock=time.monotonic):
        if type(enabled) is not bool:
            raise FeatureError("invalid_gamepad_configuration")
        self.router, self.enabled, self.clock = router, enabled, clock
        self._held, self._generation, self._last_seen = set(), None, None
        self._sequence = -1
        self._closed = False
        self._accepted = self._dropped = 0
        self._code = "gamepad_waiting" if enabled else "gamepad_disabled"

    def release(self, *, disconnected=False):
        # Release only into the same mode generation that admitted the press.
        same = self.router.modes.generation == self._generation
        try:
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now):
                now = 0.
        except Exception:
            now = 0.
        held, self._held = self._held, set()
        if same:
            for key in sorted(held):
                self.router.route(key, False, now=now)
            if disconnected and self.router.modes.mode == "game":
                self.router.route("PAD_LOST", True, now=now)
        self._last_seen = None

    def feed(self, value):
        if not validate_gamepad(value):
            raise FeatureError("invalid_gamepad_input")
        if not self.enabled or self._closed:
            raise FeatureError("gamepad_disabled")
        if value["sequence"] <= self._sequence:
            raise FeatureError("repeated_gamepad_input")
        # Consume sequence even on a rejected mode; delayed edges cannot revive.
        self._sequence = value["sequence"]
        generation = self.router.modes.generation
        if value["generation"] != generation or self.router.modes.mode not in ("game", "game_menu"):
            self._dropped += 1
            self._code = "gamepad_mode_blocked"
            self.release()
            raise FeatureError(self._code)
        if self._generation != generation:
            self.release()
            self._generation = generation
        try:
            now = self.clock()
        except Exception:
            now = None
        if type(now) not in (int, float) or not math.isfinite(now):
            self.release(disconnected=True)
            raise FeatureError("gamepad_clock_invalid")
        self._last_seen = now
        key, pressed = value["key"], value["pressed"]
        if key == "HEARTBEAT":
            self._code = "gamepad_active"
            return None
        if key == "PAD_LOST":
            self.release(disconnected=True)
            self._code = "gamepad_disconnected"
            return None
        if key == "RELEASE":
            self.release()
            self._code = "gamepad_suspended"
            return None
        if pressed == (key in self._held):
            self._dropped += 1
            raise FeatureError("repeated_gamepad_input")
        self._held.add(key) if pressed else self._held.discard(key)
        self._accepted += 1
        self._code = "gamepad_active"
        return self.router.route(key, pressed, now=now)

    def poll(self):
        if self._last_seen is None:
            return
        try:
            now = self.clock()
        except Exception:
            now = None
        if (type(now) not in (int, float) or not math.isfinite(now)
                or now < self._last_seen or now - self._last_seen > self.WATCHDOG_S):
            self.release(disconnected=True)
            self._code = "gamepad_timeout"

    def close(self):
        self.release(disconnected=True)
        self._closed = True

    def snapshot(self):
        return {"enabled": self.enabled and not self._closed,
                "mapping": "browser_standard_game_only", "physical_verified": False,
                "device_opens": 0, "accepted_edges": self._accepted,
                "sequence_floor": self._sequence,
                "dropped_edges": self._dropped, "held": sorted(self._held),
                "code": self._code}
