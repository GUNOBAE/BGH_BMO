"""Bounded preview/tracking/photo ownership with encoded in-memory SDK doubles.

These checks measure injected scheduling and bookkeeping, never physical FPS,
focus sharpness, sensor latency or Raspberry Pi display performance.
"""
from pathlib import Path
from threading import Event, Lock, Thread
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.camera import CameraFeature, FixtureDetector, Frame, PhotoStore, png_bytes
from bmo_ai.features.camera.models import PreviewFrame
from bmo_ai.features.contracts import FeatureError
from test_feature_camera_adapters import jpeg_bytes


class EncodedBackend:
    simulated = True
    cooperative_cancel = True

    def __init__(self):
        self.open_count = self.close_count = self.read_count = self.photo_count = self.refocus_count = 0
        self.seq = self.epoch = 0
        self.status = 'focused'
        self.payload = jpeg_bytes()
        self.read_gate = None
        self.read_entered = Event()
        self.read_active = False
        self.cancel_seen = False
        self.fail = False
        self._lock = Lock()

    def open(self, epoch, *, cancel_check=None):
        self.epoch = epoch
        self.open_count += 1

    def read_preview(self, *, cancel_check=None):
        self.read_count += 1
        self.read_active = True
        self.read_entered.set()
        try:
            while self.read_gate is not None and not self.read_gate.wait(.005):
                if cancel_check is not None:
                    try:
                        cancel_check()
                    except Exception:
                        self.cancel_seen = True
                        raise
            if self.fail:
                raise FeatureError('camera_capture_failed')
            if cancel_check is not None:
                cancel_check()
            self.seq += 1
            return PreviewFrame(self.epoch, self.seq, time.monotonic(), 32, 24,
                                self.payload, sensor_timestamp=self.seq * 50_000_000)
        finally:
            self.read_active = False

    def read_frame(self, **options):
        raise AssertionError('The asynchronous preview must not encode PNGs')

    def focus_snapshot(self):
        return dict(status=self.status, code='focus_observed' if self.status == 'focused' else 'focus_failed')

    def refocus(self, *, cancel_check=None):
        self.refocus_count += 1
        self.status = 'scanning'
        return self.focus_snapshot()

    def capture_photo(self, *, cancel_check=None, allow_unconfirmed=False):
        if self.read_active:
            raise AssertionError('A preview read overlaps still capture')
        if cancel_check is not None:
            cancel_check()
        self.photo_count += 1
        self.seq += 1
        return Frame(self.epoch, self.seq, time.monotonic(), 64, 48, png_bytes(64, 48))

    def close(self):
        self.close_count += 1
        return True


class CameraPreviewPipelineTests(unittest.TestCase):
    def make(self, **options):
        backend = EncodedBackend()
        directory = self.enterContext(tempfile.TemporaryDirectory())
        factory = Mock(return_value=backend)
        detector = FixtureDetector()
        feature = CameraFeature(backend_factory=factory, detector=detector,
            store=PhotoStore(Path(directory) / 'photos'), asynchronous_preview=True,
            preview_hz=20, max_hz=4, **options)
        self.addCleanup(feature.close)
        return feature, backend, factory, detector

    def wait_for(self, predicate, timeout=1.5):
        end = time.monotonic() + timeout
        tick = Event()
        while not predicate():
            if time.monotonic() >= end:
                self.fail('Injected camera pipeline did not reach the expected state')
            tick.wait(.005)

    def test_lazy_and_invalid_configuration_never_opens_or_starts_a_worker(self):
        feature, backend, factory, _ = self.make()
        self.assertIsNone(feature.latest_frame())
        self.assertFalse(feature.snapshot()['preview_pipeline']['worker_alive'])
        factory.assert_not_called()
        for options in ({'asynchronous_preview': 1}, {'preview_hz': 0},
                        {'preview_hz': 31}, {'preview_hz': float('nan')}, {'max_hz': 11}):
            with self.subTest(options=options), self.assertRaises(FeatureError):
                CameraFeature(**options)
        self.assertEqual(backend.open_count, 0)

    def test_jpeg_producer_runs_without_host_poll_and_tracking_samples_its_cache(self):
        feature, backend, _, detector = self.make()
        with patch('bmo_ai.features.camera.feature.validate_png', side_effect=AssertionError('PNG preview forbidden')):
            feature.set_preview(True, generation=1)
            self.wait_for(lambda: feature.snapshot()['preview_pipeline']['published'] >= 5)
            feature.set_tracking(True, generation=1)
            self.wait_for(lambda: feature.latest_frame() is not None)
            state = feature.poll()
            for _ in range(20):
                feature.poll()
            self.assertEqual(detector.calls, 1)
            self.assertEqual(state['target']['status'], 'tracking')
        state = feature.snapshot()
        self.assertEqual(state['preview_mime'], 'image/jpeg')
        self.assertEqual(state['preview_pipeline']['capacity'], 1)
        self.assertEqual(state['preview_pipeline']['target_hz'], 20)
        self.assertEqual(state['preview_pipeline']['physical_fps'], 'NOT_MEASURED')
        self.assertGreater(backend.read_count, detector.calls)
        self.assertGreater(state['preview_pipeline']['dropped_replaced'], 0)
        self.assertEqual(backend.refocus_count, 0)

    def test_still_pauses_one_worker_cached_transport_never_reads_sdk_and_resume_is_explicit(self):
        feature, backend, factory, _ = self.make()
        feature.set_preview(True, generation=1)
        self.wait_for(lambda: feature.latest_frame() is not None)
        result = feature.take_photo('one-still', generation=1, resume_preview=False)
        self.assertTrue(result.completed)
        self.assertEqual((backend.open_count, backend.photo_count, backend.close_count), (1, 1, 0))
        self.assertFalse(feature.snapshot()['preview_pipeline']['worker_alive'])
        before = backend.read_count
        frame = feature.latest_frame()
        self.assertTrue(frame['id'].startswith('1:'))
        self.assertEqual(frame['mime'], 'image/jpeg')
        self.assertGreaterEqual(frame['age_ms'], 0)
        for _ in range(50):
            self.assertIsNone(feature.latest_frame(after=frame['id']))
        self.assertEqual(backend.read_count, before)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(feature.take_photo('one-still', generation=1, resume_preview=False), result)
        self.assertEqual(backend.photo_count, 1)
        feature.set_preview(True, generation=1)
        self.wait_for(lambda: backend.read_count > before)
        self.assertEqual(backend.open_count, 1)

    def test_pause_finishes_inflight_read_without_sdk_cancel_or_camera_close(self):
        feature, backend, _, _ = self.make()
        backend.read_gate = Event()
        feature.set_preview(True, generation=1)
        self.assertTrue(backend.read_entered.wait(1))
        results = []
        worker = Thread(target=lambda: results.append(feature.take_photo('paused-read', generation=1, resume_preview=False)))
        worker.start()
        try:
            self.wait_for(lambda: feature.snapshot()['photo_active'])
            self.assertEqual(backend.photo_count, 0)
            backend.read_gate.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(results[0].completed)
            self.assertFalse(backend.cancel_seen)
            self.assertEqual(backend.close_count, 0)
            self.assertFalse(feature.snapshot()['preview_pipeline']['worker_alive'])
        finally:
            backend.read_gate.set()
            worker.join(2)

    def test_uncertain_focus_does_not_reserve_journal_and_explicit_override_is_honest(self):
        feature, backend, _, _ = self.make()
        backend.status = 'unconfirmed'
        feature.set_preview(True, generation=1)
        self.wait_for(lambda: feature.latest_frame() is not None)
        begin = feature.store.begin = Mock(wraps=feature.store.begin)
        blocked = feature.take_photo('current-choice', generation=1, resume_preview=False)
        self.assertEqual((blocked.code, blocked.stage), ('photo_focus_uncertain', 'focus'))
        self.assertFalse(blocked.completed)
        begin.assert_not_called()
        self.assertEqual(backend.photo_count, 0)
        self.wait_for(lambda: feature.snapshot()['preview_pipeline']['worker_alive'])
        self.assertEqual(backend.refocus_count, 0)
        feature.refocus()
        self.assertEqual(backend.refocus_count, 1)
        result = feature.take_photo('current-choice', generation=1, allow_unconfirmed=True, resume_preview=False)
        self.assertTrue(result.completed)
        self.assertEqual(result.focus_status, 'unconfirmed')
        self.assertEqual(backend.photo_count, 1)
        self.assertEqual(feature.take_photo('current-choice', generation=1, allow_unconfirmed=True, resume_preview=False), result)
        self.assertEqual(backend.photo_count, 1)

    def test_off_cancels_revision_read_retires_worker_and_closes_single_owner(self):
        feature, backend, _, _ = self.make()
        backend.read_gate = Event()
        feature.set_preview(True, generation=1)
        self.assertTrue(backend.read_entered.wait(1))
        feature.off()
        self.assertTrue(backend.cancel_seen)
        self.assertFalse(feature.snapshot()['on'])
        self.assertFalse(feature.snapshot()['preview_pipeline']['worker_alive'])
        self.assertIsNone(feature.latest_frame())
        self.assertEqual(backend.close_count, 1)
        feature.close()
        self.assertTrue(feature.cleanup_snapshot()['resources_cleaned'])

    def test_preview_failure_fails_closed_without_poll_reopening_sensor(self):
        feature, backend, factory, _ = self.make()
        backend.fail = True
        feature.set_preview(True, generation=1)
        self.wait_for(lambda: not feature.snapshot()['preview_pipeline']['worker_alive'])
        self.assertEqual(feature.snapshot()['error'], 'camera_capture_failed')
        self.assertFalse(feature.snapshot()['on'])
        for _ in range(10):
            feature.poll()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(backend.close_count, 1)
        self.assertFalse(feature.snapshot()['preview'])

    def test_nonretiring_read_fails_closed_without_capture_or_second_worker(self):
        feature, backend, factory, _ = self.make()
        entered, release = Event(), Event()
        original = backend.read_preview
        def uncooperative(*, cancel_check=None):
            entered.set()
            release.wait(1)
            return original(cancel_check=cancel_check)
        backend.read_preview = uncooperative
        feature._producer_join_timeout = .03
        feature.set_preview(True, generation=1)
        self.assertTrue(entered.wait(1))
        try:
            result = feature.take_photo('no-overlap', generation=1)
            self.assertFalse(result.completed)
            self.assertEqual(result.code, 'camera_preview_stop_failed')
            self.assertEqual(backend.photo_count, 0)
            self.assertFalse(feature.snapshot()['preview'])
            self.assertTrue(feature.cleanup_snapshot()['close_unknown'])
            with self.assertRaises(FeatureError):
                feature.set_preview(True, generation=1)
            self.assertEqual(factory.call_count, 1)
        finally:
            release.set()
            self.wait_for(lambda: not feature.snapshot()['preview_pipeline']['worker_alive'])
        self.assertFalse(feature.snapshot()['on'])


if __name__ == '__main__':
    unittest.main()
