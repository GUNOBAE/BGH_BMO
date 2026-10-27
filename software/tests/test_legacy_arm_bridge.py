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
        self.motion_times = []
        self.pose = [0]
        def moved(side, angle):
            self.motions.append((side, angle))
            self.motion_times.append(self.clock())
        self.bridge = LegacyBridge(self.port, "bA1",
            moved, lambda a: moved("both", a),
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

    def test_circuitpython_style_buffer_without_clear_handles_consecutive_disabled_frames(self):
        # CircuitPython has no bytearray.clear. Using a fake that rejects it
        # prevents a CPython-only pass from hiding a real CDC disconnect.
        class CircuitPythonBytearray(bytearray):
            def clear(self):
                raise AssertionError("unsupported bytearray.clear on CircuitPython")

        self.bridge.enabled = False
        self.bridge.data = CircuitPythonBytearray()
        self.port.buffer += b"B1 HELLO first\nB1 HELLO second\n"
        self.bridge.poll()
        self.assertEqual(len(self.port.writes), 2)
        self.assertTrue(all(b" DISABLED " in ack for ack in self.port.writes))
        self.assertIs(self.bridge.port, self.port)
        self.assertIsNone(self.bridge.fault)
        self.assertIn("DISABLED", self.send("B1 HELLO third\n"))
        self.assertEqual(len(self.port.writes), 3)
        self.assertFalse(self.motions)

    def test_circuitpython_style_buffer_without_clear_recovers_overflow_and_cr(self):
        class CircuitPythonBytearray(bytearray):
            def clear(self):
                raise AssertionError("unsupported bytearray.clear on CircuitPython")

        self.bridge.enabled = False
        self.bridge.data = CircuitPythonBytearray()
        self.port.buffer = b"X" * (module.MAX_FRAME + 50) + b"\n"
        for _ in range(8):
            self.bridge.poll()
            if not self.port.in_waiting:
                break
        self.assertEqual(self.port.writes, [])
        self.assertIsNone(self.bridge.fault)
        self.assertIs(self.bridge.port, self.port)
        self.bridge.data = CircuitPythonBytearray()
        self.port.buffer = b"BAD\r\nB1 HELLO recovered\n"
        self.bridge.poll()
        self.assertEqual(len(self.port.writes), 1)
        self.assertIn(b" DISABLED ", self.port.writes[0])
        self.assertIsNone(self.bridge.fault)
        self.assertFalse(self.motions)

    def test_wave_only_moves_right_with_original_range_and_restores_pose(self):
        self.assertIn("ACCEPTED", self.send("B1 DO bmo_00000001 bA1 wave\n"))
        self.assertEqual(self.motions, [])  # Admission cannot jump to an endpoint.
        for i in range(700):
            self.clock.advance(.02)
            if i % 5 == 0:
                self.send("B1 PING p%d bA1\n" % i)
            self.bridge.poll()
            if not self.bridge.steps:
                break
        self.assertFalse(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)
        self.assertGreater(len(self.motions), 200)
        self.assertTrue(all(side == "right" and 0 <= angle <= 180
                            for side, angle in self.motions))
        waypoints = iter((180, 145, 180, 145, 0))
        expected = next(waypoints)
        reached = []
        previous, at = 0, 0.
        for (_, angle), stamp in zip(self.motions, self.motion_times):
            self.assertLessEqual(abs(angle - previous), module.MAX_UPDATE_DEG + 1e-8)
            self.assertLessEqual(abs(angle - previous), module.MAX_SLEW_DPS * (stamp - at) + 1e-8)
            if angle == expected:
                reached.append(expected)
                expected = next(waypoints, None)
            previous, at = angle, stamp
        self.assertEqual(reached, [180, 145, 180, 145, 0])

    def test_duplicate_command_replays_ack_without_motor_restart(self):
        cmd = "B1 DO bmo_00000001 bA1 wave\n"
        self.assertEqual(self.send(cmd), self.send(cmd))
        self.assertEqual(self.bridge.step_index, 0)
        self.clock.advance(.02)
        self.bridge.poll()
        position = dict(self.bridge.positions)
        self.assertIn("ACCEPTED", self.send(cmd))
        self.assertEqual(self.bridge.positions, position)
        self.assertIn("REQUEST_CONFLICT", self.send("B1 DO bmo_00000001 bA1 shrug\n"))

    def test_gp7_override_drops_active_steps_and_latches_automation(self):
        self.send("B1 DO bmo_00000001 bA1 wave\n")
        self.bridge.manual_cancel()
        self.assertTrue(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)
        self.assertIn("STOPPED", self.send("B1 DO bmo_00000002 bA1 wave\n"))

    def test_heartbeat_timeout_cancels_and_latches(self):
        self.send("B1 DO bmo_00000001 bA1 wave\n")
        self.clock.advance(1.)
        self.bridge.poll()
        self.assertTrue(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)

    def test_hold_stop_do_not_change_original_gp7_control(self):
        self.send("B1 DO bmo_00000001 bA1 shrug\n")
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

    def test_completed_motion_cannot_replay_after_ping_evicts_cached_ack(self):
        command = "B1 DO bmo_00000001 bA1 both_down\n"
        self.assertIn("ACCEPTED", self.send(command))
        self.clock.advance(.02)
        self.bridge.poll()
        self.assertFalse(self.bridge.steps)
        for i in range(30):
            self.send("B1 PING ping%d bA1\n" % i)
        self.assertNotIn("bmo_00000001", self.bridge.last_requests)
        before = list(self.motions)
        self.assertIn("STALE_REQUEST", self.send(command))
        self.assertFalse(self.bridge.steps)
        self.assertEqual(self.motions, before)
        self.assertEqual(self.bridge.highest_motion_sequence, 1)

    def test_busy_request_cannot_execute_when_cached_rejection_is_evicted(self):
        self.send("B1 DO bmo_00000001 bA1 wave\n")
        rejected = "B1 DO bmo_00000002 bA1 both_up\n"
        self.assertIn("BUSY", self.send(rejected))
        self.send("B1 HOLD holding bA1\n")
        for i in range(30):
            self.send("B1 PING ping%d bA1\n" % i)
        self.assertIn("STALE_REQUEST", self.send(rejected))
        self.assertFalse(self.bridge.steps)
        self.assertEqual(self.motions, [])

    def test_decimal_aliases_cannot_bypass_highwater_and_invalid_ids_do_not_move(self):
        self.send("B1 DO bmo_00000001 bA1 both_down\n")
        self.clock.advance(.02)
        self.bridge.poll()
        for rid in ("bmo_1", "bmo_0001"):
            self.assertIn("STALE_REQUEST", self.send("B1 DO %s bA1 both_up\n" % rid))
        for rid in ("w1", "bmo_0", "bmo_0000", "bmo_123456789012345678901"):
            self.assertIn("INVALID_REQUEST_ID", self.send("B1 DO %s bA1 wave\n" % rid))
        self.assertEqual(self.motions, [])

    def test_stalled_poll_uses_one_small_update_without_catching_up(self):
        self.send("B1 DO bmo_00000001 bA1 both_up\n")
        self.clock.advance(.02)
        self.bridge.poll()
        first = self.motions[-1][1]
        self.clock.advance(.5)
        self.send("B1 PING stillhere bA1\n")
        second = self.motions[-1][1]
        self.assertLessEqual(second - first, module.MAX_UPDATE_DEG)
        self.assertEqual(len(self.motions), 2)
        self.bridge.poll()
        self.assertEqual(len(self.motions), 2)

    def test_pwm_failure_clears_steps_latches_and_never_retries(self):
        self.bridge.set_side = lambda *_: (_ for _ in ()).throw(OSError("PRIVATE_PWM_DETAIL"))
        self.send("B1 DO bmo_00000001 bA1 wave\n")
        self.clock.advance(.02)
        self.bridge.poll()
        self.assertEqual(self.bridge.fault, "servo_fault")
        self.assertTrue(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)
        self.assertIsNone(self.bridge.positions)
        self.assertIn("STOPPED", self.send("B1 DO bmo_00000002 bA1 wave\n"))

    def test_paired_callback_failure_does_not_leave_live_trajectory(self):
        self.bridge.set_both = lambda *_: (_ for _ in ()).throw(OSError("PRIVATE_PAIR"))
        self.send("B1 DO bmo_00000001 bA1 both_up\n")
        self.clock.advance(.02)
        self.bridge.poll()
        self.assertEqual(self.bridge.fault, "servo_fault")
        self.assertTrue(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)
        self.assertIsNone(self.bridge.positions)
        self.assertFalse(self.motions)

    def test_backwards_clock_during_motion_faults_without_another_pwm_write(self):
        self.send("B1 DO bmo_00000001 bA1 wave\n")
        self.clock.advance(.02)
        self.bridge.poll()
        writes = len(self.motions)
        self.clock.now = 0.
        self.bridge.poll()
        self.assertEqual(self.bridge.fault, "clock_fault")
        self.assertTrue(self.bridge.stopped)
        self.assertEqual(len(self.motions), writes)

    def test_partial_cdc_ack_cancels_before_any_pwm_update(self):
        self.port.write = lambda _: 1
        self.send("B1 DO bmo_00000001 bA1 both_up\n")
        self.assertEqual(self.bridge.fault, "cdc_fault")
        self.assertTrue(self.bridge.stopped)
        self.assertFalse(self.bridge.steps)
        self.assertFalse(self.motions)

    def test_get_base_and_clock_failure_are_contained_without_pwm(self):
        self.bridge.get_base = lambda: (_ for _ in ()).throw(OSError("PRIVATE_BASE"))
        self.assertIn("DENIED", self.send("B1 DO bmo_00000001 bA1 wave\n"))
        self.assertTrue(self.bridge.stopped)
        self.assertEqual(self.bridge.fault, "servo_fault")
        self.assertFalse(self.motions)
        self.bridge.stopped = False  # Separate fake fault injection, never a public reset.
        self.bridge.get_base = lambda: 0
        self.bridge.clock = lambda: float("nan")
        self.assertIn("STOPPED", self.send("B1 DO bmo_00000002 bA1 wave\n"))
        self.assertEqual(self.bridge.fault, "clock_fault")
        self.assertFalse(self.motions)

    def test_total_motion_deadline_cancels_even_with_fresh_heartbeats(self):
        self.send("B1 DO bmo_00000001 bA1 wave\n")
        for i in range(31):
            self.clock.advance(.5)
            self.send("B1 PING p%d bA1\n" % i)
        self.assertEqual(self.bridge.fault, "gesture_timeout")
        self.assertFalse(self.bridge.steps)
        self.assertTrue(self.bridge.stopped)

    def test_idle_manual_edge_blocks_already_queued_remote_request(self):
        self.bridge.manual_cancel()
        self.bridge.manual_pose(180)
        self.assertIn("STOPPED", self.send("B1 DO bmo_00000001 bA1 both_down\n"))
        self.assertEqual(self.bridge.positions, {"left": 180, "right": 180})
        self.assertFalse(self.motions)

if __name__ == "__main__":
    unittest.main()
