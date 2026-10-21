"""Capture cancellation with substituted Python children, never real arecord."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession, SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice


class OwnedCaptureCancellationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.created, self.processes, self.markers, self.threads = [], [], [], []
        self.cancelled = Event()
        self.child_env = {"PATH": os.defpath, "LC_ALL": "C.UTF-8"}
        self.mode = "sleep"
        real_mkstemp = tempfile.mkstemp
        self.real_popen = subprocess.Popen

        def mkstemp(**options):
            descriptor, name = real_mkstemp(dir=self.directory, **options)
            self.created.append(Path(name))
            return descriptor, name

        self.enterContext(patch.object(audio.tempfile, "mkstemp", side_effect=mkstemp))
        self.enterContext(patch.object(audio.subprocess, "Popen", side_effect=self.spawn))
        self.enterContext(patch.object(audio.subprocess, "run", side_effect=AssertionError("Blocking/real capture forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
        self.transcribe = self.enterContext(patch.object(audio, "transcribe", side_effect=AssertionError("STT forbidden")))
        self.enterContext(patch("bmo_ai.integration_voice._player_environment", return_value=self.child_env))
        self.addCleanup(self.cleanup_owned)

    def spawn(self, command, **options):
        # Only the production capture argv reaches this boundary. Never pass it
        # to the OS: substitute a child that writes synthetic bytes and sleeps.
        self.assertEqual(command[:2], ["arecord", "-q"])
        self.assertEqual(options["env"], self.child_env)
        self.assertEqual(options["stdout"], subprocess.DEVNULL)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertIn(Path(command[-1]), self.created)
        marker = self.directory / f"ready-{len(self.markers)}"
        self.markers.append(marker)
        if self.mode == "valid":
            code = ("import sys,wave; "
                    "w=wave.open(sys.argv[1],'wb'); w.setparams((1,2,16000,0,'NONE','NONE')); "
                    "w.writeframes(bytes(32000)); w.close()")
        else:
            code = "import pathlib,signal,sys,time; "
            if self.mode == "ignore-term":
                code += "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            code += ("pathlib.Path(sys.argv[1]).write_bytes(b'synthetic partial capture'); "
                     "pathlib.Path(sys.argv[2]).write_text('ready'); time.sleep(20)")
        process = self.real_popen([sys.executable, "-B", "-c", code, command[-1], str(marker)], **options)
        self.processes.append(process)
        return process

    def cleanup_owned(self):
        self.cancelled.set()
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
        for thread in self.threads:
            thread.join(timeout=2)

    def check(self):
        if self.cancelled.is_set():
            raise TurnCancelled()

    def wait_ready(self):
        deadline = time.monotonic() + 2
        while (not self.markers or not self.markers[-1].exists()) and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.markers and self.markers[-1].exists(), "Synthetic capture child did not become ready")

    def assert_reaped(self, process):
        self.assertIsNotNone(process.poll())
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    def test_pre_cancel_starts_no_child_and_discards_temporary_capture(self):
        self.cancelled.set()
        with self.assertRaises(TurnCancelled):
            audio.record_wav(cancel_check=self.check, env=self.child_env)
        self.assertEqual(self.processes, [])
        self.assertTrue(self.created)
        self.assertTrue(all(not path.exists() for path in self.created))

    def test_running_capture_cancel_reaps_child_and_next_capture_succeeds(self):
        errors = []

        def capture():
            try:
                audio.record_wav(seconds=20, cancel_check=self.check, env=self.child_env)
            except BaseException as error:
                errors.append(error)

        thread = Thread(target=capture, daemon=True)
        self.threads.append(thread)
        thread.start()
        self.wait_ready()
        self.cancelled.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], TurnCancelled)
        self.assert_reaped(self.processes[0])
        self.assertTrue(all(not path.exists() for path in self.created))

        self.cancelled.clear()
        self.mode = "valid"
        path = audio.record_wav(seconds=1, cancel_check=self.check, env=self.child_env)
        self.assertEqual(audio._wav_info(path)["duration_seconds"], 1)
        self.assert_reaped(self.processes[1])
        path.unlink()

    def test_session_quit_during_capture_reaps_before_return_and_never_transcribes(self):
        config = integration_config()
        client = SimulatedClient()
        client.close = Mock()
        voice = IntegrationVoice(config, client, simulate=False, emit=lambda *a, **kw: None)
        session = IntegrationSession(config, client, simulate=True, voice=voice)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        self.wait_ready()
        thread = session.worker._thread
        self.threads.append(thread)
        session.close()  # The manual launcher's /quit finally boundary.
        self.assertFalse(thread.is_alive())
        self.assertFalse(session.start("closed session"))
        self.assert_reaped(self.processes[0])
        self.assertTrue(all(not path.exists() for path in self.created))
        self.transcribe.assert_not_called()
        client.close.assert_called_once()

    def test_capture_timeout_escalates_owned_child_and_discards_partial_wav(self):
        self.mode = "ignore-term"
        real_run = audio._run

        def short_timeout(command, **options):
            self.assertEqual(options["timeout"], 11)
            # Keep the real process owner/cancellation logic; shorten only the
            # fixture's timeout after its SIGTERM handler is known to be ready.
            options["timeout"] = .03
            return real_run(command, on_start=self.wait_ready, **options)

        with patch.object(audio, "_run", side_effect=short_timeout):
            with self.assertRaises(audio.AudioRecordingError) as caught:
                audio.record_wav(seconds=1, cancel_check=self.check, env=self.child_env)
        self.assertEqual(caught.exception.code, "audio_timeout")
        self.assertEqual(self.processes[0].returncode, -9)
        self.assert_reaped(self.processes[0])
        self.assertTrue(all(not path.exists() for path in self.created))


if __name__ == "__main__":
    unittest.main()
