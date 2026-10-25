"""사용자 입회 경로의 실제 프레임 무음 녹화. 기본 합성 VideoRecorder와 분리한다."""
from dataclasses import asdict, dataclass
import math
import os
import re
import stat
import time

from ..contracts import FeatureError, request_id_ok, valid_id
from ..media.video_fixture import MAX_VIDEO_BYTES
from .codec import validate_png
from .live_codec import png_to_dib, rgb_avi_bytes, validate_rgb_avi
from .video import VideoConfig, VideoRecorder, VideoStore


LIVE_CONTRACT = 'bmo_camera_live_video_v1'


@dataclass(frozen=True, slots=True)
class LiveVideoResult:
    request_id: str
    status: str
    code: str
    stop_reason: str = 'not_started'
    video_id: str | None = None
    sha256: str | None = None
    bytes: int = 0
    duration: float = 0
    simulated: bool = False
    executed: bool = False
    completed: bool = False
    audio_streams: int = 0

    def __post_init__(self):
        if (not request_id_ok(self.request_id)
                or self.status not in ('running', 'succeeded', 'failed', 'cancelled', 'rejected', 'unknown')
                or not valid_id(self.code) or not valid_id(self.stop_reason)
                or self.video_id is not None and (type(self.video_id) is not str
                    or re.fullmatch(r'video_[0-9a-f]{32}', self.video_id) is None)
                or self.sha256 is not None and (type(self.sha256) is not str
                    or re.fullmatch(r'[0-9a-f]{64}', self.sha256) is None)
                or type(self.bytes) is not int or not 0 <= self.bytes <= MAX_VIDEO_BYTES
                or type(self.duration) not in (int, float) or not math.isfinite(self.duration)
                or not 0 <= self.duration <= 30
                or self.simulated is not False or type(self.executed) is not bool
                or type(self.completed) is not bool or type(self.audio_streams) is not int
                or self.audio_streams != 0
                or self.completed and (self.status != 'succeeded' or not self.executed
                    or self.video_id is None or self.sha256 is None or not self.bytes or not self.duration)):
            raise FeatureError('invalid_live_video_result')

    def public(self):
        return {'contract': LIVE_CONTRACT, **asdict(self)}


class LiveVideoStore(VideoStore):
    result_type = LiveVideoResult
    contract = LIVE_CONTRACT
    validate_payload = staticmethod(validate_rgb_avi)
    saved_status = 'succeeded'
    simulated = False

    def __init__(self, root, *, max_storage_bytes=256 * 1024 * 1024, **kwargs):
        super().__init__(root, **kwargs)
        if type(max_storage_bytes) is not int or not MAX_VIDEO_BYTES <= max_storage_bytes <= 1024 ** 3:
            raise FeatureError('invalid_video_storage_limit')
        self.max_storage_bytes = max_storage_bytes

    def _space(self, needed, reserve):
        super()._space(needed, reserve)
        total = 0
        names = os.listdir(self._root_fd)
        if len(names) > 2048:
            raise FeatureError('video_storage_limit')
        for name in names:
            info = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise FeatureError('unsafe_video_root')
            total += info.st_size
        if total + needed > self.max_storage_bytes:
            raise FeatureError('video_storage_limit')


class LiveVideoRecorder(VideoRecorder):
    """단일 CameraBroker 소유권과 기존 취소/저널 정책을 재사용한다.

    320×240 기본, 최대640×480/4fps/120frame/8MiB. H264/오디오/연속녹화는 없다.
    실제 libcamera configure/stop/close의 native blocking 상한은 별도 실기 검수가 필요하다.
    """
    def __init__(self, broker, *, store=None, supervised=False, clock=time.monotonic, config=None):
        if type(supervised) is not bool:
            raise FeatureError('camera_supervised_opt_in_required')
        super().__init__(broker, store=store, clock=clock, simulated=False, config=config)
        self.supervised = supervised
        self._capture_attempted = False
        self._dib_frames = []
        self._geometry = None

    def _make_result(self, *args, **kwargs):
        kwargs.setdefault('executed', self._capture_attempted)
        return LiveVideoResult(*args, **kwargs)

    def _mode_allowed(self):
        return self.supervised is True and self.simulated is False and self.broker.simulated is False

    def start_video_recording(self, request_id, *, generation, consent=False):
        if self._request is None:
            self._capture_attempted = False
            self._dib_frames, self._geometry = [], None
        return super().start_video_recording(request_id, generation=generation, consent=consent)

    def _capture(self):
        self._capture_attempted = True
        frame = self.broker.read_frame(owner=self._owner)
        if frame is None:
            return
        now = self.clock()
        if (not math.isfinite(now) or now < self._started
                or frame.captured_at > now + .05 or now - frame.captured_at > 2):
            raise FeatureError('stale_video_frame')
        if validate_png(frame.payload) != (frame.width, frame.height):
            raise FeatureError('invalid_video_frame')
        width, height, dib = png_to_dib(frame.payload)
        geometry = (width, height)
        if self._geometry is not None and geometry != self._geometry:
            raise FeatureError('video_geometry_changed')
        if len(self._dib_frames) >= math.floor(self.config.max_duration * self.config.fps):
            self._complete('video_duration_limit')
            return
        frames = self._dib_frames + [dib]
        if len(dib) * len(frames) + 8192 > self.config.max_bytes:
            if self._dib_frames:
                self._complete('video_byte_limit')
                return
            raise FeatureError('video_byte_limit')
        self._payload = rgb_avi_bytes(frames, width=width, height=height, fps=self.config.fps)
        self._geometry, self._dib_frames = geometry, frames
        self._frames, self._last_frame = len(frames), now

    def _release_camera(self):
        result = super()._release_camera()
        self._dib_frames = []
        return result

    def snapshot(self):
        with self._guard:
            permitted = self._mode_allowed()
            return {'contract': LIVE_CONTRACT, 'recording': self._request is not None,
                    'generation': max(0, self._generation),
                    'readiness': 'UNVERIFIED' if self._blocked else 'READY' if permitted and self.store else 'UNSUPPORTED',
                    'code': 'video_close_failed' if self._blocked else 'camera_supervised_opt_in_required' if not permitted
                            else 'video_storage_not_selected' if self.store is None else 'video_recording' if self._request
                            else 'video_closed' if self._closed else 'video_ready',
                    'format': 'uncompressed_dib_rgb_avi', 'audio_streams': 0,
                    'simulated': False, 'executed': self._capture_attempted,
                    'actual_camera_verified': False, 'physical_acceptance': 'USER_REVIEW_REQUIRED',
                    'max_duration': self.config.max_duration, 'max_bytes': self.config.max_bytes,
                    'min_free_bytes': self.config.min_free_bytes,
                    'frames': self._frames, 'result': self._result.public() if self._result else None}
