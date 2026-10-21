"""BMO name pronunciation must affect Fish speech only, never chat transcripts."""
import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from bmo_ai.pronunciation import spoken_bmo_name
from bmo_ai.config import BMOConfig, ConfigError
from bmo_ai.app import ConversationApp
from bmo_ai import fish_tts


class SpokenNameTests(unittest.TestCase):
    def test_bmo_name_rewrite_does_not_corrupt_other_words_or_korean_suffix(self):
        source = "Hi, I'm BMO! BMO's buttons belong to BMO. BIMOD, myBMO, BMO가."
        self.assertEqual(
            spoken_bmo_name(source, "beemo"),
            "Hi, I'm Beemo! Beemo's buttons belong to Beemo. BIMOD, myBMO, BMO가.",
        )
        self.assertEqual(
            spoken_bmo_name("BMO is BMO.", "bee-mo"),
            "Bee-mo is Bee-mo.",
        )
        self.assertEqual(spoken_bmo_name(source, "literal"), source)

    def test_config_name_mode_is_validated(self):
        cfg = BMOConfig.from_env({"BMO_FISH_NAME_MODE": "bee-mo"})
        self.assertEqual(cfg.fish_name_mode, "bee-mo")
        self.assertEqual(BMOConfig().fish_name_mode, "beemo")
        for val in ("", "xyz", "BeeMo", "BMO", "automatic"):
            with self.subTest(val=val), self.assertRaises(ConfigError):
                BMOConfig.from_env({"BMO_FISH_NAME_MODE": val})

    def test_chat_text_keeps_bmo_spelling_while_fish_uses_beemo(self):
        brain = Mock()
        brain.ask.return_value = "Hello! I'm BMO."
        config = BMOConfig(tts_provider="fish", fish_name_mode="beemo")
        app = ConversationApp(brain, Mock(), config)
        answer = app.answer("Introduce yourself")
        self.assertEqual(answer, "Hello! I'm BMO.")
        with patch("bmo_ai.fish_tts.speak") as speak:
            app.playback(answer)
        self.assertEqual(speak.call_args.args[0], "Hello! I'm BMO.")
        self.assertEqual(speak.call_args.kwargs["name_mode"], "beemo")
        self.assertEqual(speak.call_args.kwargs["delivery"], "soft")

    def test_fish_speech_sends_spoken_text_but_does_not_mutate_input(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "test.wav"
            with wave.open(str(wav), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\x00\x00" * 1200)
            original = "Hey BMO! I'm BMO."
            with patch("bmo_ai.fish_tts.synthesize_wav", return_value=wav) as synth, patch(
                "bmo_ai.safe_playback.play_wav"
            ):
                fish_tts.speak(original, gain=.3)
            self.assertEqual(original, "Hey BMO! I'm BMO.")
            self.assertEqual(synth.call_args.args[0], fish_tts.SOFT_AARON_DIRECTION + "Hey Beemo! I'm Beemo.")
            self.assertFalse(wav.exists())


    def test_soft_default_and_plain_switch_are_independent_of_name_spelling(self):
        config = BMOConfig.from_env({"BMO_TTS_PROVIDER": "fish"})
        self.assertEqual(config.fish_delivery, "soft")
        self.assertEqual(config.fish_name_mode, "beemo")
        plain = BMOConfig.from_env({
            "BMO_TTS_PROVIDER": "fish", "BMO_FISH_DELIVERY": " PLAIN ",
            "BMO_FISH_NAME_MODE": "bee-mo",
        })
        self.assertEqual((plain.fish_delivery, plain.fish_name_mode), ("plain", "bee-mo"))
        for value in ("", "playful", "excited", "LOUD"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                BMOConfig.from_env({"BMO_FISH_DELIVERY": value})

    def test_plain_delivery_can_restore_unchanged_original_bmo_voice(self):
        with tempfile.TemporaryDirectory() as d:
            first = Path(d) / "first.wav"
            with wave.open(str(first), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(16000)
                writer.writeframes(b"\x00\x00" * 1200)
            with patch("bmo_ai.fish_tts.synthesize_wav", return_value=first) as synth, patch(
                "bmo_ai.safe_playback.play_wav"
            ):
                fish_tts.speak("I'm BMO.", name_mode="bee-mo", delivery="plain", gain=.25)
            self.assertEqual(synth.call_args.args[0], "I'm Bee-mo.")
            self.assertFalse(first.exists())

    def test_invalid_delivery_rejected_before_fish_cloud_request(self):
        with patch("bmo_ai.fish_tts.synthesize_wav") as send:
            from bmo_ai.audio import AudioValidationError
            with self.assertRaises(AudioValidationError):
                fish_tts.speak("Hi BMO!", delivery="excited")
            send.assert_not_called()



if __name__ == "__main__":
    unittest.main()
