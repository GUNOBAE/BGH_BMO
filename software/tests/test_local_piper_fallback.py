"""No-network Piper fallback tests: never alter the isolated Piper test setup."""
import os
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai import local_piper
from bmo_ai.__main__ import main
from bmo_ai.app import ConversationApp, OFFLINE_KOREAN_UNSUPPORTED_EN, OFFLINE_NOTICE_EN
from bmo_ai.audio import AudioSynthesisError, AudioTranscriptionError
from bmo_ai.config import BMOConfig, ConfigError


class LocalPiperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "piper").mkdir()
        (self.root / "bmo-piper").mkdir()
        self.binary = self.root / "piper" / "piper"
        self.binary.write_text("piper fixture")
        self.binary.chmod(0o700)
        self.model = self.root / "bmo-piper" / "en_US-bmo_voice.onnx"
        self.model.write_bytes(b"model fixture")
        self.model_config = Path(str(self.model) + ".json")
        self.model_config.write_text('{"sample_rate": 22050}')
        self.out = self.root / "temp.wav"

    @staticmethod
    def wav(path):
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(22050)
            writer.writeframes(b"\x01\x00" * 1000)

    def test_read_only_doctor_and_config_default_disabled(self):
        self.assertEqual(BMOConfig().offline_voice, False)
        self.assertEqual(BMOConfig.from_env({"BMO_OFFLINE_VOICE": "true"}).offline_voice, True)
        self.assertEqual(BMOConfig.from_env({"BMO_OFFLINE_VOICE": "Off"}).offline_voice, False)
        self.assertEqual(BMOConfig.from_env({"BMO_OFFLINE_VOICE": "1"}).offline_voice, True)
        for invalid in ("", "someday", "automatic"):
            with self.subTest(invalid=invalid), self.assertRaises(ConfigError):
                BMOConfig.from_env({"BMO_OFFLINE_VOICE": invalid})
        self.assertTrue(all(local_piper.check_local_voice(
            binary=str(self.binary), model=str(self.model)).values()))
        self.model_config.unlink()
        self.assertFalse(local_piper.check_local_voice(
            binary=str(self.binary), model=str(self.model))["config_exists"])
        self.assertFalse(self.out.exists())

    def test_no_network_and_no_credentials_passed_to_piper(self):
        before_model = self.model.read_bytes()
        def fake_run(args, **kwargs):
            self.assertEqual(args[0], str(self.binary))
            self.assertEqual(args[1:3], ["--model", str(self.model)])
            self.assertEqual(args[3], "--output_file")
            self.assertEqual(kwargs["input"], "Beemo says hi!\n")
            self.assertEqual(kwargs["cwd"], str(self.root))
            self.assertFalse(kwargs.get("shell", False))
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertNotIn("FISH_API_KEY", kwargs["env"])
            self.wav(Path(args[4]))
            return subprocess.CompletedProcess(args, 0)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "do-not-leak", "FISH_API_KEY": "do-not-leak"}), patch(
            "bmo_ai.local_piper.subprocess.run", side_effect=fake_run
        ) as invoke, patch("bmo_ai.local_piper.audio._new_wav", return_value=self.out):
            produced = local_piper.synthesize_wav(
                "BMO says hi!", binary=str(self.binary), model=str(self.model)
            )
        self.assertEqual(produced, self.out)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(self.model.read_bytes(), before_model)
        self.assertAlmostEqual(local_piper.audio._wav_info(produced)["duration_seconds"], 1000 / 22050)
        produced.unlink()

    def test_piper_process_failure_cleans_wav_no_cloud_retry(self):
        for failure in (
            subprocess.TimeoutExpired(["piper"], 2),
            subprocess.CalledProcessError(1, ["piper"]),
        ):
            with self.subTest(kind=type(failure).__name__), patch(
                "bmo_ai.local_piper.subprocess.run", side_effect=failure
            ) as p, patch("bmo_ai.local_piper.audio._new_wav", return_value=self.out):
                self.out.write_bytes(b"unfinished")
                with self.assertRaises(AudioSynthesisError):
                    local_piper.synthesize_wav("Hello", binary=str(self.binary), model=str(self.model))
                self.assertFalse(self.out.exists())
                p.assert_called_once()

    def test_speak_delivers_25_percent_gain_and_cleans_temp_wav(self):
        self.wav(self.out)
        with patch("bmo_ai.local_piper.synthesize_wav", return_value=self.out), patch(
            "bmo_ai.local_piper.play_wav"
        ) as play:
            local_piper.speak("Hi", device="plughw:CARD=MAX98357A,DEV=0", gain=.25)
        self.assertEqual(play.call_args.kwargs["gain"], .25)
        self.assertEqual(play.call_args.kwargs["device"], "plughw:CARD=MAX98357A,DEV=0")
        self.assertFalse(self.out.exists())

    def test_non_english_detection(self):
        self.assertTrue(local_piper.is_english_text("Hi, I'm BMO!"))
        self.assertTrue(local_piper.is_english_text("I'm Beemo — hello!"))
        self.assertFalse(local_piper.is_english_text("안녕, BMO"))


class OfflineRoutingTests(unittest.TestCase):
    def make_app(self, *, enabled, answer="Hi BMO."):
        b = Mock()
        b.ask.return_value = answer
        c = BMOConfig(tts_provider="fish", offline_voice=enabled, fish_gain=.25)
        return ConversationApp(b, Mock(), c)

    def test_fish_success_never_runs_piper(self):
        app = self.make_app(enabled=True)
        with patch("bmo_ai.fish_tts.speak") as fish, patch("bmo_ai.local_piper.speak") as local:
            app.playback("Hello BMO")
        fish.assert_called_once()
        local.assert_not_called()

    def test_fish_connection_error_routes_same_answer_to_local_piper(self):
        app = self.make_app(enabled=True)
        with patch("bmo_ai.fish_tts.speak",
                   side_effect=AudioSynthesisError("Fish offline", "fish_connection")) as fish, patch(
            "bmo_ai.local_piper.speak"
        ) as local:
            app.playback("Hi BMO.")
        fish.assert_called_once()
        self.assertEqual(local.call_args.args[0], "Hi BMO.")
        self.assertEqual(local.call_args.kwargs["gain"], .25)
        self.assertEqual(local.call_count, 1)

    def test_disabled_fallback_never_runs_piper(self):
        app = self.make_app(enabled=False)
        with patch("bmo_ai.fish_tts.speak",
                   side_effect=AudioSynthesisError("Fish unavailable", "fish_connection")), patch(
            "bmo_ai.local_piper.speak"
        ) as local:
            with self.assertRaises(AudioSynthesisError):
                app.playback("Hi.")
            local.assert_not_called()

    def test_korean_reply_does_not_get_sent_to_english_only_voice(self):
        app = self.make_app(enabled=True)
        with patch("bmo_ai.fish_tts.speak",
                   side_effect=AudioSynthesisError("Fish offline", "fish_connection")), patch(
            "bmo_ai.local_piper.speak"
        ) as local:
            app.playback("안녕, BMO!")
        self.assertEqual(local.call_args.args[0], OFFLINE_KOREAN_UNSUPPORTED_EN)

    def test_offline_notice_is_an_explicit_status_not_a_chat_answer(self):
        app = self.make_app(enabled=True)
        with patch("bmo_ai.local_piper.speak") as local:
            app.offline_notice()
        self.assertEqual(local.call_args.args[0], OFFLINE_NOTICE_EN)
        app = self.make_app(enabled=False)
        with patch("bmo_ai.local_piper.speak") as local:
            app.offline_notice()
            local.assert_not_called()

    def test_cloud_transcription_network_failure_triggers_one_notice(self):
        client = SimpleNamespace(close=Mock())
        commands = iter(("", "/quit"))
        outputs = []
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "fake", "BMO_OFFLINE_VOICE": "true",
        }), patch("bmo_ai.app.ConversationApp.voice_input",
                  side_effect=AudioTranscriptionError("connection lost", "connection")) as input_voice, patch(
            "bmo_ai.app.ConversationApp.offline_notice"
        ) as say, patch("bmo_ai.local_piper.check_local_voice",
                        return_value={"binary_exists": True, "binary_executable": True,
                                      "model_exists": True, "config_exists": True}):
            outcome = main([], client_factory=lambda _: client,
                           input_fn=lambda _: next(commands), output_fn=outputs.append)
        self.assertEqual(outcome, 0)
        input_voice.assert_called_once()
        say.assert_called_once()

    def test_authentication_error_does_not_pretend_network_is_offline(self):
        client = SimpleNamespace(close=Mock())
        commands = iter(("", "/quit"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake", "BMO_OFFLINE_VOICE": "true"}), patch(
            "bmo_ai.app.ConversationApp.voice_input",
            side_effect=AudioTranscriptionError("wrong key", "authentication")
        ), patch("bmo_ai.app.ConversationApp.offline_notice") as say, patch(
            "bmo_ai.local_piper.check_local_voice",
            return_value={"binary_exists": True, "binary_executable": True,
                          "model_exists": True, "config_exists": True}
        ):
            outcome = main([], client_factory=lambda _: client,
                           input_fn=lambda _: next(commands), output_fn=Mock())
        self.assertEqual(outcome, 0)
        say.assert_not_called()


if __name__ == "__main__":
    unittest.main()
