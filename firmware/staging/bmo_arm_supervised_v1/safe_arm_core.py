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
            if (any(type(n) is not int for n in (a, b, c))
                    or not 500 <= a < b < c <= 2500):
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

    def _stop(self, reason):
        self.armed = False
        self.stop_latched = True
        self.stop_reason = reason
        self.targets = {}
        self.last_ping = None
        try:
            self.io.stop()
        except Exception:
            self.stop_reason = "output_release_unknown"

    def tick(self):
        """Drive no outputs unless recent host heartbeat AND physical gate."""
        if not self.armed:
            return
        now = self.clock()
        if not self._physical():
            self._stop("physical_gate_open")
            return
        if self.last_ping is None or now - self.last_ping > WATCHDOG_S or now < self.last_ping:
            self._stop("watchdog")
            return
        if self.last_tick is not None and now - self.last_tick < TICK_S:
            return
        if self.last_tick is not None and now < self.last_tick:
            self._stop("clock_error")
            return
        self.last_tick = now
        try:
            for side in ("left", "right"):
                current = self.positions[side]
                target = self.targets[side]
                if current != target:
                    delta = min(STEP_US, abs(target - current))
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
            return self._ack(rid, "STOP_LATCHED")
        if op == "HELLO":
            return self._ack(rid, "READY" if self._calibration_valid() else "UNCALIBRATED")
        if self.stop_latched:
            return self._ack(rid, "STOP_LATCHED")
        if not self._calibration_valid():
            return self._ack(rid, "UNCALIBRATED")
        if parts[2] != self.boot_id:
            return self._ack(rid, "BOOT_MISMATCH")
        if op == "PING":
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
                    # This first PWM pulse can cause physical motion.
                    self.io.arm(initial)
                except Exception:
                    self._stop("pwm_arm_failed")
                    return self._ack(rid, "OUTPUT_FAILED")
                self.positions = dict(initial)
                self.targets = dict(initial)
                self.armed = True
                self.last_ping = self.clock()
                self.last_tick = self.last_ping
            return self._ack(rid, "ARMED_POSITION_UNVERIFIED")
        if op == "MOVE":
            if not self.armed or not self._physical():
                return self._ack(rid, "NOT_ARMED")
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
