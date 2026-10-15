"""Playback operation boundaries and cleanup; no ALSA or cloud execution."""
import io
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai import audio
from bmo_ai.app import ConversationApp
from bmo_ai.config import BMOConfig
from bmo_ai.hardware import FaceState
from bmo_ai.conversation import TurnCancelled


class AudioLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.folder) / "synthetic.wav"
        self.enterContext(patch.object(audio, "_new_wav", return_value=self.path))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))

    def client(self, data=None, error=None):
        if data is None:
            output = io.BytesIO()
            with wave.open(output, "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b"\0\0" * 160)
            data = output.getvalue()
        response = Mock()

        def chunks():
            self.trace.append("tts_bytes")
            if error:
                raise error
            yield data

        response.iter_bytes.side_effect = chunks
        context = Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=False))
        return SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(
            with_streaming_response=SimpleNamespace(create=Mock(return_value=context)))))

    def player(self, *, returncode=0, failure=None):
        process = Mock(returncode=returncode)
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)

        def communicate(**kwargs):
            self.trace.append("wait")
            if failure and kwargs:
                raise failure
            return b"", b""

        process.communicate.side_effect = communicate

        def spawn(*args, **kwargs):
            self.trace.append("player_spawn")
            self.assertTrue(self.path.exists())
            audio._wav_info(self.path)
            return process

        self.enterContext(patch.object(audio.subprocess, "Popen", side_effect=spawn))
        return process

    def test_callback_follows_validated_tts_and_successful_spawn_then_cleanup(self):
        self.trace = []
        process = self.player()
        audio.speak(self.client(), "synthetic reply", on_playback_start=lambda: self.trace.append("started"))
        self.assertEqual(self.trace, ["tts_bytes", "player_spawn", "started", "wait"])
        process.kill.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_invalid_wav_or_provider_failure_never_starts_player_or_callback(self):
        for client, error_type in ((self.client(b"invalid"), audio.AudioValidationError),
                                   (self.client(error=ConnectionError("private")), audio.AudioSynthesisError)):
            self.trace = []
            started = Mock()
            with patch.object(audio.subprocess, "Popen") as spawn, self.assertRaises(error_type):
                audio.speak(client, "synthetic reply", on_playback_start=started)
            started.assert_not_called()
            spawn.assert_not_called()
            self.assertFalse(self.path.exists())

    def test_spawn_failure_has_no_start_callback_and_is_classified(self):
        self.trace = []
        started = Mock()
        with patch.object(audio.subprocess, "Popen", side_effect=FileNotFoundError("private")):
            with self.assertRaises(audio.AudioPlaybackError) as caught:
                audio.speak(self.client(), "synthetic reply", on_playback_start=started)
        self.assertEqual(caught.exception.code, "tool_unavailable")
        started.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_player_timeout_kills_reaps_and_preserves_cleanup(self):
        self.trace = []
        process = self.player(failure=subprocess.TimeoutExpired(["fake"], 1))
        with self.assertRaises(audio.AudioPlaybackError) as caught:
            audio.speak(self.client(), "synthetic reply", on_playback_start=lambda: None)
        self.assertEqual(caught.exception.code, "audio_timeout")
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)
        self.assertFalse(self.path.exists())

    def test_callback_interrupt_kills_reaps_and_cleans_file(self):
        self.trace = []
        process = self.player()
        with self.assertRaises(KeyboardInterrupt):
            audio.speak(self.client(), "synthetic reply", on_playback_start=Mock(side_effect=KeyboardInterrupt))
        process.kill.assert_called_once()
        process.communicate.assert_called_once_with()
        self.assertFalse(self.path.exists())

    def test_player_nonzero_exit_is_classified_and_cleans_file(self):
        self.trace = []
        self.player(returncode=1)
        with self.assertRaises(audio.AudioPlaybackError) as caught:
            audio.speak(self.client(), "synthetic reply", on_playback_start=lambda: None)
        self.assertEqual(caught.exception.code, "device_unavailable")
        self.assertFalse(self.path.exists())

    def test_primary_playback_failure_survives_cleanup_failure(self):
        self.trace = []
        self.player(returncode=1)
        with patch.object(Path, "unlink", side_effect=OSError("private")):
            with self.assertRaises(audio.AudioPlaybackError) as caught:
                audio.speak(self.client(), "synthetic reply", on_playback_start=lambda: None)
        self.assertEqual(caught.exception.code, "device_unavailable")

    def test_injected_voice_synthesis_then_start_and_idle_even_on_failure_or_cancel(self):
        for failure in (None, audio.AudioPlaybackError("synthetic"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                face = SimpleNamespace(state=FaceState.IDLE)
                states = []

                def state(value):
                    face.state = value
                    states.append(value)

                face.set_state = state

                def speak(text, *, on_playback_start):
                    self.assertEqual(face.state, FaceState.THINKING)
                    on_playback_start()
                    self.assertEqual(face.state, FaceState.SPEAKING)
                    if failure:
                        raise failure

                app = ConversationApp(Mock(), None, BMOConfig(), face=face,
                                      voice=SimpleNamespace(speak=speak))
                if failure:
                    with self.assertRaises(type(failure)):
                        app.playback("synthetic reply")
                else:
                    app.playback("synthetic reply")
                self.assertEqual(states, [FaceState.THINKING, FaceState.SPEAKING, FaceState.IDLE])

    def test_muted_or_empty_answer_never_calls_injected_voice(self):
        voice = Mock()
        app = ConversationApp(Mock(), None, BMOConfig(), voice=voice, muted=True)
        app.playback("synthetic reply")
        app.muted = False
        app.playback("")
        voice.speak.assert_not_called()

    def test_cancel_after_download_never_dispatches_player_and_cleans_wav(self):
        self.trace = []
        checks = Mock(side_effect=[None, TurnCancelled()])
        with patch.object(audio.subprocess, "Popen") as spawn, self.assertRaises(TurnCancelled):
            audio.speak(self.client(), "synthetic reply", on_playback_start=Mock(), cancel_check=checks)
        spawn.assert_not_called()
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
