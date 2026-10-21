"""Owned harmless child cancellation; never launches ALSA or any device."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from bmo_ai import audio
from bmo_ai.config import BMOConfig
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_voice import IntegrationVoice


class OwnedPlayerCancellationTests(unittest.TestCase):
    def test_running_child_is_reaped_and_next_child_can_run(self):
        started, cancelled = Event(), Event()
        processes, errors = [], []
        real_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def check():
            if cancelled.is_set():
                raise TurnCancelled()

        def run():
            try:
                audio._run([sys.executable, "-c", "import time; time.sleep(20)"],
                           timeout=30, error_type=audio.AudioPlaybackError,
                           on_start=started.set, cancel_check=check,
                           env={"PATH": os.defpath})
            except BaseException as error:
                errors.append(error)

        with patch.object(audio.subprocess, "Popen", side_effect=spawn):
            worker = Thread(target=run)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                cancelled.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors[0], TurnCancelled)
                self.assertIsNotNone(processes[0].poll())
                self.assertNotEqual(processes[0].returncode, 0)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(processes[0].pid, os.WNOHANG)
                cancelled.clear()
                audio._run([sys.executable, "-c", "pass"], timeout=2,
                           error_type=audio.AudioPlaybackError, cancel_check=check,
                           env={"PATH": os.defpath})
                self.assertEqual(processes[1].returncode, 0)
            finally:
                cancelled.set()
                worker.join(2)

    def test_timeout_kills_child_that_ignores_terminate(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "ready"
            code = ("import pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                    "pathlib.Path(" + repr(str(marker)) + ").write_text('ready'); time.sleep(20)")
            processes = []
            real_popen = subprocess.Popen
            def spawn(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                return process
            def ready():
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertTrue(marker.exists())
            with patch.object(audio.subprocess, "Popen", side_effect=spawn):
                with self.assertRaises(audio.AudioPlaybackError) as caught:
                    audio._run([sys.executable, "-c", code], timeout=.03,
                               error_type=audio.AudioPlaybackError, on_start=ready,
                               cancel_check=lambda: None, env={"PATH": os.defpath})
            self.assertEqual(caught.exception.code, "audio_timeout")
            self.assertEqual(processes[0].returncode, -9)
            with self.assertRaises(ChildProcessError):
                os.waitpid(processes[0].pid, os.WNOHANG)

    def test_voice_close_during_synthetic_playback_cleans_and_reaps(self):
        started = Event()
        events, paths, errors, processes = [], [], [], []
        real_popen = subprocess.Popen
        def emit(kind, **data):
            events.append(kind)
            if "path" in data:
                paths.append(Path(data["path"]))
        def spawn(command, **kwargs):
            self.assertEqual(command[0], sys.executable)
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertNotIn("FISH_API_KEY", kwargs["env"])
            process = real_popen(command, **kwargs)
            processes.append(process)
            return process
        voice = IntegrationVoice(BMOConfig(tts_provider="fish", fish_gain=.25), None,
                                 emit=emit, simulate=True)
        def run():
            try:
                voice.speak("Synthetic fixture.", on_playback_start=started.set)
            except BaseException as error:
                errors.append(error)
        with patch.object(audio.subprocess, "Popen", side_effect=spawn), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-test-value", "FISH_API_KEY": "synthetic-test-value"}):
            worker = Thread(target=run)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertTrue(voice.close())
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors[0], TurnCancelled)
                self.assertEqual(events[-1], "cancelled")
                self.assertIsNotNone(processes[0].returncode)
                self.assertTrue(all(not path.exists() for path in paths))
            finally:
                voice.close()
                worker.join(2)


if __name__ == "__main__":
    unittest.main()
