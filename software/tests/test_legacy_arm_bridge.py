"""Offline tests for original-compatible CircuitPython bridge. NO HARDWARE."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "firmware/staging/bmo_arm_legacy_bridge_v1/bmo_arm_bridge.py"
spec = importlib.util.spec_from_file_location("bmo_legacy_bridge_fixture", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
LegacyBridge = module.LegacyBridge


class Clock:
    def __init__(self):
        self.now = 0.
    def __call__(self):
        return self.now
    def advance(self, dt):
        self.now += dt


class Port:
    def __init__(self):
        self.buffer = b""
        self.writes = []
    @property
    def in_waiting(self):
        return len(self.buffer)
    def read(self, size):
        part, self.buffer = self.buffer[:size], self.buffer[size:]
        return part
    def write(self, value):
        self.writes.append(value)
        return len(value)


class TestLegacyBridge(unittest.TestCase):
    def setUp(self):
        self.port = Port()
        self.clock = Clock()
        self.motions = []
        self.pose = [0]
        self.bridge = LegacyBridge(self.port, "bA1",
            lambda side, a: self.motions.append((side, a)),
            lambda a: self.motions.append(("both", a)),
            lambda: self.pose[0], enabled=True, clock=self.clock)

    def send(self, line):
        self.port.buffer += line.encode()
        self.bridge.poll()
        return self.port.writes[-1].decode() if self.port.writes else None

    def test_disabled_hello_and_commands_never_move(self):
        self.bridge.enabled = False
        self.assertIn("DISABLED", self.send("B1 HELLO h1\n"))
        self.assertIn("DISABLED", self.send("B1 DO d1 bA1 wave\n"))
        self.assertFalse(self.motions)
        self.bridge.enabled = True
        self.assertIn("READY", self.send("B1 HELLO h2\n"))
        self.assertFalse(self.motions)

    def test_wave_only_moves_right_with_original_range_and_restores_pose(self):
        self.assertIn("ACCEPTED", self.send("B1 DO w1 bA1 wave\n"))
        for i in range(30):
            self.clock.advance(.1)
            if i % 3 == 0:
                self.send("B1 PING p%d bA1\n" % i)
            self.bridge.poll()
        self.assertEqual(self.motions, [
            ("right", 180), ("right", 145), ("right", 180),
            ("right", 145), ("right", 0)])

    def test_duplicate_command_replays_ack_without_motor_restart(self):
        cmd = "B1 DO w1 bA1 wave\n"
        self.assertEqual(self.send(cmd), self.send(cmd))
        self.assertEqual(self.bridge.step_index, 1)
        self.assertIn("REQUEST_CONFLICT", self.send("B1 DO w1 bA1 shrug\n"))

    def test_gp7_override_drops_active_steps_and_latches_automation(self):
        self.send("B1 DO w1 bA1 wave\n")
        self.bridge.manual_cancel()
        self.assertTrue(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)
        self.assertIn("STOPPED", self.send("B1 DO w2 bA1 wave\n"))

    def test_heartbeat_timeout_cancels_and_latches(self):
        self.send("B1 DO w1 bA1 wave\n")
        self.clock.advance(1.)
        self.bridge.poll()
        self.assertTrue(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)

    def test_hold_stop_do_not_change_original_gp7_control(self):
        self.send("B1 DO w1 bA1 shrug\n")
        self.assertIn("HELD", self.send("B1 HOLD h1 bA1\n"))
        self.assertIn("STOPPED", self.send("B1 STOP s1 bA1\n"))
        self.bridge.manual_cancel()
        self.assertFalse(self.bridge.steps)

    def test_invalid_stale_pwm_and_oversized_inputs_fail_closed(self):
        self.assertIn("STALE_BOOT", self.send("B1 DO old1 wrong wave\n"))
        self.assertNotIn("ACCEPTED", self.send("B1 DO raw1 bA1 4000\n"))
        count = len(self.port.writes)
        self.send("X" * 150 + "\n")
        self.assertEqual(len(self.port.writes), count)
        self.assertFalse(self.motions)

if __name__ == "__main__":
    unittest.main()
