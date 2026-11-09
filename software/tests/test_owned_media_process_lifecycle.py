"""Owned harmless Python process groups; never an emulator or sound process."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.games import OwnedProcessRunner


class OwnedMediaProcessLifecycleTests(unittest.TestCase):
    def runner(self, code="import time;time.sleep(30)", **kwargs):
        runner = OwnedProcessRunner((sys.executable, "-B", "-c", code), verified=True,
                                    ready_probe=lambda process: True, **kwargs)
        self.addCleanup(runner.stop)
        return runner

    def test_poll_observes_exit_without_releasing_pid_and_stop_signals_before_reap(self):
        runner = self.runner()
        runner.start(None)
        process = runner.process
        os.kill(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 2
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.005)
        with patch.object(process, "poll", side_effect=AssertionError("Do not reap via poll")):
            self.assertEqual(runner.poll(), "failed")
            self.assertIsNone(process.returncode)
            with patch("bmo_ai.features.games.os.killpg", wraps=os.killpg) as signaler:
                runner.stop()
                count = signaler.call_count
                runner.stop()
                self.assertEqual(signaler.call_count, count)
        self.assertIsNotNone(process.returncode)
        with self.assertRaises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)

    def test_missing_nonreaping_wait_support_never_falls_back_or_sends_signal(self):
        runner = self.runner()
        process = Mock(pid=12345)
        runner.process, runner._pgid = process, process.pid
        try:
            with patch("bmo_ai.features.games.os.waitid", side_effect=NotImplementedError), \
                 patch("bmo_ai.features.games.os.killpg") as signaler, self.assertRaises(FeatureError) as error:
                runner.stop()
            self.assertEqual(error.exception.code, "cleanup_failed")
            signaler.assert_not_called()
            process.poll.assert_not_called(); process.wait.assert_not_called()
            self.assertIs(runner.process, process)
        finally:
            runner.process = runner._pgid = None

    def test_pause_resume_only_signal_owned_group_and_stop_resumes_before_term(self):
        runner = self.runner()
        runner.start(None)
        process = runner.process
        with patch("bmo_ai.features.games.os.killpg", wraps=os.killpg) as signaler:
            runner.pause()
            runner.pause()
            runner.resume()
            runner.resume()
            runner.pause()
            runner.stop()
        signals = [call.args for call in signaler.call_args_list]
        self.assertEqual(signals[:4], [(process.pid, signal.SIGSTOP), (process.pid, signal.SIGCONT),
                                     (process.pid, signal.SIGSTOP), (process.pid, signal.SIGCONT)])
        self.assertEqual(signals[4], (process.pid, signal.SIGTERM))
        self.assertIsNotNone(process.returncode)

    def test_uncertain_group_retains_child_ownership_and_can_be_retried(self):
        runner = self.runner(stop_timeout=.01)
        runner.start(None)
        process = runner.process
        with patch("bmo_ai.desktop_browser._active_owned_group", return_value=True), \
             self.assertRaises(FeatureError):
            runner.stop()
        self.assertIs(runner.process, process)
        self.assertIsNone(process.returncode)
        runner.stop()
        self.assertIsNone(runner.process)
        self.assertIsNotNone(process.returncode)

    def test_graceful_quit_callback_does_not_allow_post_reap_signals(self):
        runner = self.runner(graceful_quit=lambda process: (process.terminate(), process.wait(timeout=2)))
        runner.start(None)
        process = runner.process
        try:
            with patch("bmo_ai.features.games.os.killpg") as signaler, self.assertRaises(FeatureError):
                runner.stop()
            signaler.assert_not_called()
            self.assertIsNotNone(process.returncode)
        finally:
            # This deliberately invalid owner callback reaped its own test
            # child. Production refuses to use the now reusable numeric PID.
            runner.process = runner._pgid = None

    def test_fresh_orphan_cleanup_is_checked_in_a_separate_test_subreaper(self):
        script = r'''
import ctypes,json,os,signal,subprocess,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
from bmo_ai.features.games import OwnedProcessRunner
if ctypes.CDLL(None).prctl(36,1,0,0,0) != 0: raise RuntimeError('test subreaper unavailable')
with tempfile.TemporaryDirectory(prefix='bmo-media-owned-toy-') as folder:
    root=Path(folder); ready=root/'child'; release=root/'release'
    child_code="import os,pathlib,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path("+repr(str(ready))+").write_text(str(os.getpid()));time.sleep(30)"
    leader_code="import pathlib,subprocess,sys,time;p=subprocess.Popen([sys.executable,'-B','-c',"+repr(child_code)+"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);r=pathlib.Path("+repr(str(release))+ ");exec(\"while not r.exists(): time.sleep(.005)\")"
    runner=OwnedProcessRunner((sys.executable,'-B','-c',leader_code),verified=True,ready_probe=lambda process:ready.exists(),stop_timeout=.05)
    child=None; leader=None; events=[]
    try:
        runner.start(None); leader=runner.process; child=int(ready.read_text()); release.write_text('go')
        deadline=time.monotonic()+3
        while os.waitid(os.P_PID,leader.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG) is None:
            if time.monotonic()>deadline: raise RuntimeError('leader exit timeout')
            time.sleep(.005)
        assert runner.poll()=='ended' and leader.returncode is None
        original_killpg=os.killpg
        def signaler(pgid,value):
            assert pgid==leader.pid and leader.returncode is None
            assert os.waitid(os.P_PID,leader.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG) is not None
            events.append(value); original_killpg(pgid,value)
        with patch('bmo_ai.features.games.os.killpg',side_effect=signaler): runner.stop()
        assert events==[signal.SIGTERM,signal.SIGKILL] and leader.returncode==0
        deadline=time.monotonic()+3
        while True:
            pid,status=os.waitpid(child,os.WNOHANG)
            if pid==child: break
            if time.monotonic()>deadline: raise RuntimeError('child reap timeout')
            time.sleep(.005)
        child=None; runner.stop()
        print(json.dumps({'orphan_group_stopped':True,'all_toy_children_reaped':True}))
    finally:
        if child is not None:
            try:
                if os.getpgid(child)==leader.pid: os.kill(child,signal.SIGKILL)
            except ProcessLookupError: pass
            os.waitpid(child,0)
        if leader is not None and leader.returncode is None:
            try: os.kill(leader.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            leader.wait(timeout=3)
'''
        result = subprocess.run([sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1], timeout=10, capture_output=True, text=True,
            env={"PATH": os.defpath, "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(result.returncode, 0, "Isolated toy media process lifecycle failed")
        self.assertEqual(json.loads(result.stdout), {"orphan_group_stopped": True, "all_toy_children_reaped": True})


if __name__ == "__main__":
    unittest.main()
