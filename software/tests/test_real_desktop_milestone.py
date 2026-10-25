"""Owned Desktop cleanup and explicit activation. No GUI, device or provider IO."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.desktop_app import main, parser, public_plan
from bmo_ai.desktop_browser import BrowserError, DesktopBrowser
from bmo_ai.desktop_diagnostics import startup_preflight
from bmo_ai.desktop_install import desktop_install_plan
from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.contracts import FeatureError


class Clock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class RealDesktopMilestoneTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.exe = self.root / "chromium"
        self.exe.write_text("Never execute this fixture")
        self.exe.chmod(0o700)
        for name in ("subprocess.Popen", "os.killpg", "socket.socket.connect",
                     "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(name, side_effect=AssertionError("Production IO forbidden")))

    def browser(self, *, waitable):
        clock = Clock()
        child = SimpleNamespace(pid=43281, returncode=None)

        def wait(timeout):
            child.returncode = 0
            return 0

        child.wait = Mock(side_effect=wait)
        signaler = Mock()
        browser = DesktopBrowser("http://127.0.0.1:12345/", executable=self.exe,
            environ={"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(self.root)},
            process_factory=Mock(return_value=child), group_signaler=signaler,
            observer=lambda process: waitable(clock), group_probe=lambda *_: False,
            cleanup_clock=clock, cleanup_sleep=clock.sleep, stop_timeout=.06)
        browser.start()
        return browser, child, signaler, clock

    def test_quiet_group_waits_for_late_root_waitability_without_false_failure(self):
        browser, child, signaler, clock = self.browser(waitable=lambda clock: clock.now >= .04)
        self.assertTrue(browser.close()["cleanup_complete"])
        self.assertEqual(clock.now, .04)
        child.wait.assert_called_once_with(timeout=.06)
        signaler.assert_not_called()
        report = browser.cleanup_snapshot()
        self.assertTrue(report["resources_cleaned"])
        self.assertEqual(set(report["resources"].values()), {"released"})
        self.assertEqual(report["stage"], "complete")
        self.assertNotIn("43281", json.dumps(report))

    def test_root_wait_timeout_keeps_ownership_and_reports_exact_stage_then_retries(self):
        waitable = [False]
        browser, child, signaler, clock = self.browser(waitable=lambda _: waitable[0])
        with self.assertRaises(BrowserError):
            browser.close()
        report = browser.cleanup_snapshot()
        self.assertEqual((report["stage"], report["code"]), ("wait_owned_root", "unknown"))
        self.assertEqual(report["resources"]["root"], "unknown")
        self.assertTrue(browser.snapshot()["private_profile"])
        self.assertFalse(report["resources_cleaned"])
        self.assertEqual(clock.now, .06)
        child.wait.assert_not_called()
        signaler.assert_not_called()
        waitable[0] = True
        self.assertTrue(browser.close()["cleanup_complete"])

    def test_cleanup_failure_does_not_skip_other_components_or_echo_exception(self):
        app = DesktopRuntime(simulate=True)
        app.browser = Mock()
        app.browser.close.side_effect = RuntimeError("private-secret-path")
        app.server = SimpleNamespace(close=Mock())
        app.host = SimpleNamespace(close=Mock())
        with self.assertRaises(FeatureError):
            app.close()
        report = app.cleanup_snapshot()
        self.assertFalse(report["resources_cleaned"])
        self.assertEqual(report["resources"]["browser"], "unknown")
        self.assertIn("browser_cleanup_failed", report["codes"])
        self.assertEqual(report["resources"]["server"], "released")
        self.assertEqual(report["resources"]["host"], "released")
        app.server.close.assert_called_once()
        app.host.close.assert_called_once()
        self.assertNotIn("private-secret", json.dumps(report))

    def test_host_explicit_incomplete_cleanup_is_not_promoted_to_released(self):
        app = DesktopRuntime(simulate=True)
        app.host = SimpleNamespace(close=lambda: None, cleanup_snapshot=lambda: {
            "resources_cleaned": False, "private": "private-secret"})
        with self.assertRaises(FeatureError):
            app.close()
        self.assertEqual(app.cleanup_snapshot()["resources"]["host"], "unknown")
        self.assertNotIn("private-secret", json.dumps(app.cleanup_snapshot()))

    def test_browser_failure_stage_is_forwarded_but_adapter_messages_are_not(self):
        app = DesktopRuntime(simulate=True)
        app.browser = SimpleNamespace(close=Mock(side_effect=BrowserError("browser_cleanup_failed")),
            cleanup_snapshot=lambda: {"resources_cleaned": False, "stage": "wait_owned_root",
                                      "code": "unknown", "private": "private-secret"})
        with self.assertRaises(FeatureError):
            app.close()
        report = app.cleanup_snapshot()
        self.assertEqual(report["details"]["browser"], {"stage": "wait_owned_root", "code": "unknown"})
        self.assertNotIn("private-secret", json.dumps(report))

    def test_host_details_forward_only_fixed_codes_and_actual_boolean_evidence(self):
        app = DesktopRuntime(simulate=True)
        app.host = SimpleNamespace(close=lambda: None, cleanup_snapshot=lambda: {
            "resources_cleaned": False, "code": "private-secret-provider-message",
            "microphone_closed": True, "microphone_cleanup_pending": "private-secret-path",
            "session": {"worker_stopped": False, "voice_client_closed": True,
                        "private_path": "private-secret-path"},
            "camera": {"sensor_closed": False, "photo_active": False, "close_unknown": True,
                       "private_token": "private-secret-token"}, "private": "private-secret"})
        with self.assertRaises(FeatureError):
            app.close()
        report = app.cleanup_snapshot()
        detail = report["details"]["host"]
        self.assertFalse(report["resources_cleaned"])
        self.assertEqual(detail["code"], "unknown")
        self.assertFalse(detail["evidence_valid"])
        self.assertFalse(detail["session_worker_stopped"])
        self.assertTrue(detail["camera_close_unknown"])
        self.assertNotIn("microphone_cleanup_pending", detail)
        self.assertNotIn("private-secret", json.dumps(report))
        self.assertTrue(all(type(value) is bool for name, value in detail.items() if name != "code"))

    def test_host_claimed_success_cannot_override_camera_close_unknown(self):
        app = DesktopRuntime(simulate=True)
        app.host = SimpleNamespace(close=lambda: None, cleanup_snapshot=lambda: {
            "resources_cleaned": True, "code": "resources_cleaned", "microphone_closed": True,
            "microphone_cleanup_pending": False,
            "session": {"worker_stopped": True, "voice_client_closed": True},
            "camera": {"resources_cleaned": False, "sensor_closed": False,
                       "photo_active": False, "close_unknown": True}})
        with self.assertRaises(FeatureError):
            app.close()
        report = app.cleanup_snapshot()
        self.assertFalse(report["resources_cleaned"])
        self.assertEqual(report["resources"]["host"], "unknown")
        self.assertIn("host_cleanup_failed", report["codes"])
        self.assertTrue(report["details"]["host"]["camera_close_unknown"])

    def test_all_close_returns_make_scoped_report_not_physical_ack(self):
        app = DesktopRuntime(simulate=True)
        app.browser = SimpleNamespace(close=lambda: None)
        app.server = SimpleNamespace(close=lambda: None)
        app.host = SimpleNamespace(close=lambda: None)
        report = app.close()
        self.assertTrue(report["resources_cleaned"])
        self.assertFalse(report["physical_release_verified"])
        self.assertEqual(report["scope"], "desktop_owned_components")
        self.assertEqual(app.close(), report)

    def test_runtime_metadata_reports_mode_without_claiming_change_actor(self):
        self.root.chmod(0o770)
        forbidden = Mock(side_effect=AssertionError("No mutation"))
        with patch("os.chmod", forbidden), patch("os.mkdir", forbidden):
            report = startup_preflight(environ={"XDG_RUNTIME_DIR": str(self.root)},
                executable_candidates=(), face_file=self.root / "absent", lock_path=self.root / "absent.lock")
        self.assertEqual(report["runtime_metadata"]["mode"], "0770")
        self.assertTrue(report["runtime_metadata"]["owner_matches"])
        self.assertEqual(report["runtime_metadata"]["mode_change_actor"], "UNKNOWN")
        self.assertNotIn(str(self.root), json.dumps(report))
        forbidden.assert_not_called()

    def test_supervised_camera_combinations_fail_before_lock_or_keys(self):
        for args in (["--camera-supervised"], ["--simulate", "--camera-supervised"],
                     ["--live", "--camera-supervised"], ["--live", "--photo-directory", "/FAKE"],
                     ["--live", "--camera-video-supervised"],
                     ["--live", "--camera-supervised", "--photo-directory", "/FAKE", "--camera-video-supervised"],
                     ["--live", "--ptt-ambient-profile", "/FAKE"]):
            with self.subTest(args=args), patch("os.open", side_effect=AssertionError("No lock")), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args)

    def test_dry_run_records_new_opt_ins_without_opening_any_resource(self):
        forbidden = Mock(side_effect=AssertionError("No resource IO"))
        with patch("os.open", forbidden), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--live", "--dry-run", "--ptt-microphone", "--ptt-trace",
                "--camera-supervised", "--photo-directory", "/FAKE",
                "--camera-video-supervised", "--video-directory", "/FAKE_VIDEO"],
                host_factory=forbidden), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["ptt_diagnostics"]["trace_enabled"])
        self.assertTrue(report["camera"]["supervised_opt_in"])
        self.assertTrue(report["camera_video"]["live_enabled"])
        self.assertFalse(report["camera"]["automatic_capture"])
        forbidden.assert_not_called()

    def test_install_plan_persists_only_explicit_button_microphone_opt_in(self):
        home = self.root / "home"
        project = home / "BMO"
        project.mkdir(parents=True, mode=0o700)
        home.chmod(0o700)
        launcher = project / "launch-desktop"
        launcher.write_text("#!/bin/sh\n# never execute")
        launcher.chmod(0o700)
        options = dict(home=home, project_root=project, environ={})
        default = desktop_install_plan(**options)
        enabled = desktop_install_plan(**options, ptt_microphone=True)
        self.assertNotIn("--ptt-microphone", default["command"])
        self.assertIn("--ptt-microphone", enabled["command"])
        self.assertFalse(enabled["automatic_capture"])
        self.assertFalse(enabled["autostart"])
        self.assertNotIn("--camera-supervised", enabled["command"])
        self.assertFalse((home / "Desktop").exists())

    def test_main_emits_post_exit_report_and_passes_trace_voice_flags_to_host(self):
        server = SimpleNamespace(start=lambda: server, close=Mock(), url="http://127.0.0.1:12345/")
        browser = SimpleNamespace(start=Mock(), close=Mock())
        host = SimpleNamespace(close=Mock(), cleanup_snapshot=lambda: {"resources_cleaned": True})
        factory = Mock(return_value=host)
        greeting = SimpleNamespace(close=Mock(return_value=True))
        with patch("bmo_ai.desktop_app.FeatureServer", return_value=server), redirect_stderr(io.StringIO()) as output:
            result = main(["--simulate", "--ptt-trace", "--plain-voice"],
                lock_path=self.root / "app.lock", host_factory=factory,
                browser_factory=lambda *_args, **_kw: browser,
                greeting_factory=lambda **_kw: greeting, on_started=lambda app: app._exit.set())
        self.assertEqual(result, 0)
        self.assertTrue(factory.call_args.kwargs["ptt_trace"])
        self.assertFalse(factory.call_args.kwargs["expressive_voice"])
        prefix = "BMO desktop cleanup: "
        report = json.loads(next(line[len(prefix):] for line in output.getvalue().splitlines() if line.startswith(prefix)))
        self.assertTrue(report["resources_cleaned"])
        self.assertFalse(report["physical_release_verified"])
        self.assertEqual(report["resources"]["host"], "released")


if __name__ == "__main__":
    unittest.main()
