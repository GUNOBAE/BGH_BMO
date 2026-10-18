"""Offline tests for optional Fish TTS; no paid requests or microphone required."""
import unittest
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


if __name__ == "__main__":
    unittest.main()
