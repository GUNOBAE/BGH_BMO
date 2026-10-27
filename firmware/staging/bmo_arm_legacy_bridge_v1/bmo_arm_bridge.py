"""BMO1 optional CDC bridge. CircuitPython/CPython; never accesses pins itself.

Only accepts fixed, named arm gestures. The original GP7/HID firmware owns PWM.
No command can change calibration, servo frequency, or remap buttons.
"""
import time

MAX_FRAME = 96
WATCHDOG_S = 0.9
MAX_SLEW_DPS = 90.0
UPDATE_PERIOD_S = 0.02
MAX_UPDATE_DEG = 2.0
GESTURE_TIMEOUT_S = 15.0
VALID_GESTURES = (
    "wave", "celebrate", "shrug", "raise_left_arm",
    "raise_right_arm", "both_up", "both_down",
)


def valid_id(s):
    return (isinstance(s, str) and 1 <= len(s) <= 32 and
            all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in s))


def motion_sequence(rid):
    # Existing B1 envelope; numeric identity is independent of the ACK cache.
    # Leading zeros preserve the old host's fixed-width decimal representation.
    digits = rid[4:] if rid.startswith("bmo_") else ""
    if not 1 <= len(digits) <= 20 or any(c not in "0123456789" for c in digits):
        return None
    value = int(digits)
    return value if value > 0 else None


class LegacyBridge:
    def __init__(self, port, boot_id, set_side, set_both, get_base,
                 *, enabled=False, clock=None):
        self.port, self.boot_id = port, boot_id
        self.set_side, self.set_both, self.get_base = set_side, set_both, get_base
        self.enabled = bool(enabled and port is not None and valid_id(boot_id))
        self.clock = clock or time.monotonic
        self.data = bytearray()
        self.overflow = False
        self.steps = ()
        self.step_index = 0
        self.next_due = 0.
        self.last_ping = None
        self.stopped = False
        self.last_requests = {}
        self.last_request_order = []
        self.fault = None
        self.highest_motion_sequence = 0
        self.positions = None  # Commanded logical angles, never measured position.
        self.last_update = None
        self.gesture_deadline = None
        self.dwelling = False

    def _reply(self, rid, code):
        return ("B1 ACK %s %s %s POSITION_UNVERIFIED\n" %
                (rid, self.boot_id, code)).encode("ascii")

    def _remember(self, rid, frame, ack):
        if len(self.last_request_order) >= 24:
            old = self.last_request_order.pop(0)
            del self.last_requests[old]
        self.last_request_order.append(rid)
        self.last_requests[rid] = (frame, ack)
        return ack

    def _cancel(self):
        self.steps = ()
        self.step_index = 0
        self.gesture_deadline = None
        self.dwelling = False

    def fault_remote(self, code="servo_fault"):
        # Never attempt another PWM operation to handle a PWM failure. In
        # particular, HOLD/STOP are not an electrical power cutoff.
        self.fault = code if code in (
            "servo_fault", "clock_fault", "cdc_fault", "bridge_fault",
            "watchdog_timeout", "gesture_timeout", "position_unknown",
        ) else "bridge_fault"
        self._cancel()
        self.stopped = True
        self.positions = None

    def _now(self):
        try:
            now = self.clock()
            if type(now) not in (int, float) or not 0 <= now < float("inf"):
                raise ValueError("clock_invalid")
            return now
        except Exception:
            self.fault_remote("clock_fault")
            return None

    def manual_cancel(self):
        # GP7 keeps its original function; manual override cancels and latches
        # active automation but does not disable manual button control.
        # Latch even while idle: a DO already queued in CDC must not overtake
        # the manual edge later in this same button-loop iteration.
        self.stopped = True
        self._cancel()

    def manual_pose(self, angle):
        # Called only after original set_arms() returned successfully. This
        # updates software bookkeeping, never performs a servo write.
        if type(angle) is int and angle in (0, 180):
            self.positions = {"left": angle, "right": angle}

    def _make_steps(self, name):
        try:
            base = self.get_base()
        except Exception:
            self.fault_remote("servo_fault")
            return None
        if type(base) is not int or base not in (0, 180):
            self.fault_remote("position_unknown")
            return None
        if self.positions is None:
            self.positions = {"left": base, "right": base}
        if name == "wave":
            return ((None, 180, .36), (None, 145, .36),
                    (None, 180, .36), (None, 145, .36),
                    (None, base, .35))
        if name == "celebrate":
            return ((135, 135, .45), (180, 180, .50), (base, base, .30))
        if name == "shrug":
            return ((160, 160, .42), (base, base, .30))
        if name == "raise_left_arm":
            return ((180, None, .0),)
        if name == "raise_right_arm":
            return ((None, 180, .0),)
        if name == "both_up":
            return ((180, 180, .0),)
        if name == "both_down":
            return ((0, 0, .0),)
        return None

    def handle(self, frame):
        if (type(frame) is not bytes or len(frame) > MAX_FRAME
                or not frame.endswith(b"\n")):
            return None
        try:
            text = frame[:-1].decode("ascii")
        except UnicodeError:
            return None
        if "\r" in text or "  " in text or text != text.strip():
            return None
        parts = text.split(" ")
        if len(parts) < 3 or parts[0] != "B1" or not valid_id(parts[2]):
            return None
        verb, rid = parts[1], parts[2]
        if rid in self.last_requests:
            old_frame, old_reply = self.last_requests[rid]
            return old_reply if old_frame == frame else self._reply(rid, "REQUEST_CONFLICT")
        if verb == "HELLO" and len(parts) == 3:
            return self._remember(rid, frame,
                                  self._reply(rid, "DISABLED" if not self.enabled else
                                              "STOPPED" if self.stopped else "READY"))
        if len(parts) not in (4, 5) or not valid_id(parts[3]):
            return self._remember(rid, frame, self._reply(rid, "INVALID"))
        if parts[3] != self.boot_id:
            return self._remember(rid, frame, self._reply(rid, "STALE_BOOT"))
        if not self.enabled:
            return self._remember(rid, frame, self._reply(rid, "DISABLED"))
        if verb == "STOP" and len(parts) == 4:
            self._cancel()
            self.stopped = True
            return self._remember(rid, frame, self._reply(rid, "STOPPED"))
        if self.stopped:
            return self._remember(rid, frame, self._reply(rid, "STOPPED"))
        if verb == "PING" and len(parts) == 4:
            now = self._now()
            if now is None:
                return self._remember(rid, frame, self._reply(rid, "STOPPED"))
            self.last_ping = now
            return self._remember(rid, frame, self._reply(rid, "ALIVE"))
        if verb == "HOLD" and len(parts) == 4:
            self._cancel()
            return self._remember(rid, frame, self._reply(rid, "HELD"))
        if verb == "DO" and len(parts) == 5:
            sequence = motion_sequence(rid)
            if sequence is None:
                return self._remember(rid, frame, self._reply(rid, "INVALID_REQUEST_ID"))
            if sequence <= self.highest_motion_sequence:
                return self._remember(rid, frame, self._reply(rid, "STALE_REQUEST"))
            # Consume once even when BUSY/INVALID/DENIED. A rejected request
            # cannot turn into a new movement after its cached ACK is evicted.
            self.highest_motion_sequence = sequence
            if parts[4] not in VALID_GESTURES:
                return self._remember(rid, frame, self._reply(rid, "INVALID"))
            if self.steps:
                return self._remember(rid, frame, self._reply(rid, "BUSY"))
            steps = self._make_steps(parts[4])
            if steps is None:
                return self._remember(rid, frame, self._reply(rid, "DENIED"))
            now = self._now()
            if now is None:
                return self._remember(rid, frame, self._reply(rid, "STOPPED"))
            self.steps = steps
            self.step_index = 0
            self.last_update = now
            self.next_due = now + UPDATE_PERIOD_S
            self.gesture_deadline = now + GESTURE_TIMEOUT_S
            self.dwelling = False
            self.last_ping = now
            return self._remember(rid, frame, self._reply(rid, "ACCEPTED"))
        return self._remember(rid, frame, self._reply(rid, "INVALID"))

    def _tick_steps(self):
        if not self.steps:
            return
        now = self._now()
        if now is None:
            return
        if self.last_update is None or now < self.last_update:
            self.fault_remote("clock_fault")
            return
        if self.last_ping is None or not 0 <= now - self.last_ping <= WATCHDOG_S:
            self.fault_remote("watchdog_timeout")
            return
        if self.gesture_deadline is None or now >= self.gesture_deadline:
            self.fault_remote("gesture_timeout")
            return
        if now < self.next_due:
            return
        if self.dwelling:
            self.step_index += 1
            self.dwelling = False
            self.last_update = now
            self.next_due = now + UPDATE_PERIOD_S
            return
        try:
            left, right, dwell = self.steps[self.step_index]
            # No accumulation of motion credit across a slow/stalled loop.
            # At most one bounded update per poll; never run catch-up loops.
            increment = min(MAX_UPDATE_DEG, MAX_SLEW_DPS * min(
                now - self.last_update, UPDATE_PERIOD_S))
            if increment <= 0:
                return

            def toward(current, target):
                if target is None:
                    return current
                difference = target - current
                if abs(difference) <= increment:
                    return target  # Preserve exact integer endpoints/base type.
                return current + (increment if difference > 0 else -increment)

            old_left, old_right = self.positions["left"], self.positions["right"]
            next_left, next_right = toward(old_left, left), toward(old_right, right)
            if left is not None and right is not None and next_left == next_right:
                if next_left != old_left or next_right != old_right:
                    self.set_both(next_left)
                    self.positions["left"], self.positions["right"] = next_left, next_right
            else:
                if next_left != old_left:
                    self.set_side("left", next_left)
                    self.positions["left"] = next_left
                if next_right != old_right:
                    self.set_side("right", next_right)
                    self.positions["right"] = next_right
            self.last_update = now
            self.next_due = now + UPDATE_PERIOD_S
            reached = ((left is None or next_left == left)
                       and (right is None or next_right == right))
            if reached:
                if self.step_index == len(self.steps) - 1:
                    self._cancel()
                else:
                    self.dwelling = True
                    self.next_due = now + dwell
        except Exception:
            self.fault_remote("servo_fault")

    def poll(self):
        if self.port is not None:
            try:
                pending = min(self.port.in_waiting, 64)
                raw = self.port.read(pending) if pending else b""
                if raw:
                    for byte in raw:
                        if byte == 10:
                            if not self.overflow:
                                ack = self.handle(bytes(self.data) + b"\n")
                                if ack is not None and self.port.write(ack) != len(ack):
                                    raise RuntimeError("cdc_partial_write")
                            self.data = bytearray()
                            self.overflow = False
                        elif byte == 13 or len(self.data) >= MAX_FRAME - 1:
                            self.data = bytearray()
                            self.overflow = True
                        elif not self.overflow:
                            self.data.append(byte)
            except Exception:
                self.fault_remote("cdc_fault")
                self.port = None
        try:
            self._tick_steps()
        except Exception:
            # Last containment boundary: remote faults must not exit code.py's
            # original GP7/GP8/other-HID scanning loop.
            self.fault_remote("bridge_fault")
