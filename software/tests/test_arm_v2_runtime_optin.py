"""Arm integration remains opt-in: app default never opens serial or PWM."""
import unittest
from unittest.mock import patch

from bmo_ai.desktop_app import parser, main
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.motion.gesture_rules_v2 import LEGACY_REVIEWED_LIMITS, plan_gesture


class ArmV2RuntimeOptInTests(unittest.TestCase):
    def test_default_live_mode_still_denies_any_motor_action(self):
        mode = RuntimePolicy(simulate=False, conversation_ptt_microphone=True)
        self.assertFalse(mode.arms_supervised)
        self.assertEqual(mode.public()["arms"], "denied")
        with self.assertRaises(FeatureError):
            mode.require("arms")
        args = parser().parse_args(["--live", "--ptt-microphone",
                                    "--camera-supervised", "--photo-directory",
                                    "/tmp/test-photos"])
        self.assertFalse(args.arms_supervised)

    def test_explicit_arm_mode_is_not_inferred_from_camera_voice(self):
        state = RuntimePolicy(simulate=False, arms_supervised=True)
        self.assertEqual(state.public()["arms"], "explicit_supervised")
        self.assertFalse(state.camera_supervised)
        self.assertFalse(state.conversation_ptt_microphone)
        state.require("arms")
        with self.assertRaises(FeatureError):
            RuntimePolicy(simulate=True, arms_supervised=True)

    def test_no_arm_flag_is_enabled_by_dry_run_status(self):
        with patch("bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open",
                   side_effect=AssertionError("CDC opened")):
            self.assertEqual(main(["--live", "--status"]), 0)
            self.assertEqual(main(["--live", "--dry-run",
                "--arms-supervised", "--arm-reviewed-code-sha", "a" * 64,
                "--arm-power-off-ready"]), 0)

    def test_explicit_intents_never_use_raw_pwm(self):
        for action in ("raise_left_arm", "raise_right_arm", "both_up", "both_down"):
            self.assertEqual(HostIntent("arm_gesture", action).target, action)
            self.assertTrue(plan_gesture(action))
        self.assertEqual(LEGACY_REVIEWED_LIMITS["left"]["min_us"], 600)
        self.assertEqual(LEGACY_REVIEWED_LIMITS["right"]["max_us"], 2400)
        with self.assertRaises(ValueError):
            plan_gesture("MOVE GP10 1500", from_model=True)


if __name__ == "__main__":
    unittest.main()
