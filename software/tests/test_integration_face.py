"""Offline contract/security tests; no project device or API backend is loaded."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import wave

from bmo_ai import integration_face as face


class MouthTimelineTests(unittest.TestCase):
    def test_large_opening_holds_original_two_bridge_frames(self):
        self.assertEqual([face.bridge("closed", "wide", time, .2)
                          for time in (0, 1 / 30, 2 / 30)], ["low", "body", "wide"])
        self.assertEqual([face.bridge("closed", "round", time, .2)
                          for time in (0, 1 / 30)], ["tight", "round"])
        self.assertEqual(face.bridge("closed", "wide", 0, .104), "wide")
        self.assertEqual(face.bridge("wide", "closed", 0, .2), "body")

    def test_expansion_uses_raw_preceding_target_and_preserves_timing(self):
        raw = [{"time": 0, "mouth": "closed"}, {"time": .2, "mouth": "wide"},
               {"time": .4, "mouth": "closed"}]
        cues = face.expand_cues(raw, .4)
        self.assertEqual([cue["mouth"] for cue in cues], ["closed", "low", "body", "wide", "closed"])
        self.assertEqual(cues[1]["time"], .2)
        self.assertAlmostEqual(cues[2]["time"], .2 + 1 / 30)
        self.assertEqual(cues[-1]["time"], .4)

    def test_real_pcm_file_silence_and_nonconstant_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.wav"
            rate = 12000
            samples = [0] * 2400 + [16000, -16000] * 2400 + [0] * 3600
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, rate, 0, "NONE", "NONE"))
                wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
            cues = face.wav_cues(path)
            self.assertEqual(cues[0], {"time": 0.0, "mouth": "closed"})
            self.assertTrue(any(cue["mouth"] == "wide" for cue in cues))
            self.assertEqual(cues[-1]["mouth"], "closed")
            self.assertTrue(all(cue["time"] >= .2 for cue in cues[1:]))
            self.assertEqual(cues, sorted(cues, key=lambda cue: cue["time"]))

    def test_all_silence_remains_closed(self):
        events = face.envelope([[0.0] * 2400], 12000)
        self.assertEqual({event["mouth"] for event in events}, {"closed"})

    def test_invalid_or_empty_wav_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 12000, 0, "NONE", "NONE"))
            with self.assertRaises(ValueError):
                face.wav_cues(path)


class FaceServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "approved.html"
        self.path.write_text("<!doctype html><title>test fixture</title>")
        self.hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.calls = 0
        self.state = dict(session_id="s", turn_id="t", utterance_id="u", sequence=1,
                          phase="thinking", emotion="crying", playback="prepared", position=0,
                          cues=[{"time": 0, "mouth": "wide"}], secret="NEVER_SERIALIZE")

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        self.calls += 1
        return self.state

    def test_unapproved_html_and_public_bind_are_rejected(self):
        with self.assertRaises(ValueError):
            face.FaceServer(self.path, snapshot=self.snapshot)
        with self.assertRaises(ValueError):
            face.FaceServer(self.path, snapshot=self.snapshot, host="0.0.0.0")

    def test_allowlist_getonly_snapshot_whitelist_and_cleanup(self):
        # Only the fixture's hash gate is patched; production checks the pinned SHA.
        with patch.object(face, "FACE_SHA256", self.hash):
            server = face.FaceServer(self.path, snapshot=self.snapshot).start()
        try:
            self.assertIs(server.start(), server)
            for resource in ("", "adapter.js", "face.html?kiosk=1"):
                with urlopen(server.url + resource, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertIn("media-src 'none'", response.headers["Content-Security-Policy"])
            with urlopen(server.url + "api/state", timeout=2) as response:
                state = json.load(response)
            self.assertEqual(self.calls, 1)
            self.assertNotIn("secret", state)
            self.assertEqual(state["cues"], [])
            self.state.update(playback="started", phase="speaking")
            with urlopen(server.url + "api/state", timeout=2) as response:
                self.assertEqual(json.load(response)["cues"], [{"time": 0, "mouth": "wide"}])
            for resource in (".env", "../approved.html", "%2e%2e/approved.html", "api/command"):
                with self.assertRaises(HTTPError) as error:
                    urlopen(server.url + resource, timeout=2)
                self.assertEqual(error.exception.code, 404)
            for request in (Request(server.url + "api/state", method="POST", data=b"{}"),
                            Request(server.url, headers={"Host": "evil.example"}),
                            Request(server.url + "api/state", headers={"Origin": "https://evil.example"})):
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertIn(error.exception.code, (403, 405))
        finally:
            server.close()
            server.close()
        self.assertIsNone(server._thread)

    def test_nonfinite_or_invalid_state_fails_closed(self):
        self.state["position"] = float("nan")
        with patch.object(face, "FACE_SHA256", self.hash), face.FaceServer(self.path, snapshot=self.snapshot) as server:
            with self.assertRaises(HTTPError) as error:
                urlopen(server.url + "api/state", timeout=2)
            self.assertEqual(error.exception.code, 503)

    def test_objects_and_truncated_identifiers_cannot_enter_public_snapshot(self):
        # Converting arbitrary metadata objects with str() could expose private
        # state; truncation could collapse two different utterance identities.
        for field in ("session_id", "turn_id", "utterance_id", "source"):
            for value in ({"private": "NEVER_SERIALIZE"}, "x" * 161):
                with self.subTest(field=field, kind=type(value).__name__):
                    state = self.state | {field: value}
                    with self.assertRaises(ValueError):
                        face._public_snapshot(state)


if __name__ == "__main__":
    unittest.main()
