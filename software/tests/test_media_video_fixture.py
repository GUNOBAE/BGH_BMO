"""Generated AVI headers and a silent clock only; no codec/player/browser."""
from dataclasses import asdict, replace
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media import AlsaWavBackend, FakeMediaBackend, MediaFeature, import_manifest
from bmo_ai.features.media.assets import AssetHandle
from bmo_ai.features.media.video_fixture import (
    FakeVideoTimelineBackend, make_video_fixture, synthetic_avi_bytes, validate_avi_bytes,
)


class VideoFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.now = 0.0
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("Player forbidden")))
        self.enterContext(patch("bmo_ai.private_credentials.resolve_credentials", side_effect=AssertionError("Credentials forbidden")))

    def feature(self, items, backend):
        feature = MediaFeature(self.root, items, backend=backend, clock=lambda: self.now)
        self.addCleanup(feature.close)
        return feature

    def start(self, feature, item, request="video:1", generation=1):
        return feature.start(item.id, request_id=request, mode_generation=generation)

    def test_generated_header_private_file_and_manifest_measure_duration(self):
        item = make_video_fixture(self.root, width=7, height=5, frames=9, fps=3)
        path = self.root / item.path
        raw = path.read_bytes()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), item.sha256)
        meta = validate_avi_bytes(raw)
        self.assertEqual((meta["width"], meta["height"], meta["frames"], meta["fps"]), (7, 5, 9, 3))
        self.assertEqual((meta["duration"], meta["audio_streams"], meta["actual_decoder_verified"]), (3, 0, False))
        payload = {key: value for key, value in asdict(item).items() if value is not None}
        imported = import_manifest({"version": 1, "items": [payload]}, content_root=self.root)
        with AssetHandle(self.root, replace(imported[0], duration=None)) as handle:
            self.assertEqual(handle.duration, 3)
            self.assertEqual(os.read(handle.fd, 4), b"RIFF")

    def test_empty_default_catalogue_and_default_backends_never_enable_video(self):
        item = make_video_fixture(self.root)
        empty = self.feature((), FakeMediaBackend())
        self.assertEqual(empty.catalog(), ())
        for backend in (None, FakeMediaBackend(), AlsaWavBackend(),
                        AlsaWavBackend(enabled=True, volume_reviewed=True)):
            with self.subTest(backend=type(backend).__name__):
                feature = self.feature((item,), backend)
                result = self.start(feature, item)
                self.assertEqual(result.status, "rejected")
                self.assertNotEqual(feature.catalog()[0].readiness, "READY")
                self.assertIsNone(feature._handle)
        verified_alsa = self.feature((item,), AlsaWavBackend(enabled=True, volume_reviewed=True))
        self.assertEqual(verified_alsa.catalog()[0].readiness, "UNSUPPORTED")

    def test_injected_fake_timeline_play_pause_resume_next_end_stop(self):
        first = make_video_fixture(self.root, asset_id="video_a", frames=4, fps=2)
        second = make_video_fixture(self.root, asset_id="video_b", frames=2, fps=2)
        backend = FakeVideoTimelineBackend(clock=lambda: self.now)
        feature = self.feature((first, second), backend)
        self.assertTrue(all(item.readiness == "READY" and item.simulated for item in feature.catalog()))
        self.assertIn("synthetic_video_timeline", feature.snapshot()["capabilities"])
        result = self.start(feature, first)
        self.assertTrue(result.simulated and result.completed)
        self.assertFalse(result.executed)
        self.assertEqual(feature.poll()[0].kind, "media_started")
        self.now = .5
        self.assertTrue(feature.pause().completed)
        self.now = 50
        self.assertEqual(feature.snapshot()["position"], .5)
        self.assertEqual(feature.poll(), ())
        self.assertTrue(feature.resume().completed)
        self.now = 50.25
        self.assertAlmostEqual(feature.snapshot()["position"], .75)
        old_handle = feature._handle
        self.assertTrue(feature.next(request_id="video:2", mode_generation=2).completed)
        self.assertEqual(feature.snapshot()["active_id"], second.id)
        self.assertEqual(feature.poll()[0].mode_generation, 2)
        self.assertIsNone(old_handle.fd)
        self.assertIsNot(feature._handle, old_handle)
        self.now += 1.01
        self.assertEqual(feature.poll()[-1].kind, "media_ended")
        self.assertIsNone(feature.snapshot()["active_id"])
        self.assertTrue(self.start(feature, first, "video:3", 3).completed)
        fd = feature._handle.fd
        self.assertTrue(feature.stop().completed)
        self.assertEqual(feature.stop().code, "already_stopped")
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_tamper_missing_hash_and_duration_mismatch_do_not_start(self):
        item = make_video_fixture(self.root)
        for value, code in ((replace(item, duration=20), "media_duration_mismatch"),
                            (replace(item, sha256="0" * 64), "media_hash_mismatch")):
            with self.subTest(code=code), self.assertRaises(FeatureError) as caught:
                AssetHandle(self.root, value)
            self.assertEqual(caught.exception.code, code)
        feature = self.feature((item,), FakeVideoTimelineBackend(clock=lambda: self.now))
        (self.root / item.path).write_bytes(b"changed")
        self.assertEqual(self.start(feature, item).code, "media_hash_mismatch")
        self.assertIsNone(feature._handle)
        missing = replace(item, id="missing_video", path="missing.avi")
        absent = self.feature((missing,), FakeVideoTimelineBackend())
        self.assertEqual(absent.catalog()[0].readiness, "ASSET_PENDING")

    def test_malformed_truncated_riff_frame_index_and_codec_are_rejected(self):
        raw = synthetic_avi_bytes()
        mutants = [b"", raw[:80], b"XXXX" + raw[4:], raw + b"extra"]
        bad_size = bytearray(raw)
        struct.pack_into("<I", bad_size, 4, len(raw))
        mutants.append(bytes(bad_size))
        for tag, replacement in ((b"DIB ", b"H264"), (b"00db", b"01wb"),
                                 (b"movi", b"xxxx"), (b"avih", b"xxxx")):
            mutants.append(raw.replace(tag, replacement, 1))
        index = raw.rfind(b"idx1")
        bad_offset = bytearray(raw)
        struct.pack_into("<I", bad_offset, index + 16, 4000000)
        mutants.append(bytes(bad_offset))
        stream_header = raw.index(b"strh") + 8
        for offset, value in ((8, 1), (16, 1), (28, 1000), (44, 1), (48, 1)):
            changed_header = bytearray(raw)
            struct.pack_into("<I", changed_header, stream_header + offset, value)
            mutants.append(bytes(changed_header))
        main_header = raw.index(b"avih") + 8
        changed_flags = bytearray(raw)
        struct.pack_into("<I", changed_flags, main_header + 12, 0x110)
        mutants.append(bytes(changed_flags))
        for number, invalid in enumerate(mutants):
            with self.subTest(number=number), self.assertRaises(FeatureError):
                validate_avi_bytes(invalid)
        path = self.root / "bad.avi"
        path.write_bytes(mutants[-1])
        item = make_video_fixture(self.root, asset_id="valid_video")
        invalid = replace(item, path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaises(FeatureError):
            AssetHandle(self.root, invalid)

    def test_generator_parameter_bounds_and_existing_files_are_preserved(self):
        for changes in ({"width": 0}, {"height": 65}, {"fps": 31}, {"frames": 121}, {"fps": True}):
            with self.subTest(changes=changes), self.assertRaises(FeatureError):
                synthetic_avi_bytes(**changes)
        item = make_video_fixture(self.root)
        previous = (self.root / item.path).read_bytes()
        with self.assertRaises(FeatureError):
            make_video_fixture(self.root)
        self.assertEqual((self.root / item.path).read_bytes(), previous)
        (self.root / "alias.avi").symlink_to(self.root / item.path)
        with self.assertRaises(FeatureError):
            make_video_fixture(self.root, asset_id="alias")
        with self.assertRaises(FeatureError):
            AssetHandle(self.root, replace(item, path="alias.avi"))

    def test_generation_failure_cleans_only_new_owned_file(self):
        with patch("bmo_ai.features.media.video_fixture.os.fsync", side_effect=OSError("synthetic")):
            with self.assertRaises(FeatureError) as caught:
                make_video_fixture(self.root)
        self.assertEqual(caught.exception.code, "video_fixture_failed")
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
