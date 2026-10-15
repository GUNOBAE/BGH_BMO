"""Optional real-Pygame checks: only in-memory surfaces, no SDL initialization.

From software/: PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. python3 -B checks/check_face_offscreen.py
Use an existing interpreter with pygame; do not install or enable devices for this check.
This check fails explicitly when pygame is missing and is outside default discovery.
"""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

import pygame

from bmo_ai.face import DEFAULT_ASSET_ROOT, PygameFace
from bmo_ai.hardware import FaceState


class OffscreenFaceChecks(unittest.TestCase):
    def setUp(self):
        self.assertFalse(pygame.get_init())
        self.assertFalse(pygame.display.get_init())
        self.assertIsNone(pygame.mixer.get_init())
        for target in (
            "pygame.init", "pygame.quit", "pygame.display.init",
            "pygame.display.set_mode", "pygame.display.flip", "pygame.display.quit",
            "pygame.mixer.init", "pygame.mixer.quit", "pygame.event.get",
            "pygame.mouse.set_visible", "pygame.time.Clock",
        ):
            guard = patch(target, side_effect=AssertionError("device/UI lifecycle forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        for owner, name in ((socket.socket, "connect"), (subprocess, "Popen")):
            guard = patch.object(owner, name, side_effect=AssertionError("network/process forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def tearDown(self):
        self.assertFalse(pygame.get_init())
        self.assertFalse(pygame.display.get_init())
        self.assertIsNone(pygame.mixer.get_init())

    def test_all_13_original_pngs_decode_and_match_provenance(self):
        record = json.loads((DEFAULT_ASSET_ROOT / "PROVENANCE.json").read_text())
        actual = {p.relative_to(DEFAULT_ASSET_ROOT).as_posix()
                  for p in DEFAULT_ASSET_ROOT.glob("*/*.png")}
        self.assertEqual(actual, {entry["path"] for entry in record["assets"]})
        self.assertEqual(len(actual), 13)
        for entry in record["assets"]:
            with self.subTest(asset=entry["path"]):
                path = DEFAULT_ASSET_ROOT / entry["path"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
                self.assertEqual(path.stat().st_size, entry["bytes"])
                self.assertEqual(pygame.image.load(str(path)).get_size(), (800, 480))

    def test_every_active_frame_matches_source_pixels_at_two_sizes(self):
        for size in ((800, 480), (1024, 600)):
            target = pygame.Surface(size)
            face = PygameFace(target)
            self.addCleanup(face.close)
            for state in FaceState:
                face.set_state(state)
                paths = sorted((DEFAULT_ASSET_ROOT / state.value).glob("*.png"))
                for index, path in enumerate(paths):
                    with self.subTest(size=size, state=state, frame=index):
                        face.render(0 if index == 0 else 0.1)
                        expected = pygame.Surface(size)
                        expected.blit(pygame.transform.smoothscale(
                            pygame.image.load(str(path)), size), (0, 0))
                        self.assertEqual(pygame.image.tobytes(target, "RGBA"),
                                         pygame.image.tobytes(expected, "RGBA"))

    def test_missing_assets_fallback_and_close_keep_surface_owned_by_caller(self):
        # A guaranteed nonexistent subpath exercises fallback without filesystem writes.
        target = pygame.Surface((16, 12))
        face = PygameFace(target, asset_root=DEFAULT_ASSET_ROOT / "missing" / "missing")
        for state in FaceState:
            face.set_state(state)
            face.render(0.1)
            self.assertEqual(tuple(target.get_at((0, 0))), (142, 214, 186, 255))
        face.close()
        face.close()
        target.fill((1, 2, 3))
        face.render(0)
        self.assertEqual(tuple(target.get_at((0, 0))), (1, 2, 3, 255))


if __name__ == "__main__":
    unittest.main(verbosity=2)
