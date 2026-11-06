"""Offline-only safety tests for the explicit one-capture microphone helper."""
import contextlib
import io
import json
from pathlib import Path
import stat
import struct
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.device_acceptance.microphone import (
    AcceptanceError, PcmEvidence, acceptance_plan, main, run_microphone_acceptance)
from bmo_ai.features.input.microphone import MicError, MicPcmSource, PcmFrame, PttRecorder, SyntheticPcmSource
from bmo_ai.features.resources import AudioFocus


class QuietSource:
    sample_rate = 16000
    def __init__(self, *, amplitude=100, delay=.04, frames=2000):
        self.amplitude, self.delay, self.limit, self.closed = amplitude, delay, frames, False
    def frames(self, capture_id, *, generation, cancel_check=None):
        for sequence in range(self.limit):
            if cancel_check is not None:
                cancel_check()
            time.sleep(self.delay)
            if cancel_check is not None:
                cancel_check()
            yield PcmFrame(struct.pack("<h", self.amplitude)*640, sequence, time.monotonic(), generation)
    def close(self):
        self.closed = True
        return True


class AcceptanceMicrophoneTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.cloud.create_client", "bmo_ai.fish_tts.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("actual device/API forbidden in test")))
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_default_plan_and_cli_have_zero_io_even_if_output_was_selected(self):
        nonexistent = self.directory / "must-not-be-created"
        with patch("bmo_ai.features.host.FeatureHost", side_effect=AssertionError("constructor forbidden")):
            result = run_microphone_acceptance(nonexistent)
            self.assertEqual(result["status"], "NOT_RUN")
            self.assertFalse(nonexistent.exists())
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["--output-directory", str(nonexistent)]), 0)
            self.assertEqual(json.loads(printed.getvalue())["status"], "NOT_RUN")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_limits_reject_unbounded_capture_and_bad_cancel_delay(self):
        for seconds in (0, 10.1, float("nan"), True):
            with self.assertRaises(AcceptanceError):
                acceptance_plan(max_seconds=seconds)
        with self.assertRaises(AcceptanceError):
            acceptance_plan(trial="cancel", max_seconds=1, cancel_after=2)
        with self.assertRaises(AcceptanceError):
            run_microphone_acceptance(live_acceptance=1)

    def test_existing_public_directory_is_rejected_without_chmod(self):
        public = self.directory / "public"
        public.mkdir(mode=0o755)
        before = public.stat().st_mode
        with self.assertRaises(AcceptanceError) as error:
            run_microphone_acceptance(public, live_acceptance=True)
        self.assertEqual(error.exception.code, "private_directory_permissions")
        self.assertEqual(before, public.stat().st_mode)
        self.assertEqual(list(public.iterdir()), [])

    def test_repository_raw_output_is_rejected_before_any_file_or_host(self):
        repository = Path(__file__).resolve().parents[2]
        target = repository / "should-not-create-raw-audio"
        with self.assertRaises(AcceptanceError) as error:
            run_microphone_acceptance(target, live_acceptance=True)
        self.assertEqual(error.exception.code, "raw_audio_in_repository_forbidden")
        self.assertFalse(target.exists())

    def test_real_host_broker_ptt_path_uses_one_injected_source_private_pcm_and_no_api(self):
        sources = []
        def factory():
            source = SyntheticPcmSource()
            sources.append(source)
            return source
        result = run_microphone_acceptance(self.directory, live_acceptance=True, _source_factory=factory)
        self.assertEqual(result["status"], "OFFLINE_CAPTURE_PASS")
        self.assertEqual(result["physical_microphone"], "NOT_RUN")
        self.assertEqual(len(sources), result["source_opens"])
        self.assertEqual(result["source_opens"], 1)
        self.assertEqual(result["paid_api_attempts"], 0)
        self.assertEqual(result["voice_or_speaker_attempts"], 0)
        self.assertEqual(result["local_empty_transcript_sink_calls"], 1)
        self.assertEqual(result["ptt_status"]["outcome"], "VAD_SILENCE_END")
        self.assertTrue(result["resources_cleaned"])
        self.assertFalse(result["broker"]["microphone_open"])
        self.assertFalse(result["broker"]["reader_alive"])
        self.assertEqual(result["real_human_speech"], "NOT_RUN")
        self.assertEqual(result["pcm"]["xrun"], "UNMEASURED_STDERR_DISCARDED")
        self.assertEqual(result["wav"]["frames"], result["pcm"]["samples"])
        for path in self.directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(len(list(self.directory.glob("*.wav"))), 1)
        self.assertEqual(len(list(self.directory.glob("*.json"))), 1)

    def test_first_pcm_ipc_is_private_metadata_only_and_exclusive(self):
        ready = self.directory / "ready.json"
        result = run_microphone_acceptance(self.directory, live_acceptance=True,
            trial="reopen", ready_file=ready, _source_factory=SyntheticPcmSource)
        self.assertEqual(result["status"], "OFFLINE_CAPTURE_PASS")
        self.assertEqual(result["source_opens"], 1)
        self.assertTrue(result["wake_off_before_ptt"]["cleanup_verified"])
        self.assertFalse(result["wake_off_before_ptt"]["microphone_open"])
        self.assertFalse(result["wake_off_before_ptt"]["reader_alive"])
        self.assertFalse(result["wake_off_before_ptt"]["wake_enabled"])
        self.assertEqual(result["wake_off_before_ptt"]["detector_status"], "DISABLED")
        self.assertEqual(result["wake_off_before_ptt"]["persistent_reader_acoustic_off"], "NOT_RUN_MODEL_MISSING")
        metadata = json.loads(ready.read_text())
        self.assertEqual(metadata["event"], "first_pcm_received")
        self.assertEqual(metadata["frame_bytes"], 1280)
        self.assertEqual(stat.S_IMODE(ready.stat().st_mode), 0o600)
        self.assertNotIn("pcm", metadata)
        before = ready.read_bytes()
        with self.assertRaises(AcceptanceError):
            run_microphone_acceptance(self.directory, live_acceptance=True,
                ready_file=ready, _source_factory=SyntheticPcmSource)
        self.assertEqual(ready.read_bytes(), before)
        self.assertEqual(result["loopback"], "NOT_EVALUATED_BY_MIC_HELPER")

    def test_explicit_cancel_is_bounded_and_raw_frames_remain_private(self):
        source = QuietSource()
        result = run_microphone_acceptance(self.directory, live_acceptance=True,
            trial="cancel", cancel_after=.1, max_seconds=2., _source_factory=lambda: source)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["deadline_reached"])
        self.assertLess(result["elapsed_seconds"], 1.5)
        self.assertTrue(source.closed)
        self.assertTrue(result["resources_cleaned"])
        self.assertGreater(result["pcm"]["frames"], 0)
        self.assertEqual(result["local_empty_transcript_sink_calls"], 0)
        self.assertEqual(result["paid_api_attempts"], 0)
        self.assertEqual(result["ptt_status"]["outcome"], "EXPLICIT_CANCELLED")
        self.assertEqual(result["ptt_status"]["harness_cancellation"], "EXPLICIT_CANCELLED")

    def test_deadline_cancels_one_source_without_background_listener(self):
        source = QuietSource()
        result = run_microphone_acceptance(self.directory, live_acceptance=True,
            max_seconds=.2, _source_factory=lambda: source)
        self.assertTrue(result["deadline_reached"])
        self.assertTrue(result["cancelled"])
        self.assertLess(result["elapsed_seconds"], 1.5)
        self.assertTrue(source.closed)
        self.assertFalse(result["broker"]["reader_alive"])
        self.assertEqual(result["source_opens"], 1)
        self.assertEqual(result["ptt_status"]["outcome"], "BOUNDED_DEADLINE_CANCELLED")
        self.assertEqual(result["ptt_status"]["harness_cancellation"], "BOUNDED_DEADLINE_CANCELLED")

    def test_all_zero_is_not_papered_over_as_quiet_room_or_spoken_ptt_success(self):
        source = QuietSource(amplitude=0)
        result = run_microphone_acceptance(self.directory, live_acceptance=True,
            trial="cancel", max_seconds=2, cancel_after=.1, _source_factory=lambda: source)
        self.assertEqual(result["status"], "OFFLINE_ALL_ZERO_NEEDS_DIAGNOSIS")
        self.assertTrue(result["pcm"]["all_zero"])
        self.assertEqual(result["pcm"]["nonzero_samples"], 0)
        self.assertTrue(result["resources_cleaned"])
        self.assertEqual(result["human_vad_accuracy"], "NOT_RUN")

    def test_pcm_evidence_detects_real_zero_nonzero_clipping_and_delivery_gaps(self):
        evidence = PcmEvidence(button_at=10.)
        first = struct.pack("<4h", 0, 32767, -32768, 1000)
        evidence.add(PcmFrame(first, 0, 10.04, 1))
        evidence.add(PcmFrame(bytes(1280), 2, 10.25, 1))
        value = evidence.snapshot()
        self.assertEqual(value["clipped_samples"], 2)
        self.assertEqual(value["sequence_errors"], 1)
        self.assertEqual(value["frame_length_errors"], 1)
        self.assertEqual(value["host_delivery_large_gaps"], 1)
        self.assertEqual(value["first_pcm_buffer_bytes"], len(first))
        self.assertEqual(value["driver_dropped_frames"], "UNMEASURED")
        self.assertNotIn("pcm", value)

    def test_device_pcm_evidence_does_not_hide_separate_ptt_stream_error(self):
        class EndsEarly(QuietSource):
            def frames(self, capture_id, *, generation, cancel_check=None):
                yield PcmFrame(struct.pack("<h", 100) * 640, 0, time.monotonic(), generation)
                raise MicError("pcm_truncated")
        result = run_microphone_acceptance(self.directory, live_acceptance=True,
            _source_factory=EndsEarly)
        self.assertEqual(result["status"], "OFFLINE_CAPTURE_PASS")
        self.assertEqual(result["ptt_status"]["outcome"], "CAPTURE_ERROR")
        self.assertEqual(result["ptt_status"]["capture"]["code"], "pcm_truncated")
        self.assertEqual(result["code"], "pcm_truncated")


class PlannedSourceDeadlineTests(unittest.TestCase):
    """Observed 125ms burst cadence reproduced without processes/devices."""
    def setUp(self):
        self.clock = SimpleNamespace(now=100.)
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("actual process forbidden")))
        self.enterContext(patch("os.set_inheritable"))
        self.enterContext(patch("os.set_blocking"))

    def source(self, *, unexpected_eof=False):
        clock = self.clock
        # Three complete40ms frames arrive in each125ms driver burst. The last
        # burst provides only20ms; a finite wall cap then retires that fragment.
        delays = [.125, 0., 0., .125, 0., 0., .125, .026]
        chunks = [struct.pack("<h", 3000) * 640] * 6 + [b"\x01\x00" * 320]
        if unexpected_eof:
            delays = [.125, 0., 0.]
            chunks = [chunks[0], chunks[-1], b""]
        events = iter(delays)
        class Selector:
            def register(self, *args): pass
            def close(self): pass
            def select(self, timeout):
                delay = next(events)
                clock.now += delay
                return [] if not unexpected_eof and delay == .026 else [object()]
        process = SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: 123, close=lambda: None),
                                  wait=lambda **kwargs: 0)
        self.enterContext(patch("selectors.DefaultSelector", return_value=Selector()))
        self.enterContext(patch("os.read", side_effect=chunks))
        return MicPcmSource(live_opt_in=True, max_duration=.4, clock=lambda: clock.now,
                            process_factory=lambda *args, **kwargs: process)

    def test_planned_partial_frame_at_wall_cap_is_duration_limit_and_reports_discard(self):
        source = self.source()
        frames = []
        with self.assertRaises(MicError) as error:
            for frame in source.frames("planned-cap", generation=1):
                frames.append(frame)
        self.assertEqual(error.exception.code, "pcm_duration_limit")
        self.assertEqual(len(frames), 6)
        self.assertEqual([frame.sequence for frame in frames], list(range(6)))
        self.assertEqual(source.snapshot()["planned_end"], "wall_duration")
        self.assertEqual(source.snapshot()["discarded_partial_bytes"], 640)
        self.assertFalse(source.snapshot()["microphone_open"])

    def test_existing_recorder_returns_bounded_max_duration_without_padding_partial_frame(self):
        source = self.source()
        recorder = PttRecorder(AudioFocus(), lambda: source, clock=lambda: self.clock.now,
                               speech_safe=True, threshold=.008)
        self.addCleanup(recorder.close)
        capture = recorder.capture("planned-host-cap", generation=1)
        self.assertEqual((capture.status, capture.code), ("ready", "max_duration"))
        self.assertEqual(len(capture.pcm), 6 * 1280)
        self.assertEqual(capture.pcm, struct.pack("<h", 3000) * (6 * 640))
        self.assertEqual(source.snapshot()["discarded_partial_bytes"], 640)

    def test_unexpected_midframe_eof_remains_truncated_without_planned_end(self):
        source = self.source(unexpected_eof=True)
        with self.assertRaises(MicError) as error:
            tuple(source.frames("unexpected-eof", generation=1))
        self.assertEqual(error.exception.code, "pcm_truncated")
        self.assertIsNone(source.snapshot()["planned_end"])
        self.assertEqual(source.snapshot()["discarded_partial_bytes"], 0)
        self.assertFalse(source.snapshot()["microphone_open"])


if __name__ == "__main__": unittest.main()
