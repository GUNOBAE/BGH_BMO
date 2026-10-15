"""Finite demo and UI integration checks without real SDL or external adapters."""
import builtins
from contextlib import redirect_stdout
import importlib.util
import io
import os
from threading import Event, get_ident
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import display_app, offline_demo
from bmo_ai.app import ConversationApp
from bmo_ai.config import BMOConfig
from bmo_ai.conversation import ConversationWorker
from bmo_ai.hardware import FaceState
from test_display_app import FakeDisplayPygame


class OfflineDemoTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.audio.record_wav",
                       "bmo_ai.audio.speak", "bmo_ai.cloud.create_client"):
            self.enterContext(patch(target, side_effect=AssertionError("external operation forbidden")))

    def run_demo(self, scenarios, *, cycles=1):
        messages, states = [], [FaceState.IDLE]
        owner = get_ident()

        def set_state(state):
            self.assertEqual(get_ident(), owner)
            states.append(state)

        demo = offline_demo.OfflineDemo(scenarios, cycles=cycles, delay=0.001, output_fn=messages.append)
        face = SimpleNamespace(set_state=set_state)
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if demo.update(face):
                    self.assertFalse(demo.failed)
                    return demo, states, messages
                time.sleep(0.0005)
            self.fail("finite offline demo exceeded test bound")
        finally:
            self.assertTrue(demo.close())

    def test_success_sequence_includes_idle_after_real_fake_player_start(self):
        demo, states, output = self.run_demo(("success",))
        self.assertEqual(states, [FaceState.IDLE, FaceState.LISTENING, FaceState.THINKING,
                                  FaceState.SPEAKING, FaceState.IDLE])
        self.assertEqual(demo.completed_count, 1)
        self.assertEqual(demo.completed[0].code, "ok")

    def test_all_scripted_failures_cancel_timeout_and_recovery_are_finite(self):
        demo, states, output = self.run_demo((*offline_demo.SCENARIOS, "success"))
        self.assertEqual([item.code for item in demo.completed],
            ["ok", "ok", "authentication", "rate_limit", "empty_stt", "invalid_wav",
             "device_unavailable", "cancelled", "timeout", "ok"])
        self.assertEqual(states[-1], FaceState.IDLE)
        self.assertIn(FaceState.ERROR, states)
        self.assertTrue(any("cancel_requested" in line for line in output))
        self.assertTrue(all(item.synthetic for item in demo.completed))
        self.assertNotIn("Hello", "\n".join(output))

    def test_100_turn_demo_retains_bounded_history_and_no_worker(self):
        demo, states, output = self.run_demo(("empty_stt",) * 5, cycles=20)
        self.assertEqual(demo.completed_count, 100)
        self.assertEqual(len(demo.completed), 64)
        self.assertIsNone(demo.worker)

    def test_import_help_and_headless_never_import_device_or_sdk_backends(self):
        real_import = builtins.__import__
        prohibited = ("pygame", "openai", "serial", "alsaaudio", "cv2", "RPi", "gpiozero")

        def guarded(name, *args, **kwargs):
            if any(name == item or name.startswith(item + ".") for item in prohibited):
                raise AssertionError("forbidden backend import")
            return real_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location("bmo_ai.offline_demo_import_safety", offline_demo.__file__)
        module = importlib.util.module_from_spec(spec)
        output = io.StringIO()
        with patch("builtins.__import__", side_effect=guarded):
            spec.loader.exec_module(module)
            with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                module.main(["--help"])
            self.assertEqual(caught.exception.code, 0)
            self.assertEqual(module.main(["--headless", "--scenario", "empty_stt"],
                                          output_fn=lambda message: None), 0)
        self.assertIn("--headless", output.getvalue())

    def test_global_deadline_closes_finite_demo_without_new_work(self):
        now = [0.0]
        demo = offline_demo.OfflineDemo(clock=lambda: now[0], output_fn=Mock())
        now[0] = 121
        self.assertTrue(demo.update(Mock()))
        self.assertTrue(demo.failed)
        self.assertIsNone(demo.worker)

    def test_invalid_demo_settings_do_not_create_worker(self):
        for kwargs in ({"scenarios": ()}, {"scenarios": ("unknown",)}, {"cycles": 21},
                       {"delay": float("nan")}, {"delay": True}):
            with self.assertRaises(ValueError):
                offline_demo.OfflineDemo(**kwargs)

    def test_display_pumps_and_renders_while_each_dependency_is_blocked(self):
        for phase in ("input", "llm", "tts", "playback"):
            with self.subTest(phase=phase):
                entered, release = Event(), Event()
                owner = get_ident()

                def factory(face, cancel):
                    def step(name):
                        self.assertNotEqual(get_ident(), owner)
                        if name == phase:
                            entered.set()
                            if not release.wait(2):
                                raise RuntimeError("test gate timed out")

                    def listen():
                        step("input")
                        return "synthetic"

                    def ask(text):
                        step("llm")
                        return "synthetic"

                    def speak(text, *, on_playback_start):
                        step("tts")
                        on_playback_start()
                        step("playback")

                    return ConversationApp(SimpleNamespace(ask=ask), None, BMOConfig(), face=face,
                        voice=SimpleNamespace(listen=listen, speak=speak))

                worker = ConversationWorker(factory)
                worker.start()
                self.assertTrue(entered.wait(1))
                renderer = Mock(spec=("set_state", "render", "close"))
                renderer.set_state.side_effect = lambda state: self.assertEqual(get_ident(), owner)
                renderer.render.side_effect = lambda dt: self.assertEqual(get_ident(), owner)
                backend = FakeDisplayPygame(driver="dummy")
                backend.event.get.side_effect = [[], [], [], [SimpleNamespace(type=backend.QUIT)]]
                session = SimpleNamespace(
                    handle_event=Mock(), close=worker.close,
                    update=lambda face: [face.set_state(item.state) for item in worker.poll()
                                         if item.state is not None] and False)
                options = SimpleNamespace(dev=False, windowed=True, display=0)
                try:
                    with patch.object(display_app, "PygameFace", return_value=renderer):
                        self.assertEqual(display_app.run_display(options, backend, session=session,
                                                                 output_fn=Mock()), 0)
                    self.assertEqual(backend.event.get.call_count, 4)
                    self.assertEqual(renderer.render.call_count, 3)
                    self.assertEqual(backend.display.flip.call_count, 3)
                    self.assertTrue(worker.worker_alive)
                    renderer.close.assert_called_once()
                    backend.display.quit.assert_called_once()
                finally:
                    worker.close()
                    release.set()
                    worker._thread.join(timeout=1)
                    self.assertFalse(worker.worker_alive)

    def test_session_cleanup_error_still_restores_cursor_and_display(self):
        backend = FakeDisplayPygame()
        renderer = Mock(spec=("render", "set_state", "close"))
        session = Mock()
        session.close.side_effect = RuntimeError("synthetic cleanup failure")
        with patch.dict(os.environ, {"SDL_VIDEODRIVER": "dummy"}), patch.object(
            display_app, "PygameFace", return_value=renderer
        ):
            self.assertEqual(display_app.main([], pygame_module=backend, window_factory=Mock(),
                                               session=session, output_fn=Mock()), 1)
        renderer.close.assert_called_once()
        backend.display.quit.assert_called_once()
        self.assertTrue(backend.mouse.set_visible.call_args.args[0])

    def test_missing_assets_warning_is_explicit(self):
        backend = FakeDisplayPygame()
        renderer = Mock(missing_states=(FaceState.IDLE,))
        output = Mock()
        with patch.object(display_app, "PygameFace", return_value=renderer):
            self.assertEqual(display_app.run_display(SimpleNamespace(dev=False, windowed=True, display=0),
                                                     backend, output_fn=output), 0)
        self.assertIn("Face assets missing", output.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
