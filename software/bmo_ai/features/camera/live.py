"""사용자 입회 opt-in만 실제 카메라 경로를 선택한다. 생성/진단은 장치를 열지 않는다."""
from dataclasses import dataclass
from importlib.util import find_spec
import os
from pathlib import Path
import time

from ..contracts import FeatureError
from .backends import NullDetector, OpenCVFaceDetector, Picamera2Backend
from .broker import CameraBroker
from .feature import CameraFeature
from .limits import MAX_PNG_BYTES
from .store import PhotoStore, private_root


def probe_camera_capability(*, finder=find_spec):
    """모듈 검색 정보만 반환한다. Picamera2 생성·import·카메라 목록 조회도 하지 않는다."""
    found = {}
    for name in ('picamera2', 'libcamera', 'cv2', 'numpy'):
        try:
            found[name] = finder(name) is not None
        except (ImportError, ValueError, AttributeError, OSError):
            found[name] = False
    available = found['picamera2'] and found['libcamera']
    return {'backend': 'picamera2', 'available': available,
            'code': 'camera_backend_available' if available else 'camera_backend_unavailable',
            'modules': found, 'opened': False, 'physical_tested': False,
            'tracking_available': found['cv2'] and found['numpy'],
            'activation': 'explicit_supervised_opt_in'}


@dataclass(frozen=True, slots=True)
class LiveCameraOptions:
    enabled: bool = False
    supervised: bool = False
    tracking: bool = False
    size: tuple[int, int] = (640, 360)
    still_size: tuple[int, int] = (1280, 720)
    max_photo_bytes: int = 8 * 1024 * 1024
    max_storage_bytes: int = 256 * 1024 * 1024
    min_free_bytes: int = 32 * 1024 * 1024

    def __post_init__(self):
        if any(type(value) is not bool for value in (self.enabled, self.supervised, self.tracking)):
            raise FeatureError('invalid_camera_options')
        for size in (self.size, self.still_size):
            if (type(size) is not tuple or len(size) != 2
                    or any(type(n) is not int or not 1 <= n <= 1920 for n in size)):
                raise FeatureError('invalid_camera_size')
        if (type(self.max_photo_bytes) is not int or not 1024 <= self.max_photo_bytes <= MAX_PNG_BYTES
                or type(self.max_storage_bytes) is not int or not self.max_photo_bytes <= self.max_storage_bytes <= 1024 ** 3
                or type(self.min_free_bytes) is not int or not 0 <= self.min_free_bytes <= 1024 ** 3):
            raise FeatureError('invalid_camera_storage_limit')


class BoundedPhotoStore(PhotoStore):
    """사진을 자동 삭제하지 않는다. 선택한 private 경로의 크기/여유 공간을 제한한다."""
    def __init__(self, root, *, options, free_bytes=None, **kwargs):
        super().__init__(root, **kwargs)
        self.options = options
        self._free_bytes = free_bytes or (lambda p: os.statvfs(p).f_bavail * os.statvfs(p).f_frsize)

    def _budget(self, needed):
        root = private_root(self.root)
        total = 0
        for count, path in enumerate(root.iterdir()):
            if count >= 2048:
                raise FeatureError('photo_storage_limit')
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid():
                raise FeatureError('unsafe_photo_root')
            total += path.stat().st_size
            if total + needed > self.options.max_storage_bytes:
                raise FeatureError('photo_storage_limit')
        free = self._free_bytes(root)
        if type(free) is not int or free < needed + self.options.min_free_bytes:
            raise FeatureError('photo_disk_full')

    def begin(self, request_id, *, simulated=True, fingerprint='camera_png_v1'):
        self._budget(self.options.max_photo_bytes)
        return super().begin(request_id, simulated=simulated, fingerprint=fingerprint)

    def save(self, request_id, frame, *, simulated, cancel_check, allow_unconfirmed=False):
        if len(frame.payload) > self.options.max_photo_bytes:
            raise FeatureError('photo_byte_limit')
        self._budget(len(frame.payload))
        return super().save(request_id, frame, simulated=simulated, cancel_check=cancel_check,
                            allow_unconfirmed=allow_unconfirmed)


class LiveCameraFactory:
    simulated = False

    def __init__(self, options, *, clock, camera_factory=None, wait_clock=time.monotonic):
        self.options, self.clock = options, clock
        self.camera_factory, self.wait_clock = camera_factory, wait_clock

    def __call__(self):
        o = self.options
        return LivePicamera2Backend(enabled=o.enabled and o.supervised, size=o.size,
            still_size=o.still_size, clock=self.clock, camera_factory=self.camera_factory,
            wait_clock=self.wait_clock, read_timeout=1.5, photo_timeout=5.0, focus_timeout=2.5)


class LivePicamera2Backend(Picamera2Backend):
    # SDK close 실패 후 같은 프로세스의 새 feature로 센서를 재취득하지 않는다.
    # 실제 상태가 UNKNOWN이면 자동 재시도 대신 사용자에게 종료/점검을 요청한다.
    _close_uncertain = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        factory = self._factory
        def guarded_factory():
            # 부모의 장치 lock 취득 뒤에도 재검사해 close와의 경쟁을 차단한다.
            if LivePicamera2Backend._close_uncertain:
                raise FeatureError('camera_close_unverified')
            return factory()
        self._factory = guarded_factory

    def open(self, stream_generation, *, cancel_check=None):
        if LivePicamera2Backend._close_uncertain:
            raise FeatureError('camera_close_unverified')
        return super().open(stream_generation, cancel_check=cancel_check)

    def close(self):
        was_uncertain = LivePicamera2Backend._close_uncertain
        if self._camera is not None:
            LivePicamera2Backend._close_uncertain = True
        released = super().close()
        LivePicamera2Backend._close_uncertain = was_uncertain or released is False
        return released and not LivePicamera2Backend._close_uncertain


class LiveCameraBroker(CameraBroker):
    def close(self, *, owner=None):
        result = super().close(owner=owner)
        if result is False:
            with self._state:
                if self._owner is owner:
                    self._owned_close_uncertain = True
        return result


def build_live_camera(options, photo_root, *, clock=time.monotonic, camera_factory=None,
                      wait_clock=time.monotonic, on_target=None, free_bytes=None):
    """명시적 입회 승인 후 사용할 실제 feature. 여기서는 센서도 파일도 열지 않는다."""
    if not isinstance(options, LiveCameraOptions) or not options.enabled or not options.supervised:
        raise FeatureError('camera_supervised_opt_in_required')
    if not isinstance(photo_root, (str, Path)) or not Path(photo_root).is_absolute():
        raise FeatureError('photo_private_directory_required')
    factory = LiveCameraFactory(options, clock=clock, camera_factory=camera_factory, wait_clock=wait_clock)
    store = BoundedPhotoStore(photo_root, options=options, free_bytes=free_bytes)
    detector = OpenCVFaceDetector(enabled=True) if options.tracking else NullDetector()
    feature = CameraFeature(backend_factory=factory, detector=detector, store=store,
                            clock=clock, on_target=on_target, max_hz=4,
                            asynchronous_preview=True, preview_hz=20)
    feature.broker = LiveCameraBroker(factory)
    return feature
