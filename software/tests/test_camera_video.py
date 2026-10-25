"""Real files + synthetic camera only; no device, native encoder or audio."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from bmo_ai.features.camera.backends import DemoBackend
from bmo_ai.features.camera.broker import CameraBroker
from bmo_ai.features.camera.feature import CameraFeature
from bmo_ai.features.camera.video import CONTRACT, VideoConfig, VideoRecorder, VideoResult, VideoStore
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.media.video_fixture import synthetic_avi_bytes, validate_avi_bytes


class Clock:
    def __init__(self): self.value = 10.0
    def __call__(self): return self.value
    def advance(self, amount=.5): self.value += amount


class CameraVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'videos'
        self.clock = Clock()
        self.backend = DemoBackend(clock=self.clock, size=(16, 16))
        self.broker = CameraBroker(lambda: self.backend)
        self.store = VideoStore(self.root)
        self.recorder = VideoRecorder(self.broker, store=self.store, clock=self.clock)
        self.addCleanup(self.recorder.close)

    def start(self, request='record-1', generation=1, recorder=None):
        return (recorder or self.recorder).start_video_recording(request, generation=generation, consent=True)

    def stop(self, request='record-1', generation=1):
        return self.recorder.stop_video_recording(request, generation=generation)

    def assert_code(self, code, callback):
        with self.assertRaises(FeatureError) as raised:
            callback()
        self.assertEqual(code, raised.exception.code)
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_lazy_off_until_explicit_consent(self):
        self.assertEqual('READY', self.recorder.snapshot()['readiness'])
        self.assertFalse(self.root.exists())
        self.assertFalse(self.broker.opened)
        result = self.recorder.start_video_recording('no-consent', generation=1)
        self.assertEqual('video_consent_required', result.code)
        self.assertFalse(self.root.exists())
        self.assertEqual(0, self.backend.open_count)

    def test_no_selected_storage_is_safe(self):
        recorder = VideoRecorder(self.broker, clock=self.clock)
        self.assertEqual('ASSET_PENDING', recorder.snapshot()['readiness'])
        self.assertEqual('video_storage_not_selected', self.start(recorder=recorder).code)
        self.assertEqual(0, self.backend.open_count)

    def test_live_is_denied_even_with_consent_and_mock_broker(self):
        recorder = VideoRecorder(self.broker, store=self.store, clock=self.clock, simulated=False)
        self.assertEqual('camera_video_denied', self.start(recorder=recorder).code)
        self.assertEqual('UNSUPPORTED', recorder.snapshot()['readiness'])
        self.assertFalse(self.root.exists())
        self.assertEqual(0, self.backend.open_count)

    def test_real_broker_factory_is_never_opened(self):
        def factory():
            self.fail('real camera factory must not be constructed')
        factory.simulated = False
        recorder = VideoRecorder(CameraBroker(factory), store=self.store, clock=self.clock)
        self.assertEqual('camera_video_denied', self.start(recorder=recorder).code)
        self.assertFalse(self.root.exists())

    def test_unmarked_real_backend_is_refused_before_open(self):
        class RealBackend:
            simulated = False
            def open(inner, generation): self.fail('real sensor open')
            def close(inner): pass
        recorder = VideoRecorder(CameraBroker(lambda: RealBackend()), store=self.store, clock=self.clock)
        self.addCleanup(recorder.close)
        result = self.start(recorder=recorder)
        self.assertEqual('failed', result.status)
        self.assertFalse(recorder.broker.opened)
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_end_to_end_start_frames_stop_verified_private_file(self):
        started = self.start()
        self.assertEqual(('running', 'video_recording'), (started.status, started.code))
        for _ in range(3):
            self.clock.advance()
            snapshot = self.recorder.poll(generation=1)
            self.assertTrue(snapshot['recording'])
        result = self.stop()
        self.assertTrue(result.completed)
        self.assertFalse(result.executed)
        self.assertTrue(result.simulated)
        self.assertEqual(('simulated', 'user_stop', 0), (result.status, result.stop_reason, result.audio_streams))
        payload = self.store.read(result.video_id)
        self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(result.bytes, len(payload))
        meta = validate_avi_bytes(payload)
        self.assertEqual((4, 2, 0), (meta['frames'], meta['fps'], meta['audio_streams']))
        self.assertEqual(result.duration, meta['duration'])
        self.assertFalse(self.broker.opened)
        self.assertEqual((1, 1, 4), (self.backend.open_count, self.backend.close_count, self.backend.read_count))
        self.assertEqual(0o700, stat.S_IMODE(self.root.stat().st_mode))
        for entry in self.root.iterdir():
            self.assertEqual(0o600, stat.S_IMODE(entry.stat().st_mode))
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_duplicate_start_and_stop_do_not_recapture_or_republish(self):
        first = self.start()
        self.assertEqual(first, self.start())
        self.assertEqual(1, self.backend.read_count)
        saved = self.stop()
        original = (self.root / (saved.video_id + '.avi')).stat()
        self.assertEqual(saved, self.stop())
        self.assertEqual(saved, self.start())
        self.assertEqual(original.st_ino, (self.root / (saved.video_id + '.avi')).stat().st_ino)
        self.assertEqual(1, self.backend.open_count)

    def test_new_recording_after_success_and_shutdown_preserves_both_media(self):
        self.start()
        first = self.stop()
        self.start('record-2', generation=2)
        second = self.stop('record-2', generation=2)
        self.assertNotEqual(first.video_id, second.video_id)
        self.assertTrue(self.recorder.close())
        self.assertEqual(2, len(list(self.root.glob('*.avi'))))
        self.assertTrue(self.store.read(first.video_id))
        self.assertTrue(self.store.read(second.video_id))

    def test_duplicate_with_changed_config_is_conflict(self):
        self.start()
        self.stop()
        recorder = VideoRecorder(self.broker, store=VideoStore(self.root), clock=self.clock,
                                 config=VideoConfig(max_duration=5))
        self.addCleanup(recorder.close)
        self.assertEqual('request_id_conflict', self.start(recorder=recorder).code)
        self.assertFalse(self.broker.opened)

    def test_different_request_while_recording_is_busy(self):
        self.start()
        self.assertEqual('video_busy', self.start('second').code)
        self.assertEqual('video_request_mismatch', self.stop('second').code)
        self.assertTrue(self.recorder.snapshot()['recording'])
        self.assertEqual(1, self.backend.open_count)

    def test_key_repeat_poll_does_not_add_unlimited_frames(self):
        self.start()
        for _ in range(200): self.recorder.poll(generation=1)
        self.assertEqual(1, self.recorder.snapshot()['frames'])
        self.assertEqual(1, self.backend.read_count)

    def test_duration_limit_auto_finishes(self):
        recorder = VideoRecorder(self.broker, store=self.store, clock=self.clock,
                                 config=VideoConfig(max_duration=1))
        self.addCleanup(recorder.close)
        self.start(recorder=recorder)
        self.clock.advance(.5)
        recorder.poll(generation=1)
        self.clock.advance(.5)
        snapshot = recorder.poll(generation=1)
        self.assertFalse(snapshot['recording'])
        self.assertTrue(snapshot['result']['completed'])
        self.assertEqual('video_duration_limit', snapshot['result']['stop_reason'])
        self.assertLessEqual(snapshot['result']['duration'], 1)

    def test_byte_limit_saves_only_last_bounded_complete_frame(self):
        first_size = len(synthetic_avi_bytes(width=16, height=16, fps=2, frames=1))
        recorder = VideoRecorder(self.broker, store=self.store, clock=self.clock,
                                 config=VideoConfig(max_bytes=max(1024, first_size)))
        self.addCleanup(recorder.close)
        self.start(recorder=recorder)
        self.clock.advance()
        snapshot = recorder.poll(generation=1)
        self.assertFalse(snapshot['recording'])
        self.assertEqual('video_byte_limit', snapshot['result']['stop_reason'])
        self.assertTrue(snapshot['result']['completed'])
        self.assertLessEqual(snapshot['result']['bytes'], recorder.config.max_bytes)

    def test_disk_threshold_blocks_before_camera_open(self):
        recorder = VideoRecorder(self.broker, store=VideoStore(self.root, free_bytes=lambda fd: 10),
                                 clock=self.clock)
        self.addCleanup(recorder.close)
        self.assertEqual('video_disk_full', self.start(recorder=recorder).code)
        self.assertEqual(0, self.backend.open_count)
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_disk_exhaustion_at_publish_fails_and_preserves_existing(self):
        space = [100 * 1024 * 1024]
        recorder = VideoRecorder(self.broker, store=VideoStore(self.root, free_bytes=lambda fd: space[0]),
                                 clock=self.clock)
        self.addCleanup(recorder.close)
        self.start(recorder=recorder)
        space[0] = 0
        result = recorder.stop_video_recording('record-1', generation=1)
        self.assertEqual('video_disk_full', result.code)
        self.assertFalse(result.completed)
        self.assertFalse(self.broker.opened)
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_cancel_never_publishes_and_duplicate_never_recaptures(self):
        self.start()
        result = self.recorder.cancel()
        self.assertEqual(('cancelled', 'video_cancelled'), (result.status, result.code))
        self.assertFalse(list(self.root.glob('*.avi')))
        self.assertEqual(result, self.start())
        self.assertEqual(1, self.backend.open_count)
        self.assertFalse(self.broker.opened)

    def test_mode_generation_change_cancels_before_next_frame(self):
        self.start()
        self.clock.advance()
        snapshot = self.recorder.poll(generation=2)
        self.assertFalse(snapshot['recording'])
        self.assertEqual('stale_video_generation', snapshot['result']['code'])
        self.assertFalse(list(self.root.glob('*.avi')))
        self.assertEqual(1, self.backend.read_count)

    def test_stale_stop_rejected_without_finishing_current_recording(self):
        self.start()
        self.assert_code('stale_video_generation', lambda: self.stop(generation=0))
        self.assertTrue(self.recorder.snapshot()['recording'])
        self.assertTrue(self.stop().completed)

    def test_prior_generation_cannot_restart_new_session(self):
        self.start(generation=3)
        self.stop(generation=3)
        self.assertEqual('stale_video_generation', self.start('second', generation=2).code)
        self.assertFalse(self.broker.opened)

    def test_shutdown_cancels_active_session_and_does_not_delete_old_files(self):
        self.start()
        saved = self.stop()
        self.start('record-2', generation=2)
        self.assertTrue(self.recorder.close())
        self.assertEqual('video_app_exit', self.recorder.snapshot()['result']['code'])
        self.assertEqual('video_closed', self.start('third', generation=3).code)
        self.assertTrue((self.root / (saved.video_id + '.avi')).is_file())
        self.assertFalse(self.broker.opened)

    def test_clock_regression_fails_closed(self):
        self.start()
        self.clock.advance(-1)
        snapshot = self.recorder.poll(generation=1)
        self.assertEqual('video_clock_invalid', snapshot['result']['code'])
        self.assertFalse(snapshot['recording'])

    def test_invalid_clock_at_first_frame_fails_and_cleans_reservation(self):
        calls = [10., float('nan')]
        recorder = VideoRecorder(self.broker, store=self.store, clock=lambda: calls.pop(0))
        self.addCleanup(recorder.close)
        result = self.start(recorder=recorder)
        self.assertEqual(('failed', 'video_clock_invalid'), (result.status, result.code))
        self.assertFalse(self.broker.opened)
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_source_failure_is_sanitized_and_cleans_lease(self):
        self.start()
        self.clock.advance()
        with patch.object(self.backend, 'read_frame', side_effect=RuntimeError('secret-ish private path')):
            snapshot = self.recorder.poll(generation=1)
        self.assertEqual('video_capture_failed', snapshot['result']['code'])
        self.assertNotIn('secret-ish', str(snapshot))
        self.assertFalse(self.broker.opened)
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_stale_frame_fails(self):
        self.start()
        old = self.backend._frame()
        self.clock.advance(3)
        with patch.object(self.backend, 'read_frame', return_value=old):
            snapshot = self.recorder.poll(generation=1)
        self.assertEqual('stale_video_frame', snapshot['result']['code'])
        self.assertFalse(snapshot['recording'])

    def test_close_failure_is_unknown_and_does_not_allow_new_owner(self):
        self.start()
        with patch.object(self.backend, 'close', return_value=False):
            result = self.stop()
        self.assertEqual(('unknown', 'video_close_failed'), (result.status, result.code))
        self.assertEqual('UNVERIFIED', self.recorder.snapshot()['readiness'])
        self.assertEqual('video_closed', self.start('second', generation=2).code)
        self.assertFalse(self.recorder.close())
        self.assert_code('camera_busy', lambda: self.broker.open())

    def test_failed_first_capture_and_failed_close_keep_unknown_owned_lease(self):
        with patch.object(self.backend, 'read_frame', side_effect=RuntimeError('capture failed')), \
                patch.object(self.backend, 'close', return_value=False):
            result = self.start()
        self.assertEqual(('unknown', 'video_close_failed'), (result.status, result.code))
        self.assertEqual('UNVERIFIED', self.recorder.snapshot()['readiness'])
        self.assertFalse(self.recorder.close())
        self.assert_code('camera_busy', lambda: self.broker.open())
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_failed_later_capture_and_failed_close_cannot_be_reopened(self):
        self.start()
        self.clock.advance()
        with patch.object(self.backend, 'read_frame', side_effect=RuntimeError('capture failed')), \
                patch.object(self.backend, 'close', return_value=False):
            snapshot = self.recorder.poll(generation=1)
        self.assertEqual(('unknown', 'video_close_failed'),
                         (snapshot['result']['status'], snapshot['result']['code']))
        self.assertEqual('UNVERIFIED', snapshot['readiness'])
        self.assertFalse(self.recorder.close())
        self.assert_code('camera_busy', lambda: self.broker.open())

    def test_failed_open_and_failed_close_keep_unknown_owned_lease(self):
        with patch.object(self.backend, 'open', side_effect=RuntimeError('open failed')), \
                patch.object(self.backend, 'close', return_value=False):
            result = self.start()
        self.assertEqual(('unknown', 'video_close_failed'), (result.status, result.code))
        self.assertFalse(self.recorder.close())
        self.assert_code('camera_busy', lambda: self.broker.open())

    def test_power_loss_metadata_is_unknown_not_automatic_retry(self):
        self.assertIsNone(self.store.begin('interrupted', config=VideoConfig()))
        self.store.close()  # Process exit drops lock but cannot prove completion.
        recorder = VideoRecorder(self.broker, store=VideoStore(self.root), clock=self.clock)
        self.addCleanup(recorder.close)
        result = self.start('interrupted', recorder=recorder)
        self.assertEqual(('unknown', 'video_interrupted'), (result.status, result.code))
        self.assertEqual(0, self.backend.open_count)
        self.assertFalse(list(self.root.glob('*.avi')))

    def test_lease_blocks_other_store_instance(self):
        self.start()
        other = VideoStore(self.root)
        self.addCleanup(other.close)
        result = other.begin('second', config=VideoConfig())
        self.assertEqual(('running', 'video_busy'), (result.status, result.code))
        self.assertTrue(self.stop().completed)

    def test_foreign_final_file_is_not_overwritten(self):
        self.start()
        video_id = 'video_' + hashlib.sha256(b'record-1').hexdigest()[:32]
        destination = self.root / (video_id + '.avi')
        destination.write_bytes(b'user owned existing file')
        result = self.stop()
        self.assertEqual('video_destination_exists', result.code)
        self.assertEqual(b'user owned existing file', destination.read_bytes())
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_journal_failure_after_publish_retains_completed_artifact(self):
        self.start()
        with patch.object(self.store, '_write', side_effect=OSError('private failure')):
            result = self.stop()
        self.assertTrue(result.completed)
        self.assertEqual('video_saved_journal_pending', result.code)
        self.assertTrue(self.store.read(result.video_id))
        recorder = VideoRecorder(self.broker, store=VideoStore(self.root), clock=self.clock)
        self.addCleanup(recorder.close)
        self.assertEqual('unknown', self.start(recorder=recorder).status)
        self.assertEqual(1, self.backend.open_count)

    def test_original_camera_preview_and_photo_are_excluded_by_video_claim(self):
        camera = CameraFeature(backend_factory=lambda: self.backend, clock=self.clock)
        recorder = VideoRecorder(camera.broker, store=self.store, clock=self.clock)
        self.addCleanup(recorder.close)
        self.start(recorder=recorder)
        self.assert_code('camera_busy', lambda: camera.broker.read_frame())
        self.assert_code('camera_busy', lambda: camera.broker.capture_photo())
        camera.off()  # It cannot close the owned active video stream.
        self.assertTrue(camera.broker.opened)
        self.assertTrue(recorder.stop_video_recording('record-1', generation=1).completed)
        self.assertFalse(camera.broker.opened)
        camera.set_preview(True, generation=2)
        self.assertTrue(camera.snapshot()['preview'])
        camera.close()

    def test_active_preview_blocks_video_without_disturbing_preview(self):
        self.broker.open()
        result = self.start()
        self.assertEqual('camera_busy', result.code)
        self.assertTrue(self.broker.opened)
        self.assertFalse(self.root.exists())
        self.broker.close()

    def test_broker_claim_rejects_inflight_open(self):
        entered, release = Event(), Event()
        class SlowDemo(DemoBackend):
            def open(inner, generation):
                entered.set()
                release.wait(2)
                super().open(generation)
        broker = CameraBroker(lambda: SlowDemo(clock=self.clock))
        thread = Thread(target=broker.open)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assert_code('camera_busy', lambda: broker.claim(object()))
        finally:
            release.set()
            thread.join(2)
            broker.close()
        self.assertFalse(thread.is_alive())

    def test_no_secret_no_path_or_frame_bytes_in_public_contract(self):
        self.start()
        snapshot = self.recorder.snapshot()
        self.assertEqual(CONTRACT, snapshot['contract'])
        result = self.stop()
        public = json.dumps({'snapshot': snapshot, 'result': result.public()})
        self.assertNotIn(str(self.root), public)
        self.assertNotIn('payload', public)
        self.assertNotIn('PNG', public)
        self.assertFalse(result.executed)
        self.assertEqual(0, result.audio_streams)

    def test_config_and_requests_are_strict(self):
        for kwargs in [dict(max_duration=float('nan')), dict(max_duration=0), dict(max_duration=True),
                       dict(max_duration=.5, fps=1), dict(max_bytes=1), dict(max_bytes=True),
                       dict(min_free_bytes=-1), dict(fps=100), dict(fps=True)]:
            with self.subTest(kwargs=kwargs):
                self.assert_code('invalid_video_config', lambda: VideoConfig(**kwargs))
        for request, generation in [('../path', 1), ('test', True), ('test', -1)]:
            self.assert_code('invalid_video_request', lambda: self.start(request, generation))
        self.assertFalse(self.root.exists())

    def test_result_cannot_claim_real_execution(self):
        self.assert_code('invalid_video_result', lambda: VideoResult('r', 'simulated', 'ok', executed=True))
        self.assert_code('invalid_video_result', lambda: VideoResult('r', 'simulated', 'ok', simulated=False))
        self.assert_code('invalid_video_result', lambda: VideoResult('r', 'simulated', 'ok', audio_streams=1))

    def test_unsafe_root_traversal_and_symlink_are_rejected(self):
        for root in [str(self.root / '..' / 'escape'), str(Path(self.tmp.name) / 'linked')]:
            if root.endswith('linked'):
                Path(root).symlink_to(self.tmp.name)
            recorder = VideoRecorder(self.broker, store=VideoStore(root), clock=self.clock)
            self.addCleanup(recorder.close)
            with self.subTest(root=root):
                self.assertEqual('unsafe_video_root', self.start(recorder=recorder).code)
        self.assertEqual(0, self.backend.open_count)

    def test_wrong_root_mode_is_not_chmodded(self):
        self.root.mkdir(mode=0o750)
        self.assertEqual('unsafe_video_root', self.start().code)
        self.assertEqual(0o750, stat.S_IMODE(self.root.stat().st_mode))
        self.assertEqual(0, self.backend.open_count)

    def test_metadata_symlink_fifo_and_hardlink_are_rejected(self):
        for kind in ['symlink', 'fifo', 'hardlink']:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as name:
                directory = Path(name) / 'video'
                directory.mkdir(mode=0o700)
                path = directory / '.video-journal.json'
                if kind == 'symlink':
                    target = Path(name) / 'existing'
                    target.write_text('private')
                    path.symlink_to(target)
                elif kind == 'fifo':
                    os.mkfifo(path, 0o600)
                else:
                    target = Path(name) / 'existing'
                    target.write_text('private')
                    target.chmod(0o600)
                    os.link(target, path)
                recorder = VideoRecorder(self.broker, store=VideoStore(directory), clock=self.clock)
                self.addCleanup(recorder.close)
                self.assertIn(self.start(recorder=recorder).code, ['video_storage_failed', 'unsafe_video_file'])
                self.assertEqual(0, self.backend.open_count)
                if kind != 'fifo': self.assertEqual('private', target.read_text())

    def test_metadata_duplicate_json_fields_rejected(self):
        self.root.mkdir(mode=0o700)
        path = self.root / '.video-journal.json'
        path.write_text('{"contract":"bmo_camera_video_v1","records":{},"records":{}}')
        path.chmod(0o600)
        self.assertEqual('video_journal_invalid', self.start().code)
        self.assertEqual(0, self.backend.open_count)

    def test_storage_read_rejects_traversal_and_unsafe_media(self):
        self.assert_code('invalid_video_id', lambda: self.store.read('../private'))
        self.start()
        result = self.stop()
        path = self.root / (result.video_id + '.avi')
        path.chmod(0o640)
        self.assert_code('unsafe_video_file', lambda: self.store.read(result.video_id))

    def test_cancellation_between_staging_and_publish_has_no_final_file(self):
        self.assertIsNone(self.store.begin('cancel-stage', config=VideoConfig()))
        def cancelled(): raise FeatureError('video_cancelled')
        self.assert_code('video_cancelled', lambda: self.store.publish('cancel-stage',
            synthetic_avi_bytes(), config=VideoConfig(), stop_reason='user_stop', cancel_check=cancelled))
        self.store.finish(VideoResult('cancel-stage', 'cancelled', 'video_cancelled'))
        self.assertFalse(list(self.root.glob('*.avi')))
        self.assertFalse(list(self.root.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
