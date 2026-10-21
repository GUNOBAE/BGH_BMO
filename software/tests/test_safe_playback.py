"""Offline safe-gain regression; no physical speaker or cloud API required."""
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai import audio
from bmo_ai.config import BMOConfig, ConfigError
from bmo_ai.app import ConversationApp
from bmo_ai.safe_playback import play_wav, attenuated_copy


class SoftwareGainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = Path(self.tmp.name) / "source.wav"
        with wave.open(str(self.original), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((12000).to_bytes(2, "little", signed=True) * 400)

    def test_software_gain_reduces_amplitude_without_modifying_original(self):
        before = self.original.read_bytes()
        reduced = attenuated_copy(self.original, .25)
        try:
            with wave.open(str(reduced), "rb") as output:
                first = int.from_bytes(output.readframes(1), "little", signed=True)
                self.assertEqual(first, 3000)
            self.assertEqual(self.original.read_bytes(), before)
        finally:
            reduced.unlink(missing_ok=True)

    def test_safe_player_uses_requested_pcm_and_deletes_copy(self):
        copy_path = []
        with patch("bmo_ai.safe_playback.audio._run") as alsa:
            def check_call(command, **kwargs):
                self.assertEqual(command[:4], ["aplay", "-q", "-D", "default"])
                self.assertNotEqual(Path(command[-1]), self.original)
                self.assertTrue(Path(command[-1]).exists())
                with wave.open(command[-1], "rb") as w:
                    self.assertEqual(int.from_bytes(w.readframes(1), "little", signed=True), 3600)
                copy_path.append(Path(command[-1]))
            alsa.side_effect = check_call
            play_wav(self.original, device="default", gain=.3)
        self.assertFalse(copy_path[0].exists())
        self.assertTrue(self.original.exists())

    def test_invalid_gain_is_rejected_before_running_aplay(self):
        with patch("bmo_ai.safe_playback.audio._run") as player:
            for value in (-1, 0, 1.01, float("nan"), True, "50"):
                with self.subTest(value=value), self.assertRaises(audio.AudioValidationError):
                    play_wav(self.original, gain=value)
            player.assert_not_called()

    def test_fish_config_loads_gain_and_app_passes_it(self):
        config = BMOConfig.from_env({
            "BMO_TTS_PROVIDER": "fish", "BMO_FISH_GAIN": "0.25"
        })
        self.assertEqual(config.fish_gain, .25)
        for value in ("", "abc", "1.01", "-.5", "nan", "0"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                BMOConfig.from_env({"BMO_FISH_GAIN": value})
        app = ConversationApp(Mock(), Mock(), config)
        with patch("bmo_ai.fish_tts.speak") as fish, patch("bmo_ai.app.audio.speak") as openai:
            app.playback("Hello BMO.")
        self.assertEqual(fish.call_args.kwargs["gain"], .25)
        openai.assert_not_called()


if __name__ == "__main__":
    unittest.main()
