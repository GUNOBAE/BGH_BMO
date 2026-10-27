"""BMO dedicated RP2040 CDC *V2* host protocol: real framing, no implicit I/O.

The old live firmware understands only HID, NOT this protocol. Merely importing
or constructing these classes must not enumerate USB, open ttyACM, change DTR,
reset the board, allocate PWM or move an arm. A supervised future deployment
may inject one *already opened and verified* CDC data interface.
"""
from dataclasses import dataclass
import math
import re
import time

MAX_FRAME_BYTES = 128
MAX_ACK_BYTES = 128
ACK_TIMEOUT_S = .50
MAX_HOST_HEARTBEAT_AGE_S = .65
IDENT = re.compile(r"[A-Za-z0-9_-]{1,32}\Z", re.ASCII)
CODE = re.compile(r"[A-Z][A-Z0-9_]{0,47}\Z", re.ASCII)


class ArmV2Error(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("BMO arm V2 rejected: " + code)


def _id(value):
    if type(value) is not str or IDENT.fullmatch(value) is None:
        raise ArmV2Error("invalid_arm_identifier")
    return value


def _boot(value):
    return _id(value)


def _packet(*parts):
    try:
        value = ("V2 " + " ".join(parts) + "\n").encode("ascii")
    except (UnicodeError, TypeError, ValueError):
        raise ArmV2Error("invalid_arm_frame") from None
    if len(value) > MAX_FRAME_BYTES:
        raise ArmV2Error("invalid_arm_frame")
    return value


def encode_hello(request_id):
    return _packet("HELLO", _id(request_id))


def encode_stop(request_id):
    # STOP does not depend on calibration or prior HELLO. It is NOT a
    # physical power-cutoff and its ACK cannot guarantee a motor is powerless.
    return _packet("STOP", _id(request_id))


def encode_arm(request_id, boot_id):
    return _packet("ARM", _id(request_id), _boot(boot_id))


def encode_ping(request_id, boot_id, counter):
    if type(counter) is not int or not 0 <= counter <= 4294967295:
        raise ArmV2Error("invalid_arm_heartbeat")
    return _packet("PING", _id(request_id), _boot(boot_id), str(counter))


def encode_hold(request_id, boot_id):
    return _packet("HOLD", _id(request_id), _boot(boot_id))


def encode_move(request_id, boot_id, side, pulse_us):
    if side not in ("left", "right") or type(side) is not str:
        raise ArmV2Error("invalid_arm_side")
    if type(pulse_us) is not int or not 500 <= pulse_us <= 2500:
        raise ArmV2Error("invalid_arm_pulse")
    return _packet("MOVE", _id(request_id), _boot(boot_id), side, str(pulse_us))


@dataclass(frozen=True, slots=True)
class ArmV2Ack:
    request_id: str
    boot_id: str
    code: str
    position_verified: bool = False


def decode_ack(raw):
    if type(raw) is not bytes or not raw:
        raise ArmV2Error("arm_ack_missing")
    if len(raw) > MAX_ACK_BYTES:
        raise ArmV2Error("arm_ack_oversized")
    if not raw.endswith(b"\n"):
        raise ArmV2Error("arm_ack_incomplete")
    if b"\r" in raw or b"\n" in raw[:-1]:
        raise ArmV2Error("arm_ack_ambiguous")
    try:
        parts = raw[:-1].decode("ascii").split(" ")
    except UnicodeError:
        raise ArmV2Error("invalid_arm_ack") from None
    if (len(parts) != 6 or parts[0:2] != ["V2", "ACK"]
            or any(not value or len(value) > 48 for value in parts)
            or parts[5] != "POSITION_UNVERIFIED"
            or IDENT.fullmatch(parts[2]) is None
            or IDENT.fullmatch(parts[3]) is None
            or CODE.fullmatch(parts[4]) is None):
        raise ArmV2Error("invalid_arm_ack")
    return ArmV2Ack(parts[2], parts[3], parts[4], False)


def reviewed_limits(profile):
    """Never infer calibration from the existing 600..2400us/0..180 PWM.

    Profile is entered only after physical limits and homing are measured. This
    parses metadata in memory and cannot enable a real arm by itself.
    """
    if type(profile) is not dict or set(profile) != {"reviewed", "left", "right"} or profile["reviewed"] is not True:
        raise ArmV2Error("arm_profile_unreviewed")
    for side in ("left", "right"):
        limits = profile[side]
        if type(limits) is not dict or set(limits) != {"min_us", "home_us", "max_us"}:
            raise ArmV2Error("arm_profile_unreviewed")
        a, b, c = limits["min_us"], limits["home_us"], limits["max_us"]
        if (any(type(value) is not int for value in (a, b, c))
                or not 500 <= a <= b <= c <= 2500 or a == c):
            raise ArmV2Error("arm_profile_unreviewed")
    return profile


class BoundedCdcExchange:
    """A REAL byte transport around an *injected, preopened* dedicated port.

    No serial library is imported or instantiated. The human-owned opener must
    verify the RP2040's dedicated CDC data interface, disable automatic
    resets/control-line toggling where possible, and supply strictly bounded
    read/write timeouts; /dev/ttyACM0 alone proves nothing.
    """

    simulated = False

    def __init__(self, port):
        if (port is None or not callable(getattr(port, "write", None))
                or not callable(getattr(port, "read_until", None))):
            raise ArmV2Error("dedicated_data_port_required")
        self._port = port

    def __call__(self, request):
        if type(request) is not bytes or not request.endswith(b"\n") or len(request) > MAX_FRAME_BYTES:
            raise ArmV2Error("invalid_arm_frame")
        port = self._port
        if (getattr(port, "is_open", False) is not True
                or type(getattr(port, "timeout", None)) not in (int, float)
                or not 0 < port.timeout <= ACK_TIMEOUT_S
                or type(getattr(port, "write_timeout", None)) not in (int, float)
                or not 0 < port.write_timeout <= ACK_TIMEOUT_S):
            raise ArmV2Error("dedicated_data_timeout_unverified")
        try:
            written = port.write(request)
            if type(written) is not int or written != len(request):
                raise ArmV2Error("arm_partial_write_unknown")
            reply = port.read_until(b"\n", MAX_ACK_BYTES)
        except ArmV2Error:
            raise
        except Exception:
            raise ArmV2Error("arm_transport_unknown") from None
        return reply  # Upper-layer decoder rejects empty/truncated/replayed data.


class ArmHostV2:
    """One outstanding command, exact ACK matching, no resend on unknown.

    Synthetic tests may use a simulated exchange. Physical ARM/MOVE additionally
    require three independently confirmed preconditions: reviewed per-arm
    calibration, real physical motor-power cutoff, and current human oversight.
    The current BMO cannot isolate motor power, so hardware ARM remains DENIED.
    """

    def __init__(self, exchange, *, profile=None, clock=None,
                 power_cutoff_confirmed=None, operator_present=None):
        if not callable(exchange):
            raise ArmV2Error("arm_exchange_required")
        self.exchange = exchange
        self.profile = profile
        self.clock = clock or time.monotonic
        self.power_cutoff_confirmed = power_cutoff_confirmed or (lambda: False)
        self.operator_present = operator_present or (lambda: False)
        self.simulated = getattr(exchange, "simulated", False) is True
        self.boot_id = None
        self.calibrated = False
        self.armed = False
        self.latch = False
        self.unknown = False
        self.last_ping = None
        self.ping_counter = 0
        self.last_ack = None
        # Nothing calls the exchange in construction.

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ArmV2Error("invalid_arm_clock")
        return value

    def _transact(self, frame, request_id):
        if self.unknown:
            raise ArmV2Error("arm_transport_unknown")
        try:
            raw = self.exchange(frame)
            ack = decode_ack(raw)
            if ack.request_id != request_id:
                raise ArmV2Error("arm_request_ack_mismatch")
            if self.boot_id is not None and ack.boot_id != self.boot_id:
                raise ArmV2Error("controller_boot_changed")
        except Exception as error:
            self.unknown = True
            self.armed = False
            # Never send an automatic retry: it may repeat physical movement.
            if isinstance(error, ArmV2Error):
                raise
            raise ArmV2Error("arm_transport_unknown") from None
        self.last_ack = ack
        return ack

    def hello(self, request_id="hello1"):
        if self.latch or self.boot_id is not None:
            raise ArmV2Error("arm_session_already_started")
        rid = _id(request_id)
        ack = self._transact(encode_hello(rid), rid)
        if ack.code not in ("READY", "UNCALIBRATED"):
            self.unknown = True
            raise ArmV2Error("arm_handshake_denied")
        self.boot_id = ack.boot_id
        self.calibrated = ack.code == "READY"
        return ack

    def _motion_guard(self):
        if self.unknown:
            raise ArmV2Error("arm_transport_unknown")
        if self.latch:
            raise ArmV2Error("arm_session_latched")
        if self.boot_id is None or not self.calibrated:
            raise ArmV2Error("arm_controller_unverified")
        reviewed_limits(self.profile)
        if not self.simulated:
            # Supplying a calibrated profile alone MUST NOT unlock real PWM.
            if not (self.power_cutoff_confirmed() is True
                    and self.operator_present() is True):
                raise ArmV2Error("arm_physical_cutoff_required")

    def arm(self, request_id):
        self._motion_guard()
        if self.armed:
            raise ArmV2Error("arm_already_armed")
        rid = _id(request_id)
        ack = self._transact(encode_arm(rid, self.boot_id), rid)
        if ack.code != "ARMED_POSITION_UNVERIFIED":
            raise ArmV2Error("arm_not_armed")
        self.armed = True
        self.last_ping = self._now()
        return ack

    def ping(self, request_id):
        if not self.armed or self.latch:
            raise ArmV2Error("arm_not_armed")
        self._motion_guard()
        self._heartbeat_fresh()
        self.ping_counter += 1
        if self.ping_counter > 4294967295:
            self.latch = True
            raise ArmV2Error("arm_heartbeat_exhausted")
        rid = _id(request_id)
        ack = self._transact(encode_ping(rid, self.boot_id, self.ping_counter), rid)
        if ack.code != "ALIVE":
            self.latch = True
            self.armed = False
            raise ArmV2Error("arm_heartbeat_rejected")
        self.last_ping = self._now()
        return ack

    def _heartbeat_fresh(self):
        if self.last_ping is None or self._now() - self.last_ping > MAX_HOST_HEARTBEAT_AGE_S:
            self.latch = True
            self.armed = False
            raise ArmV2Error("arm_host_heartbeat_expired")

    def move(self, request_id, side, pulse_us):
        if not self.armed:
            raise ArmV2Error("arm_not_armed")
        self._motion_guard()
        self._heartbeat_fresh()
        profile = self.profile.get(side) if type(self.profile) is dict else None
        if type(profile) is not dict or side not in ("left", "right"):
            raise ArmV2Error("invalid_arm_side")
        if type(pulse_us) is not int or not profile["min_us"] <= pulse_us <= profile["max_us"]:
            raise ArmV2Error("arm_out_of_bounds")
        rid = _id(request_id)
        ack = self._transact(encode_move(rid, self.boot_id, side, pulse_us), rid)
        if ack.code != "ACCEPTED_POSITION_UNVERIFIED":
            raise ArmV2Error("arm_move_rejected")
        return ack

    def hold(self, request_id):
        """Cancel current gesture trajectory without latching the STOP."""
        if not self.armed:
            raise ArmV2Error("arm_not_armed")
        self._motion_guard()
        self._heartbeat_fresh()
        rid = _id(request_id)
        ack = self._transact(encode_hold(rid, self.boot_id), rid)
        if ack.code != "HELD_POSITION_UNVERIFIED":
            raise ArmV2Error("arm_hold_rejected")
        self.last_ping = self._now()
        return ack

    def stop(self, request_id):
        rid = _id(request_id)
        self.latch = True
        self.armed = False
        # Even if the link is UNKNOWN, do not send a second uncertain command.
        if self.unknown:
            raise ArmV2Error("arm_transport_unknown")
        ack = self._transact(encode_stop(rid), rid)
        if ack.code != "STOP_LATCHED":
            raise ArmV2Error("arm_stop_release_unverified")
        return ack

    def snapshot(self):
        return dict(protocol="bmo_arm_v2", negotiated=self.boot_id is not None,
                    controller_calibrated=self.calibrated, armed=self.armed,
                    latched=self.latch, link_unknown=self.unknown,
                    position_verified=False, simulated=self.simulated)
