"""실제 장치 없이 SDK 경계·실제 픽셀 인코딩·입회 gate를 검증한다."""
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

from bmo_ai.features.camera import png_bytes
from bmo_ai.features.camera.live import (
    LiveCameraOptions, LivePicamera2Backend, build_live_camera, probe_camera_capability,
)
from bmo_ai.features.camera.live_codec import png_to_dib, rgb_avi_bytes, validate_rgb_avi
from bmo_ai.features.camera.live_video import LiveVideoRecorder, LiveVideoStore
from bmo_ai.features.camera.video import VideoConfig, VideoRecorder
from bmo_ai.features.contracts import FeatureError
from test_feature_camera_adapters import Clock, FakeCamera


class LiveCameraFixture:
    def setUp(self):
        self.clock = Clock()
        self.sdk = FakeCamera(self.clock)
        self.sdk.advance_clock = True
        self.factory = Mock(return_value=self.sdk)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, **kwargs):
        camera = build_live_camera(LiveCameraOptions(enabled=True, supervised=True), self.root / 'photos',
            clock=self.clock, camera_factory=self.factory, wait_clock=self.clock, **kwargs)
        self.addCleanup(camera.close)
        return camera


class LiveCameraTests(LiveCameraFixture, unittest.TestCase):

    def test_capability_probe_searches_modules_without_importing_or_constructing(self):
        finder = Mock(return_value=object())
        with patch('builtins.__import__', side_effect=AssertionError('native import forbidden')):
            result = probe_camera_capability(finder=finder)
        self.assertTrue(result['available'])
        self.assertFalse(result['opened'])
        self.assertFalse(result['physical_tested'])
        self.assertEqual([call.args[0] for call in finder.call_args_list], ['picamera2', 'libcamera', 'cv2', 'numpy'])

    def test_unavailable_probe_returns_fixed_code_not_import_exception(self):
        result = probe_camera_capability(finder=Mock(side_effect=ImportError('private path')))
        self.assertFalse(result['available'])
        self.assertEqual(result['code'], 'camera_backend_unavailable')
        self.assertNotIn('private', repr(result))

    def test_two_explicit_flags_required_before_factory_or_storage(self):
        for options in (LiveCameraOptions(), LiveCameraOptions(enabled=True), LiveCameraOptions(supervised=True)):
            with self.subTest(options=options), self.assertRaises(FeatureError):
                build_live_camera(options, self.root / 'photos', camera_factory=self.factory)
        self.factory.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_constructor_is_lazy_and_marks_broker_real_before_open(self):
        camera = self.build()
        self.factory.assert_not_called()
        self.assertFalse(camera.broker.simulated)
        self.assertFalse(camera.snapshot()['on'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_supervised_photo_uses_sdk_and_private_atomic_store(self):
        camera = self.build()
        result = camera.take_photo('supervised-photo', generation=1)
        self.assertEqual(result.status, 'succeeded')
        self.assertFalse(result.simulated)
        self.assertTrue(result.executed)
        self.assertTrue(result.completed)
        self.assertEqual(os.stat(camera.store.root).st_mode & 0o777, 0o700)
        image = camera.store.root / (result.photo_id + '.png')
        self.assertEqual(os.stat(image).st_mode & 0o777, 0o600)
        self.assertEqual(camera.photo_bytes(result.photo_id), image.read_bytes())
        self.assertFalse(camera.broker.opened)
        self.assertEqual(self.sdk.calls.count('close'), 1)
        duplicate = camera.take_photo('supervised-photo', generation=1)
        self.assertEqual(duplicate, result)
        self.factory.assert_called_once()

    def test_disk_full_blocks_sensor_before_open(self):
        camera = self.build(free_bytes=lambda _: 0)
        result = camera.take_photo('full-photo', generation=1)
        self.assertEqual(result.code, 'photo_disk_full')
        self.assertFalse(result.executed)
        self.factory.assert_not_called()

    def test_symlink_storage_rejected_before_sensor_open(self):
        target = self.root / 'actual'
        target.mkdir(mode=0o700)
        (self.root / 'photos').symlink_to(target, target_is_directory=True)
        camera = self.build()
        result = camera.take_photo('symlink-photo', generation=1)
        self.assertFalse(result.completed)
        self.factory.assert_not_called()

    def test_timeout_reports_failed_and_releases_owned_sensor(self):
        camera = self.build()
        self.sdk.pending = True
        result = camera.take_photo('timeout-photo', generation=1)
        self.assertEqual(result.code, 'camera_timeout')
        self.assertFalse(result.completed)
        self.assertTrue(result.executed)
        self.assertFalse(camera.broker.opened)

    def test_sdk_close_failure_stays_unknown_and_blocks_other_live_factories(self):
        self.enterContext(patch.object(LivePicamera2Backend, '_close_uncertain', False))
        camera=self.build()
        self.sdk.fail='close'
        result=camera.take_photo('close-failed',generation=1)
        self.assertTrue(result.completed)  # 이미 게시한 사진은 지우지 않는다.
        self.assertEqual(camera.snapshot()['error'],'camera_close_failed')
        self.assertFalse(camera.broker.close())
        camera.close()
        self.assertFalse(camera.cleanup_snapshot()['resources_cleaned'])
        self.assertTrue(camera.cleanup_snapshot()['close_unknown'])
        self.sdk.fail=None
        other=self.build()
        retry=other.take_photo('must-not-reopen',generation=1)
        self.assertFalse(retry.completed)
        self.factory.assert_called_once()


class RealPixelCodecTests(unittest.TestCase):
    def test_actual_pixel_color_is_preserved_in_bgr_bottom_up_avi(self):
        width, height, dib = png_to_dib(png_bytes(2, 2, (10, 20, 30)))
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(dib, (bytes((30, 20, 10)) * 2 + b'\0\0') * 2)
        raw = rgb_avi_bytes([dib, dib], width=width, height=height, fps=2)
        result = validate_rgb_avi(raw)
        self.assertEqual((result['width'], result['height'], result['duration']), (2, 2, 1))
        self.assertEqual(result['codec'], 'uncompressed_dib_rgb')

    def test_all_five_png_filters_decode_nonuniform_real_pixels(self):
        width, height = 2, 2
        source_rows = [bytes((20, 50, 80, 100, 150, 200)), bytes((11, 99, 200, 240, 123, 77))]
        def chunk(kind, body):
            return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))
        for kind in range(5):
            raw = bytearray()
            previous = bytes(6)
            for row in source_rows:
                encoded = bytearray()
                for x, value in enumerate(row):
                    left, up, corner = row[x-3] if x >= 3 else 0, previous[x], previous[x-3] if x >= 3 else 0
                    base = left + up - corner
                    distances = (abs(base-left), abs(base-up), abs(base-corner))
                    prediction = (0, left, up, (left+up)//2, (left,up,corner)[distances.index(min(distances))])[kind]
                    encoded.append((value-prediction) & 255)
                raw.extend(bytes((kind,)) + encoded)
                previous = row
            payload = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))
            payload += chunk(b'IDAT',zlib.compress(raw)) + chunk(b'IEND',b'')
            with self.subTest(filter=kind):
                _, _, dib = png_to_dib(payload)
                expected = b''.join(bytes((row[2],row[1],row[0],row[5],row[4],row[3],0,0)) for row in reversed(source_rows))
                self.assertEqual(dib, expected)

    def test_large_photo_not_silently_downsampled_as_video(self):
        with self.assertRaises(FeatureError):
            png_to_dib(png_bytes(641, 2))

    def test_avi_truncation_header_and_index_corruption_rejected(self):
        width, height, dib = png_to_dib(png_bytes(32, 24))
        raw = rgb_avi_bytes([dib], width=width, height=height, fps=2)
        for damaged in (raw[:-1], raw[:32] + b'bad!' + raw[36:], raw[:-4] + b'bad!'):
            with self.subTest(size=len(damaged)), self.assertRaises(FeatureError):
                validate_rgb_avi(damaged)


class LiveVideoTests(LiveCameraFixture, unittest.TestCase):
    # 이 class의 setUp/build는 같은 SDK double을 재사용한다. 실제 장치가 아니다.
    def recorder(self, *, supervised=True, store=True):
        camera = self.build()
        recorder = LiveVideoRecorder(camera.broker, clock=self.clock, supervised=supervised,
            store=LiveVideoStore(self.root / 'videos') if store else None,
            config=VideoConfig(max_duration=2, fps=2))
        self.addCleanup(recorder.close)
        return camera, recorder

    def test_live_video_stays_off_without_supervision_and_per_recording_consent(self):
        _, recorder = self.recorder(supervised=False)
        self.assertEqual(recorder.start_video_recording('no-user',generation=1,consent=True).status,'rejected')
        self.factory.assert_not_called()
        recorder.supervised = True
        self.assertEqual(recorder.start_video_recording('no-consent',generation=1).code,'video_consent_required')
        self.factory.assert_not_called()

    def test_real_pixel_frames_recorded_private_and_retained_with_sha(self):
        camera, recorder = self.recorder()
        result = recorder.start_video_recording('live-video',generation=1,consent=True)
        self.assertEqual(result.status,'running')
        self.clock.now += .5
        recorder.poll(generation=1)
        result = recorder.stop_video_recording('live-video',generation=1)
        self.assertEqual(result.status,'succeeded')
        self.assertFalse(result.simulated)
        self.assertTrue(result.executed)
        self.assertTrue(result.completed)
        raw = recorder.store.read(result.video_id)
        meta = validate_rgb_avi(raw)
        self.assertEqual((meta['width'],meta['height'],meta['frames']),(640,360,2))
        self.assertFalse(camera.broker.opened)
        self.assertEqual((self.root/'videos'/(result.video_id+'.avi')).stat().st_mode&0o777,0o600)
        duplicate = recorder.start_video_recording('live-video',generation=1,consent=True)
        self.assertEqual(duplicate,result)
        self.factory.assert_called_once()

    def test_shared_broker_prevents_video_stealing_active_preview(self):
        camera, recorder = self.recorder()
        camera.set_preview(True,generation=1)
        result=recorder.start_video_recording('conflict',generation=1,consent=True)
        self.assertEqual(result.code,'camera_busy')
        self.assertTrue(camera.broker.opened)

    def test_synthetic_recorder_cannot_activate_live_backend(self):
        camera=self.build()
        recorder=VideoRecorder(camera.broker,simulated=True,clock=self.clock)
        self.addCleanup(recorder.close)
        self.assertEqual(recorder.start_video_recording('legacy',generation=1,consent=True).code,'camera_video_denied')
        self.factory.assert_not_called()

    def test_real_video_auto_duration_limit_and_close(self):
        _, recorder=self.recorder()
        recorder.start_video_recording('duration',generation=1,consent=True)
        self.clock.now+=2
        state=recorder.poll(generation=1)
        self.assertFalse(state['recording'])
        self.assertEqual(state['result']['stop_reason'],'video_duration_limit')
        self.assertEqual(state['result']['status'],'succeeded')

    def test_real_video_cancel_does_not_publish_partial_media(self):
        _, recorder=self.recorder()
        recorder.start_video_recording('cancel',generation=1,consent=True)
        result=recorder.cancel()
        self.assertEqual(result.status,'cancelled')
        self.assertTrue(result.executed)
        self.assertEqual(list((self.root/'videos').glob('*.avi')),[])

    def test_interrupted_journal_never_reopens_or_replays_camera(self):
        _, recorder=self.recorder()
        recorder.start_video_recording('interrupted',generation=1,consent=True)
        recorder._release_camera()
        recorder.store.close()
        # 전원 상실을 흉내 낸 새 객체. 기존 running journal은 unknown이어야 한다.
        recorder._request=None
        camera=self.build()
        new=LiveVideoRecorder(camera.broker,clock=self.clock,supervised=True,
            store=LiveVideoStore(self.root/'videos'),config=VideoConfig(max_duration=2,fps=2))
        self.addCleanup(new.close)
        result=new.start_video_recording('interrupted',generation=1,consent=True)
        self.assertEqual((result.status,result.code),('unknown','video_interrupted'))
        self.factory.assert_called_once()

    def test_video_close_failure_keeps_unknown_and_no_published_recording(self):
        self.enterContext(patch.object(LivePicamera2Backend,'_close_uncertain',False))
        _, recorder=self.recorder()
        recorder.start_video_recording('close-video',generation=1,consent=True)
        self.sdk.fail='close'
        result=recorder.stop_video_recording('close-video',generation=1)
        self.assertEqual((result.status,result.code),('unknown','video_close_failed'))
        self.assertTrue(result.executed)
        self.assertEqual(list((self.root/'videos').glob('*.avi')),[])


if __name__ == '__main__':
    unittest.main()
