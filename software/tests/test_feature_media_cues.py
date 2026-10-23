"""Provided cues use the media clock; absent cues never invent singing lips."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.features.host import FeatureHost, HostControl
from bmo_ai.features.media import FakeMediaBackend, MediaFeature, make_fixture_media
from bmo_ai.features.reply import HostIntent


class ProvidedMediaCueTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.now = 0.
        self.items = make_fixture_media(self.root)
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.audio._run",
                       "bmo_ai.cloud.create_client", "bmo_ai.audio.record_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("External IO forbidden")))

    def host(self, *, cues):
        items = self.items
        if cues:
            path = self.root / "provided_cues.json"
            path.write_text(json.dumps([{"time": 0, "mouth": "closed"}, {"time": .1, "mouth": "low"},
                                        {"time": .3, "mouth": "wide"}]))
            path.chmod(0o600)
            items = (replace(items[0], cue_path=path.name,
                             cue_sha256=hashlib.sha256(path.read_bytes()).hexdigest()),)
        clock = lambda: self.now
        media = MediaFeature(self.root, items, backend=FakeMediaBackend(clock=clock, pausable=True), clock=clock)
        host = FeatureHost(media=media, clock=clock)
        self.addCleanup(host.close)
        host.session.set_emotion("crying")
        host.session.set_emotion("auto")
        host.execute(HostIntent("play_song", "fixture_song_a"))
        host.poll()
        return host

    def test_absent_cue_does_not_turn_media_rms_into_external_speech(self):
        host = self.host(cues=False)
        state = host.face_snapshot()
        self.assertEqual((state["emotion"], state["phase"]), ("crying", "idle"))
        self.assertNotEqual(state["source"], "provided_media_cues")
        self.assertEqual(state["cues"], [])

    def test_supplied_cues_pause_resume_and_finish_share_one_clock_and_preserve_emotion(self):
        host = self.host(cues=True)
        self.now = .15; host.poll()
        first = host.face_snapshot()
        self.assertEqual((first["source"], first["phase"], first["cues"][0]["mouth"]),
                         ("provided_media_cues", "speaking", "low"))
        host.control(HostControl("pause"), "pause_cues"); host.poll()
        paused = host.face_snapshot()
        self.assertEqual((paused["phase"], paused["cues"][0]["mouth"]), ("idle", "closed"))
        self.now = .35; host.poll()
        self.assertAlmostEqual(host.media.cue_snapshot()["position"], .15)
        host.control(HostControl("resume"), "resume_cues"); host.poll()
        self.assertEqual(host.face_snapshot()["utterance_id"], first["utterance_id"])
        self.now = .55; host.poll()
        self.assertEqual(host.face_snapshot()["cues"][0]["mouth"], "wide")
        self.now = .8; host.poll()
        ended = host.face_snapshot()
        self.assertEqual((host.modes.mode, host.audio.owner, ended["emotion"], ended["phase"]),
                         ("conversation", None, "crying", "idle"))
        self.assertNotEqual(ended["source"], "provided_media_cues")
        self.assertGreater(ended["sequence"], first["sequence"])
