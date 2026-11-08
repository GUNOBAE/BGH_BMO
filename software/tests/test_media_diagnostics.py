"""Music evidence is bounded, private, and independent of playback success."""
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmo_ai.media_diagnostics import MediaDiagnostics, CAPACITY, MAX_BYTES


REVISION = "a" * 40
RUN = "b" * 32


class MediaDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="media-evidence-")))
        self.directory = self.root / "private"
        self.time = 10.
        for name in ("socket.socket.connect", "subprocess.Popen"):
            self.enterContext(patch(name, side_effect=AssertionError("physical/network IO forbidden")))

    def store(self, **kwargs):
        return MediaDiagnostics(clock=lambda: self.time, runtime_revision=REVISION,
                                directory=self.directory, **kwargs)

    def media(self, **kwargs):
        return dict(active_id="space_potatoes", variant="video", state="playing", position_seconds=12.25,
            duration_seconds=87.5, request_id="web_fixture_1", mode_generation=4, playback_generation=7, **kwargs)

    def test_default_and_unbound_opt_in_never_access_user_store(self):
        with patch("bmo_ai.media_diagnostics._open_directory", side_effect=AssertionError("unexpected user store IO")):
            store = self.store()
            store.record("start", "host_control", self.media())
            store.bind_runtime(RUN)
            store.record("pause", "gp8_short", self.media())
            pending = self.store(persist=True)
            pending.record("lifecycle", "hidden", lifecycle=dict(hidden=True))
            self.assertFalse(pending.bind_runtime("not-a-run-id"))
        self.assertEqual(store.snapshot()["store_code"], "memory_only")
        self.assertEqual(pending.snapshot()["store_code"], "awaiting_runtime")
        self.assertFalse(self.directory.exists())

    def test_start_pause_resume_failure_keep_identity_cause_and_original_error(self):
        store = self.store(run_id=RUN)
        store.record("start", "voice_command", self.media())
        self.time += 2
        pause = store.record("pause", "gp8_short", dict(self.media(), state="paused"),
            dict(hidden=False, focused=True, connected=True, heartbeat_age_seconds=.1))
        self.time += 1
        store.record("resume", "touch_resume", self.media())
        terminal = dict(catalog_id="space_potatoes", variant="video", position_seconds=14.5,
            duration_seconds=87.5, outcome="failed", request_id="web_fixture_1", mode_generation=4,
            playback_generation=7, error_code="media_output_underrun", cleanup_error="cleanup_failed",
            error_frame=116000, error_submitted_frame=116160, error_queued_frames=160,
            error_rate=8000, error_at_monotonic=12.9)
        failed = store.record("failed", "media_backend", terminal,
                              dict(hidden=True, focused=False, heartbeat_age_seconds=3.25))
        self.assertEqual((pause["source"], pause["state"]), ("gp8_short", "paused"))
        self.assertEqual((failed["runtime_revision"], failed["run_id"]), (REVISION, RUN))
        self.assertEqual((failed["catalog_id"], failed["variant"], failed["request_id"]),
                         ("space_potatoes", "video", "web_fixture_1"))
        self.assertEqual((failed["state"], failed["position_seconds"], failed["duration_seconds"]),
                         ("failed", 14.5, 87.5))
        self.assertEqual((failed["error_code"], failed["cleanup_error"]),
                         ("media_output_underrun", "cleanup_failed"))
        self.assertEqual(failed["lifecycle"]["heartbeat_age_seconds"], 3.25)
        self.assertEqual((failed['error_frame'], failed['error_at_monotonic']), (116000, 12.9))
        self.assertEqual([row["sequence"] for row in store.snapshot()["events"]], [1, 2, 3, 4])

    def test_redaction_and_defensive_snapshots_retain_only_bounded_fields(self):
        store = self.store(run_id=RUN)
        secret = "Bearer private token /home/bgh/private.wav transcript"
        received = store.record("failed", "video_error", dict(self.media(), title=secret, path=secret,
            error_code=secret, cleanup_error=[secret]), dict(hidden=True, exception=secret))
        raw = json.dumps(store.snapshot())
        self.assertNotIn(secret, raw)
        self.assertEqual(received["error_code"], "unknown_error_code")
        self.assertEqual(received["cleanup_error"], "unknown_error_code")
        received["catalog_id"] = "altered"
        snapshot = store.snapshot()
        snapshot["events"].clear()
        self.assertEqual(store.snapshot()["latest"]["catalog_id"], "space_potatoes")
        self.assertIsNone(store.record("not_an_event", "host_control", self.media()))
        self.assertIsNone(store.record("pause", secret, self.media()))
        invalid = store.record("seek", "touch_seek", dict(self.media(), position_seconds=float("nan"),
            duration_seconds=float("inf"), request_id=secret, active_id=secret, playback_generation=True))
        self.assertIsNone(invalid["position_seconds"])
        self.assertIsNone(invalid["duration_seconds"])
        self.assertIsNone(invalid["request_id"])
        self.assertIsNone(invalid["catalog_id"])
        self.assertIsNone(invalid["playback_generation"])

    def test_private_persistence_rotates_and_keeps_prior_run_identity(self):
        store = self.store(persist=True)
        self.assertTrue(store.bind_runtime(RUN))
        self.assertFalse(self.directory.exists())  # Binding missing path remains read-only.
        for n in range(CAPACITY+7):
            self.time += 1
            store.record("lifecycle", "hidden", self.media(), dict(hidden=True, focused=False))
        target = self.directory / "events.json"
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertLessEqual(target.stat().st_size, MAX_BYTES)
        self.assertEqual(len(json.loads(target.read_text())["events"]), CAPACITY)
        next_run = "c" * 32
        restarted = self.store(run_id=next_run, persist=True)
        self.assertEqual(restarted.snapshot()["latest"]["run_id"], RUN)
        event = restarted.record("start", "host_control", self.media())
        self.assertEqual(event["run_id"], next_run)
        self.assertEqual(event["sequence"], CAPACITY+8)
        self.assertFalse(restarted.bind_runtime("d" * 32))
        self.assertEqual(restarted.snapshot()["retained"], CAPACITY)
        self.assertEqual(restarted.snapshot()["store_code"], "saved")

    def test_xdg_state_path_and_no_checkout_or_symlink_writes(self):
        xdg = self.root / "state"
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
            store = MediaDiagnostics(runtime_revision=REVISION, run_id=RUN, persist=True)
            store.record("completed", "media_backend", self.media())
        self.assertTrue((xdg / "bmo/media-diagnostics/events.json").is_file())
        checkout = self.root / "checkout"
        checkout.mkdir(mode=0o700)
        (checkout / ".git").write_text("gitdir: example")
        rejected = MediaDiagnostics(directory=checkout / "trace", run_id=RUN, persist=True)
        rejected.record("stop", "app_close", self.media())
        self.assertFalse((checkout / "trace").exists())
        self.assertEqual(rejected.snapshot()["store_code"], "media_diagnostic_storage_unavailable")
        link = self.root / "link"
        link.symlink_to(xdg, target_is_directory=True)
        rejected = MediaDiagnostics(directory=link / "trace", run_id=RUN, persist=True)
        rejected.record("stop", "app_close", self.media())
        self.assertFalse((xdg / "trace").exists())

    def test_existing_unsafe_or_swapped_file_is_not_overwritten_and_error_survives(self):
        store = self.store(run_id=RUN, persist=True)
        store.record("start", "host_control", self.media())
        target = self.directory / "events.json"
        outside = self.root / "outside.json"
        outside.write_text("preserved")
        target.unlink()
        target.symlink_to(outside)
        event = store.record("failed", "media_backend", self.media(), error_code="media_output_stalled")
        self.assertEqual(outside.read_text(), "preserved")
        self.assertEqual(event["error_code"], "media_output_stalled")
        self.assertEqual(store.snapshot()["latest"]["error_code"], "media_output_stalled")
        self.assertEqual(store.snapshot()["store_code"], "media_diagnostic_write_failed")
        self.assertFalse(store.snapshot()["persistent"])

    def test_oversized_or_unknown_schema_does_not_load_and_clock_failure_is_optional(self):
        self.directory.mkdir(mode=0o700)
        target = self.directory / "events.json"
        target.write_bytes(b" " * (MAX_BYTES+1))
        target.chmod(0o600)
        store = self.store(run_id=RUN, persist=True)
        self.assertEqual(store.snapshot()["store_code"], "media_diagnostic_storage_unavailable")
        self.assertIsNotNone(store.record("failed", "media_backend", self.media(), error_code="media_output_failed"))
        self.assertEqual(target.stat().st_size, MAX_BYTES+1)
        self.assertIsNone(MediaDiagnostics(clock=lambda: 1/0).record("pause", "gp8_short"))


if __name__ == "__main__":
    unittest.main()
