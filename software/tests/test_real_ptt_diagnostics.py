"""Milestone input regressions: synthetic PCM + fake providers, zero devices/API."""
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio
from bmo_ai.cloud import CloudError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient
from bmo_ai.features.input.microphone import MicError, PttRecorder
from bmo_ai.features.resources import AudioFocus
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.ptt_diagnostics import load_ambient_profile, measure_ambient, save_ambient_profile
from bmo_ai.turn_diagnostics import TurnDiagnostics
from test_ptt_session_integration import Clock, ControlledSource


class RealPttDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("device forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("API forbidden")))
        self.enterContext(patch("bmo_ai.private_credentials.resolve_credentials", side_effect=AssertionError("keys forbidden")))

    def recorder(self, patterns, **kwargs):
        clock = Clock()
        source = ControlledSource(clock, patterns)
        recorder = PttRecorder(AudioFocus(), lambda: source, clock=clock, **kwargs)
        self.addCleanup(recorder.close)
        return source, recorder

    def test_immediate_short_word_is_not_calibrated_away(self):
        _, recorder = self.recorder([(10000, 8), (0, 24)], speech_safe=True)
        capture = recorder.capture("early:1", generation=1)
        self.assertEqual((capture.status, capture.code), ("ready", "silence"))
        self.assertEqual(capture.pcm[:1280], (10000).to_bytes(2, "little", signed=True) * 640)
        self.assertTrue(recorder.snapshot()["first_frame_received"])

    def test_low_amplitude_speech_uses_product_threshold(self):
        _, recorder = self.recorder([(0, 10), (500, 8), (0, 24)], speech_safe=True, threshold=.008)
        self.assertEqual(recorder.capture("quiet:1", generation=1).status, "ready")

    def test_verified_ambient_rejects_steady_fan_without_stt(self):
        _, recorder = self.recorder([(1800, 100)], speech_safe=True, threshold=.008,
                                   ambient_rms=1800 / 32768)
        capture = recorder.capture("fan:1", generation=1)
        self.assertEqual((capture.status, capture.code), ("cancelled", "no_speech"))
        self.assertEqual(capture.pcm, b"")
        self.assertTrue(recorder.snapshot()["ambient_verified"])

    def test_bad_source_format_does_not_claim_real_pcm_open(self):
        clock = Clock()
        source = ControlledSource(clock, [(0, 10)])
        source.sample_rate = 8000
        recorder = PttRecorder(AudioFocus(), lambda: source, clock=clock, speech_safe=True)
        self.addCleanup(recorder.close)
        with self.assertRaises(MicError) as caught:
            recorder.capture("format:1", generation=1)
        self.assertEqual(caught.exception.code, "pcm_audio_format_failed")
        self.assertFalse(recorder.snapshot()["first_frame_received"])
        self.assertEqual(source.closed, 1)

    def host(self, *, stage=None):
        clock = Clock()
        client = FeatureSimulatedClient()
        if stage == "ai":
            client.create = Mock(side_effect=CloudError("timeout", "PRIVATE_PROVIDER_TEXT"))
        calls = dict(stt=0, fish=0, playback=0)
        def stt(*_a, **_kw):
            calls["stt"] += 1
            if stage == "stt":
                raise CloudError("authentication", "PRIVATE_PROVIDER_TEXT")
            return "" if stage == "empty" else "PRIVATE_SYNTHETIC_TRANSCRIPT"
        def synth(*_a, **_kw):
            calls["fish"] += 1
            if stage == "fish":
                raise audio.AudioSynthesisError("PRIVATE_PROVIDER_TEXT", "fish_balance")
            return synthetic_wav()
        def player(*_a, **kw):
            if stage == "playback":
                raise audio.AudioPlaybackError("PRIVATE_DEVICE_TEXT", "device_unavailable")
            calls["playback"] += 1
            kw["on_start"]()
        voice = IntegrationVoice(integration_config(), client, simulate=False,
            fish_synthesizer=synth, player=player, emit=lambda *_a, **_kw: None)
        host = FeatureHost(client=client, voice=voice, clock=clock,
            runtime=RuntimePolicy(simulate=False, conversation_ptt_microphone=True),
            ptt_source_factory=lambda: ControlledSource(clock, [(10000, 8), (0, 24)]),
            ptt_transcriber=stt, ptt_trace=True, ptt_speech_safe=True)
        self.addCleanup(host.close)
        return host, calls, client

    def turn(self, host):
        self.assertTrue(host.ptt_button())
        deadline = time.monotonic() + 4
        while host.session.busy and time.monotonic() < deadline:
            host.heartbeat(); host.poll(); time.sleep(.002)
        self.assertFalse(host.session.busy)
        host.poll()
        return host.snapshot()["turn_diagnostics"]

    def test_real_pipeline_calls_one_stt_ai_fish_player_and_safe_stages(self):
        host, calls, client = self.host()
        trace = self.turn(host)
        self.assertEqual(calls, {"stt": 1, "fish": 1, "playback": 1})
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(trace["terminal"], "ok")
        self.assertIsNone(trace["failure"])
        stages = {e["stage"] for e in trace["events"]}
        self.assertTrue({"microphone", "vad", "stt", "ai", "fish", "playback"} <= stages)
        self.assertNotIn("PRIVATE", json.dumps(trace))

    def test_each_failure_identifies_its_actual_stage_without_raw_detail(self):
        for stage, code in (("stt", "authentication"), ("ai", "timeout"),
                            ("fish", "fish_balance"), ("playback", "device_unavailable")):
            with self.subTest(stage=stage):
                host, calls, client = self.host(stage=stage)
                trace = self.turn(host)
                self.assertEqual(trace["failure"]["stage"], stage)
                self.assertEqual(trace["failure"]["code"], code)
                self.assertNotIn("PRIVATE", json.dumps(trace))
                if stage == "stt":
                    self.assertEqual((len(client.requests), calls["fish"]), (0, 0))

    def test_empty_stt_has_specific_code_and_zero_ai_fish(self):
        host, calls, client = self.host(stage="empty")
        trace = self.turn(host)
        self.assertEqual(trace["failure"], dict(stage="stt", code="stt_empty", outcome="empty"))
        self.assertEqual((len(client.requests), calls["fish"]), (0, 0))

    def test_microphone_failure_after_success_never_reuses_previous_stt_stage(self):
        host, calls, _ = self.host()
        self.assertEqual(self.turn(host)["terminal"], "ok")
        host.clock.now += .3
        host.microphone.source_factory = Mock(side_effect=MicError("microphone_tool_unavailable"))
        host.microphone.on_state = None  # Provider still preserves the boundary.
        trace = self.turn(host)
        self.assertEqual(trace["failure"], dict(stage="microphone", code="microphone_tool_unavailable", outcome="error"))
        self.assertEqual(calls["stt"], 1)

    def test_trace_is_bounded_and_rejects_text_unknown_codes_and_stale_turn(self):
        trace = TurnDiagnostics(enabled=True, capacity=8)
        trace.begin(2)
        for i in range(40):
            trace.record("vad", "PRIVATE_EXCEPTION", transcript="PRIVATE", pcm=b"PRIVATE",
                         rms_max=float("nan"), captured_bytes=999999999, first_frame_received=True)
        trace.record("ai", "timeout", outcome="error", turn=1)
        value = trace.snapshot()
        self.assertEqual(len(value["events"]), 8)
        self.assertIsNone(value["failure"])
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertEqual(value["events"][-1]["metrics"], {"first_frame_received": True})

    def test_trace_disabled_preserves_only_actionable_summary(self):
        trace = TurnDiagnostics()
        trace.begin(1)
        trace.record("stt", "authentication", outcome="error", rms_min=.01)
        self.assertEqual(trace.snapshot()["events"], [])
        self.assertEqual(trace.snapshot()["failure"]["stage"], "stt")

    def test_calibration_saves_private_stats_no_audio_and_never_overwrites(self):
        clock = Clock()
        source = ControlledSource(clock, [(500, 20)])
        value = measure_ambient(source, device="FAKE_DEVICE")
        self.assertEqual(source.closed, 1)
        self.assertNotIn("pcm", value)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ambient.json"
            save_ambient_profile(path, value)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertAlmostEqual(load_ambient_profile(path, "FAKE_DEVICE"), 500 / 32768, places=7)
            with self.assertRaises(FileExistsError):
                save_ambient_profile(path, value)
            with self.assertRaises(MicError):
                load_ambient_profile(path, "OTHER_DEVICE")
            path.chmod(0o644)
            with self.assertRaises(MicError):
                load_ambient_profile(path, "FAKE_DEVICE")


if __name__ == "__main__":
    unittest.main()
