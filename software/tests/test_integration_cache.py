"""One private session WAV; repeat uses no mic, brain, synthesis or new gain."""
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio, integration_voice
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession, SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


class SessionCacheTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(audio, "record_wav", side_effect=AssertionError("microphone forbidden")))
        self.enterContext(patch.object(audio, "transcribe", side_effect=AssertionError("STT forbidden")))
        self.enterContext(patch.object(integration_voice.local_piper, "synthesize_wav",
                                     side_effect=AssertionError("Piper forbidden")))
        self.raw, self.events, self.commands = [], [], []
        self.fish = Mock(side_effect=self.synthesize)
        self.voice = IntegrationVoice(integration_config(), Mock(),
                                      emit=lambda kind, **fields: self.events.append((kind, fields)),
                                      fish_synthesizer=self.fish, player=self.player)
        self.addCleanup(self.voice.close)

    def synthesize(self, *args, **kwargs):
        path = synthetic_wav()
        self.raw.append(path)
        return path

    def player(self, command, *, on_start, cancel_check, **settings):
        # An injected player only observes the command; it never runs ALSA.
        self.commands.append(command)
        cancel_check()
        on_start()
        cancel_check()

    def prepared(self):
        return [fields for kind, fields in self.events if kind == "prepared"]

    def test_repeat_reuses_exact_private_gained_wav_and_cues_without_providers(self):
        self.assertFalse(self.voice.repeat())
        self.assertEqual(self.events, [])
        self.assertEqual(self.commands, [])
        self.fish.assert_not_called()
        with patch.object(integration_voice, "attenuated_copy", wraps=integration_voice.attenuated_copy) as gain, \
             patch.object(integration_voice, "wav_cues", wraps=integration_voice.wav_cues) as cues:
            self.voice.speak("Last successful answer.")
        gain.assert_called_once()
        cues.assert_called_once()
        first = self.prepared()[0]
        cached = Path(first["path"])
        original = cached.read_bytes()
        self.assertEqual(cached.stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(not path.exists() for path in self.raw))
        self.assertTrue(self.voice.has_cache)
        with patch.object(self.voice, "_synthesize", side_effect=AssertionError("repeat synthesis forbidden")), \
             patch.object(integration_voice, "attenuated_copy", side_effect=AssertionError("repeat gain forbidden")), \
             patch.object(integration_voice, "wav_cues", side_effect=AssertionError("repeat cue rebuild forbidden")):
            self.assertTrue(self.voice.repeat())
            self.assertTrue(self.voice.repeat())
        self.fish.assert_called_once()
        self.assertTrue(all(fields == first for fields in self.prepared()))
        self.assertTrue(all(command[-1] == str(cached) for command in self.commands))
        self.assertEqual(cached.read_bytes(), original)
        self.voice.client.responses.create.assert_not_called()
        self.voice.close()
        self.assertFalse(cached.exists())
        self.assertFalse(self.voice.has_cache)
        self.assertFalse(self.voice.repeat())

    def test_only_new_success_replaces_cache_while_failures_keep_previous(self):
        self.voice.speak("First success.")
        first = Path(self.prepared()[-1]["path"])
        original = first.read_bytes()
        self.voice._fish_synthesizer = Mock(side_effect=audio.AudioSynthesisError("Synthetic failure."))
        with self.assertRaises(audio.AudioSynthesisError):
            self.voice.speak("Synthesis failure.")
        self.assertEqual(first.read_bytes(), original)
        self.voice._fish_synthesizer = self.fish
        self.voice._player = Mock(side_effect=audio.AudioPlaybackError("Synthetic failure."))
        with self.assertRaises(audio.AudioPlaybackError):
            self.voice.speak("Playback failure.")
        failed = Path(self.prepared()[-1]["path"])
        self.assertFalse(failed.exists())
        self.assertEqual(first.read_bytes(), original)
        with self.assertRaises(audio.AudioPlaybackError):
            self.voice.repeat()
        self.assertEqual(first.read_bytes(), original)
        self.voice._player = self.player
        self.assertTrue(self.voice.repeat())
        self.assertEqual(self.prepared()[-1]["path"], str(first))
        self.voice.speak("Replacement success.")
        replacement = Path(self.prepared()[-1]["path"])
        self.assertNotEqual(replacement, first)
        self.assertFalse(first.exists())
        self.assertTrue(replacement.exists())
        self.assertTrue(all(not path.exists() for path in self.raw))

    def test_cancelled_new_synthesis_preserves_previous_success(self):
        self.voice.speak("First success.")
        previous = Path(self.prepared()[-1]["path"])
        cancelled = Event()

        def check():
            if cancelled.is_set():
                raise TurnCancelled()

        def late_result(*args, **kwargs):
            result = self.synthesize()
            cancelled.set()
            return result

        self.voice.cancel_check = check
        self.voice._fish_synthesizer = late_result
        with self.assertRaises(TurnCancelled):
            self.voice.speak("Cancelled replacement.")
        self.assertTrue(previous.exists())
        self.assertTrue(all(not path.exists() for path in self.raw))
        cancelled.clear()
        self.assertTrue(self.voice.repeat())
        self.assertEqual(self.prepared()[-1]["path"], str(previous))

    def test_repeat_cancellation_reaps_its_only_child_and_retains_cache(self):
        self.voice.speak("First success.")
        cached = Path(self.prepared()[-1]["path"])
        self.voice.simulate = True
        self.voice._player = audio._run
        started, cancelled = Event(), Event()
        processes, errors = [], []
        real_popen = subprocess.Popen

        def check():
            if cancelled.is_set():
                raise TurnCancelled()

        def spawn(command, **settings):
            self.assertEqual(command[0], sys.executable)
            self.assertFalse(any(key.startswith(("OPENAI_", "FISH_")) for key in settings["env"]))
            process = real_popen(command, **settings)
            processes.append(process)
            return process

        def run():
            try:
                self.voice.repeat(on_playback_start=started.set)
            except BaseException as error:
                errors.append(error)

        self.voice.cancel_check = check
        with patch.object(audio.subprocess, "Popen", side_effect=spawn):
            worker = Thread(target=run)
            worker.start()
            try:
                self.assertTrue(started.wait(1))
                self.assertFalse(self.voice.repeat())
                with self.assertRaises(audio.AudioPlaybackError):
                    self.voice.speak("Overlapping speech forbidden.")
                cancelled.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors[0], TurnCancelled)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].returncode)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(processes[0].pid, os.WNOHANG)
            finally:
                cancelled.set()
                worker.join(2)
        self.assertTrue(cached.exists())
        self.assertTrue(self.voice.has_cache)
        self.fish.assert_called_once()
        cancelled.clear()
        self.voice._player = self.player
        self.assertTrue(self.voice.repeat())

    def test_clear_during_successful_playback_prevents_late_cache_promotion(self):
        def clear_on_start(command, *, on_start, cancel_check, **settings):
            on_start()
            self.voice.clear_cache()
        self.voice._player = clear_on_start
        self.voice.speak("Cleared before success returned.")
        self.assertFalse(self.voice.has_cache)
        self.assertTrue(all(not Path(fields["path"]).exists() for fields in self.prepared()))
        self.assertTrue(all(not path.exists() for path in self.raw))

    def test_close_deletes_previous_cache_and_discards_blocked_replacement(self):
        self.voice.speak("First success.")
        cached = Path(self.prepared()[-1]["path"])
        entered, release = Event(), Event()
        errors = []

        def synthesize(*args, **settings):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test synthesis gate timed out")
            return self.synthesize()

        def run():
            try:
                self.voice.speak("Late replacement.")
            except BaseException as error:
                errors.append(error)

        self.voice._fish_synthesizer = synthesize
        worker = Thread(target=run)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.voice.close())
            self.assertFalse(cached.exists())
            self.assertFalse(self.voice.has_cache)
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0], TurnCancelled)
            self.assertTrue(all(not path.exists() for path in self.raw))
            self.assertEqual(len(self.commands), 1)
        finally:
            release.set()
            worker.join(2)

    def make_session(self):
        session = IntegrationSession(integration_config(), SimulatedClient(), simulate=True,
                                     voice=self.voice)
        self.voice.listen = lambda: "Synthetic question."
        self.addCleanup(session.close)
        return session

    def finish(self, session):
        deadline = time.monotonic() + 3
        while session.busy and time.monotonic() < deadline:
            session.poll()
            time.sleep(.002)
        self.assertFalse(session.busy)

    def test_session_repeat_new_ids_same_timeline_and_unchanged_history(self):
        session = self.make_session()
        states = []
        def player(command, *, on_start, cancel_check, **settings):
            self.player(command, on_start=on_start, cancel_check=cancel_check, **settings)
            states.append(session.snapshot())
        self.voice._player = player
        self.assertFalse(session.repeat())
        self.assertEqual(session.worker.turn, 0)
        self.assertTrue(session.start("First."))
        self.finish(session)
        history = [dict(item) for item in session.brain.history]
        requests = len(session.client.requests)
        with patch.object(self.voice, "listen", side_effect=AssertionError("repeat listening forbidden")), \
             patch.object(session.brain, "ask", side_effect=AssertionError("repeat GPT forbidden")), \
             patch.object(self.voice, "_synthesize", side_effect=AssertionError("repeat TTS forbidden")), \
             patch.object(integration_voice, "attenuated_copy", side_effect=AssertionError("repeat gain forbidden")), \
             patch.object(integration_voice, "wav_cues", side_effect=AssertionError("repeat cues forbidden")):
            self.assertTrue(session.repeat())
            self.assertFalse(session.repeat())
            self.assertFalse(session.start("Cannot overlap repeat."))
            self.finish(session)
        self.assertEqual(session.brain.history, history)
        self.assertEqual(len(session.client.requests), requests)
        self.assertEqual(states[0]["session_id"], states[1]["session_id"])
        self.assertNotEqual(states[0]["turn_id"], states[1]["turn_id"])
        self.assertNotEqual(states[0]["utterance_id"], states[1]["utterance_id"])
        self.assertEqual(states[0]["cues"], states[1]["cues"])
        repeat_events = [item for item in session.events if item["turn_id"] == states[1]["turn_id"]]
        self.assertFalse(any(item["phase"] == "listening" for item in repeat_events))
        self.assertEqual(session.snapshot()["playback"], "ended")
        session.reset()
        self.assertFalse(self.voice.has_cache)
        self.assertFalse(session.repeat())
        self.assertTrue(all(not Path(command[-1]).exists() for command in self.commands))

    def test_reset_during_repeat_cancels_then_deletes_cache_and_allows_new_turn(self):
        session = self.make_session()
        self.assertTrue(session.start())
        self.finish(session)
        cached = Path(self.commands[-1][-1])
        started = Event()
        def wait_for_cancel(command, *, on_start, cancel_check, **settings):
            on_start()
            started.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                cancel_check()
                time.sleep(.002)
            raise AssertionError("repeat cancellation was not observed")
        self.voice._player = wait_for_cancel
        self.assertTrue(session.repeat())
        self.assertTrue(started.wait(1))
        session.reset()
        self.assertFalse(session.repeat())
        self.finish(session)
        self.assertFalse(cached.exists())
        self.assertFalse(self.voice.has_cache)
        self.assertEqual(session.brain.history, [])
        self.voice._player = self.player
        self.assertTrue(session.start())
        self.finish(session)
        self.assertTrue(self.voice.has_cache)
        session.close()
        self.assertFalse(self.voice.has_cache)
        self.assertFalse(session.repeat())


if __name__ == "__main__":
    unittest.main()
