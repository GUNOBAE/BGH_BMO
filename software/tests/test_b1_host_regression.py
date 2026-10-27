"""Actual B1 codec/host logic against an in-memory port; no device is opened."""
import unittest
from unittest.mock import patch

from bmo_ai.features.motion.legacy_host import (
    ACTIVE_BUDGET_S, LegacyArmError, LegacyArmLink, LegacyMotionFeature,
)
from test_legacy_arm_host import Clock, FakePort, core


class B1DispatchRegressionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("process forbidden")))
        self.enterContext(patch("os.open", side_effect=AssertionError("device forbidden")))
        self.make_fixture()

    def make_fixture(self):
        self.clock = Clock()
        self.moves = []
        controller = core.LegacyBridge(None, "bootA", lambda *a: self.moves.append(a),
            lambda *a: self.moves.append(a), lambda: 0, enabled=False, clock=self.clock)
        controller.enabled = True  # Actual frame parser, no physical port/PWM.
        self.port = FakePort(controller)
        self.link = LegacyArmLink(self.port, motor_permission=lambda: True,
                                  sequence_clock=lambda: 1000)
        self.motion = LegacyMotionFeature(self.link, clock=self.clock, cooldown=0)
        self.motion.set_context("session", "turn", 1)

    def queue(self, *, explicit=False, request="gesture"):
        result = self.motion.submit("wave", request_id=request, session_id="session",
            turn_id="turn", mode_generation=1, started_at=None if explicit else self.clock(),
            explicit=explicit)
        self.assertEqual(result.status, "accepted")

    def assert_stale(self, events):
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].code, "stale_playback_start")
        self.assertEqual(events[0].data["status"], "rejected")
        self.assertFalse(events[0].data["executed"])
        self.assertEqual(events[0].data["completion"], "not_started")
        self.assertIsNone(self.motion.pending)
        self.assertIsNone(self.motion.fault)
        self.assertFalse(self.link.unknown)
        self.assertFalse(any(packet.startswith(b"B1 DO ") for packet in self.port.sent))
        self.assertFalse(self.moves)

    def test_expired_queue_emits_terminal_receipt_without_even_hello(self):
        self.queue()
        self.clock.advance(10)
        self.assert_stale(self.motion.poll())
        self.assertEqual(self.port.sent, [])
        self.assertEqual(self.motion.poll(), ())

    def test_expiry_during_hello_sends_no_do_and_preserves_known_link(self):
        self.queue()
        original = self.port.read_until
        def delayed(*args):
            self.clock.advance(1.01)
            return original(*args)
        self.port.read_until = delayed
        self.assert_stale(self.motion.poll())
        self.assertEqual(len(self.port.sent), 1)
        self.assertTrue(self.port.sent[0].startswith(b"B1 HELLO "))
        self.assertEqual(self.link.boot, "bootA")

    def test_expiry_during_permission_check_is_rechecked_before_write(self):
        self.queue()
        def permission():
            self.clock.advance(1.01)
            return True
        self.link.motor_permission = permission
        self.assert_stale(self.motion.poll())

    def test_expiry_during_id_creation_is_rechecked_before_write(self):
        self.queue()
        calls = []
        def sequence():
            calls.append(1)
            if len(calls) == 2:
                self.clock.advance(1.01)
            return 1000 + len(calls)
        self.link.sequence_clock = sequence
        self.assert_stale(self.motion.poll())
        self.assertEqual(len(calls), 2)

    def test_future_nonfinite_and_backward_queue_times_never_dispatch(self):
        for value in (-.01, float("nan"), float("inf"), True):
            with self.subTest(value=value):
                self.make_fixture()
                self.queue()
                self.clock.t = value
                self.assert_stale(self.motion.poll())

    def test_cancel_while_hello_is_in_flight_does_not_dispatch(self):
        self.queue()
        original = self.port.read_until
        def cancelled(*args):
            self.motion.cancel()
            return original(*args)
        self.port.read_until = cancelled
        self.motion.poll()
        self.assertIsNone(self.motion.pending)
        self.assertFalse(any(packet.startswith(b"B1 DO ") for packet in self.port.sent))
        self.assertIsNone(self.motion.fault)

    def test_context_change_during_final_permission_does_not_dispatch(self):
        self.queue()
        def permission():
            self.motion.set_context("session", "newturn", 2)
            return True
        self.link.motor_permission = permission
        event, = self.motion.poll()
        self.assertEqual((event.request_id, event.data["status"]), ("gesture", "cancelled"))
        self.assertEqual(event.data["completion"], "not_started")
        self.assertFalse(event.data["executed"])
        self.assertIsNone(self.motion.fault)
        self.assertFalse(any(packet.startswith(b"B1 DO ") for packet in self.port.sent))

    def test_explicit_manual_request_keeps_its_existing_nonplayback_contract(self):
        self.queue(explicit=True)
        self.clock.advance(10)
        result, = self.motion.poll()
        self.assertEqual(result.code, "ack_position_unverified")
        self.assertTrue(result.data["executed"])
        self.assertFalse(result.data["completed"])
        self.assertFalse(result.data["position_verified"])

    def test_cancelled_pending_request_is_not_replayed_by_same_request_id(self):
        self.queue()
        self.motion.cancel()
        result = self.motion.submit("wave", request_id="gesture", session_id="session",
            turn_id="turn", mode_generation=1, started_at=0)
        self.assertEqual(result.status, "rejected")
        event, = self.motion.poll()
        self.assertEqual((event.code, event.data["status"]), ("gesture_cancelled", "cancelled"))
        self.assertFalse(event.data["executed"])
        self.assertEqual(self.motion.poll(), ())
        self.assertEqual(self.port.sent, [])

    def test_valid_dispatch_heartbeats_through_slew_budget_then_holds(self):
        self.queue()
        self.motion.poll()
        self.assertEqual(self.motion.active_until, ACTIVE_BUDGET_S)
        for _ in range(40):
            self.clock.advance(.15)
            self.motion.poll()
        self.assertIsNotNone(self.motion.active_until)
        self.assertGreater(sum(packet.startswith(b"B1 PING ") for packet in self.port.sent), 20)
        self.clock.t = ACTIVE_BUDGET_S
        self.motion.poll()
        self.assertIsNone(self.motion.active_until)
        self.assertTrue(self.port.sent[-1].startswith(b"B1 HOLD "))
        self.assertEqual(sum(packet.startswith(b"B1 DO ") for packet in self.port.sent), 1)

    def test_nonfinite_start_and_clock_are_rejected_at_admission(self):
        for value in (float("nan"), float("inf"), True, -1):
            with self.subTest(value=value):
                result = self.motion.submit("wave", request_id="bad", session_id="session",
                    turn_id="turn", mode_generation=1, started_at=value)
                self.assertEqual(result.status, "rejected")
        self.clock.t = float("nan")
        result = self.motion.submit("wave", request_id="badclock", session_id="session",
            turn_id="turn", mode_generation=1, started_at=0)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(self.port.sent, [])

    def test_link_sequences_increase_across_host_instances_in_same_epoch(self):
        with patch("bmo_ai.features.motion.legacy_host.time.monotonic_ns", side_effect=(1000, 2000)):
            other = LegacyArmLink(self.port)
            other.hello()
            first = int(self.port.sent[-1].split()[2][4:])
            recreated = LegacyArmLink(self.port)
            recreated.hello()
            second = int(self.port.sent[-1].split()[2][4:])
        self.assertGreater(second, first)

    def test_same_clock_tick_still_produces_unique_ordered_request_ids(self):
        self.link.hello()
        self.link.ping()
        self.link.ping()
        ids = [int(packet.split()[2][4:]) for packet in self.port.sent]
        self.assertEqual(ids, [1000, 1001, 1002])

    def test_ttl_boundary_is_allowed_and_active_budget_anchors_after_hello(self):
        self.queue()
        original = self.port.read_until
        def delayed(*args):
            if self.port.sent[-1].startswith(b"B1 HELLO "):
                self.clock.advance(1.)
            return original(*args)
        self.port.read_until = delayed
        event, = self.motion.poll()
        self.assertEqual(event.code, "ack_position_unverified")
        self.assertEqual(self.motion.active_until, 1. + ACTIVE_BUDGET_S)

    def test_clock_failure_while_pending_is_a_definite_no_write_expiry(self):
        self.queue()
        def broken_clock():
            raise RuntimeError("private error must not escape")
        self.motion.clock = broken_clock
        self.assert_stale(self.motion.poll())

    def test_invalid_sequence_clock_fails_closed_before_port_write(self):
        for value in (True, -1, float("nan"), "100", 10 ** 20):
            with self.subTest(value=value):
                link = LegacyArmLink(self.port, sequence_clock=lambda: value)
                with self.assertRaises(LegacyArmError):
                    link.hello()
                self.assertTrue(link.unknown)
        self.assertEqual(self.port.sent, [])


if __name__ == "__main__":
    unittest.main()
