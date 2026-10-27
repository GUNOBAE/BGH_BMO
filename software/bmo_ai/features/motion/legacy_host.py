"""BMO1 named-gesture host; no USB access on import or construction."""
import hashlib
from pathlib import Path
import re
import time

from bmo_ai.features.contracts import FeatureError, FeatureEvent, HostResult, request_id_ok
from .arm_serial_posix_v2 import MOUNT

FRAME_MAX = 96
REPLY_MAX = 128
IDENT = re.compile(r"[A-Za-z0-9_]{1,32}\Z", re.ASCII)
NAMED_GESTURES = frozenset(("wave", "celebrate", "shrug", "raise_left_arm",
                            "raise_right_arm", "both_up", "both_down"))
MODEL_ONLY = frozenset(("wave", "celebrate", "shrug"))


class LegacyArmError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_legacy_install(firmware_root, reviewed_sha, mount=MOUNT):
    """Read-only exact-byte verification. Never changes RP2040 or calibration."""
    if not isinstance(reviewed_sha, str) or re.fullmatch(r"[a-f0-9]{64}", reviewed_sha) is None:
        raise LegacyArmError("reviewed_sha_required")
    root = Path(firmware_root)
    original = (root / "original_code.py").read_bytes()
    if hashlib.sha256(original).hexdigest() != "fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e":
        raise LegacyArmError("original_source_mismatch")
    template = (root / "code.py").read_bytes()
    if template.count(b"REMOTE_ENABLED = False") != 1:
        raise LegacyArmError("invalid_reviewed_template")
    expected = template.replace(b"REMOTE_ENABLED = False", b"REMOTE_ENABLED = True", 1)
    if (hashlib.sha256(expected).hexdigest() != reviewed_sha
            or expected != (Path(mount) / "code.py").read_bytes()
            or (root / "bmo_arm_bridge.py").read_bytes() != (Path(mount) / "bmo_arm_bridge.py").read_bytes()):
        raise LegacyArmError("bridge_sha_or_dependency_mismatch")
    boot = (Path(mount) / "boot.py").read_text(encoding="utf-8")
    if "usb_cdc.enable(console=True, data=True)" not in boot:
        raise LegacyArmError("dedicated_data_disabled")
    return reviewed_sha


class LegacyArmLink:
    """One outstanding B1 request. Require exact ACK and fresh boot token."""
    def __init__(self, port, *, motor_permission=None):
        if not callable(getattr(port, "write", None)) or not callable(getattr(port, "read_until", None)):
            raise LegacyArmError("owned_cdc_port_required")
        self.port, self.boot = port, None
        self.motor_permission = motor_permission or (lambda: False)
        self.counter = 0
        self.unknown = False
        self.stopped = False

    def _request(self, command, *, is_hello=False):
        if self.unknown or self.stopped:
            raise LegacyArmError("bridge_unavailable")
        self.counter += 1
        if self.counter > 100000000:
            self.unknown = True
            raise LegacyArmError("request_counter_exhausted")
        rid = "bmo_%08d" % self.counter
        data = ("B1 " + command.format(rid=rid, boot=self.boot) + "\n").encode("ascii")
        if len(data) > FRAME_MAX:
            raise LegacyArmError("frame_too_long")
        try:
            if self.port.write(data) != len(data):
                raise LegacyArmError("partial_write")
            raw = self.port.read_until(b"\n", REPLY_MAX)
            if (type(raw) is not bytes or not 1 <= len(raw) <= REPLY_MAX or
                    not raw.endswith(b"\n") or b"\r" in raw or b"\n" in raw[:-1]):
                raise LegacyArmError("ack_missing_or_incomplete")
            parts = raw[:-1].decode("ascii").split(" ")
            if (len(parts) != 6 or parts[0:2] != ["B1", "ACK"] or parts[2] != rid
                    or IDENT.fullmatch(parts[3]) is None
                    or parts[5] != "POSITION_UNVERIFIED"
                    or re.fullmatch(r"[A-Z_]{2,32}", parts[4]) is None):
                raise LegacyArmError("invalid_ack")
            if self.boot is not None and parts[3] != self.boot:
                raise LegacyArmError("boot_changed")
            if is_hello:
                self.boot = parts[3]
            return parts[4]
        except Exception as exc:
            self.unknown = True
            if isinstance(exc, LegacyArmError):
                raise
            raise LegacyArmError("link_unknown") from None

    def hello(self):
        if self.boot is not None:
            raise LegacyArmError("already_connected")
        code = self._request("HELLO {rid}", is_hello=True)
        if code != "READY":
            self.unknown = True
            raise LegacyArmError("firmware_not_ready_" + code.lower())
        return code

    def gesture(self, name):
        if self.boot is None or name not in NAMED_GESTURES:
            raise LegacyArmError("invalid_gesture_or_not_connected")
        if self.motor_permission() is not True:
            raise LegacyArmError("physical_supervision_required")
        code = self._request("DO {rid} {boot} " + name)
        if code != "ACCEPTED":
            raise LegacyArmError("gesture_" + code.lower())
        return code

    def ping(self):
        if self.boot is None or self._request("PING {rid} {boot}") != "ALIVE":
            raise LegacyArmError("heartbeat_failed")

    def hold(self):
        if self.boot is not None and self._request("HOLD {rid} {boot}") != "HELD":
            raise LegacyArmError("hold_failed")

    def stop(self):
        if self.boot is not None and not self.stopped and not self.unknown:
            if self._request("STOP {rid} {boot}") != "STOPPED":
                self.unknown = True
                raise LegacyArmError("stop_unverified")
        self.stopped = True

    def close(self):
        close = getattr(self.port, "close", None)
        if callable(close):
            close()


class LegacyMotionFeature:
    """Plug into FeatureHost without altering model/GPT or approved R10."""
    def __init__(self, link, *, clock=None, ttl=1.0, cooldown=2.0):
        if not isinstance(link, LegacyArmLink):
            raise FeatureError("legacy_arm_link_required")
        self.link = link
        self.clock = clock or time.monotonic
        self.ttl, self.cooldown = ttl, cooldown
        self.context = None
        self.generation = 0
        self.pending = None
        self.active_until = None
        self.last_gesture = float("-inf")
        self.next_ping = 0.
        self.seen = set()
        self.events = []
        self.stopped = False
        self.closed = False
        self.fault = None

    def set_context(self, session_id, turn_id, mode_generation):
        if type(turn_id) is int:
            turn_id = str(turn_id)
        if not request_id_ok(session_id) or not request_id_ok(turn_id) or type(mode_generation) is not int:
            raise FeatureError("invalid_motion_context")
        new_context = (session_id, turn_id, mode_generation)
        if new_context != self.context:
            self.cancel()
            self.context = new_context
            self.generation = mode_generation

    def submit(self, gesture_id, *, request_id, session_id, turn_id,
               mode_generation, started_at, explicit=False):
        if not request_id_ok(request_id):
            raise FeatureError("invalid_request_id")
        if (self.closed or self.stopped or self.fault or self.pending is not None
                or self.active_until is not None or request_id in self.seen):
            return HostResult(request_id, "rejected", "arm_unavailable", simulated=False)
        if self.context != (session_id, str(turn_id), mode_generation):
            return HostResult(request_id, "rejected", "stale_context", simulated=False)
        if gesture_id not in (NAMED_GESTURES if explicit else MODEL_ONLY):
            return HostResult(request_id, "rejected", "invalid_gesture", simulated=False)
        now = self.clock()
        if not explicit and (type(started_at) not in (float, int) or
                             not 0 <= now - started_at <= self.ttl):
            return HostResult(request_id, "rejected", "stale_playback_start", simulated=False)
        if now - self.last_gesture < self.cooldown:
            return HostResult(request_id, "rejected", "gesture_cooldown", simulated=False)
        self.seen.add(request_id)
        self.pending = (request_id, gesture_id)
        return HostResult(request_id, "accepted", "gesture_queued", simulated=False)

    def poll(self):
        if self.closed or self.stopped or self.fault:
            result, self.events = tuple(self.events), []
            return result
        now = self.clock()
        try:
            if self.pending is not None:
                if self.link.boot is None:
                    self.link.hello()
                request_id, gesture = self.pending
                self.link.gesture(gesture)
                self.pending = None
                self.last_gesture = now
                self.active_until = now + (2.5 if gesture == "wave" else 1.9)
                self.next_ping = now
                self.events.append(FeatureEvent("motion_result", self.generation, request_id,
                    "ack_position_unverified", {"status": "running", "simulated": False,
                    "executed": True, "completed": False,
                    "completion": "position_unverified", "position_verified": False}))
            if self.active_until is not None:
                if now >= self.active_until:
                    self.active_until = None
                elif now >= self.next_ping:
                    self.link.ping()
                    self.next_ping = self.clock() + .14
        except LegacyArmError:
            self.fault = "arm_link_unknown"
            if self.pending is not None:
                self.events.append(FeatureEvent("motion_result", self.generation,
                    self.pending[0], "arm_link_unknown", {"status": "unknown",
                    "simulated": False, "executed": False, "completed": False,
                    "completion": "position_unverified", "position_verified": False}))
            self.pending = None
            self.active_until = None
        result, self.events = tuple(self.events), []
        return result

    def cancel(self, request_id=None):
        if self.pending is not None and request_id not in (None, self.pending[0]):
            return
        self.pending = None
        if self.active_until is not None and not self.link.unknown:
            try:
                self.link.hold()
            except LegacyArmError:
                self.fault = "arm_link_unknown"
        self.active_until = None

    def stop(self):
        self.cancel()
        self.stopped = True
        try:
            self.link.stop()
        except LegacyArmError:
            self.fault = "stop_unverified"

    def close(self):
        if not self.closed:
            self.stop()
            self.link.close()
            self.closed = True

    def snapshot(self):
        return {"simulated": False, "real_enabled": True,
                "readiness": "UNVERIFIED" if self.fault else "READY",
                "reason_code": self.fault or "position_unverified",
                "queued": int(self.pending is not None),
                "active": self.pending is not None or self.active_until is not None,
                "stopped": self.stopped, "closed": self.closed,
                "position_verified": False,
                "controller": {"protocol": "bmo_legacy_b1",
                               "negotiated": self.link.boot is not None,
                               "link_unknown": self.link.unknown}}
