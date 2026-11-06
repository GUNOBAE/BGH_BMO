"""Generated local PCM and silent backends; no songs, API or audio devices."""
from dataclasses import asdict, replace
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media import (AlsaWavBackend, FakeMediaBackend, MediaAsset,
                                  MediaFeature, import_manifest, make_fixture_media)
from bmo_ai.features.media.assets import AssetHandle


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.items = make_fixture_media(self.root)
        self.now = 0.0
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("Player forbidden")))

    def feature(self, *, items=None, backend=None):
        backend = backend if backend is not None else FakeMediaBackend(clock=lambda: self.now)
        feature = MediaFeature(self.root, self.items if items is None else items,
                               backend=backend, clock=lambda: self.now)
        self.addCleanup(feature.close)
        return feature

    def payload(self, **changes):
        value = asdict(self.items[0])
        value = {key: data for key, data in value.items() if data is not None}
        value.update(changes)
        return {"version": 1, "items": [value]}

    def start(self, feature, catalog_id="fixture_song_a", request_id="song:1", generation=1):
        return feature.start(catalog_id, request_id=request_id, mode_generation=generation)

    def test_empty_catalogue_never_creates_root_or_claims_user_songs(self):
        missing = self.root / "not_created"
        feature = MediaFeature(missing)
        self.addCleanup(feature.close)
        self.assertEqual(feature.catalog(), ())
        self.assertEqual(feature.snapshot()["readiness"], "ASSET_PENDING")
        self.assertFalse(missing.exists())
        self.assertEqual(self.start(feature).code, "unknown_media")

    def test_manifest_import_records_metadata_and_only_exposes_safe_catalogue(self):
        assets = import_manifest(self.payload(), content_root=self.root)
        self.assertEqual(assets, (self.items[0],))
        feature = self.feature(items=assets)
        item = feature.catalog()[0]
        self.assertEqual((item.readiness, item.simulated), ("READY", True))
        self.assertNotIn("path", item.public())
        self.assertNotIn("source", item.public())
        result = self.start(feature)
        self.assertTrue(result.simulated and result.completed)
        self.assertFalse(result.executed)
        self.assertEqual(result.completion, "simulated")
        self.assertEqual(feature.snapshot()["cue_kind"], "none")

    def test_manifest_rejects_fields_ids_absolute_traversal_url_and_commands(self):
        paths = ("/tmp/song.wav", "../song.wav", "nested/../song.wav", "./song.wav", "a//b.wav",
                 "https://invalid/song.wav", "file:///tmp/song.wav", "C:\\song.wav", "song.wav;echo bad",
                 "$(echo bad).wav", "percent%2fpath.wav")
        for path in paths:
            with self.subTest(path=path), self.assertRaises(FeatureError):
                import_manifest(self.payload(path=path), content_root=self.root)
        for changes in ({"id": "../song"}, {"command": "play anything"}, {"url": "https://invalid/"},
                        {"source": "https://invalid/"}, {"sha256": "not-a-hash"}, {"duration": True}):
            with self.subTest(fields=tuple(changes)), self.assertRaises(FeatureError):
                import_manifest(self.payload(**changes), content_root=self.root)
        for payload in ({"version": True, "items": []}, {"version": 1, "items": [], "command": "x"},
                        {"version": 1, "items": [asdict(self.items[0]), asdict(self.items[0])]}):
            with self.assertRaises(FeatureError):
                import_manifest(payload, content_root=self.root)

    def test_symlinks_in_file_directory_or_content_root_and_hardlinks_are_rejected(self):
        original = self.root / self.items[0].path
        (self.root / "alias.wav").symlink_to(original)
        (self.root / "alias_dir").symlink_to(self.root, target_is_directory=True)
        for path in ("alias.wav", "alias_dir/" + original.name):
            with self.subTest(path=path), self.assertRaises(FeatureError):
                import_manifest(self.payload(path=path), content_root=self.root)
        with tempfile.TemporaryDirectory() as directory:
            alias_root = Path(directory) / "root"
            alias_root.symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(FeatureError):
                import_manifest(self.payload(), content_root=alias_root)
        os.link(original, self.root / "hard.wav")
        with self.assertRaises(FeatureError):
            import_manifest(self.payload(path="hard.wav"), content_root=self.root)

    def test_bad_hash_duration_and_malformed_wav_do_not_enter_catalogue(self):
        for changes, code in (({"sha256": "0" * 64}, "media_hash_mismatch"),
                              ({"duration": 2.0}, "media_duration_mismatch")):
            with self.subTest(code=code), self.assertRaises(FeatureError) as error:
                import_manifest(self.payload(**changes), content_root=self.root)
            self.assertEqual(error.exception.code, code)
        bad = self.root / "bad.wav"
        bad.write_bytes(b"not-wave")
        with self.assertRaises(FeatureError):
            import_manifest(self.payload(path=bad.name, sha256=hashlib.sha256(bad.read_bytes()).hexdigest()),
                            content_root=self.root)

    def test_start_reverifies_tampering_and_missing_assets_are_not_ready(self):
        feature = self.feature()
        self.assertEqual(feature.catalog()[0].readiness, "READY")
        (self.root / self.items[0].path).write_bytes(b"tampered")
        backend = self.enterContext(patch.object(feature.backend, "start", wraps=feature.backend.start))
        result = self.start(feature)
        self.assertEqual((result.status, result.code), ("failed", "media_hash_mismatch"))
        backend.assert_not_called()
        self.assertEqual(feature.catalog()[0].readiness, "UNVERIFIED")
        self.assertTrue(feature.poll()[-1].data["resource_released"])
        missing = replace(self.items[1], id="missing_song", path="missing.wav")
        other = self.feature(items=(missing,))
        self.assertEqual(other.catalog()[0].readiness, "ASSET_PENDING")
        self.assertEqual(self.start(other, missing.id).code, "asset_pending")

    def test_playback_retains_verified_descriptor_when_path_is_replaced(self):
        feature = self.feature()
        self.start(feature)
        handle = feature._handle
        descriptor = handle.fd
        before = os.fstat(descriptor)
        path = self.root / self.items[0].path
        path.rename(self.root / "old.wav")
        path.write_bytes(b"replacement")
        self.assertEqual(os.fstat(descriptor).st_ino, before.st_ino)
        self.assertNotEqual(path.stat().st_ino, before.st_ino)
        os.lseek(descriptor, 0, os.SEEK_SET)
        self.assertEqual(os.read(descriptor, 4), b"RIFF")
        feature.stop()
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_pause_is_unsupported_unless_declared_and_clock_does_not_advance_when_paused(self):
        feature = self.feature()
        self.start(feature)
        self.assertEqual(feature.pause().status, "unsupported")
        self.assertEqual(feature.snapshot()["state"], "playing")
        feature.stop()
        backend = FakeMediaBackend(clock=lambda: self.now, pausable=True)
        feature = self.feature(backend=backend)
        self.start(feature)
        self.now = .2
        self.assertTrue(feature.pause().completed)
        self.now = 10
        self.assertEqual(feature.snapshot()["position"], .2)
        self.assertEqual(feature.poll()[0].kind, "media_started")
        self.assertTrue(feature.resume().completed)
        self.now = 10.1
        position = feature.snapshot()["position"]
        feature.resume()
        self.assertAlmostEqual(feature.snapshot()["position"], position)
        self.now = 10.4
        self.assertEqual(feature.poll()[-1].kind, "media_ended")

    def test_next_has_no_intermediate_terminal_event_and_deduplicates_end_request(self):
        feature = self.feature()
        self.start(feature); feature.poll()
        self.assertTrue(feature.next(request_id="song:2", mode_generation=1).completed)
        self.assertEqual(feature.snapshot()["active_id"], "fixture_song_b")
        self.assertEqual([e.kind for e in feature.poll()], ["media_started"])
        self.assertEqual(feature.next(request_id="song:2", mode_generation=1).code, "duplicate_request")
        result = feature.next(request_id="song:3", mode_generation=1)
        self.assertEqual(result.code, "end_of_catalog")
        self.assertIsNone(feature.snapshot()["active_id"])
        self.assertTrue(feature.poll()[-1].data["resource_released"])
        self.assertEqual(feature.next(request_id="song:3", mode_generation=1).code, "duplicate_request")
        self.assertIsNone(feature.snapshot()["active_id"])

    def test_end_failure_stop_and_new_track_recover_with_closed_descriptors(self):
        feature = self.feature()
        self.start(feature); feature.poll()
        descriptor = feature._handle.fd
        self.now = .5
        events = feature.poll()
        self.assertEqual(events[-1].kind, "media_ended")
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.start(feature, "fixture_song_b", "song:2", 2)
        feature.backend.fail()
        events = feature.poll()
        self.assertEqual(events[-1].kind, "media_failed")
        self.assertTrue(events[-1].data["resource_released"])
        self.assertTrue(self.start(feature, request_id="song:3", generation=3).completed)
        feature.stop(); feature.poll()
        self.assertEqual(feature.stop().code, "already_stopped")
        self.assertEqual(feature.poll(), ())

    def test_unsupported_codec_and_default_real_backend_are_not_enabled(self):
        mp3 = self.root / "sample.mp3"
        mp3.write_bytes(b"synthetic codec fixture")
        asset = MediaAsset("codec_song", "Unsupported codec", "synthetic_fixture", mp3.name,
                           hashlib.sha256(mp3.read_bytes()).hexdigest(), "audio/mpeg")
        feature = self.feature(items=(asset,))
        self.assertEqual(feature.catalog()[0].readiness, "UNSUPPORTED")
        self.assertEqual(self.start(feature, asset.id).code, "unsupported_codec")
        other = self.feature(backend=AlsaWavBackend())
        self.assertEqual(other.catalog()[0].readiness, "UNVERIFIED")
        self.assertEqual(self.start(other).code, "media_backend_unverified")

    def test_optional_cue_hash_schema_and_same_clock_are_explicit_not_generated_vocals(self):
        cue = self.root / "cue.json"
        cue.write_text(json.dumps([{"time": 0, "mouth": "closed"}, {"time": .1, "mouth": "small"}]))
        payload = self.payload(cue_path=cue.name, cue_sha256=hashlib.sha256(cue.read_bytes()).hexdigest())
        assets = import_manifest(payload, content_root=self.root)
        feature = self.feature(items=assets)
        self.start(feature)
        self.assertEqual(feature.snapshot()["cue_kind"], "provided")
        self.assertEqual(feature._handle.cues[1]["time"], .1)
        feature.stop()
        cue.write_text('[{"time":-0.1,"mouth":"closed"}]')
        with self.assertRaises(FeatureError):
            import_manifest(self.payload(cue_path=cue.name, cue_sha256=hashlib.sha256(cue.read_bytes()).hexdigest()),
                            content_root=self.root)

    def test_fixture_creation_never_overwrites_existing_assets_and_import_is_atomic(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with self.assertRaises(FeatureError):
            make_fixture_media(self.root)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)
        feature = self.feature()
        original = feature.catalog()
        with self.assertRaises(FeatureError):
            feature.import_manifest(self.payload(sha256="0" * 64))
        self.assertEqual(feature.catalog(), original)

    def test_explicit_alsa_adapter_only_passes_verified_fd_to_mocked_owned_runner(self):
        backend = AlsaWavBackend(enabled=True, volume_reviewed=True, clock=lambda: self.now)
        feature = self.feature(backend=backend)
        runner = Mock()
        runner.poll.return_value = None
        with patch("bmo_ai.features.games.OwnedProcessRunner", return_value=runner) as factory:
            result = self.start(feature)
        self.assertFalse(result.simulated)
        self.assertTrue(result.executed)
        self.assertFalse(result.completed)
        descriptor = feature._handle.fd
        self.assertEqual(factory.call_args.args[0][-1], f"/proc/self/fd/{descriptor}")
        self.assertEqual(factory.call_args.kwargs["pass_fds"], (descriptor,))
        self.assertNotIn("gain", factory.call_args.kwargs)
        self.now += .2
        self.assertEqual(feature.pause().status, "succeeded")
        runner.pause.assert_called_once()
        paused = backend.position()
        self.now += 3
        self.assertEqual(backend.position(), paused)
        self.assertEqual(feature.resume().status, "succeeded")
        runner.resume.assert_called_once()
        self.now += .1
        self.assertAlmostEqual(backend.position(), paused + .1)
        feature.stop()
        runner.stop.assert_called_once()

    def test_music_master_volume_scales_verified_fd_without_mutating_asset_and_retires_copy(self):
        from bmo_ai.master_volume import MasterVolume
        volume = MasterVolume(level=.25)
        backend = AlsaWavBackend(enabled=True, volume_reviewed=True,
                                 clock=lambda: self.now, volume=volume)
        feature = self.feature(backend=backend)
        source = self.root / self.items[0].path
        original = source.read_bytes()
        runner = Mock()
        runner.poll.return_value = None
        with patch("bmo_ai.features.games.OwnedProcessRunner", return_value=runner) as factory:
            self.assertEqual(self.start(feature).status, "running")
        prepared_fd = factory.call_args.kwargs["pass_fds"][0]
        self.assertNotEqual(prepared_fd, feature._handle.fd)
        prepared_path = Path(os.readlink(f"/proc/self/fd/{prepared_fd}"))
        def peak(raw):
            with wave.open(io.BytesIO(raw), "rb") as wav:
                pcm = wav.readframes(wav.getnframes())
            return max(abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
                       for i in range(0, len(pcm), 2))
        self.assertLessEqual(peak(os.pread(prepared_fd, 65536, 0)), peak(original) * .25 + 1)
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(prepared_path.exists())
        feature.stop()
        self.assertFalse(prepared_path.exists())
        with self.assertRaises(OSError):
            os.fstat(prepared_fd)

    def test_music_volume_and_mute_replace_only_owned_pcm_at_same_position_and_preserve_pause(self):
        from bmo_ai.master_volume import MasterVolume
        volume = MasterVolume(level=.25)
        backend = AlsaWavBackend(enabled=True, volume_reviewed=True,
                                 clock=lambda: self.now, volume=volume)
        feature = self.feature(backend=backend)
        runners = [Mock() for _ in range(3)]
        for runner in runners:
            runner.poll.return_value = None
        with patch("bmo_ai.features.games.OwnedProcessRunner", side_effect=runners) as factory:
            self.start(feature)
            self.now = .1
            volume.set(level=.5)
            self.assertTrue(backend.volume_changed())
            runners[0].stop.assert_called_once()
            self.assertAlmostEqual(backend.position(), .1)
            fd = factory.call_args.kwargs["pass_fds"][0]
            with wave.open(io.BytesIO(os.pread(fd, 65536, 0)), "rb") as wav:
                self.assertEqual(wav.getnframes(), 3200)  # Remaining .4s, not a replay.
                pcm = wav.readframes(wav.getnframes())
                self.assertEqual(max(abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
                                     for i in range(0, len(pcm), 2)), 500)
            feature.pause()
            self.now = 2
            volume.set(muted=True)
            self.assertTrue(backend.volume_changed())
            self.assertAlmostEqual(backend.position(), .1)
            self.assertEqual(feature.snapshot()["state"], "paused")
            runners[1].stop.assert_called_once()
            runners[2].pause.assert_called_once()
            fd = factory.call_args.kwargs["pass_fds"][0]
            with wave.open(io.BytesIO(os.pread(fd, 65536, 0)), "rb") as wav:
                self.assertEqual(wav.readframes(wav.getnframes()), bytes(6400))
        feature.stop()


if __name__ == "__main__":
    unittest.main()
