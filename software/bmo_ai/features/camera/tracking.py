"""Local position tracking, not identity recognition or physical pan/tilt."""
from dataclasses import dataclass
import math

from ..contracts import FeatureError
from .models import FaceBox, Frame, GazeTarget, finite


@dataclass(frozen=True, slots=True)
class TrackerConfig:
    max_age: float = .5
    loss_grace: float = .4
    dead_zone: float = .08
    smoothing_tau: float = .18
    max_speed: float = 1.8
    mirror: bool = False
    rotation: int = 0
    min_confidence: float = .5
    match_radius: float = .3
    switch_confirmations: int = 3

    def __post_init__(self):
        if (any(not finite(v) or v <= 0 for v in (self.max_age, self.loss_grace, self.smoothing_tau, self.max_speed, self.match_radius))
                or not finite(self.dead_zone) or not 0 <= self.dead_zone < 1
                or not finite(self.min_confidence) or not 0 <= self.min_confidence <= 1
                or type(self.mirror) is not bool or type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270)
                or type(self.switch_confirmations) is not int or not 1 <= self.switch_confirmations <= 20):
            raise FeatureError("invalid_tracker_config")


def map_position(u, v, *, mirror=False, rotation=0):
    """Camera-normalized point -> display-normalized gaze; y<0 means up."""
    if (not all(finite(value) and 0 <= value <= 1 for value in (u, v))
            or type(mirror) is not bool or type(rotation) is not int or rotation not in (0, 90, 180, 270)):
        raise FeatureError('invalid_camera_transform')
    if rotation == 90:
        u, v = 1 - v, u
    elif rotation == 180:
        u, v = 1 - u, 1 - v
    elif rotation == 270:
        u, v = v, 1 - u
    if mirror:
        u = 1 - u
    return 2 * u - 1, 2 * v - 1


def distance(a, b):
    return math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1])


def iou(a, b):
    width = max(0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    height = max(0, min(a.y + a.height, b.y + b.height) - max(a.y, b.y))
    intersection = width * height
    return intersection / (a.width * a.height + b.width * b.height - intersection)


class TargetTracker:
    def __init__(self, config=None):
        self.config = config or TrackerConfig()
        self.reset()

    def reset(self):
        self._box = self._pending = None
        self._pending_count = 0
        self._stream = self._seq = self._stamp = None
        self._seen = self._lost = self._output_at = None
        self._seen_seq = None
        self._target_id = 0
        self._point = (0.0, 0.0)
        self._generation = 0

    def _result(self, now, status):
        valid = status in ("tracking", "grace")
        until = (self._seen + self.config.max_age) if valid else now
        if status == "grace":
            until = min(until, self._lost + self.config.loss_grace)
        return GazeTarget(self._generation, self._seen_seq, self._seen, until,
                          self._point[0] if valid else None, self._point[1] if valid else None,
                          status, self._target_id or None)

    def expire(self, now, *, generation=None):
        if generation is not None:
            self._generation = generation
        if self._seen is None or now - self._seen > self.config.max_age:
            return self._result(now, "lost")
        if self._lost is not None:
            return self._result(now, "grace" if now - self._lost <= self.config.loss_grace else "lost")
        return self._result(now, "tracking")

    def update(self, frame: Frame, boxes, *, now, generation):
        if not isinstance(frame, Frame) or not finite(now):
            raise FeatureError("invalid_camera_frame")
        self._generation = generation
        if (frame.captured_at > now + .05 or now - frame.captured_at > self.config.max_age
                or self._stream is not None and frame.stream_generation < self._stream
                or frame.stream_generation == self._stream and self._seq is not None
                and (frame.seq <= self._seq or frame.captured_at < self._stamp)):
            return self.expire(now)
        if frame.stream_generation != self._stream:
            self.reset()
            self._generation = generation
            self._stream = frame.stream_generation
        self._seq, self._stamp = frame.seq, frame.captured_at
        candidates = tuple(box for box in boxes if isinstance(box, FaceBox) and box.confidence >= self.config.min_confidence)
        selected = None
        if candidates:
            if self._box is None:
                selected = self._first(candidates)
            else:
                nearest = min(candidates, key=lambda box: (distance(self._box, box), -iou(self._box, box)))
                if distance(self._box, nearest) <= self.config.match_radius or iou(self._box, nearest) >= .15:
                    selected = nearest
                else:
                    candidate = self._first(candidates)
                    self._pending_count = self._pending_count + 1 if self._pending is not None and distance(candidate, self._pending) < .15 else 1
                    self._pending = candidate
                    if self._pending_count >= self.config.switch_confirmations:
                        selected = candidate
                        self._box = None  # A new ephemeral position target, not an identity.
        else:
            self._pending = None
            self._pending_count = 0
        if selected is None:
            if self._lost is None:
                self._lost = now
            return self.expire(now)
        if self._box is None:
            self._target_id += 1
        self._box = selected
        self._pending = None
        self._pending_count = 0
        self._seen, self._lost = frame.captured_at, None
        self._seen_seq = frame.seq
        target = map_position(*selected.center, mirror=self.config.mirror, rotation=self.config.rotation)
        target = tuple(0.0 if abs(value) <= self.config.dead_zone else value for value in target)
        # A stalled detector/lost target cannot bank a large movement allowance
        # and spend it in a single rendered frame when detection resumes.
        dt = .1 if self._output_at is None else min(.1, max(0.0, now - self._output_at))
        alpha = 1 - math.exp(-dt / self.config.smoothing_tau)
        proposed = tuple(old + (new - old) * alpha for old, new in zip(self._point, target))
        dx, dy = proposed[0] - self._point[0], proposed[1] - self._point[1]
        length, limit = math.hypot(dx, dy), self.config.max_speed * dt
        ratio = min(1.0, limit / length) if length else 1.0
        self._point = (self._point[0] + dx * ratio, self._point[1] + dy * ratio)
        self._output_at = now
        return self._result(now, "tracking")

    @staticmethod
    def _first(boxes):
        return min(boxes, key=lambda box: (-box.width * box.height,
                   (box.center[0] - .5) ** 2 + (box.center[1] - .5) ** 2, box.x, box.y))
