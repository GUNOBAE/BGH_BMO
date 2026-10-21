"""Offline tests for opt-in Luna low + live voice timing without hardware."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai.__main__ import main, parser
from bmo_ai.app import ConversationApp
from bmo_ai.audio import AudioSynthesisError
from bmo_ai.config import BMOConfig, ConfigError
from bmo_ai.core import BMOBrain, SYSTEM_PROMPT
from bmo_ai.voice_ab_report import read_voice_log, main as report_main


class OptInLunaBrainTests(unittest.TestCase):
    def make_client(self):
        responses = Mock(return_value=SimpleNamespace(
            status="completed", output_text="Hello, Beemo!",
            incomplete_details=None, error=None
        ))
        return SimpleNamespace(responses=SimpleNamespace(create=responses), close=Mock()), responses

    def test_baseline_is_unchanged_when_no_effort_is_requested(self):
        client, responses = self.make_client()
        self.assertEqual(BMOConfig().chat_model, "gpt-4.1-mini")
        self.assertIsNone(BMOConfig().reasoning_effort)
        brain = BMOBrain(client)
        brain.ask("Hi")
        sent = responses.call_args.kwargs
        self.assertEqual(sent["model"], "gpt-4.1-mini")
        self.assertNotIn("reasoning", sent)
        self.assertEqual(sent["max_output_tokens"], 180)
        self.assertFalse(sent["store"])
        self.assertIn("fan-built", sent["instructions"])

    def test_luna_low_is_explicit_and_preserves_persona(self):
        config = BMOConfig.from_env({
            "BMO_CHAT_MODEL": "gpt-6-luna",
            "BMO_REASONING_EFFORT": " low ",
            "BMO_MAX_OUTPUT_TOKENS": "320",
        })
        self.assertEqual(config.reasoning_effort, "low")
        client, responses = self.make_client()
        brain = BMOBrain(
            client, model=config.chat_model, reasoning_effort=config.reasoning_effort,
            max_output_tokens=config.max_output_tokens
        )
        brain.ask("Hello BMO!")
        sent = responses.call_args.kwargs
        self.assertEqual(sent["model"], "gpt-6-luna")
        self.assertEqual(sent["reasoning"], {"effort": "low"})
        self.assertEqual(sent["max_output_tokens"], 320)
        self.assertIn(SYSTEM_PROMPT, sent["instructions"])

    def test_luna_none_also_supported_without_changing_default(self):
        model = BMOConfig.from_env({"BMO_CHAT_MODEL": "gpt-6-luna",
                                    "BMO_REASONING_EFFORT": "NoNe"})
        self.assertEqual(model.reasoning_effort, "none")
        client, responses = self.make_client()
        BMOBrain(client, model="gpt-6-luna", reasoning_effort="none").ask("Hi")
        self.assertEqual(responses.call_args.kwargs["reasoning"], {"effort": "none"})

    def test_invalid_combinations_fail_before_network(self):
        for env in (
            {"BMO_REASONING_EFFORT": "low"},
            {"BMO_REASONING_EFFORT": "none"},
            {"BMO_CHAT_MODEL": "gpt-6-luna", "BMO_REASONING_EFFORT": "high"},
            {"BMO_CHAT_MODEL": "gpt-6-luna", "BMO_REASONING_EFFORT": "nonsense"},
            {"BMO_CHAT_MODEL": "gpt-6.1-sol", "BMO_REASONING_EFFORT": "low"},
        ):
            with self.subTest(env=env), self.assertRaises(ConfigError):
                BMOConfig.from_env(env)
        for kwargs in ({"model": "gpt-4.1-mini", "reasoning_effort": "low"},
                       {"model": "gpt-6-luna", "reasoning_effort": "high"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BMOBrain(self.make_client()[0], **kwargs)

    def test_cli_exposes_only_tested_models_and_reasoning(self):
        choices = parser().parse_args([
            "--chat-model", "gpt-6-luna", "--reasoning-effort", "low",
            "--max-output-tokens", "320"
        ])
        self.assertEqual(choices.chat_model, "gpt-6-luna")
        self.assertEqual(choices.reasoning_effort, "low")
        self.assertEqual(choices.max_output_tokens, 320)

    def test_cli_text_mode_invokes_explicit_luna_without_device(self):
        client, requests = self.make_client()
        commands = iter(("Hello BMO!", "/quit"))
        with patch("bmo_ai.app.audio.record_wav") as capture, patch(
            "bmo_ai.fish_tts.speak"
        ) as speech, patch.dict("os.environ", {"OPENAI_API_KEY": "unused"}, clear=True):
            rc = main(
                ["--text", "--chat-model", "gpt-6-luna", "--reasoning-effort",
                 "low", "--max-output-tokens", "320"],
                client_factory=lambda c: client,
                input_fn=lambda _: next(commands), output_fn=Mock(),
            )
        self.assertEqual(rc, 0)
        capture.assert_not_called()
        speech.assert_not_called()
        self.assertEqual(requests.call_count, 1)
        self.assertEqual(requests.call_args.kwargs["reasoning"]["effort"], "low")


class VoiceTimingTests(unittest.TestCase):
    def test_mic_capture_to_fish_process_boundary_is_logged_not_raw_text(self):
        client = Mock()
        config = BMOConfig(tts_provider="fish", chat_model="gpt-6-luna",
                           reasoning_effort="low", fish_gain=.25)
        app = ConversationApp(Mock(), client, config)
        with tempfile.TemporaryDirectory() as folder:
            recording = Path(folder) / "record.wav"
            recording.touch()
            with patch("bmo_ai.app.audio.record_wav", return_value=recording), patch(
                "bmo_ai.app.audio.transcribe", return_value="private user request"
            ), patch("bmo_ai.fish_tts.speak",
                     side_effect=lambda *a, **kw: kw["on_playback_start"]()), patch(
                "bmo_ai.app.event"
            ) as log:
                self.assertEqual(app.voice_input(), "private user request")
                self.assertFalse(recording.exists())
                app.playback("private Beemo reply")
                event_args = [c for c in log.call_args_list
                              if c.args[0] == "voice_playback_boundary"]
                self.assertEqual(len(event_args), 1)
                fields = event_args[0].kwargs
                self.assertEqual(fields["chat_model"], "gpt-6-luna")
                self.assertEqual(fields["reasoning_effort"], "low")
                self.assertEqual(fields["tts_source"], "fish")
                self.assertIsInstance(fields["after_record_end_ms"], float)
                self.assertGreaterEqual(fields["after_record_end_ms"], 0)
                self.assertIsInstance(fields["tts_prepare_ms"], float)
                self.assertNotIn("private", str(fields))
                self.assertIsNone(app._record_end_monotonic)
                # /repeat never reuses a previous microphone-capture timestamp.
                app.playback("repeat without mic")
                event_args = [c for c in log.call_args_list
                              if c.args[0] == "voice_playback_boundary"]
                self.assertIsNone(event_args[-1].kwargs["after_record_end_ms"])

    def test_piper_fallback_logs_correct_audio_source(self):
        config = BMOConfig(tts_provider="fish", offline_voice=True)
        app = ConversationApp(Mock(), Mock(), config)
        def fail_fish(*args, **kwargs):
            raise AudioSynthesisError("Fish unavailable", "fish_connection")
        with patch("bmo_ai.fish_tts.speak", side_effect=fail_fish), patch(
            "bmo_ai.local_piper.speak",
            side_effect=lambda *a, **kw: kw["on_playback_start"]()
        ), patch("bmo_ai.app.event") as events:
            app.playback("Hello!")
        log = [c for c in events.call_args_list
               if c.args[0] == "voice_playback_boundary"]
        self.assertEqual(log[0].kwargs["tts_source"], "piper")

    def test_offline_report_filters_nonfish_and_malformed_records(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "baseline.jsonl"
            luna = Path(folder) / "luna.jsonl"
            def sample(model, effort, ms, source="fish", prep=800):
                return json.dumps({
                    "event": "voice_playback_boundary",
                    "chat_model": model, "reasoning_effort": effort,
                    "tts_source": source, "after_record_end_ms": ms,
                    "tts_prepare_ms": prep,
                }) + "\n"
            base.write_text(
                sample("gpt-4.1-mini", "unset", 3200) +
                sample("gpt-4.1-mini", "unset", 3600) +
                sample("gpt-4.1-mini", "unset", 100, "piper") +
                "{this is bad json}\n",
                encoding="utf-8"
            )
            luna.write_text(
                sample("gpt-6-luna", "low", 2900) +
                sample("gpt-6-luna", "low", 3100),
                encoding="utf-8"
            )
            b = read_voice_log(str(base), model="gpt-4.1-mini", effort="unset")
            self.assertEqual(b["samples"], 2)
            self.assertEqual(b["median_capture_end_to_player_ms"], 3400)
            self.assertEqual(b["piper_or_other_tts_events"], 1)
            self.assertEqual(b["malformed_lines"], 1)
            lines = []
            self.assertEqual(report_main(
                ["--baseline", str(base), "--luna", str(luna)],
                output=lines.append
            ), 0)
            self.assertIn("-400.0 ms", "\n".join(lines))
            self.assertIn("CAUTION", "\n".join(lines))

    def test_report_rejects_same_file_and_no_fake_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.jsonl"
            path.write_text('{"event":"other"}\n', encoding="utf-8")
            self.assertEqual(report_main(
                ["--baseline", str(path), "--luna", str(path)],
                output=Mock()), 2)
            second = Path(folder) / "alsoempty.jsonl"
            second.write_text('{"event":"other"}\n', encoding="utf-8")
            self.assertEqual(report_main(
                ["--baseline", str(path), "--luna", str(second)],
                output=Mock()), 1)


if __name__ == "__main__":
    unittest.main()
