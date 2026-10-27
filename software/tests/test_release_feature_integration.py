"""Production mode/action/input/media routing with only IO boundaries replaced."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai.features.contracts import FeatureError, HostResult
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.input.gamepad import BrowserGamepadGate
from bmo_ai.features.media.library import install_wav, review_wav
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy


class ReleaseFeatureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.hosts = []
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-release-features-")))
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.cloud.create_client"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def host(self, **kwargs):
        host = FeatureHost(clock=lambda: self.now, client=FeatureSimulatedClient(), **kwargs)
        self.hosts.append(host)
        return host

    def command(self, host, key, pressed=True, *, sequence=None, generation=None):
        self.sequence = getattr(self, "sequence", -1) + 1 if sequence is None else sequence
        return HostControl("gamepad", {"key": key, "pressed": pressed, "sequence": self.sequence,
            "generation": host.modes.generation if generation is None else generation})

    def pad(self, host, key, pressed=True, **kwargs):
        return host.control(self.command(host, key, pressed, **kwargs), "pad_test")

    def library(self, *, count=1):
        source = self.root / "generated.wav"
        with wave.open(str(source), "wb") as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(bytes(32000))
        library = self.root / "library"
        for index in range(count):
            install_wav(review_wav(source), library, "generated_song" if index == 0 else "generated_song_b",
                        title="Generated silence", license_id="test_generated", license_reviewed=True)
        return library

    def test_gamepad_default_off_and_global_keys_rejected(self):
        host = self.host(demo_assets=True)
        command = self.command(host, "A")
        with self.assertRaises(FeatureError) as caught:
            host.queue_command("pad_disabled", command)
        self.assertEqual(caught.exception.code, "gamepad_disabled")
        for key in ("TALK", "STOP", "MENU", "GP7", "GP8", "SERVO", "CAMERA"):
            with self.subTest(key=key), self.assertRaises(FeatureError):
                self.command(host, key)
        self.assertEqual(host.client.requests, [])
        self.assertFalse(host.snapshot()["usb_gamepad"]["enabled"])

    def test_gamepad_conversation_does_not_start_ptt_or_arm(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        for key in ("A", "B", "START", "SELECT", "UP", "ESC"):
            self.assertEqual(self.pad(host, key).code, "gamepad_mode_blocked")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertEqual(host.client.requests, [])
        self.assertIsNone(host.games._runner)
        self.assertEqual(host.motion.snapshot()["queued"], 0)
        self.assertFalse(host.motion.snapshot()["active"])

    def test_pad_menu_start_game_input_hold_exit_preserves_session(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        session, brain = host.session, host.session.brain
        host.session.set_emotion("sad")
        host.execute(HostIntent("open_game_menu"))
        self.assertEqual(self.pad(host, "A").status, "simulated")
        self.assertEqual(host.modes.mode, "game")
        runner = host.games._runner
        self.pad(host, "B"); self.pad(host, "B", False)
        self.assertIn(("B", True), runner.inputs)
        self.pad(host, "START"); self.pad(host, "SELECT")
        for _ in range(7):
            self.now += .2
            self.pad(host, "HEARTBEAT")
            host.poll()
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertIsNone(host.audio.owner)
        self.assertFalse(runner.running)
        self.pad(host, "ESC")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIs(host.session, session); self.assertIs(host.session.brain, brain)
        self.assertEqual(host.snapshot()["emotion"], "sad")
        self.assertEqual(host.client.requests, [])

    def test_generation_and_sequence_block_late_or_replayed_edges(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("open_game_menu"))
        generation = host.modes.generation
        self.pad(host, "HEARTBEAT", sequence=50)
        self.assertEqual(self.pad(host, "A", sequence=49).code, "repeated_gamepad_input")
        host.execute(HostIntent("return_to_face"))
        host.execute(HostIntent("open_game_menu"))
        result = self.pad(host, "A", sequence=51, generation=generation)
        self.assertEqual(result.code, "gamepad_mode_blocked")
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertIsNone(host.audio.owner)

    def test_pad_disconnect_and_watchdog_release_before_game_stop(self):
        for reason in ("PAD_LOST", "timeout"):
            with self.subTest(reason=reason):
                host = self.host(demo_assets=True, usb_gamepad=True)
                host.execute(HostIntent("launch_game", "fixture_game_a"))
                runner = host.games._runner
                self.pad(host, "LEFT")
                if reason == "timeout":
                    self.now += BrowserGamepadGate.WATCHDOG_S + .01
                else:
                    self.pad(host, "PAD_LOST")
                host.poll()
                self.assertIn(("LEFT", False), runner.inputs)
                self.assertEqual(host.modes.mode, "game_menu")
                self.assertFalse(runner.running)
                self.assertEqual(host.gamepad.snapshot()["held"], [])
                self.assertIsNone(host.audio.owner)

    def test_mode_change_releases_held_buttons_and_never_crosses_ptt_boundary(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("launch_game", "fixture_game_a"))
        runner = host.games._runner
        self.pad(host, "A")
        host.execute(HostIntent("return_to_face"))
        self.assertIn(("A", False), runner.inputs)
        self.assertEqual(host.gamepad.snapshot()["held"], [])
        self.assertEqual(self.pad(host, "START").code, "gamepad_mode_blocked")
        self.assertEqual(host.client.requests, [])

    def test_stale_queued_gamepad_and_stop_epoch_cannot_launch(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("open_game_menu"))
        host.queue_command("expired_pad", self.command(host, "A"))
        self.now += .501; host.poll()
        self.assertEqual(host.modes.mode, "game_menu")
        host.queue_command("cancelled_pad", self.command(host, "A"))
        host.queue_command("stop_pad", HostControl("stop")); host.poll()
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.games._runner)

    def test_long_gamepad_session_does_not_exhaust_action_nonce_ledger(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("launch_game", "fixture_game_a"))
        host.poll()
        for index in range(600):
            host.queue_command("pad_" + str(index), self.command(host, "B", index % 2 == 0))
            host.poll()
        self.assertEqual(len(host._records), 0)
        self.assertEqual(host.gamepad.snapshot()["accepted_edges"], 600)
        # Normal actions remain admitted after a long input stream.
        host.queue_command("after_stream", HostControl("stop")); host.poll()
        self.assertEqual(host.modes.mode, "conversation")
        self.assertEqual(host.client.requests, [])

    def test_gamepad_queue_is_bounded_without_overwriting_pending_edges(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("open_game_menu"))
        for index in range(64):
            host.queue_command("pad_fill_" + str(index), self.command(host, "HEARTBEAT"))
        with self.assertRaises(FeatureError) as caught:
            host.queue_command("pad_overflow", self.command(host, "A"))
        self.assertEqual(caught.exception.code, "gamepad_queue_full")
        host.poll()
        self.assertEqual(host.modes.mode, "game_menu")

    def test_gamepad_backward_clock_releases_and_stops_game(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("launch_game", "fixture_game_a"))
        runner = host.games._runner
        self.pad(host, "START")
        self.now -= 1
        host.gamepad.poll()
        self.now += 1
        host.poll()
        self.assertIn(("START", False), runner.inputs)
        self.assertFalse(runner.running)
        self.assertEqual(host.modes.mode, "game_menu")

    def test_live_music_remains_denied_without_explicit_optin(self):
        host = self.host(runtime=RuntimePolicy(simulate=False), music_directory=self.library())
        result = host.execute(HostIntent("play_song", "generated_song"))
        self.assertEqual(result.code, "capability_denied")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertEqual(host.snapshot()["asset_status"]["music_playback"], "DENIED")

    def test_music_supervision_requires_selected_library_and_live_runtime(self):
        for settings in ({"music_supervised": True},
                         {"music_supervised": True, "runtime": RuntimePolicy(simulate=False)}):
            with self.subTest(settings=settings), self.assertRaises(FeatureError) as caught:
                self.host(**settings)
            self.assertEqual(caught.exception.code, "music_supervision_requires_library")

    def test_live_music_reuses_owned_fd_backend_and_truthful_receipts(self):
        runners = []
        class BoundaryRunner:
            def __init__(self, command, **kwargs):
                self.command, self.pass_fds = command, kwargs["pass_fds"]
                self.ended, self.stopped = False, False
                runners.append(self)
            def start(self, asset):
                self.asset = asset
                self.valid_fd = os.fstat(self.pass_fds[0]).st_size > 44
            def poll(self):
                return "ended" if self.ended else None
            def stop(self):
                self.stopped = True
        with patch("bmo_ai.features.games.OwnedProcessRunner", BoundaryRunner):
            host = self.host(runtime=RuntimePolicy(simulate=False), music_directory=self.library(),
                             music_supervised=True)
            host.session.set_emotion("surprised")
            self.assertEqual(runners, [])  # Construction/menu never plays anything.
            start = host.execute(HostIntent("play_song", "generated_song"), request_id="music_live")
            self.assertEqual((start.status, start.simulated, start.executed, start.completed),
                             ("running", False, True, False))
            self.assertEqual(host.audio.owner, "music")
            runner, = runners
            self.assertEqual(runner.command[0:2], ("aplay", "-q"))
            self.assertTrue(runner.valid_fd)
            self.assertTrue(runner.command[-1].startswith("/proc/self/fd/"))
            self.assertEqual(host.control(HostControl("pause"), "cannot_pause").code, "pause_unsupported")
            runner.ended = True; host.poll()
            receipt = host._result_history["music_live"]
            self.assertEqual((receipt.status, receipt.simulated, receipt.completed), ("succeeded", False, True))
            self.assertTrue(runner.stopped)
            self.assertEqual(host.modes.mode, "conversation")
            self.assertIsNone(host.audio.owner)
            self.assertEqual(host.session.snapshot()["emotion"], "surprised")
            self.assertNotIn(str(self.root), json.dumps(host.snapshot()))
            self.assertEqual(host.client.requests, [])

    def test_missing_games_are_not_reported_installed_or_executable(self):
        host = self.host(usb_gamepad=True)
        state = host.snapshot()
        self.assertEqual(state["games"]["catalog"], [])
        self.assertEqual(state["asset_status"]["real_game_pack"], "ASSET_PENDING")
        self.assertEqual(state["asset_status"]["extensions"]["external_game_runtime"], "NOT_CONFIGURED")
        host.execute(HostIntent("open_game_menu"))
        self.pad(host, "START")
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertIsNone(host.games._runner)

    def test_motion_admission_rejection_and_exception_finish_journal_without_stopping_host(self):
        for error in (False, True):
            with self.subTest(error=error):
                host = self.host(emotion_arms=True)
                turn = host.session.session_id + ":1"
                host._turn_generations[turn] = host.modes.generation
                host._put_voice(("reply", turn, ("wave", None), self.now))
                host._put_voice(("started", turn, None, self.now))
                snapshot = dict(host.session.snapshot(), playback="started", emotion="happy")
                def reject(_gesture, *, request_id, **_):
                    if error:
                        raise RuntimeError("private servo exception")
                    return HostResult(request_id, "rejected", "gesture_cooldown")
                with patch.object(host.session, "snapshot", return_value=snapshot), \
                        patch.object(host.motion, "submit", side_effect=reject):
                    host.poll()
                receipt = host._last_result
                self.assertEqual(receipt["status"], "failed" if error else "rejected")
                self.assertEqual(receipt["code"], "motion_admission_failed" if error else "gesture_cooldown")
                self.assertFalse(receipt["executed"])
                entry = host.journal.begin(receipt["request_id"], {"gesture": "wave", "turn": turn})
                self.assertTrue(entry["duplicate"])
                self.assertEqual(entry["status"], receipt["status"])
                self.assertNotIn("private servo", json.dumps(host.snapshot()))
                host.execute(HostIntent("open_game_menu"))
                self.assertEqual(host.modes.mode, "game_menu")

    def test_failed_motion_close_keeps_cleanup_incomplete_and_retry_releases_ownership(self):
        host = self.host()
        fail = [True]
        original = host.motion.close
        def close():
            if fail[0]:
                raise FeatureError("arm_cleanup_failed")
            original()
        with patch.object(host.motion, "close", side_effect=close):
            with self.assertRaises(FeatureError):
                host.close()
            self.assertFalse(host.cleanup_snapshot()["resources_cleaned"])
            self.assertFalse(host.start_question("Must stay closed."))
            fail[0] = False
            host.close()
        self.assertTrue(host.cleanup_snapshot()["resources_cleaned"])

    def test_music_stop_mode_change_and_next_retire_original_running_receipts(self):
        library = self.library(count=2)
        for action in ("stop_media", "return_to_face", "next"):
            with self.subTest(action=action):
                runner = Mock()
                runner.poll.return_value = None
                with patch("bmo_ai.features.games.OwnedProcessRunner", return_value=runner):
                    host = self.host(runtime=RuntimePolicy(simulate=False), music_directory=library,
                                     music_supervised=True)
                    host.execute(HostIntent("play_song", "generated_song"), request_id="song_original")
                    if action == "next":
                        result = host.control(HostControl("next"), "song_next")
                        self.assertEqual(result.status, "running")
                    else:
                        result = host.execute(HostIntent(action), request_id="song_stop")
                        if action == "stop_media":
                            self.assertFalse(result.simulated)
                            self.assertTrue(result.completed)
                    host.poll()
                    original = host._result_history["song_original"]
                    self.assertEqual((original.status, original.simulated, original.completed, original.completion),
                                     ("cancelled", False, True, "stopped"))
                    previous = host.journal.begin("song_original", {"name": "play_song", "target": "generated_song"})
                    self.assertEqual(previous["status"], "cancelled")
                    if action == "next":
                        self.assertEqual(host._result_history["song_next"].status, "running")
                        host.execute(HostIntent("stop_media"), request_id="song_next_stop")
                        host.poll()
                        self.assertEqual(host._result_history["song_next"].status, "cancelled")
                    self.assertIsNone(host.audio.owner)

    def test_gamepad_reload_floor_allows_new_events_without_replaying_old_sequence(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("open_game_menu"))
        self.pad(host, "HEARTBEAT", sequence=1000000)
        host.poll()
        floor = host.snapshot()["usb_gamepad"]["sequence_floor"]
        self.assertEqual(floor, 1000000)
        self.assertEqual(self.pad(host, "A", sequence=floor).code, "repeated_gamepad_input")
        self.assertEqual(self.pad(host, "A", sequence=floor + 1).status, "simulated")
        self.assertEqual(host.modes.mode, "game")

    def test_gamepad_covering_menu_suspends_and_releases_without_stopping_game(self):
        host = self.host(demo_assets=True, usb_gamepad=True)
        host.execute(HostIntent("launch_game", "fixture_game_a"))
        runner = host.games._runner
        self.pad(host, "B")
        self.pad(host, "RELEASE")
        self.now += 2.; host.poll()
        self.assertIn(("B", False), runner.inputs)
        self.assertTrue(runner.running)
        self.assertEqual(host.modes.mode, "game")
        self.assertEqual(host.gamepad.snapshot()["held"], [])


if __name__ == "__main__":
    unittest.main()
