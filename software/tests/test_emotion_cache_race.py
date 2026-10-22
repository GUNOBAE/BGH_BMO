"""Deterministic cache-promotion races using generated PCM and a no-op player.

The gate pauses the worker immediately before its final cache lock, allowing
owner cancellation/reset/close to happen after playback's last outside check.
No cloud, audio device, model executable or browser is used.
"""
import json
from threading import Event, current_thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession
from bmo_ai.integration_voice import IntegrationVoice


class FixtureClient:
    def __init__(self):
        self.responses = self
        self.requests = []
        self.closed = Event()

    def create(self, **request):
        self.requests.append(request)
        emotion = "happy" if len(self.requests) == 1 else "sad"
        return SimpleNamespace(status="completed", error=None, incomplete_details=None,
                               output_text=json.dumps({"speech": "A clean fixture reply.",
                                                       "emotion": emotion}))

    def close(self):
        self.closed.set()


class PromotionGate:
    """Retain the real lock; owner operations may enter while the worker waits."""
    def __init__(self, lock):
        self.lock = lock
        self.entered, self.release = Event(), Event()
        self.worker_entries = 0

    def __enter__(self):
        if current_thread().name == "bmo-conversation":
            self.worker_entries += 1
            # speak() first reads the generation, then locks again to promote.
            if self.worker_entries == 2:
                self.entered.set()
                if not self.release.wait(3):
                    raise AssertionError("Synthetic cache-promotion gate timed out")
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class EmotionCacheRaceTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                       "bmo_ai.cloud.create_client", "bmo_ai.audio.record_wav",
                       "bmo_ai.audio.transcribe", "bmo_ai.fish_tts.synthesize_wav",
                       "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("External operation forbidden")))
        self.client = FixtureClient()
        config = integration_config()
        self.voice = IntegrationVoice(config, self.client, simulate=True,
                                      emit=lambda *args, **kwargs: None,
                                      player=lambda command, on_start, **kwargs: on_start())
        self.session = IntegrationSession(config, self.client, simulate=True,
                                          voice=self.voice, emotion_mode="auto")
        self.gate = None
        self.synthesis = self.enterContext(patch.object(
            self.voice, "_synthesize", wraps=self.voice._synthesize))
        self.assertTrue(self.session.start("First fixture question."))
        self.assertEqual(self.finish()[-1].code, "ok")
        self.saved_path = self.voice._cache["path"]
        self.saved_expression = dict(self.voice._cache["expression"])
        self.assertEqual(self.saved_expression["emotion"], "happy")

    def tearDown(self):
        if self.gate is not None:
            self.gate.release.set()
        self.session.close()
        thread = self.session.worker._thread
        if thread is not None:
            thread.join(timeout=3)
        self.assertFalse(self.session.worker.worker_alive)
        self.assertTrue(self.client.closed.wait(3))

    def finish(self):
        result = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result.extend(self.session.poll())
            if any(item.kind == "finished" for item in result):
                return result
            time.sleep(0.001)
        self.fail("Synthetic worker did not finish within its test bound")

    def block_replacement(self):
        self.gate = PromotionGate(self.voice._cache_lock)
        self.voice._cache_lock = self.gate
        self.assertTrue(self.session.start("Replacement fixture question."))
        self.assertTrue(self.gate.entered.wait(3))
        self.assertTrue(self.session.busy)

    def test_cancel_at_promotion_preserves_happy_cache_and_repeat_without_resynthesis(self):
        self.block_replacement()
        self.assertTrue(self.session.cancel())
        self.gate.release.set()
        self.assertEqual(self.finish()[-1].code, "cancelled")
        self.assertEqual(self.voice._cache["path"], self.saved_path)
        self.assertEqual(self.voice._cache["expression"], self.saved_expression)
        self.assertTrue(self.saved_path.exists())
        self.assertEqual(self.session.snapshot()["emotion"], "neutral")
        before_synthesis = self.synthesis.call_count
        before_requests = len(self.client.requests)
        self.assertTrue(self.session.repeat())
        self.assertEqual(self.finish()[-1].code, "ok")
        self.assertEqual(self.synthesis.call_count, before_synthesis)
        self.assertEqual(len(self.client.requests), before_requests)
        self.assertEqual(self.session.snapshot()["emotion"], "happy")
        self.assertEqual(self.voice._cache["path"], self.saved_path)

    def test_clear_cache_during_promotion_rejects_late_wav_and_expression(self):
        self.block_replacement()
        self.voice.clear_cache()
        self.gate.release.set()
        self.assertEqual(self.finish()[-1].code, "ok")
        self.assertIsNone(self.voice._cache)
        self.assertFalse(self.saved_path.exists())
        self.assertFalse(self.session.repeat())

    def test_reset_during_promotion_clears_history_cache_and_keeps_auto_mode(self):
        self.block_replacement()
        self.session.reset()
        self.gate.release.set()
        self.assertEqual(self.finish()[-1].code, "cancelled")
        self.assertIsNone(self.voice._cache)
        self.assertFalse(self.saved_path.exists())
        self.assertEqual(self.session.brain.history, [])
        self.assertEqual((self.session.snapshot()["emotion_mode"],
                          self.session.snapshot()["emotion"]), ("auto", "neutral"))
        self.assertFalse(self.session.repeat())
        self.assertIsNone(self.session.expression_client.last_reply)

    def test_close_during_promotion_cannot_publish_or_cache_late_expression(self):
        self.block_replacement()
        self.session.close()
        self.gate.release.set()
        thread = self.session.worker._thread
        if thread is not None:
            thread.join(timeout=3)
        self.assertTrue(self.client.closed.wait(3))
        self.assertIsNone(self.voice._cache)
        self.assertFalse(self.saved_path.exists())
        self.assertEqual(self.session.snapshot()["emotion"], "neutral")
        self.assertFalse(self.session.start("Rejected closed-session question."))
        self.assertFalse(self.session.repeat())


if __name__ == "__main__":
    unittest.main()
