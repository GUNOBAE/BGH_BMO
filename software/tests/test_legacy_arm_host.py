"""B1 transport and desktop safety boundary, all fake I/O."""
import hashlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from bmo_ai.desktop_app import main, parser
from bmo_ai.features.motion.legacy_host import (
    LegacyArmLink, LegacyArmError, LegacyMotionFeature, validate_legacy_install)
from bmo_ai.features.contracts import FeatureError


ROOT = Path(__file__).resolve().parents[2] / "firmware/staging/bmo_arm_legacy_bridge_v1"
spec = importlib.util.spec_from_file_location("legacy_bridge_host_test", ROOT / "bmo_arm_bridge.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Clock:
    def __init__(self):
        self.t = 0.
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


class FakePort:
    def __init__(self, controller):
        self.controller = controller
        self.reply = b""
        self.sent = []
        self.closed = False
    def write(self, packet):
        self.sent.append(packet)
        self.reply = self.controller.handle(packet)
        return len(packet)
    def read_until(self, sep, limit):
        return self.reply or b""
    def close(self):
        self.closed = True


class B1HostTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.moves = []
        self.controller = core.LegacyBridge(None, "bootA",
            lambda side, deg: self.moves.append((side, deg)),
            lambda deg: self.moves.append(("both", deg)),
            lambda: 0, enabled=True, clock=self.clock)
        self.controller.enabled = True  # pure fake; no USB allocated
        self.port = FakePort(self.controller)
        self.link = LegacyArmLink(self.port, motor_permission=lambda: True)

    def test_hello_named_wave_heartbeat_and_no_raw_angle(self):
        self.assertEqual(self.link.hello(), "READY")
        self.assertEqual(self.link.gesture("wave"), "ACCEPTED")
        self.link.ping()
        self.assertTrue(any(m.startswith(b"B1 DO") and b"wave" in m for m in self.port.sent))
        with self.assertRaises(LegacyArmError):
            self.link.gesture("PWM GP10 2400")
        self.assertEqual(len([m for m in self.port.sent if b"DO " in m]), 1)

    def test_motor_permission_is_required_after_hello(self):
        self.link.motor_permission = lambda: False
        self.link.hello()
        with self.assertRaises(LegacyArmError):
            self.link.gesture("wave")
        self.assertFalse(any(b" DO " in m for m in self.port.sent))

    def test_explicit_gesture_only_and_position_unverified_receipt(self):
        motion = LegacyMotionFeature(self.link, clock=self.clock, cooldown=0)
        motion.set_context("session1", "turn1", 1)
        result = motion.submit("wave", request_id="wave1", session_id="session1",
            turn_id="turn1", mode_generation=1, started_at=0)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(self.port.sent, [])
        events = motion.poll()
        self.assertTrue(any(e.code == "ack_position_unverified" for e in events))
        self.assertFalse(motion.snapshot()["position_verified"])
        with self.assertRaises(AssertionError):
            self.assertTrue(motion.snapshot()["position_verified"])
        motion.cancel()
        self.assertIn(b" HOLD ", b" ".join(self.port.sent))

    def test_no_retries_after_malformed_ack(self):
        self.link.hello()
        self.port.read_until = lambda *_: b"malformed\n"
        with self.assertRaises(LegacyArmError):
            self.link.gesture("wave")
        number = len(self.port.sent)
        with self.assertRaises(LegacyArmError):
            self.link.gesture("wave")
        self.assertEqual(len(self.port.sent), number)

    def test_reviewed_install_is_full_byte_pinned_and_read_only(self):
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            original = (ROOT / "code.py").read_bytes()
            enabled = original.replace(b"REMOTE_ENABLED = False", b"REMOTE_ENABLED = True", 1)
            (dest / "code.py").write_bytes(enabled)
            (dest / "bmo_arm_bridge.py").write_bytes((ROOT / "bmo_arm_bridge.py").read_bytes())
            (dest / "boot.py").write_text("import usb_cdc\nusb_cdc.enable(console=True, data=True)\n")
            sha = hashlib.sha256(enabled).hexdigest()
            self.assertEqual(validate_legacy_install(ROOT, sha, mount=dest), sha)
            (dest / "code.py").write_bytes(original)
            with self.assertRaises(LegacyArmError):
                validate_legacy_install(ROOT, sha, mount=dest)

    def test_desktop_dry_run_does_not_open_serial_even_with_opt_in_flags(self):
        with patch("bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open",
                   side_effect=AssertionError("serial unexpectedly opened")):
            self.assertEqual(main(["--live", "--dry-run", "--arms-supervised",
                "--arm-legacy-bridge", "--arm-reviewed-code-sha", "a" * 64,
                "--arm-power-off-ready", "--emotion-arms"]), 0)
        self.assertFalse(parser().parse_args(["--simulate"]).arm_legacy_bridge)


if __name__ == "__main__":
    unittest.main()
