"""Resource transitions with fake owners only; no UI, audio device or process."""
import math
import unittest
from unittest.mock import Mock

from bmo_ai.features.contracts import FeatureError, MODES
from bmo_ai.features.resources import AudioFocus, GazeArbiter, InputRouter, ModeManager


class FeatureResourceTests(unittest.TestCase):
    def modes(self):
        manager = ModeManager()
        for mode in MODES:
            manager.register(mode)
        return manager

    def test_mode_transition_releases_before_prepare_and_invalidates_old_callbacks(self):
        manager, calls = self.modes(), []
        manager.register("conversation", leave=lambda: calls.append("old_released"))
        manager.register("game", enter=lambda context: calls.append(("new_prepared", context)))
        old = manager.generation
        manager.transition("game", {"fixture": True})
        self.assertEqual(calls, ["old_released", ("new_prepared", {"fixture": True})])
        self.assertEqual(manager.mode, "game")
        self.assertFalse(manager.accepts(old))
        self.assertTrue(manager.accepts(manager.generation))
        self.assertFalse(manager.accepts(True))
        self.assertFalse(manager.transitioning)

    def test_old_cleanup_failure_keeps_old_mode_and_never_prepares_new_owner(self):
        manager = self.modes()
        enter = Mock()
        manager.register("conversation", leave=Mock(side_effect=RuntimeError("private cleanup payload")))
        manager.register("game", enter=enter)
        old = manager.generation
        with self.assertRaises(FeatureError) as caught:
            manager.transition("game")
        self.assertEqual(caught.exception.code, "mode_release_failed")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(manager.mode, "conversation")
        self.assertFalse(manager.accepts(old))
        enter.assert_not_called()

    def test_failed_new_prepare_cleans_partial_owner_then_recovers_conversation(self):
        manager, calls = self.modes(), []

        def fail(context):
            calls.append("new_prepare")
            raise RuntimeError("private prepare payload")

        manager.register("conversation", enter=lambda context: calls.append(("recovered", context)),
                         leave=lambda: calls.append("old_release"))
        manager.register("game", enter=fail, leave=lambda: calls.append("new_cleanup"))
        with self.assertRaises(FeatureError) as caught:
            manager.transition("game")
        self.assertEqual(caught.exception.code, "mode_prepare_failed")
        self.assertEqual(calls, ["old_release", "new_prepare", "new_cleanup", ("recovered", {"recovery": True})])
        self.assertEqual(manager.mode, "conversation")

    def test_failed_partial_owner_cleanup_cannot_start_conversation_over_leaked_owner(self):
        manager = self.modes()
        conversation = Mock()
        manager.register("conversation", enter=conversation)
        manager.register("game", enter=Mock(side_effect=RuntimeError("private prepare")),
                         leave=Mock(side_effect=RuntimeError("private cleanup")))
        with self.assertRaises(FeatureError) as caught:
            manager.transition("game")
        self.assertEqual(caught.exception.code, "mode_recovery_failed")
        self.assertEqual(manager.last_error, "mode_recovery_failed")
        conversation.assert_not_called()
        self.assertFalse(manager.transitioning)

    def test_failed_conversation_recovery_is_reported_and_transition_guard_released(self):
        manager = self.modes()
        manager.register("conversation", enter=Mock(side_effect=RuntimeError("private recovery")))
        manager.register("photo", enter=Mock(side_effect=RuntimeError("private prepare")))
        with self.assertRaises(FeatureError) as caught:
            manager.transition("photo")
        self.assertEqual(caught.exception.code, "mode_recovery_failed")
        self.assertFalse(manager.transitioning)

    def test_same_mode_is_noop_unless_forced_and_nested_transition_is_rejected(self):
        manager = self.modes()
        enter, leave = Mock(), Mock()
        manager.register("conversation", enter=enter, leave=leave)
        self.assertEqual(manager.transition("conversation"), 0)
        enter.assert_not_called(); leave.assert_not_called()
        self.assertEqual(manager.transition("conversation", force=True), 1)
        errors = []

        def nested(context):
            try:
                manager.transition("music")
            except FeatureError as error:
                errors.append(error.code)

        manager.register("game", enter=nested)
        manager.transition("game")
        self.assertEqual(errors, ["mode_busy"])
        self.assertEqual(manager.mode, "game")

    def test_audio_handover_requires_confirmed_stop_before_new_lease(self):
        focus, calls = AudioFocus(), []
        stop = Mock(side_effect=lambda: calls.append("old_stopped") or True)
        old = focus.acquire("music", stop=stop)
        new = focus.acquire("conversation", stop=lambda: True, preempt=True)
        self.assertEqual(calls, ["old_stopped"])
        self.assertEqual(focus.owner, "conversation")
        self.assertGreater(new.generation, old.generation)
        self.assertFalse(focus.release(old))
        self.assertEqual(focus.owner, "conversation")
        self.assertTrue(focus.release(new))

    def test_audio_unconfirmed_unsupported_or_failed_stop_keeps_old_owner(self):
        for callback, expected in ((None, "audio_busy"), (lambda: False, "audio_release_unsupported"),
                                   (Mock(side_effect=RuntimeError("private stop")), "audio_release_failed")):
            with self.subTest(expected=expected):
                focus = AudioFocus()
                old = focus.acquire("music", stop=callback)
                with self.assertRaises(FeatureError) as caught:
                    focus.acquire("conversation", preempt=True)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(focus.lease, old)
                self.assertNotIn("private", str(caught.exception))

    def test_audio_nonpreempting_or_same_owner_requests_do_not_stop_existing_audio(self):
        focus, stop = AudioFocus(), Mock(return_value=True)
        old = focus.acquire("music", stop=stop)
        self.assertIs(focus.acquire("music"), old)
        with self.assertRaises(FeatureError):
            focus.acquire("conversation")
        stop.assert_not_called()

    def test_reentrant_stop_cannot_silently_overwrite_another_owner_lease(self):
        focus = AudioFocus()
        old = None

        def changing_owner():
            focus.release(old)
            focus.acquire("other", stop=lambda: True)
            return True

        old = focus.acquire("music", stop=changing_owner)
        with self.assertRaises(FeatureError):
            focus.acquire("conversation", preempt=True)
        self.assertNotEqual(focus.owner, "conversation")

    def test_audio_stop_only_clears_lease_after_success(self):
        focus = AudioFocus()
        stop = Mock(return_value=False)
        lease = focus.acquire("music", stop=stop)
        self.assertFalse(focus.stop())
        self.assertEqual(focus.lease, lease)
        stop.return_value = True
        self.assertTrue(focus.stop())
        self.assertIsNone(focus.owner)
        self.assertTrue(focus.stop())

    def test_game_b_is_owned_by_game_for_press_and_release_without_global_back(self):
        manager = self.modes()
        router = InputRouter(manager)
        handlers = {mode: Mock(return_value=mode) for mode in ("conversation", "game_menu", "game")}
        for mode, handler in handlers.items():
            router.bind(mode, handler)
        manager.transition("game")
        self.assertEqual(router.route("B", True, now=1), "game")
        self.assertEqual(router.route("B", False, now=2), "game")
        self.assertEqual(handlers["game"].call_count, 2)
        handlers["conversation"].assert_not_called()
        handlers["game_menu"].assert_not_called()
        self.assertEqual(manager.mode, "game")

    def test_stop_and_talk_take_immediate_host_paths_before_mode_consumer(self):
        manager = self.modes()
        stop, talk, game = Mock(return_value="stopped"), Mock(return_value="talk"), Mock()
        router = InputRouter(manager, stop=stop, talk=talk)
        router.bind("game", game)
        manager.transition("game")
        self.assertEqual(router.route("STOP"), "stopped")
        self.assertEqual(router.route("TALK"), "talk")
        game.assert_not_called()
        self.assertEqual((stop.call_count, talk.call_count), (1, 1))

    def test_physical_input_is_disabled_until_a_verified_logical_map_is_installed(self):
        manager = self.modes()
        stop = Mock()
        router = InputRouter(manager, stop=stop)
        with self.assertRaises(FeatureError):
            router.route("fixture_switch", physical=True)
        with self.assertRaises(FeatureError):
            router.set_physical_map({"fixture_switch": "STOP"})
        router.set_physical_map({"fixture_switch": "STOP"}, verified=True)
        router.route("fixture_switch", physical=True)
        self.assertEqual(stop.call_count, 1)
        with self.assertRaises(FeatureError):
            router.set_physical_map({"fixture_switch": "UNTRUSTED"}, verified=True)

    def test_gaze_manual_native_camera_reaction_and_idle_priorities(self):
        now = [0.0]
        gaze = GazeArbiter(clock=lambda: now[0])
        gaze.offer("reaction", .1, .2, generation=1, expires_at=3)
        gaze.offer("camera", .3, .4, generation=1, expires_at=3)
        for phase in ("idle", "speaking"):
            self.assertEqual(gaze.resolve(phase=phase, generation=1)["owner"], "camera")
        for phase in ("listening", "thinking"):
            self.assertEqual(gaze.resolve(phase=phase, generation=1),
                             {"owner": "native", "apply": False, "x": 0., "y": 0.})
        gaze.offer("manual", .7, -.8, generation=1, expires_at=3)
        self.assertEqual(gaze.resolve(phase="thinking", generation=1)["owner"], "manual")
        gaze.clear("manual"); gaze.clear("camera")
        self.assertEqual(gaze.resolve(phase="idle", generation=1)["owner"], "reaction")
        gaze.clear()
        self.assertEqual(gaze.resolve(phase="idle", generation=1),
                         {"owner": "idle", "apply": True, "x": 0., "y": 0.})

    def test_gaze_ttl_generation_and_diagonal_bounds_do_not_modify_emotion_or_mouth(self):
        now = [1.0]
        gaze = GazeArbiter(clock=lambda: now[0])
        gaze.offer("camera", 10, -10, generation=3, expires_at=2)
        result = gaze.resolve(phase="speaking", generation=3)
        self.assertEqual((result["x"], result["y"]), (1., -1.))
        self.assertEqual(set(result), {"owner", "apply", "x", "y"})
        self.assertEqual(gaze.resolve(phase="idle", generation=4)["owner"], "idle")
        now[0] = 2.001
        self.assertEqual(gaze.resolve(phase="idle", generation=3)["owner"], "idle")
        for values in ((math.nan, 0), (0, math.inf), (True, 0)):
            with self.subTest(values=values), self.assertRaises(FeatureError):
                gaze.offer("camera", *values, generation=3, expires_at=4)


if __name__ == "__main__":
    unittest.main()
