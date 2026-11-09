"""One camera owner with an optional bounded latest-frame preview producer."""
from dataclasses import replace
from threading import Event, Lock, RLock, Thread, current_thread
import time

from ..contracts import FeatureError, request_id_ok
from .backends import DemoBackend, FixtureDetector, NullDetector
from .broker import CameraBroker
from .codec import validate_png
from .jpeg_codec import validate_jpeg
from .models import GazeTarget, PhotoResult
from .diagnostics import PHOTO_STAGES
from .tracking import TargetTracker, TrackerConfig


class _Cancelled(Exception):
    pass


class CameraFeature:
    def __init__(self, *, backend_factory=None, detector=None, store=None, on_target=None,
                 clock=time.monotonic, tracker_config=None, max_hz=10,
                 asynchronous_preview=False, preview_hz=20):
        if type(max_hz) not in (int, float) or not 1 <= max_hz <= 10:
            raise FeatureError('invalid_camera_rate')
        if type(asynchronous_preview) is not bool:
            raise FeatureError('invalid_camera_option')
        if type(preview_hz) not in (int, float) or not 1 <= preview_hz <= 30:
            raise FeatureError('invalid_camera_preview_rate')
        demo = backend_factory is None
        self.clock, self.store = clock, store
        self.broker = CameraBroker(backend_factory or (lambda: DemoBackend(clock=clock)))
        self.detector = detector if detector is not None else FixtureDetector() if demo else NullDetector()
        self.tracker = TargetTracker(tracker_config or TrackerConfig())
        self.on_target = on_target
        self._interval = 1 / max_hz
        self.asynchronous_preview = asynchronous_preview
        self.preview_hz = preview_hz
        self._producer_thread = None
        self._producer_stop = Event()
        self._producer_control = RLock()
        self._producer_join_timeout = 2.0
        self._producer_retirement_unknown = False
        self._tracking_frame_id = self._last_served_id = None
        self._pipeline = dict(sensor_frames_observed=0, encoded=0, published=0,
                              served=0, dropped_replaced=0)
        self._lock, self._photo_lock = RLock(), Lock()
        self._tracking = self._preview = self._photo_active = self._closed = False
        # A user's tracking choice survives a temporary photo/video borrower.
        # It is deliberately separate from effective detector consumption.
        self._tracking_requested = False
        self._tracking_suspensions = set()
        self._consumers = set()
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

    def _wanted_locked(self):
        return self._tracking_requested or self._preview or self._photo_active or bool(self._consumers)

    @staticmethod
    def _consumer_name(consumer):
        if type(consumer) is not str or consumer not in ('photo', 'video'):
            raise FeatureError('invalid_camera_consumer')

    def acquire_consumer(self, consumer, *, generation):
        """Keep this camera's producer alive; never create a second SDK owner."""
        return self._set_consumer(consumer, True, generation=generation)

    def release_consumer(self, consumer, *, generation):
        return self._set_consumer(consumer, False, generation=generation)

    def _set_consumer(self, consumer, enabled, *, generation):
        self._consumer_name(consumer)
        with self._lock:
            self._generation_check(generation)
            if enabled and self._producer_retirement_unknown:
                raise FeatureError('camera_preview_stop_failed')
            if enabled:
                self._consumers.add(consumer)
            else:
                self._consumers.discard(consumer)
            wanted, revision = self._wanted_locked(), self._revision
        return self._reconcile_stream(wanted, revision)

    def suspend_tracking(self, consumer, *, generation):
        return self._suspend_tracking(consumer, True, generation=generation)

    def resume_tracking(self, consumer, *, generation):
        return self._suspend_tracking(consumer, False, generation=generation)

    def _suspend_tracking(self, consumer, suspended, *, generation):
        self._consumer_name(consumer)
        with self._lock:
            self._generation_check(generation)
            if suspended:
                self._tracking_suspensions.add(consumer)
            else:
                self._tracking_suspensions.discard(consumer)
            self._tracking = self._tracking_requested and not self._tracking_suspensions
            # Never use a previous subject/frame as a newly acquired target.
            self.tracker.reset()
            self._tracking_frame_id = None
            self._last_poll = None
            self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
            wanted, revision = self._wanted_locked(), self._revision
        return self._reconcile_stream(wanted, revision)

    def _set_option(self, name, enabled, generation):
        if type(enabled) is not bool:
            raise FeatureError('invalid_camera_option')
        if name == 'tracking' and enabled and isinstance(self.detector, NullDetector):
            raise FeatureError('face_detector_unavailable')
        with self._lock:
            self._generation_check(generation)
            if enabled and self._producer_retirement_unknown:
                raise FeatureError('camera_preview_stop_failed')
            if name == 'tracking':
                self._tracking_requested = enabled
                self._tracking = enabled and not self._tracking_suspensions
            else:
                self._preview = enabled
            self._error = None
            wanted, revision = self._wanted_locked(), self._revision
            if name == 'tracking' and not enabled:
                self.tracker.reset()
                self._tracking_frame_id = None
                self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
            if not wanted:
                self._frame = None
        return self._reconcile_stream(wanted, revision)

    def _reconcile_stream(self, wanted, revision):
        try:
            if self.asynchronous_preview and not self._pause_producer():
                raise FeatureError('camera_preview_stop_failed')
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
            if self.asynchronous_preview and wanted:
                self._start_producer()
        except Exception:
            self.off()
            self._error = 'camera_unavailable'
            raise FeatureError('camera_unavailable') from None
        return self.snapshot()

    @staticmethod
    def _frame_id(frame):
        return f'{frame.stream_generation}:{frame.seq}'

    def _valid_preview(self, frame, now):
        if frame is None or frame.captured_at > now + .05 or now - frame.captured_at > self.tracker.config.max_age:
            return False
        geometry = validate_jpeg(frame.payload) if frame.format == 'jpeg' else validate_png(frame.payload)
        if geometry != (frame.width, frame.height):
            raise FeatureError('invalid_camera_frame')
        return True

    def _pause_producer(self):
        """Finish a bounded in-flight read; an ordinary pause never aborts the SDK."""
        with self._producer_control:
            thread = self._producer_thread
            self._producer_stop.set()
            if thread is not None and thread is not current_thread():
                thread.join(self._producer_join_timeout)
            retired = thread is None or not thread.is_alive()
            if retired:
                self._producer_thread = None
            else:
                with self._lock:
                    self._producer_retirement_unknown = True
                    self._revision += 1
                    self._photo_cancel.set()
                    self._tracking = self._tracking_requested = self._preview = False
                    self._tracking_suspensions.clear()
                    self._consumers.clear()
                    self._frame = None
                    self.tracker.reset()
                    self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
                    self._error = 'camera_preview_stop_failed'
            return retired

    def _start_producer(self):
        if not self.asynchronous_preview:
            return
        with self._producer_control:
            with self._lock:
                if (self._closed or self._photo_active or not self._wanted_locked()
                        or not self.broker.opened or self._producer_retirement_unknown):
                    return
                if self._producer_thread is not None and self._producer_thread.is_alive():
                    return
                revision = self._revision
                stop = self._producer_stop = Event()
                thread = Thread(target=self._produce_preview, args=(revision, stop),
                                name='bmo-camera-preview', daemon=True)
                self._producer_thread = thread
            thread.start()

    def _produce_preview(self, revision, stop):
        def current():
            # The pause event intentionally is not an SDK cancellation signal.
            with self._lock:
                if revision != self._revision or self._closed:
                    raise _Cancelled()
        try:
            while not stop.is_set():
                started = time.monotonic()
                frame = self.broker.read_preview(cancel_check=current)
                now = self.clock()
                if frame is not None:
                    with self._lock:
                        self._pipeline['sensor_frames_observed'] += 1
                        self._pipeline['encoded'] += 1
                valid = self._valid_preview(frame, now)
                with self._lock:
                    if revision != self._revision or self._closed:
                        return
                    if valid and not stop.is_set():
                        if self._frame is not None and self._frame_id(self._frame) != self._last_served_id:
                            self._pipeline['dropped_replaced'] += 1
                        self._frame = frame
                        self._pipeline['published'] += 1
                    elif self._frame is not None and now - self._frame.captured_at > self.tracker.config.max_age:
                        self._frame = None
                stop.wait(max(0, 1 / self.preview_hz - (time.monotonic() - started)))
        except Exception:
            with self._lock:
                # off()/generation cancellation already owns its cleanup.
                failed = revision == self._revision and not self._closed
                if failed:
                    self._revision += 1
                    self._photo_cancel.set()
                    self._tracking = self._tracking_requested = self._preview = False
                    self._tracking_suspensions.clear()
                    self._consumers.clear()
                    self._frame = None
                    self._error = 'camera_capture_failed'
                    self.tracker.reset()
                    self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
            if failed:
                self.broker.close()
        finally:
            # A timed-out retirement must never leave a second sensor owner.
            with self._lock:
                abandoned = self._producer_retirement_unknown
            if abandoned:
                self.broker.close()

    def _poll_latest(self, now):
        """Tracking samples the producer's one-slot cache, never the sensor."""
        with self._lock:
            if self._closed or not self._wanted_locked():
                return self.snapshot()
            if self._photo_active or self._last_poll is not None and now - self._last_poll < self._interval:
                if self._tracking:
                    self._emit(self.tracker.expire(now, generation=self._generation))
                return self.snapshot()
            self._last_poll = now
            revision, generation = self._revision, self._generation
            frame, tracking = self._frame, self._tracking
            if frame is not None and (frame.captured_at > now + .05 or now - frame.captured_at > self.tracker.config.max_age):
                self._frame = frame = None
            identity = self._frame_id(frame) if frame is not None else None
            if not tracking or identity == self._tracking_frame_id:
                if tracking:
                    self._emit(self.tracker.expire(now, generation=generation))
                return self.snapshot()
            self._tracking_frame_id = identity
        try:
            boxes = self.detector.detect(frame) if frame is not None else ()
            now = self.clock()
            with self._lock:
                if revision != self._revision or self._closed:
                    return self.snapshot()
                if self._tracking:
                    self._emit(self.tracker.update(frame, boxes, now=now, generation=generation)
                               if frame is not None else self.tracker.expire(now, generation=generation))
        except Exception:
            self.off()
            self._error = 'camera_capture_failed'
        return self.snapshot()

    def poll(self):
        now = self.clock()
        if self.asynchronous_preview:
            return self._poll_latest(now)
        with self._lock:
            if self._closed or not self._wanted_locked():
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
                    self._frame = frame
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
            available = self.preview_bytes() is not None
            mime = 'image/jpeg' if self._frame is not None and self._frame.format == 'jpeg' else 'image/png'
            pipeline = dict(self._pipeline, capacity=1, target_hz=self.preview_hz,
                            worker_alive=self._producer_thread is not None and self._producer_thread.is_alive(),
                            physical_fps='NOT_MEASURED')
            return dict(on=self.broker.opened, tracking=self._tracking, preview=self._preview,
                        tracking_requested=self._tracking_requested,
                        tracking_suspended=sorted(self._tracking_suspensions),
                        tracking_available=not isinstance(self.detector, NullDetector),
                        tracking_rate_hz=1 / self._interval,
                        tracking_transform=dict(mirror=self.tracker.config.mirror,
                                                rotation=self.tracker.config.rotation),
                        consumers=sorted(self._consumers),
                        photo_active=self._photo_active, generation=self._generation,
                        stream_generation=self.broker.stream_generation, target=self._target.public(),
                        preview_available=available, preview_mime=mime,
                        preview_pipeline=pipeline, focus=self.focus_snapshot(),
                        simulated=self.broker.simulated, error=self._error)

    def preview_bytes(self):
        with self._lock:
            if (not self._preview or self._frame is None
                    or self.clock() - self._frame.captured_at > self.tracker.config.max_age):
                return None
            return self._frame.payload

    def latest_frame(self, after=None):
        """Cached-only browser transport: no SDK read, encoding, or waiting."""
        with self._lock:
            if self.preview_bytes() is None:
                return None
            frame = self._frame
            identity = self._frame_id(frame)
            if identity == after:
                return None
            if self._last_served_id != identity:
                self._pipeline['served'] += 1
            self._last_served_id = identity
            return dict(id=identity, payload=frame.payload,
                        mime='image/jpeg' if frame.format == 'jpeg' else 'image/png',
                        captured_at=frame.captured_at, age_ms=max(0, (self.clock() - frame.captured_at) * 1000))

    def cached_frame(self, after=None):
        """Latest immutable frame for an admitted in-process consumer.

        This performs no device read/decode and does not require the user's
        preview panel to be visible. A recorder shares this one-slot cache.
        """
        with self._lock:
            frame = self._frame
            now = self.clock()
            if (frame is None or self._closed or not self.broker.opened
                    or frame.captured_at > now + .05
                    or now - frame.captured_at > self.tracker.config.max_age
                    or self._frame_id(frame) == after):
                return None
            return frame

    def focus_snapshot(self):
        return self.broker.focus_snapshot()

    def refocus(self, *, cancel_check=None):
        with self._lock:
            revision = self._revision
            if self._closed or self._photo_active:
                raise FeatureError('camera_busy')
        def current():
            with self._lock:
                if revision != self._revision or self._closed:
                    raise _Cancelled()
            if cancel_check is not None and cancel_check() is True:
                raise _Cancelled()
        return self.broker.refocus(cancel_check=current)

    def photo_bytes(self, photo_id):
        if self.store is None:
            raise FeatureError('photo_store_unconfigured')
        return self.store.read(photo_id)

    def take_photo(self, request_id, *, generation, cancel_check=None,
                   allow_unconfirmed=False, resume_preview=True):
        if not request_id_ok(request_id):
            raise FeatureError('invalid_photo_request')
        if type(allow_unconfirmed) is not bool or type(resume_preview) is not bool:
            raise FeatureError('invalid_photo_option')
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
        focus_review = False
        stage = 'storage_prepare'
        try:
            if self.asynchronous_preview and not self._pause_producer():
                raise FeatureError('camera_preview_stop_failed')
            focus = self.focus_snapshot() if self.asynchronous_preview and self.broker.opened else None
            if (focus is not None and ((not allow_unconfirmed and focus.get('status') != 'focused')
                                      or focus.get('can_take_anyway') is False)):
                focus_review = True
                return PhotoResult(request_id, 'failed', 'photo_focus_uncertain', simulated=simulated, stage='focus')
            previous = self.store.begin(request_id, simulated=simulated)
            if previous is not None:
                return previous
            reserved = True
            check()
            stage = 'camera_open'
            self.broker.open(cancel_check=check)
            simulated = self.broker.simulated
            check()
            capture_attempted = True
            stage = 'still_capture'
            capture_started = self.clock()
            options = {'allow_unconfirmed': True} if allow_unconfirmed else {}
            frame = self.broker.capture_photo(cancel_check=check, **options)
            if allow_unconfirmed and frame.focus_ok:
                frame = replace(frame, focus_ok=False, focus_status='unconfirmed')
            check()
            stage = 'frame_validation'
            now = self.clock()
            # A still Job includes PNG encoding and restoration of preview.
            # Test the exposure against THIS bounded capture, rather than the
            # tracking preview's 500 ms display-age limit after encoding ends.
            # The backend retains its focus/still deadlines and cancel checks.
            if (now < capture_started or frame.captured_at > now + .05
                    or frame.captured_at < capture_started - .05):
                raise FeatureError('stale_photo')
            stage = 'storage'
            result = self.store.save(request_id, frame, simulated=simulated, cancel_check=check, **options)
            reserved = False  # save owns final journal completion after commit.
            return result
        except BaseException as error:
            interrupted = not isinstance(error, Exception)
            retirement_failed = isinstance(error, FeatureError) and error.code == 'camera_preview_stop_failed'
            cancelled = not retirement_failed and (isinstance(error, _Cancelled)
                         or isinstance(error, FeatureError) and error.code == 'camera_cancelled'
                         or self._photo_cancel.is_set() or revision != self._revision or self._closed)
            code = ('photo_interrupted' if interrupted else 'photo_cancelled' if cancelled
                    else error.code if isinstance(error, FeatureError) else 'photo_storage_failed')
            status = 'unknown' if interrupted else 'cancelled' if cancelled else 'failed'
            evidence_stage = getattr(error, 'stage', stage)
            evidence_stage = evidence_stage if type(evidence_stage) is str and evidence_stage in PHOTO_STAGES else 'unknown'
            result = PhotoResult(request_id, status, code,
                                 simulated=simulated, executed=not simulated and capture_attempted,
                                 completion='unknown' if interrupted else 'capture_attempted' if capture_attempted and not simulated else 'not_started',
                                 stage='cancel' if cancelled else evidence_stage)
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
                wanted = self._wanted_locked() and revision == self._revision and not self._closed
                if wanted and not self.broker.opened:
                    # A failed real capture must not revive a closed sensor.
                    self._tracking = self._tracking_requested = self._preview = False
                    self._tracking_suspensions.clear()
                    self._consumers.clear()
                    self._frame = None
                    self.tracker.reset()
                    self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
            producer_alive = self._producer_thread is not None and self._producer_thread.is_alive()
            if not wanted and not producer_alive:
                if not self.broker.close():
                    self._error = 'camera_close_failed'
            self._photo_lock.release()
            if wanted and (resume_preview or focus_review):
                self._start_producer()

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
            self._tracking = self._tracking_requested = self._preview = False
            self._tracking_suspensions.clear()
            self._consumers.clear()
            self._frame = self._last_poll = None
            self.tracker.reset()
            self._emit(GazeTarget(self._generation, None, None, self.clock(), None, None, 'off'))
        retired = self._pause_producer() if self.asynchronous_preview else True
        if retired:
            if not self.broker.close():
                self._error = 'camera_close_failed'
        else:
            self._error = 'camera_preview_stop_failed'
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
            worker_alive = self._producer_thread is not None and self._producer_thread.is_alive()
            unknown = (worker_alive or self._producer_retirement_unknown or self.broker.cleanup_uncertain or self._error in
                       ('camera_close_failed', 'camera_detector_close_failed', 'camera_preview_stop_failed'))
            sensor_closed = not self.broker.opened
            cleaned = self._closed and sensor_closed and not self._photo_active and not unknown
            return {'resources_cleaned': cleaned, 'sensor_closed': sensor_closed,
                    'photo_active': self._photo_active, 'preview_worker_alive': worker_alive, 'close_unknown': unknown,
                    'code': 'resources_cleaned' if cleaned else 'camera_cleanup_unknown',
                    'physical_release_verified': False, 'scope': 'host_owned_camera_only'}
