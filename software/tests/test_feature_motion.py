"""Pure fake transport/scheduler tests; no port, GPIO, motor or firmware IO."""
import copy
import json
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.motion import FakeMotionTransport, MotionFeature, load_profile
from bmo_ai.features.motion.protocol import MotionCommand


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def profile():
    return {"version": 1, "verified": True, "safe_stop_verified": True,
            "left": {"min_degrees": 20, "neutral_degrees": 80, "max_degrees": 130, "direction": 1},
            "right": {"min_degrees": 30, "neutral_degrees": 95, "max_degrees": 150, "direction": -1}}


class FeatureMotionTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen", "os.open"):
            self.enterContext(patch(target, side_effect=AssertionError("Hardware/file IO forbidden")))
        self.clock = Clock()

    def feature(self, *, transport=None, **kwargs):
        kwargs.setdefault("cooldown", 0)
        f = MotionFeature(clock=self.clock, transport=transport, **kwargs)
        f.set_context("session", "turn1", 1)
        self.addCleanup(f.close)
        return f

    def submit(self, f, gesture="wave", request_id="req1", **kwargs):
        settings = dict(request_id=request_id, session_id="session", turn_id="turn1",
                        mode_generation=1, started_at=self.clock.now)
        settings.update(kwargs)
        return f.submit(gesture, **settings)

    def test_no_gesture_has_no_dispatch_and_happy_is_not_a_gesture(self):
        f = self.feature()
        self.assertEqual(self.submit(f, "none").code, "no_gesture")
        self.assertEqual(self.submit(f, "happy", "req2").code, "invalid_gesture")
        f.poll()
        self.assertEqual(f.transport.dispatch_count, 0)

    def test_accepted_running_ack_never_claims_physical_position_or_execution(self):
        f = self.feature()
        result = self.submit(f)
        self.assertEqual(result.status, "accepted")
        sent = f.poll()
        self.assertEqual(sent[-1].code, "command_sent")
        ack = f.poll()[-1]
        self.assertEqual((ack.data["status"], ack.code), ("simulated", "ack_position_unverified"))
        self.assertFalse(ack.data["executed"])
        self.assertFalse(ack.data["completed"])
        self.assertFalse(ack.data["position_verified"])
        self.assertEqual(ack.data["completion"], "position_unverified")
        self.assertEqual(f.transport.dispatch_count, 1)

    def test_duplicate_request_is_not_dispatched_twice_and_conflict_is_rejected(self):
        f = self.feature()
        first = self.submit(f)
        self.assertEqual(self.submit(f), first)
        f.poll(); f.poll()
        self.assertEqual(self.submit(f).code, "ack_position_unverified")
        self.assertEqual(self.submit(f, "shrug").code, "request_conflict")
        self.assertEqual(f.transport.dispatch_count, 1)

    def test_model_angles_pwm_paths_and_command_objects_are_rejected(self):
        f = self.feature()
        for index, value in enumerate(({}, {"angle": 90}, {"pwm": 1500}, "90", "../wave", "servo.move(90)", None)):
            with self.subTest(index=index):
                result = self.submit(f, value, "bad" + str(index))
                self.assertEqual(result.code, "invalid_gesture")
        self.assertEqual(f.transport.dispatch_count, 0)

    def test_current_context_and_player_started_timestamp_are_required(self):
        f = self.feature()
        self.assertEqual(self.submit(f, request_id="missing", started_at=None).code, "stale_playback_start")
        self.assertEqual(self.submit(f, request_id="future", started_at=1).code, "stale_playback_start")
        self.assertEqual(self.submit(f, request_id="stale", started_at=-2).code, "stale_playback_start")
        self.assertEqual(self.submit(f, request_id="oldturn", turn_id="turn0").code, "stale_context")
        self.assertEqual(self.submit(f, request_id="oldgen", mode_generation=0).code, "stale_context")
        self.assertEqual(self.submit(f, request_id="oldsession", session_id="older").code, "stale_context")
        self.assertEqual(self.submit(f, request_id="explicit", started_at=None, explicit=True).status, "accepted")

    def test_only_one_automatic_gesture_per_turn(self):
        f = self.feature()
        self.assertEqual(self.submit(f).status, "accepted")
        self.assertEqual(self.submit(f, "celebrate", "req2").code, "turn_gesture_limit")
        f.poll(); f.poll()
        self.assertEqual(f.transport.dispatch_count, 1)

    def test_queue_is_bounded_and_old_automatic_decoration_expires_before_dispatch(self):
        f = self.feature(queue_limit=2)
        self.assertEqual(self.submit(f, request_id="req1", explicit=True, started_at=None).status, "accepted")
        self.assertEqual(self.submit(f, request_id="req2", explicit=True, started_at=None).status, "accepted")
        self.assertEqual(self.submit(f, request_id="req3", explicit=True).code, "gesture_queue_full")
        f.poll(); f.poll(); f.poll()
        self.assertEqual(f.transport.dispatch_count, 2)
        f.set_context("session", "turn2", 2)
        self.assertEqual(self.submit(f, request_id="expires", turn_id="turn2", mode_generation=2).status, "accepted")
        self.clock.now = 2
        self.assertEqual(f.poll()[-1].code, "stale_gesture")
        self.assertEqual(f.transport.dispatch_count, 2)

    def test_cooldown_does_not_release_queued_old_turns_as_a_burst(self):
        f = self.feature(cooldown=2)
        self.submit(f); f.poll(); f.poll()
        f.set_context("session", "turn2", 2)
        self.assertEqual(self.submit(f, request_id="req2", turn_id="turn2", mode_generation=2).code,
                         "gesture_cooldown")
        self.clock.now = 2.01
        f.set_context("session", "turn3", 3)
        self.assertEqual(self.submit(f, request_id="req3", turn_id="turn3", mode_generation=3).status, "accepted")

    def test_explicit_queued_command_without_speech_also_has_a_finite_ttl(self):
        f = self.feature(transport=FakeMotionTransport(clock=self.clock, auto_ack=False))
        self.submit(f, explicit=True, started_at=None); f.poll()
        self.submit(f, request_id="queued", explicit=True, started_at=None)
        self.clock.now = 2
        events = f.poll()
        self.assertIn("stale_gesture", [event.code for event in events])
        self.assertEqual(f.transport.dispatch_count, 1)

    def test_context_change_cancels_sent_command_as_unknown_and_drops_old_queue(self):
        transport = FakeMotionTransport(clock=self.clock, auto_ack=False)
        f = self.feature(transport=transport)
        self.submit(f, explicit=True); f.poll()
        self.submit(f, request_id="queued", explicit=True)
        f.set_context("session", "turn2", 2)
        events = f.poll()
        self.assertIn("cancelled_position_unknown", [event.code for event in events])
        self.assertIn("gesture_cancelled", [event.code for event in events])
        self.assertEqual(transport.dispatch_count, 1)
        self.assertFalse(f.snapshot()["active"])

    def test_explicit_stop_is_immediate_and_blocks_late_same_turn_decoration(self):
        f = self.feature(transport=FakeMotionTransport(clock=self.clock, auto_ack=False))
        self.submit(f, explicit=True); f.poll()
        self.submit(f, request_id="queued", explicit=True)
        f.stop()
        self.assertTrue(f.snapshot()["stopped"])
        self.assertEqual(f.snapshot()["queued"], 0)
        self.assertEqual(self.submit(f, request_id="late").code, "motion_stopped")
        self.assertEqual(f.transport.stop_count, 1)
        f.set_context("session", "turn2", 2)
        self.assertFalse(f.snapshot()["stopped"])

    def test_ack_timeout_remains_unknown_after_reconnect_and_is_never_retried(self):
        transport = FakeMotionTransport(clock=self.clock, auto_ack=False)
        f = self.feature(transport=transport)
        self.submit(f); f.poll()
        self.clock.now = .51
        self.assertEqual(f.poll()[-1].code, "ack_timeout_unknown")
        transport.disconnect(); transport.reconnect()
        self.assertEqual(self.submit(f, started_at=0).status, "unknown")
        f.poll()
        self.assertEqual(transport.dispatch_count, 1)

    def test_late_ack_and_wrong_boot_or_request_ack_cannot_complete_another_command(self):
        transport = FakeMotionTransport(clock=self.clock, auto_ack=False)
        f = self.feature(transport=transport)
        self.submit(f); f.poll()
        transport.inject_ack("unrelated")
        transport.inject_ack("req1", controller_boot="old_boot")
        f.poll()
        self.assertTrue(f.snapshot()["active"])
        self.assertEqual(f.snapshot()["rejected_acks"], 2)
        self.clock.now = .6
        transport.inject_ack("req1")
        events = f.poll()
        self.assertIn("ack_timeout_unknown", [event.code for event in events])
        self.assertNotIn("ack_position_unverified", [event.code for event in events])

    def test_disconnect_and_reboot_report_unknown_without_replaying_queued_work(self):
        for reboot in (False, True):
            with self.subTest(reboot=reboot):
                transport = FakeMotionTransport(clock=self.clock, auto_ack=False)
                f = self.feature(transport=transport)
                self.submit(f, explicit=True); f.poll()
                self.submit(f, request_id="queued", explicit=True)
                if reboot:
                    transport.reconnect(controller_boot="fixture_boot_2")
                else:
                    transport.disconnect()
                events = f.poll()
                self.assertTrue(any(event.data.get("status") == "unknown" for event in events))
                self.assertEqual(transport.dispatch_count, 1)
                self.assertEqual(f.snapshot()["queued"], 0)

    def test_protocol_must_be_versioned_dedicated_data_and_deduplicating(self):
        for settings in ({"version": 2}, {"channel": "repl_console"}, {"dedup": False}):
            with self.subTest(settings=settings):
                transport = FakeMotionTransport(clock=self.clock, **settings)
                f = self.feature(transport=transport)
                self.submit(f)
                self.assertEqual(f.poll()[-1].code, "protocol_unverified")
                self.assertEqual(transport.dispatch_count, 0)

    def test_fake_controller_deduplicates_delivered_commands_and_rejects_conflicts(self):
        transport = FakeMotionTransport(clock=self.clock)
        command = MotionCommand("req1", "wave", "session", "turn1", 1, "fixture_boot_1")
        transport.send(command); transport.send(command)
        self.assertEqual(transport.dispatch_count, 1)
        with self.assertRaises(FeatureError):
            transport.send(MotionCommand("req1", "shrug", "session", "turn1", 1, "fixture_boot_1"))

    def test_even_verified_profile_cannot_enable_real_transport_or_call_unknown_backend(self):
        class ForbiddenTransport:
            def handshake(self):
                raise AssertionError("A real transport must not be touched")

        for kwargs in ({"simulated": False, "profile": profile()},
                       {"transport": ForbiddenTransport(), "profile": profile()}):
            with self.subTest(keys=tuple(kwargs)):
                f = self.feature(**kwargs)
                self.assertTrue(f.snapshot()["calibration_ready"])
                self.assertFalse(f.snapshot()["real_enabled"])
                self.assertEqual(self.submit(f).code, "real_disabled")
                f.poll(); f.close()

    def test_calibration_requires_both_independent_bounds_direction_and_stop_review(self):
        self.assertTrue(load_profile(json.dumps(profile())).reviewed)
        no_stop = profile(); no_stop["safe_stop_verified"] = False
        self.assertFalse(load_profile(no_stop).reviewed)
        invalid = []
        for name, value in (("min_degrees", -1), ("neutral_degrees", 181),
                            ("max_degrees", 10), ("direction", True), ("direction", 0)):
            bad = copy.deepcopy(profile()); bad["left"][name] = value; invalid.append(bad)
        bad = profile(); del bad["right"]; invalid.append(bad)
        bad = profile(); bad["right"]["pwm"] = 1500; invalid.append(bad)
        invalid.extend(("/private/path.json", '{"version":1,"version":1}', b"\xff"))
        for index, value in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(FeatureError) as caught:
                load_profile(value)
            self.assertNotIn("private", str(caught.exception))

    def test_bounded_host_ledger_does_not_evict_unknown_ids_for_reexecution(self):
        f = self.feature(ledger_limit=2)
        self.submit(f, "none", "req1"); self.submit(f, "none", "req2")
        self.assertEqual(self.submit(f, "none", "req3").code, "motion_ledger_full")
        self.assertEqual(self.submit(f, "none", "req1").code, "no_gesture")

    def test_transport_internal_exception_is_sanitized(self):
        class BrokenFake(FakeMotionTransport):
            def send(self, command):
                raise RuntimeError("private payload must not leak")

        f = self.feature(transport=BrokenFake(clock=self.clock))
        self.submit(f)
        events = f.poll()
        self.assertEqual(events[-1].code, "controller_error_unknown")
        self.assertNotIn("private", repr(events))

    def test_close_discards_pending_work_and_invalid_settings_fail_safely(self):
        f = self.feature()
        self.submit(f); f.close()
        self.assertEqual(self.submit(f, request_id="late").code, "motion_stopped")
        self.assertEqual(f.transport.dispatch_count, 0)
        for kwargs in ({"ttl": 0}, {"ack_timeout": float("nan")}, {"queue_limit": True}, {"cooldown": -1}):
            with self.subTest(kwargs=tuple(kwargs)), self.assertRaises(FeatureError):
                self.feature(**kwargs)


if __name__ == "__main__":
    unittest.main()
