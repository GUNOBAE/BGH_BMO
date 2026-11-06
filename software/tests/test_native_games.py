"""Silent native boundaries using metadata fakes and harmless Python children.

No SuperTux, display server, physical pad, microphone, audio or network is used.
"""
from collections import deque
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import CatalogItem, FeatureError
from bmo_ai.features.games import FakeGameRunner, GamesFeature
from bmo_ai.features.games.combined import CombinedGamesFeature
from bmo_ai.features.games.native import (
    EXECUTABLE, XPROP, XWININFO, ExitChord, JoystickExitSource, NativeGamesFeature,
    NativeGameRunner, NativePadProfile, X11WindowProbe, build_native_supertux,
    load_pad_profile, native_environment,
)


class FakePad:
    def __init__(self):
        self.events = deque()
        self.closed = False
        self.disconnected = False

    def poll(self):
        if self.disconnected:
            raise FeatureError("native_pad_disconnected")
        return tuple(self.events.popleft() for _ in range(min(64, len(self.events))))

    def close(self):
        self.closed = True


def ready_item():
    return CatalogItem("supertux", "SuperTux", "READY", "native_ready_to_launch",
                       ("native_supertux", "native_pad_exit", "silent_game"), False)


class NativeProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-native-tests-"))
        self.root = Path(self.folder)
        self.root.chmod(0o700)
        self.profile = self.root / "observed-pad.json"
        self.value = dict(version=1, game_id="supertux", device="/dev/input/by-id/usb-Observed_fixture-joystick",
                          exit_buttons=[12, 19], provenance="pi_observed_fixture")
        self.write_profile()

    def write_profile(self):
        self.profile.write_text(json.dumps(self.value))
        self.profile.chmod(0o600)

    def test_profile_has_only_observed_exit_controls_and_no_command_or_default_mapping(self):
        value = load_pad_profile(self.profile)
        self.assertEqual(value.exit_buttons, (12, 19))
        self.assertEqual(value.provenance, "pi_observed_fixture")
        for changed in ({"exit_buttons": [3, 3]}, {"exit_buttons": [True, 2]},
                        {"provenance": "guessed_usb_pad"}, {"device": "/dev/input/event0"},
                        {"command": ["sh", "-c", "anything"]}):
            with self.subTest(changed=changed):
                original = dict(self.value)
                self.value.update(changed); self.write_profile()
                with self.assertRaises(FeatureError):
                    load_pad_profile(self.profile)
                self.value = original

    def test_private_file_permissions_links_and_duplicate_json_fields_are_rejected(self):
        self.profile.chmod(0o644)
        with self.assertRaises(FeatureError):
            load_pad_profile(self.profile)
        self.profile.chmod(0o600)
        linked = self.root / "linked.json"
        linked.symlink_to(self.profile)
        with self.assertRaises(FeatureError):
            load_pad_profile(linked)
        os.link(self.profile, self.root / "hardlink")
        with self.assertRaises(FeatureError):
            load_pad_profile(self.profile)
        (self.root / "hardlink").unlink()
        self.profile.write_text('{"version":1,"version":1}')
        with self.assertRaises(FeatureError):
            load_pad_profile(self.profile)

    def test_checkout_profile_and_game_home_are_rejected(self):
        (self.root / ".git").mkdir()
        with self.assertRaises(FeatureError) as caught:
            load_pad_profile(self.profile)
        self.assertEqual(caught.exception.code, "native_outside_checkout_required")
        with self.assertRaises(FeatureError):
            native_environment({"DISPLAY": ":0"}, self.root / "private")

    def test_only_needed_gui_environment_passes_and_home_is_private_game_directory(self):
        home = self.root / "game-home"
        env = native_environment({"DISPLAY": ":0.0", "HOME": "/original/home",
            "OPENAI_API_KEY": "synthetic-secret-a", "FISH_API_KEY": "synthetic-secret-b",
            "GH_TOKEN": "synthetic-secret-c", "LD_PRELOAD": "/bad/lib.so",
            "PYTHONPATH": "/bad", "PULSE_SERVER": "tcp:remote", "WAYLAND_DISPLAY": "wayland-0",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/some/bus"}, home)
        self.assertEqual(set(env), {"PATH", "LANG", "LC_ALL", "DISPLAY", "HOME", "XDG_CONFIG_HOME",
                                   "XDG_CACHE_HOME", "XDG_DATA_HOME", "SDL_VIDEODRIVER", "SDL_AUDIODRIVER"})
        self.assertEqual(env["HOME"], str(home))
        self.assertEqual((env["SDL_VIDEODRIVER"], env["SDL_AUDIODRIVER"]), ("x11", "dummy"))
        self.assertFalse(home.exists())  # Read-only construction/status.
        self.assertNotIn("synthetic-secret", repr(env))
        for display in ("host:0", ":0;anything", "", None):
            with self.assertRaises(FeatureError):
                native_environment({"DISPLAY": display}, home)

    def test_xauthority_is_metadata_only_and_must_be_regular_owned_not_writable_by_others(self):
        path = self.root / "Xauthority"
        path.write_text("synthetic auth, not read")
        path.chmod(0o600)
        self.assertEqual(native_environment({"DISPLAY": ":1", "XAUTHORITY": str(path)}, self.root / "home")["XAUTHORITY"], str(path))
        path.chmod(0o666)
        with self.assertRaises(FeatureError):
            native_environment({"DISPLAY": ":1", "XAUTHORITY": str(path)}, self.root / "home")

    def test_implicit_original_xauthority_survives_isolated_game_home_without_reading_content(self):
        path = self.root / ".Xauthority"
        path.write_bytes(b"not copied or decoded")
        path.chmod(0o600)
        env = native_environment({"DISPLAY": ":1", "HOME": str(self.root)}, self.root / "game-home")
        self.assertEqual(env["HOME"], str(self.root / "game-home"))
        self.assertEqual(env["XAUTHORITY"], str(path))
        self.assertNotIn("not copied", repr(env))

    def test_missing_install_or_mapping_is_visible_without_process_or_device_open(self):
        with patch("bmo_ai.features.games.native._executable", side_effect=FeatureError("native_supertux_not_installed")), \
             patch("subprocess.Popen", side_effect=AssertionError("process forbidden")):
            feature = build_native_supertux()
        self.assertEqual(feature.catalog()[0].readiness, "RUNTIME_MISSING")
        self.assertEqual(feature.snapshot()["native"]["state"], "not_installed")
        with patch("bmo_ai.features.games.native._executable", side_effect=lambda path, code: path), \
             patch("bmo_ai.features.games.native.JoystickExitSource", side_effect=AssertionError("device forbidden")):
            feature = build_native_supertux(environment={"DISPLAY": ":1"})
        self.assertEqual(feature.catalog()[0].reason_code, "native_pad_profile_required")
        self.assertEqual(feature.snapshot()["native"]["state"], "setup_required")

    def test_production_factory_has_fixed_verified_silent_command_and_rechecks_mapping(self):
        with patch("bmo_ai.features.games.native._executable", side_effect=lambda path, code: path), \
             patch("bmo_ai.features.games.native._joystick_target", return_value=("/dev/input/js99", (1, 2, 3))), \
             patch("bmo_ai.features.games.native.default_game_home", return_value=self.root / "home"):
            feature = build_native_supertux(profile_path=self.profile, environment={"DISPLAY": ":1", "FISH_API_KEY": "not-passed"})
            self.assertEqual(feature.catalog()[0].readiness, "READY")
            runner = feature._factory(feature.catalog()[0])
            self.assertEqual(runner.command, (EXECUTABLE, "--fullscreen", "--disable-sound", "--disable-music", "--userdir", str(self.root / "home/userdata")))
            self.assertNotIn("FISH_API_KEY", runner.environment)
            self.value["exit_buttons"] = [12, 20]; self.write_profile()
            with patch("subprocess.Popen", side_effect=AssertionError("process forbidden")), self.assertRaises(FeatureError) as caught:
                runner.start(feature.catalog()[0])
            self.assertEqual(caught.exception.code, "native_pad_profile_changed")
            self.assertFalse(runner.owns_resources)


class NativeExitTests(unittest.TestCase):
    def test_initial_held_release_fresh_chord_threshold_and_duplicate_edges(self):
        chord = ExitChord((12, 19))
        self.assertFalse(chord.update(((12, True, True), (19, True, True)), 0))
        self.assertFalse(chord.update((), 20))
        self.assertFalse(chord.update(((12, False, False), (19, False, False)), 21))
        self.assertFalse(chord.update(((12, True, False), (19, True, False)), 22))
        self.assertFalse(chord.update(((12, True, False),), 23.49))
        self.assertTrue(chord.update((), 23.5))
        self.assertTrue(chord.update((), 24))

    def test_release_resets_timer_and_other_buttons_are_not_global_controls(self):
        chord = ExitChord((12, 19))
        chord.update(((12, True, False), (19, True, False)), 0)
        self.assertFalse(chord.update(((19, False, False), (0, True, False)), 1.4))
        self.assertFalse(chord.update((), 3))
        self.assertFalse(chord.update(((19, True, False),), 4))
        self.assertFalse(chord.update((), 5.49))
        self.assertTrue(chord.update((), 5.5))
        with self.assertRaises(FeatureError):
            chord.update((), 3)

    def test_joydev_parser_discards_axes_and_marks_initial_edges_without_opening_device(self):
        source = object.__new__(JoystickExitSource)
        source._fd = 99
        import struct
        raw = b"".join(struct.pack("<IhBB", 100, value, kind, button)
                       for value, kind, button in ((30000, 2, 0), (1, 0x81, 12), (0, 1, 19)))
        with patch("os.read", return_value=raw):
            self.assertEqual(source.poll(), ((12, True, True), (19, False, False)))
        with patch("os.read", side_effect=BlockingIOError):
            self.assertEqual(source.poll(), ())
        with patch("os.read", return_value=b""), self.assertRaises(FeatureError) as caught:
            source.poll()
        self.assertEqual(caught.exception.code, "native_pad_disconnected")
        source._fd = None

    def test_observed_exit_indices_must_exist_on_opened_device_before_game_spawn(self):
        profile = NativePadProfile("/dev/input/by-id/usb-Fixture-joystick", (12, 19), "pi_observed_fixture")
        info = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_dev=1, st_ino=2, st_rdev=3)
        def ioctl(fd, request, buffer, mutate):
            self.assertEqual(request, 0x80016A12)
            buffer[0] = 15  # index19 cannot exist on this different/changed pad.
        with patch("bmo_ai.features.games.native._joystick_target", return_value=("/dev/input/js99", (1, 2, 3))), \
             patch("os.open", return_value=99), patch("os.fstat", return_value=info), \
             patch("fcntl.ioctl", side_effect=ioctl), patch("os.close") as close, \
             self.assertRaises(FeatureError) as caught:
            JoystickExitSource(profile)
        self.assertEqual(caught.exception.code, "native_pad_mapping_mismatch")
        close.assert_called_once_with(99)


class NativeWindowTests(unittest.TestCase):
    def run_with(self, outputs):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=outputs.pop(0).encode())
        return X11WindowProbe({"DISPLAY": ":99", "PATH": os.defpath}, run=run), calls

    def test_only_active_owned_mapped_nonzero_window_is_ready(self):
        probe, calls = self.run_with(["_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2001\n",
            "_NET_WM_PID(CARDINAL) = 123\n", " Width: 800\n Height: 480\n Map State: IsViewable\n"])
        self.assertTrue(probe(123))
        self.assertTrue(probe.window_detected)
        self.assertEqual([x[0][0] for x in calls], [XPROP, XPROP, XWININFO])
        self.assertTrue(all(x[1]["timeout"] == .25 for x in calls))
        self.assertTrue(all(x[1]["env"] == {"DISPLAY": ":99", "PATH": os.defpath} for x in calls))
        self.assertFalse(any("shell" in x[1] for x in calls))

    def test_another_process_unmapped_window_malformed_data_and_tool_timeout_never_ready(self):
        for outputs in (["_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0\n"],
                        ["_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2001\n", "_NET_WM_PID(CARDINAL) = 999\n"],
                        ["_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2001\n", "_NET_WM_PID(CARDINAL) = 123\n", " Width: 800\n Height: 480\n Map State: IsUnMapped\n"],
                        ["malformed 0x2001\n"]):
            probe, _ = self.run_with(outputs)
            self.assertFalse(probe(123))
        probe = X11WindowProbe({}, run=Mock(side_effect=subprocess.TimeoutExpired("xprop", .25)))
        self.assertFalse(probe(123))


class NativeProcessTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.pad = FakePad()
        self.window = False
        self.runners = []

    def runner(self, code="import time;time.sleep(30)", **settings):
        runner = NativeGameRunner(command=(sys.executable, "-B", "-c", code),
            environment={"PATH": os.defpath, "LANG": "C.UTF-8"}, pad_factory=lambda: self.pad,
            window_probe=lambda pid: self.window, exit_buttons=(12, 19), clock=lambda: self.now, **settings)
        self.runners.append(runner)
        self.addCleanup(runner.stop)
        return runner

    def feature(self, runner):
        feature = NativeGamesFeature(item=ready_item(), runner_factory=lambda _: runner, clock=lambda: self.now)
        self.addCleanup(feature.close)
        return feature

    def start(self, feature):
        return feature.start("supertux", request_id="native:test", mode_generation=1)

    def test_spawn_waits_for_owned_window_then_emits_started_only_once(self):
        runner = self.runner()
        feature = self.feature(runner)
        result = self.start(feature)
        self.assertEqual((result.status, result.code), ("accepted", "native_game_starting"))
        self.assertFalse(result.executed or result.completed)
        self.assertTrue(feature.native_active)
        self.assertEqual(feature.snapshot()["native"]["state"], "starting")
        self.assertEqual(feature.poll(), ())
        self.window = True; self.now = .2
        events = feature.poll()
        self.assertEqual([(e.kind, e.code) for e in events], [("game_started", "native_window_ready")])
        self.assertFalse(events[0].data["physical_input_verified"])
        self.assertEqual(feature.poll(), ())
        self.assertEqual(feature.snapshot()["native"]["state"], "running")
        self.assertTrue(feature.snapshot()["native"]["window_detected"])
        process = runner.process
        result = feature.stop()
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(self.pad.closed)
        self.assertFalse(feature.native_active)
        self.assertIsNotNone(process.returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    def test_timeout_and_disconnect_cleanup_before_failure_event(self):
        for kind in ("timeout", "disconnect"):
            with self.subTest(kind=kind):
                self.pad = FakePad(); self.now = 0.
                runner = self.runner(startup_timeout=1.)
                feature = self.feature(runner)
                self.start(feature)
                process = runner.process
                if kind == "timeout":
                    self.now = 1.
                else:
                    self.pad.disconnected = True
                events = feature.poll()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0].kind, "game_failed")
                self.assertEqual(events[0].code, "native_window_timeout" if kind == "timeout" else "native_pad_disconnected")
                self.assertTrue(events[0].data["resource_released"])
                self.assertFalse(feature.native_active)
                self.assertIsNotNone(process.returncode)
                self.assertTrue(self.pad.closed)

    def test_pad_hold_requests_exit_once_without_releasing_before_host_stop(self):
        runner = self.runner(); feature = self.feature(runner)
        self.window = True; self.start(feature); feature.poll()
        self.pad.events.extend(((12, True, False), (19, True, False)))
        self.assertEqual(feature.poll(), ())
        self.now = 1.49; self.assertEqual(feature.poll(), ())
        self.now = 1.5
        events = feature.poll()
        self.assertEqual([e.kind for e in events], ["game_exit_requested"])
        self.assertFalse(events[0].data["resource_released"])
        self.assertTrue(feature.native_active)
        self.assertEqual(feature.poll(), ())
        feature.stop("game_exit")
        self.assertFalse(feature.native_active)

    def test_browser_pad_and_keys_never_stop_or_inject_native_input(self):
        runner = self.runner(); feature = self.feature(runner); self.start(feature)
        for key in ("ESC", "PAD_DISCONNECTED", "A", "START", "SELECT"):
            self.assertEqual(feature.game_input(key)["action"], "native_input_owned")
        self.assertTrue(feature.native_active)
        self.assertEqual(feature.poll(), ())

    def test_cleanup_failure_retains_native_owner_and_rejects_another_start(self):
        runner = self.runner(); feature = self.feature(runner); self.start(feature)
        original = runner.stop
        with patch.object(runner, "stop", side_effect=FeatureError("cleanup_failed")):
            result = feature.stop()
            self.assertEqual((result.status, result.code), ("unknown", "cleanup_failed"))
            self.assertTrue(feature.native_active and feature.native_foreground)
            self.assertEqual(feature.snapshot()["native"]["state"], "cleanup_failed")
            self.assertEqual(feature.start("supertux", request_id="native:other", mode_generation=2).code, "game_busy")
            self.assertEqual(feature.poll(), ())
        feature.stop()
        self.assertFalse(feature.native_active)

    def test_failed_spawn_closes_pad_and_does_not_claim_game_execution(self):
        runner = self.runner(); feature = self.feature(runner)
        with patch("subprocess.Popen", side_effect=OSError("private command details")):
            result = self.start(feature)
        self.assertEqual((result.status, result.code), ("failed", "native_spawn_failed"))
        self.assertTrue(self.pad.closed)
        self.assertFalse(feature.native_active or result.executed)
        self.assertNotIn("private command", repr(feature.snapshot()))

    def test_crash_and_natural_exit_are_distinct_and_stop_only_own_group(self):
        runner = self.runner(); feature = self.feature(runner)
        self.window = True; self.start(feature); feature.poll()
        process = runner.process
        os.kill(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 2
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)
        events = feature.poll()
        self.assertEqual((events[0].kind, events[0].code), ("game_failed", "native_game_crashed"))
        self.assertTrue(events[0].data["resource_released"])
        self.assertIsNotNone(process.returncode)

    def test_zero_exit_before_window_is_failure_but_ready_game_normal_exit_is_completed(self):
        with tempfile.TemporaryDirectory(prefix="bmo-native-exit-") as directory:
            for window_ready in (False, True):
                self.pad = FakePad(); self.window = window_ready; self.now = 0.
                release = Path(directory) / ("ready" if window_ready else "early")
                code = ("from pathlib import Path;import time;"
                        "p=Path(" + repr(str(release)) + ");"
                        "exec('while not p.exists(): time.sleep(.005)')")
                runner = self.runner(code)
                feature = self.feature(runner)
                self.start(feature); feature.poll()
                process = runner.process
                release.write_text("exit now")
                deadline = time.monotonic() + 2
                while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.005)
                events = feature.poll()
                self.assertEqual([event.kind for event in events], ["game_ended" if window_ready else "game_failed"])
                self.assertEqual(events[0].code, "native_exited" if window_ready else "native_game_crashed")
                self.assertTrue(events[0].data["resource_released"])
                self.assertFalse(feature.native_active)
                self.assertTrue(self.pad.closed)
                self.assertEqual(process.returncode, 0)


class CombinedNativeGamesTests(unittest.TestCase):
    def test_generic_empty_fallback_has_no_packaged_api_and_native_remains_visible(self):
        native = NativeGamesFeature(item=CatalogItem("supertux", "SuperTux", "UNVERIFIED", "native_pad_profile_required"))
        games = CombinedGamesFeature(GamesFeature(), native)
        self.addCleanup(games.close)
        self.assertEqual(games.resources(), {})
        self.assertFalse(games.is_packaged("supertux"))
        self.assertFalse(games.snapshot()["packaged"])
        self.assertEqual(games.catalog()[0].id, "supertux")
        with self.assertRaises(FeatureError):
            games.browser_ready("irrelevant", 1)
        with self.assertRaises(FeatureError):
            games.browser_outcome({})

    def test_web_catalogue_resources_handshake_and_selection_survive_native_addition(self):
        from bmo_ai.features.games.packaged import PackagedGamesFeature
        web = PackagedGamesFeature(clock=lambda: 0.)
        native = NativeGamesFeature(item=CatalogItem("supertux", "SuperTux", "RUNTIME_MISSING", "native_supertux_not_installed"))
        games = CombinedGamesFeature(web, native, clock=lambda: 0.)
        self.addCleanup(games.close)
        self.assertEqual([i.id for i in games.catalog()], ["bmo_blocks", "bmo_maze", "bmo_snake", "supertux"])
        self.assertEqual(games.resources(), web.resources())
        self.assertTrue(games.is_packaged("bmo_blocks"))
        self.assertTrue(games.is_native("supertux"))
        result = games.start("bmo_blocks", request_id="web:one", mode_generation=1)
        self.assertEqual(result.code, "web_game_loading")
        games.browser_ready(games.snapshot()["web"]["launch_id"], 1)
        self.assertEqual([e.kind for e in games.poll()], ["game_started"])
        games.game_input("LEFT")
        self.assertEqual(games.snapshot()["web"]["held"], ["LEFT"])
        games.stop()
        games.poll()
        for _ in range(3):
            games.menu_input("DOWN")
        selection = games.menu_input("A")
        self.assertEqual((selection["catalog_id"], selection["action"]), ("supertux", "select"))
        self.assertEqual(selection["code"], "native_supertux_not_installed")

    def test_native_with_no_web_bundle_keeps_ready_state_and_no_browser_resources(self):
        native = NativeGamesFeature(item=CatalogItem("supertux", "SuperTux", "UNVERIFIED", "native_pad_profile_required"))
        games = CombinedGamesFeature(native=native)
        self.addCleanup(games.close)
        self.assertEqual(games.resources(), {})
        self.assertFalse(games.snapshot()["packaged"])
        self.assertEqual(games.snapshot()["native"]["state"], "setup_required")
        with self.assertRaises(FeatureError):
            games.browser_ready("irrelevant", 1)


if __name__ == "__main__":
    unittest.main()
