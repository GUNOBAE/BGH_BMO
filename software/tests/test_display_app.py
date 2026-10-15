"""Manual display app contracts using fake SDL, events and renderer only."""
import builtins
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from bmo_ai import display_app
from bmo_ai.hardware import FaceState


class FakeDisplayPygame:
    """No image loading, device, actual SDL, audio, clock sleep or processes."""

    QUIT = 1
    KEYDOWN = 2
    K_ESCAPE = 27
    K_q = 113
    K_1, K_2, K_3, K_4, K_5 = range(49, 54)
    FULLSCREEN = 0x80000000

    def __init__(self, events=None, *, initialized=False, driver="wayland",
                 desktops=((1024, 600), (1920, 1080)), cursor=True):
        self.surface = object()
        self.display = SimpleNamespace(
            get_init=Mock(return_value=initialized),
            init=Mock(),
            get_driver=Mock(return_value=driver),
            get_desktop_sizes=Mock(return_value=desktops),
            set_mode=Mock(return_value=self.surface),
            set_caption=Mock(),
            flip=Mock(),
            quit=Mock(),
        )
        self.mouse = SimpleNamespace(
            get_visible=Mock(return_value=cursor),
            set_visible=Mock(),
        )
        self.clock = SimpleNamespace(tick=Mock(return_value=16))
        self.time = SimpleNamespace(Clock=Mock(return_value=self.clock))
        self.event = SimpleNamespace(get=Mock(side_effect=(
            events if events is not None else [[SimpleNamespace(type=self.QUIT)]]
        )))
        self.init = Mock(side_effect=AssertionError("global pygame init forbidden"))
        self.quit = Mock(side_effect=AssertionError("global pygame quit forbidden"))
        self.mixer = SimpleNamespace(
            init=Mock(side_effect=AssertionError("mixer init forbidden")),
            quit=Mock(side_effect=AssertionError("mixer quit forbidden")),
        )


class DisplayAppTests(unittest.TestCase):
    def setUp(self):
        self.backends = []
        self.renderer = Mock(spec=("render", "set_state", "close"))
        self.renderer_factory = self.enterContext(
            patch.object(display_app, "PygameFace", return_value=self.renderer)
        )
        self.window = SimpleNamespace(set_fullscreen=Mock())
        self.window_factory = Mock(return_value=self.window)
        self.output = Mock()
        self.enterContext(patch.dict(os.environ, {"WAYLAND_DISPLAY": "test-wayland"}, clear=True))

    def tearDown(self):
        for backend in self.backends:
            backend.init.assert_not_called()
            backend.quit.assert_not_called()
            backend.mixer.init.assert_not_called()
            backend.mixer.quit.assert_not_called()

    def backend(self, *args, **kwargs):
        backend = FakeDisplayPygame(*args, **kwargs)
        self.backends.append(backend)
        return backend

    def options(self, **overrides):
        return SimpleNamespace(**dict({"dev": False, "windowed": False, "display": 0},
                                      **overrides))

    def run_display(self, backend, **options):
        return display_app.run_display(
            self.options(**options), backend, window_factory=self.window_factory,
            output_fn=self.output,
        )

    def main(self, backend, argv=None, *, output_fn=None):
        return display_app.main(
            argv or [], pygame_module=backend, window_factory=self.window_factory,
            output_fn=self.output if output_fn is None else output_fn,
        )

    def key(self, backend, key):
        return SimpleNamespace(type=backend.KEYDOWN, key=key)

    def assert_cleaned_up(self, backend, *, cursor=True):
        self.renderer.close.assert_called_once_with()
        self.assertEqual(backend.mouse.set_visible.call_args_list,
                         [call(False), call(cursor)])
        backend.display.quit.assert_called_once_with()

    def test_import_and_help_are_lazy_and_do_not_import_pygame_or_adapters(self):
        original_import = builtins.__import__
        prohibited = ("pygame", "subprocess", "openai", "serial", "alsaaudio",
                      "bmo_ai.cloud", "bmo_ai.audio", "cloud", "audio")

        def guarded_import(name, *args, **kwargs):
            if any(name == item or name.startswith(item + ".") for item in prohibited):
                raise AssertionError("display import tried a forbidden dependency")
            return original_import(name, *args, **kwargs)

        spec = importlib.util.spec_from_file_location(
            "bmo_ai.display_app_import_safety", display_app.__file__
        )
        module = importlib.util.module_from_spec(spec)
        output = io.StringIO()
        with patch("builtins.__import__", side_effect=guarded_import):
            spec.loader.exec_module(module)
            with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                module.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--dev", output.getvalue())
        self.assertIn("--windowed", output.getvalue())
        self.renderer_factory.assert_not_called()
        self.window_factory.assert_not_called()

    def test_fullscreen_uses_current_desktop_resolution_without_modeset_flags(self):
        backend = self.backend()
        self.assertEqual(self.run_display(backend), 0)
        backend.display.set_mode.assert_called_once_with((1024, 600), display=0)
        backend.display.set_caption.assert_called_once_with("BMO")
        self.window.set_fullscreen.assert_called_once_with(desktop=True)
        self.renderer_factory.assert_called_once_with(backend.surface, pygame_module=backend)
        self.assert_cleaned_up(backend)

    def test_windowed_uses_800_by_480_without_fullscreen(self):
        backend = self.backend()
        self.assertEqual(self.run_display(backend, windowed=True), 0)
        backend.display.set_mode.assert_called_once_with((800, 480), display=0)
        self.window_factory.assert_not_called()
        self.assert_cleaned_up(backend)

    def test_selected_display_uses_its_desktop_size(self):
        backend = self.backend()
        self.assertEqual(self.main(backend, ["--display", "1"]), 0)
        backend.display.set_mode.assert_called_once_with((1920, 1080), display=1)

    def test_dev_keys_delegate_all_five_states_to_existing_renderer(self):
        backend = self.backend()
        backend.event.get.side_effect = [
            [self.key(backend, key)] for key in
            (backend.K_1, backend.K_2, backend.K_3, backend.K_4, backend.K_5)
        ] + [[self.key(backend, backend.K_q)]]
        self.assertEqual(self.main(backend, ["--dev"]), 0)
        self.assertEqual(self.renderer.set_state.call_args_list,
                         [call(state) for state in FaceState])
        self.assertEqual(self.renderer.render.call_count, 5)
        self.assertEqual(self.output.call_count, 2)
        self.assert_cleaned_up(backend)

    def test_normal_mode_ignores_state_keys(self):
        backend = self.backend()
        backend.event.get.side_effect = [
            [self.key(backend, key) for key in
             (backend.K_1, backend.K_2, backend.K_3, backend.K_4, backend.K_5)],
            [self.key(backend, backend.K_ESCAPE)],
        ]
        self.assertEqual(self.run_display(backend), 0)
        self.renderer.set_state.assert_not_called()
        self.renderer.render.assert_called_once_with(0.016)
        self.assertEqual(self.output.call_count, 1)

    def test_escape_q_and_window_close_all_exit_without_rendering_another_frame(self):
        for kind in ("escape", "q", "close"):
            with self.subTest(kind=kind):
                self.renderer.reset_mock()
                backend = self.backend(cursor=False)
                event = (SimpleNamespace(type=backend.QUIT) if kind == "close" else
                         self.key(backend, backend.K_ESCAPE if kind == "escape" else backend.K_q))
                backend.event.get.side_effect = [[event]]
                self.assertEqual(self.run_display(backend), 0)
                self.renderer.render.assert_not_called()
                backend.display.flip.assert_not_called()
                self.assert_cleaned_up(backend, cursor=False)

    def test_clock_limits_to_60_hz_and_passes_seconds_without_changing_animation(self):
        backend = self.backend()
        backend.clock.tick.side_effect = [16, 100, 250]
        backend.event.get.side_effect = [[], [], [SimpleNamespace(type=backend.QUIT)]]
        self.assertEqual(self.run_display(backend), 0)
        self.assertEqual(backend.clock.tick.call_args_list, [call(60)] * 3)
        self.assertEqual(self.renderer.render.call_args_list, [call(0.016), call(0.1)])
        self.assertEqual(backend.display.flip.call_count, 2)

    def test_existing_initialized_display_is_left_owned_by_caller(self):
        backend = self.backend(initialized=True)
        self.assertEqual(self.main(backend), 2)
        backend.display.init.assert_not_called()
        backend.display.quit.assert_not_called()
        backend.mouse.set_visible.assert_not_called()
        self.renderer_factory.assert_not_called()

    def test_initialization_failure_quits_only_owned_display(self):
        backend = self.backend()
        backend.display.init.side_effect = RuntimeError("private environment detail")
        self.assertEqual(self.main(backend), 1)
        backend.display.quit.assert_called_once_with()
        backend.mouse.set_visible.assert_not_called()
        self.renderer.close.assert_not_called()
        self.assertNotIn("private environment detail", str(self.output.call_args_list))

    def test_renderer_construction_failure_restores_cursor_and_quits_display(self):
        backend = self.backend()
        self.renderer_factory.side_effect = RuntimeError("synthetic asset failure")
        self.assertEqual(self.main(backend), 1)
        backend.display.quit.assert_called_once_with()
        self.assertEqual(backend.mouse.set_visible.call_args_list, [call(False), call(True)])
        self.renderer.close.assert_not_called()

    def test_render_error_restores_cursor_and_quits_display(self):
        backend = self.backend(events=[[]])
        self.renderer.render.side_effect = RuntimeError("synthetic render failure")
        self.assertEqual(self.main(backend), 1)
        self.assert_cleaned_up(backend)

    def test_output_error_still_closes_renderer_and_restores_cursor(self):
        backend = self.backend()
        output = Mock(side_effect=[RuntimeError("synthetic output failure"), None])
        self.assertEqual(self.main(backend, output_fn=output), 1)
        self.assert_cleaned_up(backend)
        self.assertEqual(output.call_count, 2)

    def test_keyboard_interrupt_exits_successfully_and_cleans_up(self):
        backend = self.backend()
        backend.event.get.side_effect = KeyboardInterrupt
        self.assertEqual(self.main(backend), 0)
        self.assert_cleaned_up(backend)
        self.assertNotIn("SDL_VIDEODRIVER", os.environ)

    def test_cleanup_failure_still_restores_cursor_and_quits_display(self):
        backend = self.backend()
        self.renderer.close.side_effect = RuntimeError("synthetic cleanup failure")
        self.assertEqual(self.main(backend), 1)
        self.assert_cleaned_up(backend)

    def test_wayland_is_preferred_and_driver_environment_is_restored(self):
        backend = self.backend()
        os.environ["DISPLAY"] = ":test"
        observed = []
        backend.display.init.side_effect = lambda: observed.append(os.environ["SDL_VIDEODRIVER"])
        self.assertEqual(self.main(backend), 0)
        self.assertEqual(observed, ["wayland"])
        self.assertNotIn("SDL_VIDEODRIVER", os.environ)
        self.assertEqual(os.environ["DISPLAY"], ":test")

    def test_x11_is_selected_when_only_display_is_set(self):
        backend = self.backend(driver="x11")
        os.environ.pop("WAYLAND_DISPLAY")
        os.environ["DISPLAY"] = ":test"
        observed = []
        backend.display.init.side_effect = lambda: observed.append(os.environ["SDL_VIDEODRIVER"])
        self.assertEqual(self.main(backend), 0)
        self.assertEqual(observed, ["x11"])
        self.assertNotIn("SDL_VIDEODRIVER", os.environ)

    def test_explicit_dummy_driver_is_preserved_for_offscreen_callers(self):
        backend = self.backend(driver="dummy")
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        self.assertEqual(self.main(backend), 0)
        self.assertEqual(os.environ["SDL_VIDEODRIVER"], "dummy")

    def test_absent_desktop_and_direct_console_drivers_are_rejected_before_sdl(self):
        for driver in (None, "kmsdrm", "fbcon", "directfb"):
            with self.subTest(driver=driver):
                backend = self.backend()
                with patch.dict(os.environ, {}, clear=True):
                    if driver is not None:
                        os.environ["SDL_VIDEODRIVER"] = driver
                    self.assertEqual(self.main(backend), 2)
                    self.assertEqual(os.environ.get("SDL_VIDEODRIVER"), driver)
                backend.display.init.assert_not_called()
                backend.display.quit.assert_not_called()
                self.renderer_factory.assert_not_called()

    def test_unexpected_active_console_driver_is_rejected_without_creating_window(self):
        backend = self.backend(driver="kmsdrm")
        self.assertEqual(self.main(backend), 2)
        backend.display.set_mode.assert_not_called()
        backend.display.quit.assert_called_once_with()
        self.window_factory.assert_not_called()
        self.renderer_factory.assert_not_called()

    def test_unavailable_display_and_invalid_resolution_fail_before_window_creation(self):
        for options, desktops in (({"display": 2}, ((1024, 600),)),
                                  ({}, ((0, 600),))):
            with self.subTest(options=options, desktops=desktops):
                backend = self.backend(desktops=desktops)
                with self.assertRaises(display_app.DisplayAppError):
                    self.run_display(backend, **options)
                backend.display.set_mode.assert_not_called()
                backend.display.quit.assert_called_once_with()
                self.renderer_factory.assert_not_called()

    def test_negative_display_option_is_rejected_before_sdl(self):
        backend = self.backend()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.main(backend, ["--display", "-1"])
        self.assertEqual(caught.exception.code, 2)
        backend.display.init.assert_not_called()

    def test_pygame_import_failure_is_actionable_without_touching_sdl(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "pygame":
                raise ImportError("synthetic missing package")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            self.assertEqual(display_app.main([], output_fn=self.output), 2)
        self.assertIn("Pygame", self.output.call_args.args[0])
        self.assertNotIn("SDL_VIDEODRIVER", os.environ)
        self.renderer_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
