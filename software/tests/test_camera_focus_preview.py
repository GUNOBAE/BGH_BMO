"""Current-stream AF/JPEG contracts with SDK doubles; no devices/imports/API."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from bmo_ai.features.camera.backends import Picamera2Backend
from bmo_ai.features.camera.codec import validate_png
from bmo_ai.features.camera.jpeg_codec import validate_jpeg
from bmo_ai.features.camera.models import PreviewFrame
from bmo_ai.features.contracts import FeatureError
from test_feature_camera_adapters import Clock, FakeCamera, jpeg_bytes


class CameraFocusPreviewTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('os.open', side_effect=AssertionError('device forbidden')))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('API forbidden')))
        self.clock = Clock()
        self.sdk = FakeCamera(self.clock)
        self.sdk.advance_clock = True
        self.sdk.autofocus_cycle = lambda **_kw: self.fail('new shutter AF cycle forbidden')
        self.backend = Picamera2Backend(enabled=True, clock=self.clock,
            wait_clock=self.clock, size=(640, 360), still_size=(1280, 720),
            camera_factory=lambda: self.sdk, focus_timeout=.2)
        self.addCleanup(self.backend.close)

    def assert_code(self, code, callback):
        with self.assertRaises(FeatureError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_continuous_af_observes_every_jpeg_and_shutter_reuses_current_lens(self):
        self.backend.open(8)
        self.assertEqual(self.sdk.controls, [{'AfMode': 2}])
        self.assertEqual(self.sdk.preview_options['controls'], {'FrameRate': 20})
        first = self.backend.read_preview()
        self.assertIsInstance(first, PreviewFrame)
        self.assertEqual((first.format, first.width, first.height), ('jpeg', 640, 360))
        self.assertEqual(validate_jpeg(first.payload), (640, 360))
        self.assertEqual(self.backend.focus_snapshot()['status'], 'focused')
        self.assertTrue(self.backend.focus_snapshot()['can_take_anyway'])
        self.sdk.af_state = 1
        self.backend.read_preview()
        self.assertEqual(self.backend.focus_snapshot()['status'], 'scanning')
        self.sdk.af_state, self.sdk.lens_position = 2, .75
        current = self.backend.read_preview()
        captures = self.sdk.calls.count('capture')
        self.sdk.af_state = 0  # Cached proof stays stable during manual freeze.
        photo = self.backend.capture_photo()
        self.assertEqual(self.sdk.calls.count('capture'), captures)
        self.assertTrue(photo.focus_ok)
        self.assertEqual(photo.focus_status, 'focused')
        self.assertEqual(photo.stream_generation, current.stream_generation)
        self.assertEqual(validate_png(photo.payload), self.sdk.still_size)
        self.assertEqual(self.sdk.still_options['main']['size'], (1280, 720))
        self.assertEqual(self.sdk.still_options['controls'], {'AfMode': 0, 'LensPosition': .75})
        self.assertEqual(self.backend.focus_snapshot()['lens_position'], .75)
        self.assertEqual(self.sdk.calls.count('still'), 1)

    def test_failed_focus_stays_live_and_explicit_override_is_actual_unconfirmed_still(self):
        self.backend.open(1)
        self.sdk.focused = False
        preview = self.backend.read_preview()
        self.assert_code('photo_focus_uncertain', self.backend.capture_photo)
        self.assertEqual(self.sdk.calls.count('still'), 0)
        self.assertNotIn('close', self.sdk.calls)
        self.assertEqual(self.backend.focus_snapshot()['code'], 'focus_failed')
        photo = self.backend.capture_photo(allow_unconfirmed=True)
        self.assertFalse(photo.focus_ok)
        self.assertEqual(photo.focus_status, 'unconfirmed')
        self.assertNotEqual(photo.payload, preview.payload)
        self.assertEqual(validate_png(photo.payload), self.sdk.still_size)
        self.assertEqual(self.sdk.calls.count('still'), 1)

    def test_refocus_is_one_explicit_nonblocking_trigger_then_metadata_completion(self):
        self.backend.open(3)
        self.backend.read_preview()
        before = self.sdk.calls.count('capture')
        state = self.backend.refocus()
        self.assertEqual((state['status'], state['code']), ('scanning', 'focus_requested'))
        self.assertEqual(self.sdk.controls[-1], {'AfMode': 1, 'AfTrigger': 0})
        self.assertEqual(self.sdk.calls.count('capture'), before)
        self.backend.read_preview()  # Old Focused metadata is not this new scan.
        self.assertNotEqual(self.backend.focus_snapshot()['status'], 'focused')
        self.sdk.af_state = 1
        self.backend.read_preview()
        self.clock.now += .25
        self.backend.read_preview()
        self.assertEqual(self.backend.focus_snapshot()['code'], 'focus_deadline')
        self.assert_code('photo_focus_uncertain', self.backend.capture_photo)
        self.assertEqual(sum('AfTrigger' in controls for controls in self.sdk.controls), 1)
        self.sdk.af_state = 2
        self.backend.read_preview()
        state = self.backend.focus_snapshot()
        self.assertEqual(state['status'], 'focused')
        self.assertIsNotNone(state['af_finished_at'])
        self.assertGreaterEqual(state['elapsed'], .25)
        self.backend.read_preview()
        self.assertEqual(self.backend.focus_snapshot()['elapsed'], state['elapsed'])

    def test_metadata_types_cannot_be_truthy_focus_and_missing_lens_blocks_override(self):
        self.backend.open(2)
        for value in (True, '2', 2.0, None):
            self.sdk.metadata_override = {'AfState': value}
            self.backend.read_preview()
            self.assertEqual(self.backend.focus_snapshot()['status'], 'unconfirmed')
            self.assert_code('photo_focus_uncertain', self.backend.capture_photo)
        self.sdk.metadata_override = {'LensPosition': float('nan')}
        self.backend.read_preview()
        self.assertIsNone(self.backend.focus_snapshot()['lens_position'])
        self.assertFalse(self.backend.focus_snapshot()['can_take_anyway'])
        self.assert_code('photo_focus_uncertain', lambda: self.backend.capture_photo(allow_unconfirmed=True))
        self.assertNotIn('still', self.sdk.calls)

    def test_af_mode_control_need_not_be_repeated_in_frame_metadata(self):
        original = self.sdk.wait
        def without_mode(*args, **kwargs):
            metadata = original(*args, **kwargs)
            metadata.pop('AfMode', None)
            return metadata
        self.sdk.wait = without_mode
        self.backend.open(4)
        self.backend.read_preview()
        state = self.backend.focus_snapshot()
        self.assertEqual((state['status'], state['code']), ('focused', 'focus_observed'))
        self.assertIsNone(state['af_mode'])  # No invented measurement of the control.
        self.assertEqual((state['af_state'], state['lens_position']), ('focused', 1.25))
        self.assertTrue(state['can_take_anyway'])
        photo = self.backend.capture_photo()
        self.assertTrue(photo.focus_ok)
        self.assertEqual(self.sdk.still_options['controls'], {'AfMode': 0, 'LensPosition': 1.25})

    def test_refocus_without_af_mode_metadata_still_needs_observed_scan_then_completion(self):
        original = self.sdk.wait
        def without_mode(*args, **kwargs):
            metadata = original(*args, **kwargs)
            metadata.pop('AfMode', None)
            return metadata
        self.sdk.wait = without_mode
        self.backend.open(5)
        self.backend.read_preview()
        self.backend.refocus()
        self.backend.read_preview()
        self.assertNotEqual(self.backend.focus_snapshot()['status'], 'focused')
        self.sdk.af_state = 1
        self.backend.read_preview()
        self.assertEqual(self.backend.focus_snapshot()['status'], 'scanning')
        self.sdk.af_state = 2
        self.backend.read_preview()
        state = self.backend.focus_snapshot()
        self.assertEqual(state['status'], 'focused')
        self.assertIsNone(state['af_mode'])
        self.assertIsNotNone(state['af_finished_at'])

    def test_no_af_controls_is_unsupported_not_automatically_focused(self):
        self.sdk.camera_controls = {}
        self.backend.open(1)
        self.backend.read_preview()
        self.assertEqual(self.backend.focus_snapshot()['status'], 'unsupported')
        self.assertTrue(self.backend.focus_snapshot()['can_take_anyway'])
        self.assert_code('photo_focus_uncertain', self.backend.capture_photo)
        self.assertEqual(self.backend.refocus()['status'], 'unsupported')
        photo = self.backend.capture_photo(allow_unconfirmed=True)
        self.assertFalse(photo.focus_ok)
        self.assertEqual(photo.focus_status, 'unsupported')
        self.assertEqual(self.sdk.controls, [])

    def test_refocus_sdk_fault_and_preview_timeout_abort_and_do_not_capture_still(self):
        self.backend.open(1)
        self.sdk.fail = 'set_controls'
        self.assert_code('camera_focus_failed', self.backend.refocus)
        self.assertEqual(self.backend.focus_snapshot()['status'], 'device_fault')
        self.assertFalse(self.backend.focus_snapshot()['can_take_anyway'])
        self.assertNotIn('still', self.sdk.calls)
        self.sdk.fail = None
        self.backend.open(2)
        self.sdk.pending = True
        self.assert_code('camera_timeout', self.backend.read_preview)
        self.assertEqual(self.backend.focus_snapshot()['code'], 'camera_timeout')
        self.assertEqual(self.sdk.calls[-3:], ['flush', 'stop', 'close'])

    def test_old_stream_focus_is_not_reused_and_preview_resumes_only_when_requested(self):
        self.backend.open(1)
        self.backend.read_preview()
        self.backend.capture_photo()
        self.assertEqual(self.sdk.controls[-1]['AfMode'], 0)
        self.backend.read_preview()
        self.assertEqual(self.sdk.controls[-1], {'AfMode': 2})
        self.backend.close()
        self.backend.open(2)
        self.assertEqual(self.backend.focus_snapshot()['code'], 'focus_not_observed')
        self.assertFalse(self.backend.focus_snapshot()['can_take_anyway'])
        self.assertEqual(self.backend.focus_snapshot()['stream_generation'], 2)


class PreviewJpegContractTests(unittest.TestCase):
    def test_structural_geometry_validation_is_bounded_without_decoding_png(self):
        payload = jpeg_bytes(640, 360)
        self.assertEqual(validate_jpeg(payload), (640, 360))
        for raw in (b'', payload[:-1], payload + b'junk', payload[:20], b'\xff\xd8' + b'x' * (2 * 1024 * 1024)):
            with self.subTest(size=len(raw)), self.assertRaises(FeatureError):
                validate_jpeg(raw)
        frame = PreviewFrame(1, 2, 3., 640, 360, payload, sensor_timestamp=1)
        for changes in ({'format': 'gif'}, {'sensor_timestamp': True}, {'payload': b'x' * (2 * 1024 * 1024 + 1)}):
            with self.assertRaises(FeatureError):
                replace(frame, **changes)


if __name__ == '__main__':
    unittest.main()
