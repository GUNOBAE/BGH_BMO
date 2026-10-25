"""Local generated WAV snapshots/import/retirement; network and players denied."""
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from bmo_ai import media_assets
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media import FakeMediaBackend, MediaFeature
from bmo_ai.features.media import library


class MediaLibraryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-media-library-test-")))
        self.source = self.root / "generated.wav"
        with wave.open(str(self.source), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 3200)
        self.content = self.root / "content"
        self.review = library.review_wav(self.source)
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No audio player")))

    def install(self, review=None, media_id="song_a", **kwargs):
        settings = {"title": "Generated silent WAV", "license_id": "test_generated", "license_reviewed": True}
        settings.update(kwargs)
        return library.install_wav(self.review if review is None else review, self.content, media_id, **settings)

    def test_review_and_missing_catalogue_are_read_only(self):
        before = set(self.root.iterdir())
        report = self.review.public()
        self.assertEqual(report["media_type"], "audio/wav")
        self.assertEqual(report["duration"], .2)
        self.assertEqual(report["playback"], "NOT_RUN")
        self.assertNotIn(str(self.source), repr(self.review))
        self.assertEqual(library.load_library(self.content), ())
        self.assertEqual(set(self.root.iterdir()), before)

    def test_install_publishes_private_verified_library_without_playing(self):
        result = self.install()
        self.assertEqual(result["status"], "IMPORTED_FOR_REVIEW")
        self.assertFalse(result["runtime_enabled"])
        items = library.load_library(self.content)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].sha256, self.review.sha256)
        self.assertEqual(stat.S_IMODE(self.content.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in self.content.iterdir()))
        feature = MediaFeature(self.content, items, backend=FakeMediaBackend())
        try:
            self.assertEqual(feature.catalog()[0].readiness, "READY")
            self.assertEqual(feature.snapshot()["cue_kind"], "none")
            self.assertTrue(feature.start(items[0].id, request_id="music:1", mode_generation=1).simulated)
        finally:
            feature.close()

    def test_source_replacement_after_review_uses_immutable_snapshot(self):
        original = self.source.read_bytes()
        self.source.write_bytes(b"new source content")
        self.install()
        asset = library.load_library(self.content)[0]
        self.assertEqual((self.content / asset.path).read_bytes(), original)

    def test_license_hash_codec_and_snapshot_metadata_must_match(self):
        for changes in ({"license_reviewed": False}, {"license_id": None}, {"media_id": "../bad"}):
            settings = dict(changes)
            media_id = settings.pop("media_id", "song_a")
            with self.assertRaises(FeatureError):
                self.install(media_id=media_id, **settings)
        for review in (replace(self.review, _raw=b"bad"), replace(self.review, duration=3),
                       replace(self.review, sha256="0" * 64)):
            with self.assertRaises(FeatureError):
                self.install(review)
        self.assertFalse(self.content.exists())

    def test_existing_song_audio_or_symlink_is_never_overwritten(self):
        self.install()
        before = {p.name: p.read_bytes() for p in self.content.iterdir()}
        with self.assertRaises(FeatureError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "media_import_exists")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.content.iterdir()})
        (self.content / "song_b.song.json").symlink_to(self.source)
        with self.assertRaises(FeatureError):
            self.install(media_id="song_b")
        self.assertTrue((self.content / "song_b.song.json").is_symlink())

    def test_source_symlink_parent_link_fifo_hardlink_and_oversize_are_refused(self):
        alias = self.root / "alias.wav"; alias.symlink_to(self.source)
        alias_dir = self.root / "alias_dir"; alias_dir.symlink_to(self.root, target_is_directory=True)
        fifo = self.root / "fifo.wav"; os.mkfifo(fifo)
        hard = self.root / "hard.wav"; os.link(self.source, hard)
        for path in (alias, alias_dir / "generated.wav", fifo, hard):
            with self.subTest(path=path.name), self.assertRaises(FeatureError):
                library.review_wav(path)
        hard.unlink()
        with patch.object(library, "MAX_IMPORT_BYTES", 45), self.assertRaises(FeatureError):
            library.review_wav(self.source)

    def test_malformed_truncated_and_unsupported_source_cannot_be_reviewed(self):
        for suffix, raw in ((".mp3", b"ID3"), (".wav", b"x" * 50),
                            (".wav", self.source.read_bytes()[:-5])):
            path = self.root / ("invalid" + suffix); path.write_bytes(raw)
            with self.assertRaises(FeatureError):
                library.review_wav(path)
        with self.assertRaises(FeatureError):
            library.review_wav(self.source, expected_sha256="0" * 64)
        with self.assertRaises(FeatureError):
            library.review_wav(self.source, expected_sha256="invalid")

    def test_unsafe_library_root_and_permission_are_refused_without_repair(self):
        self.content.mkdir(mode=0o750)
        with self.assertRaises(FeatureError):
            self.install()
        self.assertEqual(stat.S_IMODE(self.content.stat().st_mode), 0o750)
        for root in (self.root / ".config" / "bmo", self.root / ".." / "escape"):
            with self.assertRaises(FeatureError):
                library.load_library(root)

    def test_disk_shortage_and_publish_failure_roll_back_only_own_files(self):
        self.content.mkdir(mode=0o700)
        marker = self.content / "unrelated"; marker.write_bytes(b"keep")
        with patch.object(library.os, "fstatvfs", return_value=SimpleNamespace(f_bavail=1, f_frsize=1)), \
             self.assertRaises(FeatureError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "media_disk_space_low")
        original_link = library.os.link
        def fail_second(source, target, **kwargs):
            if target.endswith(".song.json"):
                raise OSError("private destination details")
            return original_link(source, target, **kwargs)
        with patch.object(library.os, "link", side_effect=fail_second), self.assertRaises(FeatureError):
            self.install()
        self.assertEqual(marker.read_bytes(), b"keep")
        self.assertFalse(any(p.name.endswith((".wav", ".song.json")) or p.name.startswith(".media-stage-")
                             for p in self.content.iterdir()))

    def test_metadata_permissions_tampering_duplicates_and_hash_mismatch_block_load(self):
        self.install()
        metadata = self.content / "song_a.song.json"
        before = metadata.read_bytes()
        metadata.chmod(0o640)
        with self.assertRaises(FeatureError): library.load_library(self.content)
        metadata.chmod(0o600)
        metadata.write_bytes(b'{"version":1,"version":1}')
        with self.assertRaises(FeatureError): library.load_library(self.content)
        metadata.write_bytes(before)
        asset = library.load_library(self.content)[0]
        (self.content / asset.path).write_bytes(b"private tamper")
        with self.assertRaises(FeatureError): library.load_library(self.content)

    def test_retirement_hides_song_but_preserves_audio_metadata_and_rejects_wrong_sha(self):
        self.install()
        asset = library.load_library(self.content)[0]
        audio = (self.content / asset.path).read_bytes()
        metadata = (self.content / "song_a.song.json").read_bytes()
        with self.assertRaises(FeatureError):
            library.retire_import(self.content, "song_a", expected_sha256="0" * 64)
        result = library.retire_import(self.content, "song_a", expected_sha256=self.review.sha256)
        self.assertTrue(result["audio_preserved"] and result["metadata_preserved"])
        self.assertEqual(library.load_library(self.content), ())
        self.assertEqual((self.content / asset.path).read_bytes(), audio)
        retired = list(self.content.glob(".retired-*.json"))
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0].read_bytes(), metadata)

    def test_retirement_fsync_failure_keeps_preserved_metadata(self):
        self.install()
        with patch.object(library.os, "fsync", side_effect=OSError("private fsync")), self.assertRaises(FeatureError):
            library.retire_import(self.content, "song_a", expected_sha256=self.review.sha256)
        self.assertEqual(len(list(self.content.glob(".retired-*.json"))), 1)
        self.assertEqual(len(list(self.content.glob("*.wav"))), 1)

    def test_cli_default_dryrun_install_and_retire_never_enable_player(self):
        with redirect_stdout(io.StringIO()) as output:
            code = media_assets.main([])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ASSET_PENDING")
        before = set(self.root.iterdir())
        with redirect_stdout(io.StringIO()) as output:
            code = media_assets.main(["--audio", str(self.source), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(set(self.root.iterdir()), before)
        with redirect_stdout(io.StringIO()) as output:
            code = media_assets.main(["--audio", str(self.source), "--content-root", str(self.content),
                "--sha256", self.review.sha256, "--media-id", "song_a", "--title", "Silent fixture",
                "--license-id", "test_generated", "--license-reviewed", "--install"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["runtime_enabled"])
        with redirect_stdout(io.StringIO()) as output:
            code = media_assets.main(["--retire", "--content-root", str(self.content),
                                     "--media-id", "song_a", "--sha256", self.review.sha256])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "RETIRED")

    def test_cli_errors_are_fixed_codes_without_path_source_or_traceback(self):
        with redirect_stdout(io.StringIO()) as output:
            code = media_assets.main(["--audio", str(self.root / "PRIVATE_SOURCE.wav"), "--install"])
        self.assertEqual(code, 2)
        report = output.getvalue()
        self.assertEqual(json.loads(report)["status"], "REJECTED")
        self.assertNotIn("PRIVATE_SOURCE", report)
        self.assertNotIn(str(self.root), report)


if __name__ == "__main__":
    unittest.main()
