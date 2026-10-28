"""Actual recorder/transport code with synthetic PCM and a fake HTTP boundary."""
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai import audio, fish_tts
from bmo_ai.conversation import TurnCancelled
from bmo_ai.features.input.microphone import MicError, PcmFrame, PttRecorder, ptt_vad_settings
from bmo_ai.features.resources import AudioFocus
from bmo_ai.turn_diagnostics import TurnDiagnostics


class Clock:
    def __init__(self): self.now = 10.
    def __call__(self): return self.now


class Source:
    sample_rate = 16000
    def __init__(self, clock, patterns, *, on_frame=None):
        self.clock, self.patterns, self.on_frame = clock, patterns, on_frame
        self.observer, self.closed = None, 0
    def set_timing_observer(self, observer): self.observer = observer
    def frames(self, capture_id, *, generation, cancel_check):
        if self.observer:
            self.observer("microphone", "mic_ready", at=self.clock())
        seq = 0
        for amplitude, count in self.patterns:
            for _ in range(count):
                cancel_check(); self.clock.now += .04
                frame = PcmFrame(struct.pack("<h", amplitude) * 640, seq, self.clock(), generation)
                if self.on_frame: self.on_frame(seq)
                yield frame; seq += 1
    def close(self):
        if not self.closed: self.clock.now += .03
        self.closed += 1
        return True


class CharacterPttTimingTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.focus = Clock(), AudioFocus()
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("device forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("API forbidden")))

    def recorder(self, patterns, **settings):
        self.trace = TurnDiagnostics(enabled=True, clock=self.clock)
        self.trace.mark_input(); self.clock.now += .02; self.trace.begin(1)
        self.source = Source(self.clock, patterns)
        recorder = PttRecorder(self.focus, lambda: self.source, clock=self.clock,
            on_timing=self.trace.record, threshold=.008, speech_safe=True, **settings)
        self.addCleanup(recorder.close)
        return recorder

    def test_known_threshold_starts_without_unused_calibration_wait_and_keeps_first_syllable(self):
        states = []
        recorder = self.recorder([(10000, 8), (0, 24)], on_state=lambda s: states.append((s["state"], self.clock())))
        began = self.clock()
        capture = recorder.capture("first_syllable", generation=1)
        self.assertEqual((capture.status, capture.code), ("ready", "silence"))
        self.assertEqual(capture.pcm[:1280], struct.pack("<h", 10000) * 640)
        speech_at = next(at for state, at in states if state == "speech")
        self.assertAlmostEqual(speech_at - began, .24, places=6)
        self.assertEqual(recorder.snapshot()["calibration_seconds"], .4)
        self.assertEqual(recorder.snapshot()["effective_calibration_seconds"], 0.)
        self.assertEqual(recorder.snapshot()["silence_seconds"], .9)

    def test_advancing_monotonic_clock_accepts_actual_first_frame_without_retimestamp(self):
        class AdvancingClock(Clock):
            def __call__(self):
                self.now += .0001
                return self.now
        self.clock = AdvancingClock()
        recorder = self.recorder([(10000, 8), (0, 24)])
        capture = recorder.capture("advancing_first_frame", generation=1)
        self.assertEqual((capture.status, capture.code), ("ready", "silence"))
        self.assertEqual(capture.pcm[:1280], struct.pack("<h", 10000) * 640)
        markers = self.trace.snapshot()["markers_ms"]
        self.assertEqual(markers["speech_start"], markers["first_pcm_frame"])

    def test_trace_retains_host_receipt_and_distinct_source_frame_vad_close_observations(self):
        recorder = self.recorder([(10000, 8), (0, 24)])
        recorder.capture("timed", generation=1)
        trace = self.trace.snapshot()
        markers = trace["markers_ms"]
        self.assertEqual(markers["host_input_received"], 0.)
        self.assertAlmostEqual(markers["turn_admitted"], 20.)
        self.assertAlmostEqual(markers["microphone_ready"], 20.)
        self.assertAlmostEqual(markers["first_pcm_frame"], 60.)
        self.assertAlmostEqual(markers["speech_start"], 60.)
        self.assertAlmostEqual(trace["timings"]["last_voiced_to_vad_close_ms"]["value_ms"], 920.)
        self.assertAlmostEqual(trace["timings"]["vad_to_capture_close_ms"]["value_ms"], 30.)
        self.assertEqual(trace["timings"]["acoustic_first_sound_ms"], {"status": "UNMEASURED", "value_ms": None})
        self.assertEqual(trace["timings"]["tts_to_first_byte_ms"]["status"], "UNMEASURED")
        self.assertFalse(trace["acoustic_measurement"])
        self.assertNotIn('"pcm":', json.dumps(trace))
        self.assertFalse(trace["contains_audio"] or trace["contains_transcript"])

    def test_long_leading_silence_upload_is_trimmed_to_bounded_preroll_without_losing_tail(self):
        recorder = self.recorder([(0, 50), (10000, 8), (0, 24)])
        capture = recorder.capture("trimmed", generation=1)
        self.assertEqual(capture.status, "ready")
        self.assertEqual(len(capture.pcm), (10 + 8 + 23) * 1280)
        self.assertEqual(capture.pcm[:10 * 1280], bytes(10 * 1280))
        self.assertEqual(capture.pcm[10 * 1280:18 * 1280], struct.pack("<h", 10000) * (8 * 640))
        self.assertEqual(capture.pcm[-23 * 1280:], bytes(23 * 1280))
        event = next(e for e in self.trace.snapshot()["events"] if e["code"] == "capture_ready")
        self.assertEqual(event["metrics"]["trimmed_leading_bytes"], 40 * 1280)

    def test_short_hesitation_and_final_syllable_stay_in_one_capture(self):
        recorder = self.recorder([(10000, 8), (0, 12), (12000, 8), (0, 24)])
        capture = recorder.capture("hesitation", generation=1)
        self.assertEqual(capture.code, "silence")
        self.assertEqual(capture.pcm[8 * 1280:20 * 1280], bytes(12 * 1280))
        self.assertEqual(capture.pcm[20 * 1280:28 * 1280], struct.pack("<h", 12000) * (8 * 640))
        self.assertEqual(len(capture.pcm), (8 + 12 + 8 + 23) * 1280)
        self.assertLess(self.clock() - 10., 3.)  # No extra no-speech wait after a detected phrase.

    def test_short_initial_word_before_qualifying_phrase_is_never_trimmed_away(self):
        recorder = self.recorder([(9000, 3), (0, 15), (12000, 8), (0, 24)])
        capture = recorder.capture("early_hesitation", generation=1)
        self.assertEqual(capture.status, "ready")
        self.assertEqual(capture.pcm[:3 * 1280], struct.pack("<h", 9000) * (3 * 640))
        self.assertEqual(capture.pcm[18 * 1280:26 * 1280], struct.pack("<h", 12000) * (8 * 640))
        self.assertEqual(len(capture.pcm), (3 + 15 + 8 + 23) * 1280)

    def test_explicit_profiles_change_only_silence_and_do_not_mutate_baseline(self):
        self.assertEqual(ptt_vad_settings(), {"silence_timeout": .9})
        self.assertEqual(ptt_vad_settings("balanced"), {"silence_timeout": .6})
        self.assertEqual(ptt_vad_settings("quick"), {"silence_timeout": .45})
        changed = ptt_vad_settings(); changed["silence_timeout"] = .01
        self.assertEqual(ptt_vad_settings()["silence_timeout"], .9)
        with self.assertRaises(MicError): ptt_vad_settings("automatic")

    def test_candidate_profile_keeps_pause_shorter_than_its_own_tail_budget(self):
        recorder = self.recorder([(10000, 8), (0, 10), (12000, 8), (0, 20)], **ptt_vad_settings("balanced"))
        capture = recorder.capture("candidate", generation=1)
        self.assertEqual(capture.code, "silence")
        self.assertIn(struct.pack("<h", 12000) * 640, capture.pcm)
        self.assertEqual(len(capture.pcm), (8 + 10 + 8 + 15) * 1280)

    def test_second_press_stops_capture_without_cutting_retained_validated_word(self):
        recorder = self.recorder([(10000, 80)])
        recorder.on_state = lambda s: recorder.finish() if s["state"] == "speech" else None
        capture = recorder.capture("manual_finish", generation=1)
        self.assertEqual(capture.code, "ptt_released")
        self.assertEqual(capture.pcm, struct.pack("<h", 10000) * (6 * 640))
        self.assertEqual(self.source.closed, 1)

    def test_cancel_discards_all_pcm_and_never_admits_late_transcription(self):
        recorder = self.recorder([(10000, 80)])
        recorder.on_state = lambda s: recorder.cancel() if s["state"] == "speech" else None
        capture = recorder.capture("cancelled_word", generation=1)
        self.assertEqual((capture.status, capture.pcm), ("cancelled", b""))
        transcribe = Mock(side_effect=AssertionError("late STT forbidden"))
        with self.assertRaises(TurnCancelled): recorder.transcribe_capture(capture, None, transcriber=transcribe)
        transcribe.assert_not_called()

    def test_stt_markers_wrap_actual_handoff_and_observers_never_receive_transcript(self):
        recorder = self.recorder([(10000, 8), (0, 24)])
        capture = recorder.capture("one_stt", generation=1)
        def transcribe(*_a, **_kw): self.clock.now += .12; return "PRIVATE_WORDS"
        self.assertEqual(recorder.transcribe_capture(capture, object(), transcriber=transcribe), "PRIVATE_WORDS")
        trace = self.trace.snapshot()
        self.assertEqual(trace["timings"]["stt_ms"], {"status": "MEASURED", "value_ms": 120.})
        self.assertNotIn("PRIVATE", json.dumps(trace))
        with self.assertRaises(MicError): recorder.transcribe_capture(capture, object(), transcriber=transcribe)

    def test_missing_source_ready_hook_does_not_fabricate_mic_ready(self):
        recorder = self.recorder([(10000, 8), (0, 24)])
        self.source.set_timing_observer = None
        recorder.capture("unknown_ready", generation=1)
        trace = self.trace.snapshot()
        self.assertIn("first_pcm_frame", trace["markers_ms"])
        self.assertNotIn("microphone_ready", trace["markers_ms"])
        self.assertEqual(trace["timings"]["host_to_mic_ready_ms"]["status"], "UNMEASURED")

    def test_marker_clock_validation_and_stale_turn_cannot_fabricate_latency(self):
        trace = TurnDiagnostics(enabled=True, clock=self.clock)
        trace.mark_input(); trace.begin(3)
        trace.record("fish", "started")
        trace.record("fish", "audio_first_byte", at=float("nan"))
        trace.record("fish", "audio_first_byte", at=self.clock() + 2)
        trace.record("fish", "audio_first_byte", turn=2)
        self.assertEqual(trace.snapshot()["timings"]["tts_to_first_byte_ms"]["status"], "UNMEASURED")
        self.clock.now += .12; trace.record("fish", "audio_first_byte")
        self.assertEqual(trace.snapshot()["timings"]["tts_to_first_byte_ms"]["value_ms"], 120.)
        for _ in range(200): trace.record("vad", "last_voiced")
        self.assertLess(len(trace.snapshot()["events"]), 10)


class CharacterFishTimingTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.events = Clock(), []
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(bytes(3200))
        self.wav = buf.getvalue()
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("paid network forbidden")))

    def synth(self, *, observer=None, empty=False):
        owner = self
        class Response:
            index = 0
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, _count):
                owner.clock.now += .1; self.index += 1
                if empty: return b""
                return (owner.wav[:100], owner.wav[100:], b"")[min(self.index - 1, 2)]
        with patch.object(fish_tts, "urlopen", return_value=Response()):
            return fish_tts.synthesize_wav("Synthetic text", api_key="SYNTHETIC_TEST_KEY",
                on_timing=observer or (lambda stage, code, **fields: self.events.append((stage, code, fields))), clock=self.clock)

    def test_first_nonempty_chunk_download_and_validation_are_distinct_observations(self):
        path = self.synth()
        self.addCleanup(lambda: audio._discard(path, suppress=True))
        self.assertEqual([code for _, code, _ in self.events], ["audio_first_byte", "audio_downloaded", "audio_validated"])
        self.assertAlmostEqual(self.events[0][2]["at"], 10.1)
        self.assertAlmostEqual(self.events[1][2]["at"], 10.3)
        self.assertTrue(all(set(fields) == {"at"} for _, _, fields in self.events))
        self.assertNotIn("SYNTHETIC", json.dumps(self.events))

    def test_observer_failure_cannot_repeat_or_break_transport(self):
        callback = Mock(side_effect=RuntimeError("PRIVATE OBSERVER"))
        path = self.synth(observer=callback)
        self.addCleanup(lambda: audio._discard(path, suppress=True))
        self.assertTrue(Path(path).is_file()); self.assertEqual(callback.call_count, 3)

    def test_empty_body_never_claims_first_byte_or_validated_audio(self):
        with self.assertRaises(audio.AudioError): self.synth(empty=True)
        self.assertEqual([code for _, code, _ in self.events], ["audio_downloaded"])


if __name__ == "__main__": unittest.main()
