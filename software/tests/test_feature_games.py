"""Game admission, logical input and owned harmless-process cleanup only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import CatalogItem, FeatureError
from bmo_ai.features.games import GamesFeature, FakeGameRunner, OwnedProcessRunner


class GamesTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("External app forbidden")))

    def feature(self, **settings):
        feature = GamesFeature(clock=lambda: self.now, **settings)
        self.addCleanup(feature.close)
        return feature

    def start(self, feature, catalog_id="fixture_game_a", request_id="game:1", generation=1):
        return feature.start(catalog_id, request_id=request_id, mode_generation=generation)

    def test_empty_and_pending_catalogue_do_not_claim_installed_games(self):
        feature = self.feature()
        self.assertEqual(feature.catalog(), ())
        self.assertEqual(feature.snapshot()["readiness"], "ASSET_PENDING")
        self.assertEqual(feature.menu_input("A")["code"], "game_pack_missing")
        pending = self.feature(catalog_items=(CatalogItem("missing_game", "Unavailable pack"),))
        self.assertEqual(pending.menu_input("A")["action"], "select")
        self.assertEqual(self.start(pending, "missing_game").code, "asset_pending")
        self.assertIsNone(pending.snapshot()["active_id"])

    def test_fake_game_menu_other_game_face_roundtrip_and_honest_results(self):
        feature = self.feature(fixture=True)
        self.assertEqual(len(feature.catalog()), 2)
        selection = feature.menu_input("A")
        self.assertEqual(selection["action"], "launch")
        result = self.start(feature, selection["catalog_id"])
        self.assertTrue(result.simulated and result.completed)
        self.assertFalse(result.executed)
        self.assertEqual(result.completion, "simulated")
        feature.stop("game_exit")
        feature.menu_input("DOWN")
        selected = feature.menu_input("A")["catalog_id"]
        self.assertEqual(selected, "fixture_game_b")
        self.assertEqual(self.start(feature, selected, "game:2", 2).status, "simulated")
        feature.stop("game_exit")
        self.assertEqual(feature.menu_input("B")["action"], "return_to_face")
        self.assertIsNone(feature.snapshot()["active_id"])
        events = feature.poll()
        self.assertEqual([e.mode_generation for e in events if e.kind == "game_ended"], [1, 2])

    def test_duplicate_conflict_and_busy_requests_never_create_more_runners(self):
        factory = Mock(side_effect=lambda item: FakeGameRunner())
        feature = self.feature(fixture=True, runner_factory=factory)
        self.start(feature)
        self.assertEqual(self.start(feature).code, "duplicate_request")
        self.assertEqual(self.start(feature, "fixture_game_b").code, "request_id_conflict")
        self.assertEqual(self.start(feature, "fixture_game_b", "game:2").code, "game_busy")
        factory.assert_called_once()
        feature.stop()
        self.assertEqual(self.start(feature, "fixture_game_b", "game:2").status, "simulated")

    def test_b_is_forwarded_during_game_and_escape_requests_one_exit(self):
        feature = self.feature(fixture=True)
        self.start(feature); feature.poll()
        runner = feature._runner
        self.assertEqual(feature.game_input("b")["action"], "forwarded")
        self.assertIn(("B", True), runner.inputs)
        self.assertEqual(feature.poll(), ())
        feature.game_input("ESC")
        feature.game_input("ESC")
        events = feature.poll()
        self.assertEqual([e.kind for e in events], ["game_exit_requested"])
        self.assertTrue(runner.running)  # Host performs the stop/mode transaction.

    def test_start_select_requires_continuous_one_point_two_seconds_and_debounces(self):
        feature = self.feature(fixture=True)
        self.start(feature); feature.poll()
        feature.game_input("START", now=0)
        feature.game_input("SELECT", now=0)
        self.now = 1.199
        self.assertEqual(feature.poll(), ())
        self.now = 1.2
        self.assertEqual([e.kind for e in feature.poll()], ["game_exit_requested"])
        self.now = 10
        self.assertEqual(feature.poll(), ())
        feature.stop(); self.start(feature, request_id="game:2"); feature.poll()
        feature.game_input("START", now=10)
        feature.game_input("SELECT", now=10)
        feature.game_input("SELECT", False, now=11)
        self.now = 12
        self.assertEqual(feature.poll(), ())

    def test_pad_loss_stops_owned_runner_and_crash_recovers_for_next_game(self):
        feature = self.feature(fixture=True)
        self.start(feature); feature.poll()
        runner = feature._runner
        feature.game_input("PAD_DISCONNECTED")
        self.assertFalse(runner.running)
        events = feature.poll()
        self.assertEqual(events[-1].code, "pad_disconnected")
        self.assertTrue(events[-1].data["resource_released"])
        self.start(feature, request_id="game:2"); feature.poll()
        feature._runner.finish(crashed=True)
        events = feature.poll()
        self.assertEqual((events[-1].kind, events[-1].code), ("game_failed", "game_crashed"))
        self.assertTrue(events[-1].data["resource_released"])
        self.assertEqual(self.start(feature, "fixture_game_b", "game:3").status, "simulated")

    def test_pause_is_only_supported_by_a_declared_backend(self):
        feature = self.feature(fixture=True)
        self.start(feature)
        self.assertEqual(feature.pause().status, "unsupported")
        feature.stop()
        feature._factory = lambda item: FakeGameRunner(pausable=True)
        self.start(feature, request_id="game:2")
        self.assertTrue(feature.pause().completed)
        self.assertEqual(feature.snapshot()["state"], "paused")
        self.assertTrue(feature.resume().completed)
        self.assertEqual(feature.snapshot()["state"], "running")

    def test_unverified_external_backend_does_not_inherit_web_hotkeys_or_launch(self):
        runner = OwnedProcessRunner(("unverified-native",), backend="retroarch")
        self.assertFalse(runner.verified)
        self.assertNotIn("exit_hold", runner.capabilities)
        self.assertNotIn("normalized_input", runner.capabilities)
        with self.assertRaises(FeatureError):
            runner.start(None)
        item = CatalogItem("external_game", "Unverified external", "READY", "ok", simulated=False)
        feature = self.feature(catalog_items=(item,), runner_factory=lambda _: runner)
        self.assertEqual(self.start(feature, item.id).code, "backend_unverified")

    def test_cleanup_failure_never_reports_resource_release_and_can_be_retried(self):
        feature = self.feature(fixture=True)
        self.start(feature)
        runner = feature._runner
        stop = runner.stop
        runner.stop = Mock(side_effect=RuntimeError("private command details"))
        result = feature.stop()
        self.assertEqual((result.status, result.code), ("unknown", "cleanup_failed"))
        self.assertFalse(result.completed)
        self.assertIs(feature._runner, runner)
        self.assertNotIn("private", repr(result))
        runner.stop = stop
        self.assertTrue(feature.stop().completed)

    def test_invalid_ids_and_generations_fail_before_runner_and_closed_cannot_launch(self):
        feature = self.feature(fixture=True)
        for catalog_id, request, generation in (("../game", "r", 1), ("fixture_game_a", "bad id", 1),
                                                ("fixture_game_a", "r", True)):
            with self.subTest(catalog_id=catalog_id), self.assertRaises(FeatureError):
                feature.start(catalog_id, request_id=request, mode_generation=generation)
        feature.close()
        self.assertEqual(self.start(feature).code, "feature_closed")


class OwnedGameProcessTests(unittest.TestCase):
    def test_stop_reaps_owned_harmless_child_and_does_not_signal_other_owned_test_child(self):
        runner = OwnedProcessRunner((sys.executable, "-c", "import time; time.sleep(30)"),
                                    verified=True, ready_probe=lambda process: True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 env={"PATH": os.defpath}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            runner.start(None)
            process = runner.process
            runner.stop()
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
            self.assertIsNone(other.poll())
        finally:
            runner.stop()
            other.terminate(); other.wait(timeout=2)

    def test_sigterm_ignoring_owned_child_is_killed_and_reaped(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "ready"
            code = ("import pathlib,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    "pathlib.Path(" + repr(str(marker)) + ").write_text('ready');time.sleep(30)")
            runner = OwnedProcessRunner((sys.executable, "-c", code), verified=True,
                ready_probe=lambda process: marker.exists(), stop_timeout=.02)
            try:
                runner.start(None)
                process = runner.process
                runner.stop()
                self.assertEqual(process.returncode, -9)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(process.pid, os.WNOHANG)
            finally:
                runner.stop()

    def test_failed_start_has_no_retained_child_and_fixed_error(self):
        runner = OwnedProcessRunner((sys.executable, "-c", "pass"), verified=True,
                                    ready_probe=lambda process: False)
        with self.assertRaises(FeatureError) as error:
            runner.start(None)
        self.assertEqual(error.exception.code, "game_crashed")
        self.assertIsNone(runner.process)


if __name__ == "__main__":
    unittest.main()
