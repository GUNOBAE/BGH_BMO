"""Install only into synthetic homes; never touch the user's real Desktop."""
from contextlib import redirect_stdout
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai import desktop_install as installer
from bmo_ai.desktop_install import DesktopInstallError


class DesktopInstallTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home = self.root / "synthetic_home"
        self.home.mkdir(mode=0o700)
        self.project = self.home / "BMO Project"
        self.project.mkdir(mode=0o700)
        self.launcher = self.project / "launch-desktop"
        self.launcher.write_text("#!/bin/sh\n# synthetic never executed\n")
        self.launcher.chmod(0o700)
        self.env = {"HOME": str(self.home), "OPENAI_API_KEY": "synthetic_private_openai",
                    "FISH_API_KEY": "synthetic_private_fish"}
        self.desktop = self.home / "Desktop" / "BMO.desktop"
        self.app = self.home / ".local/share/applications/BMO.desktop"
        self.icon = self.home / ".local/share/icons/hicolor/scalable/apps/bmo-desktop.svg"
        for target in ("subprocess.Popen", "socket.socket.connect", "getpass.getpass",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("Execution/network/key forbidden")))

    def options(self, **changes):
        result = {"project_root": self.project, "home": self.home, "environ": self.env}
        result.update(changes)
        return result

    def install(self, **changes):
        return installer.install_desktop(**self.options(install=True, **changes))

    def assertNoStages(self):
        self.assertEqual(list(self.home.rglob(".bmo-desktop-stage-*")), [])

    def test_default_dry_run_is_reviewable_and_creates_no_directories_or_files(self):
        before = set(self.home.rglob("*"))
        report = installer.install_desktop(**self.options())
        self.assertEqual(report["status"], "DRY_RUN")
        self.assertEqual(report["command"], [str(self.launcher), "--live"])
        self.assertEqual(report["files_written"], 0)
        self.assertTrue(report["launcher_verified"])
        self.assertEqual([row["status"] for row in report["targets"]], ["ABSENT"] * 3)
        self.assertIn('Exec="' + str(self.launcher) + '" "--live"', report["template"])
        self.assertIn("Terminal=false", report["template"])
        self.assertFalse(report["autostart"] or report["services_changed"])
        self.assertNotIn("synthetic_private", json.dumps(report))
        self.assertEqual(set(self.home.rglob("*")), before)

    def test_simulate_template_is_explicit_alternative(self):
        report = installer.desktop_install_plan(**self.options(simulate=True))
        self.assertEqual(report["command"][-1], "--simulate")
        self.assertIn('"--simulate"', report["template"])
        self.assertFalse(self.desktop.parent.exists())

    def test_explicit_install_creates_only_owned_entries_and_hash_pinned_generic_icon(self):
        report = self.install()
        self.assertEqual((report["status"], report["files_written"]), ("INSTALLED", 3))
        for path, mode in ((self.desktop, 0o700), (self.app, 0o600), (self.icon, 0o600)):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            self.assertEqual(path.stat().st_uid, os.geteuid())
            self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(self.desktop.read_bytes(), self.app.read_bytes())
        self.assertEqual(hashlib.sha256(self.icon.read_bytes()).hexdigest(), installer.ICON_SHA256)
        self.assertIn("generic text icon", self.icon.read_text())
        self.assertFalse(any(path.name in ("autostart", "systemd") for path in self.home.rglob("*")))
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o700
                            for path in self.home.rglob("*") if path.is_dir()))
        self.assertEqual(report["activation"], "NOT_RUN")
        self.assertEqual(report["desktop_trust"], "NOT_RUN")
        self.assertNoStages()

    def test_identical_duplicate_is_idempotent_and_does_not_rewrite_existing_files(self):
        self.install()
        before = {path: (path.stat().st_ino, path.stat().st_mtime_ns) for path in (self.desktop, self.app, self.icon)}
        report = self.install()
        self.assertEqual((report["status"], report["files_written"]), ("ALREADY_INSTALLED", 0))
        self.assertEqual(before, {path: (path.stat().st_ino, path.stat().st_mtime_ns)
                                  for path in (self.desktop, self.app, self.icon)})
        plan = installer.desktop_install_plan(**self.options())
        self.assertTrue(all(row["status"] == "IDENTICAL" for row in plan["targets"]))

    def test_existing_conflict_is_never_replaced_and_error_does_not_echo_content(self):
        self.desktop.parent.mkdir(mode=0o700)
        self.desktop.write_text("existing_private_entry")
        self.desktop.chmod(0o700)
        for operation in (lambda: installer.desktop_install_plan(**self.options()), self.install):
            with self.assertRaises(DesktopInstallError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "desktop_entry_conflict")
            self.assertNotIn("existing_private_entry", str(caught.exception))
            self.assertEqual(self.desktop.read_text(), "existing_private_entry")
        self.assertFalse(self.app.exists() or self.icon.exists())

    def test_target_symlinks_hardlinks_and_modes_are_refused_without_modification(self):
        self.desktop.parent.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.write_text("keep_private_file")
        outside.chmod(0o700)
        self.desktop.symlink_to(outside)
        with self.assertRaises(DesktopInstallError):
            self.install()
        self.assertEqual(outside.read_text(), "keep_private_file")
        self.desktop.unlink()
        os.link(outside, self.desktop)
        with self.assertRaises(DesktopInstallError):
            self.install()
        self.desktop.unlink()
        self.desktop.write_text("keep_private_mode")
        self.desktop.chmod(0o777)
        with self.assertRaises(DesktopInstallError):
            self.install()
        self.assertEqual(stat.S_IMODE(self.desktop.stat().st_mode), 0o777)

    def test_directory_symlinks_and_writable_directories_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        desktop_dir = self.home / "Desktop"
        desktop_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(DesktopInstallError):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])
        desktop_dir.unlink()
        desktop_dir.mkdir(mode=0o777)
        desktop_dir.chmod(0o777)
        with self.assertRaises(DesktopInstallError):
            self.install()
        self.assertEqual(list(desktop_dir.iterdir()), [])

    def test_xdg_targets_must_stay_inside_owned_home_and_paths_are_not_commands(self):
        for env in ({"XDG_DATA_HOME": str(self.root / "external")},
                    {"XDG_DESKTOP_DIR": str(self.root / "external")},
                    {"XDG_DATA_HOME": "relative"}):
            with self.subTest(names=tuple(env)), self.assertRaises(DesktopInstallError):
                self.install(environ=env)
        for path in ("relative", str(self.home / "a%f"), str(self.home / "bad\nline"),
                     str(self.home / "a/../b")):
            with self.assertRaises(DesktopInstallError):
                self.install(project_root=path)
        self.assertFalse((self.root / "external").exists())

    def test_explicit_localized_desktop_and_xdg_data_paths_are_supported(self):
        environment = {"XDG_DESKTOP_DIR": str(self.home / "바탕화면"),
                       "XDG_DATA_HOME": str(self.home / "local_data")}
        self.install(environ=environment)
        self.assertTrue((self.home / "바탕화면/BMO.desktop").is_file())
        self.assertTrue((self.home / "local_data/applications/BMO.desktop").is_file())
        self.assertFalse(self.desktop.exists())

    def test_xdg_overrides_cannot_create_autostart_or_duplicate_target_entries(self):
        for environment in ({"XDG_DESKTOP_DIR": str(self.home / ".config/autostart")},
                            {"XDG_DESKTOP_DIR": str(self.home / "autostart")},
                            {"XDG_DATA_HOME": str(self.home / ".ssh")},
                            {"XDG_DESKTOP_DIR": str(self.home / ".local/share/applications")}):
            with self.subTest(names=tuple(environment)), self.assertRaises(DesktopInstallError) as caught:
                self.install(environ=environment)
            self.assertEqual(caught.exception.code, "desktop_invalid_config")
        self.assertEqual(set(self.home.iterdir()), {self.project})

    def test_launcher_must_be_ready_and_source_icon_must_match_exact_hash(self):
        self.launcher.unlink()
        self.assertFalse(installer.desktop_install_plan(**self.options())["launcher_verified"])
        with self.assertRaises(DesktopInstallError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "desktop_launcher_pending")
        self.assertFalse(self.desktop.parent.exists())
        bad_icon = self.root / "bad.svg"
        bad_icon.write_text("<svg>unapproved_private_asset</svg>")
        with patch.object(installer, "_ICON", bad_icon), self.assertRaises(DesktopInstallError) as caught:
            installer.desktop_install_plan(**self.options())
        self.assertEqual(caught.exception.code, "desktop_icon_invalid")
        self.assertNotIn("unapproved_private", str(caught.exception))

    def test_fsync_failure_before_publish_rolls_back_only_owned_stage(self):
        with patch.object(installer.os, "fsync", side_effect=OSError("private_storage_failure")), \
             self.assertRaises(DesktopInstallError) as caught:
            self.install()
        self.assertNotIn("private_storage", str(caught.exception))
        self.assertFalse(self.desktop.exists() or self.app.exists() or self.icon.exists())
        self.assertNoStages()

    def test_second_publication_failure_rolls_back_new_files_and_preserves_existing_artifacts(self):
        original, calls = installer.os.link, [0]
        unrelated = self.home / "keep.txt"
        unrelated.write_text("existing_private_file")
        def fail_second(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("synthetic second publication failure")
            return original(*args, **kwargs)
        with patch.object(installer.os, "link", side_effect=fail_second), self.assertRaises(DesktopInstallError):
            self.install()
        self.assertFalse(self.desktop.exists() or self.app.exists() or self.icon.exists())
        self.assertEqual(unrelated.read_text(), "existing_private_file")
        self.assertNoStages()

    def test_interrupt_immediately_after_link_is_rolled_back_by_owned_inode(self):
        original, interrupted = installer.os.link, [False]
        def interrupt_after_link(*args, **kwargs):
            result = original(*args, **kwargs)
            if not interrupted[0]:
                interrupted[0] = True
                raise KeyboardInterrupt()
            return result
        with patch.object(installer.os, "link", side_effect=interrupt_after_link), \
             self.assertRaises(DesktopInstallError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "desktop_install_cancelled")
        self.assertFalse(self.desktop.exists() or self.app.exists() or self.icon.exists())
        self.assertNoStages()

    def test_concurrent_install_lock_prevents_publication(self):
        directory = self.home / ".local/share/bmo-desktop"
        for path in (self.home / ".local", self.home / ".local/share", directory):
            path.mkdir(mode=0o700)
        descriptor = os.open(directory / ".install.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(DesktopInstallError) as caught:
                self.install()
        finally:
            os.close(descriptor)
        self.assertEqual(caught.exception.code, "desktop_install_busy")
        self.assertFalse(self.desktop.exists() or self.app.exists() or self.icon.exists())

    def test_cli_defaults_to_dry_run_and_never_uses_actual_home_in_tests(self):
        with patch.dict(os.environ, {"HOME": str(self.home)}, clear=True), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(installer.main(["--project-root", str(self.project)]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "DRY_RUN")
        self.assertFalse(self.desktop.parent.exists())


if __name__ == "__main__":
    unittest.main()
