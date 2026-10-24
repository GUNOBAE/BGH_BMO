"""Fake clock and in-memory PCM; never opens audio, network or subprocesses."""
import struct
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.input import PcmTurnGate, WakeWordGate
from bmo_ai.features.resources import AudioFocus


class PcmInputTests(unittest.TestCase):
    def setUp(self):
        self.now, self.focus = 20., AudioFocus()
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("Process forbidden")))

    def gate(self, **settings):
        gate = PcmTurnGate(self.focus, enabled=True, clock=lambda: self.now, **settings)
        self.addCleanup(gate.close)
        return gate

    def pcm(self, amplitude=2000, duration=.02, sample_rate=16000):
        return struct.pack("<h", amplitude) * int(sample_rate * duration)

    def feed(self, gate, frames, *, amplitude=2000, begin=0, generation=1, advance=True):
        results = []
        for sequence in range(begin, begin + frames):
            if advance:
                self.now += .02
            results.append(gate.feed(self.pcm(amplitude), sequence=sequence, timestamp=self.now,
                                     generation=generation))
        return results

    def test_default_is_disabled_without_implicit_recording_or_turn(self):
        gate = PcmTurnGate(self.focus, clock=lambda: self.now)
        self.addCleanup(gate.close)
        self.assertFalse(gate.start_ptt("ptt:1", generation=1))
        self.assertFalse(gate.feed(self.pcm(), sequence=0, timestamp=self.now, generation=1))
        self.assertEqual(gate.poll(), ())
        self.assertEqual(gate.snapshot()["reason_code"], "disabled")
        self.assertEqual(gate.snapshot()["real_audio"], "NOT_RUN")
        self.assertFalse(gate.snapshot()["microphone_open"])
        self.assertFalse(gate.snapshot()["automatic_capture"])

    def test_wake_missing_or_unverified_never_calls_model_or_starts_capture(self):
        for model in (None, Mock(side_effect=AssertionError("Model forbidden"))):
            wake = WakeWordGate(model)
            self.assertFalse(wake.feed(self.pcm(), generation=1))
            self.assertFalse(wake.snapshot()["enabled"])
            self.assertEqual(wake.snapshot()["real_wake_word"], "NOT_RUN")
            self.assertEqual(wake.snapshot()["reason_code"],
                             "wake_model_missing" if model is None else "wake_unverified")
            if model is not None:
                model.assert_not_called()
            wake.close()

    def test_speech_then_silence_yields_one_bounded_result_and_stays_idle(self):
        gate = self.gate()
        self.assertTrue(gate.start_ptt("ptt:1", generation=1))
        self.assertTrue(all(self.feed(gate, 4)))
        self.assertEqual(gate.snapshot()["state"], "speech")
        self.feed(gate, 15, amplitude=0, begin=4)
        result, = gate.poll()
        self.assertEqual((result.status, result.code), ("ready", "silence"))
        self.assertEqual(len(result.pcm), 19 * len(self.pcm()))
        self.assertEqual(result.generation, 1)
        self.assertEqual(gate.poll(), ())
        self.assertFalse(gate.feed(self.pcm(), sequence=19, timestamp=self.now, generation=1))
        self.assertEqual(gate.snapshot()["state"], "idle")

    def test_ptt_release_completes_only_after_minimum_continuous_speech(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 3)
        self.feed(gate, 1, amplitude=0, begin=3)
        self.feed(gate, 3, begin=4)
        self.assertTrue(gate.release_ptt(generation=1))
        result, = gate.poll()
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "no_speech", b""))
        gate.start_ptt("ptt:2", generation=2)
        self.feed(gate, 4, generation=2)
        self.assertFalse(gate.release_ptt(generation=True))
        self.assertTrue(gate.release_ptt(generation=2))
        result, = gate.poll()
        self.assertEqual((result.status, result.code), ("ready", "ptt_released"))

    def test_no_speech_timeout_drops_silence_and_does_not_restart(self):
        gate = self.gate(no_speech_timeout=.2)
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 3, amplitude=0)
        self.now += .3
        result, = gate.poll()
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "no_speech", b""))
        self.assertEqual(gate.snapshot()["buffered_bytes"], 0)
        self.now += 100
        self.assertEqual(gate.poll(), ())

    def test_maximum_duration_completes_speech_without_sleep(self):
        gate = self.gate(max_duration=.5, no_speech_timeout=.3, silence_timeout=.1)
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 4)
        self.now = 20.5
        result, = gate.poll()
        self.assertEqual((result.status, result.code), ("ready", "max_duration"))
        self.assertLessEqual(len(result.pcm), gate.max_buffer_bytes)

    def test_byte_budget_bounds_even_fast_injected_frames_and_pending_count(self):
        gate = self.gate(max_duration=.5, no_speech_timeout=.3, silence_timeout=.1)
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 26, advance=False)
        result, = gate.poll()
        self.assertEqual((result.status, result.code), ("ready", "max_buffer"))
        self.assertEqual(len(result.pcm), gate.max_buffer_bytes)
        self.assertEqual(gate.poll(), ())
        self.assertEqual(gate.snapshot()["buffered_bytes"], 0)

    def test_invalid_pcm_cancels_and_drops_partial_speech(self):
        for bad in (b"", b"x", bytearray(b"xx"), b"x" * 3202):
            with self.subTest(length=len(bad)):
                gate = self.gate()
                gate.start_ptt("ptt:1", generation=1)
                self.feed(gate, 4)
                self.assertFalse(gate.feed(bad, sequence=4, timestamp=self.now, generation=1))
                result, = gate.poll()
                self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "invalid_pcm", b""))

    def test_duplicate_and_late_generation_frames_cannot_extend_recording(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 1)
        before = gate.snapshot()["buffered_bytes"]
        for sequence, generation in ((0, 1), (0, 0), (999, 0), (1, True)):
            self.assertFalse(gate.feed(self.pcm(), sequence=sequence, timestamp=self.now, generation=generation))
            self.assertEqual(gate.snapshot()["buffered_bytes"], before)
        self.assertTrue(gate.feed(self.pcm(), sequence=1, timestamp=self.now, generation=1))

    def test_stale_future_and_backward_timestamps_do_not_mutate_buffer(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.now = 20.4
        for sequence, stamp in enumerate((20., 20.5, float("nan"), 19.9)):
            self.assertFalse(gate.feed(self.pcm(), sequence=sequence, timestamp=stamp, generation=1))
            self.assertEqual(gate.snapshot()["buffered_bytes"], 0)
        self.assertTrue(gate.feed(self.pcm(), sequence=4, timestamp=20.4, generation=1))
        self.assertFalse(gate.feed(self.pcm(), sequence=5, timestamp=20.3, generation=1))
        self.assertEqual(gate.snapshot()["buffered_bytes"], len(self.pcm()))

    def test_invalid_sequence_cancels_instead_of_replaying_pcm(self):
        for sequence in (-1, True, "1"):
            with self.subTest(sequence=sequence):
                gate = self.gate()
                gate.start_ptt("ptt:1", generation=1)
                self.assertFalse(gate.feed(self.pcm(), sequence=sequence, timestamp=self.now, generation=1))
                result, = gate.poll()
                self.assertEqual(result.code, "invalid_pcm_sequence")
                self.assertEqual(result.pcm, b"")

    def test_every_audio_focus_owner_blocks_ptt_without_releasing_output(self):
        for owner in ("conversation", "music", "game"):
            with self.subTest(owner=owner):
                stop = Mock(return_value=True)
                lease = self.focus.acquire(owner, stop=stop)
                gate = self.gate()
                self.assertFalse(gate.start_ptt("ptt:1", generation=1))
                self.assertEqual(gate.snapshot()["reason_code"], "audio_focus_busy")
                self.assertEqual(self.focus.lease, lease)
                stop.assert_not_called()
                self.focus.release(lease)

    def test_output_start_mid_capture_cancels_and_erases_pcm_candidate(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 4)
        lease = self.focus.acquire("conversation")
        self.assertFalse(gate.feed(self.pcm(), sequence=4, timestamp=self.now, generation=1))
        result, = gate.poll()
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "audio_focus_busy", b""))
        self.assertEqual(gate.snapshot()["buffered_bytes"], 0)
        self.assertEqual(self.focus.lease, lease)
        self.focus.release(lease)
        self.assertFalse(gate.feed(self.pcm(), sequence=5, timestamp=self.now, generation=1))

    def test_pending_ready_result_is_invalidated_if_voice_starts_before_consume(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 4)
        gate.release_ptt(generation=1)
        self.assertTrue(gate.snapshot()["result_pending"])
        lease = self.focus.acquire("conversation")
        result, = gate.poll()
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "audio_focus_busy", b""))
        self.assertEqual(self.focus.lease, lease)

    def test_playback_probe_true_or_failure_fails_closed_even_without_focus(self):
        for probe in (lambda: True, lambda: None, Mock(side_effect=RuntimeError("private player"))):
            with self.subTest(probe=probe):
                gate = self.gate(playback_active=probe)
                self.assertFalse(gate.start_ptt("ptt:1", generation=1))
                self.assertEqual(gate.snapshot()["reason_code"], "audio_focus_busy")
                self.assertIsNone(self.focus.owner)

    def test_probe_detects_mid_capture_playback_and_clears_candidate(self):
        playing = [False]
        gate = self.gate(playback_active=lambda: playing[0])
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 4)
        playing[0] = True
        gate.tick()
        result, = gate.poll()
        self.assertEqual((result.code, result.pcm), ("audio_focus_busy", b""))

    def test_busy_duplicate_generation_and_pending_result_require_new_explicit_turn(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.assertFalse(gate.start_ptt("ptt:2", generation=2))
        self.assertEqual(gate.snapshot()["reason_code"], "capture_busy")
        gate.release_ptt(generation=1)
        self.assertFalse(gate.start_ptt("ptt:2", generation=2))
        gate.poll()
        for capture_id, generation in (("ptt:1", 2), ("ptt:2", 1)):
            self.assertFalse(gate.start_ptt(capture_id, generation=generation))
            self.assertEqual(gate.snapshot()["reason_code"], "duplicate_capture")
        self.assertTrue(gate.start_ptt("ptt:2", generation=2))

    def test_cancel_close_and_late_frames_do_not_return_audio(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 4)
        self.assertTrue(gate.cancel("user"))
        result, = gate.poll()
        self.assertEqual((result.status, result.code, result.pcm), ("cancelled", "user", b""))
        gate.start_ptt("ptt:2", generation=2)
        self.feed(gate, 4, generation=2)
        gate.close(); gate.close()
        self.assertFalse(gate.release_ptt(generation=2))
        self.assertFalse(gate.feed(self.pcm(), sequence=4, timestamp=self.now, generation=2))
        self.assertEqual(gate.poll(), ())
        self.assertEqual(gate.snapshot()["state"], "closed")
        self.assertEqual(gate.snapshot()["buffered_bytes"], 0)

    def test_capture_public_and_repr_exclude_pcm_bytes(self):
        gate = self.gate()
        gate.start_ptt("ptt:1", generation=1)
        self.feed(gate, 4)
        gate.release_ptt(generation=1)
        result, = gate.poll()
        self.assertNotIn("pcm", result.public())
        self.assertNotIn(repr(result.pcm), repr(result))
        self.assertEqual(result.public()["source"], "injected_pcm")
        self.assertEqual(result.public()["byte_count"], len(result.pcm))

    def test_bounded_capture_ledger_fails_closed_instead_of_forgetting_old_ids(self):
        gate = self.gate()
        gate._attempts.update("ptt:" + str(number) for number in range(4096))
        self.assertFalse(gate.start_ptt("ptt:new", generation=1))
        self.assertEqual(gate.snapshot()["reason_code"], "capture_capacity")
        self.assertEqual(len(gate._attempts), 4096)
        self.assertEqual(gate.poll(), ())

    def test_invalid_configuration_and_backwards_clock_fail_closed(self):
        for settings in ({"sample_rate": True}, {"threshold": float("nan")}, {"threshold": 1.1},
                         {"max_duration": 0}, {"freshness": 6}, {"enabled": 1}):
            with self.subTest(settings=settings), self.assertRaises(FeatureError):
                PcmTurnGate(self.focus, **settings)
        gate = self.gate()
        for capture_id, generation in (("bad path", 1), ("ptt:1", True), ("ptt:1", -1)):
            with self.subTest(capture_id=capture_id, generation=generation), self.assertRaises(FeatureError):
                gate.start_ptt(capture_id, generation=generation)
        gate.start_ptt("ptt:1", generation=1)
        self.now = 19
        result, = gate.poll()
        self.assertEqual((result.code, result.pcm), ("invalid_capture_clock", b""))


if __name__ == "__main__":
    unittest.main()
