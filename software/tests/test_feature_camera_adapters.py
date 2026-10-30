"""Opt-in adapter contracts using Python SDK doubles; real optional imports = 0."""
import builtins
from dataclasses import replace
from pathlib import Path
import struct
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.camera import (CameraFeature, DemoBackend, Frame, OpenCVFaceDetector,
                                  PhotoStore, Picamera2Backend, png_bytes, validate_png)
from bmo_ai.features.camera.broker import CameraBroker
from bmo_ai.features.contracts import FeatureError


class Clock:
    now = 10.0
    def __call__(self):
        return self.now


def jpeg_bytes(width=32, height=24):
    """A valid constant grayscale baseline JPEG, generated with stdlib only."""
    def segment(marker, body):
        return b'\xff' + bytes((marker,)) + struct.pack('!H', len(body) + 2) + body
    quant = segment(0xDB, b'\x00' + b'\x01' * 64)
    frame = segment(0xC0, struct.pack('!BHHB', 8, height, width, 1) + b'\x01\x11\x00')
    counts = b'\x01' + b'\x00' * 15
    huffman = segment(0xC4, b'\x00' + counts + b'\x00' + b'\x10' + counts + b'\x00')
    scan = segment(0xDA, b'\x01\x01\x00\x00\x3f\x00')
    bits = ((width + 7) // 8) * ((height + 7) // 8) * 2
    entropy = b'\x00' * (bits // 8)
    if bits % 8:
        entropy += bytes(((1 << (8 - bits % 8)) - 1,))
    return b'\xff\xd8' + quant + frame + huffman + scan + entropy + b'\xff\xd9'


class FakeCamera:
    """Only the public SDK signatures used by the adapter, never a device."""
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.camera_controls = {'AfMode': object()}
        self.mode = 'off'
        self.size = (32, 24)
        self.still_size = (64, 48)
        self.sensor = 1_000_000_000
        self.focused = True
        self.af_mode = 2
        self.af_state = None
        self.lens_position = 1.25
        self.metadata_override = None
        self.controls = []
        self.advance_clock = False
        self.fail = None
        self.pending = False
        self.on_wait = None

    def mark(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError('synthetic private SDK detail')

    def create_preview_configuration(self, **options):
        self.mark('preview_config');self.preview_options = options
        self.size = options['main']['size']
        return {'kind': 'preview'}

    def create_still_configuration(self, **options):
        self.mark('still_config');self.still_options = options
        return {'kind': 'still'}

    def configure(self, config):
        self.mark('configure');self.mode = config['kind']

    def start(self, *, show_preview):
        self.mark('start');assert show_preview is False

    def set_controls(self, controls):
        self.mark('set_controls')
        self.controls.append(dict(controls))
        if 'AfMode' in controls:
            self.af_mode = controls['AfMode']
        if 'LensPosition' in controls:
            self.lens_position = controls['LensPosition']

    def capture_file(self, output, *, format, wait):
        self.mark('capture');assert format in ('png', 'jpeg') and wait is False
        return ('preview' if format == 'jpeg' else 'frame', output)

    def switch_mode_and_capture_file(self, config, output, *, format, wait):
        self.mark('still');assert config['kind'] == 'still' and format == 'png' and wait is False
        self.mode = 'still'
        return ('photo', output)

    def autofocus_cycle(self, *, wait):
        self.mark('focus');assert wait is False
        return ('focus', None)

    def wait(self, job, *, timeout):
        self.calls.append(('wait', timeout))
        assert 0 < timeout <= .05
        if self.on_wait:
            callback, self.on_wait = self.on_wait, None
            callback()
        if self.pending:
            self.clock.now += timeout
            raise TimeoutError()
        self.mark('wait_result')
        kind, output = job
        if kind == 'focus':
            return self.focused
        size = self.size if kind in ('frame', 'preview') else self.still_size
        output.write(jpeg_bytes(*size) if kind == 'preview' else png_bytes(*size))
        self.sensor += 100_000_000
        if self.advance_clock:
            self.clock.now += .1
        if kind == 'photo':
            self.mode = 'preview'  # SDK switch-and-capture restores original mode.
        metadata = {'SensorTimestamp': self.sensor, 'AfMode': self.af_mode,
            'AfState': self.af_state if self.af_state is not None else 2 if self.focused else 3,
            'LensPosition': self.lens_position}
        if self.metadata_override is not None:
            metadata.update(self.metadata_override)
        return metadata

    def cancel_all_and_flush(self):
        self.mark('flush')

    def stop(self):
        self.mark('stop');self.mode = 'off'

    def close(self):
        self.mark('close')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.camera = FakeCamera(self.clock)
        self.camera.advance_clock = True
        self.factory = Mock(return_value=self.camera)
        self.backend = Picamera2Backend(enabled=True, camera_factory=self.factory,
            clock=self.clock, wait_clock=self.clock, size=(32, 24), read_timeout=.12,
            photo_timeout=.2, focus_timeout=.1)
        self.addCleanup(self.backend.close)
        original_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split('.')[0] in ('picamera2', 'libcamera', 'cv2', 'numpy', 'PIL'):
                raise AssertionError('Real optional import forbidden')
            return original_import(name, *args, **kwargs)
        self.enterContext(patch('builtins.__import__', side_effect=guarded))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch('subprocess.Popen', side_effect=AssertionError('Device process forbidden')))

    def assert_code(self, code, callback):
        with self.assertRaises(FeatureError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn('private', str(caught.exception))

    def test_disabled_and_constructed_adapters_never_call_optional_loaders(self):
        self.factory.assert_not_called()
        disabled = Picamera2Backend(camera_factory=self.factory)
        self.assert_code('camera_real_disabled', lambda: disabled.open(1))
        loader = Mock(side_effect=AssertionError('Loader forbidden'))
        detector = OpenCVFaceDetector(module_loader=loader)
        self.assert_code('detector_real_disabled', lambda: detector.detect(None))
        loader.assert_not_called();self.factory.assert_not_called()

    def test_preview_and_native_still_use_one_camera_and_restore_preview(self):
        self.backend.open(4)
        first = self.backend.read_frame()
        photo = self.backend.capture_photo()
        third = self.backend.read_frame()
        self.assertEqual((first.width, first.height), (32, 24))
        self.assertEqual((photo.width, photo.height), (64, 48))
        self.assertEqual((third.seq, third.stream_generation, self.camera.mode), (3, 4, 'preview'))
        self.assertAlmostEqual(third.captured_at - first.captured_at, .2)
        self.assertEqual(self.camera.still_options,
            {'main': {'format': 'RGB888'}, 'raw': None, 'buffer_count': 1, 'queue': False,
             'controls': {'AfMode': 0, 'LensPosition': 1.25}})
        self.assertEqual(self.camera.preview_options['main']['size'], (32, 24))
        self.factory.assert_called_once_with()

    def test_explicit_still_size_and_fixed_focus_skip_autofocus(self):
        self.backend.still_size = (80, 60)
        self.camera.camera_controls = {}
        self.backend.open(1)
        self.assert_code('photo_focus_uncertain', self.backend.capture_photo)
        photo = self.backend.capture_photo(allow_unconfirmed=True)
        self.assertFalse(photo.focus_ok)
        self.assertEqual(photo.focus_status, 'unsupported')
        self.assertEqual(self.camera.still_options['main']['size'], (80, 60))
        self.assertNotIn('focus', self.camera.calls)

    def test_each_open_failure_cleans_camera_and_releases_exclusive_lock(self):
        for stage in ('preview_config', 'configure', 'start'):
            with self.subTest(stage=stage):
                self.camera.fail = stage
                self.assert_code('camera_unavailable', lambda: self.backend.open(1))
                self.assertIn('close', self.camera.calls)
                self.camera.fail = None
                self.backend.open(2);self.backend.close()

    def test_constructor_failure_and_interrupt_release_exclusive_lock(self):
        for failure in (RuntimeError('private'), KeyboardInterrupt()):
            self.factory.side_effect = failure
            with self.assertRaises(FeatureError if isinstance(failure, Exception) else KeyboardInterrupt):
                self.backend.open(1)
            self.factory.side_effect = None
            self.backend.open(2);self.backend.close()

    def test_open_cancel_before_and_between_sdk_steps_cleans_up(self):
        self.assert_code('camera_cancelled', lambda: self.backend.open(1, cancel_check=lambda: True))
        self.factory.assert_not_called()
        calls = iter((False, False, True))
        self.assert_code('camera_cancelled', lambda: self.backend.open(2, cancel_check=lambda: next(calls)))
        self.assertIn('close', self.camera.calls)
        self.assertNotIn('start', self.camera.calls)
        self.backend.open(3)

    def test_second_owner_rejected_and_close_is_idempotent_even_on_stop_error(self):
        self.backend.open(1)
        other_factory = Mock(side_effect=AssertionError('Second device forbidden'))
        other = Picamera2Backend(enabled=True, camera_factory=other_factory)
        self.assert_code('camera_busy', lambda: other.open(2))
        other_factory.assert_not_called()
        self.camera.fail = 'stop';self.backend.close();self.backend.close()
        self.assertEqual(self.camera.calls.count('close'), 1)
        self.camera.fail = None
        self.backend.open(3)

    def test_read_timeout_flushes_closes_and_releases_lock_without_worker(self):
        self.backend.open(1);self.camera.pending = True
        before = self.clock()
        self.assert_code('camera_timeout', self.backend.read_frame)
        self.assertLessEqual(self.clock() - before, .120001)
        self.assertEqual(self.camera.calls[-3:], ['flush', 'stop', 'close'])
        self.assert_code('camera_off', self.backend.read_frame)
        self.camera.pending = False;self.backend.open(2)

    def test_late_sdk_success_and_photo_capture_timeout_are_not_reported_as_success(self):
        self.backend.open(1)
        self.camera.on_wait = lambda: setattr(self.clock, 'now', self.clock.now + 1)
        self.assert_code('camera_timeout', self.backend.read_frame)
        self.backend.open(2)
        self.camera.camera_controls = {}
        self.camera.pending = True
        self.assert_code('camera_timeout', self.backend.capture_photo)
        self.assertEqual(self.camera.calls[-3:], ['flush', 'stop', 'close'])

    def test_close_reports_sdk_failure_but_releases_lock_and_broker_owner(self):
        broker = CameraBroker(lambda: self.backend)
        broker.open();self.camera.fail = 'close'
        self.assertFalse(broker.close())
        self.assertFalse(broker.opened)
        self.camera.fail = None
        self.backend.open(2)

    def test_malformed_metadata_and_png_abort_without_returning_frame(self):
        original_wait = self.camera.wait
        for response in (None, {}, {'SensorTimestamp': True}):
            with self.subTest(response=response):
                self.backend.open(1)
                def malformed(*args, **kwargs):
                    original_wait(*args, **kwargs)
                    return response
                self.camera.wait = malformed
                self.assert_code('camera_capture_failed', self.backend.read_frame)
                self.camera.wait = original_wait
        self.backend.open(2)
        def invalid_png(job, **options):
            job[1].write(b'bad-png')
            return {'SensorTimestamp': 1}
        self.camera.wait = invalid_png
        self.assert_code('camera_capture_failed', self.backend.read_frame)

    def test_mid_read_cancel_rejects_result_and_closes_owned_camera(self):
        self.backend.open(1)
        state = {'cancelled': False}
        self.camera.on_wait = lambda: state.update(cancelled=True)
        self.assert_code('camera_cancelled', lambda: self.backend.read_frame(cancel_check=lambda: state['cancelled']))
        self.assertEqual(self.camera.calls[-3:], ['flush', 'stop', 'close'])

    def test_close_during_wait_rejects_late_result_and_never_reopens(self):
        self.backend.open(1);self.camera.on_wait = self.backend.close
        self.assert_code('camera_cancelled', self.backend.read_frame)
        self.factory.assert_called_once_with()
        self.assertEqual(self.camera.calls.count('close'), 1)

    def test_focus_uncertain_avoids_still_preserves_stream_but_sdk_timeout_closes(self):
        self.backend.open(1);self.camera.focused = False
        self.assert_code('photo_focus_uncertain', self.backend.capture_photo)
        self.assertNotIn('still', self.camera.calls)
        self.assertEqual(self.backend.focus_snapshot()['status'], 'unconfirmed')
        self.assertNotIn('close', self.camera.calls)
        self.clock.now += .6  # Force a new observation after cached evidence expires.
        self.camera.pending = True
        self.assert_code('camera_timeout', self.backend.capture_photo)
        self.assertEqual(self.camera.calls[-3:], ['flush', 'stop', 'close'])

    def test_capture_error_never_exposes_sdk_detail_and_broker_discards_owner(self):
        broker = CameraBroker(lambda: self.backend)
        self.addCleanup(broker.close)
        broker.open();self.camera.fail = 'capture'
        self.assert_code('camera_capture_failed', broker.read_frame)
        self.assertFalse(broker.opened)
        self.assertEqual(self.camera.calls.count('close'), 1)

    def test_broker_propagates_cancel_to_sdk_and_feature_cannot_restore_dead_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            feature = CameraFeature(backend_factory=lambda: self.backend, clock=self.clock,
                                   store=PhotoStore(Path(directory) / 'photos'))
            self.addCleanup(feature.close)
            feature.set_tracking(True, generation=1)
            state = {'cancelled': False}
            self.camera.on_wait = lambda: state.update(cancelled=True)
            result = feature.take_photo('cancelled-1', generation=1,
                                        cancel_check=lambda: state['cancelled'])
            self.assertEqual(result.status, 'cancelled')
            self.assertFalse(result.completed)
            self.assertFalse(feature.snapshot()['on'])
            self.assertFalse(feature.snapshot()['tracking'])
            feature.poll();self.factory.assert_called_once_with()

    def test_module3_full_resolution_synthetic_png_is_verified_and_saved(self):
        # Geometry contract only; no sensor, focus, quality or latency claim.
        self.camera.still_size = (4608, 2592)
        with tempfile.TemporaryDirectory() as directory:
            feature = CameraFeature(backend_factory=lambda: self.backend, clock=self.clock,
                                   store=PhotoStore(Path(directory) / 'photos'))
            self.addCleanup(feature.close)
            result = feature.take_photo('full-size-fixture', generation=1)
            self.assertTrue(result.completed)
            self.assertEqual(validate_png(feature.photo_bytes(result.photo_id)), (4608, 2592))

    def test_future_photo_timestamp_is_not_persisted_as_success(self):
        backend = DemoBackend(clock=self.clock,size=(16,16))
        capture = backend.capture_photo
        backend.capture_photo = lambda: replace(capture(),captured_at=self.clock()+1)
        with tempfile.TemporaryDirectory() as directory:
            feature = CameraFeature(backend_factory=lambda:backend,clock=self.clock,
                                   store=PhotoStore(Path(directory)/'photos'))
            self.addCleanup(feature.close)
            result = feature.take_photo('future-photo',generation=1)
            self.assertEqual((result.status,result.code),('failed','stale_photo'))
            self.assertEqual(list(feature.store.root.glob('*.png')),[])

    def test_invalid_timeout_and_size_fail_without_factory_or_import(self):
        for settings in ({'read_timeout': 0}, {'photo_timeout': float('nan')}, {'focus_timeout': True},
                         {'size': (8193, 1)}, {'still_size': (32, 0)}):
            with self.subTest(settings=settings), self.assertRaises(FeatureError):
                Picamera2Backend(enabled=True, camera_factory=self.factory, **settings)
        self.factory.assert_not_called()


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.frame = Frame(1, 1, 1.0, 32, 24, png_bytes(32, 24))
        self.classifier = Mock()
        self.classifier.empty.return_value = False
        self.classifier.detectMultiScale.return_value = ((8, 6, 16, 12),)
        self.cv = SimpleNamespace(CascadeClassifier=Mock(return_value=self.classifier),
            data=SimpleNamespace(haarcascades='/fake/'), IMREAD_GRAYSCALE=0,
            imdecode=Mock(return_value=SimpleNamespace(shape=(24, 32))))
        self.np = SimpleNamespace(uint8=object(), frombuffer=Mock(return_value=object()))
        self.loader = Mock(return_value=(self.cv, self.np))
        self.detector = OpenCVFaceDetector(enabled=True, module_loader=self.loader)
        self.addCleanup(self.detector.close)
        original_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split('.')[0] in ('picamera2', 'libcamera', 'cv2', 'numpy'):
                raise AssertionError('Real optional import forbidden')
            return original_import(name, *args, **kwargs)
        self.enterContext(patch('builtins.__import__', side_effect=guarded))

    def test_lazy_cached_local_boxes_and_close_reload(self):
        self.loader.assert_not_called()
        boxes = self.detector.detect(self.frame)
        self.assertEqual((boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height), (.25, .25, .5, .5))
        self.detector.detect(self.frame);self.loader.assert_called_once_with()
        self.classifier.detectMultiScale.assert_called_with(self.cv.imdecode.return_value,
            scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        self.detector.close();self.detector.detect(self.frame)
        self.assertEqual(self.loader.call_count, 2)

    def test_empty_classifier_bad_decode_geometry_and_box_fail_closed(self):
        cases = ('empty', 'none', 'shape', 'box', 'failure')
        for case in cases:
            with self.subTest(case=case):
                self.detector.close();self.classifier.empty.return_value = case == 'empty'
                self.cv.imdecode.return_value = None if case == 'none' else SimpleNamespace(shape=(99, 32) if case == 'shape' else (24, 32))
                self.classifier.detectMultiScale.return_value = ((31, 0, 10, 5),) if case == 'box' else ()
                self.classifier.detectMultiScale.side_effect = RuntimeError('private path') if case == 'failure' else None
                with self.assertRaises(FeatureError) as caught:
                    self.detector.detect(self.frame)
                self.assertEqual(caught.exception.code, 'face_detector_failed')
                self.assertNotIn('private', str(caught.exception))

    def test_invalid_png_rejected_before_loading_detector(self):
        bad = Frame(1, 1, 1.0, 32, 24, b'not-png')
        with self.assertRaises(FeatureError):
            self.detector.detect(bad)
        self.loader.assert_not_called()

    def test_jpeg_preview_uses_existing_local_decoder_and_same_normalized_boxes(self):
        from bmo_ai.features.camera.models import PreviewFrame
        preview = PreviewFrame(1, 1, 1., 32, 24, jpeg_bytes(32, 24))
        boxes = self.detector.detect(preview)
        self.assertEqual((boxes[0].x, boxes[0].y), (.25, .25))
        self.cv.imdecode.assert_called_once()


if __name__ == '__main__':
    unittest.main()
