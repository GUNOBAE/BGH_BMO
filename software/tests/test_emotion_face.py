"""Public emotion metadata only; no original art, devices, SDK or credentials."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from bmo_ai import integration_face as face


class PublicEmotionSnapshotTests(unittest.TestCase):
    def snapshot(self, **changes):
        return dict(session_id="s", turn_id="t", utterance_id="u", sequence=1,
                    phase="thinking", emotion="neutral", playback="prepared",
                    position=0, cues=[]) | changes

    def test_legacy_snapshot_defaults_to_manual(self):
        value = face._public_snapshot(self.snapshot(emotion="sad"))
        self.assertEqual(value["emotion_mode"], "manual")
        self.assertEqual(value["emotion"], "sad")

    def test_all_six_emotions_and_both_modes_are_public_metadata(self):
        for mode in ("auto", "manual"):
            for emotion in face.EMOTIONS:
                with self.subTest(mode=mode, emotion=emotion):
                    value = face._public_snapshot(self.snapshot(emotion_mode=mode, emotion=emotion,
                        speech="private synthetic fixture", last_reply={"speech": "private synthetic fixture"},
                        api_key="synthetic marker", staged_emotion="happy"))
                    self.assertEqual(value["emotion_mode"], mode)
                    self.assertEqual(value["emotion"], emotion)
                    self.assertTrue({"speech", "last_reply", "api_key", "staged_emotion"}.isdisjoint(value))

    def test_invalid_modes_are_rejected_without_payload_in_errors(self):
        for mode in (None, True, 1, "AUTO", "override", "private synthetic fixture", {}, []):
            with self.subTest(kind=type(mode).__name__):
                with self.assertRaises(ValueError) as error:
                    face._public_snapshot(self.snapshot(emotion_mode=mode))
                self.assertEqual(str(error.exception), "Invalid snapshot state")

    def test_http_emotion_mode_is_allowlisted_and_invalid_modes_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.html"
            path.write_text("<!doctype html><title>synthetic fixture</title>")
            state = self.snapshot(emotion="happy", emotion_mode="auto", speech="private synthetic fixture")
            with patch.object(face, "FACE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest()), \
                 face.FaceServer(path, snapshot=lambda: state) as server:
                with urlopen(server.url + "api/state", timeout=2) as response:
                    value = json.load(response)
                self.assertEqual(value["emotion_mode"], "auto")
                self.assertEqual(value["emotion"], "happy")
                self.assertNotIn("speech", value)
                state["emotion_mode"] = "private synthetic fixture"
                with self.assertRaises(HTTPError) as error:
                    urlopen(server.url + "api/state", timeout=2)
                self.assertEqual(error.exception.code, 503)
                self.assertEqual(error.exception.read(), b"State unavailable")


if __name__ == "__main__":
    unittest.main()
