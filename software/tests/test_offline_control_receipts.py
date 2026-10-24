"""Three offline review regressions: receipts, safety capacity, partial playback.

The live-policy path uses fake recorder/STT/Fish and an owned Python callback as
its player. It never executes ALSA, a subprocess, an SDK or a physical device.
"""
import builtins
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


class PlayerGate:
    def __init__(self, *, failure=False):
        self.started, self.release = Event(), Event()
        self.calls = 0
        self.failure = failure

    def __call__(self, command, *, on_start, cancel_check, **kwargs):
        self.calls += 1
        cancel_check()
        on_start()
        self.started.set()
        # A deliberately uncooperative synthetic boundary lets multiple owner
        # controls arrive before worker completion. No child/device is started.
        if not self.release.wait(3):
            raise AssertionError('Synthetic player gate was not released')
        if self.failure:
            raise audio.AudioPlaybackError('Synthetic failure', code='device_unavailable')
        cancel_check()


class OfflineControlReceiptTests(unittest.TestCase):
    def setUp(self):
        self.hosts, self.gates, self.forbidden = [], [], []
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'subprocess.Popen',
                       'bmo_ai.cloud.create_client', 'bmo_ai.private_credentials.resolve_credentials',
                       'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                       'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.local_piper.synthesize_wav'):
            guard = Mock(side_effect=AssertionError('Production IO forbidden'))
            self.forbidden.append(guard)
            self.enterContext(patch(target, guard))
        original_import = builtins.__import__
        def no_device_import(name, *args, **kwargs):
            if name.split('.')[0] in ('picamera2', 'libcamera', 'cv2', 'numpy', 'serial'):
                raise AssertionError('Real optional device import forbidden')
            return original_import(name, *args, **kwargs)
        self.enterContext(patch('builtins.__import__', side_effect=no_device_import))

    def tearDown(self):
        for gate in self.gates:
            gate.release.set()
        for host in self.hosts:
            host.close()
        for guard in self.forbidden:
            guard.assert_not_called()

    def host(self, *, live=False, failure=False):
        client = FeatureSimulatedClient([dict(version=1, speech='Synthetic receipt answer.',
            emotion='crying', gesture='none', intent=None)])
        gate = PlayerGate(failure=failure)
        config = integration_config()
        if live:
            # Replace the forbidden boundaries with local synthetic functions;
            # never invoke a production recorder, transcriber or synthesizer.
            self.enterContext(patch('bmo_ai.audio.record_wav', side_effect=lambda *a, **k: synthetic_wav()))
            self.enterContext(patch('bmo_ai.audio.transcribe', return_value='Synthetic offline input.'))
        voice = IntegrationVoice(config, client, simulate=not live, emit=lambda *a, **k: None,
            player=gate, fish_api_key='fixture-only-no-key',
            fish_synthesizer=lambda *a, **k: synthetic_wav())
        host = FeatureHost(demo_assets=not live, client=client, voice=voice, config=config,
                           runtime=RuntimePolicy(simulate=not live))
        self.hosts.append(host);self.gates.append(gate)
        return host, gate

    def started(self, host, gate):
        deadline = time.monotonic()+2
        while not gate.started.is_set() and time.monotonic()<deadline:
            host.poll();time.sleep(.001)
        self.assertTrue(gate.started.is_set(), 'Synthetic player never started')
        host.poll()
        self.assertTrue(host.session.busy)
        self.assertEqual(gate.calls, 1)

    def finish(self, host, gate):
        gate.release.set()
        deadline = time.monotonic()+2
        while host.session.busy and time.monotonic()<deadline:
            host.poll();time.sleep(.001)
        self.assertFalse(host.session.busy)
        host.poll()
        self.assertIsNone(host.audio.owner)

    def result(self, host, request):
        result = host._result_history.get(request)
        self.assertIsNotNone(result, 'Missing owner receipt')
        self.assertNotIn(result.status, ('accepted', 'running'), 'Receipt never reached a terminal state')
        return result

    def test_original_question_stop_and_reset_each_receive_terminal_receipt(self):
        host, gate = self.host()
        question = host.control(HostControl('question', 'Synthetic question.'), 'question_q')
        self.assertEqual(question.status, 'running')
        self.started(host, gate)
        stop = host.control(HostControl('stop'), 'stop_s')
        reset = host.control(HostControl('reset'), 'reset_r')
        self.assertEqual((stop.status, reset.status), ('running', 'running'))
        self.finish(host, gate)
        self.result(host, 'question_q')
        self.assertTrue(self.result(host, 'stop_s').completed)
        self.assertTrue(self.result(host, 'reset_r').completed)
        self.assertEqual(host.session.snapshot()['emotion'], 'neutral')

    def test_pending_stop_or_reset_does_not_claim_failed_resource_cleanup_completed(self):
        for action in ('stop', 'reset'):
            with self.subTest(action=action):
                host, gate = self.host()
                host.control(HostControl('question', 'Synthetic question.'), 'question_'+action)
                self.started(host, gate)
                with patch.object(host.camera, 'off', side_effect=FeatureError('cleanup_failed')):
                    pending = host.control(HostControl(action), 'cleanup_'+action)
                    self.assertEqual(pending.status, 'running')
                    self.finish(host, gate)
                    failed = self.result(host, 'cleanup_'+action)
                    self.assertIn(failed.status, ('failed', 'unknown', 'rejected'))
                    self.assertFalse(failed.completed)
                self.result(host, 'question_'+action)

    def test_reserved_stop_and_cancel_bypass_full_normal_queue_and_retire_old_actions(self):
        for action in ('stop', 'cancel'):
            with self.subTest(action=action):
                host, _ = self.host()
                for index in range(32):
                    host.queue_command('queued_'+str(index), HostIntent('launch_game', 'fixture_game_a'))
                self.assertEqual(host._commands.qsize(), 32)
                receipt = host.queue_command('safety_first', HostControl(action))
                repeated = host.queue_command('safety_second', HostControl(action))
                self.assertEqual(receipt['request_id'], repeated['request_id'])
                host.poll()
                self.result(host, receipt['request_id'])
                self.assertEqual(host.modes.mode, 'conversation')
                self.assertIsNone(host.games.snapshot()['active_id'])
                self.assertIsNone(host.audio.owner)
                self.assertEqual(host._commands.qsize(), 0)

    def test_reserved_safety_receipts_remain_bounded_when_normal_receipts_are_full(self):
        host, _ = self.host()
        for index in range(512):
            host.queue_command('normal_'+str(index), HostControl('emotion', 'neutral'))
            if index%32==31:
                host.poll()
        self.assertEqual(len(host._records), 512)
        with self.assertRaises(FeatureError):
            host.queue_command('ordinary_overflow', HostControl('emotion', 'neutral'))
        safety = {}
        for action in ('stop', 'cancel'):
            first = host.queue_command(action+'_first', HostControl(action))
            safety[action] = first['request_id']
            for index in range(64):
                repeated = host.queue_command(action+'_'+str(index), HostControl(action))
                self.assertEqual(repeated['request_id'], first['request_id'])
        self.assertEqual(len(set(safety.values())), 2)
        self.assertEqual(len(host._records), 512)
        host.poll()
        for request in safety.values():
            self.result(host, request)
        self.assertEqual(len(host._records), 512)
        self.assertLessEqual(len(host._result_history), 512)

    def test_live_started_then_cancel_reports_partial_execution_without_real_io(self):
        host, gate = self.host(live=True)
        host.control(HostControl('question', 'Synthetic question.'), 'live_cancel_q')
        self.started(host, gate)
        host.control(HostControl('cancel'), 'cancel_c')
        self.finish(host, gate)
        result = self.result(host, 'live_cancel_q')
        self.assertEqual(result.status, 'cancelled')
        self.assertFalse(result.simulated or result.completed)
        self.assertTrue(result.executed)
        self.assertEqual(result.completion, 'partial_playback')

    def test_live_started_then_player_error_reports_partial_execution_without_real_io(self):
        host, gate = self.host(live=True, failure=True)
        host.control(HostControl('question', 'Synthetic question.'), 'live_error_q')
        self.started(host, gate)
        self.finish(host, gate)
        result = self.result(host, 'live_error_q')
        self.assertEqual(result.status, 'failed')
        self.assertFalse(result.simulated or result.completed)
        self.assertTrue(result.executed)
        self.assertEqual(result.completion, 'partial_playback')


if __name__=='__main__':
    unittest.main()
