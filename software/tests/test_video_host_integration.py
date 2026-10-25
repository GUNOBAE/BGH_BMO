"""Synthetic video through actual Host control/queue/lifecycle, with real files.

No device, mic, native encoder, subprocess, key resolver or external API is run.
The separately selected video directory outlives Host's temporary media fixtures.
"""
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.camera import CameraFeature, DemoBackend
from bmo_ai.features.camera.video import CONTRACT
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.media.video_fixture import validate_avi_bytes
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, amount=.5): self.now += amount


class VideoHostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary) / 'selected_videos'
        self.clock = Clock()
        self.hosts = []
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('external network forbidden')))
        self.enterContext(patch('socket.socket.connect_ex', side_effect=AssertionError('external network forbidden')))
        for target in ('subprocess.Popen', 'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                       'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.cloud.create_client',
                       'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(target, side_effect=AssertionError('real IO forbidden')))

    def tearDown(self):
        for host in self.hosts:
            if not host._closed:
                host.close()

    def host(self, *, selected=True, simulate=True, demo_assets=False, ptt=True):
        backend = DemoBackend(clock=self.clock, size=(16, 16))
        camera = CameraFeature(backend_factory=lambda: backend, clock=self.clock)
        config, client = integration_config(), FeatureSimulatedClient()
        # Even the live-policy denial test injects a wholly synthetic voice.
        def silent_player(_command, **options):
            options['cancel_check']()
            options['on_start']()
            options['cancel_check']()
        voice = IntegrationVoice(config, client, simulate=True, emit=lambda *a, **k: None,
                                 cancel_check=lambda: None, player=silent_player)
        host = FeatureHost(clock=self.clock, camera=camera, config=config, client=client, voice=voice,
            runtime=RuntimePolicy(simulate=simulate, conversation_ptt_microphone=ptt),
            demo_assets=demo_assets, video_directory=self.root if selected else None)
        self.hosts.append(host)
        return host, backend

    def control(self, host, action, value=None, request=None):
        return host.control(HostControl(action, value), request or 'test_' + action)

    def start(self, host, *, request='video_start_1', consent=True):
        return self.control(host, 'video_start', {'consent': consent}, request)

    def poll(self, host):
        host.heartbeat()
        host.poll()
        return host.snapshot()

    def finish_voice(self, host):
        deadline = time.monotonic() + 3
        while host.session.busy and time.monotonic() < deadline:
            self.poll(host)
            time.sleep(.002)
        self.assertFalse(host.session.busy)
        self.poll(host)

    def test_default_idle_has_no_video_storage_or_camera_io(self):
        host, backend = self.host(selected=False)
        for _ in range(5): state = self.poll(host)
        self.assertEqual(CONTRACT, state['camera_video']['contract'])
        self.assertEqual('ASSET_PENDING', state['camera_video']['readiness'])
        self.assertFalse(state['camera_video']['recording'])
        self.assertFalse(self.root.exists())
        self.assertEqual(0, backend.open_count)
        self.assertEqual([], host.client.requests)
        self.assertEqual('video_storage_not_selected', self.start(host).code)

    def test_selected_directory_and_true_consent_are_required(self):
        host, backend = self.host()
        denied = self.start(host, consent=False)
        self.assertEqual(('rejected', 'video_consent_required'), (denied.status, denied.code))
        self.assertFalse(self.root.exists())
        self.assertEqual(0, backend.open_count)
        for invalid in ({}, {'consent': 1}, {'consent': True, 'path': '/private'}, None):
            with self.subTest(value=invalid), self.assertRaises(FeatureError):
                HostControl('video_start', invalid)

    def test_live_camera_video_denied_even_with_selected_directory_and_ptt_opt_in(self):
        host, backend = self.host(simulate=False)
        result = self.start(host)
        self.assertEqual(('rejected', 'capability_denied'), (result.status, result.code))
        self.assertEqual('denied', host.snapshot()['capabilities']['camera_video'])
        self.assertFalse(self.root.exists())
        self.assertEqual(0, backend.open_count)
        self.assertIsNone(host.video_bytes('video_' + 'a' * 32))
        self.assertEqual([], host.client.requests)

    def test_owner_queue_start_stop_yields_verified_private_silent_video(self):
        host, backend = self.host()
        admitted = host.queue_command('start-nonce', HostControl('video_start', {'consent': True}))
        self.assertEqual('accepted', admitted['status'])
        self.assertEqual(0, backend.open_count)  # HTTP admission does no capture.
        self.assertFalse(self.root.exists())
        first = self.poll(host)
        self.assertTrue(first['camera_video']['recording'])
        self.assertEqual('conversation', first['mode'])
        self.assertIsNone(first['audio_owner'])
        for _ in range(3):
            self.clock.advance()
            self.poll(host)
        stopped = host.queue_command('stop-nonce', HostControl('video_stop'))
        final = self.poll(host)
        result = final['camera_video']['result']
        payload = host.video_bytes(result['video_id'])
        self.assertIsNotNone(payload)
        self.assertEqual(result['sha256'], hashlib.sha256(payload).hexdigest())
        self.assertEqual(result['bytes'], len(payload))
        self.assertEqual((4, 0), (validate_avi_bytes(payload)['frames'], result['audio_streams']))
        self.assertTrue(result['completed'])
        self.assertTrue(result['simulated'])
        self.assertFalse(result['executed'])
        self.assertEqual('simulated', final['last_result']['status'])
        self.assertEqual(stopped['request_id'], final['last_result']['request_id'])
        self.assertFalse(final['camera_video']['recording'])
        self.assertEqual((1, 1), (backend.open_count, backend.close_count))
        self.assertEqual(0o700, stat.S_IMODE(self.root.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE((self.root / (result['video_id'] + '.avi')).stat().st_mode))
        self.assertEqual([], host.client.requests)

    def test_duplicate_nonce_does_not_reopen_or_overwrite_saved_file(self):
        host, backend = self.host()
        command = HostControl('video_start', {'consent': True})
        first = host.queue_command('one-start', command)
        self.assertEqual(first, host.queue_command('one-start', command))
        self.poll(host)
        self.control(host, 'video_stop')
        snapshot = self.poll(host)['camera_video']
        result = snapshot['result']
        path = self.root / (result['video_id'] + '.avi')
        inode, digest = path.stat().st_ino, hashlib.sha256(path.read_bytes()).hexdigest()
        receipt = host.queue_command('one-start', command)
        self.assertTrue(receipt['completed'])
        self.poll(host)
        self.assertEqual(1, backend.open_count)
        self.assertEqual(inode, path.stat().st_ino)
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(1, len(list(self.root.glob('*.avi'))))

    def test_duplicate_stop_preserves_both_start_and_stop_receipts(self):
        host, backend = self.host()
        started = host.queue_command('begin', HostControl('video_start', {'consent': True}))
        self.poll(host)
        stopped = host.queue_command('save', HostControl('video_stop'))
        self.poll(host)
        first = host.queue_command('begin', HostControl('video_start', {'consent': True}))
        second = host.queue_command('save', HostControl('video_stop'))
        self.assertEqual(started['request_id'], first['request_id'])
        self.assertEqual(stopped['request_id'], second['request_id'])
        self.assertTrue(first['completed']); self.assertTrue(second['completed'])
        self.assertEqual('video_saved', first['code']); self.assertEqual('video_saved', second['code'])
        self.assertEqual(1, backend.open_count)

    def test_recording_blocks_question_ptt_repeat_without_audio_or_stt(self):
        host, backend = self.host()
        self.start(host)
        self.assertFalse(host.start_question('Synthetic question.'))
        self.assertFalse(host.ptt_button())
        repeat = self.control(host, 'repeat')
        self.assertEqual(('rejected', 'video_mode_busy'), (repeat.status, repeat.code))
        self.assertEqual([], host.client.requests)
        self.assertFalse(host.session.busy)
        self.assertIsNone(host.audio.owner)
        self.assertTrue(self.poll(host)['camera_video']['recording'])
        self.assertFalse(host.microphone.snapshot()['microphone_open'])

    def test_foreground_game_menu_and_existing_audio_owner_block_video(self):
        host, backend = self.host(demo_assets=True)
        host.execute(HostIntent('open_game_menu'), request_id='menu')
        self.assertEqual('video_mode_busy', self.start(host).code)
        host.execute(HostIntent('return_to_face'), request_id='face')
        lease = host.audio.acquire('greeting')
        self.assertEqual('video_mode_busy', self.start(host, request='second').code)
        self.assertFalse(self.root.exists())
        self.assertEqual(0, backend.open_count)
        host.audio.release(lease)

    def test_game_transition_cancels_video_before_broker_off(self):
        host, backend = self.host(demo_assets=True)
        self.start(host)
        outcome = host.execute(HostIntent('open_game_menu'), request_id='menu')
        state = self.poll(host)
        self.assertEqual('simulated', outcome.status)
        self.assertEqual('game_menu', state['mode'])
        self.assertFalse(state['camera_video']['recording'])
        self.assertEqual('video_mode_changed', state['camera_video']['result']['code'])
        self.assertEqual(1, backend.close_count)
        self.assertFalse(list(self.root.glob('*.avi')))
        self.assertFalse(host.ptt_button())

    def test_return_to_face_cancels_even_without_mode_change(self):
        host, backend = self.host()
        self.start(host)
        outcome = host.execute(HostIntent('return_to_face'), request_id='back')
        state = self.poll(host)
        self.assertEqual('simulated', outcome.status)
        self.assertEqual('conversation', state['mode'])
        self.assertFalse(state['camera_video']['recording'])
        self.assertEqual('video_return_to_face', state['camera_video']['result']['code'])
        self.assertFalse(host.camera.broker.opened)
        self.assertEqual(1, backend.close_count)

    def test_preview_transition_cancels_video_then_reopens_same_broker(self):
        host, backend = self.host()
        self.start(host)
        result = self.control(host, 'preview', True)
        state = self.poll(host)
        self.assertEqual('simulated', result.status)
        self.assertEqual('video_preview_transition', state['camera_video']['result']['code'])
        self.assertFalse(state['camera_video']['recording'])
        self.assertTrue(state['camera']['preview'])
        self.assertEqual((2, 1), (backend.open_count, backend.close_count))
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_photo_transition_cancels_video_before_entering_preview(self):
        host, backend = self.host()
        self.start(host)
        result = host.execute(HostIntent('take_photo'), request_id='photo')
        state = self.poll(host)
        self.assertEqual(('running', 'photo_countdown'), (result.status, result.code))
        self.assertEqual('photo', state['mode'])
        self.assertEqual('video_photo_transition', state['camera_video']['result']['code'])
        self.assertFalse(state['camera_video']['recording'])
        self.assertTrue(state['camera']['preview'])
        self.assertEqual((2, 1), (backend.open_count, backend.close_count))
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_other_host_storage_lease_is_rejected_not_ever_running_receipt(self):
        first, original = self.host()
        second, competing = self.host()
        self.assertEqual('running', self.start(first).status)
        result = self.start(second, request='other-host')
        self.assertEqual(('rejected', 'video_busy'), (result.status, result.code))
        state = self.poll(second)
        self.assertFalse(state['camera_video']['recording'])
        self.assertEqual(('rejected', 'video_busy'),
                         (state['last_result']['status'], state['last_result']['code']))
        self.assertEqual(0, competing.open_count)
        self.assertTrue(self.poll(first)['camera_video']['recording'])
        self.assertEqual(1, original.open_count)

    def test_duration_auto_finish_updates_original_request_receipt(self):
        host, backend = self.host()
        accepted = host.queue_command('auto-limit', HostControl('video_start', {'consent': True}))
        self.poll(host)
        self.clock.advance(13)
        state = self.poll(host)
        self.assertFalse(state['camera_video']['recording'])
        self.assertEqual('video_duration_limit', state['camera_video']['result']['stop_reason'])
        receipt = host.queue_command('auto-limit', HostControl('video_start', {'consent': True}))
        self.assertEqual(accepted['request_id'], receipt['request_id'])
        self.assertEqual('simulated', receipt['status'])
        self.assertTrue(receipt['completed'])
        self.assertEqual(1, backend.close_count)

    def test_emergency_stop_beats_full_ordinary_queue_and_stale_video_starts(self):
        host, backend = self.host()
        self.start(host)
        for index in range(32):
            host.queue_command('waiting-' + str(index), HostControl('video_start', {'consent': True}))
        with self.assertRaises(FeatureError):
            host.queue_command('overflow', HostControl('video_start', {'consent': True}))
        admitted_stop = host.queue_command('safety-stop', HostControl('stop'))
        state = self.poll(host)
        self.assertFalse(state['camera_video']['recording'])
        self.assertEqual('cancelled', state['camera_video']['result']['status'])
        self.assertEqual(1, backend.open_count)
        self.assertEqual(1, backend.close_count)
        self.assertFalse(list(self.root.glob('*.avi')))
        receipt = host.queue_command('repeat-stop', HostControl('stop'))
        self.assertEqual(admitted_stop['request_id'], receipt['request_id'])

    def test_cancel_retires_queued_start_before_any_camera_open(self):
        host, backend = self.host()
        host.queue_command('delayed-start', HostControl('video_start', {'consent': True}))
        host.queue_command('blur-cancel', HostControl('cancel'))
        state = self.poll(host)
        self.assertFalse(state['camera_video']['recording'])
        self.assertEqual(0, backend.open_count)
        self.assertFalse(self.root.exists())
        receipt = host.queue_command('delayed-start', HostControl('video_start', {'consent': True}))
        self.assertEqual(('rejected', 'stale_mode'), (receipt['status'], receipt['code']))

    def test_failed_storage_permissions_not_changed_and_no_camera_open(self):
        self.root.mkdir(mode=0o750)
        self.root.chmod(0o750)
        host, backend = self.host()
        result = self.start(host)
        self.assertEqual('unsafe_video_root', result.code)
        self.assertEqual(0o750, stat.S_IMODE(self.root.stat().st_mode))
        self.assertEqual(0, backend.open_count)
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_close_preserves_saved_media_and_cancels_next_active_recording(self):
        host, backend = self.host()
        self.start(host)
        self.control(host, 'video_stop')
        saved = self.poll(host)['camera_video']['result']
        path = self.root / (saved['video_id'] + '.avi')
        before = path.read_bytes()
        self.start(host, request='video_start_2')
        host.close()
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(1, len(list(self.root.glob('*.avi'))))
        self.assertFalse(host.video.snapshot()['recording'])
        self.assertFalse(host.camera.broker.opened)
        self.assertEqual(2, backend.close_count)

    def test_unknown_close_keeps_claim_and_is_not_reported_as_success(self):
        host, backend = self.host()
        self.start(host)
        with patch.object(backend, 'close', return_value=False):
            result = self.control(host, 'video_stop')
        self.assertEqual(('unknown', 'video_close_failed'), (result.status, result.code))
        self.assertFalse(result.completed)
        self.assertEqual('UNVERIFIED', self.poll(host)['camera_video']['readiness'])
        self.assertFalse(host.camera.broker.opened)
        with self.assertRaises(FeatureError): host.camera.broker.open()
        self.assertFalse(host.stop())
        self.assertFalse(list(self.root.glob('*.avi')))
        with self.assertRaises(FeatureError): host.close()

    def test_emotion_history_and_repeat_survive_without_reexecuting_video(self):
        host, backend = self.host()
        self.assertTrue(host.start_question('Synthetic seed question.'))
        self.finish_voice(host)
        history = [dict(item) for item in host.session.brain.history]
        emotion = host.session.snapshot()['emotion']
        self.assertTrue(host.session.voice.has_cache)
        self.start(host)
        self.control(host, 'video_stop')
        saved = self.poll(host)['camera_video']['result']
        before = (backend.open_count, backend.read_count)
        self.assertEqual(history, host.session.brain.history)
        self.assertEqual(emotion, host.session.snapshot()['emotion'])
        self.assertEqual('running', self.control(host, 'repeat').status)
        self.finish_voice(host)
        self.assertEqual(before, (backend.open_count, backend.read_count))
        self.assertEqual(history, host.session.brain.history)
        self.assertEqual(emotion, host.session.snapshot()['emotion'])
        self.assertEqual(saved['video_id'], host.snapshot()['camera_video']['result']['video_id'])
        self.assertEqual(1, len(host.client.requests))

    def test_public_snapshot_contains_safe_metadata_not_paths_bytes_or_false_live_claim(self):
        host, backend = self.host()
        self.start(host)
        self.control(host, 'video_stop')
        state = self.poll(host)
        public = json.dumps(state)
        self.assertNotIn(str(self.root), public)
        self.assertNotIn('RIFF', public)
        self.assertNotIn('payload', public)
        self.assertFalse(state['camera_video']['executed'])
        self.assertFalse(state['camera_video']['actual_camera_verified'])
        self.assertEqual(0, state['camera_video']['audio_streams'])
        self.assertIsNone(host.video_bytes('video_' + '0' * 32))
        self.assertIsNone(host.video_bytes('../private'))


if __name__ == '__main__':
    unittest.main()
