"""Camera consumers and local position gaze, with no device/provider IO."""
from dataclasses import replace
import time
import unittest
from unittest.mock import Mock

from bmo_ai.features.camera import CameraFeature, DemoBackend, FaceBox, FixtureDetector, Frame, TargetTracker, TrackerConfig, png_bytes
from bmo_ai.features.camera.backends import NullDetector
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.resources import GazeArbiter


class Clock:
    def __init__(self):
        self.now = 1.

    def __call__(self):
        return self.now

    def advance(self, seconds=.26):
        self.now += seconds


class TrackingConsumerTests(unittest.TestCase):
    def make(self, *, asynchronous=False, detector=None):
        clock = Clock()
        backend = DemoBackend(clock=clock, size=(32, 24))
        detector = detector if detector is not None else FixtureDetector()
        factory = Mock(return_value=backend)
        feature = CameraFeature(backend_factory=factory, detector=detector,
                                clock=clock, max_hz=4, asynchronous_preview=asynchronous)
        self.addCleanup(feature.close)
        return feature, backend, detector, clock, factory

    def wait_for(self, condition):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        self.fail('bounded producer condition did not complete')

    def test_tracking_stop_releases_only_tracking_not_preview_or_video(self):
        feature, backend, detector, clock, factory = self.make()
        feature.set_tracking(True, generation=1)
        feature.set_preview(True, generation=1)
        feature.acquire_consumer('video', generation=1)
        feature.poll()
        self.assertEqual(detector.calls, 1)
        self.assertIsNotNone(feature.cached_frame())
        feature.set_tracking(False, generation=1)
        state = feature.snapshot()
        self.assertFalse(state['tracking_requested'])
        self.assertFalse(state['tracking'])
        self.assertTrue(state['on'])
        self.assertTrue(state['preview'])
        self.assertEqual(state['consumers'], ['video'])
        clock.advance()
        feature.poll()
        self.assertEqual(detector.calls, 1)
        feature.release_consumer('video', generation=1)
        self.assertTrue(feature.snapshot()['on'])
        feature.set_preview(False, generation=1)
        self.assertFalse(feature.snapshot()['on'])
        self.assertEqual((backend.open_count, backend.close_count, factory.call_count), (1, 1, 1))

    def test_borrower_suspends_detector_and_restores_only_current_user_choice(self):
        feature, backend, detector, clock, _ = self.make()
        feature.set_tracking(True, generation=1)
        feature.poll()
        feature.acquire_consumer('video', generation=1)
        feature.suspend_tracking('video', generation=1)
        self.assertTrue(feature.snapshot()['tracking_requested'])
        self.assertFalse(feature.snapshot()['tracking'])
        self.assertEqual(feature.snapshot()['tracking_suspended'], ['video'])
        clock.advance()
        feature.poll()
        self.assertEqual(detector.calls, 1)
        self.assertIsNotNone(feature.cached_frame())
        self.assertIsNone(feature.preview_bytes())  # position processing does not expose the image.
        feature.resume_tracking('video', generation=1)
        feature.release_consumer('video', generation=1)
        feature.poll()
        self.assertEqual(detector.calls, 2)
        self.assertEqual(backend.open_count, 1)
        feature.acquire_consumer('video', generation=1)
        feature.suspend_tracking('video', generation=1)
        feature.set_tracking(False, generation=1)  # explicit stop during a borrower.
        feature.resume_tracking('video', generation=1)
        feature.release_consumer('video', generation=1)
        self.assertFalse(feature.snapshot()['tracking'])
        self.assertFalse(feature.snapshot()['on'])

    def test_nested_borrowers_do_not_resume_early_and_off_retires_all_choices(self):
        feature, backend, _, _, _ = self.make()
        feature.set_tracking(True, generation=4)
        feature.suspend_tracking('photo', generation=4)
        feature.suspend_tracking('video', generation=4)
        feature.resume_tracking('photo', generation=4)
        self.assertFalse(feature.snapshot()['tracking'])
        feature.resume_tracking('video', generation=4)
        self.assertTrue(feature.snapshot()['tracking'])
        feature.acquire_consumer('video', generation=4)
        feature.off()
        feature.resume_tracking('video', generation=4)
        state = feature.snapshot()
        self.assertFalse(state['on'])
        self.assertFalse(state['tracking_requested'])
        self.assertEqual(state['consumers'], [])
        self.assertEqual(backend.open_count, 1)
        with self.assertRaises(FeatureError):
            feature.acquire_consumer('video', generation=3)
        with self.assertRaises(FeatureError):
            feature.acquire_consumer('arbitrary_owner', generation=4)

    def test_video_consumer_uses_async_one_slot_cache_with_browser_preview_off(self):
        feature, backend, detector, _, factory = self.make(asynchronous=True)
        feature.acquire_consumer('video', generation=1)
        self.wait_for(lambda: feature.cached_frame() is not None)
        frame = feature.cached_frame()
        self.assertIsNone(feature.latest_frame())  # private in-process consumer is not browser opt-in.
        self.assertIsNone(feature.cached_frame(after=f'{frame.stream_generation}:{frame.seq}'))
        before = backend.read_count
        for _ in range(20):
            feature.cached_frame()
        self.assertLessEqual(backend.read_count - before, 1)
        self.assertEqual(detector.calls, 0)
        feature.set_tracking(True, generation=1)
        feature.suspend_tracking('video', generation=1)
        feature.set_preview(True, generation=1)
        feature.set_preview(False, generation=1)
        self.wait_for(lambda: backend.read_count > before + 1)
        self.assertIsNotNone(feature.cached_frame())
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(backend.open_count, 1)
        feature.off()
        self.assertIsNone(feature.cached_frame())
        self.assertFalse(feature.snapshot()['preview_pipeline']['worker_alive'])

    def test_missing_local_detector_is_a_capability_failure_not_fake_tracking(self):
        feature, backend, _, _, _ = self.make(detector=NullDetector())
        self.assertFalse(feature.snapshot()['tracking_available'])
        feature.set_preview(True, generation=1)
        with self.assertRaises(FeatureError) as failure:
            feature.set_tracking(True, generation=1)
        self.assertEqual(failure.exception.code, 'face_detector_unavailable')
        self.assertFalse(feature.snapshot()['tracking_requested'])
        self.assertTrue(feature.snapshot()['preview'])
        self.assertTrue(feature.snapshot()['on'])
        self.assertEqual(backend.open_count, 1)


class PositionGazeFlowTests(unittest.TestCase):
    def test_multitarget_continuity_stale_loss_and_bounded_center_return(self):
        clock = Clock()
        tracker = TargetTracker(TrackerConfig(switch_confirmations=3))
        gaze = GazeArbiter(clock=clock)
        first = FaceBox(.65, .35, .15, .2)
        other = FaceBox(.05, .05, .5, .5)
        frame = Frame(1, 1, clock(), 32, 24, png_bytes(32, 24))
        target = tracker.update(frame, [first], now=clock(), generation=1)
        first_id = target.target_id
        for seq in (2, 3, 4):
            clock.advance(.1)
            frame = replace(frame, seq=seq, captured_at=clock())
            target = tracker.update(frame, [other, first], now=clock(), generation=1)
            self.assertEqual(target.target_id, first_id)
            self.assertGreater(target.x, 0)
        gaze.offer('camera', target.x, target.y, generation=1, expires_at=target.valid_until)
        camera = gaze.resolve(phase='idle', generation=1)
        self.assertEqual(camera['owner'], 'camera')
        self.assertEqual(gaze.resolve(phase='listening', generation=1)['owner'], 'native')
        self.assertEqual(gaze.resolve(phase='thinking', generation=1)['owner'], 'native')
        clock.advance(.6)
        lost = tracker.update(frame, [first], now=clock(), generation=1)
        self.assertEqual(lost.status, 'lost')
        self.assertIsNone(lost.x)
        gaze.clear('camera', smooth=True, generation=1)
        returning = gaze.resolve(phase='idle', generation=1)
        self.assertEqual(returning['owner'], 'camera')
        clock.advance(.175)
        midway = gaze.resolve(phase='idle', generation=1)
        self.assertGreater(abs(midway['x']), 0)
        self.assertLess(abs(midway['x']), abs(returning['x']))
        gaze.offer('manual', -.8, .5, generation=1, expires_at=clock()+1)
        self.assertEqual(gaze.resolve(phase='thinking', generation=1)['owner'], 'manual')
        gaze.clear('manual')
        clock.advance(.2)
        self.assertEqual(gaze.resolve(phase='idle', generation=1),
                         {'owner':'idle', 'apply':True, 'x':0., 'y':0.})

    def test_center_return_does_not_cross_generation_or_restart_on_repeated_loss(self):
        clock = Clock()
        gaze = GazeArbiter(clock=clock)
        gaze.offer('camera', .8, -.5, generation=1, expires_at=clock()+.5)
        gaze.clear('camera', smooth=True, generation=1)
        clock.advance(.2)
        first = gaze.resolve(phase='idle', generation=1)
        gaze.clear('camera', smooth=True, generation=1)
        clock.advance(.1)
        second = gaze.resolve(phase='idle', generation=1)
        self.assertLess(abs(second['x']), abs(first['x']))
        self.assertEqual(gaze.resolve(phase='idle', generation=2)['owner'], 'idle')


if __name__ == '__main__':
    unittest.main()
