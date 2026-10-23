"""Pinned profile selection and selected-only HTTP metadata; synthetic files."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bmo_ai import integration_face as face
from bmo_ai.screen_profiles import SCREEN_PROFILES


class ScreenProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / "synthetic.html"
        self.body = b"<!doctype html><title>opaque synthetic screen</title>"
        self.path.write_bytes(self.body)
        self.hash = hashlib.sha256(self.body).hexdigest()
        self.snapshot = Mock(return_value=dict(session_id="s", turn_id="", utterance_id="",
                        sequence=0, phase="idle", emotion="neutral", playback="idle", position=0, cues=[]))

    def test_profiles_and_records_are_immutable_with_explicit_candidate_status(self):
        self.assertEqual(set(SCREEN_PROFILES), {"v13.2", "r10"})
        self.assertEqual(SCREEN_PROFILES["v13.2"]["sha256"], face.FACE_SHA256)
        self.assertEqual(SCREEN_PROFILES["v13.2"]["approval_status"], "approved")
        self.assertEqual(SCREEN_PROFILES["r10"]["approval_status"], "candidate_pending_user_approval")
        self.assertNotEqual(SCREEN_PROFILES["v13.2"]["sha256"], SCREEN_PROFILES["r10"]["sha256"])
        with self.assertRaises(TypeError):
            SCREEN_PROFILES["r10"] = {}
        with self.assertRaises(TypeError):
            SCREEN_PROFILES["v13.2"]["sha256"] = self.hash

    def test_default_original_pin_patch_remains_compatible_and_serves_exact_bytes(self):
        with patch.object(face, "FACE_SHA256", self.hash), \
             face.FaceServer(self.path, snapshot=self.snapshot) as server:
            self.assertEqual(server.screen_profile, "v13.2")
            with urlopen(server.url + "face.html?kiosk=1", timeout=2) as response:
                self.assertEqual(response.read(), self.body)
            with urlopen(server.url + "api/screen", timeout=2) as response:
                metadata = json.load(response)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(metadata, dict(version="v13.2", sha256=self.hash, approval_status="approved"))
            self.snapshot.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.body)

    def test_original_pin_patch_cannot_bypass_the_candidate_hash(self):
        with patch.object(face, "FACE_SHA256", self.hash):
            with self.assertRaises(ValueError):
                face.FaceServer(self.path, snapshot=self.snapshot, screen_profile="r10")

    def test_unknown_profile_rejected_before_any_asset_is_read(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Asset read forbidden")) as read:
            for profile in ("latest", "R10", "", None, True, {}):
                with self.subTest(kind=type(profile).__name__), self.assertRaises(ValueError):
                    face.FaceServer(self.path, snapshot=self.snapshot, screen_profile=profile)
        read.assert_not_called()

    def test_candidate_metadata_has_no_paths_list_or_runtime_switch_endpoint(self):
        # Replace only the candidate pin for this synthetic unit fixture. The
        # browser check uses both real files and the untouched production pins.
        records = dict(SCREEN_PROFILES)
        records["r10"] = MappingProxyType(dict(records["r10"]) | {"sha256": self.hash})
        with patch.object(face, "SCREEN_PROFILES", MappingProxyType(records)), \
             face.FaceServer(self.path, snapshot=self.snapshot, screen_profile="r10") as server:
            with urlopen(server.url + "api/screen?screen_profile=v13.2", timeout=2) as response:
                metadata = json.load(response)
            self.assertEqual(metadata, dict(version="r10", sha256=self.hash,
                                           approval_status="candidate_pending_user_approval"))
            self.assertEqual(set(metadata), {"version", "sha256", "approval_status"})
            self.assertNotIn(self.directory, json.dumps(metadata))
            self.assertNotIn(SCREEN_PROFILES["v13.2"]["sha256"], json.dumps(metadata))
            with urlopen(server.url + "face.html", timeout=2) as response:
                self.assertEqual(response.read(), self.body)
            for target in ("api/screens", "api/screen/r10", "screen_profiles.py", "../synthetic.html"):
                with self.subTest(target=target), self.assertRaises(HTTPError) as error:
                    urlopen(server.url + target, timeout=2)
                self.assertEqual(error.exception.code, 404)
            for request, code in ((Request(server.url + "api/screen", method="POST", data=b"{}"), 405),
                                  (Request(server.url + "api/screen", headers={"Origin": "https://invalid.example"}), 403)):
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, code)
        self.assertEqual(self.path.read_bytes(), self.body)


if __name__ == "__main__":
    unittest.main()
