"""Staged no-boot-motion controller protocol regression; no device opened."""
from pathlib import Path
import unittest

from bmo_ai.features.motion.arm_control_v1 import (
    ArmCommandError, encode_command, decode_reply,
)


BOOT = "0123456789abcdef"
LIMITS = {
    "left": {"minimum": 85, "neutral": 90, "maximum": 95},
    "right": {"minimum": 84, "neutral": 90, "maximum": 96},
}


class StagedArmProtocolTests(unittest.TestCase):
    def test_hello_stop_and_supervised_move_encode_strictly(self):
        self.assertEqual(encode_command("hello", 0),
                         b'{"v":1,"seq":0,"op":"hello"}\n')
        self.assertIn(b'"op":"stop"', encode_command("stop", 1, boot=BOOT))
        message = encode_command("move", 2, boot=BOOT,
                                 side="left", angle=90,
                                 operator_present=True, reviewed_limits=LIMITS)
        self.assertIn(b'"angle":90', message)
        self.assertLessEqual(len(message), 192)

    def test_reject_unreviewed_motion_and_unsafe_angle(self):
        for kwargs in (dict(side="left", angle=90),
                       dict(side="left", angle=140, operator_present=True,
                            reviewed_limits=LIMITS),
                       dict(side="something", angle=90, operator_present=True,
                            reviewed_limits=LIMITS),
                       dict(side="left", angle=True, operator_present=True,
                            reviewed_limits=LIMITS)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ArmCommandError):
                encode_command("move", 5, boot=BOOT, **kwargs)
        with self.assertRaises(ArmCommandError):
            encode_command("arm", 4, boot=BOOT, operator_present=False,
                           reviewed_limits=LIMITS)
        with self.assertRaises(ArmCommandError):
            encode_command("heartbeat", 6, boot="not_a_boot_id")

    def test_no_position_feedback_in_controller_reply(self):
        payload = (b'{"v":1,"boot":"0123456789abcdef","seq":1,'
                   b'"code":"accepted_position_unverified","accepted":true,'
                   b'"position_verified":false}\n')
        result = decode_reply(payload, expected_boot=BOOT)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["position_verified"])
        for invalid in (payload.replace(b"false", b"true"),
                        payload.replace(b'"accepted":true', b'"accepted":false,"accepted":true'),
                        payload.replace(BOOT.encode(), b"other"), b"\xff\n"):
            with self.subTest(invalid=invalid), self.assertRaises(ArmCommandError):
                decode_reply(invalid, expected_boot=BOOT)

    def test_staged_firmware_has_no_boot_servo_movement(self):
        root = Path(__file__).resolve().parents[2]
        firmware = (root / "firmware" / "staging" / "BMO_SAFE_CODE_V1.py").read_text()
        boot = (root / "firmware" / "staging" / "BMO_SAFE_BOOT_V1.py").read_text()
        self.assertIn("duty_cycle=0", firmware)
        self.assertIn("last_heartbeat >= .6", firmware)
        self.assertIn('open("/arm_limits.json"', firmware)
        self.assertIn("usb_cdc.enable(console=True, data=True)", boot)
        self.assertNotIn("set_arms(0)", firmware)
        self.assertNotIn("os.system(", firmware)


if __name__ == "__main__":
    unittest.main()
