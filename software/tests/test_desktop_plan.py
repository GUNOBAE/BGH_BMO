"""Reviewable desktop templates only; never touch user desktop/autostart paths."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.desktop_plan import desktop_plan, main
from bmo_ai.features.contracts import FeatureError


class DesktopPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bmo-desktop-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Project With Space"
        self.root.mkdir(mode=0o700)
        self.launcher = self.root / "launch-features"
        self.launcher.write_text("#!/bin/sh\nexit 0\n")
        self.launcher.chmod(0o700)
        for target in ("subprocess.Popen", "socket.socket.connect", "getpass.getpass",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("Execution/credentials forbidden")))

    def test_dry_run_template_has_explicit_manual_mode_and_writes_nothing(self):
        before = set(self.root.iterdir())
        result = desktop_plan(project_root=self.root)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertTrue(result["launcher_verified"])
        self.assertEqual(result["command"][1:], ["--simulate", "--screen-profile", "r10"])
        self.assertIn('Exec="' + str(self.launcher) + '"', result["template"])
        self.assertIn("Terminal=true", result["template"])
        self.assertIn("X-GNOME-Autostart-enabled=false", result["template"])
        self.assertFalse(result["autostart"])
        self.assertFalse(result["systemd_changes"])
        self.assertFalse(result["files_written"])
        self.assertEqual(set(self.root.iterdir()), before)

    def test_live_plan_is_only_explicit_conversation_preparation_not_execution(self):
        result = desktop_plan(project_root=self.root, mode="live", screen_profile="v13.2")
        self.assertEqual(result["command"][1:], ["--live", "--screen-profile", "v13.2"])
        self.assertEqual(result["activation"], "NOT_RUN")
        self.assertFalse(result["physical_features_enabled"])

    def test_missing_or_symlink_or_writable_launcher_is_not_verified(self):
        self.launcher.unlink()
        self.assertFalse(desktop_plan(project_root=self.root)["launcher_verified"])
        elsewhere = Path(self.temp.name) / "elsewhere"
        elsewhere.write_text("fixture"); elsewhere.chmod(0o700)
        self.launcher.symlink_to(elsewhere)
        self.assertFalse(desktop_plan(project_root=self.root)["launcher_verified"])
        self.launcher.unlink(); self.launcher.write_text("fixture"); self.launcher.chmod(0o777)
        self.assertFalse(desktop_plan(project_root=self.root)["launcher_verified"])

    def test_control_shell_and_relative_paths_are_rejected(self):
        for path in ("relative", "/tmp/private;command", "/tmp/private\ncommand", "/tmp/a/../b", "/tmp/$private"):
            with self.subTest(index=len(path)), self.assertRaises(FeatureError):
                desktop_plan(project_root=path)

    def test_cli_only_renders_when_dry_run_is_explicit(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main([])
        self.assertEqual(caught.exception.code, 2)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--dry-run", "--project-root", str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "DRY_RUN")
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--dry-run", "--project-root", str(self.root), "--print-template"]), 0)
        self.assertIn("[Desktop Entry]", output.getvalue())
        self.assertEqual(list(self.root.glob("*.desktop")), [])


if __name__ == "__main__":
    unittest.main()
