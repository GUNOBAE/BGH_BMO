"""Independent notice/cancellation review with generated PCM and no real IO."""
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.audio import AudioPlaybackError
from bmo_ai.cloud import CloudError
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from test_emotion_session import Clock, ReplyClient


class ContinuityNoticeReviewTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                       "bmo_ai.cloud.create_client", "bmo_ai.audio.record_wav",
                       "bmo_ai.audio.transcribe", "bmo_ai.fish_tts.synthesize_wav",
                       "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("External IO forbidden")))
        self.releases = []
        self.client = ReplyClient([
            ("A quiet fixture reply.", "crying"),
            CloudError("connection", "Synthetic provider failure."),
            ("A bright fixture reply.", "happy"),
        ])
        self.config = integration_config(offline_voice=True)
        self.player_hook = None
        self.started = []

        def player(command, *, on_start, cancel_check, **kwargs):
            cancel_check()
            on_start()
            self.started.append(self.session.snapshot())
            if self.player_hook is not None:
                self.player_hook()
            cancel_check()

        self.voice = IntegrationVoice(
            self.config, self.client, emit=lambda *a, **kw: None,
            fish_synthesizer=lambda *a, **kw: synthetic_wav(),
            piper_synthesizer=lambda *a, **kw: synthetic_wav(), player=player,
        )
        self.voice.listen = lambda: "A synthetic question."
        self.session = IntegrationSession(self.config, self.client, simulate=True,
                                          voice=self.voice, emotion_mode="auto", clock=Clock())
        self.synthesis = self.enterContext(patch.object(
            self.voice, "_synthesize", wraps=self.voice._synthesize))
        self.assertTrue(self.session.start())
        self.assertEqual(self.finish()[-1].code, "ok")
        self.assertEqual(self.session.snapshot()["emotion"], "crying")
        self.old_cache_path = self.voice._cache["path"]
        self.old_cache_expression = dict(self.voice._cache["expression"])

    def tearDown(self):
        for release in self.releases:
            release.set()
        self.session.close()
        thread = self.session.worker._thread
        if thread is not None:
            thread.join(timeout=3)
        self.assertFalse(self.session.worker.worker_alive)

    def finish(self):
        result, deadline = [], time.monotonic() + 3
        while self.session.busy and time.monotonic() < deadline:
            result.extend(self.session.poll())
            time.sleep(.001)
        self.assertFalse(self.session.busy)
        result.extend(self.session.poll())
        return result

    def block_notice_after_started(self):
        entered, release = Event(), Event()
        self.releases.append(release)

        def gate():
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic notice gate timed out")

        self.player_hook = gate
        self.assertTrue(self.session.start())
        self.assertTrue(entered.wait(1))
        self.assertEqual((self.session.snapshot()["emotion"],
                          self.session.snapshot()["emotion_validation"]), ("neutral", "notice"))
        return release

    def test_cancel_started_notice_restores_dialogue_and_preserves_success_cache(self):
        release = self.block_notice_after_started()
        self.assertTrue(self.session.cancel())
        self.assertEqual(self.session.snapshot()["emotion"], "crying")
        release.set()
        self.assertEqual(self.finish()[-1].code, "cancelled")
        self.assertEqual(self.session.snapshot()["emotion"], "crying")
        self.assertEqual(self.voice._cache["path"], self.old_cache_path)
        self.assertEqual(self.voice._cache["expression"], self.old_cache_expression)
        before_synthesis, before_requests = self.synthesis.call_count, len(self.client.requests)
        self.player_hook = None
        self.assertTrue(self.session.repeat())
        self.assertEqual(self.finish()[-1].code, "ok")
        self.assertEqual(self.started[-1]["emotion"], "crying")
        self.assertEqual((self.synthesis.call_count, len(self.client.requests)),
                         (before_synthesis, before_requests))

    def test_notice_player_error_restores_dialogue_without_promoting_notice_cache(self):
        def fail():
            raise AudioPlaybackError("Synthetic playback failure.", "device_unavailable")

        self.player_hook = fail
        self.assertTrue(self.session.start())
        self.assertEqual(self.finish()[-1].code, "connection")
        self.assertEqual((self.started[-1]["emotion"], self.started[-1]["emotion_validation"]),
                         ("neutral", "notice"))
        self.assertEqual(self.session.snapshot()["emotion"], "crying")
        self.assertEqual(self.voice._cache["path"], self.old_cache_path)
        self.assertEqual(self.voice._cache["expression"], self.old_cache_expression)

    def test_reset_during_notice_invalidates_prior_restore_and_cache(self):
        release = self.block_notice_after_started()
        self.session.reset()
        self.assertEqual(self.session.snapshot()["emotion"], "neutral")
        release.set()
        self.assertEqual(self.finish()[-1].code, "cancelled")
        self.assertEqual((self.session.snapshot()["emotion_mode"],
                          self.session.snapshot()["emotion"]), ("auto", "neutral"))
        self.assertEqual(self.session.brain.history, [])
        self.assertIsNone(self.voice._cache)
        self.assertFalse(self.old_cache_path.exists())

    def test_mode_override_during_notice_wins_and_repeat_scopes_new_neutral_notice(self):
        release = self.block_notice_after_started()
        self.session.set_emotion("happy")
        self.session.set_emotion("auto")
        self.assertEqual(self.session.snapshot()["emotion"], "happy")
        release.set()
        self.assertEqual(self.finish()[-1].code, "connection")
        self.assertEqual(self.session.snapshot()["emotion"], "happy")
        self.assertTrue(self.voice._cache["expression"]["notice"])
        before_synthesis, before_requests = self.synthesis.call_count, len(self.client.requests)
        self.player_hook = None
        self.assertTrue(self.session.repeat())
        self.assertEqual(self.finish()[-1].code, "ok")
        self.assertEqual((self.started[-1]["emotion"], self.started[-1]["emotion_validation"]),
                         ("neutral", "notice"))
        self.assertEqual(self.session.snapshot()["emotion"], "happy")
        self.assertEqual((self.synthesis.call_count, len(self.client.requests)),
                         (before_synthesis, before_requests))

    def test_stale_notice_terminal_callbacks_do_not_end_or_restore_over_new_turn(self):
        self.assertTrue(self.session.start())
        self.assertEqual(self.finish()[-1].code, "connection")
        old_emit = self.voice.emit
        entered, release = Event(), Event()
        self.releases.append(release)

        def gate():
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic reply gate timed out")

        self.player_hook = gate
        self.assertTrue(self.session.start())
        self.assertTrue(entered.wait(1))
        self.assertEqual((self.session.snapshot()["emotion"], self.session.snapshot()["playback"]),
                         ("happy", "started"))
        old_emit("ended")
        old_emit("error")
        self.assertEqual((self.session.snapshot()["emotion"], self.session.snapshot()["playback"]),
                         ("happy", "started"))
        release.set()
        self.assertEqual(self.finish()[-1].code, "ok")
        self.assertEqual(self.session.snapshot()["emotion"], "happy")


if __name__ == "__main__":
    unittest.main()
