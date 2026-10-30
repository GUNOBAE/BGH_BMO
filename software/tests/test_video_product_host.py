"""Product video modes with actual Host, cache producer and local MP4 encoder.

Synthetic JPEG IO only: these checks never access a device/provider or play audio.
"""
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.camera import CameraFeature, DemoBackend, FixtureDetector
from bmo_ai.features.camera.compressed_video import probe_compressed_video
from bmo_ai.features.camera.models import PreviewFrame
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from test_feature_camera_adapters import jpeg_bytes


class ProductCamera(DemoBackend):
    def __init__(self, clock):
        super().__init__(clock=clock, size=(640, 360))
        self.preview_payload = jpeg_bytes(640, 360)

    def read_preview(self):
        self._seq += 1
        self.read_count += 1
        now = self.clock()
        return PreviewFrame(self._generation, self._seq, now, 640, 360,
                            self.preview_payload, sensor_timestamp=int(now * 1e9))


class VideoProductHostTests(unittest.TestCase):
    def setUp(self):
        self.offset = 0.
        self.serial = 0
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.backend = ProductCamera(self.clock)
        self.camera = CameraFeature(backend_factory=lambda: self.backend,
            detector=FixtureDetector(), clock=self.clock, max_hz=4,
            asynchronous_preview=True, preview_hz=20)
        self.client = FeatureSimulatedClient()
        voice = IntegrationVoice(integration_config(), self.client, simulate=True,
            emit=lambda *_a, **_k: None,
            player=lambda _cmd, on_start, cancel_check, **_kw:
                (cancel_check(), on_start(), cancel_check()))
        self.host = FeatureHost(demo_assets=True, character_life=True, camera=self.camera,
            video_directory=self.root / 'clips', client=self.client, voice=voice,
            clock=self.clock, photo_feedback=False)
        self.camera.on_target = self.host._camera_target
        self.addCleanup(self.host.close)
        for name in ('socket.socket.connect', 'bmo_ai.cloud.create_client',
                     'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                     'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.local_piper.synthesize_wav'):
            self.enterContext(patch(name, side_effect=AssertionError('hardware/provider IO forbidden')))

    def clock(self):
        return time.monotonic() + self.offset

    def poll(self):
        self.host.heartbeat()
        self.host.poll()
        return self.host.snapshot()

    def until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        state = self.poll()
        while not predicate(state):
            if time.monotonic() >= deadline:
                self.fail('bounded product phase did not complete: '+str((state.get('mode'), state.get('video_phase'), state.get('camera_video'))))
            time.sleep(.01)
            state = self.poll()
        return state

    def execute(self, name, target=None):
        self.serial += 1
        return self.host.execute(HostIntent(name, target), request_id=f'product_video_{self.serial}')

    def ready_video(self, target='seconds_1'):
        result = self.execute('start_video', target)
        self.assertEqual(result.status, 'running')
        state = self.until(lambda s: bool(s.get('video_preview_token')))
        self.assertEqual(state['video_phase'], 'preparing')
        self.assertTrue(state['camera']['preview_available'])
        self.assertFalse(self.host.session.busy)
        self.host.control(HostControl('video_preview_ready', {
            'generation':state['mode_generation'], 'request_id':state['video_request_id'],
            'preview_token':state['video_preview_token']}), f'product_video_visible_{self.serial}')
        self.assertEqual(self.poll()['video_phase'], 'countdown')
        self.offset += 5.01
        return self.poll()

    def test_back_while_encoder_starting_waits_for_worker_and_cannot_capture_restored_stream(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def slow_probe():
            entered.set()
            release.wait(3)
            return dict(available=True, code='video_encoder_ready')
        with patch('bmo_ai.features.camera.compressed_video.probe_compressed_video', slow_probe), \
             patch('bmo_ai.features.camera.compressed_video.subprocess.Popen', side_effect=AssertionError('cancelled startup must not encode')) as spawn:
            self.execute('start_face_tracking')
            self.ready_video()
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.host.video.snapshot()['worker_alive'])
            self.assertEqual(self.host.video.snapshot()['phase'], 'starting')
            self.execute('return_to_face')
            state = self.poll()
            self.assertEqual(state['mode'], 'video')
            self.assertEqual(state['video_phase'], 'finalizing')
            self.assertTrue(state['camera']['on'])
            self.assertFalse(state['camera']['tracking'])
            release.set()
            state = self.until(lambda s: s['mode'] == 'conversation')
            self.assertFalse(state['camera_video']['worker_alive'])
            self.assertTrue(state['camera']['tracking'])
            self.assertFalse(state['camera']['preview'])
            spawn.assert_not_called()
            self.assertEqual(list((self.root / 'clips').glob('*.mp4')), [])

    def test_cancel_countdown_never_starts_encoder_and_restores_tracking(self):
        self.execute('start_face_tracking')
        with patch('bmo_ai.features.camera.compressed_video.probe_compressed_video') as probe:
            self.execute('start_video')
            self.until(lambda s: bool(s.get('video_preview_token')))
            self.host.control(HostControl('cancel'), 'product_cancel_preparing')
            state = self.until(lambda s: s['mode'] == 'conversation')
            self.assertTrue(state['camera']['tracking'])
            self.assertFalse(state['camera']['preview'])
            probe.assert_not_called()
            self.assertFalse((self.root / 'clips').exists())

    def test_global_stop_waits_for_startup_then_does_not_restore_camera_consumers(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def slow_probe():
            entered.set()
            release.wait(3)
            return dict(available=True, code='video_encoder_ready')
        with patch('bmo_ai.features.camera.compressed_video.probe_compressed_video', slow_probe), \
             patch('bmo_ai.features.camera.compressed_video.subprocess.Popen', side_effect=AssertionError('stopped startup must not encode')) as spawn:
            self.execute('start_face_tracking')
            self.ready_video()
            self.assertTrue(entered.wait(1))
            self.host.control(HostControl('stop'), 'product_global_stop')
            state = self.poll()
            self.assertEqual(state['mode'], 'video')
            self.assertTrue(state['camera_video']['worker_alive'])
            release.set()
            state = self.until(lambda s: s['mode'] == 'conversation')
            self.assertFalse(state['camera']['on'])
            self.assertFalse(state['camera']['tracking_requested'])
            self.assertEqual(state['camera']['consumers'], [])
            spawn.assert_not_called()

    def test_missing_camera_is_a_safe_failed_mode_not_host_poll_exception(self):
        self.backend.open = Mock(side_effect=FeatureError('camera_unavailable'))
        self.execute('start_video')
        state = self.until(lambda s: s.get('video_phase') == 'failed')
        self.assertEqual(state['video']['code'], 'camera_unavailable')
        self.assertFalse(state['camera']['on'])
        self.execute('return_to_face')
        self.assertEqual(self.poll()['mode'], 'conversation')

    def test_camera_off_during_preparation_cannot_restore_previous_tracking(self):
        self.execute('start_face_tracking')
        with patch('bmo_ai.features.camera.compressed_video.probe_compressed_video') as probe:
            self.execute('start_video')
            self.until(lambda s: bool(s.get('video_preview_token')))
            self.host.control(HostControl('camera_off'), 'product_video_camera_off')
            state = self.until(lambda s: s['mode'] == 'conversation')
            self.assertFalse(state['camera']['on'])
            self.assertFalse(state['camera']['tracking_requested'])
            self.assertFalse(state['camera']['preview'])
            probe.assert_not_called()

    @unittest.skipUnless(probe_compressed_video()['available'], 'installed local ffmpeg/libx264/ffprobe required')
    def test_recording_gp8_stop_verified_review_repeat_and_next_press_back(self):
        self.execute('start_face_tracking')
        self.ready_video('seconds_2')
        state = self.until(lambda s: s['video_phase'] == 'recording'
            and s['camera_video']['metrics'].get('frames_written', 0) >= 5)
        self.assertFalse(state['camera']['tracking'])
        self.assertTrue(state['camera']['tracking_requested'])
        self.assertFalse(self.host.start_question('must not open a microphone or provider'))
        self.host.control(HostControl('input', {'key':'START', 'pressed':True}), 'product_gp8_stop_down')
        # Held GP8 cannot also return to conversation when finalization ends.
        self.host.control(HostControl('input', {'key':'START', 'pressed':True}), 'product_gp8_held')
        state = self.until(lambda s: s['video_phase'] == 'review', timeout=12)
        self.assertEqual(state['mode'], 'video')
        self.assertTrue(state['video']['completed'])
        self.assertTrue(state['video']['playable'])
        self.assertFalse(state['video']['executed'])
        self.assertFalse(state['camera_video']['worker_alive'])
        metrics = state['video']['metrics']
        expected_duration = metrics['source_span'] + 1 / metrics['target_fps']
        self.assertAlmostEqual(state['video']['duration'], expected_duration, delta=.02,
            msg='encoder startup buffering must not compress source timestamp intervals')
        self.assertEqual(metrics['encoder_timestamp_basis'], 'source_pts_milliseconds')
        video_id = state['video_saved_id']
        payload = self.host.video_bytes(video_id)
        self.assertIn(b'ftyp', payload[:32])
        self.assertEqual(state['video']['audio_streams'], 0)
        saved = list((self.root / 'clips').glob('*.mp4'))
        self.assertEqual(len(saved), 1)
        self.assertFalse(self.client.requests)
        self.host.control(HostControl('repeat'), 'product_review_repeat')
        self.until(lambda s: not self.host.session.busy)
        self.assertEqual(list((self.root / 'clips').glob('*.mp4')), saved)
        self.host.control(HostControl('input', {'key':'START', 'pressed':False}), 'product_gp8_stop_up')
        self.host.control(HostControl('input', {'key':'START', 'pressed':True}), 'product_gp8_back_down')
        state = self.until(lambda s: s['mode'] == 'conversation')
        self.assertTrue(state['camera']['tracking'])
        self.assertFalse(state['camera']['preview'])
        self.assertFalse(self.client.requests)
        self.assertEqual(list((self.root / 'clips').glob('*.mp4')), saved)
        # The keyup after Back belongs to conversation. A new transaction must
        # still honor its first GP8 press rather than retain the previous latch.
        self.execute('start_video')
        self.until(lambda s: bool(s.get('video_preview_token')))
        self.host.control(HostControl('input', {'key':'START', 'pressed':True}), 'product_next_video_first_gp8')
        state = self.until(lambda s: s['mode'] == 'conversation')
        self.assertTrue(state['camera']['tracking'])
        self.assertEqual(list((self.root / 'clips').glob('*.mp4')), saved)

    @unittest.skipUnless(probe_compressed_video()['available'], 'installed local ffmpeg/libx264/ffprobe required')
    def test_app_close_retires_encoder_before_camera_and_preserves_interrupted_playable_file(self):
        self.execute('start_face_tracking')
        self.ready_video('seconds_2')
        self.until(lambda s: s['video_phase'] == 'recording'
            and s['camera_video']['metrics'].get('frames_written', 0) >= 5)
        self.host.close()
        recording = self.host.video.snapshot()
        self.assertFalse(recording['worker_alive'])
        self.assertFalse(recording['recording'])
        self.assertTrue(recording['result']['interrupted'])
        self.assertTrue(recording['result']['playable'])
        self.assertTrue(recording['result']['completed'])
        self.assertEqual(recording['result']['status'], 'cancelled')
        self.assertEqual(len(list((self.root / 'clips').glob('*.mp4'))), 1)
        self.assertFalse(self.camera.snapshot()['on'])
        self.assertFalse(self.camera.snapshot()['preview_pipeline']['worker_alive'])
        self.assertTrue(self.host.cleanup_snapshot()['resources_cleaned'])


if __name__ == '__main__':
    unittest.main()
