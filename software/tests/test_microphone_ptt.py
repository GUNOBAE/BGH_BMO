"""Synthetic PCM/fake arecord children only: no real device or cloud API."""
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai.conversation import TurnCancelled
from bmo_ai.features.input.microphone import (
    MicError, MicPcmSource, PcmFrame, PttRecorder, SyntheticPcmSource,
)
from bmo_ai.features.resources import AudioFocus


_REAL_POPEN = subprocess.Popen


class Clock:
    def __init__(self):
        self.now = 100.
    def __call__(self):
        return self.now


class SequenceSource:
    def __init__(self, clock, patterns=None, transform=None, close_result=True):
        self.clock, self.transform = clock, transform
        self.patterns = patterns or [(0, 10), (10000, 8), (0, 24)]
        self.closed, self.close_result = 0, close_result
    def frames(self, capture_id, *, generation, cancel_check):
        index = 0
        for amplitude, count in self.patterns:
            for _ in range(count):
                cancel_check()
                self.clock.now += .04
                frame = PcmFrame(struct.pack("<h", amplitude) * 640, index, self.clock(), generation)
                yield self.transform(frame) if self.transform else frame
                index += 1
    def close(self):
        self.closed += 1
        return self.close_result


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.focus, self.sources, self.states = Clock(), AudioFocus(), [], []
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("real process forbidden")))

    def recorder(self, patterns=None, transform=None, **settings):
        def factory():
            source = SequenceSource(self.clock, patterns, transform)
            self.sources.append(source)
            return source
        recorder = PttRecorder(self.focus, factory, clock=self.clock,
                               on_state=lambda value: self.states.append(value), **settings)
        self.addCleanup(recorder.close)
        return recorder

    def test_construction_idle_and_close_never_create_sources(self):
        recorder = self.recorder()
        self.assertFalse(recorder.snapshot()["microphone_open"])
        self.assertFalse(recorder.snapshot()["automatic_capture"])
        self.assertEqual(self.sources, [])
        self.assertTrue(recorder.close())
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "input_closed")
        self.assertEqual(self.sources, [])

    def test_finite_speech_silence_result_uses_product_vad_settings(self):
        recorder = self.recorder()
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual((result.status, result.code, result.sample_rate), ("ready", "silence", 16000))
        # Calibration is excluded; the retained speech+silence is sample bounded.
        self.assertEqual(len(result.pcm), 31 * 1280)
        self.assertEqual(self.sources[0].closed, 1)
        snapshot = recorder.snapshot()
        self.assertEqual(snapshot["silence_seconds"], .9)
        self.assertEqual(snapshot["max_seconds"], 12.)
        self.assertEqual(snapshot["calibration_seconds"], .4)
        self.assertFalse(snapshot["microphone_open"])
        self.assertEqual([s["state"] for s in self.states], ["calibrating", "listening", "speech", "ready"])
        self.assertNotIn("pcm", snapshot)
        self.assertNotIn("path", snapshot)

    def test_default_synthetic_source_never_opens_process_or_network(self):
        recorder = PttRecorder(self.focus, SyntheticPcmSource)
        self.addCleanup(recorder.close)
        result = recorder.capture("sim:1", generation=1)
        self.assertEqual((result.status, result.code), ("ready", "silence"))
        self.assertLess(len(result.pcm), 384000)

    def test_no_speech_has_zero_stt_and_zero_wav_files(self):
        recorder = self.recorder([(0, 100)])
        result = recorder.capture("ptt:1", generation=1)
        transcribe = Mock(side_effect=AssertionError("STT forbidden"))
        with patch("bmo_ai.audio._new_wav", side_effect=AssertionError("WAV forbidden")):
            self.assertIsNone(recorder.transcribe_capture(result, object(), transcriber=transcribe))
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "no_speech", b""))
        transcribe.assert_not_called()

    def test_short_bursts_do_not_meet_consecutive_speech_requirement(self):
        recorder = self.recorder([(0, 10), (30000, 3), (0, 3), (30000, 3), (0, 90)])
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual((result.status, result.code), ("cancelled", "no_speech"))
        self.assertEqual(result.pcm, b"")

    def test_hum_calibration_raises_threshold_without_stt_for_noise(self):
        recorder = self.recorder([(1800, 100)])
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual((result.status, result.code), ("cancelled", "no_speech"))
        self.assertFalse(any(s["state"] == "speech" for s in self.states))

    def test_noisy_calibration_rejected_and_clears_owned_source(self):
        recorder = self.recorder([(20000, 100)])
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "calibration_noisy")
        self.assertEqual(self.sources[0].closed, 1)
        self.assertFalse(recorder.snapshot()["microphone_open"])

    def test_speech_pauses_less_than_silence_budget_preserve_turn(self):
        recorder = self.recorder([(0, 10), (10000, 8), (0, 15), (12000, 8), (0, 24)])
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual((result.status, result.code), ("ready", "silence"))
        self.assertGreater(len(result.pcm), (8 + 23) * 1280)

    def test_second_press_finishes_speech_once_without_waiting_for_silence(self):
        recorder = self.recorder([(0, 10), (10000, 50)])
        def state(value):
            if value["state"] == "speech":
                self.assertTrue(recorder.finish())
        recorder.on_state = state
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual((result.status, result.code), ("ready", "ptt_released"))
        self.assertFalse(recorder.finish())
        self.assertLess(len(result.pcm), 8 * 1280)

    def test_second_press_before_speech_returns_no_speech(self):
        recorder = self.recorder()
        recorder.on_state = lambda s: recorder.finish() if s["state"] == "calibrating" else None
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "no_speech", b""))
        self.assertEqual(self.sources[0].closed, 1)

    def test_cancel_during_speech_discards_pcm_and_rejects_transcript(self):
        recorder = self.recorder()
        recorder.on_state = lambda s: recorder.cancel() if s["state"] == "speech" else None
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "user_cancelled", b""))
        with self.assertRaises(TurnCancelled):
            recorder.transcribe_capture(result, None, transcriber=Mock())
        self.assertEqual(self.sources[0].closed, 1)

    def test_external_cancel_closes_source_and_exposes_no_stale_ready_result(self):
        recorder = self.recorder()
        checks = [0]
        def cancel():
            checks[0] += 1
            if checks[0] > 45:
                raise TurnCancelled()
        with self.assertRaises(TurnCancelled):
            recorder.capture("ptt:1", generation=1, cancel_check=cancel)
        self.assertEqual(self.sources[0].closed, 1)
        self.assertFalse(recorder.snapshot()["microphone_open"])
        self.assertEqual(recorder.snapshot()["state"], "idle")

    def test_stalled_initial_source_is_conservatively_shown_active_until_cancelled(self):
        admitted = Event()
        class BlockingSource:
            closed = False
            def frames(self, capture_id, *, generation, cancel_check):
                admitted.set()
                while True:
                    cancel_check()
                    time.sleep(.005)
                yield  # This is a generator; it intentionally never emits PCM.
            def close(self):
                self.closed = True
                return True
        source = BlockingSource()
        recorder = PttRecorder(self.focus, lambda: source)
        self.addCleanup(recorder.close)
        outcomes = []
        worker = Thread(target=lambda: outcomes.append(recorder.capture("ptt:1", generation=1)))
        worker.start()
        self.assertTrue(admitted.wait(1))
        try:
            self.assertEqual(recorder.snapshot()["state"], "calibrating")
            self.assertTrue(recorder.snapshot()["microphone_open"])
        finally:
            recorder.cancel()
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes[0].code, "user_cancelled")
        self.assertTrue(source.closed)
        self.assertFalse(recorder.snapshot()["microphone_open"])

    def test_busy_output_denies_source_creation_and_never_preempts_focus(self):
        recorder = self.recorder()
        stop = Mock()
        lease = self.focus.acquire("music", stop=stop)
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "audio_focus_busy")
        self.assertEqual(self.sources, [])
        self.assertIs(self.focus.lease, lease)
        stop.assert_not_called()

    def test_exact_owned_conversation_lease_can_be_permitted_but_playback_still_blocks(self):
        lease = self.focus.acquire("conversation")
        recorder = self.recorder(allowed_focus=lambda: self.focus.lease is lease)
        result = recorder.capture("ptt:1", generation=1)
        self.assertEqual(result.status, "ready")
        self.assertIs(self.focus.lease, lease)
        recorder.playback_active = lambda: True
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:2", generation=2)
        self.assertEqual(caught.exception.code, "audio_focus_busy")
        self.assertEqual(len(self.sources), 1)

    def test_bad_ownership_probe_fails_closed(self):
        lease = self.focus.acquire("conversation")
        recorder = self.recorder(allowed_focus=Mock(side_effect=RuntimeError("private data")))
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "audio_focus_busy")
        self.assertNotIn("private", str(caught.exception))
        self.assertIs(self.focus.lease, lease)

    def test_playback_start_mid_turn_cancels_source_without_ready_result(self):
        playing = [False]
        recorder = self.recorder(playback_active=lambda: playing[0])
        recorder.on_state = lambda s: playing.__setitem__(0, True) if s["state"] == "speech" else None
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "audio_focus_busy")
        self.assertFalse(recorder.snapshot()["microphone_open"])
        self.assertEqual(self.sources[0].closed, 1)

    def test_reentrant_capture_is_rejected_without_second_source(self):
        recorder = self.recorder()
        def state(value):
            if value["state"] == "speech":
                with self.assertRaises(MicError) as caught:
                    recorder.capture("ptt:2", generation=2)
                self.assertEqual(caught.exception.code, "microphone_busy")
        recorder.on_state = state
        self.assertEqual(recorder.capture("ptt:1", generation=1).status, "ready")
        self.assertEqual(len(self.sources), 1)

    def test_duplicate_or_older_generation_cannot_create_a_new_source(self):
        recorder = self.recorder()
        recorder.capture("ptt:1", generation=5)
        for generation in (5, 4, 0):
            with self.assertRaises(MicError) as caught:
                recorder.capture("ptt:2", generation=generation)
            self.assertEqual(caught.exception.code, "duplicate_capture")
        self.assertEqual(len(self.sources), 1)
        self.assertEqual(recorder.capture("ptt:2", generation=6).status, "ready")

    def test_invalid_frame_shapes_generation_sequences_and_timestamps_fail_closed(self):
        mutations = (
            lambda f: PcmFrame(b"x", f.sequence, f.timestamp, f.generation),
            lambda f: PcmFrame(b"x" * 3202, f.sequence, f.timestamp, f.generation),
            lambda f: PcmFrame(f.pcm, f.sequence + 1, f.timestamp, f.generation),
            lambda f: PcmFrame(f.pcm, f.sequence, f.timestamp + .5, f.generation),
            lambda f: PcmFrame(f.pcm, f.sequence, f.timestamp - 1., f.generation),
            lambda f: PcmFrame(f.pcm, f.sequence, float("nan"), f.generation),
            lambda f: PcmFrame(f.pcm, f.sequence, f.timestamp, True),
            lambda f: PcmFrame(f.pcm, f.sequence, f.timestamp, f.generation + 1),
            lambda f: "raw private bytes",
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                recorder = self.recorder(transform=mutate)
                with self.assertRaises(MicError) as caught:
                    recorder.capture("ptt:1", generation=1)
                self.assertEqual(caught.exception.code, "invalid_pcm_frame")
                self.assertFalse(recorder.snapshot()["microphone_open"])
                self.assertEqual(self.sources[-1].closed, 1)

    def test_source_disappearing_after_speech_does_not_transcribe_partial_audio(self):
        recorder = self.recorder([(0, 10), (10000, 8)])
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "microphone_stream_closed")
        self.assertEqual(recorder.snapshot()["state"], "failed")

    def test_unknown_source_failure_does_not_expose_raw_error_or_create_stt_file(self):
        recorder = self.recorder()
        recorder.source_factory = Mock(side_effect=RuntimeError("private path and API value"))
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "microphone_capture_failed")
        self.assertNotIn("private", str(caught.exception))

    def test_cleanup_failure_retains_ownership_and_blocks_subsequent_capture(self):
        recorder = self.recorder()
        bad = SequenceSource(self.clock, close_result=False)
        recorder.source_factory = Mock(return_value=bad)
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:1", generation=1)
        self.assertEqual(caught.exception.code, "pcm_cleanup_failed")
        self.assertTrue(recorder.snapshot()["microphone_open"])
        with self.assertRaises(MicError) as caught:
            recorder.capture("ptt:2", generation=2)
        self.assertEqual(caught.exception.code, "pcm_cleanup_failed")
        recorder.source_factory.assert_called_once()
        self.assertFalse(recorder.close())
        bad.close_result = True
        self.assertTrue(recorder.close())

    def test_one_ready_capture_transcribes_once_private_wav_and_always_unlinks(self):
        recorder = self.recorder()
        capture = recorder.capture("ptt:1", generation=1)
        paths, client = [], object()
        def transcriber(given_client, path, **options):
            self.assertIs(given_client, client)
            paths.append(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                self.assertFalse(os.get_inheritable(descriptor))
            finally:
                os.close(descriptor)
            with wave.open(str(path), "rb") as recorded:
                self.assertEqual((recorded.getframerate(), recorded.getnchannels(), recorded.getsampwidth()), (16000, 1, 2))
                self.assertEqual(recorded.readframes(recorded.getnframes()), capture.pcm)
            self.assertEqual(options, dict(model="fake-stt", language="ko"))
            return "합성 대화"
        run = Mock(side_effect=transcriber)
        self.assertEqual(recorder.transcribe_capture(capture, client, model="fake-stt", language="ko", transcriber=run), "합성 대화")
        self.assertEqual(run.call_count, 1)
        self.assertFalse(paths[0].exists())
        with self.assertRaises(MicError) as caught:
            recorder.transcribe_capture(capture, client, transcriber=run)
        self.assertEqual(caught.exception.code, "stale_transcript")
        self.assertEqual(run.call_count, 1)

    def test_continuous_speech_stops_at_total_twelve_second_wall_budget(self):
        recorder = self.recorder([(0, 10), (10000, 400)])
        capture = recorder.capture("ptt:1", generation=1)
        self.assertEqual((capture.status, capture.code), ("ready", "max_duration"))
        self.assertLessEqual(len(capture.pcm), 384000)
        self.assertLessEqual(self.clock() - 100., 12.05)
        self.assertEqual(self.sources[0].closed, 1)

    def test_wav_cleanup_failure_is_reported_and_stt_result_is_not_returned(self):
        recorder = self.recorder()
        capture = recorder.capture("ptt:1", generation=1)
        from bmo_ai import audio
        original_discard = audio._discard
        paths = []
        def reject(path, **options):
            paths.append(path)
            original_discard(path)
            raise audio.AudioCleanupError("safe fixed cleanup", "audio_cleanup_failed")
        with patch.object(audio, "_discard", side_effect=reject), self.assertRaises(audio.AudioCleanupError):
            recorder.transcribe_capture(capture, None, transcriber=lambda *a, **k: "synthetic")
        self.assertFalse(paths[0].exists())

    def test_stt_error_unlinks_private_wav_and_cannot_retranscribe(self):
        recorder = self.recorder()
        capture = recorder.capture("ptt:1", generation=1)
        paths = []
        def fail(client, path, **options):
            paths.append(path)
            raise RuntimeError("injected provider failure")
        with self.assertRaises(RuntimeError):
            recorder.transcribe_capture(capture, None, transcriber=fail)
        self.assertFalse(paths[0].exists())
        with self.assertRaises(MicError) as caught:
            recorder.transcribe_capture(capture, None, transcriber=Mock())
        self.assertEqual(caught.exception.code, "stale_transcript")

    def test_cancel_during_stt_rejects_late_transcript_and_deletes_audio(self):
        recorder = self.recorder()
        capture = recorder.capture("ptt:1", generation=1)
        paths = []
        def late(client, path, **options):
            paths.append(path)
            recorder.cancel()
            return "late private transcript"
        with self.assertRaises(TurnCancelled):
            recorder.transcribe_capture(capture, None, transcriber=late)
        self.assertFalse(paths[0].exists())
        self.assertEqual(recorder.snapshot()["reason_code"], "user_cancelled")

    def test_reentrant_transcriber_cannot_start_second_microphone_or_reuse_result(self):
        recorder = self.recorder()
        capture = recorder.capture("ptt:1", generation=1)
        def transcriber(client, path, **options):
            for call in (lambda: recorder.capture("ptt:2", generation=2),
                         lambda: recorder.transcribe_capture(capture, None)):
                with self.assertRaises(MicError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "microphone_busy")
            return "synthetic"
        self.assertEqual(recorder.transcribe_capture(capture, None, transcriber=transcriber), "synthetic")
        self.assertEqual(len(self.sources), 1)

    def test_new_capture_invalidates_unconsumed_old_result(self):
        recorder = self.recorder()
        old = recorder.capture("ptt:1", generation=1)
        new = recorder.capture("ptt:2", generation=2)
        run = Mock(return_value="ok")
        with self.assertRaises(MicError) as caught:
            recorder.transcribe_capture(old, None, transcriber=run)
        self.assertEqual(caught.exception.code, "stale_transcript")
        self.assertEqual(recorder.transcribe_capture(new, None, transcriber=run), "ok")
        self.assertEqual(run.call_count, 1)

    def test_invalid_settings_and_capture_identity_never_start_source(self):
        for settings in ({"calibration": .2}, {"calibration": float("nan")}, {"max_duration": 13},
                         {"noise_multiplier": .5}, {"max_noise": True}, {"min_speech": -1}):
            with self.subTest(settings=settings), self.assertRaises(Exception):
                self.recorder(**settings)
        recorder = self.recorder()
        for identity, generation in (("bad path", 1), ("ptt:1", True), ("ptt:1", -1)):
            with self.assertRaises(MicError):
                recorder.capture(identity, generation=generation)
        self.assertEqual(self.sources, [])

    def test_pcm_repr_and_snapshots_do_not_publish_raw_pcm_or_transcripts(self):
        frame = PcmFrame(b"PRIVATE_PCM_BYTES", 0, 1., 1)
        self.assertNotIn("PRIVATE", repr(frame))
        recorder = self.recorder()
        capture = recorder.capture("ptt:1", generation=1)
        self.assertNotIn(repr(capture.pcm), repr(capture))
        self.assertTrue(all("pcm" not in state and "transcript" not in state for state in self.states))


class MicPcmSourceTests(unittest.TestCase):
    def setUp(self):
        self.processes, self.calls = [], []
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("real recorder forbidden")))

    def source(self, script, **settings):
        def factory(command, **options):
            self.calls.append((command, options))
            process = _REAL_POPEN([sys.executable, "-c", script], **options)
            self.processes.append(process)
            return process
        source = MicPcmSource(live_opt_in=True, process_factory=factory, **settings)
        self.addCleanup(source.close)
        return source

    def test_default_opt_in_off_never_spawns(self):
        factory = Mock(side_effect=AssertionError("spawn forbidden"))
        source = MicPcmSource(process_factory=factory)
        self.assertFalse(source.snapshot()["enabled"])
        with self.assertRaises(MicError) as caught:
            next(source.frames("ptt:1", generation=1))
        self.assertEqual(caught.exception.code, "microphone_opt_in_required")
        factory.assert_not_called()
        self.assertTrue(source.close())

    def test_native_command_short_reads_cloexec_environment_and_owned_reap(self):
        script = "import os,time; data=b'\\x01\\x02'*640; [(os.write(1,data[i:i+137]),time.sleep(.001)) for i in range(0,len(data),137)]; time.sleep(5)"
        source = self.source(script)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-private", "FISH_API_KEY": "fake-fish", "LD_PRELOAD": "fake-preload", "PYTHONPATH": "fake-hooks"}):
            iterator = source.frames("ptt:1", generation=7)
            frame = next(iterator)
        self.assertEqual((len(frame.pcm), frame.sequence, frame.generation), (1280, 0, 7))
        self.assertFalse(os.get_inheritable(self.processes[0].stdout.fileno()))
        self.assertFalse(os.get_blocking(self.processes[0].stdout.fileno()))
        command, options = self.calls[0]
        self.assertEqual(command, ["/usr/bin/arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1", "-D", "plughw:CARD=Device,DEV=0"])
        for key in ("OPENAI_API_KEY", "FISH_API_KEY", "LD_PRELOAD", "PYTHONPATH"):
            self.assertNotIn(key, options["env"])
        self.assertEqual(options["env"]["PATH"], "/usr/bin:/bin")
        self.assertTrue(options["close_fds"])
        self.assertTrue(options["start_new_session"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        iterator.close()
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertTrue(self.processes[0].stdout.closed)
        self.assertFalse(source.snapshot()["microphone_open"])

    def test_fake_arecord_to_vad_to_existing_stt_is_one_owned_child_one_request(self):
        script = ("import os,struct,time; "
                  "pcm=struct.pack('<h',0)*640*10+struct.pack('<h',10000)*640*8+struct.pack('<h',0)*640*24; "
                  "view=memoryview(pcm); "
                  "\nwhile view:\n n=os.write(1,view[:937]); view=view[n:]\n"
                  "time.sleep(5)")
        recorder = PttRecorder(AudioFocus(), lambda: self.source(script))
        self.addCleanup(recorder.close)
        capture = recorder.capture("ptt:1", generation=1)
        paths = []
        def create(*, file, **options):
            paths.append(Path(file.name))
            self.assertEqual(options, dict(model="gpt-4o-mini-transcribe", response_format="json", language="ko"))
            self.assertEqual(stat.S_IMODE(os.fstat(file.fileno()).st_mode), 0o600)
            with wave.open(file, "rb") as contents:
                self.assertEqual(contents.getframerate(), 16000)
                self.assertEqual(contents.readframes(contents.getnframes()), capture.pcm)
            return SimpleNamespace(text="합성 테스트")
        sdk_call = Mock(side_effect=create)
        fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=sdk_call)))
        transcript = recorder.transcribe_capture(capture, fake_client, language="ko")
        self.assertEqual(transcript, "합성 테스트")
        self.assertEqual(sdk_call.call_count, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(process.returncode is not None for process in self.processes))
        self.assertTrue(all(not path.exists() for path in paths))

    def test_partial_pipe_eof_is_malformed_and_never_pads_a_fake_frame(self):
        source = self.source("import os; os.write(1,b'x'*641)")
        with self.assertRaises(MicError) as caught:
            list(source.frames("ptt:1", generation=1))
        self.assertEqual(caught.exception.code, "pcm_truncated")
        self.assertIsNotNone(self.processes[0].returncode)

    def test_clean_eof_after_complete_frame_is_not_successful_speech(self):
        source = self.source("import os; os.write(1,b'x'*1280)")
        iterator = source.frames("ptt:1", generation=1)
        self.assertEqual(len(next(iterator).pcm), 1280)
        with self.assertRaises(MicError) as caught:
            next(iterator)
        self.assertEqual(caught.exception.code, "microphone_stream_closed")
        self.assertIsNotNone(self.processes[0].returncode)

    def test_stalled_child_hits_bounded_timeout_and_is_reaped(self):
        source = self.source("import time; time.sleep(5)", max_duration=.12)
        began = time.monotonic()
        with self.assertRaises(MicError) as caught:
            next(source.frames("ptt:1", generation=1))
        self.assertEqual(caught.exception.code, "pcm_duration_limit")
        self.assertLess(time.monotonic() - began, 1.)
        self.assertIsNotNone(self.processes[0].returncode)

    def test_cancel_stalled_child_returns_within_poll_budget_and_reaps(self):
        source = self.source("import time; time.sleep(5)")
        checks = [0]
        def cancel():
            checks[0] += 1
            if checks[0] >= 5:
                raise TurnCancelled()
        began = time.monotonic()
        with self.assertRaises(TurnCancelled):
            next(source.frames("ptt:1", generation=1, cancel_check=cancel))
        self.assertLess(time.monotonic() - began, 1.)
        self.assertIsNotNone(self.processes[0].returncode)

    def test_child_ignoring_term_is_killed_and_reaped_without_global_signals(self):
        script = "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); os.write(1,b'x'*1280); time.sleep(5)"
        source = self.source(script)
        iterator = source.frames("ptt:1", generation=1)
        next(iterator)
        began = time.monotonic()
        iterator.close()
        self.assertEqual(self.processes[0].returncode, -9)
        self.assertLess(time.monotonic() - began, 1.)

    def test_byte_limit_closes_fast_fake_recorder_without_unbounded_buffer(self):
        source = self.source("import os,time; os.write(1,b'x'*20000); time.sleep(5)", max_duration=.12)
        iterator = source.frames("ptt:1", generation=1)
        frames = []
        with self.assertRaises(MicError) as caught:
            while True:
                frames.append(next(iterator))
        self.assertEqual(caught.exception.code, "pcm_duration_limit")
        self.assertEqual(sum(len(f.pcm) for f in frames), 3840)
        self.assertEqual(source.snapshot()["captured_bytes"], source.snapshot()["max_bytes"])
        self.assertIsNotNone(self.processes[0].returncode)

    def test_duplicate_native_stream_cannot_start_second_child(self):
        source = self.source("import os,time; os.write(1,b'x'*1280); time.sleep(5)")
        iterator = source.frames("ptt:1", generation=1)
        next(iterator)
        with self.assertRaises(MicError) as caught:
            next(source.frames("ptt:2", generation=2))
        self.assertEqual(caught.exception.code, "microphone_busy")
        self.assertEqual(len(self.calls), 1)
        iterator.close()

    def test_selector_setup_and_close_failure_still_reaps_child_and_unlocks_capture(self):
        class BadSelector:
            broken = True
            def register(self, *args):
                raise OSError("private selector registration")
            def close(self):
                if self.broken:
                    raise OSError("private selector close")
        selector = BadSelector()
        source = self.source("import time; time.sleep(5)")
        with patch("selectors.DefaultSelector", return_value=selector):
            with self.assertRaises(MicError) as caught:
                next(source.frames("ptt:1", generation=1))
        self.assertEqual(caught.exception.code, "pcm_cleanup_failed")
        self.assertNotIn("private", str(caught.exception))
        self.assertIsNotNone(self.processes[0].returncode)
        self.assertTrue(self.processes[0].stdout.closed)
        self.assertFalse(source._capture_lock.locked())
        self.assertIs(source._selector, selector)
        self.assertFalse(source.close())
        selector.broken = False
        self.assertTrue(source.close())
        self.assertIsNone(source._selector)

    def test_missing_tool_and_spawn_failures_have_only_fixed_codes(self):
        for failure, expected in ((FileNotFoundError("secret path"), "microphone_tool_unavailable"),
                                  (OSError("private ALSA data"), "microphone_start_failed")):
            source = MicPcmSource(live_opt_in=True, process_factory=Mock(side_effect=failure))
            with self.assertRaises(MicError) as caught:
                next(source.frames("ptt:1", generation=1))
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("private", str(caught.exception))
            self.assertTrue(source.close())

    def test_unconfirmed_reap_returns_false_keeps_handle_and_allows_bounded_retry(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("fake", .2)
        source = MicPcmSource(live_opt_in=True)
        source._process = process
        self.assertFalse(source.close())
        self.assertTrue(source.snapshot()["microphone_open"])
        self.assertEqual(source.snapshot()["reason_code"], "pcm_cleanup_failed")
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        process.wait.side_effect = None
        process.wait.return_value = 0
        self.assertTrue(source.close())
        self.assertFalse(source.snapshot()["microphone_open"])
        self.assertTrue(source.close())

    def test_invalid_native_configuration_is_rejected_before_any_spawn(self):
        for settings in ({"live_opt_in": 1}, {"frame_ms": 10}, {"frame_ms": 101},
                         {"frame_ms": True}, {"max_duration": 13}, {"max_duration": float("nan")},
                         {"device": "bad\nalsa"}):
            with self.subTest(settings=settings), self.assertRaises(MicError):
                MicPcmSource(**settings)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
