"""Owner-polled camera feature; it never invokes face, speech or motor APIs."""
from threading import Event, Lock, RLock
import time

from ..contracts import FeatureError, request_id_ok
from .backends import DemoBackend, FixtureDetector, NullDetector
from .broker import CameraBroker
from .codec import validate_png
from .models import GazeTarget, PhotoResult
from .tracking import TargetTracker, TrackerConfig


class _Cancelled(Exception):
    pass


class CameraFeature:
    def __init__(self, *, backend_factory=None, detector=None, store=None, on_target=None,
                 clock=time.monotonic, tracker_config=None, max_hz=10):
        if type(max_hz) not in (int, float) or not 1 <= max_hz <= 10:
            raise FeatureError('invalid_camera_rate')
        demo = backend_factory is None
        self.clock, self.store = clock, store
        self.broker = CameraBroker(backend_factory or (lambda: DemoBackend(clock=clock)))
        self.detector = detector if detector is not None else FixtureDetector() if demo else NullDetector()
        self.tracker = TargetTracker(tracker_config or TrackerConfig())
        self.on_target = on_target
        self._interval = 1 / max_hz
        self._lock, self._photo_lock = RLock(), Lock()
        self._tracking = self._preview = self._photo_active = self._closed = False
        self._generation = self._revision = 0
        self._photo_id = None
        self._photo_cancel = Event()
        self._frame = None
        self._last_poll = None
        self._error = None
        self._target = GazeTarget(0, None, None, clock(), None, None, 'off')

    def _generation_check(self, generation):
        if type(generation) is not int or generation < self._generation or generation < 0:
            raise FeatureError('stale_camera_generation')
        if self._closed:
            raise FeatureError('camera_closed')
        if generation != self._generation:
            self._generation = generation
            self._revision += 1
            self._photo_cancel.set()
            self.tracker.reset()
            self._frame = None

    def _emit(self, target):
        self._target = target
        if self.on_target is not None:
            try:
                self.on_target(target)
            except Exception:
                self._error = 'camera_target_callback_failed'

    def set_tracking(self, enabled, *, generation):
        return self._set_option('tracking', enabled, generation)

    def set_preview(self, enabled, *, generation):
        return self._set_option('preview', enabled, generation)

    def _set_option(self, name, enabled, generation):
        if type(enabled) is not bool:
            raise FeatureError('invalid_camera_option')
        with self._lock:
            self._generation_check(generation)
            setattr(self, '_' + name, enabled)
            self._error = None
            wanted, revision = self._tracking or self._preview or self._photo_active, self._revision
            if name == 'tracking' and not enabled:
                self.tracker.reset()
                self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
            if name == 'preview' and not enabled:
                self._frame = None
        try:
            if wanted:
                def current():
                    with self._lock:
                        if revision != self._revision or self._closed:
                            raise _Cancelled()
                self.broker.open(cancel_check=current)
                with self._lock:
                    if revision != self._revision or self._closed:
                        self.broker.close()
            else:
                self.broker.close()
        except Exception:
            self.off()
            self._error = 'camera_unavailable'
            raise FeatureError('camera_unavailable') from None
        return self.snapshot()

    def poll(self):
        now = self.clock()
        with self._lock:
            if self._closed or not (self._tracking or self._preview):
                return self.snapshot()
            if self._photo_active or self._last_poll is not None and now - self._last_poll < self._interval:
                if self._tracking:
                    self._emit(self.tracker.expire(now, generation=self._generation))
                return self.snapshot()
            self._last_poll = now
            revision, generation = self._revision, self._generation
            tracking = self._tracking
        try:
            def current():
                with self._lock:
                    if revision != self._revision or self._closed:
                        raise _Cancelled()
            frame = self.broker.read_frame(cancel_check=current)
            now = self.clock()
            if frame is not None and (frame.captured_at > now + .05 or now - frame.captured_at > self.tracker.config.max_age):
                frame = None
            if frame is not None and validate_png(frame.payload) != (frame.width, frame.height):
                raise FeatureError('invalid_camera_frame')
            boxes = self.detector.detect(frame) if frame is not None and tracking else ()
            now = self.clock()
            if frame is not None and now - frame.captured_at > self.tracker.config.max_age:
                frame = None
            with self._lock:
                if revision != self._revision or self._closed:
                    return self.snapshot()
                if frame is not None:
                    self._frame = frame if self._preview else None
                elif self._frame is not None and now - self._frame.captured_at > self.tracker.config.max_age:
                    self._frame = None
                if self._tracking:
                    self._emit(self.tracker.update(frame, boxes, now=now, generation=generation)
                               if frame is not None else self.tracker.expire(now, generation=generation))
        except Exception:
            self.off()
            self._error = 'camera_capture_failed'
        return self.snapshot()

    def snapshot(self):
        with self._lock:
            return dict(on=self.broker.opened, tracking=self._tracking, preview=self._preview,
                        photo_active=self._photo_active, generation=self._generation,
                        stream_generation=self.broker.stream_generation, target=self._target.public(),
                        preview_available=self.preview_bytes() is not None, preview_mime='image/png',
                        simulated=self.broker.simulated, error=self._error)

    def preview_bytes(self):
        with self._lock:
            if (not self._preview or self._frame is None
                    or self.clock() - self._frame.captured_at > self.tracker.config.max_age):
                return None
            return self._frame.payload

    def photo_bytes(self, photo_id):
        if self.store is None:
            raise FeatureError('photo_store_unconfigured')
        return self.store.read(photo_id)

    def take_photo(self, request_id, *, generation, cancel_check=None):
        if not request_id_ok(request_id):
            raise FeatureError('invalid_photo_request')
        with self._lock:
            self._generation_check(generation)
        if self.store is None:
            return PhotoResult(request_id, 'rejected', 'photo_store_unconfigured')
        if not self._photo_lock.acquire(blocking=False):
            return PhotoResult(request_id, 'running', 'photo_busy', simulated=self.broker.simulated)
        reserved = False
        with self._lock:
            revision = self._revision
            self._photo_cancel = Event()
            self._photo_active, self._photo_id = True, request_id

        def check():
            if self._photo_cancel.is_set() or revision != self._revision or self._closed:
                raise _Cancelled()
            if cancel_check is not None:
                try:
                    if cancel_check() is True:
                        raise _Cancelled()
                except Exception:
                    raise _Cancelled() from None

        simulated = self.broker.simulated
        capture_attempted = False
        try:
            previous = self.store.begin(request_id, simulated=simulated)
            if previous is not None:
                return previous
            reserved = True
            check()
            self.broker.open(cancel_check=check)
            simulated = self.broker.simulated
            check()
            capture_attempted = True
            frame = self.broker.capture_photo(cancel_check=check)
            check()
            now = self.clock()
            if frame.captured_at > now + .05 or now - frame.captured_at > self.tracker.config.max_age:
                raise FeatureError('stale_photo')
            result = self.store.save(request_id, frame, simulated=simulated, cancel_check=check)
            reserved = False  # save owns final journal completion after commit.
            return result
        except BaseException as error:
            interrupted = not isinstance(error, Exception)
            cancelled = (isinstance(error, _Cancelled)
                         or isinstance(error, FeatureError) and error.code == 'camera_cancelled'
                         or self._photo_cancel.is_set() or revision != self._revision or self._closed)
            code = ('photo_interrupted' if interrupted else 'photo_cancelled' if cancelled
                    else error.code if isinstance(error, FeatureError) else 'photo_storage_failed')
            status = 'unknown' if interrupted else 'cancelled' if cancelled else 'failed'
            result = PhotoResult(request_id, status, code,
                                 simulated=simulated, executed=not simulated and capture_attempted,
                                 completion='unknown' if interrupted else 'capture_attempted' if capture_attempted and not simulated else 'not_started')
            if reserved:
                try:
                    self.store.finish(result)
                except Exception:
                    # The persisted running reservation becomes unknown on retry.
                    pass
            if interrupted:
                raise
            return result
        finally:
            with self._lock:
                self._photo_active, self._photo_id = False, None
                wanted = (self._tracking or self._preview) and revision == self._revision and not self._closed
                if wanted and not self.broker.opened:
                    # A failed real capture must not revive a closed sensor.
                    self._tracking = self._preview = False
                    self._frame = None
                    self.tracker.reset()
                    self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
            if not wanted:
                if not self.broker.close():
                    self._error = 'camera_close_failed'
            self._photo_lock.release()

    def cancel_photo(self, request_id):
        with self._lock:
            if request_id == self._photo_id and self._photo_active:
                self._photo_cancel.set()
                return True
            return False

    def off(self):
        with self._lock:
            self._revision += 1
            self._photo_cancel.set()
            self._tracking = self._preview = False
            self._frame = self._last_poll = None
            self.tracker.reset()
            self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
        if not self.broker.close():
            self._error = 'camera_close_failed'
        try:
            self.detector.close()
        except Exception:
            self._error = 'camera_detector_close_failed'
        return self.snapshot()

    def close(self):
        with self._lock:
            self._closed = True
        return self.off()

    def cleanup_snapshot(self):
        """off만으로 native close 성공을 추정하지 않는 호스트 소유 자원 보고."""
        with self._lock:
            unknown = (self.broker.cleanup_uncertain or self._error in
                       ('camera_close_failed', 'camera_detector_close_failed'))
            sensor_closed = not self.broker.opened
            cleaned = self._closed and sensor_closed and not self._photo_active and not unknown
            return {'resources_cleaned': cleaned, 'sensor_closed': sensor_closed,
                    'photo_active': self._photo_active, 'close_unknown': unknown,
                    'code': 'resources_cleaned' if cleaned else 'camera_cleanup_unknown',
                    'physical_release_verified': False, 'scope': 'host_owned_camera_only'}
