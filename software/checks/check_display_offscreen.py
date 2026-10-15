"""Exercise the manual app with SDL's dummy video driver, never a real screen.

From software/: PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. /usr/bin/python3 -B checks/check_display_offscreen.py
"""
import os
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import Mock, patch

# Select before importing pygame; SDL cannot fall back to a physical backend.
os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame

from bmo_ai.display_app import main
from bmo_ai.face import DEFAULT_ASSET_ROOT
from bmo_ai.hardware import FaceState


class DisplayOffscreenChecks(unittest.TestCase):
    def setUp(self):
        self.assertFalse(pygame.display.get_init())
        self.assertIsNone(pygame.mixer.get_init())
        for target in ("pygame.init", "pygame.quit", "pygame.mixer.init"):
            guard = patch(target, side_effect=AssertionError("audio/global init forbidden"))
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

    def assert_state_pixels(self, state):
        self.assertEqual(pygame.display.get_driver(), "dummy")
        surface = pygame.display.get_surface()
        self.assertEqual(surface.get_size(), (800, 480))
        path = sorted((DEFAULT_ASSET_ROOT / state.value).glob("*.png"))[0]
        expected = pygame.Surface((800, 480))
        expected.blit(pygame.image.load(str(path)), (0, 0))
        self.assertEqual(pygame.image.tobytes(surface, "RGBA"),
                         pygame.image.tobytes(expected, "RGBA"))

    def test_dev_keys_render_all_five_states_and_escape_closes_dummy_window(self):
        states = list(FaceState)
        keys = [pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5]
        step = 0

        def events():
            nonlocal step
            if step:
                self.assert_state_pixels(states[step - 1])
            key = keys[step] if step < len(keys) else pygame.K_ESCAPE
            step += 1
            return [pygame.event.Event(pygame.KEYDOWN, key=key)]

        clock = Mock()
        clock.tick.return_value = 0
        with patch("pygame.event.get", side_effect=events), patch("pygame.time.Clock", return_value=clock):
            self.assertEqual(main(["--dev", "--windowed"], output_fn=lambda text: None), 0)
        self.assertEqual(step, 6)
        self.assertTrue(all(call.args == (60,) for call in clock.tick.call_args_list))

    def test_normal_mode_keeps_idle_and_q_closes_dummy_window(self):
        step = 0

        def events():
            nonlocal step
            if step:
                self.assert_state_pixels(FaceState.IDLE)
            key = pygame.K_4 if step == 0 else pygame.K_q
            step += 1
            return [pygame.event.Event(pygame.KEYDOWN, key=key)]

        clock = Mock()
        clock.tick.return_value = 0
        with patch("pygame.event.get", side_effect=events), patch("pygame.time.Clock", return_value=clock):
            self.assertEqual(main(["--windowed"], output_fn=lambda text: None), 0)
        self.assertEqual(step, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
