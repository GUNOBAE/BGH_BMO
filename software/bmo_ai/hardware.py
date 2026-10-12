"""Side-effect-free hardware contracts and an untrusted telemetry decoder.

Importing this module never opens a device. There is deliberately no serial
transport or working arm backend: the current controller can move arms when its
firmware restarts. Cloud replies are text, never hardware commands.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
import math
import time
from typing import Callable, Protocol


class FaceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


class FacePort(Protocol):
    def set_state(self, state: FaceState) -> None: ...

    def close(self) -> None: ...


class NullFace:
    """In-memory face state sink; no display, GPIO, or animation claim."""

    def __init__(self) -> None:
        self.state = FaceState.IDLE

    def set_state(self, state: FaceState) -> None:
        if not isinstance(state, FaceState):
            raise TypeError("state must be a FaceState")
        self.state = state

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class CameraFrame:
    """Encoded image from an explicitly installed adapter, never sent by default."""

    data: bytes
    media_type: str
    captured_at: float  # Host monotonic clock, not controller uptime.


class CameraPort(Protocol):
    """Future camera adapters must bound capture and release resources on close."""

    def capture(self, *, timeout_seconds: float) -> CameraFrame: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class IMUSample:
    """Parsed controller values; validity of the physical measurement is unknown."""

    sequence: int
    timestamp_ms: int
    acceleration_g: tuple[float, float, float]
    gyroscope_dps: tuple[float, float, float]
    temperature_c: float


@dataclass(frozen=True)
class ButtonEvent:
    sequence: int
    timestamp_ms: int
    button_id: int  # Mapping to physical buttons must be verified separately.
    pressed: bool


@dataclass(frozen=True)
class Heartbeat:
    sequence: int
    timestamp_ms: int


TelemetryEvent = IMUSample | ButtonEvent | Heartbeat


class IMUPort(Protocol):
    def latest_imu(self) -> IMUSample | None: ...


class ButtonPort(Protocol):
    def drain_buttons(self) -> tuple[ButtonEvent, ...]: ...


class ArmPort(Protocol):
    def move_to(
        self, left_degrees: float, right_degrees: float, *, duration_seconds: float
    ) -> None: ...

    def close(self) -> None: ...


class ArmMovementDisabledError(RuntimeError):
    pass


class DeniedArmController:
    """Fails closed for every request; no flag or environment variable unlocks it."""

    def move_to(
        self, left_degrees: float, right_degrees: float, *, duration_seconds: float
    ) -> None:
        raise ArmMovementDisabledError(
            "Arm movement is disabled; a separately reviewed, supervised backend is required"
        )

    def close(self) -> None:
        pass


class TelemetryError(ValueError):
    """Malformed or unsupported telemetry. Error text never contains frame data."""


MAX_FRAME_BYTES = 512
MAX_CHUNK_BYTES = 4096
_UINT32_MAX = (1 << 32) - 1


def _reject_constant(value: str) -> None:
    raise TelemetryError("Non-finite JSON numbers are forbidden")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TelemetryError("Duplicate JSON keys are forbidden")
        result[key] = value
    return result


def _integer(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise TelemetryError("Integer field is outside the supported range")
    return value


def _number(value: object, lower: float, upper: float) -> float:
    if type(value) not in (int, float):
        raise TelemetryError("Numeric field must contain a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise TelemetryError("Numeric field is outside the supported range") from error
    if not math.isfinite(number) or not lower <= number <= upper:
        raise TelemetryError("Numeric field is outside the supported range")
    return number


def _vector(value: object, limit: float) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise TelemetryError("Vector field must contain three numbers")
    return tuple(_number(component, -limit, limit) for component in value)  # type: ignore[return-value]


def parse_telemetry(frame: bytes) -> TelemetryEvent:
    """Decode one proposed v1 JSON line; this does not implement existing firmware.

    Exact fields prevent unknown control-shaped payloads being accepted. Physical
    range bounds prevent infinities and absurd values, but do not certify sensor
    calibration or health. Nothing returned by this function is an action.
    """
    if not isinstance(frame, bytes):
        raise TypeError("frame must be bytes")
    if not frame or len(frame) > MAX_FRAME_BYTES:
        raise TelemetryError("Telemetry frame is empty or too large")
    try:
        payload = json.loads(
            frame.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise TelemetryError("Invalid UTF-8 or JSON telemetry") from error
    if not isinstance(payload, dict):
        raise TelemetryError("Telemetry must be a JSON object")
    if type(payload.get("v")) is not int or payload["v"] != 1:
        raise TelemetryError("Unsupported telemetry version")
    kind = payload.get("type")
    if not isinstance(kind, str):
        raise TelemetryError("Telemetry type must be a string")
    base = {"v", "type", "seq", "timestamp_ms"}
    fields = {
        "imu": {"accel_g", "gyro_dps", "temperature_c"},
        "button": {"button_id", "pressed"},
        "heartbeat": set(),
    }
    if kind not in fields or set(payload) != base | fields[kind]:
        raise TelemetryError("Unknown telemetry type or fields")
    sequence = _integer(payload["seq"], 0, _UINT32_MAX)
    timestamp = _integer(payload["timestamp_ms"], 0, _UINT32_MAX)
    if kind == "imu":
        return IMUSample(
            sequence,
            timestamp,
            _vector(payload["accel_g"], 16.0),
            _vector(payload["gyro_dps"], 2000.0),
            _number(payload["temperature_c"], -40.0, 125.0),
        )
    if kind == "button":
        button_id = _integer(payload["button_id"], 0, 31)
        if type(payload["pressed"]) is not bool:
            raise TelemetryError("Button pressed field must be a boolean")
        return ButtonEvent(sequence, timestamp, button_id, payload["pressed"])
    return Heartbeat(sequence, timestamp)


class TelemetryDecoder:
    """Bounded newline framing over already supplied bytes; never opens serial.

    Bad frames increment counters and are discarded. An oversized line is dropped
    through its newline, so its tail cannot be interpreted as a new packet.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._discarding = False
        self.accepted_frames = 0
        self.rejected_frames = 0
        self.last_error: str | None = None

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, chunk: bytes) -> tuple[TelemetryEvent, ...]:
        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        if len(chunk) > MAX_CHUNK_BYTES:
            raise TelemetryError("Input chunk exceeds the decoder limit")
        events: list[TelemetryEvent] = []
        for byte in chunk:
            if self._discarding:
                if byte == 10:
                    self._discarding = False
                continue
            if byte == 10:
                line = bytes(self._buffer).removesuffix(b"\r")
                self._buffer.clear()
                try:
                    event = parse_telemetry(line)
                except TelemetryError as error:
                    self.rejected_frames += 1
                    self.last_error = str(error)
                else:
                    self.accepted_frames += 1
                    events.append(event)
            elif len(self._buffer) >= MAX_FRAME_BYTES and not (
                len(self._buffer) == MAX_FRAME_BYTES and byte == 13
            ):
                self._buffer.clear()
                self._discarding = True
                self.rejected_frames += 1
                self.last_error = "Telemetry line exceeds the frame limit"
            else:
                # CR is framing, not payload. Permit exactly one extra byte at
                # the limit; only LF can complete that otherwise-full line.
                self._buffer.append(byte)
        return tuple(events)

    def end_of_stream(self) -> None:
        """Discard a truncated frame; call before reusing a decoder for a new stream."""
        if self._buffer:
            self.rejected_frames += 1
            self.last_error = "Truncated telemetry frame"
        self._buffer.clear()
        self._discarding = False


class TelemetryMonitor:
    """Freshness and replay guard for parsed observations; not a device adapter.

    Sequence numbers are global unsigned 32-bit values. Forward wrap is accepted;
    duplicates and backwards packets are rejected. After a controller restart a
    caller must explicitly replace the monitor, only after supervising that restart.
    """

    def __init__(
        self,
        *,
        max_age_seconds: float = 1.0,
        button_queue_size: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            type(max_age_seconds) not in (int, float)
            or not math.isfinite(max_age_seconds)
            or max_age_seconds <= 0
        ):
            raise ValueError("max_age_seconds must be finite and positive")
        if type(button_queue_size) is not int or not 1 <= button_queue_size <= 1024:
            raise ValueError("button_queue_size must be between 1 and 1024")
        self.max_age_seconds = max_age_seconds
        self._clock = clock
        self._sequence: int | None = None
        self._received_at: float | None = None
        self._imu: tuple[float, IMUSample] | None = None
        self._buttons: deque[tuple[float, ButtonEvent]] = deque(maxlen=button_queue_size)
        self.rejected_sequences = 0
        self.dropped_buttons = 0

    def _now(self) -> float:
        now = self._clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("Clock must return a finite monotonic time")
        return now

    def ingest(self, event: TelemetryEvent) -> bool:
        if not isinstance(event, (IMUSample, ButtonEvent, Heartbeat)):
            raise TypeError("event must be a parsed telemetry event")
        _integer(event.sequence, 0, _UINT32_MAX)
        _integer(event.timestamp_ms, 0, _UINT32_MAX)
        now = self._now()
        if self._received_at is not None and now < self._received_at:
            raise ValueError("Clock moved backwards")
        if self._sequence is not None:
            delta = (event.sequence - self._sequence) & _UINT32_MAX
            if delta == 0 or delta >= (1 << 31):
                self.rejected_sequences += 1
                return False
        self._sequence = event.sequence
        self._received_at = now
        if isinstance(event, IMUSample):
            self._imu = (now, event)
        elif isinstance(event, ButtonEvent):
            if len(self._buttons) == self._buttons.maxlen:
                self.dropped_buttons += 1
            self._buttons.append((now, event))
        return True

    def latest_imu(self) -> IMUSample | None:
        now = self._now()
        if self._imu is None:
            return None
        received_at, sample = self._imu
        if not 0 <= now - received_at <= self.max_age_seconds:
            return None
        return sample

    def drain_buttons(self) -> tuple[ButtonEvent, ...]:
        now = self._now()
        result = tuple(
            event
            for received_at, event in self._buttons
            if 0 <= now - received_at <= self.max_age_seconds
        )
        self._buttons.clear()
        return result
