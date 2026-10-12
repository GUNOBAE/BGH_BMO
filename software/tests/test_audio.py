"""Speech tests use synthetic WAVs and mocks; no microphone, speaker, or cloud."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai import audio


def make_wav(path, *, seconds=1, rate=16000, channels=1, width=2):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(bytes(int(seconds * rate) * channels * width))


class HTTPError(RuntimeError):
    def __init__(self, status):
        super().__init__("sk-private-key user-transcript-secret")
        self.status_code = status


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.created = []
        original_mkstemp = tempfile.mkstemp

        def mkstemp(**kwargs):
            fd, name = original_mkstemp(dir=self.directory, **kwargs)
            self.created.append(Path(name))
            return fd, name

        self.mkstemp_patch = patch.object(audio.tempfile, "mkstemp", side_effect=mkstemp)
        self.mkstemp_patch.start()
        self.addCleanup(self.mkstemp_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def recording_process(self, command, **kwargs):
        make_wav(command[-1], seconds=int(command[command.index("-d") + 1]))
        return subprocess.CompletedProcess(command, 0)

    def speech_client(self, *, stream_error=None, invalid=False, streaming_header=False):
        response = Mock()

        def chunks():
            if stream_error is not None:
                yield b"partial"
                raise stream_error
            if invalid:
                yield b"this is not WAV"
                return
            source = self.directory / "fake_generated.wav"
            make_wav(source, rate=24000)
            raw = bytearray(source.read_bytes())
            if streaming_header:
                struct.pack_into("<I", raw, 4, 0xFFFFFFFF)
                struct.pack_into("<I", raw, 40, 0xFFFFFFFF)
            yield bytes(raw[:100])
            yield bytes(raw[100:])

        response.iter_bytes.side_effect = chunks
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        create = Mock(return_value=context)
        client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(
            with_streaming_response=SimpleNamespace(create=create))))
        return client, create, context

    def test_record_uses_configured_device_and_retains_owned_valid_file(self):
        with patch.object(audio.subprocess, "run", side_effect=self.recording_process) as run:
            path = audio.record_wav(seconds=2, device="plughw:CARD=USB,DEV=0")
        self.assertTrue(path.exists())
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["arecord", "-q", "-D", "plughw:CARD=USB,DEV=0"])
        self.assertEqual(run.call_args.kwargs["timeout"], 12)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(audio._wav_info(path)["duration_seconds"], 2)
        path.unlink()

    def test_record_preserves_alsa_default_without_selecting_a_card(self):
        with patch.object(audio.subprocess, "run", side_effect=self.recording_process) as run:
            path = audio.record_wav(seconds=1)
        self.assertNotIn("-D", run.call_args.args[0])
        path.unlink()

    def test_invalid_duration_and_device_never_start_recording(self):
        with patch.object(audio.subprocess, "run") as run:
            for value in (0, -1, 61, 1.0, True, "5", None):
                with self.subTest(value=value), self.assertRaises(audio.AudioValidationError):
                    audio.record_wav(seconds=value)
            for device in ("", "\nprivate", "a\x00b", 1):
                with self.subTest(device=device), self.assertRaises(audio.AudioValidationError):
                    audio.record_wav(device=device)
        run.assert_not_called()
        self.assertEqual(self.created, [])

    def test_record_failure_and_interrupt_delete_temporary_files(self):
        failures = (
            FileNotFoundError("private"),
            subprocess.CalledProcessError(1, ["arecord"], stderr=b"private"),
            subprocess.TimeoutExpired(["arecord"], 15, stderr=b"private"),
            PermissionError("private"),
            KeyboardInterrupt(),
        )
        for error in failures:
            expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else audio.AudioRecordingError
            with self.subTest(error=type(error).__name__), patch.object(audio.subprocess, "run", side_effect=error):
                with self.assertRaises(expected) as caught:
                    audio.record_wav()
                self.assertFalse(self.created[-1].exists())
                self.assertNotIn("private", str(caught.exception))

    def test_record_checks_pcm_format_and_expected_duration(self):
        for options in ({"rate": 48000}, {"channels": 2}, {"width": 1}, {"seconds": 0.25}):
            def process(command, **kwargs):
                make_wav(command[-1], **options)
            with self.subTest(options=options), patch.object(audio.subprocess, "run", side_effect=process):
                with self.assertRaises(audio.AudioValidationError):
                    audio.record_wav(seconds=1)
                self.assertFalse(self.created[-1].exists())

    def test_wav_rejects_empty_truncated_wrong_size_and_invalid_pcm(self):
        path = self.directory / "input.wav"
        make_wav(path)
        valid = path.read_bytes()
        samples = [b"", b"RIFF", valid[:-1]]
        bad_riff = bytearray(valid)
        struct.pack_into("<I", bad_riff, 4, len(valid) + 100)
        samples.append(bad_riff)
        bad_align = bytearray(valid)
        struct.pack_into("<H", bad_align, 32, 3)
        samples.append(bad_align)
        bad_rate = bytearray(valid)
        struct.pack_into("<I", bad_rate, 28, 1)
        samples.append(bad_rate)
        bad_encoding = bytearray(valid)
        struct.pack_into("<H", bad_encoding, 20, 3)
        samples.append(bad_encoding)
        odd_pcm = bytearray(valid[:-1])
        struct.pack_into("<I", odd_pcm, 4, len(odd_pcm) - 8)
        struct.pack_into("<I", odd_pcm, 40, len(odd_pcm) - 44)
        samples.append(odd_pcm)
        for sample in samples:
            with self.subTest(length=len(sample)):
                path.write_bytes(sample)
                with self.assertRaises(audio.AudioValidationError):
                    audio._wav_info(path)

    def test_wav_streaming_header_is_only_allowed_explicitly(self):
        path = self.directory / "input.wav"
        make_wav(path, rate=24000)
        raw = bytearray(path.read_bytes())
        struct.pack_into("<I", raw, 4, 0xFFFFFFFF)
        struct.pack_into("<I", raw, 40, 0xFFFFFFFF)
        path.write_bytes(raw)
        with self.assertRaises(audio.AudioValidationError):
            audio._wav_info(path)
        self.assertEqual(audio._wav_info(path, allow_streaming_header=True)["duration_seconds"], 1)

    def test_wav_extensible_pcm_is_validated(self):
        path = self.directory / "input.wav"
        make_wav(path)
        raw = path.read_bytes()
        fmt = bytearray(raw[20:36])
        struct.pack_into("<H", fmt, 0, 0xFFFE)
        fmt += struct.pack("<HHI", 22, 16, 0) + audio._PCM_GUID
        body = b"WAVEfmt " + struct.pack("<I", 40) + fmt + raw[36:]
        path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
        self.assertEqual(audio._wav_info(path)["sample_width"], 2)
        modified = bytearray(path.read_bytes())
        modified[44] = 3
        path.write_bytes(modified)
        with self.assertRaises(audio.AudioValidationError):
            audio._wav_info(path)

    def test_transcribe_passes_model_language_and_closes_input(self):
        path = self.directory / "input.wav"
        make_wav(path)
        create = Mock(return_value=SimpleNamespace(text="  안녕하세요!  "))
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        self.assertEqual(audio.transcribe(client, path, model="gpt-4o-mini-transcribe", language="ko"), "안녕하세요!")
        args = create.call_args.kwargs
        self.assertEqual(args["model"], "gpt-4o-mini-transcribe")
        self.assertEqual(args["language"], "ko")
        self.assertEqual(args["response_format"], "json")
        self.assertTrue(args["file"].closed)
        self.assertTrue(path.exists())
        create.return_value = SimpleNamespace(text="   ")
        self.assertEqual(audio.transcribe(client, path), "")
        self.assertNotIn("language", create.call_args.kwargs)
        audio.transcribe(client, path, model="gpt-transcribe", language="ko")
        self.assertEqual(create.call_args.kwargs["languages"], ["ko"])
        self.assertNotIn("language", create.call_args.kwargs)

    def test_transcribe_validates_before_any_cloud_request(self):
        create = Mock()
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        path = self.directory / "bad.wav"
        path.write_bytes(b"private")
        with self.assertRaises(audio.AudioValidationError):
            audio.transcribe(client, path)
        with self.assertRaises(audio.AudioValidationError):
            audio.transcribe(client, path, language="Korean")
        create.assert_not_called()

    def test_transcription_errors_are_sanitized_with_no_retries(self):
        path = self.directory / "input.wav"
        make_wav(path)
        for error in (HTTPError(401), HTTPError(429), HTTPError(500), TimeoutError("secret")):
            create = Mock(side_effect=error)
            client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
            with self.subTest(error=type(error).__name__), self.assertRaises(audio.AudioTranscriptionError) as caught:
                audio.transcribe(client, path)
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIn("secret", str(caught.exception))
            self.assertTrue(create.call_args.kwargs["file"].closed)
            self.assertEqual(create.call_count, 1)
        create = Mock(return_value=SimpleNamespace(text=None))
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        with self.assertRaises(audio.AudioTranscriptionError) as caught:
            audio.transcribe(client, path)
        self.assertEqual(caught.exception.code, "invalid_transcription_response")

    def test_speak_passes_config_and_cleans_validated_wav(self):
        client, create, context = self.speech_client(streaming_header=True)
        with patch.object(audio.subprocess, "run") as run:
            audio.speak(client, "안녕하세요", voice="coral", device="default", model="gpt-4o-mini-tts", instructions="Be clear")
        self.assertEqual(create.call_args.kwargs, {
            "model": "gpt-4o-mini-tts", "voice": "coral", "input": "안녕하세요",
            "response_format": "wav", "instructions": "Be clear",
        })
        self.assertEqual(run.call_args.args[0][:4], ["aplay", "-q", "-D", "default"])
        self.assertEqual(run.call_args.kwargs["timeout"], 11)
        self.assertFalse(self.created[-1].exists())
        context.__exit__.assert_called_once()

    def test_legacy_tts_omits_unsupported_instructions_and_uses_alsa_default(self):
        client, create, _ = self.speech_client()
        with patch.object(audio.subprocess, "run") as run:
            audio.speak(client, "hello", voice="coral", model="tts-1", instructions="unused")
        self.assertNotIn("instructions", create.call_args.kwargs)
        self.assertNotIn("-D", run.call_args.args[0])

    def test_empty_or_invalid_speech_makes_no_cloud_request(self):
        client, create, _ = self.speech_client()
        audio.speak(client, " \n ")
        for kwargs in (
            {"text": "x" * 4097}, {"text": None},
            {"text": "hello", "voice": "unknown"},
            {"text": "hello", "voice": "marin", "model": "tts-1"},
            {"text": "hello", "instructions": "x" * 4097},
            {"text": "hello", "model": "model\nsecret"},
            {"text": "hello", "download_timeout": 0},
            {"text": "hello", "download_timeout": float("inf")},
            {"text": "hello", "download_timeout": True},
        ):
            with self.subTest(kwargs={key: len(value) if isinstance(value, str) else value for key, value in kwargs.items()}):
                with self.assertRaises(audio.AudioValidationError):
                    audio.speak(client, **kwargs)
        self.assertEqual(self.created, [])
        create.assert_not_called()

    def test_public_speech_settings_validation_matches_speak(self):
        audio.validate_speech_settings("gpt-4o-mini-tts", "marin")
        audio.validate_speech_settings("tts-1", "coral")
        for model, voice in (("tts-1", "cedar"), ("gpt-4o-mini-tts", "unknown"), ("private\nmodel", "coral")):
            with self.subTest(model=model, voice=voice), self.assertRaises(audio.AudioValidationError):
                audio.validate_speech_settings(model, voice)

    def test_download_size_cap_is_enforced_before_disk_write_and_cleanup(self):
        client, create, context = self.speech_client()
        response = context.__enter__.return_value
        response.iter_bytes.side_effect = lambda: iter((b"x" * 64, b"y"))
        removed_sizes = []
        original_discard = audio._discard

        def discard(path, **kwargs):
            removed_sizes.append(path.stat().st_size)
            original_discard(path, **kwargs)

        with patch.object(audio, "MAX_AUDIO_BYTES", 64), patch.object(audio, "_discard", side_effect=discard):
            with patch.object(audio.subprocess, "run") as run:
                with self.assertRaises(audio.AudioSynthesisError) as caught:
                    audio.speak(client, "hello")
        self.assertEqual(caught.exception.code, "speech_download_too_large")
        self.assertEqual(removed_sizes, [64])
        self.assertFalse(self.created[-1].exists())
        self.assertEqual(create.call_count, 1)
        response.iter_bytes.assert_called_once_with()
        run.assert_not_called()

    def test_download_deadline_is_checked_before_and_after_network_reads(self):
        for timings in ((0, 61), (0, 0, 61)):
            client, create, context = self.speech_client()
            with self.subTest(timings=timings), patch.object(audio.time, "monotonic", side_effect=timings):
                with patch.object(audio.subprocess, "run") as run:
                    with self.assertRaises(audio.AudioSynthesisError) as caught:
                        audio.speak(client, "hello")
            self.assertEqual(caught.exception.code, "audio_download_timeout")
            self.assertFalse(self.created[-1].exists())
            self.assertEqual(create.call_count, 1)
            context.__exit__.assert_called_once()
            run.assert_not_called()

    def test_stream_connection_error_uses_cloud_classification_and_cleans_file(self):
        client, _, _ = self.speech_client(stream_error=ConnectionError("private"))
        with self.assertRaises(audio.AudioSynthesisError) as caught:
            audio.speak(client, "hello")
        self.assertEqual(caught.exception.code, "connection")
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse(self.created[-1].exists())

    def test_download_invalid_chunk_and_storage_failure_are_typed(self):
        client, _, context = self.speech_client()
        context.__enter__.return_value.iter_bytes.side_effect = lambda: iter(("private",))
        with self.assertRaises(audio.AudioSynthesisError) as caught:
            audio.speak(client, "hello")
        self.assertEqual(caught.exception.code, "invalid_speech_download")
        self.assertFalse(self.created[-1].exists())
        client, _, _ = self.speech_client()
        with patch.object(Path, "open", side_effect=PermissionError("private")):
            with self.assertRaises(audio.AudioSynthesisError) as caught:
                audio.speak(client, "hello")
        self.assertEqual(caught.exception.code, "audio_storage_failed")
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse(self.created[-1].exists())

    def test_failed_or_interrupted_synthesis_cleans_partial_file(self):
        for error in (HTTPError(401), RuntimeError("private"), KeyboardInterrupt()):
            client, create, context = self.speech_client(stream_error=error)
            expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else audio.AudioSynthesisError
            with self.subTest(error=type(error).__name__), patch.object(audio.subprocess, "run") as run:
                with self.assertRaises(expected) as caught:
                    audio.speak(client, "user-transcript-secret")
                self.assertNotIn("private", str(caught.exception))
                self.assertFalse(self.created[-1].exists())
                self.assertEqual(create.call_count, 1)
                context.__exit__.assert_called_once()
                run.assert_not_called()

    def test_failed_stream_context_entry_and_exit_cleanup(self):
        for method in ("__enter__", "__exit__"):
            client, _, context = self.speech_client()
            getattr(context, method).side_effect = RuntimeError("private")
            with self.subTest(method=method), self.assertRaises(audio.AudioSynthesisError):
                audio.speak(client, "hello")
            self.assertFalse(self.created[-1].exists())

    def test_invalid_synthesis_never_plays_and_cleans_file(self):
        client, _, _ = self.speech_client(invalid=True)
        with patch.object(audio.subprocess, "run") as run:
            with self.assertRaises(audio.AudioValidationError):
                audio.speak(client, "hello")
        run.assert_not_called()
        self.assertFalse(self.created[-1].exists())

    def test_playback_failure_or_interrupt_cleans_file(self):
        for error in (subprocess.TimeoutExpired(["aplay"], 11), subprocess.CalledProcessError(1, ["aplay"], stderr=b"secret"), KeyboardInterrupt()):
            client, _, _ = self.speech_client()
            expected = KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else audio.AudioPlaybackError
            with self.subTest(error=type(error).__name__), patch.object(audio.subprocess, "run", side_effect=error):
                with self.assertRaises(expected):
                    audio.speak(client, "hello")
                self.assertFalse(self.created[-1].exists())

    def test_cleanup_failure_safe_and_preserves_original_interrupt(self):
        client, _, _ = self.speech_client()
        with patch.object(audio.subprocess, "run"), patch.object(Path, "unlink", side_effect=PermissionError("private")):
            with self.assertLogs("bmo_ai", level="INFO") as logs:
                with self.assertRaises(audio.AudioCleanupError) as caught:
                    audio.speak(client, "user-transcript-secret")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(logs.output, ["INFO:bmo_ai:audio_temp_cleanup_failed"])
        client, _, _ = self.speech_client(stream_error=KeyboardInterrupt())
        with patch.object(Path, "unlink", side_effect=PermissionError("private")):
            with self.assertLogs("bmo_ai", level="INFO"):
                with self.assertRaises(KeyboardInterrupt):
                    audio.speak(client, "hello")

    def test_temporary_storage_failure_is_sanitized(self):
        with patch.object(audio.tempfile, "mkstemp", side_effect=OSError("private")):
            with self.assertRaises(audio.AudioRecordingError) as caught:
                audio.record_wav()
            self.assertEqual(caught.exception.code, "audio_storage_failed")
            self.assertNotIn("private", str(caught.exception))

    def test_discovery_is_json_read_only_and_never_selects_devices(self):
        hardware = "**** List of CAPTURE Hardware Devices ****\ncard 2: USB [USB Microphone], device 0: USB Audio [USB Audio]\n  Subdevices: 1/1\n"
        pcm = "default\n    Default PCM\nplughw:CARD=USB,DEV=0\n    USB Microphone\n    Conversion available\n"

        def listing(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=hardware if command[-1] == "-l" else pcm)

        with patch.object(audio.subprocess, "run", side_effect=listing) as run:
            result = audio.discover_devices()
        self.assertEqual([call.args[0] for call in run.call_args_list], [["arecord", "-l"], ["arecord", "-L"], ["aplay", "-l"], ["aplay", "-L"]])
        self.assertEqual(result["capture"]["hardware"][0]["alsa_name"], "hw:CARD=USB,DEV=0")
        self.assertEqual(result["capture"]["pcm"][1]["name"], "plughw:CARD=USB,DEV=0")
        self.assertEqual(result["capture"]["pcm"][1]["description"], "USB Microphone Conversion available")
        self.assertEqual(result["playback"]["errors"], [])
        self.assertIn("capture", json.dumps(result))
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 3)
            self.assertEqual(call.kwargs["env"]["LC_ALL"], "C")
        self.assertEqual(self.created, [])

    def test_discovery_failures_are_reported_without_raw_stderr(self):
        failures = (
            FileNotFoundError("private"), subprocess.TimeoutExpired(["arecord"], 3, stderr="private"),
            subprocess.CalledProcessError(1, ["arecord"], stderr="private"), PermissionError("private"),
        )
        for error in failures:
            with self.subTest(error=type(error).__name__), patch.object(audio.subprocess, "run", side_effect=error):
                inventory = audio.discover_devices()
            self.assertEqual(len(inventory["capture"]["errors"]), 2)
            self.assertEqual(len(inventory["playback"]["errors"]), 2)
            self.assertNotIn("private", json.dumps(inventory))

    def test_process_timeout_kills_and_reaps_child_without_using_hardware(self):
        pid_file = self.directory / "child.pid"
        program = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
        with self.assertRaises(audio.AudioRecordingError) as caught:
            audio._run([sys.executable, "-c", program, str(pid_file)], timeout=0.5, error_type=audio.AudioRecordingError)
        self.assertEqual(caught.exception.code, "audio_timeout")
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
