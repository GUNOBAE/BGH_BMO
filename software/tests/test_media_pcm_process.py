"""Actual child-process PCM producer, with generated WAV and no audio device."""
import mmap
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media.pcm_clock import SampleClockWavBackend
from bmo_ai.features.media.pcm_process import LatestRecord,SIZE,SyntheticClockSink
from bmo_ai.master_volume import MasterVolume


class MusicProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory=self.enterContext(tempfile.TemporaryDirectory(prefix='pcm-process-'))
        self.path=Path(self.directory)/'generated.wav'
        with wave.open(str(self.path),'wb') as stream:
            stream.setnchannels(2);stream.setsampwidth(2);stream.setframerate(48000)
            stream.writeframes((5000).to_bytes(2,'little',signed=True)*2*72000)
        self.fd=os.open(self.path,os.O_RDONLY|os.O_CLOEXEC)
        self.addCleanup(os.close,self.fd)
        self.handle=SimpleNamespace(fd=self.fd,duration=1.5)
        self.volume=MasterVolume(.25)

    def backend(self,**kwargs):
        backend=SampleClockWavBackend(enabled=True,volume_reviewed=True,volume=self.volume,
            _process_test_sink=True,**kwargs)
        self.addCleanup(backend.stop)
        return backend

    def wait(self,predicate,timeout=2.):
        deadline=time.monotonic()+timeout
        while not predicate():
            if time.monotonic()>deadline:self.fail('Synthetic PCM condition did not settle')
            time.sleep(.005)

    @staticmethod
    def gil_stall(seconds=.25):
        original=sys.getswitchinterval()
        try:
            sys.setswitchinterval(1.)
            deadline=time.perf_counter()+seconds
            while time.perf_counter()<deadline:pass
        finally:sys.setswitchinterval(original)

    def test_old_shared_interpreter_queue_reproduces_underflow_under_gil_stall(self):
        backend=self.backend(sink_factory=SyntheticClockSink)
        backend.start(self.handle)
        self.wait(lambda:backend.output_started)
        self.gil_stall()
        self.wait(lambda:backend.poll()=='failed')
        self.assertEqual(backend.diagnostics()['error_code'],'media_output_underrun')
        self.assertEqual(backend.diagnostics()['producer'],'injected_sink_thread')

    def test_separate_producer_continues_during_identical_parent_gil_stall(self):
        with patch.dict(os.environ,{'FISH_API_KEY':'forbidden-test','OPENAI_API_KEY':'forbidden-test',
                                    'GH_TOKEN':'forbidden-test'}):
            backend=self.backend();backend.start(self.handle)
        self.wait(lambda:backend.output_started)
        self.assertTrue(backend._process_snapshot['provider_env_absent'])
        before=backend.position();pid=backend.diagnostics()['worker_pid']
        self.assertNotEqual(pid,os.getpid())
        self.gil_stall()
        self.assertGreater(backend.position(),before+.15)
        self.assertIsNone(backend.poll())
        self.assertEqual(backend.diagnostics()['producer'],'isolated_process')
        self.wait(lambda:backend.poll()=='ended')
        self.assertIsNone(backend.diagnostics()['error_code'])
        self.assertAlmostEqual(backend.position(),1.5)
        backend.stop()
        self.assertEqual(backend.diagnostics()['worker_exit_code'],0)
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_pause_seek_resume_volume_mute_stop_are_owned_and_sample_based(self):
        backend=self.backend();backend.start(self.handle)
        self.wait(lambda:backend.output_started)
        old=backend._process;before=backend.position()
        backend.pause();paused=backend.position()
        self.assertIsNotNone(old.poll());self.assertGreaterEqual(paused,before)
        time.sleep(.03);self.assertEqual(backend.position(),paused)
        backend.seek(.6);self.assertEqual(backend.position(),.6)
        self.assertIsNone(backend._process)
        backend.resume();self.assertNotEqual(backend._process.pid,old.pid)
        self.wait(lambda:backend.output_started)
        self.assertGreaterEqual(backend.position(),.6)
        self.volume.set(level=.5);backend.volume_changed()
        def amplitude(expected):
            backend.poll()
            return backend._process_snapshot.get('synthetic_last_amplitude')==expected
        self.wait(lambda:amplitude(2500))
        self.volume.set(muted=True);backend.volume_changed()
        self.wait(lambda:amplitude(0))
        pid=backend._process.pid
        backend.stop();self.assertIsNone(backend._process)
        self.assertEqual(backend.position(),0.)
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_worker_crash_keeps_last_frame_and_failure_through_cleanup(self):
        backend=self.backend();backend.start(self.handle)
        self.wait(lambda:backend.output_started)
        position=backend.position();pid=backend._process.pid
        os.kill(pid,signal.SIGKILL)
        self.wait(lambda:backend.poll()=='failed')
        evidence=backend.diagnostics()
        self.assertEqual(evidence['error_code'],'media_output_failed')
        self.assertGreaterEqual(evidence['position_seconds'],position)
        self.assertEqual(evidence['worker_exit_code'],-signal.SIGKILL)
        backend.stop()
        self.assertEqual(backend.diagnostics()['error_code'],'media_output_failed')
        self.assertEqual(backend.diagnostics()['worker_pid'],pid)

    def test_frozen_owned_worker_is_reaped_with_original_cleanup_failure(self):
        backend=self.backend();backend.start(self.handle)
        self.wait(lambda:backend.output_started)
        pid=backend._process.pid
        os.kill(pid,signal.SIGSTOP)
        started=time.monotonic();backend.stop()
        self.assertLess(time.monotonic()-started,2.5)
        self.assertIsNone(backend._process)
        self.assertEqual(backend.diagnostics()['cleanup_error'],'media_output_cleanup_failed')
        self.assertEqual(backend.diagnostics()['error_code'],'media_output_cleanup_failed')
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_frozen_child_expires_same_three_second_guard_and_cannot_clear_fault(self):
        backend=self.backend();backend.start(self.handle)
        self.wait(lambda:backend.output_started)
        pid=backend._process.pid
        os.kill(pid,signal.SIGSTOP)
        try:
            # The child cannot run its own progress guard while stopped. Its
            # last genuine status timestamp must expire on the live parent.
            time.sleep(3.05)
            self.assertEqual(backend.poll(),'failed')
            first=backend.diagnostics()
            self.assertEqual(first['error_code'],'media_output_stalled')
            frozen=backend.position()
            self.assertEqual(backend.poll(),'failed')
            self.assertEqual(backend.position(),frozen)
            self.assertEqual(backend.diagnostics()['error_at_monotonic'],first['error_at_monotonic'])
        finally:os.kill(pid,signal.SIGCONT)
        self.wait(lambda:backend._process.poll() is not None)
        self.assertEqual(backend.poll(),'failed')
        self.assertEqual(backend.diagnostics()['error_code'],'media_output_stalled')
        self.assertEqual(backend.position(),frozen)
        backend.stop()
        self.assertEqual(backend.diagnostics()['error_code'],'media_output_stalled')

    def test_fresh_worker_status_without_sample_progress_still_expires(self):
        backend=self.backend();backend.start(self.handle)
        self.wait(lambda:backend.output_started)
        current=dict(backend._process_snapshot)
        backend._process_progress_frame=current['frame']
        backend._process_progress_at=time.monotonic()-3.1
        def fresh_but_stalled():return dict(current,status_at=time.monotonic())
        with patch.object(backend._process_status,'read',side_effect=fresh_but_stalled):
            self.assertEqual(backend.poll(),'failed')
            self.assertEqual(backend.diagnostics()['error_code'],'media_output_stalled')

    def test_latest_record_is_bounded_and_rejects_torn_payload(self):
        memory=mmap.mmap(-1,SIZE)
        self.addCleanup(memory.close)
        record=LatestRecord(memory)
        record.publish({'frame':1})
        self.assertEqual(record.read(),{'frame':1})
        memory[16]=ord('x')
        self.assertIsNone(record.read())
        with self.assertRaises(FeatureError):record.publish({'value':'a'*4096})


if __name__=='__main__':unittest.main()
