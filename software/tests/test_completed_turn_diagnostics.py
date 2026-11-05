"""Completed numeric timing receipts; no provider, device or original speech."""
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.completed_turn_diagnostics import CAPACITY, MAX_BYTES, CompletedTurnStore
from bmo_ai.turn_diagnostics import TurnDiagnostics


class CompletedTurnDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.directory = self.root / "timings"
        self.now = 10.
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("device/provider forbidden")))

    def store(self, directory=None, **kw):
        value = CompletedTurnStore(directory or self.directory, runtime_revision="a" * 40,
            wall_clock=lambda: 1800000000, **kw)
        self.addCleanup(value.close)
        return value

    def trace(self, sink=None, turn=1):
        value = TurnDiagnostics(enabled=True, clock=lambda: self.now, completed_sink=sink)
        value.mark_input(); value.begin(turn)
        for stage, code, elapsed in (
            ("vad", "last_voiced", .40), ("vad", "vad_closed", 1.32),
            ("capture_close", "capture_closed", 1.35), ("stt", "request_started", 1.40),
            ("stt", "ok", 1.60), ("ai", "started", 1.62), ("ai", "ok", 2.20),
            ("fish", "started", 2.25), ("fish", "audio_first_byte", 2.50),
            ("fish", "audio_downloaded", 3.), ("fish", "audio_ready", 3.05),
            ("playback", "playback_started", 3.10)):
            self.now = 10. + elapsed
            value.record(stage, code)
        return value

    def read(self):
        return json.loads((self.directory / "completed-turns.json").read_text())

    def test_completion_saves_once_then_survives_next_turn_and_reopen(self):
        store = self.store()
        trace = self.trace(store.record)
        self.assertEqual(store.snapshot()["retained"], 0)
        self.assertFalse((self.directory / "completed-turns.json").exists())
        trace.finish("ok")
        trace.finish("ok")
        trace.begin(2)
        self.assertEqual(trace.snapshot()["markers_ms"], {"turn_admitted": 0.})
        record, = self.read()["records"]
        self.assertEqual(record["runtime_revision"], "a" * 40)
        self.assertEqual(record["terminal"], "ok")
        self.assertEqual(record["timings"]["last_voiced_to_player_start_ms"]["value_ms"], 2700.)
        self.assertEqual(record["timings"]["capture_close_to_stt_ms"]["value_ms"], 50.)
        self.assertEqual(record["timings"]["stt_to_llm_ms"]["value_ms"], 20.)
        self.assertEqual(record["timings"]["llm_to_tts_ms"]["value_ms"], 50.)
        self.assertEqual(record["timings"]["audio_ready_to_player_ms"]["value_ms"], 50.)
        self.assertEqual(record["timings"]["acoustic_first_sound_ms"], dict(status="UNMEASURED", value_ms=None))
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.directory / "completed-turns.json").stat().st_mode), 0o600)
        store.close()
        self.assertEqual(self.store().snapshot()["retained"], 1)

    def test_missing_markers_stay_unmeasured_and_private_payloads_never_persist(self):
        store = self.store(vad_profile="balanced", vad_mode="webrtc")
        trace = TurnDiagnostics(enabled=True, clock=lambda: self.now)
        trace.begin(1); trace.record("stt", "authentication", outcome="error"); trace.finish("authentication")
        value = trace.snapshot()
        value.update(transcript="PRIVATE TRANSCRIPT", audio=b"PCM", path="/private/user",
                     provider={"api_key": "SECRET"})
        value["markers_ms"]["SECRET"] = 5
        value["markers_ms"]["player_started"] = float("nan")
        value["timings"]["llm_ms"] = dict(status="MEASURED", value_ms=10)
        self.assertTrue(store.record(value))
        raw = (self.directory / "completed-turns.json").read_text()
        for secret in ("PRIVATE", "SECRET", "PCM", "/private", "api_key", "provider"):
            self.assertNotIn(secret, raw)
        row, = self.read()["records"]
        self.assertEqual(row["vad_profile"], "balanced")
        self.assertEqual(row["failure"]["code"], "authentication")
        self.assertEqual(row["timings"]["llm_ms"], dict(status="UNMEASURED", value_ms=None))
        self.assertFalse(row["contains_audio"] or row["contains_transcript"])

    def test_oldest_completed_records_are_bounded_across_sessions(self):
        store = self.store()
        for number in range(1, CAPACITY + 9):
            trace = TurnDiagnostics(enabled=True, clock=lambda: self.now, completed_sink=store.record)
            trace.begin(number); trace.finish("cancelled" if number % 2 else "ok")
        self.assertEqual(store.snapshot()["retained"], CAPACITY)
        self.assertEqual(self.read()["records"][0]["turn"], 9)
        self.assertLess((self.directory / "completed-turns.json").stat().st_size, MAX_BYTES)
        self.assertEqual(list(self.directory.iterdir()), [self.directory / "completed-turns.json"])

    def test_storage_error_is_redacted_and_cannot_fail_completed_turn_or_repeat_sink(self):
        store = self.store()
        trace = self.trace(store.record)
        with patch("bmo_ai.completed_turn_diagnostics.os.link", side_effect=OSError("PRIVATE PATH")):
            trace.finish("ok")
        self.assertEqual(trace.snapshot()["terminal"], "ok")
        self.assertFalse(store.snapshot()["enabled"])
        self.assertEqual(store.snapshot()["code"], "diagnostic_write_failed")
        self.assertNotIn("PRIVATE", json.dumps(store.snapshot()))
        self.assertEqual(list(self.directory.iterdir()), [])
        sink = Mock(side_effect=RuntimeError("private observer"))
        trace = self.trace(sink)
        trace.finish("ok"); trace.finish("ok")
        sink.assert_called_once()

    def test_foreign_existing_file_symlink_checkout_and_public_directory_preserved(self):
        self.directory.mkdir(mode=0o700)
        path = self.directory / "completed-turns.json"
        path.write_text("preserve user file"); path.chmod(0o600)
        self.assertFalse(self.store().snapshot()["enabled"])
        self.assertEqual(path.read_text(), "preserve user file")
        linked = self.root / "linked"; linked.symlink_to(self.directory, target_is_directory=True)
        self.assertFalse(self.store(linked).snapshot()["enabled"])
        public = self.root / "public"; public.mkdir(); public.chmod(0o755)
        self.assertFalse(self.store(public).snapshot()["enabled"])
        checkout = self.root / "checkout"; checkout.mkdir(mode=0o700); (checkout / ".git").mkdir()
        self.assertFalse(self.store(checkout / "timings").snapshot()["enabled"])
        self.assertFalse((checkout / "timings").exists())
        path.unlink(); path.symlink_to(public / "must-not-create")
        self.assertFalse(self.store().snapshot()["enabled"])
        self.assertFalse((public / "must-not-create").exists())

    def test_replaced_file_and_changed_directory_permission_disable_only_storage(self):
        store = self.store(); trace = self.trace()
        trace.finish("ok"); self.assertTrue(store.record(trace.snapshot()))
        path = self.directory / "completed-turns.json"
        path.unlink(); path.write_text("replacement"); path.chmod(0o600)
        self.assertFalse(store.record(trace.snapshot()))
        self.assertEqual(path.read_text(), "replacement")
        other = self.store(self.root / "other")
        (self.root / "other").chmod(0o755)
        self.assertFalse(other.record(trace.snapshot()))
        self.assertEqual(other.snapshot()["code"], "diagnostic_write_failed")

    def test_atomic_update_preserves_previous_records_on_publish_error(self):
        store = self.store(); trace = self.trace()
        trace.finish("ok"); self.assertTrue(store.record(trace.snapshot()))
        original = (self.directory / "completed-turns.json").read_bytes()
        with patch("bmo_ai.completed_turn_diagnostics.os.replace", side_effect=OSError("private")):
            self.assertFalse(store.record(trace.snapshot()))
        self.assertEqual((self.directory / "completed-turns.json").read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.directory / "completed-turns.json"])

    def test_disabled_diagnostics_never_write_and_incomplete_trace_is_rejected(self):
        store = self.store()
        trace = TurnDiagnostics(enabled=False, clock=lambda: self.now, completed_sink=store.record)
        trace.begin(1); trace.finish("ok")
        self.assertFalse((self.directory / "completed-turns.json").exists())
        trace = TurnDiagnostics(enabled=True, clock=lambda: self.now); trace.begin(1)
        self.assertFalse(store.record(trace.snapshot()))
        self.assertEqual(store.snapshot()["retained"], 0)

    def test_actual_host_completion_persists_then_next_ptt_turn_adds_one_receipt(self):
        from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient
        from bmo_ai.features.runtime import RuntimePolicy
        from bmo_ai.integration_profile import integration_config
        from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
        from test_ptt_session_integration import Clock, ControlledSource
        clock, client, config = Clock(), FeatureSimulatedClient(), integration_config()
        voice = IntegrationVoice(config, client, simulate=False,
            fish_synthesizer=lambda *_a, **_kw: synthetic_wav(),
            player=lambda *_a, **kw: kw["on_start"](), emit=lambda *_a, **_kw: None)
        store = self.store()
        host = FeatureHost(config=config, client=client, voice=voice, clock=clock,
            runtime=RuntimePolicy(simulate=False, conversation_ptt_microphone=True),
            ptt_source_factory=lambda: ControlledSource(clock, [(10000, 8), (0, 24)]),
            ptt_transcriber=lambda *_a, **_kw: "PRIVATE QUESTION", ptt_trace=True,
            ptt_speech_safe=True, completed_turn_store=store)
        self.addCleanup(host.close)
        for number in (1, 2):
            clock.now += .3
            self.assertTrue(host.ptt_button())
            deadline = time.monotonic() + 3
            while host.session.busy and time.monotonic() < deadline:
                host.heartbeat(); host.poll(); time.sleep(.002)
            host.poll()
            self.assertFalse(host.session.busy)
            self.assertEqual(store.snapshot()["retained"], number)
            row = self.read()["records"][-1]
            self.assertEqual((row["turn"], row["terminal"]), (number, "ok"))
            self.assertEqual(row["timings"]["last_voiced_to_player_start_ms"]["status"], "MEASURED")
            self.assertEqual(row["timings"]["tts_to_first_byte_ms"]["status"], "UNMEASURED")
        self.assertNotIn("PRIVATE QUESTION", (self.directory / "completed-turns.json").read_text())
        self.assertEqual(len(client.requests), 2)


if __name__ == "__main__":
    unittest.main()
