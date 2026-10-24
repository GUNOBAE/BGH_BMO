"""Already-owned synthetic ZIPs only; no downloads or game execution."""
from contextlib import redirect_stdout
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.games import assets
from bmo_ai.game_assets import main


class GameAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bmo-game-asset-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "owned.zip"
        self.content = self.root / "content"
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen"):
            self.enterContext(patch(target, side_effect=AssertionError("Network/execution forbidden")))

    def zip(self, entries=None, *, compression=zipfile.ZIP_STORED):
        entries = entries or [("index.html", b"<p>synthetic game</p>"), ("LICENSE", b"private_license_fixture")]
        with zipfile.ZipFile(self.archive, "w", compression=compression) as output:
            for row in entries:
                name, data = row[:2]
                mode = row[2] if len(row) > 2 else stat.S_IFREG | 0o600
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                info.compress_type = compression
                output.writestr(info, data)
        return self.archive

    def install(self, review, **kwargs):
        settings = dict(license_id="LicenseRef-UserReviewed", license_reviewed=True,
                        web_content_reviewed=True)
        settings.update(kwargs)
        return assets.install_reviewed(review, self.content, "fixture_pack", **settings)

    def assertNoStage(self):
        if self.content.exists():
            self.assertFalse(any(".stage-" in path.name for path in self.content.iterdir()))

    def test_inventory_stats_only_known_paths_and_missing_assets_remain_pending(self):
        report = assets.inventory_known_assets([self.archive])
        self.assertEqual(report["status"], "ASSET_PENDING")
        self.zip()
        with patch("builtins.open", side_effect=AssertionError("No content read")), \
             patch("os.open", side_effect=AssertionError("No descriptor open")):
            report = assets.inventory_known_assets([self.archive])
        self.assertEqual(report["status"], "AVAILABLE_FOR_REVIEW")
        self.assertFalse(report["installed_games_verified"])
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_review_is_readonly_hash_pinned_and_does_not_print_license_content_or_source_path(self):
        self.zip()
        before = set(self.root.iterdir())
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        review = assets.review_archive(self.archive, expected_sha256=digest)
        self.assertEqual(review.sha256, digest)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertTrue(review.active_web_content)
        self.assertEqual(review.license_documents, ("LICENSE",))
        public = json.dumps(review.public())
        self.assertNotIn("private_license_fixture", public)
        self.assertNotIn(str(self.root), public)
        self.assertNotIn(str(self.root), repr(review))
        self.assertEqual(review.public()["license_review"], "NOT_RUN")

    def test_traversal_absolute_windows_sensitive_and_duplicate_names_are_rejected(self):
        bad = ("../escape.html", "/escape.html", "C:/escape.html", "a\\escape.html", "a//b.html",
               "a/./b.html", ".env", ".git/config", "credentials.json", ".bmo-game-install.json")
        for name in bad:
            with self.subTest(name=name):
                self.zip([(name, b"fixture")])
                with self.assertRaises(FeatureError):
                    assets.review_archive(self.archive)
        for entries in ((('a.html', b'a'), ('A.html', b'b')),
                        (('a.txt', b'a'), ('a.txt/b.html', b'b'))):
            self.zip(entries)
            with self.assertRaises(FeatureError):
                assets.review_archive(self.archive)

    def test_symlink_device_executable_bits_extensions_and_native_magic_are_rejected(self):
        entries = (("link.html", b"../outside", stat.S_IFLNK | 0o777),
                   ("pipe.txt", b"", stat.S_IFIFO | 0o600),
                   ("game.js", b"fixture", stat.S_IFREG | 0o700),
                   ("game.py", b"print('fixture')"), ("LICENSE.py", b"fixture"),
                   ("image.png", b"\x7fELFfixture"), ("image.png", b"MZfixture"),
                   ("game.js", b"#!/bin/sh\nfixture"))
        for index, entry in enumerate(entries):
            with self.subTest(index=index):
                self.zip([entry])
                with self.assertRaises(FeatureError):
                    assets.review_archive(self.archive)

    def test_encrypted_flags_and_entry_count_size_ratio_are_bounded(self):
        self.zip()
        raw = bytearray(self.archive.read_bytes())
        for marker, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = raw.find(marker)
            flags = struct.unpack_from("<H", raw, position + offset)[0]
            struct.pack_into("<H", raw, position + offset, flags | 1)
        self.archive.write_bytes(raw)
        with self.assertRaises(FeatureError):
            assets.review_archive(self.archive)
        self.zip()
        with patch.object(assets, "MAX_ENTRIES", 1), self.assertRaises(FeatureError):
            assets.review_archive(self.archive)
        with patch.object(assets, "MAX_FILE_BYTES", 4), self.assertRaises(FeatureError):
            assets.review_archive(self.archive)
        self.zip([("large.txt", b"0" * 10000)], compression=zipfile.ZIP_DEFLATED)
        with self.assertRaises(FeatureError):
            assets.review_archive(self.archive)

    def test_central_preflight_rejects_count_before_zipfile_or_zipinfo_allocations(self):
        self.zip([("a.txt", b"a"), ("b.txt", b"b"), ("c.txt", b"c")])
        constructor, info_type = assets.zipfile.ZipFile, assets.zipfile.ZipInfo
        with patch.object(assets, "MAX_ENTRIES", 1), \
             patch.object(assets.zipfile, "ZipFile", wraps=constructor) as parsed, \
             patch.object(assets.zipfile, "ZipInfo", wraps=info_type) as allocated, \
             self.assertRaises(FeatureError):
            assets.review_archive(self.archive)
        parsed.assert_not_called()
        allocated.assert_not_called()

    def test_central_preflight_rejects_lying_counts_offsets_zip64_and_multidisk(self):
        self.zip([("a.txt", b"a"), ("b.txt", b"b"), ("c.txt", b"c")])
        raw = self.archive.read_bytes()
        end, central = raw.rfind(b"PK\x05\x06"), raw.find(b"PK\x01\x02")
        mutations = []
        for offset, value, width in ((4, 1, "H"), (6, 1, "H"), (8, 2, "H"),
                                     (8, 1, "H"), (10, 1, "H"), (10, 0xffff, "H"),
                                     (12, 0xffffffff, "I"), (16, 0xffffffff, "I"),
                                     (16, 1, "I"), (20, 1, "H")):
            changed = bytearray(raw)
            struct.pack_into("<" + width, changed, end + offset, value)
            mutations.append(bytes(changed))
        lying_both = bytearray(raw)
        struct.pack_into("<HH", lying_both, end + 8, 1, 1)
        mutations.append(bytes(lying_both))
        for offset, value, width in ((6, 45, "H"), (34, 1, "H"), (42, central, "I"),
                                     (28, 600, "H"), (30, 0xffff, "H")):
            changed = bytearray(raw)
            struct.pack_into("<" + width, changed, central + offset, value)
            mutations.append(bytes(changed))
        # ZIP64 extras must be rejected even when all 32-bit fields look normal.
        info = zipfile.ZipInfo("a.txt")
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        info.extra = struct.pack("<HHQ", 1, 8, 1)
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr(info, b"a")
        mutations.append(self.archive.read_bytes())
        constructor = assets.zipfile.ZipFile
        for number, invalid in enumerate(mutations):
            self.archive.write_bytes(invalid)
            with self.subTest(number=number), patch.object(assets.zipfile, "ZipFile", wraps=constructor) as parsed, \
                 self.assertRaises(FeatureError):
                assets.review_archive(self.archive)
            parsed.assert_not_called()
        self.archive.write_bytes(raw)
        with patch.object(assets, "MAX_CENTRAL_BYTES", 46), \
             patch.object(assets.zipfile, "ZipFile", wraps=constructor) as parsed, \
             self.assertRaises(FeatureError):
            assets.review_archive(self.archive)
        parsed.assert_not_called()

    def test_central_preflight_accepts_bounded_stored_deflated_and_directory_entries(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            self.zip([("web/", b"", stat.S_IFDIR | 0o700), ("web/index.html", b"<p>fixture</p>"),
                      ("LICENSE", b"fixture")], compression=compression)
            review = assets.review_archive(self.archive)
            self.assertEqual(tuple(entry.name for entry in review.entries), ("web/index.html", "LICENSE"))

    def test_source_symlink_hardlink_and_changed_checksum_are_rejected(self):
        self.zip()
        link = self.root / "alias.zip"
        link.symlink_to(self.archive)
        with self.assertRaises(FeatureError):
            assets.review_archive(link)
        link.unlink(); os.link(self.archive, link)
        with self.assertRaises(FeatureError):
            assets.review_archive(self.archive)
        link.unlink()
        with self.assertRaises(FeatureError):
            assets.review_archive(self.archive, expected_sha256="0" * 64)

    def test_install_requires_explicit_license_and_active_content_review_before_writes(self):
        review = assets.review_archive(self.zip())
        for options in ({"license_reviewed": False}, {"web_content_reviewed": False}, {"license_id": None}):
            with self.subTest(options=tuple(options)), self.assertRaises(FeatureError):
                self.install(review, **options)
        self.assertFalse(self.content.exists())

    def test_explicit_install_creates_only_private_files_and_never_runtime_ready(self):
        review = assets.review_archive(self.zip())
        result = self.install(review)
        pack = self.content / "fixture_pack"
        self.assertEqual(result["status"], "INSTALLED_FOR_REVIEW")
        self.assertEqual(result["runtime"], "NOT_RUN")
        self.assertFalse(result["content_executed"])
        self.assertEqual((pack / "index.html").read_bytes(), b"<p>synthetic game</p>")
        self.assertEqual(stat.S_IMODE(pack.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in pack.iterdir()))
        manifest = json.loads((pack / ".bmo-game-install.json").read_bytes())
        self.assertFalse(manifest["runtime_verified"])
        self.assertNotIn(str(self.root), json.dumps(manifest))
        self.assertNoStage()

    def test_source_changed_after_review_is_rejected_before_install(self):
        review = assets.review_archive(self.zip())
        self.zip([("index.html", b"changed fixture")])
        with self.assertRaises(FeatureError):
            self.install(review)
        self.assertFalse(self.content.exists())

    def test_existing_install_is_preserved_unless_replacement_is_explicit_and_backup_retained(self):
        first = assets.review_archive(self.zip())
        self.install(first)
        second = assets.review_archive(self.zip([("index.html", b"new fixture")]))
        with self.assertRaises(FeatureError):
            self.install(second)
        self.assertEqual((self.content / "fixture_pack/index.html").read_bytes(), b"<p>synthetic game</p>")
        result = self.install(second, replace_existing=True)
        self.assertTrue(result["rollback_available"])
        self.assertEqual((self.content / result["rollback_id"] / "index.html").read_bytes(), b"<p>synthetic game</p>")
        self.assertEqual((self.content / "fixture_pack/index.html").read_bytes(), b"new fixture")

    def test_extraction_failure_cleans_own_stage_and_leaves_original_archive_unchanged(self):
        review = assets.review_archive(self.zip())
        original = assets._stage_file
        count = [0]
        def fail_later(*args):
            count[0] += 1
            if count[0] == 2:
                raise OSError("private fixture error")
            return original(*args)
        with patch.object(assets, "_stage_file", side_effect=fail_later), self.assertRaises(FeatureError) as caught:
            self.install(review)
        self.assertNotIn("private", str(caught.exception))
        self.assertFalse((self.content / "fixture_pack").exists())
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), review.sha256)
        self.assertNoStage()

    def test_publication_failure_restores_previous_version_without_deleting_backups(self):
        self.install(assets.review_archive(self.zip()))
        review = assets.review_archive(self.zip([("index.html", b"replacement")]))
        original = assets.os.rename
        def fail_publish(source, target, **kwargs):
            if ".stage-" in source:
                raise OSError("private publication failure")
            return original(source, target, **kwargs)
        with patch.object(assets.os, "rename", side_effect=fail_publish), self.assertRaises(FeatureError) as caught:
            self.install(review, replace_existing=True)
        self.assertEqual(caught.exception.code, "game_install_rolled_back")
        self.assertEqual((self.content / "fixture_pack/index.html").read_bytes(), b"<p>synthetic game</p>")
        self.assertNoStage()

    def test_failure_after_publish_rolls_back_complete_directory(self):
        self.install(assets.review_archive(self.zip()))
        review = assets.review_archive(self.zip([("index.html", b"replacement")]))
        original = assets.os.fsync
        root_inode, failed = self.content.stat().st_ino, [False]
        def fail_root_once(fd):
            if os.fstat(fd).st_ino == root_inode and not failed[0]:
                failed[0] = True
                raise OSError("private fsync fixture")
            return original(fd)
        with patch.object(assets.os, "fsync", side_effect=fail_root_once), self.assertRaises(FeatureError):
            self.install(review, replace_existing=True)
        self.assertEqual((self.content / "fixture_pack/index.html").read_bytes(), b"<p>synthetic game</p>")
        self.assertNoStage()

    def test_interrupt_immediately_after_each_rename_recovers_original_directory(self):
        self.install(assets.review_archive(self.zip()))
        review = assets.review_archive(self.zip([("index.html", b"replacement")]))
        original = assets.os.rename
        for phase in ("old", "new"):
            interrupted = [False]
            def interrupt_after_rename(source, target, **kwargs):
                result = original(source, target, **kwargs)
                if not interrupted[0] and ((phase == "old" and source == "fixture_pack")
                        or (phase == "new" and ".stage-" in source)):
                    interrupted[0] = True
                    raise KeyboardInterrupt()
                return result
            with self.subTest(phase=phase), patch.object(assets.os, "rename", side_effect=interrupt_after_rename), \
                 self.assertRaises(FeatureError) as caught:
                self.install(review, replace_existing=True)
            self.assertEqual(caught.exception.code, "game_install_rolled_back")
            self.assertEqual((self.content / "fixture_pack/index.html").read_bytes(), b"<p>synthetic game</p>")
            self.assertNoStage()

    def test_failed_rollback_retains_old_backup_and_stage_for_manual_recovery(self):
        self.install(assets.review_archive(self.zip()))
        review = assets.review_archive(self.zip([("index.html", b"replacement")]))
        original = assets.os.rename
        def refuse_publish_and_restore(source, target, **kwargs):
            if ".stage-" in source or ".rollback-" in source:
                raise OSError("synthetic persistent rename failure")
            return original(source, target, **kwargs)
        with patch.object(assets.os, "rename", side_effect=refuse_publish_and_restore), \
             self.assertRaises(FeatureError) as caught:
            self.install(review, replace_existing=True)
        self.assertEqual(caught.exception.code, "game_rollback_pending")
        backups = list(self.content.glob(".fixture_pack.rollback-*"))
        stages = list(self.content.glob(".fixture_pack.stage-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(stages), 1)
        self.assertEqual((backups[0] / "index.html").read_bytes(), b"<p>synthetic game</p>")
        self.assertEqual((stages[0] / "index.html").read_bytes(), b"replacement")

    def test_staging_and_rollback_name_collisions_never_delete_existing_directories(self):
        self.content.mkdir(mode=0o700)
        existing = self.content / ".fixture_pack.stage-fixed"
        existing.mkdir(mode=0o700); (existing / "keep.txt").write_bytes(b"existing backup")
        review = assets.review_archive(self.zip())
        with patch.object(assets.secrets, "token_hex", return_value="fixed"), self.assertRaises(FeatureError):
            self.install(review)
        self.assertEqual((existing / "keep.txt").read_bytes(), b"existing backup")
        (existing / "keep.txt").unlink(); existing.rmdir()
        self.install(review)
        backup = self.content / ".fixture_pack.rollback-fixed"
        backup.mkdir(mode=0o700); (backup / "keep.txt").write_bytes(b"older rollback")
        with patch.object(assets.secrets, "token_hex", return_value="fixed"), self.assertRaises(FeatureError):
            self.install(review, replace_existing=True)
        self.assertEqual((backup / "keep.txt").read_bytes(), b"older rollback")

    def test_symlink_destination_and_unverified_root_are_rejected(self):
        review = assets.review_archive(self.zip())
        self.content.mkdir(mode=0o755)
        self.content.chmod(0o755)
        with self.assertRaises(FeatureError):
            self.install(review)
        self.content.chmod(0o700)
        outside = self.root / "outside"; outside.mkdir()
        (self.content / "fixture_pack").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FeatureError):
            self.install(review, replace_existing=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_install_lock_refuses_without_extraction(self):
        review = assets.review_archive(self.zip())
        self.content.mkdir(mode=0o700)
        fd = os.open(self.content / ".game-install.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(FeatureError) as caught:
                self.install(review)
            self.assertEqual(caught.exception.code, "game_install_busy")
            self.assertFalse((self.content / "fixture_pack").exists())
        finally:
            os.close(fd)

    def test_cli_default_review_is_readonly_and_install_omissions_are_safe(self):
        self.zip()
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--archive", str(self.archive), "--dry-run"]), 0)
        self.assertEqual(json.loads(output.getvalue())["runtime"], "NOT_RUN")
        self.assertFalse(self.content.exists())
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--archive", str(self.archive), "--install"]), 2)
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == "__main__":
    unittest.main()
