"""Synthetic defaults and explicit opt-in adapters; constructors open nothing."""
from io import BytesIO
from threading import Lock
import time

from ..contracts import FeatureError
from .codec import png_bytes, validate_png
from .models import FaceBox, Frame, finite


def _picamera_factory():
    from picamera2 import Picamera2
    return Picamera2()


def _opencv_modules():
    import cv2
    import numpy
    return cv2, numpy


def _check_cancel(callback):
    if callback is not None:
        try:
            cancelled = callback() is True
        except Exception:
            cancelled = True
        if cancelled:
            raise FeatureError('camera_cancelled')


class DemoBackend:
    simulated = True

    def __init__(self, *, clock=time.monotonic, size=(320, 240)):
        self.clock, self.size = clock, size
        self.open_count = self.close_count = self.read_count = self.photo_count = 0
        self._generation = self._seq = 0
        self._opened = False
        self._payload = None

    def open(self, stream_generation):
        if self._opened:
            raise FeatureError('camera_already_open')
        self._generation, self._seq = stream_generation, 0
        self._payload = png_bytes(*self.size)
        self._opened = True
        self.open_count += 1

    def _frame(self):
        if not self._opened:
            raise FeatureError('camera_off')
        self._seq += 1
        return Frame(self._generation, self._seq, self.clock(), *self.size, self._payload)

    def read_frame(self):
        self.read_count += 1
        return self._frame()

    def capture_photo(self):
        self.photo_count += 1
        return self._frame()

    def close(self):
        if self._opened:
            self.close_count += 1
        self._opened, self._payload = False, None


class FixtureDetector:
    """Explicit synthetic positions. They are never real face detections."""
    def __init__(self, boxes=None):
        self.boxes = tuple(boxes) if boxes is not None else (FaceBox(.35, .25, .25, .35),)
        self.calls = self.close_count = 0

    def detect(self, frame):
        self.calls += 1
        return tuple(self.boxes)

    def close(self):
        self.close_count += 1


class NullDetector:
    def detect(self, frame):
        return ()

    def close(self):
        pass


class Picamera2Backend:
    """UNVERIFIED live adapter, disabled unless an explicit human opt-in is set.

    Pi/Debian camera availability, focus, preview and latency require supervised
    validation. No Picamera2 import or sensor constructor occurs before open().
    """
    simulated = False
    cooperative_cancel = True
    _device_lock = Lock()

    def __init__(self, *, enabled=False, clock=time.monotonic, size=(320, 240),
                 still_size=None, camera_factory=None, read_timeout=1.0,
                 photo_timeout=4.0, focus_timeout=2.0, wait_clock=time.monotonic):
        for dimensions in (size, still_size):
            if dimensions is not None and (type(dimensions) is not tuple or len(dimensions) != 2
                    or any(type(n) is not int or not 1 <= n <= 8192 for n in dimensions)):
                raise FeatureError('invalid_camera_size')
        if any(not finite(value) or not .05 <= value <= 30
               for value in (read_timeout, photo_timeout, focus_timeout)):
            raise FeatureError('invalid_camera_timeout')
        self.enabled, self.clock, self.size = enabled, clock, size
        self.still_size = still_size
        self._factory = camera_factory or _picamera_factory
        self._wait_clock = wait_clock
        self.read_timeout, self.photo_timeout, self.focus_timeout = read_timeout, photo_timeout, focus_timeout
        self._camera = None
        self._owns_lock = False
        self._generation = self._seq = 0
        self._sensor_origin = self._clock_origin = None

    def open(self, stream_generation, *, cancel_check=None):
        if self.enabled is not True:
            raise FeatureError('camera_real_disabled')
        _check_cancel(cancel_check)
        if self._camera is not None or not self._device_lock.acquire(blocking=False):
            raise FeatureError('camera_busy')
        self._owns_lock = True
        try:
            self._camera = self._factory()
            _check_cancel(cancel_check)
            config = self._camera.create_preview_configuration(
                main={'size': self.size, 'format': 'RGB888'}, buffer_count=2, queue=False)
            _check_cancel(cancel_check)
            self._camera.configure(config)
            _check_cancel(cancel_check)
            self._camera.start(show_preview=False)
            _check_cancel(cancel_check)
            self._generation, self._seq = stream_generation, 0
            self._sensor_origin = self._clock_origin = None
        except BaseException as error:
            self.close()
            if not isinstance(error, Exception):
                raise
            if isinstance(error, FeatureError) and error.code == 'camera_cancelled':
                raise
            raise FeatureError('camera_unavailable') from None

    def _wait(self, camera, job, timeout, cancel_check):
        deadline = self._wait_clock() + timeout
        while True:
            _check_cancel(cancel_check)
            if camera is not self._camera:
                raise FeatureError('camera_cancelled')
            remaining = deadline - self._wait_clock()
            if remaining <= 0:
                raise FeatureError('camera_timeout')
            try:
                result = camera.wait(job, timeout=min(.05, remaining))
            except TimeoutError:
                continue
            _check_cancel(cancel_check)
            if camera is not self._camera:
                raise FeatureError('camera_cancelled')
            if self._wait_clock() > deadline:
                raise FeatureError('camera_timeout')
            return result

    def _abort(self, camera):
        if camera is self._camera:
            try:
                camera.cancel_all_and_flush()
            except Exception:
                pass
            self.close()

    def _capture(self, focus_ok=True, *, still=False, cancel_check=None):
        camera = self._camera
        if camera is None:
            raise FeatureError('camera_off')
        try:
            _check_cancel(cancel_check)
            output = BytesIO()
            if still:
                main = {'format': 'RGB888'}
                if self.still_size is not None:
                    main['size'] = self.still_size
                config = camera.create_still_configuration(main=main, raw=None, buffer_count=1, queue=False)
                _check_cancel(cancel_check)
                job = camera.switch_mode_and_capture_file(config, output, format='png', wait=False)
            else:
                job = camera.capture_file(output, format='png', wait=False)
            metadata = self._wait(camera, job, self.photo_timeout if still else self.read_timeout, cancel_check)
            payload = output.getvalue()
            width, height = validate_png(payload)
            sensor = metadata.get('SensorTimestamp')
            if type(sensor) is not int or sensor <= 0:
                raise FeatureError('camera_timestamp_unavailable')
            # Establish one relative mapping per stream, rather than assuming
            # Python and libcamera clocks have the same monotonic origin.
            if self._sensor_origin is None:
                self._sensor_origin, self._clock_origin = sensor, self.clock()
            stamp = self._clock_origin + (sensor - self._sensor_origin) / 1_000_000_000
            self._seq += 1
            return Frame(self._generation, self._seq, stamp, width, height, payload, focus_ok=focus_ok)
        except BaseException as error:
            # SDK cancellation leaves its stream indeterminate; do not restore
            # tracking with a timed-out or partially switched sensor instance.
            self._abort(camera)
            if not isinstance(error, Exception):
                raise
            if isinstance(error, FeatureError) and error.code in ('camera_timeout', 'camera_cancelled'):
                raise
            raise FeatureError('camera_capture_failed') from None

    def read_frame(self, *, cancel_check=None):
        return self._capture(cancel_check=cancel_check)

    def capture_photo(self, *, cancel_check=None):
        camera = self._camera
        if camera is None:
            raise FeatureError('camera_off')
        try:
            _check_cancel(cancel_check)
            focused = self._wait(camera, camera.autofocus_cycle(wait=False), self.focus_timeout,
                                 cancel_check) if 'AfMode' in camera.camera_controls else True
        except BaseException as error:
            self._abort(camera)
            if not isinstance(error, Exception):
                raise
            if isinstance(error, FeatureError) and error.code in ('camera_timeout', 'camera_cancelled'):
                raise
            raise FeatureError('camera_focus_failed') from None
        if focused is not True:
            raise FeatureError('photo_focus_failed')
        return self._capture(focus_ok=True, still=True, cancel_check=cancel_check)

    def close(self):
        camera, self._camera = self._camera, None
        success = True
        try:
            if camera is not None:
                try:
                    camera.stop()
                finally:
                    camera.close()
        except Exception:
            success = False
        finally:
            if self._owns_lock:
                self._owns_lock = False
                self._device_lock.release()
        return success


class OpenCVFaceDetector:
    """Optional local Haar detector, with no recognition, cloud or stored frames."""
    def __init__(self, *, enabled=False, module_loader=None):
        self.enabled, self._classifier = enabled, None
        self._loader = module_loader or _opencv_modules
        self._modules = None

    def detect(self, frame):
        if self.enabled is not True:
            raise FeatureError('detector_real_disabled')
        try:
            if not isinstance(frame, Frame) or validate_png(frame.payload) != (frame.width, frame.height):
                raise ValueError()
            if self._modules is None:
                self._modules = self._loader()
            cv2, np = self._modules
            if self._classifier is None:
                classifier = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
                if classifier.empty():
                    raise ValueError()
                self._classifier = classifier
            gray = cv2.imdecode(np.frombuffer(frame.payload, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None or tuple(gray.shape[:2]) != (frame.height, frame.width):
                raise ValueError()
            rectangles = self._classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
            return tuple(FaceBox(float(x) / frame.width, float(y) / frame.height, float(w) / frame.width, float(h) / frame.height)
                         for x, y, w, h in rectangles)
        except Exception:
            raise FeatureError('face_detector_failed') from None

    def close(self):
        self._classifier = None
        self._modules = None
