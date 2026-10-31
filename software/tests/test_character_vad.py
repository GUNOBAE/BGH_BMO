"""Local VAD/reference regressions; no device, paid API or user-voice approval."""
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.input.ambient_vad import VadError, VoicingEvidence, WebRtcVoiceEvidence
from bmo_ai.features.input.microphone import MicError, PcmFrame, PttRecorder
from bmo_ai.features.input.pcm import PcmTurnGate
from bmo_ai.features.resources import AudioFocus
from bmo_ai.ptt_replay import load_public_reference_pcm16k, reference_overlay, replay_pcm


FIXTURES = Path(__file__).with_name("fixtures")


def pack(values):
    return struct.pack("<"+"h"*len(values), *values)


def reference(level=.004, stretch=1.):
    raw = (FIXTURES/"webrtcvad_reference_8khz_s16le.raw").read_bytes()
    if sha256(raw).hexdigest() != "3dbb730b90e0266d78d4fe03f00aba37a83780a6a5240d9084b5c01266194c1e":
        raise AssertionError("Public reference fixture identity changed")
    samples = [item[0] for item in struct.iter_unpack("<h", raw)]
    upsampled = []
    for index, value in enumerate(samples):
        upsampled.extend((value, (value+samples[min(index+1,len(samples)-1)])/2))
    if stretch != 1.:
        original = upsampled
        upsampled = []
        for index in range(round(len(original)*stretch)):
            position = index/stretch
            left, right = min(int(position),len(original)-1), min(int(position)+1,len(original)-1)
            upsampled.append(original[left]+(original[right]-original[left])*(position-left))
    rms = math.sqrt(sum(value*value for value in upsampled)/len(upsampled))/32768
    return [round(value*level/rms) for value in upsampled]


class VoiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("device forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))

    def test_native_40ms_splits20ms_and_tracks_exact_positive_duration(self):
        observed = []
        class Backend:
            def __init__(self, mode): self.mode=mode
            def is_speech(self, pcm, rate):
                observed.append((pcm,rate))
                return len(observed) == 1
        detector = WebRtcVoiceEvidence(_module=SimpleNamespace(Vad=Backend))
        pcm = struct.pack("<h", 100)*640
        value = detector.classify(pcm)
        self.assertEqual((value.voiced,value.subframes,value.positive_subframes,value.positive_seconds), (True,2,1,.02))
        self.assertEqual(b"".join(item[0] for item in observed),pcm)
        self.assertTrue(all(len(item[0]) == 640 and item[1] == 16000 for item in observed))
        self.assertFalse(detector.snapshot()["raw_pcm_modified"])

    def test_runtime_missing_fails_before_source_opens_and_never_rms_falls_back(self):
        factory = Mock(side_effect=AssertionError("source must remain unopened"))
        recorder = PttRecorder(AudioFocus(),factory,vad_mode="webrtc")
        self.addCleanup(recorder.close)
        with patch("bmo_ai.features.input.ambient_vad.importlib.import_module",side_effect=ModuleNotFoundError):
            with self.assertRaises(MicError) as error:
                recorder.capture("missing-vad-runtime",generation=1)
        self.assertEqual(error.exception.code,"vad_backend_missing")
        factory.assert_not_called()

    def test_25ms_remainder_cannot_credit_more_positive_time_than_current_frame(self):
        class Backend:
            def __init__(self, mode): pass
            def is_speech(self, pcm, rate): return True
        detector=WebRtcVoiceEvidence(_module=SimpleNamespace(Vad=Backend))
        first=detector.classify(bytes(800))
        second=detector.classify(bytes(800))
        self.assertEqual(first.positive_seconds,.02)
        self.assertEqual(second.subframes,2)
        self.assertEqual(second.positive_seconds,.025)
        self.assertLessEqual(first.positive_seconds+second.positive_seconds,.05)
        native=WebRtcVoiceEvidence(_module=SimpleNamespace(Vad=Backend))
        self.assertEqual(native.classify(bytes(1280)).positive_seconds,.04)

    def test_backend_error_is_explicit_instead_of_silently_becoming_silence(self):
        class Backend:
            def __init__(self, mode): pass
            def is_speech(self, pcm, rate): raise RuntimeError("private backend details")
        detector = WebRtcVoiceEvidence(_module=SimpleNamespace(Vad=Backend))
        with self.assertRaises(VadError) as error:
            detector.classify(bytes(1280))
        self.assertEqual(error.exception.code,"vad_backend_failed")
        self.assertNotIn("private",str(error.exception))

    def test_external_voicing_is_strictly_typed(self):
        clock = SimpleNamespace(now=100.)
        gate = PcmTurnGate(AudioFocus(),enabled=True,clock=lambda:clock.now,speech_window=.6)
        self.addCleanup(gate.close)
        self.assertTrue(gate.start_ptt("typed-voice",generation=1))
        clock.now += .04
        self.assertFalse(gate.feed(bytes(1280),sequence=0,timestamp=clock.now,generation=1,voiced=1))
        self.assertEqual(gate.poll()[0].code,"invalid_voicing")

    def test_sparse_interleaved_noise_cannot_accumulate_across_unbounded_time(self):
        clock = SimpleNamespace(now=100.)
        gate = PcmTurnGate(AudioFocus(),enabled=True,clock=lambda:clock.now,
            min_speech=.24,speech_window=.6,no_speech_timeout=3.,max_duration=12.)
        self.addCleanup(gate.close)
        gate.start_ptt("sparse-vad-noise",generation=1)
        pending = ()
        for sequence in range(80):
            clock.now += .04
            voiced = sequence % 3 == 0
            gate.feed(struct.pack("<h",5000)*640,sequence=sequence,timestamp=clock.now,
                      generation=1,voiced=voiced,voiced_seconds=.04 if voiced else 0.)
            pending = gate.poll()
            if pending: break
        self.assertEqual((pending[0].status,pending[0].code), ("cancelled","no_speech"))
        self.assertEqual(pending[0].pcm,b"")

    def test_window_retains_first_fragment_before_qualification_beyond_preroll(self):
        clock = SimpleNamespace(now=100.)
        flags = [False]*50 + [True]*2 + [False]*9 + [True]*4 + [False]*24
        amplitudes = [0]*50 + [8000]*2 + [0]*9 + [10000]*4 + [0]*24
        class Evidence:
            def __init__(self): self.index=0
            def classify(self, pcm):
                voiced=flags[self.index];self.index+=1
                return VoicingEvidence(voiced,2,2 if voiced else 0,.04 if voiced else 0.)
            def snapshot(self): return {"backend":"SYNTHETIC_TEST_BOUNDARY"}
            def close(self): pass
        class Source:
            sample_rate=16000
            def frames(self,capture_id,*,generation,cancel_check):
                for sequence,amplitude in enumerate(amplitudes):
                    cancel_check();clock.now += .04
                    yield PcmFrame(struct.pack("<h",amplitude)*640,sequence,clock.now,generation)
            def close(self): return True
        recorder = PttRecorder(AudioFocus(),Source,clock=lambda:clock.now,vad_mode="webrtc")
        self.addCleanup(recorder.close)
        with patch("bmo_ai.features.input.ambient_vad.WebRtcVoiceEvidence",Evidence):
            result=recorder.capture("fragmented-first-word",generation=1)
        self.assertEqual((result.status,result.code),("ready","silence"))
        self.assertEqual(result.pcm[10*1280:12*1280],struct.pack("<h",8000)*(2*640))
        self.assertEqual(result.pcm[:10*1280],bytes(10*1280))
        self.assertEqual(len(result.pcm),(10+2+9+4+23)*1280)

    def test_invalid_mode_does_not_create_a_source(self):
        factory=Mock()
        with self.assertRaises(MicError): PttRecorder(AudioFocus(),factory,vad_mode="automatic")
        factory.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("webrtcvad"),"install pinned optional requirements-vad.txt")
class RealLocalVadReferenceTests(unittest.TestCase):
    """Published reference/constructed overlays, never user-human acceptance."""
    def setUp(self):
        self.enterContext(patch("subprocess.Popen",side_effect=AssertionError("device forbidden")))
        self.enterContext(patch("socket.socket.connect",side_effect=AssertionError("network forbidden")))

    def test_public_reference_has_pinned_origin_rate_and_license(self):
        data=json.loads((FIXTURES/"webrtcvad_reference.json").read_text())
        self.assertEqual(data["sample_rate"],8000)
        self.assertEqual(data["user_human_acceptance"],"NOT_RUN")
        license_text=(FIXTURES/data["license_file"]).read_text()
        self.assertIn("The MIT License",license_text)
        self.assertIn("The WebRTC project authors",license_text)
        reference()
        self.assertEqual(load_public_reference_pcm16k(),pack(reference(.004)))

    def test_quiet_reference_below_old_threshold_preserves_initial_bytes_and_full_tail(self):
        speech=reference(.002)
        pcm=pack(speech+[0]*24000)
        result=replay_pcm(pcm,vad_mode="webrtc")
        self.assertEqual((result["capture_status"],result["capture_code"]),("ready","silence"))
        self.assertGreaterEqual(result["retained_bytes"],len(speech)*2)
        self.assertEqual(result["retained_pcm_sha256"],sha256(pcm[:result["retained_bytes"]]).hexdigest())
        self.assertEqual(result["human_speech_accuracy"],"NOT_RUN")

    def test_lower_pitch_stretched_reference_stays_detected_without_pcm_processing(self):
        speech=reference(.004,stretch=1.4)
        pcm=pack(speech+[0]*24000)
        result=replay_pcm(pcm,vad_mode="webrtc")
        self.assertEqual(result["capture_code"],"silence")
        self.assertGreaterEqual(result["retained_bytes"],len(speech)*2)
        self.assertEqual(result["retained_pcm_sha256"],sha256(pcm[:result["retained_bytes"]]).hexdigest())

    def test_480ms_hesitation_retains_both_reference_phrases(self):
        speech=reference(.004)
        both=speech+[0]*7680+speech
        pcm=pack(both+[0]*24000)
        result=replay_pcm(pcm,vad_mode="webrtc")
        self.assertEqual(result["capture_code"],"silence")
        self.assertGreaterEqual(result["retained_bytes"],len(both)*2)
        self.assertEqual(result["retained_pcm_sha256"],sha256(pcm[:result["retained_bytes"]]).hexdigest())

    def test_generic_stationary_low_frequency_noise_reproduces_rms_cap_but_not_new_entry(self):
        samples=[round(850*math.sin(2*math.pi*60*i/16000)+200*math.sin(2*math.pi*120*i/16000)) for i in range(64000)]
        pcm=pack(samples)
        old=replay_pcm(pcm,vad_mode="rms")
        new=replay_pcm(pcm,vad_mode="webrtc")
        self.assertEqual(old["capture_code"],"max_duration")
        self.assertEqual((new["capture_status"],new["capture_code"]),("cancelled","no_speech"))
        self.assertEqual(new["replay_clock_seconds"],3.)

    def test_quiet_reference_near_constructed_low_frequency_floor_is_preserved(self):
        background=pack([round(550*math.sin(2*math.pi*60*i/16000)
                              +120*math.sin(2*math.pi*120*i/16000)) for i in range(64000)])
        raw=(FIXTURES/"webrtcvad_reference_8khz_s16le.raw").read_bytes()
        pcm,label=reference_overlay(background,raw,target_rms=.004)
        result=replay_pcm(pcm,vad_mode="webrtc")
        self.assertEqual(result["capture_code"],"silence")
        self.assertGreaterEqual(result["retained_bytes"],label["reference_samples"]*2)
        self.assertEqual(result["retained_pcm_sha256"],sha256(pcm[:result["retained_bytes"]]).hexdigest())
        self.assertEqual(label["human_acceptance"],"NOT_RUN")
        self.assertEqual(label["kind"],"CONSTRUCTED_REFERENCE_OVERLAY_NOT_HUMAN_ACCEPTANCE")

    def test_cancel_discards_reference_and_never_requests_transcription(self):
        result=replay_pcm(pack(reference()+[0]*32000),vad_mode="webrtc",cancel_at=.4)
        self.assertEqual((result["capture_status"],result["capture_code"]),("cancelled","user_cancelled"))
        self.assertEqual(result["retained_bytes"],0)

    def test_long_leading_silence_is_trimmed_without_cutting_first_reference_samples(self):
        speech=reference(.004)
        pcm=pack([0]*32000+speech+[0]*24000)
        result=replay_pcm(pcm,vad_mode="webrtc")
        self.assertEqual(result["capture_code"],"silence")
        offset=result["consumed_source_frames"]*1280-result["retained_bytes"]
        self.assertLessEqual(offset,32000*2)
        self.assertGreater(offset,0)
        self.assertEqual(result["retained_pcm_sha256"],sha256(pcm[offset:offset+result["retained_bytes"]]).hexdigest())

    def test_short_saved_source_does_not_fabricate_no_speech_or_padding(self):
        result=replay_pcm(bytes(1280*10),vad_mode="webrtc")
        self.assertEqual(result["capture_status"],"INCOMPLETE_SAVED_SOURCE")
        self.assertEqual(result["capture_code"],"pcm_duration_limit")
        self.assertEqual(result["retained_bytes"],0)


if __name__ == "__main__": unittest.main()
