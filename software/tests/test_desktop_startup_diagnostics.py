"""Session metadata fixtures and startup faults; never open devices or repair OS."""
from contextlib import redirect_stderr, redirect_stdout
import fcntl
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
from bmo_ai.desktop_diagnostics import failure, startup_preflight
from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.resources import AudioFocus


class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.face = self.root / "face.html"
        self.face.write_bytes(b"Synthetic face")
        self.exe = self.root / "chromium"
        self.exe.write_text("Never execute")
        self.exe.chmod(0o700)
        self.env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(self.root),
                    "OPENAI_API_KEY": "synthetic-sensitive-fixture"}

    def preflight(self):
        return startup_preflight(environ=self.env, face_file=self.face,
            lock_path=self.root / "absent.lock", executable_candidates=(self.exe,))

    def test_read_only_preflight_detects_0770_without_chmod_create_spawn_or_keys(self):
        self.root.chmod(0o770)
        forbidden = Mock(side_effect=AssertionError("Mutation or device forbidden"))
        with patch("os.chmod", forbidden), patch("os.mkdir", forbidden), patch("subprocess.Popen", forbidden), \
                patch("bmo_ai.private_credentials.resolve_credentials", forbidden):
            result = self.preflight()
        forbidden.assert_not_called()
        self.assertEqual(result["runtime_directory"], "runtime_directory_permissions")
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o770)
        self.assertEqual(result["launcher_lock"], "absent")
        self.assertFalse((self.root / "absent.lock").exists())
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn("synthetic-sensitive-fixture", json.dumps(result))

    def test_0700_and_unsafe_executable_and_wrong_face_hash_report_separately(self):
        self.assertEqual(self.preflight()["runtime_directory"], "ready")
        self.assertEqual(self.preflight()["chromium"], "ready")
        self.assertEqual(self.preflight()["screen"], "screen_sha256_mismatch")
        self.exe.chmod(0o777)
        self.assertEqual(self.preflight()["chromium"], "browser_executable_unsafe")
        self.face.unlink()
        self.assertEqual(self.preflight()["screen"], "screen_unavailable")

    def test_runtime_symlink_is_refused_without_touching_target(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.env["XDG_RUNTIME_DIR"] = str(alias)
        self.assertEqual(self.preflight()["runtime_directory"], "runtime_directory_permissions")

    def test_read_only_lock_probe_reports_occupied_and_never_truncates(self):
        path = self.root / "existing.lock"
        path.write_bytes(b"private synthetic lock contents")
        path.chmod(0o600)
        with path.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = startup_preflight(environ=self.env, face_file=self.face, lock_path=path,
                                       executable_candidates=())
        self.assertEqual(result["launcher_lock"], "occupied")
        self.assertEqual(path.read_bytes(), b"private synthetic lock contents")

    def test_safe_failure_allowlist_discards_exception_text_path_and_arbitrary_codes(self):
        for error in (RuntimeError("secret/path/key"), SimpleNamespace(code="secret/path/key"),
                      FeatureError("synthetic_private_provider")):
            self.assertEqual(failure("browser_start", error),
                "BMO desktop failed: stage=browser_start code=desktop_start_failed")
        self.assertIn("code=runtime_directory_permissions", failure("browser_start", BrowserError("runtime_directory_permissions")))

    def test_cli_read_only_diagnosis_never_launches_engine_or_credentials(self):
        forbidden = Mock(side_effect=AssertionError("No runtime operation"))
        with patch("bmo_ai.desktop_diagnostics.startup_preflight", return_value={"files_written": 0}) as preflight, \
                patch("os.open", forbidden), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--diagnose-startup"], host_factory=forbidden,
                                  credential_resolver=forbidden, browser_factory=forbidden), 0)
        self.assertEqual(json.loads(output.getvalue())["files_written"], 0)
        preflight.assert_called_once()
        forbidden.assert_not_called()

    def test_main_browser_fault_reports_exact_stage_code_and_closes_server(self):
        for mode in ([], ["--windowed"]):
            server = Mock()
            server.start.return_value = server
            server.url = "http://127.0.0.1:1234/"
            browser = Mock()
            browser.start.side_effect = BrowserError("runtime_directory_permissions")
            with patch("bmo_ai.desktop_app.FeatureServer", return_value=server), \
                    redirect_stderr(io.StringIO()) as output:
                code = main(["--simulate", *mode], lock_path=self.root / "launcher.lock",
                            browser_factory=Mock(return_value=browser))
            self.assertEqual(code, 1)
            self.assertIn("stage=browser_start code=runtime_directory_permissions", output.getvalue())
            server.close.assert_called_once()
            browser.close.assert_called_once()

    def test_browser_refuses_runtime_0770_before_profile_or_process_creation(self):
        self.root.chmod(0o770)
        factory = Mock(side_effect=AssertionError("No spawn"))
        browser = DesktopBrowser("http://127.0.0.1:1234/", environ=self.env,
                                 executable=self.exe, process_factory=factory)
        with self.assertRaises(BrowserError) as raised: browser.start()
        self.assertEqual(raised.exception.code, "runtime_directory_permissions")
        self.assertFalse(tuple(self.root.glob("bmo-chromium-*")))
        factory.assert_not_called()

    def test_public_plan_matches_welcome_v2_source_not_obsolete_greeting(self):
        from bmo_ai.greeting_cache import GREETING_TEXT
        value = public_plan(parser().parse_args(["--status"]))
        self.assertEqual(value["greeting"]["text"], GREETING_TEXT)
        self.assertEqual(value["greeting"]["asset_version"], "welcome-v2")

    def ready_runtime(self):
        app = DesktopRuntime(simulate=True, require_face_presented=True)
        app.set_server(SimpleNamespace(_bridge=SimpleNamespace(sha256="a"*64), close=lambda: None))
        app.set_browser(SimpleNamespace(mark_ready=lambda: True, poll=lambda: {"state": "running"}, close=lambda: None))
        host = SimpleNamespace(audio=AudioFocus(), session=SimpleNamespace(busy=False), poll=lambda: None, close=lambda: None)
        player = Mock()
        player.play.side_effect = lambda **_: None
        app.set_host(host, player)
        app.queue_desktop_control("ready", {"action": "face_ready", "value": {
            "run_id": app.run_id, "screen_sha256": "a"*64, "frame_version": "fixture"}})
        return app, player

    def test_greeting_cannot_begin_before_face_is_presented_and_stale_ack_rejected(self):
        app, player = self.ready_runtime()
        try:
            app.poll()
            self.assertEqual(app.startup_snapshot()["progress_percent"], 100)
            player.play.assert_not_called()
            with self.assertRaises(FeatureError):
                app.queue_desktop_control("old", {"action": "face_presented", "value": {"run_id": "old"}})
            app.queue_desktop_control("presented", {"action": "face_presented", "value": {"run_id": app.run_id}})
            app.poll()
            if app._worker is not None: app._worker.join(1)
            app.poll()
            player.play.assert_called_once()
            app.queue_desktop_control("reload", {"action": "face_presented", "value": {"run_id": app.run_id}})
            app.poll()
            player.play.assert_called_once()
        finally: app.close()

    def test_retry_after_early_stop_cannot_bypass_face_presentation(self):
        app, player = self.ready_runtime()
        try:
            app._user_cancel.set()
            app.poll()
            self.assertEqual(app.startup_snapshot()["code"], "greeting_cancelled")
            app.queue_desktop_control("early_retry", {"action": "retry_greeting", "value": None})
            app.poll()
            player.play.assert_not_called()
            app.queue_desktop_control("shown", {"action": "face_presented", "value": {"run_id": app.run_id}})
            app.queue_desktop_control("visible_retry", {"action": "retry_greeting", "value": None})
            app.poll()
            if app._worker is not None: app._worker.join(1)
            app.poll()
            player.play.assert_called_once()
        finally: app.close()


if __name__ == "__main__": unittest.main()
