"""Separate installed board, launcher and current player-start evidence."""
import json
import unittest

from bmo_ai.features.motion.legacy_host import LegacyArmLink, LegacyMotionFeature
from bmo_ai.features.motion.runtime_status import conversation_arm_status, STOP_SEMANTICS
from test_legacy_arm_host import Clock, FakePort, core


class ArmRuntimeStatusTests(unittest.TestCase):
    def status(self, **overrides):
        options = dict(simulate=False, supervised_requested=True, supervised_effective=True,
                       emotion_arms_requested=True, emotion_arms_effective=True,
                       motion=dict(real_enabled=True, transport=dict(status="NOT_CONNECTED")))
        options.update(overrides)
        return conversation_arm_status(**options)

    def test_board_protocol_is_not_static_or_launcher_permission(self):
        result = self.status(supervised_requested=False, supervised_effective=False,
                             emotion_arms_requested=False, emotion_arms_effective=False,
                             motion=dict(real_enabled=False, transport=dict(last_response_code="READY")))
        self.assertIsNone(result["board"]["remote_enabled_static"])
        self.assertTrue(result["board"]["remote_enabled_at_last_ack"])
        self.assertFalse(result["launcher"]["supervised_effective"])
        self.assertIn("arm_runtime_off", result["conversation"]["blocked_reasons"])
        self.assertIn("emotion_arms_off", result["conversation"]["blocked_reasons"])
        self.assertFalse(result["conversation"]["eligible_at_player_start"])
        self.assertFalse(result["board"]["physical_verified"])

    def test_current_plan_waits_for_actual_start_and_keeps_deferred_hello(self):
        turn = dict(turn_id="session:2", stage="planned", gesture_id="wave",
                    plan_emotion="happy", player_start_seen=False)
        before = self.status(turn_state=turn)["conversation"]
        self.assertEqual(before["blocked_reasons"], ["awaiting_player_start"])
        turn.update(stage="player_started", playback_emotion="happy",
                    player_start_seen=True, player_start_fresh=True)
        at_start = self.status(turn_state=turn)["conversation"]
        self.assertEqual(at_start["blocked_reasons"], [])
        self.assertTrue(at_start["eligible_at_player_start"])
        self.assertEqual(at_start["gesture_id"], "wave")
        turn.update(stage="submitted", result_status="accepted", result_code="gesture_queued")
        self.assertEqual(self.status(turn_state=turn)["conversation"]["result_code"], "gesture_queued")
        # Terminal/cancelled or stale delivery cannot masquerade as a new start.
        for stage, code in (("ended", "turn_ended"), ("cancelled", "turn_cancelled"),
                            ("error", "turn_error"), ("stale", "stale_playback_start")):
            with self.subTest(stage=stage):
                turn["stage"] = stage
                value = self.status(turn_state=turn)["conversation"]
                self.assertIn(code, value["blocked_reasons"])
                self.assertFalse(value["eligible_at_player_start"])

    def test_no_plan_and_adapter_rejection_stay_distinct(self):
        self.assertIn("current_turn_no_gesture", self.status()["conversation"]["blocked_reasons"])
        turn = dict(turn_id="session:3", stage="submitted", gesture_id="shrug",
                    player_start_seen=True, player_start_fresh=True,
                    result_status="rejected", result_code="gesture_cooldown")
        value = self.status(turn_state=turn)["conversation"]
        self.assertEqual(value["blocked_reasons"], ["gesture_cooldown"])
        self.assertFalse(value["eligible_at_player_start"])

    def test_setup_failure_keeps_requested_flags_without_claiming_effective_enable(self):
        result = self.status(supervised_effective=False, emotion_arms_effective=False,
                             setup_error="arm_data_port_busy_or_unavailable")
        self.assertTrue(result["launcher"]["supervised_requested"])
        self.assertTrue(result["launcher"]["emotion_arms_requested"])
        self.assertFalse(result["launcher"]["supervised_effective"])
        self.assertFalse(result["launcher"]["emotion_arms_effective"])
        self.assertIn("arm_data_port_busy_or_unavailable", result["conversation"]["blocked_reasons"])

    def test_explicit_legacy_named_path_is_not_misreported_as_current_speech_profile(self):
        turn = dict(turn_id='session:3', stage='submitted', gesture_id='wave',
            explicit_request=True, semantic_validated=True, player_start_seen=True,
            player_start_fresh=True, automatic_reason='explicit_request')
        motion = dict(real_enabled=True, performance_supported=False,
            performance=dict(path='legacy_named_gesture', turn_id='session:3'))
        state = self.status(turn_state=turn, motion=motion, emotion_arms_effective=False)['conversation']
        self.assertEqual(state['execution_path'], 'legacy_named_gesture')
        self.assertNotIn('arm_performance_firmware_required', state['blocked_reasons'])
        self.assertNotIn('emotion_arms_off', state['blocked_reasons'])
        motion['performance']['turn_id'] = 'session:2'
        later = self.status(turn_state=turn, motion=motion)['conversation']
        self.assertIsNone(later['execution_path'])

    def test_status_projection_is_bounded_and_never_copies_arbitrary_text(self):
        private = "never copy this transcript or credential"
        result = self.status(setup_error=private, motion={"blocked_reasons": private},
                             turn_state=dict(turn_id=private, gesture_id=private,
                                             stage=[], result_status={}, plan_emotion=[],
                                             playback_emotion={}, result_code=private))
        self.assertNotIn(private, json.dumps(result))
        self.assertIsNone(result["conversation"]["turn_id"])
        self.assertEqual(result["launcher"]["setup_code"], "arm_setup_failed")
        self.assertEqual(result["stop_semantics"], STOP_SEMANTICS)


class B1BoardLatchRestartTests(unittest.TestCase):
    def test_fresh_host_hello_cannot_clear_stop_manual_or_watchdog_latch(self):
        for cause in ("stop", "manual", "watchdog"):
            with self.subTest(cause=cause):
                clock, moves = Clock(), []
                board = core.LegacyBridge(None, "sameBoardBoot", lambda *v: moves.append(v),
                                           lambda *v: moves.append(v), lambda: 0,
                                           enabled=True, clock=clock)
                board.enabled = True  # Pure memory fixture; no board access.
                first = LegacyArmLink(FakePort(board), motor_permission=lambda: True,
                                      sequence_clock=lambda: 1000)
                first.hello()
                if cause == "stop":
                    first.stop()
                elif cause == "manual":
                    board.manual_cancel()
                else:
                    first.gesture("wave")
                    clock.advance(1.)
                    board._tick_steps()
                    self.assertEqual(board.fault, "watchdog_timeout")
                self.assertTrue(board.stopped)
                # Equivalent to a fresh Desktop transport: same controller boot
                # and no reset or manual latch-clear operation is introduced.
                port = FakePort(board)
                next_link = LegacyArmLink(port, motor_permission=lambda: True,
                                          sequence_clock=lambda: 2000)
                motion = LegacyMotionFeature(next_link, clock=clock)
                motion.set_context("nextSession", "turn1", 1)
                motion.submit("wave", request_id="newRequest", session_id="nextSession",
                              turn_id="turn1", mode_generation=1, started_at=clock())
                motion.poll()
                state = motion.snapshot()
                self.assertTrue(state["stopped"])
                self.assertEqual(state["readiness"], "BLOCKED")
                self.assertEqual(state["reason_code"], "arm_stopped")
                self.assertEqual(state["transport"]["status"], "STOPPED")
                self.assertEqual(state["transport"]["last_response_code"], "STOPPED")
                self.assertIn("arm_stopped", state["blocked_reasons"])
                self.assertEqual(state["stop_semantics"], STOP_SEMANTICS)
                self.assertFalse(any(b" DO " in packet for packet in port.sent))
                self.assertEqual(moves, [])
                status = conversation_arm_status(simulate=False, supervised_requested=True,
                    supervised_effective=True, emotion_arms_requested=True,
                    emotion_arms_effective=True, motion=state)
                self.assertTrue(status["board"]["remote_enabled_at_last_ack"])
                self.assertIn("arm_stopped", status["conversation"]["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
