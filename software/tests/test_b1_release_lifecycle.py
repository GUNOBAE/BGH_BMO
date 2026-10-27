"""Production B1 host + unchanged controller lifecycle, with no device I/O."""
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.motion.legacy_host import (
    ACTIVE_BUDGET_S, LegacyArmError, LegacyArmLink, LegacyMotionFeature,
)
from test_legacy_arm_host import Clock, FakePort, core


class B1ReleaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("process forbidden")))
        self.enterContext(patch("os.open", side_effect=AssertionError("device forbidden")))
        self.clock, self.moves = Clock(), []
        self.controller = core.LegacyBridge(None, "bootA", self.move,
            lambda angle: self.move("both", angle), lambda: 0, clock=self.clock)
        self.controller.enabled = True
        self.port = FakePort(self.controller)
        self.link = LegacyArmLink(self.port, motor_permission=lambda: True,
                                  sequence_clock=lambda: 1000)
        self.motion = LegacyMotionFeature(self.link, clock=self.clock, cooldown=0)
        self.motion.set_context("session", "turn", 1)

    def move(self, side, angle):
        self.moves.append((self.clock(), side, angle))

    def queue(self, request="gesture", *, explicit=False):
        result = self.motion.submit("wave", request_id=request, session_id="session",
            turn_id="turn", mode_generation=1, started_at=self.clock(), explicit=explicit)
        self.assertEqual(result.status, "accepted")

    def frames(self, verb):
        return [frame for frame in self.port.sent if frame.startswith(b"B1 " + verb + b" ")]

    def during_do_ack(self, callback):
        original = self.port.read_until
        def read(*args):
            ack = original(*args)
            if self.port.sent[-1].startswith(b"B1 DO "):
                callback()
            return ack
        self.port.read_until = read

    def test_cancel_during_accepted_do_ack_holds_without_resurrecting_active(self):
        self.queue()
        self.during_do_ack(self.motion.cancel)
        event, = self.motion.poll()
        self.assertEqual((event.code, event.data["status"]), ("obsolete_motion_held", "cancelled"))
        self.assertTrue(event.data["executed"])  # Controller accepted DO before cancel.
        self.assertFalse(event.data["completed"])
        self.assertIsNone(self.motion.active_until)
        self.assertFalse(self.controller.steps)
        self.assertEqual(len(self.frames(b"DO")), 1)
        self.assertEqual(len(self.frames(b"HOLD")), 1)
        self.assertEqual(len(self.frames(b"PING")), 0)
        self.assertEqual(self.motion.poll(), ())
        self.assertFalse(self.moves)

    def test_queued_cancel_has_one_terminal_not_started_receipt_and_zero_io(self):
        self.queue()
        self.motion.cancel()
        self.motion.cancel()
        event, = self.motion.poll()
        self.assertEqual((event.request_id, event.data["status"]), ("gesture", "cancelled"))
        self.assertEqual(event.data["completion"], "not_started")
        self.assertFalse(event.data["executed"])
        self.assertFalse(self.port.sent)
        self.assertEqual(self.motion.poll(), ())

    def test_queued_context_change_finishes_old_generation_without_dispatch(self):
        self.queue()
        self.motion.set_context("session", "next", 2)
        event, = self.motion.poll()
        self.assertEqual((event.request_id, event.mode_generation, event.data["status"]),
                         ("gesture", 1, "cancelled"))
        self.assertEqual(event.data["completion"], "not_started")
        self.assertFalse(self.port.sent)

    def test_cancel_inside_permission_guard_has_terminal_zero_do_receipt(self):
        self.queue()
        def permission():
            self.motion.cancel()
            return True
        self.link.motor_permission = permission
        event, = self.motion.poll()
        self.assertEqual(event.data["status"], "cancelled")
        self.assertEqual(event.data["completion"], "not_started")
        self.assertFalse(self.frames(b"DO"))
        self.assertIsNone(self.motion.fault)

    def test_cancel_then_lost_do_ack_never_claims_a_zero_write_cancel(self):
        self.queue()
        original = self.port.read_until
        def read(*args):
            if self.port.sent[-1].startswith(b"B1 DO "):
                self.motion.cancel()
                return b""
            return original(*args)
        self.port.read_until = read
        event, = self.motion.poll()
        self.assertEqual((event.data["status"], event.data["completion"]),
                         ("unknown", "position_unverified"))
        self.assertEqual(len(self.frames(b"DO")), 1)
        self.assertEqual(self.motion.poll(), ())

    def test_new_turn_during_accepted_do_ack_keeps_old_generation_receipt(self):
        self.queue()
        self.during_do_ack(lambda: self.motion.set_context("session", "next", 2))
        event, = self.motion.poll()
        self.assertEqual((event.request_id, event.mode_generation), ("gesture", 1))
        self.assertEqual(self.motion.context, ("session", "next", 2))
        self.assertIsNone(self.motion.active_until)
        self.assertFalse(self.controller.steps)

    def test_expiry_while_do_ack_waits_is_held_and_not_claimed_zero_write(self):
        self.queue()
        self.during_do_ack(lambda: self.clock.advance(1.01))
        event, = self.motion.poll()
        self.assertEqual(event.code, "obsolete_motion_held")
        self.assertTrue(event.data["executed"])
        self.assertEqual(len(self.frames(b"DO")), 1)
        self.assertEqual(len(self.frames(b"HOLD")), 1)
        self.assertFalse(self.controller.steps)
        self.assertIsNone(self.motion.fault)

    def test_obsolete_accepted_do_with_lost_hold_ack_is_unknown_never_retried(self):
        self.queue()
        original = self.port.read_until
        def read(*args):
            ack = original(*args)
            if self.port.sent[-1].startswith(b"B1 DO "):
                self.motion.cancel()
            if self.port.sent[-1].startswith(b"B1 HOLD "):
                return b""
            return ack
        self.port.read_until = read
        event, = self.motion.poll()
        self.assertEqual(event.data["status"], "unknown")
        self.assertTrue(event.data["executed"])
        self.assertEqual(self.motion.snapshot()["last_error_code"], "ack_missing_or_incomplete")
        self.assertEqual(self.motion.poll(), ())
        self.assertEqual(len(self.frames(b"DO")), 1)
        self.assertEqual(len(self.frames(b"HOLD")), 1)

    def test_new_turn_queued_during_old_ack_cannot_inherit_old_transport_failure(self):
        self.queue()
        original = self.port.read_until
        def read(*args):
            ack = original(*args)
            if self.port.sent[-1].startswith(b"B1 DO "):
                self.motion.set_context("session", "next", 2)
                result = self.motion.submit("shrug", request_id="replacement", session_id="session",
                    turn_id="next", mode_generation=2, started_at=self.clock())
                self.assertEqual(result.status, "accepted")
            if self.port.sent[-1].startswith(b"B1 HOLD "):
                return b""
            return ack
        self.port.read_until = read
        old, new = self.motion.poll()
        self.assertEqual((old.request_id, old.mode_generation, old.data["status"], old.data["executed"]),
                         ("gesture", 1, "unknown", True))
        self.assertEqual((new.request_id, new.mode_generation, new.data["status"], new.data["executed"]),
                         ("replacement", 2, "cancelled", False))
        self.assertEqual(new.data["completion"], "not_started")
        self.assertIsNone(self.motion.pending)
        self.assertEqual(len(self.frames(b"DO")), 1)

    def test_targeted_cancel_for_other_request_does_not_interrupt_current(self):
        self.queue()
        self.motion.poll()
        self.motion.cancel("unrelated")
        self.assertIsNotNone(self.motion.active_until)
        self.assertTrue(self.controller.steps)
        self.assertEqual(self.frames(b"HOLD"), [])
        self.motion.cancel("gesture")
        event, = self.motion.poll()
        self.assertEqual((event.request_id, event.data["status"]), ("gesture", "cancelled"))
        self.assertFalse(self.controller.steps)

    def test_one_automatic_motion_per_turn_even_with_new_request_ids_after_cancel(self):
        self.queue()
        self.motion.cancel()
        result = self.motion.submit("celebrate", request_id="different", session_id="session",
            turn_id="turn", mode_generation=1, started_at=self.clock())
        self.assertEqual((result.status, result.code), ("rejected", "turn_gesture_limit"))
        self.assertFalse(self.port.sent)
        self.motion.set_context("session", "next", 2)
        self.assertEqual(self.motion.submit("celebrate", request_id="nextgesture", session_id="session",
            turn_id="next", mode_generation=2, started_at=self.clock()).status, "accepted")

    def test_receipt_ledger_has_fixed_bound_without_eviction_or_replay(self):
        with patch("bmo_ai.features.motion.legacy_host.LEDGER_LIMIT", 2):
            self.queue("first", explicit=True)
            self.motion.cancel()
            self.queue("second", explicit=True)
            self.motion.cancel()
            result = self.motion.submit("wave", request_id="third", session_id="session",
                turn_id="turn", mode_generation=1, started_at=self.clock(), explicit=True)
            self.assertEqual(result.code, "motion_ledger_full")
            self.assertEqual(self.motion.seen, {"first", "second"})
            self.assertFalse(self.port.sent)

    def test_lost_do_ack_does_not_repeat_and_firmware_watchdog_latches(self):
        self.queue()
        original = self.port.read_until
        self.port.read_until = lambda *args: b"" if self.port.sent[-1].startswith(b"B1 DO ") else original(*args)
        event, = self.motion.poll()
        self.assertEqual(event.data["status"], "unknown")
        self.assertTrue(self.link.unknown)
        count = len(self.port.sent)
        for _ in range(3):
            self.motion.poll()
        self.assertEqual(len(self.port.sent), count)
        self.assertEqual(len(self.frames(b"DO")), 1)
        self.clock.advance(core.WATCHDOG_S + .01)
        self.controller.poll()
        self.assertTrue(self.controller.stopped)
        self.assertEqual(self.controller.fault, "watchdog_timeout")
        self.assertFalse(self.moves)

    def test_controller_reboot_during_heartbeat_drops_motion_without_rehello(self):
        self.queue()
        self.motion.poll()
        replacement = core.LegacyBridge(None, "bootB", self.move,
            lambda angle: self.move("both", angle), lambda: 0, clock=self.clock)
        replacement.enabled = True
        self.port.controller = replacement
        self.clock.advance(.15)
        event, = self.motion.poll()
        self.assertEqual(event.data["status"], "unknown")
        self.assertEqual(self.motion.snapshot()["last_error_code"], "boot_changed")
        self.assertTrue(self.link.unknown)
        self.assertEqual(len(self.frames(b"HELLO")), 1)
        self.assertEqual(len(self.frames(b"DO")), 1)
        self.assertFalse(replacement.steps)

    def test_manual_gp7_override_before_do_latches_host_without_motion(self):
        self.queue()
        original = self.port.write
        def write(frame):
            if frame.startswith(b"B1 DO "):
                self.controller.manual_cancel()
                self.controller.manual_pose(180)  # Original firmware already did manual PWM.
            return original(frame)
        self.port.write = write
        event, = self.motion.poll()
        self.assertEqual(event.data["status"], "unknown")
        self.assertEqual(self.motion.snapshot()["last_error_code"], "gesture_stopped")
        self.assertFalse(self.controller.steps)
        self.assertEqual(self.controller.positions, {"left": 180, "right": 180})
        self.assertEqual(self.frames(b"PING"), [])
        self.assertEqual(self.moves, [])

    def test_manual_gp7_override_during_active_heartbeat_is_not_rearmed(self):
        self.queue()
        self.motion.poll()
        self.controller.manual_cancel()
        self.controller.manual_pose(180)
        self.clock.advance(.15)
        event, = self.motion.poll()
        self.assertEqual(event.data["status"], "unknown")
        self.assertEqual(self.motion.snapshot()["last_error_code"], "heartbeat_failed")
        self.assertTrue(self.controller.stopped)
        self.assertIsNone(self.motion.active_until)
        self.assertEqual(len(self.frames(b"DO")), 1)
        self.assertEqual(self.motion.poll(), ())

    def test_software_budget_hold_receipt_does_not_claim_physical_completion(self):
        self.queue()
        self.motion.poll()
        self.clock.advance(ACTIVE_BUDGET_S)
        event, = self.motion.poll()
        self.assertEqual((event.code, event.data["status"]), ("gesture_budget_held", "unknown"))
        self.assertFalse(event.data["completed"])
        self.assertFalse(event.data["position_verified"])
        self.assertIsNone(self.motion.active_until)
        self.assertFalse(self.controller.steps)

    def test_disabled_handshake_never_queues_do_or_toggles_controller_flag(self):
        self.controller.enabled = False
        self.queue()
        event, = self.motion.poll()
        self.assertEqual(event.data["status"], "unknown")
        self.assertEqual(self.motion.snapshot()["last_error_code"], "firmware_not_ready_disabled")
        self.assertFalse(self.controller.enabled)
        self.assertEqual(self.frames(b"DO"), [])

    def test_successful_close_sends_one_hold_stop_closes_port_once(self):
        self.queue()
        self.motion.poll()
        close = self.port.close
        calls = []
        def closed():
            calls.append(1)
            close()
        self.port.close = closed
        self.motion.close()
        self.motion.close()
        self.link.close()
        self.assertEqual((len(self.frames(b"HOLD")), len(self.frames(b"STOP")), calls), (1, 1, [1]))
        self.assertEqual(self.motion.cleanup_snapshot(), {"resources_cleaned": True,
            "port_closed": True, "code": "resources_cleaned", "physical_release_verified": False})
        count = len(self.port.sent)
        with self.assertRaises(LegacyArmError):
            self.link.ping()
        self.assertEqual(count, len(self.port.sent))

    def test_port_close_failure_is_sanitized_retryable_and_not_clean(self):
        self.port.close = lambda: (_ for _ in ()).throw(OSError("private device path or detail"))
        with self.assertRaises(FeatureError) as caught:
            self.motion.close()
        self.assertEqual(caught.exception.code, "arm_cleanup_failed")
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse(self.motion.cleanup_snapshot()["resources_cleaned"])
        self.assertEqual(self.motion.snapshot()["last_error_code"], "port_close_failed")
        self.port.close = lambda: None
        self.motion.close()
        self.assertTrue(self.motion.cleanup_snapshot()["resources_cleaned"])

    def test_negative_generation_cannot_escape_later_as_invalid_event(self):
        with self.assertRaises(FeatureError):
            self.motion.set_context("session", "turn", -1)
        with self.assertRaises(FeatureError):
            self.motion.set_context("session", -1, 1)
        self.assertFalse(self.port.sent)


if __name__ == "__main__":
    unittest.main()
