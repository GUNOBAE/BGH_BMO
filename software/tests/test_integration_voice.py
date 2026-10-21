"""Integration voice contracts with fake synthesis and no devices/cloud."""
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai import audio, integration_voice
from bmo_ai.app import OFFLINE_KOREAN_UNSUPPORTED_EN, OFFLINE_NOTICE_EN
from bmo_ai.config import BMOConfig
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


class IntegrationVoiceTests(unittest.TestCase):
    def setUp(self):
        self.config = BMOConfig(tts_provider="fish", fish_gain=.25)
        self.client = Mock()
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(audio, "record_wav", side_effect=AssertionError("microphone forbidden")))
        self.enterContext(patch.object(audio, "transcribe", side_effect=AssertionError("STT forbidden")))

    def new_voice(self, config=None, **kwargs):
        events = []
        voice = IntegrationVoice(config or self.config, self.client,
                                 emit=lambda kind, **data: events.append((kind, data)), **kwargs)
        self.addCleanup(voice.close)
        return voice, events

    @staticmethod
    def immediate_player(command, *, on_start, cancel_check, **kwargs):
        cancel_check()
        on_start()

    def test_gain_once_actual_output_order_and_cleanup(self):
        raw_paths, ready_paths, trace = [], [], []

        def synthesize(text, **settings):
            self.assertEqual(text, "[gentle, warm, slightly lower pitch, still lightly playful] Hello Beemo.")
            self.assertEqual(settings["model"], "s2.1-pro-free")
            self.assertEqual(settings["reference_id"], self.config.fish_voice_id)
            path = synthetic_wav()
            raw_paths.append(path)
            return path

        def emit(kind, **data):
            trace.append(kind)
            self.assertNotIn("text", data)
            if kind == "prepared":
                path = Path(data["path"])
                ready_paths.append(path)
                self.assertTrue(path.exists())
                with wave.open(str(path), "rb") as stream:
                    samples = stream.readframes(stream.getnframes())
                peak = max(abs(int.from_bytes(samples[i:i+2], "little", signed=True))
                           for i in range(0, len(samples), 2))
                self.assertEqual(peak, 3000)
                self.assertEqual(data["source"], "fish")

        voice, _ = self.new_voice(fish_synthesizer=synthesize, player=self.immediate_player)
        voice.emit = emit
        with patch.object(integration_voice, "attenuated_copy", wraps=integration_voice.attenuated_copy) as gain:
            voice.speak("Hello BMO.", on_playback_start=lambda: trace.append("callback"))
        gain.assert_called_once()
        self.assertEqual(trace, ["prepared", "callback", "started", "ended"])
        self.assertTrue(all(not path.exists() for path in raw_paths + ready_paths))
        self.client.responses.create.assert_not_called()

    def test_simulate_never_uses_real_synthesis_or_devices(self):
        voice, events = self.new_voice(simulate=True, player=self.immediate_player)
        voice.message = "Synthetic user message."
        with patch.object(integration_voice.fish_tts, "synthesize_wav", side_effect=AssertionError("Fish forbidden")), \
             patch.object(integration_voice.local_piper, "synthesize_wav", side_effect=AssertionError("Piper forbidden")):
            self.assertEqual(voice.listen(), voice.message)
            voice.speak("Synthetic reply.")
        self.assertEqual([kind for kind, _ in events], ["prepared", "started", "ended"])
        self.assertTrue(all(data["source"] == "synthetic" for _, data in events))
        self.client.assert_not_called()

    def test_opt_in_piper_reuses_answer_or_fixed_english_notice_without_gpt(self):
        for text, expected in (("Hello BMO.", "Hello BMO."),
                               ("안녕하세요.", OFFLINE_KOREAN_UNSUPPORTED_EN)):
            piper = Mock(side_effect=lambda *args, **kwargs: synthetic_wav())
            fish = Mock(side_effect=audio.AudioSynthesisError("Synthetic failure.", "fish_connection"))
            voice, events = self.new_voice(BMOConfig(tts_provider="fish", fish_gain=.25, offline_voice=True),
                                          fish_synthesizer=fish, piper_synthesizer=piper,
                                          player=self.immediate_player)
            voice.speak(text)
            self.assertEqual(piper.call_args.args, (expected,))
            self.assertEqual(piper.call_args.kwargs["name_mode"], "beemo")
            self.assertEqual([kind for kind, _ in events], ["prepared", "started", "ended"])
            self.assertTrue(all(data["source"] == "piper" for _, data in events))
            self.client.responses.create.assert_not_called()

    def test_fallback_remains_opt_in_and_failure_does_not_block_next_turn(self):
        fish = Mock(side_effect=[audio.AudioSynthesisError("Synthetic failure."), synthetic_wav()])
        piper = Mock(side_effect=AssertionError("Piper not enabled"))
        voice, events = self.new_voice(fish_synthesizer=fish, piper_synthesizer=piper,
                                      player=self.immediate_player)
        with self.assertRaises(audio.AudioSynthesisError):
            voice.speak("First.")
        voice.speak("Second.")
        piper.assert_not_called()
        self.assertEqual([kind for kind, _ in events], ["error", "prepared", "started", "ended"])

    def test_late_synthesis_result_is_discarded_without_start(self):
        cancelled = Event()
        raw_paths = []

        def check():
            if cancelled.is_set():
                raise TurnCancelled()

        def synthesize(*args, **kwargs):
            path = synthetic_wav()
            raw_paths.append(path)
            cancelled.set()
            return path

        player = Mock()
        voice, events = self.new_voice(cancel_check=check, fish_synthesizer=synthesize, player=player)
        with self.assertRaises(TurnCancelled):
            voice.speak("Late result.")
        self.assertEqual([kind for kind, _ in events], ["cancelled"])
        self.assertFalse(raw_paths[0].exists())
        player.assert_not_called()
        cancelled.clear()
        voice._fish_synthesizer = lambda *args, **kwargs: synthetic_wav()
        voice._player = self.immediate_player
        voice.speak("Next turn.")
        self.assertEqual(events[-1][0], "ended")

    def test_cancel_after_prepared_never_starts_and_cleans_output(self):
        cancelled = Event()
        paths = []

        def check():
            if cancelled.is_set():
                raise TurnCancelled()

        def emit(kind, **data):
            if kind == "prepared":
                paths.append(Path(data["path"]))
                cancelled.set()

        voice, _ = self.new_voice(simulate=True, cancel_check=check, player=Mock())
        voice.emit = emit
        with self.assertRaises(TurnCancelled):
            voice.speak("Cancel before playback.")
        voice._player.assert_not_called()
        self.assertFalse(paths[0].exists())

    def test_cancel_while_synthesis_is_blocked_rejects_duplicate_then_discards(self):
        entered, release, cancelled = Event(), Event(), Event()
        paths, failures = [], []
        def check():
            if cancelled.is_set():
                raise TurnCancelled()
        def synthesize(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test release did not arrive")
            path = synthetic_wav()
            paths.append(path)
            return path
        voice, events = self.new_voice(cancel_check=check, fish_synthesizer=synthesize,
                                      player=Mock())
        def run():
            try:
                voice.speak("A blocked provider result.")
            except BaseException as failure:
                failures.append(failure)
        worker = Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            cancelled.set()
            with self.assertRaises(audio.AudioPlaybackError) as caught:
                voice.speak("Duplicate.")
            self.assertEqual(caught.exception.code, "player_busy")
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(failures[0], TurnCancelled)
            self.assertEqual([kind for kind, _ in events], ["cancelled"])
            self.assertFalse(paths[0].exists())
            voice._player.assert_not_called()
        finally:
            release.set()
            worker.join(2)

    def test_real_listen_boundary_forwards_settings_and_cleans_recording(self):
        recorded = synthetic_wav()
        voice, _ = self.new_voice()
        with patch.object(audio, "record_wav", return_value=recorded) as record, \
             patch.object(audio, "transcribe", return_value="Synthetic transcript.") as transcribe:
            self.assertEqual(voice.listen(), "Synthetic transcript.")
        record.assert_called_once_with(self.config.seconds, self.config.mic_device)
        transcribe.assert_called_once_with(self.client, recorded,
                                           model="gpt-4o-mini-transcribe", language=None)
        self.assertFalse(recorded.exists())

    def test_player_error_cleans_files_and_next_turn_works(self):
        paths = []
        player = Mock(side_effect=audio.AudioPlaybackError("Synthetic failure."))
        voice, events = self.new_voice(simulate=True, player=player)
        def emit(kind, **data):
            events.append((kind, data))
            if "path" in data:
                paths.append(Path(data["path"]))
        voice.emit = emit
        with self.assertRaises(audio.AudioPlaybackError):
            voice.speak("Failed playback.")
        self.assertEqual(events[-1][0], "error")
        self.assertTrue(all(not path.exists() for path in paths))
        voice._player = self.immediate_player
        voice.speak("Next turn.")
        self.assertEqual(events[-1][0], "ended")

    def test_fixed_offline_notice_uses_piper_and_same_events(self):
        piper = Mock(side_effect=lambda *args, **kwargs: synthetic_wav())
        fish = Mock(side_effect=AssertionError("No Fish for cloud failure notice"))
        voice, events = self.new_voice(BMOConfig(tts_provider="fish", offline_voice=True),
                                      piper_synthesizer=piper, fish_synthesizer=fish,
                                      player=self.immediate_player)
        voice.offline_notice()
        self.assertEqual(piper.call_args.args, (OFFLINE_NOTICE_EN,))
        self.assertEqual(events[-1], ("ended", {"source": "piper"}))
        fish.assert_not_called()

    def test_cancellation_setter_is_refreshed_per_turn(self):
        cancelled = Mock(side_effect=TurnCancelled())
        voice, events = self.new_voice(simulate=True, cancel_check=cancelled,
                                      player=self.immediate_player)
        with self.assertRaises(TurnCancelled):
            voice.speak("Cancelled turn.")
        voice.cancel_check = lambda: None
        voice.speak("Fresh turn.")
        self.assertEqual(events[-1][0], "ended")


if __name__ == "__main__":
    unittest.main()
