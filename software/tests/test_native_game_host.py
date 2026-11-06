"""Native SuperTux ownership through the real Host, with no real game/device.

The runner below controls process/window/pad boundaries independently. Actual
process-group and GUI-environment behavior is covered by the native adapter's
own synthetic-process checks; no USB model or real button number is assumed.
"""
from collections import deque
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.contracts import CatalogItem, FeatureError
from bmo_ai.features.games import GamesFeature
from bmo_ai.features.games.native import NativeGamesFeature
from bmo_ai.features.games.packaged import GAME_IDS
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.input.microphone import SyntheticPcmSource
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class Clock:
    def __init__(self):
        self.now = 10.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class NativeRunnerBoundary:
    simulated = False
    verified = True
    capabilities = frozenset(("native_supertux", "audio_stop", "exit_hold"))

    def __init__(self):
        self.ready = self.window_detected = self.exit_requested = self.owns_resources = False
        self.failure_code = "native_game_failed"
        self.probe_window = self.cleanup_failed = False
        self.terminal = None
        self.starts = self.stops = 0
        self.pad_edges = deque()
        self.pad_connected = True

    def start(self, _item):
        self.starts += 1
        self.owns_resources = True

    def poll(self):
        if not self.pad_connected:
            self.failure_code = "native_pad_disconnected"
            return "failed"
        for logical, pressed in self.pad_edges:
            if logical == "verified_exit_chord" and pressed:
                self.exit_requested = True
        self.pad_edges.clear()
        self.window_detected = self.probe_window
        self.ready = self.probe_window and self.pad_connected
        return self.terminal

    def stop(self):
        self.stops += 1
        if self.cleanup_failed:
            raise FeatureError("cleanup_failed")
        self.owns_resources = self.ready = False

    close = stop


class DesktopResource:
    _bridge = SimpleNamespace(sha256="a" * 64)

    def __init__(self):
        self.state = "running"
        self.closed = False

    def poll(self):
        return dict(state=self.state, code="browser_failed")

    def mark_ready(self):
        return True

    def close(self):
        self.closed = True


class NoGreeting:
    def play(self, **_kwargs):
        raise AssertionError("Silent native fixture must not play a greeting")

    def close(self):
        pass


class NativeGameHostTests(unittest.TestCase):
    def setUp(self):
        self.hosts, self.runners, self.apps = [], [], []
        self.clock = Clock()
        self.sources, self.transcriptions, self.player_starts = [], [], []
        self.sequence = 0
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.cloud.create_client", "bmo_ai.fish_tts.synthesize_wav",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("Real process/device/provider forbidden")))

    def tearDown(self):
        for runner in self.runners:
            runner.cleanup_failed = False
        for app in reversed(self.apps):
            app.close()
        for host in reversed(self.hosts):
            host.close()

    def native_feature(self, *, readiness="READY", code="native_ready_to_launch"):
        item = CatalogItem("supertux", "SuperTux", readiness, code,
                           ("native_supertux",), False)
        runner = NativeRunnerBoundary()
        self.runners.append(runner)
        return NativeGamesFeature(item=item, runner_factory=lambda _item: runner,
                                  clock=self.clock), runner

    def host(self, *, feature=None, playable_games=True, enabled=True, **options):
        if feature is None:
            feature, runner = self.native_feature()
        else:
            runner = self.runners[-1]
        config = integration_config()
        client = FeatureSimulatedClient([dict(version=1, speech="I am back.", emotion="neutral",
                                              gesture="none", intent=None)])

        def player(_command, *, on_start, cancel_check, **_kwargs):
            cancel_check()
            on_start()
            self.player_starts.append(1)
            cancel_check()

        voice = IntegrationVoice(config, client, simulate=True, player=player,
                                  emit=lambda *_args, **_kwargs: None)

        def source():
            self.sources.append(1)
            return SyntheticPcmSource(clock=self.clock)

        def transcribe(*_args, **_kwargs):
            self.transcriptions.append(1)
            return "How are you now?"

        host = FeatureHost(config=config, client=client, voice=voice, clock=self.clock,
            runtime=RuntimePolicy(simulate=False, conversation_ptt_microphone=True),
            playable_games=playable_games, native_supertux=enabled, native_game_feature=feature,
            usb_gamepad=True, ptt_source_factory=source, ptt_transcriber=transcribe,
            ptt_speech_safe=True, **options)
        self.hosts.append(host)
        return host, runner

    def launch(self, host, request="launch_native"):
        return host.execute(HostIntent("launch_game", "supertux"), request_id=request)

    def start_native(self, host, runner):
        result = self.launch(host)
        self.assertEqual(result.code, "native_game_starting")
        runner.probe_window = True
        host.poll()
        self.assertEqual(host.games.snapshot()["state"], "running")

    def pump(self, host, predicate):
        deadline = time.monotonic() + 4.
        while time.monotonic() < deadline:
            host.heartbeat()
            host.poll()
            if predicate():
                return
            time.sleep(.002)
        self.fail("Native/Host fixture did not reach its owned boundary")

    def browser_key(self, host, key, pressed=True, *, action="gamepad", queued=False,
                    generation=None):
        self.sequence += 1
        value = dict(key=key, pressed=pressed, sequence=self.sequence,
                     generation=host.modes.generation if generation is None else generation)
        command = HostControl(action, value)
        request = "browser_native_" + str(self.sequence)
        return host.queue_command(request, command) if queued else host.control(command, request)

    def desktop(self, host):
        app = DesktopRuntime(clock=self.clock, simulate=False, silent_start=True)
        browser = DesktopResource()
        app.set_server(DesktopResource())
        app.set_browser(browser)
        app.set_host(host, NoGreeting())
        app.queue_desktop_control("nativeFaceReady", {"action": "face_ready", "value": {
            "run_id": app.run_id, "screen_sha256": "a" * 64,
            "frame_version": "native-offline-v1"}})
        app.heartbeat()
        app.poll()
        self.assertEqual(app.startup_snapshot()["phase"], "ready")
        self.apps.append(app)
        return app, browser

    def test_explicit_native_permission_is_required_and_keeps_existing_three_games(self):
        with self.assertRaises(FeatureError):
            self.host(enabled=False)
        host, _runner = self.host()
        self.assertEqual({item.id for item in host.games.catalog()}, set(GAME_IDS) | {"supertux"})
        self.assertEqual(host.runtime.public()["external_games"], "explicit_supertux_only")
        # The explicit route does not turn arbitrary native catalogue entries
        # or arbitrary command strings into runnable games.
        self.assertEqual(host.execute(HostIntent("launch_game", "arbitrary_native")).code, "unknown_game")
        arbitrary = CatalogItem("arbitrary_native", "Unreviewed native title", "READY", "ok", (), False)
        other, _ = self.host(playable_games=False, games=GamesFeature((arbitrary,),
            runner_factory=lambda _item: self.fail("Arbitrary native runner was opened")))
        self.assertEqual(other.execute(HostIntent("launch_game", arbitrary.id)).code, "real_disabled")
        with self.assertRaises(FeatureError):
            FeatureHost(client=FeatureSimulatedClient(),
                        runtime=RuntimePolicy(simulate=False, native_supertux=True))

    def test_missing_install_is_distinct_and_does_not_spawn_or_claim_game_ownership(self):
        feature, runner = self.native_feature(readiness="RUNTIME_MISSING", code="native_supertux_not_installed")
        host, _ = self.host(feature=feature)
        result = self.launch(host)
        self.assertEqual(result.code, "native_supertux_not_installed")
        self.assertEqual(host.games.snapshot()["native"]["state"], "not_installed")
        self.assertEqual(runner.starts, 0)
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)

    def test_process_creation_is_only_starting_until_window_and_input_boundary(self):
        host, runner = self.host()
        result = self.launch(host)
        self.assertEqual((result.status, result.code, result.executed),
                         ("accepted", "native_game_starting", False))
        self.assertEqual(runner.starts, 1)
        self.assertTrue(host.native_game_foreground)
        self.assertEqual(host.games.snapshot()["state"], "starting")
        self.assertEqual(host.audio.owner, "game")
        for _ in range(3):
            host.poll()
        self.assertEqual(host._result_history["launch_native"].status, "accepted")
        runner.probe_window = True
        host.poll()
        receipt = host._result_history["launch_native"]
        self.assertEqual((receipt.status, receipt.executed, receipt.completed), ("running", True, False))
        self.assertEqual(receipt.completion, "window_observed")
        self.assertEqual(host.games.snapshot()["state"], "running")
        self.assertEqual(self.sources + self.transcriptions + self.player_starts, [])
        self.launch(host)  # Same nonce cannot create another owned process.
        self.assertEqual(runner.starts, 1)

    def test_native_owner_ignores_retired_browser_pad_keyboard_blur_and_gp8_release(self):
        host, runner = self.host()
        self.start_native(host, runner)
        for action in ("gamepad", "game_key"):
            for key, pressed in (("PAD_LOST", True), ("A", False), ("RELEASE", True)):
                with self.subTest(action=action, key=key):
                    result = self.browser_key(host, key, pressed, action=action)
                    self.assertEqual(result.code, "native_game_owns_input")
        for pressed in (True, False):
            result = host.control(HostControl("input", {"key": "TALK", "pressed": pressed}),
                                  "native_talk_" + str(pressed))
            self.assertEqual(result.code, "native_game_owns_input")
        host.poll()
        self.assertEqual(host.modes.mode, "game")
        self.assertEqual(runner.stops, 0)
        self.assertEqual(self.sources + self.transcriptions, [])

    def test_queued_old_browser_input_cannot_disconnect_new_native_owner(self):
        host, runner = self.host()
        host.execute(HostIntent("open_game_menu"), request_id="old_game_menu")
        self.browser_key(host, "A", False, action="game_key", queued=True)
        self.start_native(host, runner)
        self.clock.advance(1.)  # Also exceed the old browser pad lease.
        host.poll()
        self.assertEqual(host.modes.mode, "game")
        self.assertEqual(host.audio.owner, "game")
        self.assertEqual(runner.stops, 0)

    def test_native_audio_scope_does_not_claim_master_volume_or_physical_pad_verification(self):
        host, runner = self.host()
        self.start_native(host, runner)
        native = host.games.snapshot()["native"]
        self.assertEqual(native["audio_scope"], "silent_native")
        self.assertFalse(native["master_volume_applies"])
        self.assertFalse(native["physical_input_verified"])
        self.assertEqual(native["input_owner"], "supertux_sdl")
        host.execute(HostIntent("mute_audio"), request_id="mute_bmo_while_native")
        self.assertTrue(host.volume.snapshot()["muted"])
        self.assertEqual(host.audio.owner, "game")
        self.assertEqual(runner.stops, 0)
        host.execute(HostIntent("return_to_face"), request_id="native_audio_back")
        self.assertIsNone(host.audio.owner)
        self.assertTrue(host.volume.snapshot()["muted"])  # Preserved BMO setting.

    def test_native_exit_and_failure_return_to_face_and_new_gp8_dialogue(self):
        for terminal in ("ended", "failed", "pad_exit"):
            with self.subTest(terminal=terminal):
                host, runner = self.host()
                self.start_native(host, runner)
                if terminal == "pad_exit":
                    runner.pad_edges.append(("verified_exit_chord", True))
                else:
                    runner.terminal = terminal
                self.pump(host, lambda: host.modes.mode == "conversation")
                self.assertFalse(runner.owns_resources)
                self.assertFalse(host.native_game_foreground)
                self.assertIsNone(host.audio.owner)
                before = len(self.transcriptions)
                result = host.control(HostControl("input", {"key": "TALK", "pressed": True}),
                                      "after_native_" + terminal)
                self.assertNotEqual(result.status, "rejected")
                self.pump(host, lambda: not host.session.busy and len(self.transcriptions) > before)
                self.assertEqual(len(host.client.requests), 1)
                self.assertIsNone(host.audio.owner)

    def test_cleanup_unknown_keeps_mode_and_audio_until_successful_owned_retry(self):
        host, runner = self.host()
        self.start_native(host, runner)
        runner.cleanup_failed = True
        result = host.execute(HostIntent("return_to_face"), request_id="native_cleanup_unknown")
        self.assertIn(result.status, ("failed", "rejected", "unknown"))
        self.assertEqual(host.modes.mode, "game")
        self.assertEqual(host.audio.owner, "game")
        self.assertTrue(host.native_game_foreground)
        result = host.control(HostControl("input", {"key": "TALK", "pressed": True}), "blocked_after_cleanup")
        self.assertEqual(result.code, "native_game_owns_input")
        self.assertEqual(self.sources, [])
        runner.cleanup_failed = False
        host.execute(HostIntent("return_to_face"), request_id="native_cleanup_recovered")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)
        self.assertFalse(runner.owns_resources)

    def test_native_foreground_can_outlive_hidden_browser_heartbeat_but_not_browser_process(self):
        host, runner = self.host()
        app, browser = self.desktop(host)
        self.start_native(host, runner)
        app.poll()
        self.clock.advance(6.)
        app.poll()  # The browser is alive but hidden by the native window.
        self.assertFalse(app.failed)
        self.assertEqual(host.modes.mode, "game")
        self.assertEqual(runner.stops, 0)
        browser.state = "failed"
        app.poll()
        self.assertTrue(app.failed)
        self.assertTrue(app.exiting)
        app.close()
        self.assertFalse(runner.owns_resources)

    def test_return_grace_ends_and_normal_browser_disconnect_watchdog_returns(self):
        host, runner = self.host()
        app, _browser = self.desktop(host)
        self.start_native(host, runner)
        app.poll()
        self.clock.advance(8.)
        app.poll()
        runner.terminal = "ended"
        app.poll()
        self.assertEqual(host.modes.mode, "conversation")
        self.assertFalse(app.failed)  # Old pre-game heartbeat cannot fail return.
        self.clock.advance(1.)
        app.poll()
        self.assertFalse(app.failed)
        app.heartbeat()  # Restored shell acknowledges the current face.
        self.clock.advance(6.)
        app.poll()
        self.assertTrue(app.failed)
        self.assertEqual(app.startup_snapshot()["code"], "browser_disconnected")


if __name__ == "__main__":
    unittest.main()
