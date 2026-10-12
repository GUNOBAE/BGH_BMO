from dataclasses import FrozenInstanceError, asdict, replace
import unittest
from unittest.mock import patch

from bmo_ai.config import BMOConfig, ConfigError


class ConfigTests(unittest.TestCase):
    def test_defaults_are_cost_bounded_english_and_have_no_secret(self):
        config = BMOConfig.from_env({"OPENAI_API_KEY": "private-test-key"})
        self.assertEqual(config.chat_model, "gpt-4.1-mini")
        self.assertEqual(config.stt_model, "gpt-4o-mini-transcribe")
        self.assertEqual(config.tts_model, "gpt-4o-mini-tts")
        self.assertEqual(config.voice, "coral")
        self.assertEqual(config.language, "en")
        self.assertEqual(config.seconds, 5)
        self.assertEqual(config.timeout, 30)
        self.assertEqual(config.max_retries, 1)
        self.assertEqual(config.max_turns, 8)
        self.assertEqual(config.max_input_chars, 4000)
        self.assertEqual(config.max_history_chars, 16000)
        self.assertEqual(config.max_output_tokens, 180)
        self.assertEqual(config.log_level, "INFO")
        self.assertIsNone(config.log_file)
        self.assertIsNone(config.mic_device)
        self.assertIsNone(config.speaker_device)
        self.assertNotIn("private-test-key", repr(config))
        self.assertFalse(any("key" in name for name in asdict(config)))

    def test_explicit_mapping_does_not_read_process_environment(self):
        with patch.dict("os.environ", {"BMO_LANGUAGE": "ko", "BMO_SECONDS": "7"}):
            self.assertEqual(BMOConfig.from_env({}).language, "en")
            self.assertEqual(BMOConfig.from_env().language, "ko")
            self.assertEqual(BMOConfig.from_env().seconds, 7)

    def test_all_settings_can_be_loaded_and_whitespace_is_normalized(self):
        config = BMOConfig.from_env({
            "BMO_CHAT_MODEL": " chat-model ", "BMO_STT_MODEL": "stt-model",
            "BMO_TTS_MODEL": "tts-model", "BMO_VOICE": "alloy",
            "BMO_LANGUAGE": " KO ", "BMO_SECONDS": " 10 ",
            "BMO_MIC_DEVICE": " plughw:CARD=USB,DEV=0 ",
            "BMO_SPEAKER_DEVICE": "default", "BMO_TIMEOUT": "2.5",
            "BMO_MAX_RETRIES": "0", "BMO_MAX_TURNS": "2",
            "BMO_MAX_INPUT_CHARS": "500", "BMO_MAX_HISTORY_CHARS": "1000",
            "BMO_MAX_OUTPUT_TOKENS": "75", "BMO_LOG_FILE": "/tmp/bmo.log",
            "BMO_LOG_LEVEL": "debug", "UNRELATED_SETTING": "ignored",
        })
        self.assertEqual(config, BMOConfig(
            chat_model="chat-model", stt_model="stt-model", tts_model="tts-model",
            voice="alloy", language="ko", seconds=10,
            mic_device="plughw:CARD=USB,DEV=0", speaker_device="default",
            timeout=2.5, max_retries=0, max_turns=2, max_input_chars=500,
            max_history_chars=1000, max_output_tokens=75,
            log_file="/tmp/bmo.log", log_level="DEBUG",
        ))

    def test_optional_empty_settings_become_none(self):
        config = BMOConfig.from_env({
            "BMO_MIC_DEVICE": " ", "BMO_SPEAKER_DEVICE": "", "BMO_LOG_FILE": "",
        })
        self.assertIsNone(config.mic_device)
        self.assertIsNone(config.speaker_device)
        self.assertIsNone(config.log_file)

    def test_invalid_values_fail_without_exposing_raw_input(self):
        invalid = {
            "BMO_LANGUAGE": ("es", ""), "BMO_LOG_LEVEL": ("TRACE",),
            "BMO_SECONDS": ("0", "21", "5.5", "-1", "", "1_0", "５"),
            "BMO_TIMEOUT": ("0", "-1", "nan", "inf", "301", "not-a-number"),
            "BMO_MAX_RETRIES": ("6", "-1"), "BMO_MAX_TURNS": ("0", "101"),
            "BMO_MAX_INPUT_CHARS": ("0", "100001"),
            "BMO_MAX_HISTORY_CHARS": ("0", "1000001", "3999"),
            "BMO_MAX_OUTPUT_TOKENS": ("0", "8193"),
            "BMO_CHAT_MODEL": ("", "with space"),
            "BMO_STT_MODEL": ("",), "BMO_TTS_MODEL": ("", "invalid/model"),
            "BMO_VOICE": ("", "banana"), "BMO_MIC_DEVICE": ("a\nb",),
            "BMO_SPEAKER_DEVICE": ("a\x00b",), "BMO_LOG_FILE": ("a\nb",),
        }
        for variable, values in invalid.items():
            for value in values:
                with self.subTest(variable=variable, value=value), self.assertRaises(ConfigError):
                    BMOConfig.from_env({variable: value})
        secret = "private-credential-accidentally-pasted"
        with self.assertRaises(ConfigError) as context:
            BMOConfig.from_env({"BMO_SECONDS": secret})
        self.assertNotIn(secret, str(context.exception))

    def test_legacy_voice_compatibility_is_validated_before_cloud_startup(self):
        self.assertEqual(BMOConfig.from_env({
            "BMO_TTS_MODEL": "tts-1", "BMO_VOICE": "alloy",
        }).voice, "alloy")
        with self.assertRaises(ConfigError) as context:
            BMOConfig.from_env({"BMO_TTS_MODEL": "tts-1", "BMO_VOICE": "marin"})
        self.assertIn("BMO_VOICE", str(context.exception))
        self.assertNotIn("marin", str(context.exception))

    def test_invalid_mapping_type_and_direct_dataclass_overrides_are_validated(self):
        with self.assertRaises(ConfigError):
            BMOConfig.from_env({"BMO_SECONDS": 5})
        config = BMOConfig()
        for changes in ({"seconds": True}, {"timeout": False}, {"timeout": float("nan")},
                        {"language": "es"}, {"max_input_chars": 20000}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                replace(config, **changes)
        with self.assertRaises(FrozenInstanceError):
            config.seconds = 10


if __name__ == "__main__":
    unittest.main()
