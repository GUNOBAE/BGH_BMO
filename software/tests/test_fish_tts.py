"""Offline tests for optional Fish TTS; no paid requests or microphone required."""
import io
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from bmo_ai.config import BMOConfig, ConfigError
from bmo_ai.audio import AudioValidationError
from bmo_ai import fish_tts


class FishTTSSettingsTests(unittest.TestCase):
    def test_free_model_and_aaron_default(self):
        cfg = BMOConfig.from_env({"BMO_TTS_PROVIDER": "fish"})
        self.assertEqual(cfg.fish_model, "s2.1-pro-free")
        self.assertEqual(cfg.fish_voice_id, fish_tts.AARON_BMO_VOICE_ID)

    def test_invalid_options_rejected_before_audio(self):
        for setting in (
            {"BMO_TTS_PROVIDER": "invalid"},
            {"BMO_FISH_MODEL": "unlisted"},
            {"BMO_FISH_VOICE_ID": "short"},
        ):
            with self.subTest(setting=next(iter(setting))), self.assertRaises(ConfigError):
                BMOConfig.from_env(setting)

    def test_fish_speak_rejects_unknown_model_without_network(self):
        with patch("bmo_ai.fish_tts.urlopen") as request:
            with self.assertRaises(AudioValidationError):
                fish_tts.speak("hello", model="unlisted")
            request.assert_not_called()


    def test_explicit_model_and_playback_with_cleanup(self):
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as wave_file:
            wave_file.setnchannels(1)
            wave_file.setsampwidth(2)
            wave_file.setframerate(16000)
            wave_file.writeframes(b"\x00\x00" * 320)

        class Response:
            def __init__(self):
                self.done = False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, _):
                if self.done:
                    return b""
                self.done = True
                return audio_buffer.getvalue()

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "fish.wav"
            with patch.dict(os.environ, {"FISH_API_KEY": "test-only"}), patch(
                "bmo_ai.fish_tts.urlopen", return_value=Response()
            ) as send, patch("bmo_ai.fish_tts.audio._new_wav", return_value=destination), patch(
                "bmo_ai.fish_tts.audio._run"
            ) as player:
                fish_tts.speak("BMO is here!", model="drama-3")
            self.assertEqual(send.call_args.args[0].get_header("Model"), "drama-3")
            self.assertEqual(send.call_count, 1)
            self.assertEqual(player.call_count, 1)
            self.assertEqual(player.call_args.args[0][0], "aplay")
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
