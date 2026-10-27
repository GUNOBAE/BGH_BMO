"""Pure CircuitPython/CPython v2 arm protocol core. NO GPIO, serial, or PWM imports.

Staging only: no live host transport and no assumption about existing firmware.
A physical power cutoff is necessary; PWM deinit is NOT a hard emergency stop.
"""
import time

MAX_FRAME = 128
MAX_COMMANDS = 32
WATCHDOG_S = 0.35
TICK_S = 0.02
STEP_US = 10
MANUAL_STEP_US = 45  # preserve the original full 0/180 travel, ramped on each tick


def _identifier(text):
    return (type(text) is str and 1 <= len(text) <= 32
            and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in text))


def _pulse(text):
    if not text or not text.isdigit() or len(text) > 4:
        raise ValueError("invalid pulse")
    return int(text)


def decode_frame(frame):
    """One bounded, unambiguous ASCII/LF command; deliberately NOT old JSON v1."""
    if type(frame) is not bytes or not 1 <= len(frame) <= MAX_FRAME or not frame.endswith(b"\n"):
        raise ValueError("invalid frame")
    try:
        raw = frame[:-1].decode("ascii")
    except UnicodeError:
        raise ValueError("invalid frame") from None
    if "\r" in raw or "\n" in raw or "  " in raw or raw != raw.strip():
        raise ValueError("invalid frame")
    terms = raw.split(" ")
    if len(terms) < 3 or terms[0] != "V2":
        raise ValueError("invalid frame")
    op = terms[1]
    if op in ("HELLO", "STOP") and len(terms) == 3 and _identifier(terms[2]):
        return (op, terms[2])
    if op == "ARM" and len(terms) == 4 and _identifier(terms[2]) and _identifier(terms[3]):
        return (op, terms[2], terms[3])
    if (op == "PING" and len(terms) == 5 and _identifier(terms[2])
            and _identifier(terms[3]) and terms[4].isdigit() and len(terms[4]) <= 10):
        n = int(terms[4])
        if n <= 4294967295:
            return (op, terms[2], terms[3], n)
    if (op == "MOVE" and len(terms) == 6 and _identifier(terms[2])
            and _identifier(terms[3]) and terms[4] in ("left", "right")):
        return (op, terms[2], terms[3], terms[4], _pulse(terms[5]))
    raise ValueError("invalid frame")


class SafeArmController:
    """One owner; manual calibration and a wired physical interlock are mandatory.

    io.arm(dict) initializes PWM only after all gates pass. io.set_pulse is
    bounded by reviewed calibration. io.stop disables outputs, not servo power.
    position_verified is intentionally always False (no position feedback).
    """

    def __init__(self, io, boot_id, *, settings=None, physical_enabled=None, clock=None):
        if not _identifier(boot_id):
            raise ValueError("boot id missing")
        self.io = io
        self.boot_id = boot_id
        self.settings = settings
        self.physical_enabled = physical_enabled or (lambda: False)
        self.clock = clock or time.monotonic
        self.armed = False
        self.stop_latched = False
        self.last_ping = None
        self.last_tick = None
        self.ping_counter = -1
        self.positions = {}
        self.targets = {}
        self.requests = {}
        self.stop_reason = "boot_disabled"
        self.owner = None  # None, 'host', or 'red_button'
        self.manual_up = True  # original physical up=left 600us/right 2400us
        # Merely constructing this policy NEVER touches PWM or GPIO.

    def _calibration_valid(self):
        s = self.settings
        if type(s) is not dict or set(s) != {"reviewed", "left", "right"} or s["reviewed"] is not True:
            return False
        for side in ("left", "right"):
            v = s[side]
            if type(v) is not dict or set(v) != {"min_us", "home_us", "max_us"}:
                return False
            a, b, c = v["min_us"], v["home_us"], v["max_us"]
            # Home may equal a bound: the user physically left both arms at
            # their previous fully raised command (left 600us, right 2400us).
            # Never invent travel *beyond* the observed endpoint to force a
            # strictly interior home. The interval still must have nonzero width.
            if (any(type(n) is not int for n in (a, b, c))
                    or not 500 <= a <= b <= c <= 2500 or a == c):
                return False
        return True

    def _physical(self):
        try:
            return self.physical_enabled() is True
        except Exception:
            return False

    def _ack(self, request_id, code):
        # No controller position feedback exists. Never claim movement completed.
        rid = request_id if _identifier(request_id) else "invalid"
        return ("V2 ACK %s %s %s POSITION_UNVERIFIED\n" %
                (rid, self.boot_id, code)).encode("ascii")

    def emergency_stop(self, reason="emergency_stop"):
        """Software STOP latch: never claim this removes physical motor power."""
        self._stop(reason)

    def _stop(self, reason):
        self.armed = False
        self.stop_latched = True
        self.stop_reason = reason
        self.targets = {}
        self.last_ping = None
        self.owner = None
        try:
            self.io.stop()
        except Exception:
            self.stop_reason = "output_release_unknown"

    def manual_toggle(self):
        """Preserve verified GP7: each *press* toggles both arms up/down.

        Legacy convention, not a new calibration: up is left 600/right 2400;
        down is left 2400/right 600. Uses each arm's independently reviewed
        bounds and only runs in an explicitly motor-enabled build. The first
        PWM is aligned to the user's last physically confirmed UP position.
        STOP latch cannot be defeated by a button press.
        """
        if self.stop_latched or not self._calibration_valid() or not self._physical():
            return False
        s = self.settings
        if not (s["left"]["home_us"] == s["left"]["min_us"]
                and s["right"]["home_us"] == s["right"]["max_us"]):
            return False  # Never infer a new physical home.
        if not self.armed:
            first = {side: s[side]["home_us"] for side in ("left", "right")}
            try:
                self.io.arm(first)
            except Exception:
                self._stop("manual_pwm_arm_failed")
                return False
            self.positions = dict(first)
            self.targets = dict(first)
            self.armed = True
            self.manual_up = True
        self.owner = "red_button"  # real user button overrides pending host motion
        self.last_ping = None
        self.manual_up = not self.manual_up
        self.targets = ({
            "left": s["left"]["min_us"], "right": s["right"]["max_us"]
        } if self.manual_up else {
            "left": s["left"]["max_us"], "right": s["right"]["min_us"]
        })
        self.last_tick = self.clock()
        return True

    def tick(self):
        """GP7 button moves autonomously; HOST ownership needs fresh heartbeats."""
        if not self.armed:
            return
        now = self.clock()
        if not self._physical():
            self._stop("physical_gate_open")
            return
        if self.owner == "host":
            if (self.last_ping is None or now - self.last_ping > WATCHDOG_S
                    or now < self.last_ping):
                self._stop("watchdog")
                return
        if self.last_tick is not None and now < self.last_tick:
            self._stop("clock_error")
            return
        if self.last_tick is not None and now - self.last_tick < TICK_S:
            return
        self.last_tick = now
        try:
            for side in ("left", "right"):
                current = self.positions[side]
                target = self.targets[side]
                if current != target:
                    step = MANUAL_STEP_US if self.owner == "red_button" else STEP_US
                    delta = min(step, abs(target - current))
                    next_us = current + (delta if target > current else -delta)
                    self.io.set_pulse(side, next_us)
                    self.positions[side] = next_us
        except Exception:
            self._stop("pwm_write_failed")

    def handle(self, frame):
        try:
            parts = decode_frame(frame)
        except ValueError:
            return self._ack("invalid", "FRAME_REJECTED")
        op, rid = parts[0], parts[1]
        if op == "STOP":
            self._stop("operator_stop")
            return self._ack(rid, "STOP_RELEASE_UNKNOWN" if self.stop_reason == "output_release_unknown" else "STOP_LATCHED")
        if op == "HELLO":
            return self._ack(rid, "READY" if self._calibration_valid() else "UNCALIBRATED")
        if self.stop_latched:
            return self._ack(rid, "STOP_LATCHED")
        if not self._calibration_valid():
            return self._ack(rid, "UNCALIBRATED")
        if parts[2] != self.boot_id:
            return self._ack(rid, "BOOT_MISMATCH")
        if op == "PING":
            if self.armed and self.owner != "host":
                return self._ack(rid, "BUTTON_OVERRIDE")
            if parts[3] <= self.ping_counter:
                return self._ack(rid, "PING_REPLAY")
            self.ping_counter = parts[3]
            if self.armed:
                self.last_ping = self.clock()
            return self._ack(rid, "ALIVE")
        if op == "ARM":
            if not self._physical():
                return self._ack(rid, "PHYSICAL_ENABLE_REQUIRED")
            if not self.armed:
                initial = {side: self.settings[side]["home_us"] for side in ("left", "right")}
                try:
                    # Physical linkage MUST already be aligned with reviewed home.
                    self.io.arm(initial)
                except Exception:
                    self._stop("pwm_arm_failed")
                    return self._ack(rid, "OUTPUT_FAILED")
                self.positions = dict(initial)
                self.targets = dict(initial)
                self.armed = True
            # If the button moved arms first, ADOPT existing PWM/positions and
            # never snap them back to either home. Host now owns the heartbeat.
            self.owner = "host"
            self.last_ping = self.clock()
            self.last_tick = self.last_ping
            return self._ack(rid, "ARMED_POSITION_UNVERIFIED")
        if op == "MOVE":
            if not self.armed or not self._physical():
                return self._ack(rid, "NOT_ARMED")
            if self.owner != "host":
                return self._ack(rid, "BUTTON_OVERRIDE")
            previous = self.requests.get(rid)
            if previous is not None:
                return self._ack(rid, "DUPLICATE" if previous == frame else "REQUEST_CONFLICT")
            if len(self.requests) >= MAX_COMMANDS:
                self._stop("ledger_full")
                return self._ack(rid, "LEDGER_FULL_STOP")
            side, pulse = parts[3], parts[4]
            limits = self.settings[side]
            if not limits["min_us"] <= pulse <= limits["max_us"]:
                return self._ack(rid, "OUT_OF_BOUNDS")
            self.requests[rid] = frame
            self.targets[side] = pulse
            return self._ack(rid, "ACCEPTED_POSITION_UNVERIFIED")
        return self._ack(rid, "FRAME_REJECTED")
