"""Automatic expressions through the real session/worker with synthetic IO.

Continuity v2 supersedes Stage 1's new-turn/two-second/cancel neutral policy;
the staging, validation, cache and cancellation checks remain in place.
"""
from collections import deque
import json
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio, integration_voice
from bmo_ai.app import OFFLINE_KOREAN_UNSUPPORTED_EN, OFFLINE_NOTICE_EN
from bmo_ai.cloud import CloudError
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession, SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ReplyClient(SimulatedClient):
    def __init__(self, replies):
        super().__init__()
        self.replies = deque(replies)
        self.closed = Event()

    def create(self, **request):
        self.requests.append(request)
        reply = self.replies.popleft()
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, SimpleNamespace):
            return reply
        if isinstance(reply, tuple):
            speech, emotion = reply
            reply = json.dumps(dict(speech=speech, emotion=emotion)) if "text" in request else speech
        return SimpleNamespace(status="completed", incomplete_details=None, error=None,
                               output_text=reply)

    def close(self):
        self.closed.set()


class AutomaticSessionTests(unittest.TestCase):
    def setUp(self):
        self.sessions, self.releases = [], []
        for target in ("socket.socket.connect", "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Live IO forbidden")))

    def tearDown(self):
        for release in self.releases:
            release.set()
        for session in self.sessions:
            session.close()

    def session(self, replies, *, mode="auto", offline=False, real_voice=False):
        clock, client = Clock(), ReplyClient(replies)
        config = integration_config(offline_voice=offline)
        observations = []

        def player(command, *, on_start, cancel_check, **settings):
            cancel_check()
            before = session.snapshot()
            on_start()
            observations.append((before, session.snapshot(), Path(command[-1]) if real_voice else None))
            cancel_check()

        voice = IntegrationVoice(config, client, simulate=not real_voice,
                                 emit=lambda *a, **kw: None, player=player)
        voice.listen = lambda: "Synthetic question."
        session = IntegrationSession(config, client, simulate=True, voice=voice,
                                     emotion_mode=mode, clock=clock)
        session.observations = observations
        session.test_clock = clock
        self.sessions.append(session)
        return session

    def finish(self, session):
        deadline, events = time.monotonic() + 3, []
        while session.busy and time.monotonic() < deadline:
            events.extend(session.poll())
            time.sleep(.002)
        self.assertFalse(session.busy)
        events.extend(session.poll())
        return events

    def gate(self, target, attribute):
        entered, release = Event(), Event()
        self.releases.append(release)
        original = getattr(target, attribute)

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic dependency gate timed out")
            return original(*args, **kwargs)

        setattr(target, attribute, delayed)
        return entered, release, original

    def test_auto_reply_stages_until_player_and_persists_until_next_started(self):
        s = self.session([("A happy little discovery.", "happy"), ("A simple fact.", "neutral")])
        brain = s.brain
        self.assertIs(s.voice.client, s.client)
        self.assertTrue(s.start())
        self.finish(s)
        before, started, _ = s.observations[0]
        self.assertEqual((before["phase"], before["emotion"]), ("thinking", "neutral"))
        self.assertEqual((started["phase"], started["emotion"]), ("speaking", "happy"))
        self.assertEqual(s.snapshot()["emotion_validation"], "validated")
        s.test_clock.now = 1.999
        s.poll()
        self.assertEqual(s.snapshot()["emotion"], "happy")
        s.test_clock.now = 2.0
        s.poll()
        self.assertEqual(s.snapshot()["emotion"], "happy")
        self.assertTrue(s.start())
        self.finish(s)
        self.assertEqual(s.observations[-1][0]["emotion"], "happy")
        self.assertIs(s.brain, brain)
        self.assertEqual([len(r["input"]) for r in s.client.requests], [1, 3])
        self.assertEqual(s.snapshot()["emotion"], "neutral")
        self.assertTrue(all("text" in request for request in s.client.requests))
        self.assertEqual([item["content"] for item in s.brain.history if item["role"] == "assistant"],
                         ["A happy little discovery.", "A simple fact."])
        self.assertTrue(all("speech" not in event and "last_reply" not in event for event in s.events))

    def test_next_turn_keeps_sad_until_started_and_does_not_expire_new_happy(self):
        s = self.session([("I am here with you.", "sad"), ("That is lovely news.", "happy")])
        s.start(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "sad")
        s.test_clock.now = 1.0
        entered, release = Event(), Event()
        self.releases.append(release)
        player = s.voice._player

        def playing(command, **settings):
            player(command, **settings)
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic playback gate timed out")

        s.voice._player = playing
        s.start()
        self.assertTrue(entered.wait(1))
        self.assertEqual(s.observations[-1][0]["emotion"], "sad")
        self.assertEqual(s.snapshot()["emotion"], "happy")
        s.test_clock.now = 3.0
        s.poll()
        self.assertEqual((s.snapshot()["phase"], s.snapshot()["emotion"]), ("speaking", "happy"))
        release.set()
        self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")
        s.test_clock.now = 4.999
        s.poll()
        self.assertEqual(s.snapshot()["emotion"], "happy")
        s.test_clock.now = 5.0
        s.poll()
        self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_default_manual_and_named_override_survive_until_auto_or_reset(self):
        s = self.session([("Plain first.", "sad"), ("Auto second.", "happy")], mode="manual")
        s.set_emotion("crying")
        s.start(); self.finish(s)
        self.assertNotIn("text", s.client.requests[0])
        self.assertEqual(s.snapshot()["emotion"], "crying")
        s.test_clock.now = 10
        s.poll(); s.cancel()
        self.assertEqual(s.snapshot()["emotion"], "crying")
        s.set_emotion("auto")
        self.assertEqual(s.snapshot()["emotion"], "crying")
        s.start(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")
        s.set_emotion("manual")
        s.test_clock.now = 20
        s.poll()
        self.assertEqual((s.snapshot()["emotion_mode"], s.snapshot()["emotion"]), ("manual", "happy"))
        s.reset()
        self.assertEqual((s.snapshot()["emotion_mode"], s.snapshot()["emotion"]), ("manual", "neutral"))
        self.assertEqual(s.brain.history, [])
        self.assertFalse(s.voice.has_cache)

    def test_busy_mode_changes_cannot_apply_old_auto_or_plain_request_metadata(self):
        for mode in ("auto", "manual"):
            with self.subTest(request_mode=mode):
                s = self.session([("Old reply.", "happy"), ("Current reply.", "sad")], mode=mode)
                entered, release, original = self.gate(s.client, "create")
                s.start()
                self.assertTrue(entered.wait(1))
                s.set_emotion("crying")
                s.set_emotion("auto")
                release.set(); self.finish(s)
                self.assertEqual("text" in s.client.requests[0], mode == "auto")
                self.assertEqual(s.snapshot()["emotion"], "crying")
                s.client.create = original
                s.start(); self.finish(s)
                self.assertEqual(s.snapshot()["emotion"], "sad")

    def test_missing_or_invalid_emotion_uses_clean_speech_and_neutral(self):
        for reply in ('{"speech":"Clean reply."}', '{"speech":"Clean reply.","emotion":"unknown"}'):
            with self.subTest(reply=reply):
                s = self.session([reply])
                s.start(); self.finish(s)
                self.assertEqual(s.snapshot()["emotion"], "neutral")
                self.assertEqual(s.snapshot()["emotion_validation"], "degraded")
                self.assertEqual(s.brain.history[-1]["content"], "Clean reply.")

    def test_unmatched_provisional_reply_keeps_fixed_expression_error_and_never_plays(self):
        s = self.session([("A valid next reply.", "happy")])
        ask = s.brain.ask
        s.brain.ask = lambda message: "Unmatched synthetic reply."
        s.start()
        events = self.finish(s)
        self.assertEqual([event.code for event in events if event.kind == "finished"],
                         ["expression_format"])
        self.assertEqual(s.observations, [])
        self.assertEqual(s.snapshot()["emotion"], "neutral")
        s.brain.ask = ask
        s.start(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_malformed_incomplete_and_unsafe_reply_never_play_then_next_turn_works(self):
        bad = ("not-json", '{"speech":"[laugh] Unsafe.","emotion":"happy"}',
               SimpleNamespace(status="incomplete", incomplete_details={"reason": "max_output_tokens"},
                               error=None, output_text='{"speech":"partial'))
        for reply in bad:
            with self.subTest(kind=type(reply).__name__):
                s = self.session([reply, ("Recovered clean answer.", "happy")])
                s.start(); self.finish(s)
                self.assertEqual(s.observations, [])
                self.assertEqual(s.snapshot()["playback"], "error")
                self.assertEqual(s.snapshot()["emotion"], "neutral")
                self.assertEqual(s.brain.history, [])
                s.start(); self.finish(s)
                self.assertEqual(s.snapshot()["emotion"], "happy")
                self.assertEqual(len(s.client.requests), 2)

    def test_cancel_delayed_llm_discards_expression_history_and_playback(self):
        s = self.session([("Late happy answer.", "happy")])
        entered, release, _ = self.gate(s.client, "create")
        s.start(); self.assertTrue(entered.wait(1))
        s.cancel()
        release.set(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "neutral")
        self.assertEqual(s.brain.history, [])
        self.assertEqual(s.observations, [])
        self.assertFalse(s.voice.has_cache)

    def test_cancel_delayed_tts_preserves_manual_override_and_ignores_late_candidate(self):
        s = self.session([("Late sad answer.", "sad")])
        entered, release, _ = self.gate(s.voice, "_synthesize")
        s.start(); self.assertTrue(entered.wait(1))
        self.assertEqual(s.snapshot()["emotion"], "neutral")
        s.set_emotion("angry")
        s.cancel()
        release.set(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "angry")
        self.assertEqual(s.snapshot()["emotion_mode"], "manual")
        self.assertEqual(s.observations, [])
        self.assertFalse(s.voice.has_cache)

    def test_mode_change_after_tts_staging_ignores_old_generation_at_player_start(self):
        s = self.session([("Happy cached reply.", "happy")])
        entered, release, _ = self.gate(s.voice, "_synthesize")
        s.start(); self.assertTrue(entered.wait(1))
        s.set_emotion("crying")
        s.set_emotion("auto")
        release.set(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "crying")
        # The validated semantic expression belongs to this successful WAV,
        # while an in-flight mode change prevents it controlling the new mode.
        self.assertEqual(s.voice._cache["expression"]["emotion"], "happy")
        self.assertTrue(s.repeat()); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_disconnect_and_reconnect_discard_pending_tts_expression(self):
        s = self.session([("Late happy answer.", "happy")])
        entered, release, _ = self.gate(s.voice, "_synthesize")
        s.browser_snapshot(); s.start(); self.assertTrue(entered.wait(1))
        s.test_clock.now = 4
        s.poll()
        self.assertEqual(s.browser_snapshot()["emotion"], "neutral")
        release.set(); self.finish(s)
        self.assertEqual(s.observations, [])
        self.assertEqual(s.snapshot()["cues"], [])

    def test_close_during_delayed_llm_or_tts_drops_late_emotion_and_owned_cache(self):
        for boundary in ("llm", "tts"):
            with self.subTest(boundary=boundary):
                s = self.session([("Late happy answer.", "happy")])
                target, method = (s.client, "create") if boundary == "llm" else (s.voice, "_synthesize")
                entered, release, _ = self.gate(target, method)
                s.start(); self.assertTrue(entered.wait(1))
                worker = s.worker._thread
                s.close()
                self.assertEqual(s.snapshot()["emotion"], "neutral")
                release.set(); worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertTrue(s.client.closed.wait(1))
                self.assertEqual(s.observations, [])
                self.assertFalse(s.voice.has_cache)

    def test_repeat_keeps_successful_wav_cues_and_emotion_after_failed_replacement(self):
        s = self.session([("First happy answer.", "happy"), ("Failed sad answer.", "sad")])
        s.start(); self.finish(s)
        first = dict(s.voice._cache)
        synthesize = s.voice._synthesize
        s.voice._synthesize = Mock(side_effect=audio.AudioSynthesisError("Synthetic failure."))
        s.start(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")
        self.assertEqual(s.voice._cache["expression"]["emotion"], "happy")
        s.voice._synthesize = synthesize
        history = [dict(item) for item in s.brain.history]
        with patch.object(s.brain, "ask", side_effect=AssertionError("Repeat GPT forbidden")), \
             patch.object(s.voice, "_synthesize", side_effect=AssertionError("Repeat TTS forbidden")), \
             patch.object(integration_voice, "attenuated_copy", side_effect=AssertionError("Repeat gain forbidden")):
            self.assertTrue(s.repeat()); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")
        self.assertEqual(s.voice._cache["path"], first["path"])
        self.assertEqual(s.voice._cache["cues"], first["cues"])
        self.assertEqual(s.brain.history, history)
        self.assertEqual(len(s.client.requests), 2)
        s.set_emotion("crying")
        s.repeat(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "crying")
        s.set_emotion("auto")
        s.repeat(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")

    def test_reset_during_delayed_tts_keeps_mode_clears_candidate_display_and_cache(self):
        s = self.session([("Late happy reply.", "happy"), ("New sad reply.", "sad")])
        entered, release, original = self.gate(s.voice, "_synthesize")
        s.start(); self.assertTrue(entered.wait(1))
        s.reset()
        release.set(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion_mode"], "auto")
        self.assertEqual(s.snapshot()["emotion"], "neutral")
        self.assertEqual(s.brain.history, [])
        self.assertFalse(s.voice.has_cache)
        self.assertIsNone(s.expression_client.last_reply)
        s.voice._synthesize = original
        s.start(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "sad")

    def test_piper_same_english_keeps_emotion_and_neutral_notices_restore_prior(self):
        s = self.session([("Lovely news.", "happy"), ("한국어 답변입니다.", "crying"),
                          CloudError("connection", "Synthetic cloud failure.")],
                         offline=True, real_voice=True)
        piper = Mock(side_effect=lambda *args, **kwargs: synthetic_wav())
        s.voice._piper_synthesizer = piper
        s.voice._fish_synthesizer = Mock(side_effect=audio.AudioSynthesisError("Synthetic Fish failure."))
        s.start(); self.finish(s)
        self.assertEqual(s.snapshot()["emotion"], "happy")
        self.assertEqual(s.observations[-1][1]["source"], "piper")
        self.assertFalse(s.voice._cache["expression"]["notice"])
        s.start(); self.finish(s)
        self.assertEqual(s.observations[-1][1]["emotion"], "neutral")
        self.assertEqual(s.snapshot()["emotion"], "happy")
        self.assertEqual(piper.call_args.args, (OFFLINE_KOREAN_UNSUPPORTED_EN,))
        self.assertTrue(s.voice._cache["expression"]["notice"])
        s.start(); self.finish(s)
        self.assertEqual(piper.call_args.args, (OFFLINE_NOTICE_EN,))
        self.assertEqual(s.observations[-1][1]["emotion"], "neutral")
        self.assertEqual(s.snapshot()["emotion"], "happy")
        self.assertTrue(s.voice._cache["expression"]["notice"])
        self.assertEqual(len(s.client.requests), 3)


if __name__ == "__main__":
    unittest.main()
