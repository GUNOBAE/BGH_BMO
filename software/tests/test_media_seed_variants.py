"""Synthetic short MP4/PCM only: private seed adaptation and one song/two modes."""
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

from bmo_ai import media_assets
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media import FakeMediaBackend, MediaFeature
from bmo_ai.features.media.library import load_library, retire_import
from bmo_ai.features.media.seed import review_media_seed, install_media_seed


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class MediaSeedVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._folder = tempfile.TemporaryDirectory(prefix="bmo-synthetic-seed-")
        cls.root = Path(cls._folder.name)
        cls.face, cls.mp3, cls.video = (cls.root / name for name in ("face.wav", "song.mp3", "video.mp4"))
        def run(args):
            result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
            if result.returncode:
                raise AssertionError("Synthetic media fixture encode failed")
        run(["-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000", "-ac", "2", "-c:a", "pcm_s16le", str(cls.face)])
        run(["-i", str(cls.face), "-c:a", "libmp3lame", str(cls.mp3)])
        # Deliberately different synthetic audio proves Video uses its own
        # embedded soundtrack rather than the separate face/MP3 source.
        run(["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=1", "-f", "lavfi", "-i",
             "sine=frequency=880:duration=1:sample_rate=48000", "-c:v", "libx264", "-threads", "1",
             "-c:a", "aac", "-ac", "2", "-shortest", "-movflags", "+faststart", str(cls.video)])
        cls.hashes = {role: hashlib.sha256(path.read_bytes()).hexdigest()
                      for role, path in (("face_wav", cls.face), ("mp3", cls.mp3), ("video", cls.video))}

    @classmethod
    def tearDownClass(cls):
        cls._folder.cleanup()

    def setUp(self):
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-seed-review-")))
        self.zip = self.temp / "user-given-seed.zip"
        self.content = self.temp / "private-content"
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network")))

    def archive(self, *, unsafe=None, energy=None, missing=False):
        with zipfile.ZipFile(self.zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("handoff/MEDIA_SEED.json", json.dumps({"schema": "handoff_only", "song_id": "space_potatoes"}))
            archive.writestr("handoff/FILES_SHA256.json", json.dumps({}))
            for path in (self.face, self.mp3, self.video):
                if missing and path == self.video:
                    continue
                archive.write(path, "provided/" + path.name)
            archive.writestr("untrusted.py", "raise AssertionError('Never execute archive scripts')")
            if energy is not None:
                archive.writestr("handoff/whole_mix_energy.json", json.dumps(energy))
            if unsafe:
                archive.writestr(unsafe, "escape")
        return hashlib.sha256(self.zip.read_bytes()).hexdigest()

    def review(self, digest):
        return review_media_seed(self.zip, expected_sha256=digest, component_hashes=self.hashes)

    def test_adapter_decodes_embedded_video_audio_and_publishes_one_catalog_entry(self):
        digest = self.archive(energy={"source_wav_sha256": self.hashes["face_wav"], "step_seconds": .02,
                                      "values": [.1] * 50})
        original = self.zip.read_bytes()
        with self.review(digest) as review:
            self.assertEqual(review.energy_status, "HASH_BOUND_PREPARED_WHOLE_MIX_ENERGY")
            self.assertEqual(review.public()["variants"], ["face", "video"])
            self.assertNotIn(str(self.temp), repr(review))
            with self.assertRaises(FeatureError):
                install_media_seed(review, self.content)
            result = install_media_seed(review, self.content, private_use_confirmed=True)
            self.assertEqual(result["status"], "IMPORTED_FOR_REVIEW")
            self.assertEqual(result["playback"], "NOT_RUN")
            self.assertFalse(result["runtime_enabled"])
        self.assertEqual(self.zip.read_bytes(), original)
        items = load_library(self.content)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual((item.id, item.variants), ("space_potatoes", ("face", "video")))
        self.assertEqual((self.content / item.path).read_bytes(), self.face.read_bytes())
        self.assertNotEqual(item.sha256, item.video_audio_sha256)
        metadata = json.loads((self.content / "space_potatoes.song.json").read_bytes())
        self.assertEqual(metadata["provenance"]["video_audio_source"], "verified_mp4_embedded_audio")
        self.assertEqual(metadata["provenance"]["public_distribution_rights"], "NOT_VERIFIED")
        self.assertEqual(metadata["asset"]["cue_kind"], "whole_mix_energy_approximation")
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.content.iterdir()))
        self.assertEqual(self.content.stat().st_mode & 0o777, 0o700)

    def test_actual_video_bytes_stay_pinned_and_audio_variant_never_uses_face_pcm(self):
        digest = self.archive()
        with self.review(digest) as review:
            install_media_seed(review, self.content, private_use_confirmed=True)
        item = load_library(self.content)[0]
        now = [0.]
        backend = FakeMediaBackend(clock=lambda: now[0], pausable=True)
        feature = MediaFeature(self.content, (item,), backend=backend)
        self.addCleanup(feature.close)
        self.assertEqual(len(feature.catalog()), 1)
        result = feature.start(item.id, request_id="media:video1", mode_generation=1, variant="video")
        self.assertTrue(result.completed)
        self.assertEqual(backend.handle.asset.sha256, item.video_audio_sha256)
        self.assertFalse(backend.handle.cues)
        fd, size = feature.open_video(request_id="media:video1", mode_generation=1)
        self.addCleanup(os.close, fd)
        self.assertEqual(os.pread(fd, size, 0), self.video.read_bytes())
        (self.content / item.video_path).rename(self.content / "original-video.mp4")
        (self.content / item.video_path).write_bytes(b"replacement")
        another, _ = feature.open_video()
        try:
            self.assertEqual(os.pread(another, size, 0), self.video.read_bytes())
        finally:
            os.close(another)
        self.assertEqual(feature.snapshot()["display_mode"], "video")
        with self.assertRaises(FeatureError):
            feature.open_video(request_id="late:request", mode_generation=0)
        now[0] = .2
        feature.pause(); before = feature.snapshot()["playback_generation"]
        self.assertTrue(feature.seek(.5).completed)
        self.assertEqual(feature.snapshot()["state"], "paused")
        self.assertEqual(feature.snapshot()["position_seconds"], .5)
        self.assertGreater(feature.snapshot()["playback_generation"], before)
        self.assertTrue(feature.replay().completed)
        self.assertEqual(feature.snapshot()["state"], "playing")
        self.assertEqual(feature.snapshot()["position_seconds"], 0.)
        feature.stop()
        with self.assertRaises(FeatureError):
            feature.open_video()

    def test_face_energy_samples_share_audio_clock_pause_seek_and_close(self):
        digest = self.archive(energy={"wav_sha256": "0" * 64, "step_seconds": .02, "values": [1.] * 50})
        with self.review(digest) as review:
            self.assertEqual(review.energy_status, "MEASURED_FROM_VERIFIED_WHOLE_MIX_WAV")
            install_media_seed(review, self.content, private_use_confirmed=True)
        now = [0.]
        feature = MediaFeature(self.content, load_library(self.content), FakeMediaBackend(clock=lambda: now[0], pausable=True))
        self.addCleanup(feature.close)
        feature.start("space_potatoes", request_id="face:1", mode_generation=2)
        now[0] = .2
        cue = feature.cue_snapshot()
        self.assertEqual(cue["cue_kind"], "whole_mix_energy_approximation")
        self.assertAlmostEqual(cue["position"], .2)
        feature.pause()
        now[0] = 100
        self.assertAlmostEqual(feature.cue_snapshot()["position"], .2)
        self.assertEqual(feature.cue_snapshot()["mouth"], "closed")
        feature.seek(.7)
        self.assertAlmostEqual(feature.cue_snapshot()["position"], .7)
        feature.stop()
        self.assertIsNone(feature.cue_snapshot())

    def test_missing_component_traversal_wrong_hash_and_duplicate_reject_before_publish(self):
        for settings in ({"missing": True}, {"unsafe": "../escape"}):
            with self.subTest(settings=settings):
                digest = self.archive(**settings)
                with self.assertRaises(FeatureError), self.review(digest):
                    pass
                self.assertFalse(self.content.exists())
        self.archive()
        with self.assertRaises(FeatureError), self.review("0" * 64):
            pass
        with self.assertRaises(FeatureError), review_media_seed(self.zip):
            pass  # Published source hashes cannot be replaced by handoff JSON.

    def test_existing_content_never_overwritten_and_retirement_preserves_both_modes(self):
        digest = self.archive()
        with self.review(digest) as review:
            install_media_seed(review, self.content, private_use_confirmed=True)
            before = {path.name: path.read_bytes() for path in self.content.iterdir()}
            with self.assertRaises(FeatureError) as caught:
                install_media_seed(review, self.content, private_use_confirmed=True)
            self.assertEqual(caught.exception.code, "media_import_exists")
            self.assertEqual(before, {path.name: path.read_bytes() for path in self.content.iterdir()})
        item = load_library(self.content)[0]
        retire_import(self.content, item.id, expected_sha256=item.sha256)
        self.assertEqual(load_library(self.content), ())
        self.assertTrue((self.content / item.path).exists())
        self.assertTrue((self.content / item.video_path).exists())
        self.assertTrue((self.content / item.video_audio_path).exists())

    def test_cli_seed_defaults_to_review_and_requires_explicit_private_install(self):
        from contextlib import redirect_stdout
        digest = self.archive()
        with patch("bmo_ai.features.media.seed.SPACE_COMPONENTS", self.hashes):
            with redirect_stdout(io.StringIO()) as output:
                code = media_assets.main(["--media-seed", str(self.zip), "--sha256", digest])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["playback"], "NOT_RUN")
            self.assertFalse(self.content.exists())
            with redirect_stdout(io.StringIO()) as output:
                code = media_assets.main(["--media-seed", str(self.zip), "--sha256", digest, "--content-root", str(self.content),
                                          "--install"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["code"], "media_private_use_confirmation_required")


if __name__ == "__main__":
    unittest.main()
