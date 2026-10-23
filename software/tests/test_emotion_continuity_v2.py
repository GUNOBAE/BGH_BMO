"""Continuity v2 replaces the Stage 1 neutral-on-turn/two-second policy.

All phases use the production session/worker and generated private PCM. The
clock advances explicitly; no microphone, API, ALSA/Piper process or art is used.
"""
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio, integration_voice
from bmo_ai.cloud import CloudError
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import EMOTIONS, IntegrationSession
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from test_emotion_session import Clock, ReplyClient


class EmotionContinuityV2Tests(unittest.TestCase):
    def setUp(self):
        self.sessions, self.releases = [], []
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Live IO forbidden")))

    def tearDown(self):
        for release in self.releases:
            release.set()
        for session in self.sessions:
            session.close()

    def session(self, replies, *, offline=False, real_voice=False):
        clock, client = Clock(), ReplyClient(replies)
        config = integration_config(offline_voice=offline)
        observations = []

        def player(command, *, on_start, cancel_check, **settings):
            cancel_check()
            before = session.snapshot()
            on_start()
            observations.append((before, session.snapshot()))
            cancel_check()

        voice = IntegrationVoice(config, client, simulate=not real_voice,
                                 emit=lambda *a, **kw: None, player=player)
        voice.listen = lambda: "Synthetic question."
        session = IntegrationSession(config, client, simulate=True, voice=voice,
                                     emotion_mode="auto", clock=clock)
        session.observations, session.test_clock = observations, clock
        self.sessions.append(session)
        return session

    def finish(self, session):
        events, deadline = [], time.monotonic() + 3
        while session.busy and time.monotonic() < deadline:
            events.extend(session.poll())
            time.sleep(.002)
        self.assertFalse(session.busy)
        events.extend(session.poll())
        return events

    def gate(self, target, name):
        entered, release = Event(), Event()
        self.releases.append(release)
        original = getattr(target, name)

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic dependency gate timed out")
            return original(*args, **kwargs)

        setattr(target, name, delayed)
        return entered, release, original

    def seed(self, session, emotion="crying"):
        self.assertTrue(session.start())
        self.finish(session)
        self.assertEqual(session.snapshot()["emotion"], emotion)

    def test_all_six_emotions_survive_long_idle_listening_thinking_and_prepared(self):
        for emotion in sorted(EMOTIONS):
            with self.subTest(emotion=emotion):
                next_emotion = "neutral" if emotion != "neutral" else "happy"
                s = self.session([("First fixture reply.", emotion), ("Next fixture reply.", next_emotion)])
                self.seed(s, emotion)
                for now in (2.001, 3600.0):
                    s.test_clock.now = now
                    s.poll()
                    self.assertEqual((s.snapshot()["phase"], s.snapshot()["emotion"]), ("idle", emotion))
                listening, release_listening, _ = self.gate(s.voice, "listen")
                thinking, release_thinking, _ = self.gate(s.client, "create")
                prepared, release_prepared, _ = self.gate(s.voice, "_player")
                self.assertTrue(s.start())
                self.assertTrue(listening.wait(1))
                self.assertEqual((s.snapshot()["phase"], s.snapshot()["emotion"]), ("listening", emotion))
                release_listening.set()
                self.assertTrue(thinking.wait(1))
                self.assertEqual((s.snapshot()["phase"], s.snapshot()["emotion"]), ("thinking", emotion))
                release_thinking.set()
                self.assertTrue(prepared.wait(1))
                self.assertEqual((s.snapshot()["playback"], s.snapshot()["emotion"]), ("prepared", emotion))
                release_prepared.set(); self.finish(s)
                self.assertEqual(s.observations[-1][0]["emotion"], emotion)
                self.assertEqual(s.observations[-1][1]["emotion"], next_emotion)
                self.assertEqual(s.snapshot()["emotion"], next_emotion)

    def test_fixed_notice_is_neutral_only_during_playback_then_restores_prior_dialogue(self):
        s = self.session([("First crying reply.", "crying"),
                          CloudError("connection", "Synthetic connection failure.")],
                         offline=True, real_voice=True)
        s.voice._fish_synthesizer = Mock(side_effect=lambda *a, **kw: synthetic_wav())
        s.voice._piper_synthesizer = Mock(side_effect=lambda *a, **kw: synthetic_wav())
        self.seed(s)
        self.assertTrue(s.start()); self.finish(s)
        before, started = s.observations[-1]
        self.assertEqual(before["emotion"], "crying")
        self.assertEqual((started["emotion"], started["emotion_validation"]), ("neutral", "notice"))
        self.assertEqual(s.snapshot()["emotion"], "crying")
        self.assertTrue(s.voice._cache["expression"]["notice"])

    def test_invalid_incomplete_refused_or_cloud_error_keeps_display_and_can_recover(self):
        failures = (
            "not-json",
            SimpleNamespace(status="incomplete", incomplete_details={"reason": "max_output_tokens"},
                            error=None, output_text='{"speech":"partial'),
            SimpleNamespace(status="completed", incomplete_details=None, error=None,
                            output_text='{"speech":"Unused reply.","emotion":"happy"}',
                            output=[{"type": "message", "content": [{"type": "refusal"}]}]),
            CloudError("server", "Synthetic server failure."),
        )
        for bad in failures:
            with self.subTest(kind=type(bad).__name__):
                s = self.session([("First crying reply.", "crying"), bad,
                                  ("Clean recovery reply.", "happy")])
                self.seed(s)
                saved = dict(s.voice._cache)
                thinking, release, original = self.gate(s.client, "create")
                s.start(); self.assertTrue(thinking.wait(1))
                self.assertEqual((s.snapshot()["phase"], s.snapshot()["emotion"]), ("thinking", "crying"))
                release.set(); self.finish(s)
                self.assertEqual(s.snapshot()["playback"], "error")
                self.assertEqual(s.snapshot()["emotion"], "crying")
                self.assertEqual(s.snapshot()["cues"], [])
                self.assertEqual(len(s.observations), 1)
                self.assertEqual(s.voice._cache["path"], saved["path"])
                self.assertEqual(len(s.brain.history), 2)
                s.client.create = original
                s.start(); self.finish(s)
                self.assertEqual(s.observations[-1][0]["emotion"], "crying")
                self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_cancel_before_started_at_each_dependency_keeps_display_and_prior_cache(self):
        for boundary in ("listen", "create", "_synthesize", "_player"):
            with self.subTest(boundary=boundary):
                s = self.session([("First crying reply.", "crying"),
                                  ("Next happy reply.", "happy"), ("Recovery happy reply.", "happy")])
                self.seed(s)
                saved = dict(s.voice._cache)
                target = s.client if boundary == "create" else s.voice
                entered, release, original = self.gate(target, boundary)
                s.start(); self.assertTrue(entered.wait(1))
                self.assertEqual(s.snapshot()["emotion"], "crying")
                self.assertTrue(s.cancel())
                self.assertEqual(s.browser_snapshot()["emotion"], "crying")
                self.assertFalse(s.start())
                release.set(); self.finish(s)
                self.assertEqual(s.snapshot()["emotion"], "crying")
                self.assertEqual(s.snapshot()["cues"], [])
                self.assertIsNone(s._staged_expression)
                self.assertEqual(s.voice._cache["path"], saved["path"])
                self.assertEqual(s.voice._cache["expression"], saved["expression"])
                self.assertEqual(len(s.observations), 1)
                setattr(target, boundary, original)
                s.start(); self.finish(s)
                self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_cancel_after_started_keeps_new_display_but_repeat_uses_prior_success(self):
        s = self.session([("First crying reply.", "crying"), ("Started happy reply.", "happy")])
        self.seed(s)
        saved = dict(s.voice._cache)
        entered, release = Event(), Event()
        self.releases.append(release)
        player = s.voice._player

        def playing(command, *, cancel_check, **settings):
            player(command, cancel_check=cancel_check, **settings)
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic player gate timed out")
            cancel_check()

        s.voice._player = playing
        s.start(); self.assertTrue(entered.wait(1))
        self.assertEqual((s.snapshot()["phase"], s.snapshot()["emotion"]), ("speaking", "happy"))
        s.cancel()
        self.assertEqual(s.snapshot()["emotion"], "happy")
        release.set(); self.finish(s)
        self.assertEqual(s.voice._cache["path"], saved["path"])
        self.assertEqual(s.voice._cache["expression"]["emotion"], "crying")
        s.voice._player = player
        requests, history = len(s.client.requests), list(s.brain.history)
        with patch.object(s.voice, "_synthesize", side_effect=AssertionError("Repeat synthesis forbidden")), \
             patch.object(integration_voice, "attenuated_copy", side_effect=AssertionError("Repeat gain forbidden")):
            self.assertTrue(s.repeat()); self.finish(s)
        before, started = s.observations[-1]
        self.assertEqual(before["emotion"], "happy")
        self.assertEqual(started["emotion"], "crying")
        self.assertEqual(s.snapshot()["emotion"], "crying")
        self.assertEqual(len(s.client.requests), requests)
        self.assertEqual(s.brain.history, history)
        self.assertEqual(s.voice._cache["cues"], saved["cues"])

    def test_synthesis_and_prestart_player_errors_keep_display_without_new_speaking(self):
        for method in ("_synthesize", "_player"):
            with self.subTest(method=method):
                s = self.session([("First crying reply.", "crying"),
                                  ("Failed happy reply.", "happy"), ("Recovery neutral reply.", "neutral")])
                self.seed(s)
                original = getattr(s.voice, method)
                error = audio.AudioSynthesisError if method == "_synthesize" else audio.AudioPlaybackError
                setattr(s.voice, method, Mock(side_effect=error("Synthetic failure.")))
                s.start(); self.finish(s)
                self.assertEqual((s.snapshot()["playback"], s.snapshot()["emotion"]), ("error", "crying"))
                self.assertEqual(len(s.observations), 1)
                self.assertEqual(s.snapshot()["cues"], [])
                self.assertEqual(s.voice._cache["expression"]["emotion"], "crying")
                setattr(s.voice, method, original)
                s.start(); self.finish(s)
                self.assertEqual(s.observations[-1][0]["emotion"], "crying")
                self.assertEqual(s.snapshot()["emotion"], "neutral")

    def test_disconnect_reconnect_discards_late_candidate_without_clearing_display(self):
        s = self.session([("First crying reply.", "crying"),
                          ("Late happy reply.", "happy"), ("Recovery happy reply.", "happy")])
        self.seed(s)
        entered, release, original = self.gate(s.voice, "_synthesize")
        s.browser_snapshot(); s.start(); self.assertTrue(entered.wait(1))
        s.test_clock.now = 4
        s.poll()
        self.assertEqual((s.browser_snapshot()["phase"], s.snapshot()["emotion"]), ("idle", "crying"))
        release.set(); self.finish(s)
        self.assertEqual(len(s.observations), 1)
        self.assertEqual(s.snapshot()["emotion"], "crying")
        s.voice._synthesize = original
        s.start(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_close_during_delayed_llm_or_tts_keeps_display_and_rejects_late_result(self):
        for boundary in ("create", "_synthesize"):
            with self.subTest(boundary=boundary):
                s = self.session([("First crying reply.", "crying"), ("Late happy reply.", "happy")])
                self.seed(s)
                path = s.voice._cache["path"]
                target = s.client if boundary == "create" else s.voice
                entered, release, _ = self.gate(target, boundary)
                s.start(); self.assertTrue(entered.wait(1))
                worker = s.worker._thread
                s.close()
                self.assertEqual(s.snapshot()["emotion"], "crying")
                release.set(); worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertTrue(s.client.closed.wait(1))
                self.assertFalse(path.exists())
                self.assertEqual(s.snapshot()["emotion"], "crying")
                self.assertEqual(len(s.observations), 1)
                self.assertFalse(s.voice.has_cache)

    def test_reset_is_explicit_neutral_with_mode_history_cache_and_pending_cleanup(self):
        for busy in (False, True):
            with self.subTest(busy=busy):
                s = self.session([("First crying reply.", "crying"), ("Late happy reply.", "happy")])
                self.seed(s)
                path = s.voice._cache["path"]
                if busy:
                    entered, release, _ = self.gate(s.voice, "_synthesize")
                    s.start(); self.assertTrue(entered.wait(1))
                s.reset()
                self.assertEqual(s.snapshot()["emotion"], "neutral")
                if busy:
                    release.set(); self.finish(s)
                self.assertEqual(s.snapshot()["emotion_mode"], "auto")
                self.assertEqual(s.snapshot()["emotion"], "neutral")
                self.assertEqual(s.brain.history, [])
                self.assertFalse(path.exists())
                self.assertIsNone(s._staged_expression)
                self.assertIsNone(s.expression_client.last_reply)

    def test_repeat_keeps_current_display_until_started_then_persists_cached_expression(self):
        s = self.session([("First crying reply.", "crying")])
        self.seed(s)
        saved = dict(s.voice._cache)
        s.set_emotion("sad"); s.set_emotion("auto")
        self.assertEqual((s.snapshot()["emotion_mode"], s.snapshot()["emotion"]), ("auto", "sad"))
        entered, release, _ = self.gate(s.voice, "_player")
        with patch.object(s.brain, "ask", side_effect=AssertionError("Repeat GPT forbidden")), \
             patch.object(s.voice, "_synthesize", side_effect=AssertionError("Repeat synthesis forbidden")), \
             patch.object(integration_voice, "attenuated_copy", side_effect=AssertionError("Repeat gain forbidden")), \
             patch.object(integration_voice, "wav_cues", side_effect=AssertionError("Repeat cue rebuild forbidden")):
            self.assertTrue(s.repeat()); self.assertTrue(entered.wait(1))
            self.assertEqual((s.snapshot()["playback"], s.snapshot()["emotion"]), ("prepared", "sad"))
            release.set(); self.finish(s)
        self.assertEqual(s.observations[-1][1]["emotion"], "crying")
        s.test_clock.now = 3600
        s.poll()
        self.assertEqual(s.snapshot()["emotion"], "crying")
        self.assertEqual(s.voice._cache["path"], saved["path"])
        self.assertEqual(s.voice._cache["cues"], saved["cues"])
        self.assertEqual(len(s.client.requests), 1)

    def test_notice_repeat_restores_current_dialogue_instead_of_caching_prior_display(self):
        s = self.session([("First crying reply.", "crying"),
                          CloudError("connection", "Synthetic connection failure.")],
                         offline=True, real_voice=True)
        s.voice._fish_synthesizer = Mock(side_effect=lambda *a, **kw: synthetic_wav())
        s.voice._piper_synthesizer = Mock(side_effect=lambda *a, **kw: synthetic_wav())
        self.seed(s)
        s.start(); self.finish(s)
        cached = dict(s.voice._cache)
        self.assertEqual(cached["expression"]["emotion"], "neutral")
        self.assertTrue(cached["expression"]["notice"])
        s.set_emotion("sad"); s.set_emotion("auto")
        with patch.object(s.brain, "ask", side_effect=AssertionError("Repeat GPT forbidden")), \
             patch.object(s.voice, "_synthesize", side_effect=AssertionError("Repeat synthesis forbidden")):
            self.assertTrue(s.repeat()); self.finish(s)
        self.assertEqual(s.observations[-1][0]["emotion"], "sad")
        self.assertEqual(s.observations[-1][1]["emotion"], "neutral")
        self.assertEqual(s.snapshot()["emotion"], "sad")
        self.assertEqual(s.voice._cache["path"], cached["path"])
        self.assertEqual(s.voice._cache["expression"], cached["expression"])
        self.assertEqual(len(s.client.requests), 2)

    def test_notice_terminal_cleanup_restores_prior_unless_explicit_override_or_reset(self):
        for action, expected in (("cancel", "crying"), ("error", "crying"),
                                 ("override", "happy"), ("reset", "neutral")):
            with self.subTest(action=action):
                s = self.session([("First crying reply.", "crying"),
                                  CloudError("connection", "Synthetic connection failure.")],
                                 offline=True, real_voice=True)
                s.voice._fish_synthesizer = Mock(side_effect=lambda *a, **kw: synthetic_wav())
                s.voice._piper_synthesizer = Mock(side_effect=lambda *a, **kw: synthetic_wav())
                self.seed(s)
                entered, release = Event(), Event()
                self.releases.append(release)
                player = s.voice._player

                def notice_playing(command, *, cancel_check, **settings):
                    player(command, cancel_check=cancel_check, **settings)
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("Synthetic notice gate timed out")
                    cancel_check()
                    if action == "error":
                        raise audio.AudioPlaybackError("Synthetic notice playback failure.")

                s.voice._player = notice_playing
                s.start(); self.assertTrue(entered.wait(1))
                self.assertEqual((s.snapshot()["emotion"], s.snapshot()["emotion_validation"]),
                                 ("neutral", "notice"))
                if action == "cancel":
                    s.cancel()
                elif action == "override":
                    s.set_emotion("happy"); s.set_emotion("auto")
                elif action == "reset":
                    s.reset()
                release.set(); self.finish(s)
                self.assertEqual(s.snapshot()["emotion"], expected)
                self.assertIsNone(s._notice_restore)


if __name__ == "__main__":
    unittest.main()
