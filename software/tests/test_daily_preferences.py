"""Explicit local choices survive restart without writing launch defaults."""
import fcntl
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmo_ai.daily_preferences import DailyPreferences, MAX_BYTES


class DailyPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.directory = self.root / "preferences"
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("device/provider forbidden")))

    def put(self, raw, *, directory=None):
        root = directory or self.directory
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = root / "preferences.json"
        target.write_text(raw)
        target.chmod(0o600)
        return target

    def test_missing_store_is_readonly_and_preserves_absent_launcher_defaults(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config")}):
            store = DailyPreferences()
        self.assertEqual(store.load(), {})
        self.assertEqual(store.snapshot(), dict(enabled=True, code="preferences_empty",
            stored_fields=[], automatic_write=False))
        self.assertFalse((self.root / "config").exists())
        self.assertTrue(store.update())
        self.assertFalse((self.root / "config").exists())

    def test_explicit_choices_survive_restart_and_do_not_change_unset_choices(self):
        store = DailyPreferences(self.directory)
        self.assertTrue(store.update(subtitle_mode="ko"))
        restarted = DailyPreferences(self.directory)
        self.assertEqual(restarted.load(), {"subtitle_mode": "ko"})
        self.assertTrue(restarted.update(volume=dict(level=.05, muted=True), presence_mode="normal"))
        self.assertEqual(DailyPreferences(self.directory).load(), dict(
            subtitle_mode="ko", volume=dict(level=.05, muted=True), presence_mode="normal"))
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        target = self.directory / "preferences.json"
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertLess(target.stat().st_size, MAX_BYTES)
        self.assertNotIn(str(self.root), target.read_text())
        # Unmute, language OFF and automatic speech OFF are actual saved choices.
        self.assertTrue(restarted.update(volume=dict(level=.3, muted=False), subtitle_mode="off", presence_mode="off"))
        self.assertEqual(DailyPreferences(self.directory).load(), dict(
            subtitle_mode="off", volume=dict(level=.3, muted=False), presence_mode="off"))

    def test_separate_instances_merge_only_explicit_fields_and_defend_mutable_aliases(self):
        one, two = DailyPreferences(self.directory), DailyPreferences(self.directory)
        volume = dict(level=.4, muted=False)
        self.assertTrue(one.update(volume=volume))
        volume["muted"] = True
        loaded = one.load()
        loaded["volume"]["level"] = 1.
        self.assertEqual(one.load(), {"volume": dict(level=.4, muted=False)})
        self.assertTrue(two.update(subtitle_mode="en"))
        self.assertEqual(two.load(), dict(volume=dict(level=.4, muted=False), subtitle_mode="en"))
        self.assertTrue(one.update(presence_mode="quiet"))
        self.assertEqual(DailyPreferences(self.directory).load(), dict(
            volume=dict(level=.4, muted=False), subtitle_mode="en", presence_mode="quiet"))

    def test_invalid_updates_never_replace_good_preferences(self):
        store = DailyPreferences(self.directory)
        self.assertTrue(store.update(presence_mode="normal"))
        before = (self.directory / "preferences.json").read_bytes()
        invalid = (
            dict(api_key="not a supported field"), dict(arms_enabled=True),
            dict(subtitle_mode="auto"), dict(subtitle_mode=None), dict(presence_mode=True),
            dict(presence_mode="frequent"), dict(volume=dict(level=True, muted=False)),
            dict(volume=dict(level=-.1, muted=False)), dict(volume=dict(level=1.1, muted=False)),
            dict(volume=dict(level=float("nan"), muted=False)),
            dict(volume=dict(level=float("inf"), muted=False)),
            dict(volume=dict(level=.25, muted=1)), dict(volume=dict(level=.25)),
            dict(volume=dict(level=.25, muted=False, extra="unknown")),
        )
        for values in invalid:
            with self.subTest(values=values):
                self.assertFalse(store.update(**values))
                self.assertEqual((self.directory / "preferences.json").read_bytes(), before)
                self.assertTrue(store.snapshot()["enabled"])
        self.assertTrue(store.update(subtitle_mode="ko"))

    def test_invalid_existing_file_blocks_writes_without_overwrite(self):
        invalid = (
            "not JSON", '{"version":true,"settings":{}}',
            '{"version":2,"settings":{}}', '{"version":1,"settings":[],"extra":0}',
            '{"version":1,"settings":{"api_key":"must not load"}}',
            '{"version":1,"settings":{"presence_mode":"off","presence_mode":"normal"}}',
            '{"version":1,"settings":{"volume":{"level":NaN,"muted":false}}}',
            '{"version":1,"settings":{"volume":{"level":' + "9" * 400 + ',"muted":false}}}',
            "[" * 1500 + "0" + "]" * 1500,
            " " * (MAX_BYTES + 1),
        )
        for index, raw in enumerate(invalid):
            with self.subTest(index=index):
                directory = self.root / str(index)
                target = self.put(raw, directory=directory)
                store = DailyPreferences(directory)
                self.assertFalse(store.snapshot()["enabled"])
                self.assertEqual(store.load(), {})
                self.assertFalse(store.update(subtitle_mode="ko"))
                self.assertEqual(target.read_text(), raw)
                self.assertEqual(list(directory.iterdir()), [target])

    def test_live_file_corruption_is_not_overwritten_by_later_user_change(self):
        store = DailyPreferences(self.directory)
        self.assertTrue(store.update(subtitle_mode="en"))
        target = self.directory / "preferences.json"
        target.write_text("external incomplete change")
        self.assertFalse(store.update(presence_mode="off"))
        self.assertEqual(target.read_text(), "external incomplete change")
        self.assertFalse(store.snapshot()["enabled"])
        self.assertEqual(store.load(), {"subtitle_mode": "en"})

    def test_unsafe_paths_modes_checkout_and_links_are_rejected(self):
        self.directory.mkdir(mode=0o755)
        self.directory.chmod(0o755)
        self.assertFalse(DailyPreferences(self.directory).snapshot()["enabled"])
        real = self.root / "real"
        real.mkdir(mode=0o700)
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        self.assertFalse(DailyPreferences(link / "new").snapshot()["enabled"])
        self.assertFalse((real / "new").exists())
        checkout = self.root / "checkout"
        checkout.mkdir(mode=0o700)
        (checkout / ".git").write_text("gitdir: another repository")
        self.assertFalse(DailyPreferences(checkout / "new").snapshot()["enabled"])
        self.assertFalse((checkout / "new").exists())
        self.assertFalse(DailyPreferences(Path("relative")).snapshot()["enabled"])
        self.assertFalse(DailyPreferences(self.root / ".." / "invalid").snapshot()["enabled"])

    def test_unsafe_file_types_and_permissions_never_block_or_get_replaced(self):
        raw = json.dumps(dict(version=1, settings=dict(subtitle_mode="en")))
        for kind in ("public", "symlink", "hardlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                directory = self.root / kind
                directory.mkdir(mode=0o700)
                target = directory / "preferences.json"
                if kind == "fifo":
                    os.mkfifo(target, 0o600)
                elif kind == "directory":
                    target.mkdir(mode=0o700)
                elif kind == "symlink":
                    target.symlink_to(self.put(raw, directory=self.root / "outside"))
                else:
                    target.write_text(raw)
                    target.chmod(0o644 if kind == "public" else 0o600)
                    if kind == "hardlink":
                        os.link(target, directory / "another")
                store = DailyPreferences(directory)
                self.assertFalse(store.snapshot()["enabled"])
                self.assertFalse(store.update(subtitle_mode="ko"))
                self.assertEqual(len(list(directory.iterdir())), 2 if kind == "hardlink" else 1)

    def test_failure_before_publish_preserves_original_and_removes_temporary_file(self):
        store = DailyPreferences(self.directory)
        self.assertTrue(store.update(subtitle_mode="en"))
        target = self.directory / "preferences.json"
        before = target.read_bytes()
        with patch("bmo_ai.daily_preferences.os.replace", side_effect=OSError("private detail")):
            self.assertFalse(store.update(subtitle_mode="ko"))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), [".preferences.lock", "preferences.json"])
        self.assertEqual(store.snapshot()["code"], "preferences_write_failed")
        self.assertNotIn("private detail", str(store.snapshot()))

    def test_lock_contention_is_bounded_and_can_be_retried_by_next_explicit_change(self):
        store = DailyPreferences(self.directory)
        self.assertTrue(store.update(subtitle_mode="en"))
        fd = os.open(self.directory / ".preferences.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(store.update(subtitle_mode="ko"))
            self.assertEqual(store.snapshot()["code"], "preferences_busy")
            self.assertTrue(store.snapshot()["enabled"])
        finally:
            os.close(fd)
        self.assertTrue(store.update(subtitle_mode="ko"))
        self.assertEqual(DailyPreferences(self.directory).load()["subtitle_mode"], "ko")

    def test_substituted_symlink_after_construction_cannot_write_elsewhere(self):
        store = DailyPreferences(self.directory)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir(mode=0o700)
        self.directory.symlink_to(elsewhere, target_is_directory=True)
        self.assertFalse(store.update(presence_mode="normal"))
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
