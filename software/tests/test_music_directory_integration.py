"""Reviewed generated WAV library -> existing Host silent music lifecycle.

No source songs, speakers, microphone, paid API or native media runtime are used.
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai.desktop_app import main
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.media.library import install_wav, load_library, review_wav
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy


class MusicDirectoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-music-selected-test-")))
        self.library = self.root / "reviewed_library"
        self.clock = 100.
        self.hosts = []
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe", "bmo_ai.audio._run",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.cloud.create_client",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def import_songs(self, count=2):
        for index in range(count):
            source = self.root / f"generated_{index}.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 8000)
            review = review_wav(source)
            result = install_wav(review, self.library, f"song_{index}",
                title=f"Generated silence {index}", license_id="test_generated", license_reviewed=True)
            self.assertEqual(result["playback"], "NOT_RUN")

    def host(self, *, simulate=True, selected=True, directory=None):
        kwargs = {"music_directory": self.library if directory is None else directory} if selected else {}
        host = FeatureHost(runtime=RuntimePolicy(simulate=simulate), client=FeatureSimulatedClient(),
                           clock=lambda: self.clock, **kwargs)
        self.hosts.append(host)
        return host

    def snapshot_files(self):
        return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode & 0o777)
                for p in self.library.iterdir()}

    def test_imported_catalog_pause_resume_next_end_preserves_face_emotion_and_context(self):
        self.import_songs()
        host = self.host()
        session, brain = host.session, host.session.brain
        session.set_emotion("sad")
        history = list(brain.history)
        state = host.snapshot()
        self.assertEqual([item["id"] for item in state["media"]["catalog"]], ["song_0", "song_1"])
        self.assertEqual(state["asset_status"]["user_songs"], "AVAILABLE_FOR_REVIEW")
        self.assertEqual(state["asset_status"]["music_playback"], "SILENT_MOCK")
        start = host.execute(HostIntent("play_song", "song_0"))
        self.assertEqual(start.status, "simulated")
        self.assertEqual(host.modes.mode, "music")
        self.assertEqual(host.audio.owner, "music")
        self.assertEqual(host.media.snapshot()["cue_kind"], "none")
        self.assertIsNone(host.media.cue_snapshot())
        self.clock += .2
        self.assertTrue(host.control(HostControl("pause"), "pause_music").completed)
        position = host.media.snapshot()["position"]
        self.clock += 1
        self.assertEqual(host.media.snapshot()["position"], position)
        self.assertTrue(host.control(HostControl("resume"), "resume_music").completed)
        self.assertTrue(host.control(HostControl("next"), "next_music").simulated)
        self.assertEqual(host.media.snapshot()["active_id"], "song_1")
        self.clock += .6
        host.heartbeat(); host.poll()
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)
        self.assertIs(host.session, session); self.assertIs(host.session.brain, brain)
        self.assertEqual(session.snapshot()["emotion"], "sad")
        self.assertEqual(session.brain.history, history)
        self.assertEqual(host.client.requests, [])
        self.assertFalse(host.session.voice.has_cache)

    def test_explicit_stop_and_app_close_preserve_every_imported_file(self):
        self.import_songs()
        before = self.snapshot_files()
        host = self.host()
        host.session.set_emotion("happy")
        host.execute(HostIntent("play_song", "song_0"))
        self.assertEqual(host.execute(HostIntent("stop_media")).status, "simulated")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)
        self.assertEqual(host.session.snapshot()["emotion"], "happy")
        host.execute(HostIntent("play_song", "song_1"))
        host.close()
        self.assertEqual(self.snapshot_files(), before)
        self.assertEqual(len(load_library(self.library)), 2)

    def test_live_catalog_review_does_not_enable_music_or_open_backend(self):
        self.import_songs(count=1)
        host = self.host(simulate=False)
        with patch.object(host.media.backend, "start", side_effect=AssertionError("No backend start")) as start:
            result = host.execute(HostIntent("play_song", "song_0"))
        self.assertEqual((result.status, result.code), ("rejected", "capability_denied"))
        self.assertEqual(host.snapshot()["capabilities"]["music"], "denied")
        self.assertEqual(host.snapshot()["asset_status"]["music_playback"], "DENIED")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)
        self.assertFalse(host.camera.snapshot()["on"])
        start.assert_not_called()

    def test_missing_selected_library_stays_empty_and_is_never_created(self):
        before = set(self.root.iterdir())
        host = self.host()
        self.assertEqual(host.media.catalog(), ())
        self.assertEqual(host.snapshot()["asset_status"]["user_songs"], "ASSET_PENDING")
        self.assertEqual(host.execute(HostIntent("play_song", "absent_song")).code, "unknown_media")
        host.close()
        self.assertFalse(self.library.exists())
        self.assertEqual(set(self.root.iterdir()), before)

    def test_default_catalog_does_not_scan_or_load_a_library(self):
        self.import_songs(count=1)
        with patch("bmo_ai.features.media.library.load_library", side_effect=AssertionError("No implicit library read")):
            host = self.host(selected=False)
        self.assertEqual(host.media.catalog(), ())
        self.assertEqual(host.snapshot()["asset_status"]["user_songs"], "ASSET_PENDING")
        self.assertEqual(host.snapshot()["asset_status"]["music_playback"], "SILENT_MOCK")

    def test_conflicting_demo_flags_fail_before_files_or_keys_and_dryrun_hides_path(self):
        forbidden = Mock(side_effect=AssertionError("No files/keys/engine"))
        with patch("os.open", forbidden), patch("bmo_ai.features.media.library.load_library", forbidden), \
             redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--simulate", "--demo-assets", "--music-directory", str(self.library)],
                 host_factory=forbidden, credential_resolver=forbidden)
        self.assertEqual(caught.exception.code, 2)
        forbidden.assert_not_called()
        with patch("bmo_ai.features.host.TemporaryDirectory", forbidden), self.assertRaises(FeatureError) as caught:
            FeatureHost(demo_assets=True, music_directory=self.library)
        self.assertEqual(caught.exception.code, "media_library_conflict")
        with patch("os.open", forbidden), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--simulate", "--music-directory", str(self.library), "--dry-run"],
                                  host_factory=forbidden), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["music_library"], {"selected": True, "live_playback_enabled": False, "automatic_import": False})
        self.assertNotIn(str(self.library), output.getvalue())
        forbidden.assert_not_called()

    def test_changed_library_hash_is_rejected_before_host_playback(self):
        self.import_songs(count=1)
        asset, = load_library(self.library)
        (self.library / asset.path).write_bytes(b"changed synthetic asset")
        with patch("bmo_ai.features.media.FakeMediaBackend.start", side_effect=AssertionError("No start")) as start, \
             self.assertRaises(FeatureError) as caught:
            self.host()
        self.assertEqual(caught.exception.code, "media_hash_mismatch")
        start.assert_not_called()

    def test_asset_changed_after_catalog_load_is_reverified_and_recovers_mode_focus(self):
        self.import_songs(count=1)
        host = self.host()
        host.session.set_emotion("surprised")
        asset, = load_library(self.library)
        (self.library / asset.path).write_bytes(b"changed after library load")
        with patch.object(host.media.backend, "start", side_effect=AssertionError("No changed asset start")) as start:
            result = host.execute(HostIntent("play_song", "song_0"))
        self.assertEqual(result.status, "rejected")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)
        self.assertEqual(host.media.catalog()[0].readiness, "UNVERIFIED")
        self.assertEqual(host.session.snapshot()["emotion"], "surprised")
        self.assertEqual(host.session.brain.history, [])
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
