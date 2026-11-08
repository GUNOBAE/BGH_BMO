"""Actual CharacterPlan/player-start/Host/B1 path with synthetic IO only."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai.completed_turn_diagnostics import CompletedTurnStore
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.motion.legacy_host import LegacyArmLink, LegacyMotionFeature
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from test_legacy_arm_host import FakePort, core as v1

_FIRMWARE = Path(__file__).resolve().parents[2] / "firmware/staging/bmo_arm_legacy_bridge_v3/bmo_arm_bridge.py"
_SPEC = importlib.util.spec_from_file_location("character_speech_arm_fixture", _FIRMWARE)
core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(core)


class CharacterArmRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.completed_stores = []
        self.enterContext(patch('bmo_ai.completed_turn_diagnostics.default_directory',
            side_effect=AssertionError('Synthetic turns must never use the user diagnostic store')))
        for name in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                     "bmo_ai.cloud.create_client", "bmo_ai.fish_tts.synthesize_wav",
                     "bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open"):
            self.enterContext(patch(name, side_effect=AssertionError("Physical or network IO forbidden")))
        self.prepared, self.begin, self.started, self.release = Event(), Event(), Event(), Event()
        self.host = None
        self.clock_value = time.monotonic()

    def tearDown(self):
        self.begin.set()
        self.release.set()
        if self.host is not None:
            self.host.close()

    def make_host(self, *, gesture="wave", emotion_arms=True, permitted=True, clock=None,
                  board_module=core, context="greeting", emotion="happy", reaction="none"):
        clock = clock or time.monotonic
        config = integration_config()
        self.client = FeatureSimulatedClient([dict(version=3, speech="Good morning, friend.",
            emotion=emotion, gesture=gesture, intent=None, reaction=reaction,
            character=dict(delivery_id="warm", intensity="low", gaze_id="none", source="dialogue", gesture_context=context))])
        self.board = board_module.LegacyBridge(None, "fixtureBoard", lambda *_: None, lambda *_: None,
                                      lambda: 0, enabled=True, clock=clock)
        self.board.enabled = True  # Pure memory fixture, never a mounted device.
        self.port = FakePort(self.board)
        link = LegacyArmLink(self.port, motor_permission=lambda: permitted)
        self.motion = LegacyMotionFeature(link, clock=clock, cooldown=0)

        def player(_command, *, on_start, cancel_check, **_kwargs):
            deadline = time.monotonic() + 5
            self.prepared.set()
            while not self.begin.wait(.002):
                cancel_check()
                if time.monotonic() > deadline:
                    raise AssertionError("Synthetic player-start gate timed out")
            on_start()
            self.started.set()
            while not self.release.wait(.002):
                cancel_check()
                if time.monotonic() > deadline:
                    raise AssertionError("Synthetic playback gate timed out")
            cancel_check()

        voice = IntegrationVoice(config, self.client, simulate=True, expressive=True,
                                  character_life=True, player=player, emit=lambda *_a, **_kw: None)
        store = CompletedTurnStore(self.directory / str(len(self.completed_stores)))
        self.addCleanup(store.close)
        self.assertTrue(store.snapshot()['enabled'])
        self.completed_stores.append(store)
        self.host = FeatureHost(config=config, client=self.client, voice=voice,
            runtime=RuntimePolicy(simulate=False, arms_supervised=True), motion=self.motion,
            clock=clock, character_life=True, expressive_voice=True, emotion_arms=emotion_arms,
            completed_turn_store=store)

    def pump(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.host.heartbeat()
            self.board._tick_steps()
            self.host.poll()
            if predicate():
                return
            time.sleep(.002)
        self.fail("Host did not reach the expected synthetic boundary")

    def arms(self):
        return self.host.snapshot()["system_diagnostics"]["arms"]

    def do_packets(self):
        return [packet for packet in self.port.sent if b" DO " in packet]

    def act_packets(self):
        return [packet for packet in self.port.sent if b" ACT " in packet]

    def prepare(self):
        self.assertTrue(self.host.start_question("Good morning, BMO."))
        self.assertTrue(self.prepared.wait(2))
        self.pump(lambda: self.arms()["conversation"]["stage"] == "planned")

    def start(self):
        self.begin.set()
        self.assertTrue(self.started.wait(2))

    def test_current_named_plan_waits_for_player_start_once_and_repeat_never_reissues(self):
        self.make_host()
        self.prepare()
        before = self.arms()
        self.assertEqual(before["conversation"]["gesture_id"], "wave")
        self.assertEqual(before["conversation"]["blocked_reasons"], ["awaiting_player_start"])
        self.assertFalse(before["conversation"]["player_start_seen"])
        self.assertEqual(self.port.sent, [])  # Even HELLO is lazy until start.
        self.start()
        self.pump(lambda: len(self.act_packets()) == 1)
        current = self.arms()["conversation"]
        self.assertTrue(current["player_start_seen"])
        self.assertTrue(current["player_start_fresh"])
        self.assertEqual(current["result_status"], "running")
        self.assertEqual(current["result_code"], "performance_ack_position_unverified")
        self.assertTrue(self.act_packets()[0].endswith(b" greeting\n"))
        self.assertEqual(self.do_packets(), [])
        turn = self.host.session.snapshot()["turn_id"]
        self.host._playback("started", turn, self.host.clock())
        for _ in range(3):
            self.host.poll()
        self.assertEqual(len(self.act_packets()), 1)
        self.release.set()
        self.pump(lambda: not self.host.session.busy)
        self.assertEqual(self.arms()["conversation"]["stage"], "ended")
        self.pump(lambda: not self.motion.snapshot()["active"])
        self.assertEqual(self.board.positions, {"left": 0, "right": 0})
        self.assertTrue(any(b" END " in packet for packet in self.port.sent))
        self.host.control(HostControl("repeat"), request="repeatCurrentArm")
        self.pump(lambda: not self.host.session.busy)
        self.assertEqual(len(self.act_packets()), 1)
        self.assertEqual(len(self.client.requests), 1)

    def test_emotion_opt_out_preserves_plan_but_sends_no_hello_or_motion(self):
        self.make_host(emotion_arms=False)
        self.prepare()
        self.start()
        self.pump(lambda: self.arms()["conversation"]["player_start_seen"])
        state = self.arms()
        self.assertTrue(state["launcher"]["supervised_effective"])
        self.assertFalse(state["launcher"]["emotion_arms_effective"])
        self.assertIn("emotion_arms_off", state["conversation"]["blocked_reasons"])
        self.assertEqual(state["conversation"]["gesture_id"], "wave")
        self.assertEqual(self.port.sent, [])

    def test_no_model_gesture_is_distinct_from_disabled_adapter(self):
        self.make_host(gesture="none")
        self.prepare()
        self.start()
        self.pump(lambda: self.arms()["conversation"]["player_start_seen"])
        state = self.arms()
        self.assertTrue(state["launcher"]["emotion_arms_effective"])
        self.assertIn("current_turn_no_gesture", state["conversation"]["blocked_reasons"])
        self.assertNotIn("arm_runtime_off", state["conversation"]["blocked_reasons"])
        self.assertEqual(self.port.sent, [])

    def test_adapter_permission_failure_is_separate_from_valid_plan_and_player_start(self):
        self.make_host(permitted=False)
        self.prepare()
        self.start()
        self.pump(lambda: "arm_operator_condition_missing" in self.arms()["conversation"]["blocked_reasons"])
        state = self.arms()
        self.assertTrue(state["conversation"]["player_start_seen"])
        self.assertTrue(state["board"]["remote_enabled_at_last_ack"])
        self.assertIsNone(state["board"]["remote_enabled_static"])
        self.assertFalse(state["conversation"]["eligible_at_player_start"])
        self.assertEqual(self.do_packets(), [])

    def test_cancel_before_start_never_dispatches_and_cancel_after_start_returns(self):
        self.make_host()
        self.prepare()
        self.host.cancel_conversation()
        self.pump(lambda: not self.host.session.busy)
        self.assertEqual(self.arms()["conversation"]["stage"], "cancelled")
        self.assertEqual(self.do_packets(), [])
        self.host.close()
        self.prepared.clear()
        self.begin.clear()
        self.started.clear()
        self.release.clear()
        self.make_host()
        self.prepare()
        self.start()
        self.pump(lambda: len(self.act_packets()) == 1)
        self.host.cancel_conversation()
        self.pump(lambda: not self.host.session.busy)
        self.assertEqual(self.arms()["conversation"]["stage"], "cancelled")
        self.assertEqual(len(self.act_packets()), 1)
        self.pump(lambda: not self.motion.snapshot()["active"])
        self.assertTrue(any(b" END " in packet for packet in self.port.sent))
        self.assertFalse(any(b" HOLD " in packet for packet in self.port.sent))
        self.assertFalse(self.board.stopped)  # Normal return is token-bound, never reprepare.
        self.assertEqual(self.board.positions, {"left": 0, "right": 0})

    def test_v1_automatic_profile_is_blocked_without_fallback_but_explicit_menu_works(self):
        self.make_host(board_module=v1)
        self.prepare();self.start()
        self.pump(lambda: self.motion.snapshot()['performance_supported'] is False)
        self.assertEqual(self.motion.snapshot()['performance']['code'], 'arm_performance_firmware_required')
        self.assertEqual(self.act_packets(), []);self.assertEqual(self.do_packets(), [])
        self.release.set();self.pump(lambda: not self.host.session.busy)
        result=self.host.execute(HostIntent('arm_gesture','raise_left_arm'), request_id='explicitMenu')
        self.assertEqual(result.status,'accepted')
        self.pump(lambda: len(self.do_packets()) == 1)
        self.assertTrue(self.do_packets()[0].endswith(b' raise_left_arm\n'))

    def test_surprise_context_uses_same_plan_and_returns_while_long_speech_continues(self):
        self.make_host(gesture='shrug', context='surprise', emotion='surprised', reaction='gasp')
        self.prepare();self.start()
        self.pump(lambda: len(self.act_packets()) == 1)
        self.assertTrue(self.act_packets()[0].endswith(b' surprise\n'))
        self.assertEqual(self.host.session.snapshot()['emotion'], 'surprised')
        self.pump(lambda: not self.motion.snapshot()['active'])
        self.assertTrue(self.host.session.busy)
        self.assertEqual(self.board.positions, {'left':0,'right':0})
        self.assertEqual(len(self.client.requests),1)

    def test_new_question_starts_during_old_return_without_late_old_arm_raise(self):
        self.make_host();self.prepare();self.start()
        self.pump(lambda: self.board.positions['right'] >= 10)
        old=self.host.session.snapshot()['turn_id']
        self.host.cancel_conversation();self.pump(lambda: not self.host.session.busy)
        self.prepared.clear();self.begin.clear();self.started.clear();self.release.clear()
        self.assertTrue(self.host.start_question('What is two plus two?'))
        self.assertTrue(self.prepared.wait(2))
        self.assertNotEqual(self.host.session.snapshot()['turn_id'],old)
        self.host._playback('started',old,self.host.clock())
        self.host._playback('ended',old,self.host.clock())
        self.pump(lambda: not self.motion.snapshot()['active'])
        self.assertEqual(self.board.positions,{'left':0,'right':0})
        self.assertEqual(len(self.act_packets()),1)
        self.assertTrue(self.host.session.busy)

    def test_stop_or_manual_latch_never_turns_late_end_into_return(self):
        for kind in ('stop','manual'):
            with self.subTest(kind=kind):
                if self.host is not None:self.host.close()
                self.prepared.clear();self.begin.clear();self.started.clear();self.release.clear()
                self.make_host();self.prepare();self.start()
                self.pump(lambda: self.board.positions['right'] >= 10)
                turn=self.host.session.snapshot()['turn_id']
                if kind=='stop':self.host.stop()
                else:self.board.manual_cancel()
                position=dict(self.board.positions)
                before=sum(b' END ' in packet for packet in self.port.sent)
                self.host._playback('ended',turn,self.host.clock())
                self.host.cancel_conversation()
                self.pump(lambda: not self.host.session.busy)
                self.assertTrue(self.board.stopped)
                self.assertEqual(self.board.positions,position)
                self.assertEqual(sum(b' END ' in packet for packet in self.port.sent),before if kind=='stop' else before+1)
                # The unobserved GP7 may receive one stale END; the board denies
                # it under STOPPED before any PWM and the host never retries.
                self.assertFalse(any(b' PREPARE ' in packet for packet in self.port.sent))

    def test_late_player_start_does_not_dispatch_a_current_or_old_gesture(self):
        self.make_host(clock=lambda: self.clock_value)
        self.prepare()
        self.start()
        self.clock_value += .6
        self.pump(lambda: self.arms()["conversation"]["stage"] == "stale")
        self.assertIn("stale_playback_start", self.arms()["conversation"]["blocked_reasons"])
        self.assertEqual(self.do_packets(), [])


if __name__ == "__main__":
    unittest.main()
