"""Camera-local immutable data, with no device, image persistence or identity IO."""
from dataclasses import asdict, dataclass
import math
import re
from typing import Protocol

from ..contracts import FeatureError, request_id_ok, valid_id


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class Frame:
    stream_generation: int
    seq: int
    captured_at: float
    width: int
    height: int
    payload: bytes
    format: str = "png"
    focus_ok: bool = True

    def __post_init__(self):
        if (type(self.stream_generation) is not int or self.stream_generation < 0
                or type(self.seq) is not int or self.seq < 0
                or not finite(self.captured_at) or self.captured_at < 0
                or any(type(n) is not int or not 1 <= n <= 4096 for n in (self.width, self.height))
                or type(self.payload) is not bytes or not 0 < len(self.payload) <= 8 * 1024 * 1024
                or self.format != "png" or type(self.focus_ok) is not bool):
            raise FeatureError("invalid_camera_frame")


@dataclass(frozen=True, slots=True)
class FaceBox:
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0

    def __post_init__(self):
        values = (self.x, self.y, self.width, self.height, self.confidence)
        if (not all(finite(value) for value in values)
                or not 0 <= self.x <= 1 or not 0 <= self.y <= 1
                or not 0 < self.width <= 1 or not 0 < self.height <= 1
                or self.x + self.width > 1.000001 or self.y + self.height > 1.000001
                or not 0 <= self.confidence <= 1):
            raise FeatureError("invalid_face_box")

    @property
    def center(self):
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class GazeTarget:
    generation: int
    frame_seq: int | None
    captured_at: float | None
    valid_until: float
    x: float | None
    y: float | None
    status: str
    target_id: int | None = None

    def __post_init__(self):
        if (type(self.generation) is not int or self.generation < 0
                or self.status not in ("tracking", "grace", "lost", "off")
                or self.frame_seq is not None and (type(self.frame_seq) is not int or self.frame_seq < 0)
                or self.target_id is not None and (type(self.target_id) is not int or self.target_id < 1)
                or not finite(self.valid_until)
                or any(value is not None and not finite(value) for value in (self.x, self.y, self.captured_at))
                or any(value is not None and not -1 <= value <= 1 for value in (self.x, self.y))
                or (self.status in ("tracking", "grace") and (self.x is None or self.y is None))
                or (self.status in ("lost", "off") and (self.x is not None or self.y is not None))):
            raise FeatureError("invalid_gaze_target")

    def public(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PhotoResult:
    request_id: str
    status: str
    code: str
    photo_id: str | None = None
    sha256: str | None = None
    bytes: int = 0
    simulated: bool = True
    executed: bool = False
    completed: bool = False
    completion: str = "not_started"

    def __post_init__(self):
        if (not request_id_ok(self.request_id)
                or self.status not in ("running", "simulated", "succeeded", "failed", "cancelled", "rejected", "unknown")
                or not valid_id(self.code) or not valid_id(self.completion)
                or self.photo_id is not None and (type(self.photo_id) is not str or re.fullmatch(r'photo_[0-9a-f]{32}', self.photo_id) is None)
                or self.sha256 is not None and (type(self.sha256) is not str or re.fullmatch(r'[0-9a-f]{64}', self.sha256) is None)
                or any(type(value) is not bool for value in (self.simulated, self.executed, self.completed))
                or self.simulated and self.executed
                or type(self.bytes) is not int or self.bytes < 0
                or self.completed and (self.photo_id is None or self.sha256 is None or not self.bytes)):
            raise FeatureError("invalid_photo_result")

    def public(self):
        # Neither a filesystem path nor image data is a public result field.
        return asdict(self)


class CameraBackend(Protocol):
    simulated: bool

    def open(self, stream_generation: int) -> None: ...
    def read_frame(self) -> Frame: ...
    def capture_photo(self) -> Frame: ...
    def close(self) -> None: ...


class FaceDetector(Protocol):
    def detect(self, frame: Frame) -> tuple[FaceBox, ...]: ...
    def close(self) -> None: ...
