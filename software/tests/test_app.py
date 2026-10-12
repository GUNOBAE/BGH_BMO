import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai.app import ConversationApp
from bmo_ai.audio import AudioCleanupError
from bmo_ai.config import BMOConfig
from bmo_ai.hardware import FaceState


class AppTests(unittest.TestCase):
    def test_record_deleted_after_transcription_failure_and_interrupt(self):
        for error in (RuntimeError("offline"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "mic.wav"
                path.touch()
                app = ConversationApp(Mock(), Mock(), BMOConfig())
                with patch("bmo_ai.app.audio.record_wav", return_value=path), patch(
                    "bmo_ai.app.audio.transcribe", side_effect=error
                ), self.assertRaises(type(error)):
                    app.voice_input()
                self.assertFalse(path.exists())

    def test_default_stt_auto_detection_and_korean_hint(self):
        for language, hint in (("en", None), ("ko", "ko"), ("auto", None)):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "mic.wav"
                path.touch()
                app = ConversationApp(Mock(), Mock(), BMOConfig(language=language))
                with patch("bmo_ai.app.audio.record_wav", return_value=path), patch(
                    "bmo_ai.app.audio.transcribe", return_value="안녕"
                ) as transcribe:
                    self.assertEqual(app.voice_input(), "안녕")
                    self.assertEqual(transcribe.call_args.kwargs["language"], hint)

    def test_cleanup_failure_preserves_primary_error_and_interrupt(self):
        for error in (RuntimeError("private primary error"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                app = ConversationApp(Mock(), Mock(), BMOConfig())
                with patch("bmo_ai.app.audio.record_wav", return_value=Path("mock.wav")), patch(
                    "bmo_ai.app.audio.transcribe", side_effect=error
                ), patch("bmo_ai.app.Path.unlink", side_effect=PermissionError("private cleanup path")), self.assertLogs(
                    "bmo_ai", level="WARNING"
                ) as logs:
                    with self.assertRaises(type(error)) as caught:
                        app.voice_input()
                self.assertIs(caught.exception, error)
                self.assertIn("audio_temp_cleanup_failed", logs.output[0])
                self.assertNotIn("private", "\n".join(logs.output))

    def test_cleanup_failure_after_success_is_typed_and_sanitized(self):
        app = ConversationApp(Mock(), Mock(), BMOConfig())
        with patch("bmo_ai.app.audio.record_wav", return_value=Path("mock.wav")), patch(
            "bmo_ai.app.audio.transcribe", return_value="hello"
        ), patch("bmo_ai.app.Path.unlink", side_effect=PermissionError("private cleanup path")), self.assertLogs(
            "bmo_ai", level="WARNING"
        ) as logs:
            with self.assertRaises(AudioCleanupError) as caught:
                app.voice_input()
        self.assertEqual(caught.exception.code, "audio_cleanup_failed")
        self.assertNotIn("private", str(caught.exception) + "\n".join(logs.output))

    def test_muting_skips_cloud_speech_and_configured_device_passed(self):
        config = BMOConfig(voice="coral", speaker_device="plughw:CARD=amp,DEV=0")
        app = ConversationApp(Mock(), Mock(), config, muted=True)
        with patch("bmo_ai.app.audio.speak") as speak:
            app.playback("hello")
            speak.assert_not_called()
            app.muted = False
            app.playback("hello")
            self.assertEqual(speak.call_args.kwargs["device"], config.speaker_device)

    def test_face_failure_does_not_break_conversation(self):
        brain = Mock()
        brain.ask.return_value = "hello"
        face = Mock()
        face.set_state.side_effect = OSError("display unavailable")
        app = ConversationApp(brain, Mock(), BMOConfig(), face=face)
        self.assertEqual(app.answer("hi"), "hello")
        face.set_state.assert_called_with(FaceState.THINKING)
