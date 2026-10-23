"""Owner-driven module/voice integration with no live backend or credentials."""
import json
from pathlib import Path
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.journal import ActionJournal
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


def reply(**changes):
    return dict(version=1, speech="A private synthetic spoken fixture.", emotion="crying",
                gesture="none", intent=None) | changes


class FeatureHostTests(unittest.TestCase):
    def setUp(self):
        self.hosts, self.releases = [], []
        self.now = 0.0
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Live operation forbidden")))

    def tearDown(self):
        for release in self.releases:
            release.set()
        for host in self.hosts:
            host.close()

    def host(self, replies=(), **settings):
        client = FeatureSimulatedClient(replies)

        def player(command, *, on_start, cancel_check, **kwargs):
            cancel_check(); on_start(); cancel_check()

        voice = IntegrationVoice(integration_config(), client, simulate=True,
                                 emit=lambda *a, **kw: None, player=player)
        host = FeatureHost(demo_assets=True, clock=lambda: self.now, client=client,
                           voice=voice, **settings)
        self.hosts.append(host)
        return host

    def finish_voice(self, host):
        deadline = time.monotonic() + 3
        while host.session.busy and time.monotonic() < deadline:
            host.poll()
            time.sleep(.002)
        self.assertFalse(host.session.busy)
        host.poll()

    def seed(self, host):
        self.assertTrue(host.start_question("Synthetic first question."))
        self.finish_voice(host)
        self.assertEqual(host.snapshot()["emotion"], "crying")

    def gate(self, target, name):
        entered, release = Event(), Event()
        self.releases.append(release)
        original = getattr(target, name)

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Synthetic worker gate timed out")
            return original(*args, **kwargs)

        setattr(target, name, delayed)
        return entered, release, original

    def execute(self, host, name, target=None, request="host:1"):
        result = host.execute(HostIntent(name, target), request_id=request,
                              expected_generation=host.modes.generation)
        host.poll()
        return result

    def test_crying_conversation_survives_game_music_and_photo_returns_with_same_brain(self):
        host = self.host([reply()])
        self.seed(host)
        brain, history = host.session.brain, list(host.session.brain.history)
        self.execute(host, "open_game_menu", request="menu:1")
        result = self.execute(host, "launch_game", "fixture_game_a", "game:1")
        self.assertTrue(result.simulated and result.completed)
        self.assertFalse(result.executed)
        self.assertEqual(host.snapshot()["audio_owner"], "game")
        self.execute(host, "exit_game", request="exit:1")
        self.assertEqual(host.snapshot()["mode"], "game_menu")
        self.execute(host, "return_to_face", request="face:1")
        self.execute(host, "play_song", "fixture_song_a", "song:1")
        self.assertEqual(host.snapshot()["audio_owner"], "music")
        self.now = .6
        host.poll()
        self.assertEqual((host.snapshot()["mode"], host.snapshot()["audio_owner"]), ("conversation", None))
        self.execute(host, "take_photo", request="photo:1")
        self.now = 3.7
        host.poll()
        self.assertTrue(host.snapshot()["photo"]["simulated"])
        self.assertFalse(host.snapshot()["photo"]["executed"])
        self.execute(host, "return_to_face", request="face:2")
        self.assertIs(host.session.brain, brain)
        self.assertEqual(host.session.brain.history, history)
        self.assertEqual(host.snapshot()["emotion"], "crying")
        self.assertEqual(len(host.client.requests), 1)
        self.assertNotIn("private synthetic", json.dumps(host.snapshot()))

    def test_queue_admission_is_thread_safe_deduplicated_and_executes_only_on_poll_owner(self):
        host = self.host()
        acknowledgements = []
        threads = [Thread(target=lambda: acknowledgements.append(
            host.queue_command("logical-menu", HostIntent("open_game_menu")))) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(1)
        self.assertEqual(len(acknowledgements), 4)
        self.assertEqual(len({ack["request_id"] for ack in acknowledgements}), 1)
        self.assertTrue(all(ack["status"] == "accepted" for ack in acknowledgements))
        self.assertEqual(host.modes.mode, "conversation")
        self.assertEqual(host._commands.qsize(), 1)
        host.poll()
        self.assertEqual(host.snapshot()["mode"], "game_menu")
        repeated = host.queue_command("logical-menu", HostIntent("open_game_menu"))
        self.assertTrue(repeated["completed"])
        with self.assertRaises(FeatureError) as error:
            host.queue_command("logical-menu", HostIntent("return_to_face"))
        self.assertEqual(error.exception.code, "request_conflict")

    def test_old_generation_and_unknown_ids_do_not_take_resources_or_change_history(self):
        host = self.host([reply()]); self.seed(host)
        generation = host.modes.generation
        self.execute(host, "open_game_menu", request="menu:1")
        stale = host.execute(HostIntent("launch_game", "fixture_game_a"), request_id="old:1",
                             expected_generation=generation)
        self.assertEqual((stale.status, stale.code), ("rejected", "stale_mode"))
        self.assertFalse(stale.executed or stale.completed)
        result = self.execute(host, "launch_game", "missing_game", "missing:1")
        self.assertEqual(result.status, "rejected")
        self.assertIsNone(host.snapshot()["audio_owner"])
        self.assertEqual(host.snapshot()["emotion"], "crying")
        self.assertEqual(len(host.client.requests), 1)

    def test_game_b_remains_game_control_and_escape_or_pad_loss_returns_to_list(self):
        host = self.host()
        self.execute(host, "launch_game", "fixture_game_a", "game:1")
        host.control(HostControl("input", {"key": "B", "pressed": True}), "input:b")
        host.poll()
        self.assertEqual(host.snapshot()["mode"], "game")
        self.assertIn(("B", True), host.games._runner.inputs)
        host.control(HostControl("input", {"key": "ESC", "pressed": True}), "input:esc")
        host.poll()
        self.assertEqual((host.snapshot()["mode"], host.snapshot()["audio_owner"]), ("game_menu", None))
        self.execute(host, "launch_game", "fixture_game_b", "game:2")
        host.control(HostControl("input", {"key": "PAD_LOST", "pressed": True}), "input:lost")
        host.poll()
        self.assertEqual((host.snapshot()["mode"], host.snapshot()["audio_owner"]), ("game_menu", None))

    def test_media_next_end_releases_audio_and_returns_to_conversation(self):
        host = self.host()
        self.execute(host, "play_song", "fixture_song_a", "song:1")
        host.control(HostControl("next"), "song:2"); host.poll()
        self.assertEqual(host.snapshot()["media"]["active_id"], "fixture_song_b")
        host.control(HostControl("next"), "song:3"); host.poll()
        self.assertEqual((host.snapshot()["mode"], host.snapshot()["audio_owner"]), ("conversation", None))

    def test_stop_failure_is_not_completed_and_prevents_overlapping_conversation_audio(self):
        host = self.host()
        self.execute(host, "launch_game", "fixture_game_a", "game:1")
        runner = host.games._runner
        original = runner.stop
        runner.stop = Mock(side_effect=RuntimeError("private cleanup details"))
        try:
            result = host.control(HostControl("stop"), "stop:1")
            self.assertFalse(result.completed)
            self.assertNotEqual(result.code, "ok")
            self.assertEqual(host.audio.owner, "game")
            routed = host.control(HostControl("input", {"key": "STOP", "pressed": True}), "stop:input")
            self.assertFalse(routed.completed)
            self.assertNotEqual(routed.code, "ok")
            with self.assertRaises(FeatureError):
                host.start_question("Should not overlap.")
            self.assertNotIn("private", repr(result))
        finally:
            runner.stop = original

    def test_game_stops_before_conversation_player_can_acquire_audio(self):
        host = self.host([reply()])
        self.execute(host, "launch_game", "fixture_game_a", "game:1")
        trace = []
        runner = host.games._runner
        original_stop, original_player = runner.stop, host.session.voice._player
        def stop():
            trace.append("game_stopped"); return original_stop()
        def player(command, **settings):
            trace.append("voice_started")
            self.assertEqual(host.audio.owner, "conversation")
            return original_player(command, **settings)
        runner.stop = stop
        host.session.voice._player = player
        self.assertTrue(host.start_question())
        self.finish_voice(host)
        self.assertEqual(trace[:2], ["game_stopped", "voice_started"])
        self.assertIsNone(host.audio.owner)

    def test_cancel_during_llm_or_tts_discards_late_feature_intent(self):
        for boundary in ("create", "_synthesize"):
            with self.subTest(boundary=boundary):
                host = self.host([reply(intent={"name": "open_game_menu", "target": None})])
                target = host.client if boundary == "create" else host.session.voice
                entered, release, _ = self.gate(target, boundary)
                dispatch = self.enterContext(patch.object(host, "execute", wraps=host.execute))
                self.assertTrue(host.start_question())
                self.assertTrue(entered.wait(1))
                host.poll()
                host.cancel_conversation()
                release.set(); self.finish_voice(host)
                self.assertEqual(host.snapshot()["mode"], "conversation")
                self.assertIsNone(host.snapshot()["audio_owner"])
                dispatch.assert_not_called()

    def test_intent_dispatches_once_after_ended_and_repeat_does_not_replay_it(self):
        host = self.host([reply(intent={"name": "open_game_menu", "target": None})])
        entered, release = Event(), Event()
        self.releases.append(release)
        original = host.session.voice._player
        def playing(command, **settings):
            original(command, **settings)
            entered.set()
            if not release.wait(3): raise AssertionError("Synthetic player timed out")
            settings["cancel_check"]()
        host.session.voice._player = playing
        dispatch = self.enterContext(patch.object(host, "execute", wraps=host.execute))
        self.assertTrue(host.start_question()); self.assertTrue(entered.wait(1))
        host.poll()
        self.assertEqual(host.snapshot()["phase"], "speaking")
        self.assertEqual(host.snapshot()["mode"], "conversation")
        dispatch.assert_not_called()
        release.set(); self.finish_voice(host)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(host.snapshot()["mode"], "game_menu")
        for _ in range(3): host.poll()
        self.assertEqual(dispatch.call_count, 1)
        host.session.voice._player = original
        result = host.control(HostControl("repeat"), "repeat:1")
        self.assertEqual(result.code, "repeat_started")
        self.finish_voice(host)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(len(host.client.requests), 1)
        self.assertEqual(host.snapshot()["mode"], "conversation")

    def test_named_manual_override_uses_plain_fixture_speech_not_feature_json(self):
        host = self.host([reply(speech="Clean manual reply.", emotion="happy")])
        host.session.set_emotion("sad")
        voice = self.enterContext(patch.object(host.session.voice, "speak", wraps=host.session.voice.speak))
        self.assertTrue(host.start_question()); self.finish_voice(host)
        self.assertEqual(host.session.brain.history[-1]["content"], "Clean manual reply.")
        self.assertEqual(voice.call_args.args[0], "Clean manual reply.")
        self.assertNotIn("text", host.client.requests[0])
        self.assertEqual(host.snapshot()["emotion"], "sad")

    def test_gesture_proposal_is_submitted_once_at_started_and_never_by_repeat(self):
        host = self.host([reply(gesture="wave")])
        entered, release = Event(), Event()
        self.releases.append(release)
        original = host.session.voice._player
        def playing(command, **settings):
            original(command, **settings)
            entered.set()
            if not release.wait(3): raise AssertionError("Synthetic player timed out")
            settings["cancel_check"]()
        host.session.voice._player = playing
        gesture = self.enterContext(patch.object(host.motion, "submit", wraps=host.motion.submit))
        self.assertTrue(host.start_question()); self.assertTrue(entered.wait(1))
        host.poll()
        self.assertEqual(gesture.call_count, 1)
        self.assertEqual(gesture.call_args.args[0], "wave")
        self.assertEqual(gesture.call_args.kwargs["mode_generation"], host.modes.generation)
        self.assertEqual(gesture.call_args.kwargs["session_id"], host.session.session_id)
        host.poll()
        self.assertEqual(gesture.call_count, 1)
        release.set(); self.finish_voice(host)
        host.session.voice._player = original
        host.control(HostControl("repeat"), "repeat:gesture")
        self.finish_voice(host)
        self.assertEqual(gesture.call_count, 1)

    def test_reset_keeps_feature_journal_dedup_while_clearing_conversation_only(self):
        host = self.host([reply()]); self.seed(host)
        launch = self.enterContext(patch.object(host.games, "start", wraps=host.games.start))
        self.execute(host, "launch_game", "fixture_game_a", "game:stable")
        host.control(HostControl("reset"), "reset:1"); host.poll()
        duplicate = self.execute(host, "launch_game", "fixture_game_a", "game:stable")
        self.assertFalse(duplicate.executed)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(host.snapshot()["mode"], "conversation")
        self.assertEqual(host.snapshot()["emotion"], "neutral")
        self.assertEqual(host.session.brain.history, [])

    def test_restart_journal_unknown_is_not_replayed_and_private_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "journal"
            first = ActionJournal(store)
            first.begin("uncertain:1", HostIntent("take_photo").public())
            first.finish("uncertain:1", "running", "photo_countdown")
            first.close()
            journal = ActionJournal(store)
            host = self.host(journal=journal)
            result = host.execute(HostIntent("take_photo"), request_id="uncertain:1")
            self.assertEqual((result.status, result.code), ("unknown", "restart_unknown"))
            self.assertFalse(result.completed or result.executed)
            self.assertIsNone(host._pending_photo)
            alias = Path(directory) / "alias"
            alias.symlink_to(store, target_is_directory=True)
            with self.assertRaises(FeatureError): ActionJournal(alias)

    def test_journal_admission_failure_is_fixed_and_never_starts_a_feature(self):
        host = self.host()
        with patch.object(host.journal, "begin", side_effect=RuntimeError("private database details")), \
             patch.object(host.games, "start", wraps=host.games.start) as launch:
            result = host.execute(HostIntent("launch_game", "fixture_game_a"), request_id="journal:begin")
        self.assertEqual((result.status, result.code), ("rejected", "journal_unavailable"))
        self.assertFalse(result.completed or result.executed)
        launch.assert_not_called()
        self.assertIsNone(host.audio.owner)
        self.assertNotIn("private", repr(result))

    def test_journal_completion_failure_is_unknown_and_same_request_does_not_reexecute(self):
        host = self.host()
        launch = self.enterContext(patch.object(host.games, "start", wraps=host.games.start))
        with patch.object(host.journal, "finish", side_effect=RuntimeError("private database details")):
            result = host.execute(HostIntent("launch_game", "fixture_game_a"), request_id="journal:finish")
        self.assertEqual((result.status, result.code), ("unknown", "journal_unavailable"))
        self.assertFalse(result.completed or result.executed)
        self.assertEqual(result.completion, "unknown")
        duplicate = host.execute(HostIntent("launch_game", "fixture_game_a"), request_id="journal:finish")
        self.assertEqual(duplicate.status, "unknown")
        self.assertEqual(launch.call_count, 1)
        self.assertNotIn("private", repr(result))

    def test_direct_request_conflict_returns_rejection_without_breaking_owner_poll(self):
        host = self.host()
        first = host.execute(HostIntent("open_game_menu"), request_id="stable:1")
        result = host.execute(HostIntent("take_photo"), request_id="stable:1")
        self.assertEqual((result.status, result.code), ("rejected", "request_conflict"))
        self.assertFalse(result.completed or result.executed)
        host.poll()
        self.assertEqual(host.snapshot()["mode"], "game_menu")
        self.assertIsNone(host._pending_photo)
        duplicate = host.execute(HostIntent("open_game_menu"), request_id="stable:1")
        self.assertEqual(duplicate.public(), first.public())

    def test_shell_heartbeat_survives_hidden_face_and_actual_loss_stops_game(self):
        host = self.host([reply()]); self.seed(host)
        self.execute(host, "launch_game", "fixture_game_a", "game:1")
        host.heartbeat()
        self.now = 100
        host.heartbeat(); host.poll()
        self.assertEqual(host.snapshot()["mode"], "game")
        self.now = 104
        host.poll()
        self.assertEqual((host.snapshot()["mode"], host.snapshot()["audio_owner"]), ("conversation", None))
        self.assertEqual(host.snapshot()["emotion"], "crying")


if __name__ == "__main__":
    unittest.main()
