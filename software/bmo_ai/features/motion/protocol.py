"""Memory-only proposed controller protocol. No existing firmware is assumed."""
from collections import deque
from dataclasses import dataclass
import math
import time

from ..contracts import FeatureError, GESTURES, request_id_ok


@dataclass(frozen=True, slots=True)
class Handshake:
    version: int = 1
    channel: str = "dedicated_data"
    controller_boot: str = "fixture_boot_1"
    controller_dedup: bool = True
    position_feedback: bool = False


@dataclass(frozen=True, slots=True)
class MotionCommand:
    request_id: str
    gesture_id: str
    session_id: str
    turn_id: str
    mode_generation: int
    controller_boot: str

    def __post_init__(self):
        if (any(not request_id_ok(value) for value in
                (self.request_id, self.session_id, self.turn_id, self.controller_boot))
                or type(self.gesture_id) is not str or self.gesture_id not in GESTURES
                or self.gesture_id == "none" or type(self.mode_generation) is not int
                or self.mode_generation < 0):
            raise FeatureError("invalid_motion_command")


@dataclass(frozen=True, slots=True)
class MotionAck:
    request_id: str
    controller_boot: str
    accepted: bool = True


class FakeMotionTransport:
    """Bounded fake ACK/dedup state; never opens or resets a port.

    Reconnect with a new boot clears the *fake controller's* ledger. The host must
    retain its own unknown results and must not resend commands after a reboot.
    """
    simulated = True

    def __init__(self, *, clock=time.monotonic, ack_delay=0.0, auto_ack=True,
                 ledger_limit=256, version=1, channel="dedicated_data", dedup=True, wire_crc=False):
        if (type(ack_delay) not in (int, float) or not math.isfinite(ack_delay) or ack_delay < 0
                or type(auto_ack) is not bool or type(wire_crc) is not bool or type(ledger_limit) is not int
                or not 1 <= ledger_limit <= 4096):
            raise FeatureError("invalid_motion_transport")
        self.clock, self.ack_delay, self.auto_ack = clock, ack_delay, auto_ack
        self.ledger_limit = ledger_limit
        self.wire_crc = wire_crc
        self._version, self._channel, self._dedup = version, channel, dedup
        self._boot = "fixture_boot_1"
        self.connected = True
        self.closed = False
        self._ledger = {}
        self._acks = deque(maxlen=ledger_limit)
        self.dispatch_count = self.stop_count = 0

    def handshake(self):
        if self.closed or not self.connected:
            raise FeatureError("controller_unavailable")
        return Handshake(self._version, self._channel, self._boot, self._dedup, False)

    def send(self, command):
        # Exercise the proposed wire contract in memory, without a device port.
        from .wire import encode_frame, decode_frame
        command = decode_frame(encode_frame(command, crc=self.wire_crc), require_crc=self.wire_crc)
        hello = self.handshake()
        if not isinstance(command, MotionCommand) or command.controller_boot != hello.controller_boot:
            raise FeatureError("controller_boot_mismatch")
        previous = self._ledger.get(command.request_id)
        if previous is not None:
            if previous != command:
                raise FeatureError("request_conflict")
            # Replayed delivery is acknowledged without dispatching it twice.
        else:
            if len(self._ledger) >= self.ledger_limit:
                raise FeatureError("controller_ledger_full")
            self._ledger[command.request_id] = command
            self.dispatch_count += 1
        if self.auto_ack:
            ack = decode_frame(encode_frame(MotionAck(command.request_id, self._boot),
                                           crc=self.wire_crc), require_crc=self.wire_crc)
            self._acks.append((self.clock() + self.ack_delay,
                               ack))

    def inject_ack(self, request_id, *, controller_boot=None, accepted=True):
        self._acks.append((self.clock(), MotionAck(request_id, controller_boot or self._boot, accepted)))

    def poll(self):
        self.handshake()
        ready = []
        while self._acks and self._acks[0][0] <= self.clock():
            ready.append(self._acks.popleft()[1])
        return tuple(ready)

    def stop(self):
        self.stop_count += 1
        self._acks.clear()

    def disconnect(self):
        self.connected = False

    def reconnect(self, *, controller_boot=None):
        if self.closed:
            raise FeatureError("controller_unavailable")
        if controller_boot is not None:
            if not request_id_ok(controller_boot):
                raise FeatureError("invalid_controller_boot")
            if controller_boot != self._boot:
                self._ledger.clear()
                self._acks.clear()
                self._boot = controller_boot
        self.connected = True

    def close(self):
        self.closed = True
        self.connected = False
        self._acks.clear()
