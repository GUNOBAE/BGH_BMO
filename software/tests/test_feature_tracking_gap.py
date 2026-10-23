"""A lost camera target must not accumulate permission for one large jump."""
import math
import unittest

from bmo_ai.features.camera import FaceBox, Frame, TargetTracker, TrackerConfig, png_bytes


class TrackingGapTests(unittest.TestCase):
    def test_reacquisition_limits_the_first_frame_step_after_a_long_gap(self):
        tracker = TargetTracker(TrackerConfig(max_speed=.5, smoothing_tau=.01,
                                             switch_confirmations=1))
        payload = png_bytes(2, 2)
        def update(seq, at, boxes):
            return tracker.update(Frame(1, seq, at, 2, 2, payload), boxes,
                                  now=at, generation=1)
        first = update(1, 0., [FaceBox(0., .45, .1, .1)])
        update(2, .1, [])
        update(3, .3, [])
        update(4, .7, [])
        restored = update(5, .8, [FaceBox(.9, .45, .1, .1)])
        self.assertEqual(restored.status, "tracking")
        self.assertLessEqual(math.hypot(restored.x-first.x, restored.y-first.y), .5*.1+1e-8)
