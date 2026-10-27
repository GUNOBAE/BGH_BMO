"""BMO1 optional CDC bridge. CircuitPython/CPython; never accesses pins itself.

Only accepts fixed, named arm gestures. The original GP7/HID firmware owns PWM.
No command can change calibration, servo frequency, or remap buttons.
"""
import time

MAX_FRAME = 96
WATCHDOG_S = 0.9
VALID_GESTURES = (
    "wave", "celebrate", "shrug", "raise_left_arm",
    "raise_right_arm", "both_up", "both_down",
)


def valid_id(s):
    return (isinstance(s, str) and 1 <= len(s) <= 32 and
            all(c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in s))


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

    def manual_cancel(self):
        # GP7 keeps its original function; manual override cancels and latches
        # active automation but does not disable manual button control.
        if self.steps:
            self.stopped = True
        self._cancel()

    def _make_steps(self, name):
        base = self.get_base()
        if type(base) is not int or base not in (0, 180):
            return None
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
                                  self._reply(rid, "READY" if self.enabled else "DISABLED"))
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
            self.last_ping = self.clock()
            return self._remember(rid, frame, self._reply(rid, "ALIVE"))
        if verb == "HOLD" and len(parts) == 4:
            self._cancel()
            return self._remember(rid, frame, self._reply(rid, "HELD"))
        if verb == "DO" and len(parts) == 5 and parts[4] in VALID_GESTURES:
            if self.steps:
                return self._remember(rid, frame, self._reply(rid, "BUSY"))
            steps = self._make_steps(parts[4])
            if steps is None:
                return self._remember(rid, frame, self._reply(rid, "DENIED"))
            self.steps = steps
            self.step_index = 0
            self.next_due = self.clock()
            self.last_ping = self.clock()
            return self._remember(rid, frame, self._reply(rid, "ACCEPTED"))
        return self._remember(rid, frame, self._reply(rid, "INVALID"))

    def _tick_steps(self):
        if not self.steps:
            return
        now = self.clock()
        if self.last_ping is None or now - self.last_ping > WATCHDOG_S:
            self._cancel()
            self.stopped = True
            return
        if now < self.next_due:
            return
        left, right, dwell = self.steps[self.step_index]
        if left is not None and right is not None and left == right:
            self.set_both(left)
        else:
            if left is not None:
                self.set_side("left", left)
            if right is not None:
                self.set_side("right", right)
        self.step_index += 1
        if self.step_index >= len(self.steps):
            self._cancel()
        else:
            self.next_due = now + dwell

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
                            self.data.clear()
                            self.overflow = False
                        elif byte == 13 or len(self.data) >= MAX_FRAME - 1:
                            self.data.clear()
                            self.overflow = True
                        elif not self.overflow:
                            self.data.append(byte)
            except Exception:
                self.fault = "cdc_fault"
                self._cancel()
                self.stopped = True
                self.port = None
        self._tick_steps()
