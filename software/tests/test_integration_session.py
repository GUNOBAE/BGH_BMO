"""Real worker and synthetic IO share the same session/face lifecycle."""
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.audio import AudioSynthesisError
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession, SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        for target in ("bmo_ai.cloud.create_client", "bmo_ai.audio.record_wav",
                       "bmo_ai.audio.transcribe", "bmo_ai.fish_tts.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("live IO forbidden")))

    def tearDown(self):
        for session in self.sessions:
            session.close()

    def session(self, **kwargs):
        s = IntegrationSession(integration_config(), SimulatedClient(), simulate=True, **kwargs)
        self.sessions.append(s)
        return s

    def finish(self, s):
        deadline = time.monotonic() + 4
        while s.busy and time.monotonic() < deadline:
            s.poll()
            time.sleep(0.003)
        self.assertFalse(s.busy)

    def test_two_turn_context_reset_and_relative_lifecycle(self):
        s = self.session()
        for message in ("first", "second"):
            self.assertTrue(s.start(message))
            self.assertFalse(s.start("duplicate"))
            self.finish(s)
        self.assertEqual([len(r["input"]) for r in s.client.requests], [1, 3])
        phases = [e["phase"] for e in s.events]
        for phase in ("listening", "thinking", "speaking", "idle"):
            self.assertIn(phase, phases)
        state = s.snapshot()
        self.assertEqual(state["playback"], "ended")
        self.assertEqual(state["cues"], [])
        self.assertIsNone(state["utterance_id"])
        self.assertTrue(all(e["phase"] == "thinking" for e in s.events if e["kind"] == "prepared"))
        s.reset()
        self.assertEqual(s.brain.history, [])
        s.start("after reset")
        self.finish(s)
        self.assertEqual(len(s.client.requests[-1]["input"]), 1)

    def test_cancel_owned_player_and_next_turn(self):
        s = self.session()
        s.start()
        deadline = time.monotonic() + 3
        while s.snapshot()["playback"] != "started" and time.monotonic() < deadline:
            s.poll()
            time.sleep(0.002)
        self.assertEqual(s.snapshot()["phase"], "speaking")
        s.set_emotion("crying")
        self.assertEqual(s.snapshot()["phase"], "speaking")
        s.cancel()
        self.finish(s)
        self.assertEqual(s.snapshot()["playback"], "cancelled")
        self.assertEqual(s.snapshot()["emotion"], "crying")
        self.assertEqual(s.snapshot()["cues"], [])
        self.assertTrue(s.start())
        self.finish(s)

    def test_disconnect_cancel_preserves_emotion_and_reset(self):
        s = self.session(disconnect_timeout=0.03)
        s.set_emotion("happy")
        s.browser_snapshot()
        s.start()
        self.finish(s)
        self.assertEqual(s.snapshot()["playback"], "cancelled")
        self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_new_session_id_prevents_worker_turn_collision(self):
        a, b = self.session(), self.session()
        self.assertNotEqual(a.snapshot()["session_id"], b.snapshot()["session_id"])
        a.start(); b.start()
        self.finish(a); self.finish(b)
        self.assertNotEqual(a.snapshot()["turn_id"], b.snapshot()["turn_id"])

    def test_cancelled_late_answer_does_not_enter_history(self):
        s = self.session()
        entered, release = Event(), Event()
        original = s.client.create
        def delayed(**request):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test gate timed out")
            return original(**request)
        s.client.create = delayed
        s.start("late question")
        self.assertTrue(entered.wait(1))
        s.cancel()
        release.set()
        self.finish(s)
        self.assertEqual(s.brain.history, [])
        self.assertFalse(any(e["kind"] == "prepared" for e in s.events))
        s.client.create = original
        s.start("next question")
        self.finish(s)
        self.assertEqual(len(s.client.requests[-1]["input"]), 1)

    def blocked_synthesis_session(self, **kwargs):
        entered, release = Event(), Event()
        paths = []

        def synthesize(*args, **settings):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test synthesis gate timed out")
            path = synthetic_wav()
            paths.append(path)
            return path

        def player(command, *, on_start, cancel_check, **settings):
            cancel_check()
            on_start()

        voice = IntegrationVoice(integration_config(), None, emit=lambda *a, **kw: None,
                                 fish_synthesizer=synthesize, player=player)
        voice.listen = lambda: "A synthetic question."
        session = self.session(voice=voice, **kwargs)
        # Release a fake dependency even when an assertion fails. The session's
        # normal cleanup then owns the generated WAV and late-result rejection.
        self.addCleanup(release.set)
        return session, entered, release, paths

    def test_timeout_keeps_blocked_synthesis_slot_until_cleanup_then_recovers(self):
        now = [0.0]
        s, entered, release, paths = self.blocked_synthesis_session(
            timeout=.03, clock=lambda: now[0])
        self.assertTrue(s.start())
        self.assertTrue(entered.wait(1))
        now[0] = .04
        s.poll()
        self.assertTrue(s.busy)
        self.assertFalse(s.start("duplicate while dependency is blocked"))
        self.assertLessEqual(s.worker.queued_events, 32)
        self.assertEqual(s.snapshot()["cues"], [])
        release.set()
        self.finish(s)
        self.assertEqual(s.snapshot()["playback"], "error")
        self.assertFalse(any(e["kind"] in ("prepared", "started") for e in s.events))
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertTrue(s.start("recovered turn"))
        self.finish(s)
        self.assertEqual(s.snapshot()["playback"], "ended")

    def test_reset_during_synthesis_discards_late_audio_and_clears_same_brain(self):
        s, entered, release, paths = self.blocked_synthesis_session()
        brain = s.brain
        self.assertTrue(s.start())
        self.assertTrue(entered.wait(1))
        self.assertTrue(s.brain.history)
        s.reset()
        self.assertFalse(s.start("reset still waiting for owned dependency"))
        release.set()
        self.finish(s)
        self.assertIs(s.brain, brain)
        self.assertEqual(s.brain.history, [])
        self.assertEqual(s.snapshot()["playback"], "idle")
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertTrue(s.start("fresh after reset"))
        self.finish(s)
        self.assertEqual(len(s.client.requests[-1]["input"]), 1)

    def test_provider_failure_uses_error_state_and_next_turn_can_play(self):
        s, entered, release, paths = self.blocked_synthesis_session()
        release.set()
        synthesis = s.voice._fish_synthesizer
        failed = False

        def fail_once(*args, **settings):
            nonlocal failed
            if not failed:
                failed = True
                raise AudioSynthesisError("Synthetic provider failure.", "fish_connection")
            return synthesis(*args, **settings)

        s.voice._fish_synthesizer = fail_once
        self.assertTrue(s.start())
        self.finish(s)
        self.assertEqual(s.snapshot()["phase"], "idle")
        self.assertEqual(s.snapshot()["playback"], "error")
        self.assertEqual(s.snapshot()["cues"], [])
        self.assertTrue(s.start())
        self.finish(s)
        self.assertEqual(s.snapshot()["playback"], "ended")
        self.assertEqual(len(s.client.requests), 2)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_close_during_synthesis_defers_owned_cleanup_without_late_playback(self):
        s, entered, release, paths = self.blocked_synthesis_session()
        closed = Event()
        s.client.close = Mock(side_effect=closed.set)
        self.assertTrue(s.start())
        self.assertTrue(entered.wait(1))
        thread = s.worker._thread
        s.close()
        self.assertFalse(s.start("closed session"))
        self.assertFalse(closed.is_set())
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(closed.wait(1))
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertFalse(any(e["kind"] in ("prepared", "started") for e in s.events))
        s.client.close.assert_called_once()
