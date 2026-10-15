"""Face rendering contracts with synthetic files and no pygame or devices."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.app import ConversationApp
from bmo_ai.config import BMOConfig
from bmo_ai import face as face_module
from bmo_ai.face import FALLBACK_COLOR, PygameFace
from bmo_ai.hardware import FaceState


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FakePygameError(Exception):
    pass


class FakeFrame:
    def __init__(self, source, size=None):
        self.source = source
        self.size = size


class FakeSurface:
    def __init__(self, size):
        self.size = size
        self.blits = []
        self.colors = []

    def get_size(self):
        return self.size

    def blit(self, frame, position):
        self.blits.append((frame, position))

    def fill(self, color):
        self.colors.append(color)


class FakePygame:
    """Only image operations are permitted; UI/audio ownership stays outside."""

    error = FakePygameError

    def __init__(self):
        self.loads = []
        self.scales = []
        self.created_surfaces = []
        self.image = SimpleNamespace(load=self.load)
        self.transform = SimpleNamespace(smoothscale=self.smoothscale)
        self.Surface = self.surface
        self.init = Mock(side_effect=AssertionError("pygame init forbidden"))
        self.quit = Mock(side_effect=AssertionError("pygame quit forbidden"))
        self.display = SimpleNamespace(
            quit=Mock(side_effect=AssertionError("display quit forbidden")),
            set_mode=Mock(side_effect=AssertionError("display creation forbidden")),
            flip=Mock(side_effect=AssertionError("display update forbidden")),
        )
        self.mixer = SimpleNamespace(
            init=Mock(side_effect=AssertionError("audio init forbidden")),
        )
        self.event = SimpleNamespace(
            get=Mock(side_effect=AssertionError("event polling forbidden")),
        )

    def load(self, path):
        path = Path(path)
        self.loads.append(path)
        data = path.read_bytes()
        if data == b"os-error":
            raise OSError("unreadable synthetic image")
        if not data.startswith(PNG_SIGNATURE):
            raise FakePygameError("corrupt synthetic image")
        return FakeFrame(path)

    def smoothscale(self, image, size):
        self.scales.append((image, size))
        return FakeFrame(image.source, size)

    def surface(self, size):
        surface = FakeSurface(size)
        self.created_surfaces.append(surface)
        return surface


class FaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.backend = FakePygame()
        self.surface = FakeSurface((800, 480))

    def frames(self, state, *names, root=None):
        folder = (self.root if root is None else root) / state.value
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in names:
            path = folder / name
            path.write_bytes(PNG_SIGNATURE)
            paths.append(path)
        return paths

    def renderer(self, root=None):
        return PygameFace(
            self.surface,
            asset_root=self.root if root is None else root,
            pygame_module=self.backend,
        )

    def shown(self):
        return self.surface.blits[-1][0].source.name

    def test_frames_keep_lexical_order_and_fill_the_supplied_surface(self):
        self.frames(FaceState.IDLE, "2.png", "10.png", "1.png")
        renderer = self.renderer()
        for dt, expected in ((0, "1.png"), (0.1, "10.png"), (0.1, "2.png")):
            renderer.render(dt)
            self.assertEqual(self.shown(), expected)
            frame, position = self.surface.blits[-1]
            self.assertEqual(frame.size, self.surface.size)
            self.assertEqual(position, (0, 0))
        self.assertEqual([path.name for path in self.backend.loads], ["1.png", "10.png", "2.png"])
        self.assertTrue(all(size == self.surface.size for _, size in self.backend.scales))

    def test_ten_fps_accumulates_short_intervals_and_wraps(self):
        self.frames(FaceState.IDLE, "0.png", "1.png", "2.png")
        renderer = self.renderer()
        for dt, expected in ((0, "0.png"), (0.05, "0.png"), (0.05, "1.png"),
                             (0.1, "2.png"), (0.1, "0.png")):
            renderer.render(dt)
            self.assertEqual(self.shown(), expected)

    def test_long_interval_advances_once_and_discards_excess_time(self):
        self.frames(FaceState.IDLE, "0.png", "1.png", "2.png")
        renderer = self.renderer()
        for dt, expected in ((7.35, "1.png"), (0, "1.png"),
                             (0.05, "1.png"), (0.05, "2.png")):
            renderer.render(dt)
            self.assertEqual(self.shown(), expected)

    def test_state_change_resets_index_but_preserves_elapsed_time_without_io(self):
        self.frames(FaceState.IDLE, "idle0.png", "idle1.png")
        self.frames(FaceState.THINKING, "think0.png", "think1.png")
        renderer = self.renderer()
        renderer.render(0.1)
        renderer.render(0.06)
        loads_before = list(self.backend.loads)
        scales_before = list(self.backend.scales)
        blits_before = list(self.surface.blits)
        renderer.set_state(FaceState.THINKING)
        self.assertEqual(self.backend.loads, loads_before)
        self.assertEqual(self.backend.scales, scales_before)
        self.assertEqual(self.surface.blits, blits_before)
        renderer.render(0)
        self.assertEqual(self.shown(), "think0.png")
        renderer.render(0.04)
        self.assertEqual(self.shown(), "think1.png")

    def test_repeated_state_keeps_both_frame_and_elapsed_time(self):
        self.frames(FaceState.IDLE, "0.png", "1.png", "2.png")
        renderer = self.renderer()
        renderer.render(0.1)
        renderer.render(0.06)
        renderer.set_state(FaceState.IDLE)
        renderer.render(0)
        self.assertEqual(self.shown(), "1.png")
        renderer.render(0.04)
        self.assertEqual(self.shown(), "2.png")

    def test_missing_states_reuse_idle_animation(self):
        self.frames(FaceState.IDLE, "idle0.png", "idle1.png")
        renderer = self.renderer()
        for state in FaceState:
            with self.subTest(state=state):
                renderer.set_state(state)
                renderer.render(0)
                self.assertEqual(self.shown(), "idle0.png")
                renderer.render(0.1)
                self.assertEqual(self.shown(), "idle1.png")

    def test_corrupt_or_unreadable_frames_are_skipped_and_empty_state_uses_idle(self):
        paths = self.frames(FaceState.IDLE, "0.png", "1.png", "2.png", "3.png")
        paths[1].write_bytes(b"corrupt")
        paths[2].write_bytes(b"os-error")
        self.frames(FaceState.ERROR, "broken.png")[0].write_bytes(b"corrupt")
        renderer = self.renderer()
        renderer.render(0)
        self.assertEqual(self.shown(), "0.png")
        renderer.render(0.1)
        self.assertEqual(self.shown(), "3.png")
        renderer.set_state(FaceState.ERROR)
        renderer.render(0)
        self.assertEqual(self.shown(), "0.png")
        self.assertEqual(len(self.backend.scales), 2)

    def test_missing_or_corrupt_idle_uses_original_rgb_fallback_for_every_state(self):
        for case in ("missing", "corrupt"):
            with self.subTest(case=case):
                root = self.root / case
                if case == "corrupt":
                    self.frames(FaceState.IDLE, "broken.png", root=root)[0].write_bytes(b"bad")
                renderer = self.renderer(root)
                fallback = self.backend.created_surfaces[-1]
                self.assertEqual(fallback.size, self.surface.size)
                self.assertEqual(fallback.colors, [FALLBACK_COLOR])
                self.assertEqual(FALLBACK_COLOR, (142, 214, 186))
                for state in FaceState:
                    renderer.set_state(state)
                    renderer.render(0)
                    self.assertIs(self.surface.blits[-1][0], fallback)
                self.assertEqual(self.surface.colors, [])

    def test_explicit_asset_root_overrides_default_and_is_independent_of_cwd(self):
        self.frames(FaceState.IDLE, "chosen.png")
        other = self.root / "other-working-directory"
        self.frames(FaceState.IDLE, "wrong.png", root=other)
        previous = Path.cwd()
        try:
            os.chdir(other)
            with patch.object(face_module, "DEFAULT_ASSET_ROOT", other):
                renderer = self.renderer()
                renderer.render(0)
        finally:
            os.chdir(previous)
        self.assertEqual(self.shown(), "chosen.png")
        self.assertEqual(self.backend.loads, [self.root / "idle" / "chosen.png"])

    def test_default_asset_root_is_absolute_and_usable_after_cwd_change(self):
        self.assertTrue(face_module.DEFAULT_ASSET_ROOT.is_absolute())
        self.frames(FaceState.IDLE, "default.png")
        other = self.root / "other-working-directory"
        other.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(other)
            with patch.object(face_module, "DEFAULT_ASSET_ROOT", self.root):
                renderer = PygameFace(self.surface, pygame_module=self.backend)
                renderer.render(0)
        finally:
            os.chdir(previous)
        self.assertEqual(self.shown(), "default.png")

    def test_close_is_idempotent_and_leaves_caller_owned_ui_and_audio_alone(self):
        self.frames(FaceState.IDLE, "0.png")
        renderer = self.renderer()
        renderer.render(0)
        before = list(self.surface.blits)
        renderer.close()
        renderer.close()
        renderer.render(1)
        renderer.set_state(FaceState.THINKING)
        self.assertEqual(self.surface.blits, before)
        for operation in (self.backend.init, self.backend.quit, self.backend.display.quit,
                          self.backend.display.set_mode, self.backend.display.flip,
                          self.backend.mixer.init, self.backend.event.get):
            operation.assert_not_called()

    def test_invalid_state_or_elapsed_time_is_rejected_before_drawing(self):
        renderer = self.renderer()
        for state in (None, "idle", 0, object()):
            with self.subTest(state=state), self.assertRaises(TypeError):
                renderer.set_state(state)
        for dt in (None, "0", True, False, -0.1, float("nan"),
                   float("inf"), -float("inf"), complex(0)):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                renderer.render(dt)
        self.assertEqual(self.surface.blits, [])
        renderer.render(0)
        renderer.render(1)
        self.assertEqual(len(self.surface.blits), 2)

    def test_module_import_and_injected_backend_work_when_pygame_is_absent(self):
        spec = importlib.util.spec_from_file_location(
            "bmo_ai._face_without_pygame", face_module.__file__
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"pygame": None}):
            spec.loader.exec_module(module)
            renderer = module.PygameFace(
                self.surface, asset_root=self.root, pygame_module=self.backend
            )
            renderer.render(0)
        self.assertEqual(len(self.surface.blits), 1)

    def test_conversation_app_renders_all_five_states_with_mock_brain_and_audio(self):
        for state in FaceState:
            self.frames(state, state.value + ".png")
        renderer = self.renderer()
        recording = self.root / "mock-recording.wav"
        recording.touch()

        def show(state):
            self.assertEqual(renderer.state, state)
            renderer.render(0)

        def record(*args, **kwargs):
            show(FaceState.LISTENING)
            return recording

        def answer(message):
            show(FaceState.THINKING)
            return "mock reply"

        brain = SimpleNamespace(ask=Mock(side_effect=answer))
        client = object()
        app = ConversationApp(brain, client, BMOConfig(), face=renderer)
        app.idle()
        show(FaceState.IDLE)
        with patch("bmo_ai.app.audio.record_wav", side_effect=record) as record_mock, patch(
            "bmo_ai.app.audio.transcribe", return_value="mock input"
        ) as transcribe_mock, patch(
            "bmo_ai.app.audio.speak", side_effect=lambda *args, **kwargs: show(FaceState.SPEAKING)
        ) as speak_mock:
            self.assertEqual(app.voice_input(), "mock input")
            self.assertFalse(recording.exists())
            reply = app.answer("mock input")
            self.assertEqual(reply, "mock reply")
            app.playback(reply)
        app.failed()
        show(FaceState.ERROR)
        self.assertEqual(
            [frame.source.parent.name for frame, _ in self.surface.blits],
            ["idle", "listening", "thinking", "speaking", "error"],
        )
        record_mock.assert_called_once()
        transcribe_mock.assert_called_once()
        brain.ask.assert_called_once_with("mock input")
        speak_mock.assert_called_once()
