"""Doctor inspects only explicit synthetic files and mocked package/Git metadata."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import offline_doctor as doctor


class OfflineDoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bmo-doctor-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {}
        profiles = {}
        for name in ("r10", "v13.2"):
            path = self.root / (name + ".html")
            path.write_bytes(("<!doctype html><p>synthetic " + name + "</p>").encode())
            self.paths[name] = path
            profiles[name] = {"default_path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "approval_status": "approved_design" if name == "r10" else "approved"}
        self.enterContext(patch.object(doctor, "SCREEN_PROFILES", profiles))
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                       "bmo_ai.private_credentials.resolve_credentials", "getpass.getpass"):
            self.enterContext(patch(target, side_effect=AssertionError("Live/secret operations forbidden")))
        self.dependencies = Mock(return_value={"available": True, "version": "1.0-fixture", "imported": False, "executed": False})
        self.git = Mock(return_value={"status": "OBSERVED", "head": "a" * 40,
            "branch": "feature/bmo-offline-integration-v2", "tracked_changes": False,
            "changed_tracked_count": 0, "remote_verification": "NOT_RUN", "untracked_content_read": False})

    def report(self, **kwargs):
        return doctor.offline_report(project_root=self.root, screen_paths=self.paths,
            asset_paths=[self.root / "missing-owned-game.zip"], dependency_probe=self.dependencies,
            git_probe=self.git, **kwargs)

    def test_ready_is_metadata_only_and_all_live_tests_remain_not_run(self):
        result = self.report()
        self.assertEqual(result["readiness"], "READY_FOR_HUMAN_TEST")
        self.assertEqual(result["readiness_scope"], "metadata_prerequisites_only")
        self.assertEqual((result["tests"], result["ci"]), ("NOT_RUN", "NOT_RUN"))
        self.assertTrue(all(value == "NOT_RUN" for value in result["live"].values()))
        self.assertFalse(result["credentials_accessed"])
        self.assertFalse(result["ports_opened"])
        self.assertFalse(result["devices_opened"])
        self.assertEqual(result["game_assets"]["status"], "ASSET_PENDING")
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_required_dependency_missing_blocks_ready_but_optional_camera_does_not(self):
        self.dependencies.side_effect = lambda module, distribution: {"available": module != "picamera2", "imported": False, "executed": False}
        self.assertEqual(self.report()["readiness"], "READY_FOR_HUMAN_TEST")
        self.dependencies.side_effect = lambda module, distribution: {"available": module != "openai", "imported": False, "executed": False}
        self.assertEqual(self.report()["readiness"], "NOT_READY")

    def test_wrong_screen_hash_missing_screen_or_missing_git_never_reports_ready(self):
        original = self.paths["r10"].read_bytes()
        self.paths["r10"].write_bytes(b"private fixture content changed")
        result = self.report()
        self.assertEqual(result["screens"]["r10"]["status"], "MISMATCH")
        self.assertEqual(result["readiness"], "NOT_READY")
        self.assertNotIn("private fixture", json.dumps(result))
        self.paths["r10"].unlink()
        self.assertEqual(self.report()["screens"]["r10"]["status"], "ASSET_PENDING")
        self.paths["r10"].write_bytes(original)
        self.git.return_value = {"status": "UNAVAILABLE"}
        self.assertEqual(self.report()["readiness"], "NOT_READY")

    def test_screen_symlinks_nonregular_and_oversized_files_are_not_read(self):
        target = self.paths["r10"]
        expected = doctor.SCREEN_PROFILES["r10"]["sha256"]
        alias = self.root / "alias.html"
        alias.symlink_to(target)
        self.assertFalse(doctor._screen(alias, expected)["sha256_match"])
        folder = self.root / "folder.html"; folder.mkdir()
        self.assertFalse(doctor._screen(folder, expected)["sha256_match"])
        large = self.root / "large.html"; large.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        self.assertEqual(doctor._screen(large, expected)["status"], "UNVERIFIED")

    def test_config_credential_paths_are_refused_even_as_explicit_screen_override(self):
        path = self.root / ".config" / "bmo" / "private.html"
        with patch("os.open", side_effect=AssertionError("No key/config descriptor")):
            self.assertEqual(doctor._screen(path, "a" * 64)["status"], "UNVERIFIED")

    def test_dependency_probe_uses_package_metadata_without_import_or_tool_execution(self):
        with patch.object(doctor.importlib.util, "find_spec", return_value=object()), \
             patch.object(doctor.importlib.metadata, "version", return_value="2.0-fixture"), \
             patch("importlib.import_module", side_effect=AssertionError("No actual module initialization")):
            result = doctor._dependency("fixture", "fixture")
        self.assertEqual(result, {"available": True, "version": "2.0-fixture", "imported": False, "executed": False})
        with patch.object(doctor.importlib.util, "find_spec", side_effect=OSError("private metadata path")):
            self.assertFalse(doctor._dependency("fixture", "fixture")["available"])

    def test_git_reads_disable_optional_locks_hooks_global_config_and_credentials(self):
        replies = [SimpleNamespace(stdout="a" * 40 + "\n"),
                   SimpleNamespace(stdout="feature/bmo-offline-integration-v2\n"),
                   SimpleNamespace(stdout=" M private_filename.py\n")]
        with patch.object(doctor.subprocess, "run", side_effect=replies) as run:
            result = doctor._git(self.root)
        self.assertTrue(result["tracked_changes"])
        self.assertEqual(result["changed_tracked_count"], 1)
        self.assertNotIn("private_filename", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        for call in run.call_args_list:
            self.assertIn("--no-optional-locks", call.args[0])
            self.assertIn("core.fsmonitor=false", call.args[0])
            self.assertEqual(call.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            self.assertFalse(any(name.startswith(("OPENAI_", "FISH_", "GH_", "SSH_")) for name in call.kwargs["env"]))
        with patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired("private", 3)):
            self.assertEqual(doctor._git(self.root)["status"], "UNAVAILABLE")

    def test_cli_requires_offline_and_never_installs_or_executes_tests(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            doctor.main([])
        self.assertEqual(caught.exception.code, 2)
        before = set(self.root.iterdir())
        with patch.object(doctor, "_dependency", self.dependencies), patch.object(doctor, "_git", self.git), \
             redirect_stdout(io.StringIO()) as output:
            result = doctor.main(["--offline", "--project-root", str(self.root),
                "--r10-path", str(self.paths["r10"]), "--v13-path", str(self.paths["v13.2"]),
                "--asset", str(self.root / "missing.zip")])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "offline_metadata_only")
        self.assertEqual(set(self.root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
