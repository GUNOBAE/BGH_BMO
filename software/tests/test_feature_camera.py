"""Synthetic camera/PNG only: no Picamera2/OpenCV imports, API or device IO."""
import builtins
from dataclasses import replace
import errno
import json
import math
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.camera import (CameraFeature, DemoBackend, FaceBox, FixtureDetector,
    Frame, OpenCVFaceDetector, PhotoResult, PhotoStore, Picamera2Backend,
    TargetTracker, TrackerConfig, map_position, png_bytes, validate_png)


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def advance(self, amount=.11):
        self.now += amount


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.backend = DemoBackend(clock=self.clock, size=(32, 24))
        self.factory = Mock(return_value=self.backend)
        self.detector = FixtureDetector()
        self.targets = []
        self.store = PhotoStore(self.directory / 'photos')
        self.feature = CameraFeature(backend_factory=self.factory, detector=self.detector,
                                     store=self.store, clock=self.clock, on_target=self.targets.append)
        self.addCleanup(self.feature.close)
        real_import = builtins.__import__
        def no_device_import(name, *args, **kwargs):
            if name.split('.')[0] in ('picamera2', 'cv2'):
                raise AssertionError('Real camera/detector import forbidden')
            return real_import(name, *args, **kwargs)
        self.enterContext(patch('builtins.__import__', side_effect=no_device_import))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch('subprocess.Popen', side_effect=AssertionError('Device process forbidden')))

    def photo(self, request='photo-1', **kwargs):
        return self.feature.take_photo(request, generation=1, **kwargs)

    def test_default_off_and_lazy_real_constructors_open_nothing(self):
        self.feature.poll()
        self.assertFalse(self.feature.snapshot()['on'])
        self.assertIsNone(self.feature.preview_bytes())
        self.factory.assert_not_called()
        self.assertEqual(self.detector.calls, 0)
        Picamera2Backend(enabled=True)  # Construction still must not import/open.
        with self.assertRaises(FeatureError) as error:
            Picamera2Backend().open(1)
        self.assertEqual(error.exception.code, 'camera_real_disabled')
        with self.assertRaises(FeatureError):
            OpenCVFaceDetector().detect(None)

    def test_tracking_preview_photo_share_one_owner_then_explicit_off(self):
        self.feature.set_preview(True, generation=7)
        self.feature.set_tracking(True, generation=7)
        state = self.feature.poll()
        self.assertEqual((self.backend.open_count, self.detector.calls), (1, 1))
        self.assertEqual(validate_png(self.feature.preview_bytes()), (32, 24))
        self.assertEqual(state['target']['generation'], 7)
        self.assertNotEqual(state['stream_generation'], 7)
        self.feature.poll()
        self.assertEqual(self.backend.read_count, 1)
        self.feature.set_preview(False, generation=7)
        self.assertTrue(self.feature.snapshot()['on'])
        self.assertIsNone(self.feature.preview_bytes())
        self.feature.set_tracking(False, generation=7)
        self.assertFalse(self.feature.snapshot()['on'])
        self.assertEqual(self.backend.close_count, 1)
        self.feature.poll()
        self.assertEqual(self.factory.call_count, 1)

    def test_generation_rejects_late_commands_without_opening_camera(self):
        self.feature.set_tracking(True, generation=8)
        with self.assertRaises(FeatureError):
            self.feature.set_preview(True, generation=7)
        self.assertFalse(self.feature.snapshot()['preview'])
        self.feature.off()
        self.assertEqual(self.targets[-1].status, 'off')
        self.assertEqual(self.targets[-1].generation, 8)
        self.feature.poll()
        self.assertEqual(self.factory.call_count, 1)

    def test_stale_or_frozen_frames_do_not_refresh_preview_or_gaze(self):
        self.feature.set_preview(True, generation=1)
        self.feature.set_tracking(True, generation=1)
        self.feature.poll()
        stamp = self.clock()
        real_read = self.backend.read_frame
        self.backend.read_frame = lambda: replace(real_read(), captured_at=stamp)
        self.clock.advance(.7)
        state = self.feature.poll()
        self.assertEqual(state['target']['status'], 'lost')
        self.assertIsNone(state['target']['x'])
        self.assertIsNone(self.feature.preview_bytes())
        self.assertEqual(self.detector.calls, 1)

    def test_malformed_preview_or_geometry_disables_capture_without_detector_output(self):
        original = self.backend.read_frame
        for change in (dict(width=99), dict(payload=b'not an encoded image')):
            self.backend.read_frame = lambda: replace(original(), **change)
            self.feature.set_preview(True, generation=1)
            self.feature.set_tracking(True, generation=1)
            state = self.feature.poll()
            self.assertFalse(state['on'])
            self.assertEqual(state['error'], 'camera_capture_failed')
            self.assertIsNone(self.feature.preview_bytes())
        self.assertEqual(self.detector.calls, 0)

    def test_capture_temporarily_pauses_detection_and_restores_prior_options(self):
        self.feature.set_preview(True, generation=1)
        self.feature.set_tracking(True, generation=1)
        self.feature.poll()
        real_capture = self.backend.capture_photo
        def during_photo():
            self.assertTrue(self.feature.snapshot()['photo_active'])
            self.clock.advance()
            self.feature.poll()
            self.assertEqual(self.detector.calls, 1)
            return real_capture()
        self.backend.capture_photo = during_photo
        result = self.photo()
        self.assertTrue(result.completed)
        self.assertTrue(self.feature.snapshot()['tracking'])
        self.assertTrue(self.feature.snapshot()['preview'])
        self.assertEqual((self.backend.open_count, self.backend.close_count), (1, 0))
        self.clock.advance()
        self.feature.poll()
        self.assertEqual(self.detector.calls, 2)

    def test_synthetic_photo_is_verified_private_and_never_claims_real_capture(self):
        result = self.photo()
        self.assertEqual(result.status, 'simulated')
        self.assertTrue(result.simulated and result.completed)
        self.assertFalse(result.executed)
        self.assertEqual(result.completion, 'synthetic_saved')
        self.assertEqual(validate_png(self.feature.photo_bytes(result.photo_id)), (32, 24))
        path = self.store.root / (result.photo_id + '.png')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(str(self.directory), json.dumps(result.public()))
        self.assertEqual((self.backend.open_count, self.backend.close_count), (1, 1))
        self.feature.poll()
        self.assertFalse(self.feature.snapshot()['on'])

    def test_idempotency_retries_and_restart_return_saved_result_without_recapture(self):
        first = self.photo()
        self.assertEqual(self.photo(), first)
        other_factory = Mock(side_effect=AssertionError('Retry must not open camera'))
        restarted = CameraFeature(backend_factory=other_factory, store=PhotoStore(self.store.root), clock=self.clock)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.take_photo('photo-1', generation=10), first)
        other_factory.assert_not_called()
        self.assertEqual(self.backend.photo_count, 1)

    def test_same_id_conflict_and_interrupted_journal_never_replay(self):
        self.photo()
        conflict = self.store.begin('photo-1', fingerprint='other_contract')
        self.assertEqual((conflict.status, conflict.code), ('rejected', 'request_id_conflict'))
        self.assertIsNone(self.store.begin('interrupted'))
        self.store.ledger._release()  # Model process death, without finishing.
        result = PhotoStore(self.store.root).begin('interrupted')
        self.assertEqual((result.status, result.completion), ('unknown', 'unknown'))
        self.assertEqual(self.backend.photo_count, 1)

    def test_pre_cancel_is_recorded_without_capture_and_retry_is_not_reexecuted(self):
        result = self.photo(cancel_check=lambda: True)
        self.assertEqual(result.status, 'cancelled')
        self.factory.assert_not_called()
        self.assertEqual(self.photo().status, 'cancelled')
        self.assertEqual(list(self.store.root.glob('*.png')), [])

    def test_cancel_during_capture_removes_partial_work_and_keeps_tracking(self):
        self.feature.set_tracking(True, generation=1)
        real_capture = self.backend.capture_photo
        def cancelled():
            self.assertTrue(self.feature.cancel_photo('photo-1'))
            return real_capture()
        self.backend.capture_photo = cancelled
        result = self.photo()
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(list(self.store.root.glob('*.png')), [])
        self.assertTrue(self.feature.snapshot()['tracking'])
        self.assertTrue(self.feature.snapshot()['on'])

    def test_cancel_after_commit_preserves_saved_fact_and_bytes(self):
        original_finish = self.store.ledger.finish
        def cancel_after_publish(result):
            if result.completed:
                self.feature.cancel_photo(result.request_id)
            return original_finish(result)
        self.store.ledger.finish = cancel_after_publish
        result = self.photo()
        self.assertTrue(result.completed)
        self.assertEqual(validate_png(self.feature.photo_bytes(result.photo_id)), (32, 24))
        self.assertEqual(self.photo(), result)
        self.assertEqual(self.backend.photo_count, 1)

    def test_off_during_capture_cannot_restore_or_reactivate_previous_tracking(self):
        entered, release = Event(), Event()
        results = []
        self.feature.set_tracking(True, generation=1)
        self.feature.set_preview(True, generation=1)
        original = self.backend.capture_photo
        def blocked():
            entered.set()
            if not release.wait(2):
                raise AssertionError('Synthetic capture gate timed out')
            return original()
        self.backend.capture_photo = blocked
        thread = Thread(target=lambda: results.append(self.photo()))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.feature.off()
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(results[0].status, 'cancelled')
            self.assertFalse(self.feature.snapshot()['on'])
            self.assertFalse(self.feature.snapshot()['tracking'])
            self.assertFalse(self.feature.snapshot()['preview'])
            self.feature.poll()
            self.assertEqual(self.factory.call_count, 1)
            self.assertEqual(self.backend.close_count, 1)
            self.assertIsNone(self.feature.preview_bytes())
        finally:
            release.set()
            thread.join(2)

    def test_late_detector_result_after_off_never_publishes_a_target(self):
        entered, release = Event(), Event()
        def blocked(frame):
            entered.set()
            if not release.wait(2):
                raise AssertionError('Synthetic detector gate timed out')
            return (FaceBox(.7, .1, .2, .2),)
        self.detector.detect = blocked
        self.feature.set_tracking(True, generation=1)
        thread = Thread(target=self.feature.poll)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.feature.off()
            count = len(self.targets)
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(self.targets), count)
            self.assertEqual(self.targets[-1].status, 'off')
        finally:
            release.set()
            thread.join(2)

    def test_parallel_retry_uses_same_store_lease_and_never_opens_second_backend(self):
        entered, release = Event(), Event()
        original = self.backend.capture_photo
        def blocked():
            entered.set()
            if not release.wait(2):
                raise AssertionError('Synthetic capture gate timed out')
            return original()
        self.backend.capture_photo = blocked
        results = []
        thread = Thread(target=lambda: results.append(self.photo()))
        thread.start()
        other_factory = Mock(side_effect=AssertionError('Duplicate capture forbidden'))
        other = CameraFeature(backend_factory=other_factory, store=PhotoStore(self.store.root), clock=self.clock)
        self.addCleanup(other.close)
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(other.take_photo('photo-1', generation=1).status, 'running')
            other_factory.assert_not_called()
            release.set()
            thread.join(2)
            self.assertEqual(other.take_photo('photo-1', generation=1), results[0])
            self.assertEqual(self.backend.photo_count, 1)
        finally:
            release.set()
            thread.join(2)

    def test_disk_full_has_no_false_success_partial_file_or_automatic_retry(self):
        with patch('bmo_ai.features.camera.store.os.link', side_effect=OSError(errno.ENOSPC, 'synthetic private path')):
            result = self.photo()
        self.assertEqual(result.status, 'failed')
        self.assertFalse(result.completed)
        self.assertNotIn('private', json.dumps(result.public()))
        self.assertEqual(list(self.store.root.glob('*.png')), [])
        self.assertEqual(list(self.store.root.glob('.capture-*')), [])
        self.assertEqual(self.photo(), result)
        self.assertEqual(self.backend.photo_count, 1)

    def test_journal_failure_after_publication_preserves_photo_and_blocks_restart_replay(self):
        def unavailable(result):
            self.store.ledger._release()
            raise OSError('synthetic private storage failure')
        self.store.ledger.finish = unavailable
        result = self.photo()
        self.assertTrue(result.completed)
        self.assertEqual(result.code, 'photo_saved_journal_pending')
        self.assertTrue(self.feature.photo_bytes(result.photo_id))
        retry = PhotoStore(self.store.root).begin('photo-1')
        self.assertEqual(retry.status, 'unknown')
        self.assertEqual(self.backend.photo_count, 1)

    def test_focus_failure_is_not_reported_as_saved(self):
        original = self.backend.capture_photo
        self.backend.capture_photo = lambda: replace(original(), focus_ok=False)
        result = self.photo()
        self.assertEqual((result.status, result.code), ('failed', 'photo_focus_failed'))
        self.assertFalse(result.completed)
        self.assertEqual(list(self.store.root.glob('*.png')), [])

    def test_interrupt_releases_camera_and_journal_without_replaying_uncertain_work(self):
        self.backend.capture_photo = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.photo()
        self.assertFalse(self.feature.snapshot()['on'])
        self.assertEqual(self.backend.close_count, 1)
        self.assertIsNone(self.store.ledger._lease)
        retry = PhotoStore(self.store.root).begin('photo-1')
        self.assertEqual((retry.status, retry.code), ('unknown', 'photo_interrupted'))
        self.assertFalse(retry.completed)

    def test_invalid_requests_and_symlink_paths_never_open_camera(self):
        for request in ('../x', '/tmp/photo', '', 'x' * 129):
            with self.assertRaises(FeatureError):
                self.photo(request)
        link = self.directory / 'unsafe-link'
        link.symlink_to(self.directory, target_is_directory=True)
        self.feature.store = PhotoStore(link)
        self.assertEqual(self.photo().status, 'failed')
        self.factory.assert_not_called()
        with self.assertRaises(FeatureError):
            self.store.read('../not-a-photo')

    def test_close_is_permanent_and_repeated_cleanup_is_safe(self):
        self.feature.set_preview(True, generation=1)
        self.feature.close()
        self.feature.close()
        self.assertEqual(self.backend.close_count, 1)
        with self.assertRaises(FeatureError):
            self.feature.set_tracking(True, generation=2)
        self.feature.poll()
        self.assertFalse(self.feature.snapshot()['on'])


class TargetTrackerTests(unittest.TestCase):
    def setUp(self):
        self.payload = png_bytes(16, 16)

    def frame(self, seq, time, stream=1):
        return Frame(stream, seq, time, 16, 16, self.payload)

    def test_axis_diagonal_mirror_and_rotation_mapping_are_explicit(self):
        for point, expected in (((0, 0), (-1, -1)), ((1, 1), (1, 1)), ((0, 1), (-1, 1)), ((1, 0), (1, -1)), ((.5, .5), (0, 0))):
            self.assertEqual(map_position(*point), expected)
        rotated = map_position(.2, .3, rotation=90)
        self.assertAlmostEqual(rotated[0], .4)
        self.assertAlmostEqual(rotated[1], -.6)
        self.assertAlmostEqual(map_position(.2, .3, mirror=True)[0], .6)
        self.assertEqual(map_position(0, 0, rotation=180), (1, 1))
        self.assertEqual(map_position(0, 0, rotation=270), (-1, 1))

    def test_invalid_geometry_timestamp_and_transform_fail_safely(self):
        for value in (float('nan'), float('inf'), True):
            with self.assertRaises(FeatureError):
                FaceBox(value, .1, .2, .2)
        with self.assertRaises(FeatureError):
            FaceBox(.9, .1, .2, .2)
        with self.assertRaises(FeatureError):
            TrackerConfig(rotation=45)
        with self.assertRaises(FeatureError):
            map_position(.2, .3, rotation=45)
        with self.assertRaises(FeatureError):
            Frame(1, 1, -1, 16, 16, self.payload)

    def test_first_largest_face_is_retained_when_another_face_becomes_larger(self):
        tracker = TargetTracker(TrackerConfig(smoothing_tau=.001, max_speed=100))
        current = FaceBox(.5, .3, .3, .3)
        first = tracker.update(self.frame(1, 0), (FaceBox(.1, .3, .1, .1), current), now=0, generation=9)
        second = tracker.update(self.frame(2, .1), (FaceBox(0, .1, .4, .5), current), now=.1, generation=9)
        self.assertEqual(first.target_id, second.target_id)
        self.assertGreater(second.x, 0)
        self.assertEqual(second.generation, 9)

    def test_far_target_switch_needs_consecutive_confirmation(self):
        tracker = TargetTracker(TrackerConfig(smoothing_tau=.001, max_speed=100, match_radius=.15))
        old, new = FaceBox(0, .4, .2, .2), FaceBox(.8, .4, .2, .2)
        first = tracker.update(self.frame(1, 0), (old,), now=0, generation=1)
        for seq in (2, 3):
            target = tracker.update(self.frame(seq, (seq - 1) * .1), (new,), now=(seq - 1) * .1, generation=1)
            self.assertEqual(target.status, 'grace')
            self.assertEqual(target.target_id, first.target_id)
        switched = tracker.update(self.frame(4, .3), (new,), now=.3, generation=1)
        self.assertEqual(switched.status, 'tracking')
        self.assertNotEqual(switched.target_id, first.target_id)
        self.assertGreater(switched.x, 0)

    def test_loss_grace_expiration_returns_none_not_frozen_gaze(self):
        tracker = TargetTracker()
        first = tracker.update(self.frame(1, 0), (FaceBox(.1, .1, .2, .2),), now=0, generation=1)
        grace = tracker.update(self.frame(2, .1), (), now=.1, generation=1)
        self.assertEqual(grace.status, 'grace')
        self.assertEqual(grace.captured_at, first.captured_at)
        self.assertLessEqual(grace.valid_until, .5)
        lost = tracker.expire(.6)
        self.assertEqual(lost.status, 'lost')
        self.assertIsNone(lost.x)
        self.assertIsNone(lost.y)

    def test_stale_reordered_future_and_previous_stream_frames_do_not_replace_target(self):
        tracker = TargetTracker()
        box = FaceBox(.2, .2, .2, .2)
        first = tracker.update(self.frame(3, .2, stream=2), (box,), now=.2, generation=5)
        for frame in (self.frame(2, .2, stream=2), self.frame(4, .1, stream=2), self.frame(4, 4, stream=2), self.frame(100, .3, stream=1)):
            target = tracker.update(frame, (FaceBox(.7, .7, .2, .2),), now=.3, generation=5)
            self.assertEqual((target.frame_seq, target.captured_at), (first.frame_seq, first.captured_at))
        stale = tracker.update(self.frame(5, .2, stream=2), (box,), now=1.0, generation=5)
        self.assertEqual(stale.status, 'lost')

    def test_dead_zone_smoothing_and_vector_speed_limit_are_bounded(self):
        centered = TargetTracker()
        result = centered.update(self.frame(1, 0), (FaceBox(.46, .46, .1, .1),), now=0, generation=1)
        self.assertEqual((result.x, result.y), (0, 0))
        tracker = TargetTracker(TrackerConfig(max_speed=1.0))
        box = FaceBox(.8, .8, .2, .2)
        previous = (0, 0)
        for seq in range(1, 11):
            now = (seq - 1) * .1
            target = tracker.update(self.frame(seq, now), (box,), now=now, generation=20)
            self.assertLessEqual(math.hypot(target.x - previous[0], target.y - previous[1]), .1000001)
            self.assertTrue(0 <= target.x <= 1 and 0 <= target.y <= 1)
            previous = target.x, target.y
        self.assertGreater(previous[0], 0)

    def test_png_validation_rejects_corruption_and_truncation(self):
        self.assertEqual(validate_png(self.payload), (16, 16))
        for invalid in (b'', b'not a png', self.payload[:-1], self.payload[:-1] + bytes([self.payload[-1] ^ 1])):
            with self.assertRaises(FeatureError):
                validate_png(invalid)


if __name__ == '__main__':
    unittest.main()
