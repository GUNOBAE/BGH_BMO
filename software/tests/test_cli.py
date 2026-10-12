import contextlib
import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai.__main__ import main
from bmo_ai.cloud import CloudError


class CliTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def client(self, responses=None):
        if responses is None:
            responses = Mock(return_value=SimpleNamespace(output_text="hello", status="completed"))
        return SimpleNamespace(responses=SimpleNamespace(create=responses), close=Mock())

    def test_text_mode_no_audio_reset_and_close(self):
        client = self.client()
        commands = iter(("Hi", "/reset", "안녕", "/quit"))
        output = []
        with patch("bmo_ai.app.audio.record_wav") as record, patch("bmo_ai.app.audio.speak") as speak:
            result = main(["--text"], client_factory=lambda config: client,
                          input_fn=lambda prompt: next(commands), output_fn=output.append)
        self.assertEqual(result, 0)
        record.assert_not_called()
        speak.assert_not_called()
        self.assertEqual(len(client.responses.create.call_args.kwargs["input"]), 1)
        self.assertIn("Conversation cleared.", output)
        client.close.assert_called_once()

    def test_turn_failure_is_sanitized_and_next_turn_recovers(self):
        secret = "private transcript and test credential"
        client = self.client(Mock(side_effect=[RuntimeError(secret),
            SimpleNamespace(output_text="recovered", status="completed")]))
        commands = iter(("hi", "try again", "/quit"))
        output = []
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            result = main(["--text"], client_factory=lambda config: client,
                          input_fn=lambda prompt: next(commands), output_fn=output.append)
        self.assertEqual(result, 0)
        self.assertIn("BMO > recovered", output)
        self.assertNotIn(secret, "\n".join(output) + log.getvalue())
        self.assertEqual(len(client.responses.create.call_args.kwargs["input"]), 1)

    def test_voice_unknown_command_does_not_record(self):
        client = self.client()
        commands = iter(("unexpected", "/quit"))
        with patch("bmo_ai.app.audio.record_wav") as record:
            main(["--mute"], client_factory=lambda config: client,
                 input_fn=lambda prompt: next(commands), output_fn=lambda text: None)
        record.assert_not_called()

    def test_missing_key_is_useful_and_no_traceback(self):
        output = []
        self.assertEqual(main(["--text"], output_fn=output.append), 2)
        self.assertIn("OPENAI_API_KEY", output[0])

    def test_doctor_no_client_and_no_cloud_config_requirement(self):
        output = []
        factory = Mock(side_effect=AssertionError("cloud forbidden"))
        with patch.dict(os.environ, {"BMO_SECONDS": "invalid"}), patch(
            "bmo_ai.audio.discover_devices", return_value={"capture": [], "playback": []}
        ), patch("bmo_ai.diagnostics.hardware_inventory", return_value={"hardware_operations": 0}):
            self.assertEqual(main(["--doctor"], client_factory=factory, output_fn=output.append), 0)
        factory.assert_not_called()
        self.assertIn("hardware", json.loads(output[0]))

    def test_invalid_seconds_fails_before_client(self):
        factory = Mock()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--text", "--seconds", "100"], client_factory=factory)
        self.assertEqual(caught.exception.code, 2)
        factory.assert_not_called()

    def test_cli_overrides_invalid_matching_environment_before_validation(self):
        client = self.client()
        factory = Mock(return_value=client)
        with patch.dict(os.environ, {"BMO_SECONDS": "invalid", "BMO_LANGUAGE": "invalid"}):
            self.assertEqual(main(["--text", "--seconds", "3", "--language", "ko"],
                client_factory=factory, input_fn=lambda prompt: "/quit", output_fn=Mock()), 0)
        config = factory.call_args.args[0]
        self.assertEqual(config.seconds, 3)
        self.assertEqual(config.language, "ko")

    def test_repeat_after_playback_failure_never_repeats_chat_or_capture(self):
        from bmo_ai.audio import AudioPlaybackError
        client = self.client()
        commands = iter(("", "/repeat", "/reset", "/repeat", "/quit"))
        output = []
        with patch("bmo_ai.app.ConversationApp.voice_input", return_value="hello") as record, patch(
            "bmo_ai.app.audio.speak", side_effect=[AudioPlaybackError("Device unavailable"), None]
        ) as speak:
            self.assertEqual(main([], client_factory=lambda config: client,
                input_fn=lambda prompt: next(commands), output_fn=output.append), 0)
        self.assertEqual(client.responses.create.call_count, 1)
        self.assertEqual(record.call_count, 1)
        self.assertEqual(speak.call_count, 2)
        self.assertIn("No completed reply to repeat.", output)

    def test_eof_closes_client(self):
        client = self.client()
        self.assertEqual(main(["--text"], client_factory=lambda config: client,
                              input_fn=Mock(side_effect=EOFError), output_fn=Mock()), 0)
        client.close.assert_called_once()

    def test_client_closes_when_initialization_or_ready_output_fails(self):
        for startup in ("brain", "output"):
            with self.subTest(startup=startup):
                client = self.client()
                output = Mock(side_effect=BrokenPipeError) if startup == "output" else Mock()
                with patch("bmo_ai.__main__.BMOBrain", side_effect=RuntimeError) if startup == "brain" else contextlib.nullcontext():
                    with self.assertRaises((RuntimeError, BrokenPipeError)):
                        main(["--text"], client_factory=lambda config: client, output_fn=output)
                client.close.assert_called_once()
