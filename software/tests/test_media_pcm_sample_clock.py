"""Injected silent PCM sinks: consumed sample clock, one output, cancel/seek."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media import MediaAsset, MediaFeature, SampleClockWavBackend
from bmo_ai.master_volume import MasterVolume


class SilentPcmSink:
    active = 0
    peak_active = 0
    sinks = []

    def __init__(self, device, rate, channels):
        self.guard = threading.Lock()
        self.rate, self.channels = rate, channels
        self.submitted = self.consumed = 0
        self.samples = bytearray()
        self.closed = False
        SilentPcmSink.active += 1
        SilentPcmSink.peak_active = max(SilentPcmSink.peak_active, SilentPcmSink.active)
        SilentPcmSink.sinks.append(self)

    def write(self, block):
        with self.guard:
            count = min(len(block) // (self.channels * 2), 160 - (self.submitted - self.consumed))
            self.submitted += count
            self.samples.extend(block[:count * self.channels * 2])
            return count

    def delay(self):
        with self.guard:
            return self.submitted - self.consumed

    def advance(self, frames):
        with self.guard:
            self.consumed = min(self.submitted, self.consumed + frames)

    def close(self):
        if not self.closed:
            self.closed = True
            SilentPcmSink.active -= 1


class MediaPcmSampleClockTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-music-clock-")))
        self.path = self.root / "synthetic.wav"
        with wave.open(str(self.path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
            wav.writeframes((10000).to_bytes(2, "little", signed=True) * 8000)
        item = MediaAsset("space_potatoes", "Synthetic clock input", "generated_test", self.path.name,
            hashlib.sha256(self.path.read_bytes()).hexdigest(), "audio/wav", 1.)
        self.volume = MasterVolume(.25)
        self.backend = SampleClockWavBackend(enabled=True, volume_reviewed=True, volume=self.volume,
                                              sink_factory=SilentPcmSink)
        self.feature = MediaFeature(self.root, (item,), self.backend)
        self.addCleanup(self.feature.close)
        SilentPcmSink.active = SilentPcmSink.peak_active = 0
        SilentPcmSink.sinks = []
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No extra output process")))

    def wait(self, condition):
        deadline = time.monotonic() + 1.
        while not condition():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)

    def start(self):
        result = self.feature.start("space_potatoes", request_id="media:1", mode_generation=1)
        self.assertEqual(result.status, "running")
        self.wait(lambda: SilentPcmSink.sinks and SilentPcmSink.sinks[-1].submitted == 160)
        return SilentPcmSink.sinks[-1]

    def test_clock_waits_for_sink_consumed_frames_not_process_start_or_wall_time(self):
        sink = self.start()
        self.assertFalse(self.feature.snapshot()["output_started"])
        self.assertEqual(self.backend.position(), 0.)
        time.sleep(.04)
        self.assertEqual(self.backend.position(), 0.)
        sink.advance(80)
        self.wait(lambda: self.backend.position() == .01)
        self.assertTrue(self.feature.snapshot()["output_started"])
        self.assertEqual(self.feature.snapshot()["clock_quality"], "alsa_submitted_minus_delay")
        self.assertEqual(self.feature.snapshot()["position_seconds"], .01)
        self.assertEqual(int.from_bytes(sink.samples[:2], "little", signed=True), 2500)

    def test_pause_seek_resume_and_volume_keep_single_output_at_consumed_sample(self):
        sink = self.start()
        sink.advance(80)
        self.wait(lambda: self.backend.position() == .01)
        self.feature.pause()
        self.assertTrue(sink.closed)
        self.assertEqual(SilentPcmSink.active, 0)
        self.assertEqual(self.backend.position(), .01)
        self.feature.seek(.5)
        self.assertEqual(self.backend.position(), .5)
        self.assertEqual(SilentPcmSink.active, 0)
        self.feature.resume()
        self.wait(lambda: SilentPcmSink.sinks[-1].submitted == 160)
        second = SilentPcmSink.sinks[-1]
        self.assertEqual(self.backend.position(), .5)
        self.assertEqual(SilentPcmSink.peak_active, 1)
        self.volume.set(muted=True)
        self.backend.volume_changed()
        self.wait(lambda: SilentPcmSink.sinks[-1].submitted == 160)
        self.assertTrue(second.closed)
        self.assertEqual(SilentPcmSink.sinks[-1].samples, bytes(320))
        self.assertEqual(self.backend.position(), .5)
        self.feature.stop()
        self.assertEqual(SilentPcmSink.active, 0)
        self.assertEqual(self.backend.position(), 0.)

    def test_failed_pcm_sink_does_not_claim_started_sound_or_leave_owner_active(self):
        def failure(*args):
            raise FeatureError("media_output_unavailable")
        self.backend._sink_factory = failure
        result = self.feature.start("space_potatoes", request_id="fail:1", mode_generation=1)
        self.assertEqual((result.status, result.code), ("failed", "media_output_unavailable"))
        self.assertEqual(self.feature.snapshot()["state"], "idle")
        self.assertIsNone(self.backend._thread)
        self.assertTrue(self.feature.poll()[-1].data["resource_released"])

    def test_unverified_backend_never_constructs_sink(self):
        with patch.object(self.backend, "_sink_factory") as factory:
            self.backend.verified = False
            result = self.feature.start("space_potatoes", request_id="blocked:1", mode_generation=1)
            self.assertEqual(result.code, "media_backend_unverified")
            factory.assert_not_called()

    def test_short_wav_starts_its_final_partial_buffer_before_reporting_ended(self):
        with wave.open(str(self.path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
            wav.writeframes(b"\0\0" * 80)  # 10ms, shorter than the ALSA start threshold.
        item = MediaAsset("short_song", "Short generated PCM", "generated_test", self.path.name,
            hashlib.sha256(self.path.read_bytes()).hexdigest(), "audio/wav", .01)
        class ThresholdSink(SilentPcmSink):
            def __init__(self, *args):
                super().__init__(*args)
                self.input_finished = False
            def finish_input(self):
                self.input_finished = True
            def delay(self):
                if self.input_finished:
                    self.advance(self.submitted)
                return super().delay()
        backend = SampleClockWavBackend(enabled=True, volume_reviewed=True, sink_factory=ThresholdSink)
        feature = MediaFeature(self.root, (item,), backend)
        self.addCleanup(feature.close)
        self.assertEqual(feature.start(item.id, request_id="short:1", mode_generation=1).status, "running")
        self.wait(lambda: backend.poll() == "ended")
        self.assertTrue(SilentPcmSink.sinks[-1].input_finished)
        self.assertAlmostEqual(backend.position(), .01)
        self.assertTrue(feature.poll()[-1].data["resource_released"])

    def test_seek_to_end_closes_owned_output_without_starting_empty_pcm(self):
        first = self.start()
        self.assertTrue(self.feature.seek(1.).completed)
        self.assertTrue(first.closed)
        self.assertEqual(len(SilentPcmSink.sinks), 1)
        self.assertEqual(SilentPcmSink.active, 0)
        self.assertEqual(self.backend.position(), 1.)
        self.assertEqual(self.backend.poll(), "ended")
        self.assertTrue(self.feature.poll()[-1].data["resource_released"])

    def test_replay_and_stale_duplicate_do_not_create_overlapping_output(self):
        sink = self.start()
        sink.advance(120)
        self.wait(lambda: self.backend.position() == .015)
        self.feature.replay()
        self.assertTrue(sink.closed)
        self.assertEqual(self.backend.position(), 0.)
        duplicate = self.feature.start("space_potatoes", request_id="media:1", mode_generation=1)
        self.assertEqual(duplicate.code, "duplicate_request")
        self.assertEqual(SilentPcmSink.peak_active, 1)

    def test_uncertain_output_cleanup_retains_ownership_until_explicit_retry(self):
        sink = self.start()
        with patch.object(sink, "close", side_effect=OSError("injected cleanup failure")):
            result = self.feature.stop()
            self.assertEqual((result.status, result.code), ("unknown", "cleanup_failed"))
            self.assertIsNotNone(self.feature._handle)
            self.assertIs(self.backend._retained_sink, sink)
            self.assertEqual(SilentPcmSink.active, 1)
            self.assertEqual(self.feature.start("space_potatoes", request_id="late:2", mode_generation=2).code, "media_busy")
        self.assertTrue(self.feature.stop().completed)
        self.assertEqual(SilentPcmSink.active, 0)
        self.assertIsNone(self.backend._retained_sink)


if __name__ == "__main__":
    unittest.main()
