"""Synthetic defaults and explicit opt-in adapters; constructors open nothing."""
from io import BytesIO
from threading import Lock
import time

from ..contracts import FeatureError
from .codec import png_bytes, validate_png
from .models import FaceBox, Frame


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
    _device_lock = Lock()

    def __init__(self, *, enabled=False, clock=time.monotonic, size=(320, 240)):
        self.enabled, self.clock, self.size = enabled, clock, size
        self._camera = None
        self._owns_lock = False
        self._generation = self._seq = 0
        self._sensor_origin = self._clock_origin = None

    def open(self, stream_generation):
        if self.enabled is not True:
            raise FeatureError('camera_real_disabled')
        if self._camera is not None or not self._device_lock.acquire(blocking=False):
            raise FeatureError('camera_busy')
        self._owns_lock = True
        try:
            from picamera2 import Picamera2
            self._camera = Picamera2()
            config = self._camera.create_preview_configuration(
                main={'size': self.size, 'format': 'RGB888'}, buffer_count=2, queue=False)
            self._camera.configure(config)
            self._camera.start(show_preview=False)
            self._generation, self._seq = stream_generation, 0
            self._sensor_origin = self._clock_origin = None
        except Exception:
            self.close()
            raise FeatureError('camera_unavailable') from None

    def _capture(self, focus_ok=True):
        if self._camera is None:
            raise FeatureError('camera_off')
        try:
            output = BytesIO()
            metadata = self._camera.capture_file(output, format='png', wait=1.0)
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
        except Exception:
            raise FeatureError('camera_capture_failed') from None

    def read_frame(self):
        return self._capture()

    def capture_photo(self):
        if self._camera is None:
            raise FeatureError('camera_off')
        try:
            focused = self._camera.autofocus_cycle(wait=2.0) if 'AfMode' in self._camera.camera_controls else True
        except Exception:
            raise FeatureError('camera_focus_failed') from None
        return self._capture(focus_ok=focused is True)

    def close(self):
        camera, self._camera = self._camera, None
        try:
            if camera is not None:
                try:
                    camera.stop()
                finally:
                    camera.close()
        except Exception:
            pass
        finally:
            if self._owns_lock:
                self._owns_lock = False
                self._device_lock.release()


class OpenCVFaceDetector:
    """Optional local Haar detector, with no recognition, cloud or stored frames."""
    def __init__(self, *, enabled=False):
        self.enabled, self._classifier = enabled, None

    def detect(self, frame):
        if self.enabled is not True:
            raise FeatureError('detector_real_disabled')
        try:
            import cv2
            import numpy as np
            if self._classifier is None:
                self._classifier = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
                if self._classifier.empty():
                    raise ValueError()
            gray = cv2.imdecode(np.frombuffer(frame.payload, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            rectangles = self._classifier.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
            return tuple(FaceBox(float(x) / frame.width, float(y) / frame.height, float(w) / frame.width, float(h) / frame.height)
                         for x, y, w, h in rectangles)
        except Exception:
            raise FeatureError('face_detector_failed') from None

    def close(self):
        self._classifier = None
