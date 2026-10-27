"""No servo, USB, network, Fish, or RP2040 operation in these tests."""
import unittest

from bmo_ai.features.motion.emotion_gestures import select_emotion_gesture as choose


class EmotionArmPolicyTests(unittest.TestCase):
    def test_opt_in_changes_only_distinct_expressive_emotions(self):
        self.assertEqual(choose("happy", enabled=True), "celebrate")
        self.assertEqual(choose("surprised", enabled=True), "shrug")
        self.assertEqual(choose("happy", previous_spoken_emotion="happy", enabled=True), "none")
        self.assertEqual(choose("surprised", previous_spoken_emotion="surprised", enabled=True), "none")

    def test_unenabled_and_sensitive_emotions_are_silent(self):
        for emotion in ("happy", "surprised", "sad", "crying", "angry", "neutral", "speaking", "thinking", "bogus"):
            with self.subTest(emotion=emotion):
                self.assertEqual(choose(emotion), "none")
        for emotion in ("sad", "crying", "angry", "neutral"):
            self.assertEqual(choose(emotion, enabled=True), "none")

    def test_model_gesture_beats_inference_and_action_blocks_implicit(self):
        self.assertEqual(choose("happy", "wave", enabled=True), "wave")
        self.assertEqual(choose("surprised", "celebrate", enabled=True), "celebrate")
        self.assertEqual(choose("happy", intent=object(), enabled=True), "none")
        self.assertEqual(choose("happy", "wave", intent=object(), enabled=True), "wave")

    def test_never_accepts_pwm_angles_or_unlisted_model_gestures(self):
        for proposed in ("180", "left=2500", "move_left", "happy", None, 180):
            with self.subTest(proposed=proposed):
                self.assertEqual(choose("happy", proposed, enabled=True), "none")


if __name__ == "__main__":
    unittest.main()
