"""Real Pygame + SDL dummy conversation checks; no physical display or audio.

From software/: PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. timeout 30s /usr/bin/python3 -B checks/check_conversation_offscreen.py
Also works without face PNGs. No cloud SDK or device adapter is constructed.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

from pathlib import Path
from threading import Event, get_ident
import unittest
from unittest.mock import patch

import pygame

from bmo_ai import display_app, offline_demo
from bmo_ai.face import DEFAULT_ASSET_ROOT, PygameFace
from bmo_ai.hardware import FaceState


class ConversationOffscreenChecks(unittest.TestCase):
    def setUp(self):
        self.assertFalse(pygame.display.get_init())
        self.assertIsNone(pygame.mixer.get_init())
        for target in ("pygame.init", "pygame.quit", "pygame.mixer.init", "socket.socket.connect",
                       "subprocess.Popen", "bmo_ai.audio.record_wav", "bmo_ai.audio.speak",
                       "bmo_ai.cloud.create_client"):
            self.enterContext(patch(target, side_effect=AssertionError("external operation forbidden")))
        self.owner = get_ident()
        self.states = [FaceState.IDLE]
        self.output = []
        self.frames = 0
        real_flip = pygame.display.flip

        def flip():
            self.assertEqual(get_ident(), self.owner)
            self.assertEqual(pygame.display.get_driver(), "dummy")
            self.frames += 1
            real_flip()

        self.enterContext(patch("pygame.display.flip", side_effect=flip))

    def tearDown(self):
        self.assertFalse(pygame.display.get_init())
        self.assertIsNone(pygame.mixer.get_init())
        self.assertFalse(pygame.get_init())

    def renderer(self, *, missing=False):
        outer = self

        class CheckedFace(PygameFace):
            def __init__(self, surface, **kwargs):
                outer.assertEqual(get_ident(), outer.owner)
                super().__init__(surface, **kwargs,
                    asset_root=DEFAULT_ASSET_ROOT / "nonexistent" if missing else None)

            def set_state(self, state):
                outer.assertEqual(get_ident(), outer.owner)
                outer.states.append(state)
                super().set_state(state)

            def render(self, dt):
                outer.assertEqual(get_ident(), outer.owner)
                super().render(dt)

            def close(self):
                outer.assertEqual(get_ident(), outer.owner)
                super().close()

        return patch.object(display_app, "PygameFace", CheckedFace)

    def test_finite_success_and_errors_keep_pumping_dummy_display(self):
        demo = offline_demo.OfflineDemo(("success", "broken_tts", "cancel", "timeout", "success"),
                                        delay=0.02, output_fn=self.output.append)
        with self.renderer():
            self.assertEqual(display_app.main(["--windowed"], session=demo, output_fn=self.output.append), 0)
        self.assertEqual([item.code for item in demo.completed],
                         ["ok", "invalid_wav", "cancelled", "timeout", "ok"])
        self.assertIn(FaceState.SPEAKING, self.states)
        self.assertIn(FaceState.ERROR, self.states)
        self.assertEqual(self.states[-1], FaceState.IDLE)
        self.assertGreater(self.frames, 10)
        self.assertIsNone(demo.worker)

    def test_missing_assets_still_runs_entire_conversation_and_warns(self):
        demo = offline_demo.OfflineDemo(("success",), delay=0.02, output_fn=self.output.append)
        with self.renderer(missing=True):
            self.assertEqual(display_app.main(["--windowed"], session=demo, output_fn=self.output.append), 0)
        self.assertEqual(demo.completed[0].code, "ok")
        self.assertTrue(any("Face assets missing" in message for message in self.output))
        self.assertGreater(self.frames, 2)

    def test_quit_during_uncooperative_response_closes_display_before_worker(self):
        entered, release = Event(), Event()
        demo = offline_demo.OfflineDemo(("slow",), output_fn=self.output.append)
        real_app = offline_demo.offline_app

        def app(scenario, face, cancel, **kwargs):
            instance = real_app(scenario, face, cancel, delay=0.001)

            def ask(message):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("test gate exceeded bound")
                return "synthetic late response"

            instance.brain.ask = ask
            return instance

        real_get = pygame.event.get

        def events():
            self.assertEqual(get_ident(), self.owner)
            return ([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q)]
                    if entered.is_set() else real_get())

        worker = None
        try:
            with self.renderer(), patch.object(offline_demo, "offline_app", side_effect=app), patch(
                "pygame.event.get", side_effect=events
            ):
                self.assertEqual(display_app.main(["--windowed"], session=demo, output_fn=self.output.append), 0)
            worker = demo.worker
            self.assertIsNotNone(worker)
            self.assertTrue(worker.worker_alive)
            self.assertFalse(pygame.display.get_init())
            self.assertEqual(worker.poll(), ())
            self.assertTrue(any("has not stopped" in message for message in self.output))
        finally:
            release.set()
            if worker is not None and worker._thread is not None:
                worker._thread.join(timeout=1)
                self.assertFalse(worker.worker_alive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
