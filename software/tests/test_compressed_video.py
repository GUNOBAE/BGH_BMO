"""Real local codec IO with synthetic frames, never a camera or provider."""
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
import random
import stat
import struct
import subprocess
import tempfile
from threading import Event
import time
import unittest
import zlib
from unittest.mock import patch

from bmo_ai.features.camera.compressed_video import (
    CompressedVideoConfig, CompressedVideoRecorder, CompressedVideoStore,
    probe_compressed_video, _matroska_header, _matroska_packet,
)
from bmo_ai.features.camera.codec import png_bytes
from bmo_ai.features.camera.models import Frame, PreviewFrame
from bmo_ai.features.contracts import FeatureError
from test_feature_camera_adapters import jpeg_bytes


def wait_done(recorder, timeout=12.):
    deadline = time.monotonic() + timeout
    while recorder.snapshot()['worker_alive'] and time.monotonic() < deadline:
        time.sleep(.01)
    if recorder.snapshot()['worker_alive']:
        raise AssertionError('bounded recorder worker did not finish')
    return recorder.snapshot()['result']


class CacheSource:
    def __init__(self, *, gap=False):
        self.payload = jpeg_bytes(640, 360)
        self.started = time.monotonic()
        self.gap = gap
        self.last = None

    def __call__(self):
        now = time.monotonic()
        elapsed = now - self.started
        if self.gap and .25 < elapsed < .60:
            return self.last
        seq = int(elapsed * 20)
        self.last = PreviewFrame(1, seq, self.started + seq / 20, 640, 360,
                                 self.payload, sensor_timestamp=(seq + 1) * 50_000_000)
        return self.last


class CompressedContractTests(unittest.TestCase):
    def test_constructor_and_missing_consent_do_not_spawn_or_create_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch('subprocess.Popen') as spawn, patch('subprocess.run') as run:
            root = Path(tmp) / 'not_created'
            recorder = CompressedVideoRecorder(root)
            result = recorder.start('denied', generation=1)
            self.assertEqual(result['code'], 'video_consent_required')
            self.assertFalse(root.exists())
            spawn.assert_not_called()
            run.assert_not_called()
            recorder.close()

    def test_config_supports_sixty_seconds_but_rejects_unbounded_values(self):
        self.assertEqual(CompressedVideoConfig().max_duration, 60)
        self.assertEqual(CompressedVideoConfig().max_frames, 1201)
        for values in ({'max_duration': 61}, {'fps': 60}, {'max_bytes': 2 ** 31},
                       {'size': (641, 360)}, {'finalize_timeout': 100}, {'max_source_age': float('nan')}):
            with self.subTest(values=values), self.assertRaises(FeatureError):
                CompressedVideoConfig(**values)
        with tempfile.TemporaryDirectory() as tmp:
            recorder = CompressedVideoRecorder(Path(tmp) / 'clips')
            for duration in (0, 61, -1):
                self.assertEqual(recorder.start('outside', generation=1, consent=True, duration=duration)['code'],
                                 'video_duration_out_of_range')
            self.assertFalse((Path(tmp) / 'clips').exists())
            recorder.close()

    def test_start_is_async_and_cancel_during_probe_does_not_encode(self):
        entered, release = Event(), Event()
        def probe():
            entered.set()
            release.wait(2)
            return dict(available=True, code='video_encoder_ready')
        with tempfile.TemporaryDirectory() as tmp, patch('bmo_ai.features.camera.compressed_video.probe_compressed_video', probe), patch('subprocess.Popen') as spawn:
            recorder = CompressedVideoRecorder(Path(tmp) / 'clips', simulated=True)
            began = time.monotonic()
            result = recorder.start('slow_probe', generation=1, consent=True)
            self.assertLess(time.monotonic() - began, .1)
            self.assertEqual(result['code'], 'video_starting')
            self.assertTrue(entered.wait(1))
            self.assertEqual(recorder.snapshot()['phase'], 'starting')
            recorder.cancel()
            release.set()
            result = wait_done(recorder)
            self.assertEqual(result['status'], 'cancelled')
            self.assertFalse(result['completed'])
            self.assertFalse(result['playable'])
            spawn.assert_not_called()
            recorder.close()

    def test_missing_encoder_degrades_this_capability_without_storage(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bmo_ai.features.camera.compressed_video._tool', return_value=None):
            root = Path(tmp) / 'clips'
            recorder = CompressedVideoRecorder(root)
            self.assertFalse(recorder.capability()['available'])
            recorder.start('missing', generation=1, consent=True)
            result = wait_done(recorder)
            self.assertEqual(result['code'], 'video_encoder_missing')
            self.assertFalse(root.exists())
            recorder.close()

    def test_symlink_root_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'target').mkdir(mode=0o700)
            (root / 'link').symlink_to(root / 'target', target_is_directory=True)
            store = CompressedVideoStore(root / 'link')
            with self.assertRaises(FeatureError):
                store.begin('escape', 'a' * 64, {})
            self.assertEqual(list((root / 'target').iterdir()), [])

    def test_private_disk_budget_prevents_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CompressedVideoStore(Path(tmp) / 'clips')
            with patch.object(store, 'budget', side_effect=FeatureError('video_disk_full')):
                with self.assertRaisesRegex(FeatureError, 'could not be completed'):
                    store.begin('full', 'a' * 64, {})
            self.assertIsNone(store._root)
            self.assertFalse(any((Path(tmp) / 'clips').glob('*.mp4*')))

    def test_recovered_running_request_is_unknown_and_never_encoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'clips'
            recorder = CompressedVideoRecorder(root, simulated=True)
            running = recorder._base('power_loss', 'running', 'video_starting')
            fingerprint = hashlib.sha256(json.dumps({'config': asdict(recorder.config),
                'duration': 30., 'simulated': True}, sort_keys=True).encode()).hexdigest()
            self.assertIsNone(recorder.store.begin('power_loss', fingerprint, running))
            recorder.store.close()
            with patch('bmo_ai.features.camera.compressed_video.probe_compressed_video',
                       return_value=dict(available=True, code='video_encoder_ready')), patch.object(recorder, '_spawn') as spawn:
                recorder.start('power_loss', generation=2, consent=True)
                result = wait_done(recorder)
                self.assertEqual(result['status'], 'unknown')
                self.assertEqual(result['code'], 'video_interrupted')
                self.assertFalse(result['playable'])
                spawn.assert_not_called()
            recorder.close()

    def test_unsafe_or_duplicate_journal_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'clips'
            root.mkdir(mode=0o700)
            journal = root / '.compressed-video-journal.json'
            for raw in (b'{"contract":"a","contract":"b","records":{}}',
                        b'{"contract":"bmo_camera_compressed_video_v1","records":{"x":{"fingerprint":"a","result":NaN}}}'):
                journal.write_bytes(raw)
                journal.chmod(0o600)
                store = CompressedVideoStore(root)
                with self.assertRaises(FeatureError):
                    store.begin('fresh', 'a' * 64, {})
                self.assertIsNone(store._root)


@unittest.skipUnless(probe_compressed_video()['available'], 'installed local ffmpeg/libx264/ffprobe required')
class CompressedCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name) / 'clips'
        cls.recorder = CompressedVideoRecorder(cls.root, frame_source=CacheSource(gap=True), simulated=True)
        cls.recorder.start('synthetic_gap', generation=3, consent=True, duration=1.2)
        cls.result = wait_done(cls.recorder)
        if not cls.result.get('playable'):
            raise AssertionError(cls.result)

    @classmethod
    def tearDownClass(cls):
        cls.recorder.close()
        cls.directory.cleanup()

    def test_actual_h264_mp4_full_decode_and_private_publish(self):
        result = self.result
        self.assertEqual(result['status'], 'simulated')
        self.assertFalse(result['executed'])
        self.assertTrue(result['completed'])
        self.assertEqual(result['audio_streams'], 0)
        self.assertEqual(result['codec'], 'h264')
        self.assertGreater(result['bytes'], 0)
        self.assertGreater(result['duration'], .6)
        self.assertLessEqual(result['duration'], 1.35)
        file = self.root / (result['video_id'] + '.mp4')
        raw = file.read_bytes()
        self.assertIn(b'ftyp', raw[:32])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result['sha256'])
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertFalse(list(self.root.glob('*.partial')))

    def test_source_timestamps_drop_counts_and_container_timing_are_distinct(self):
        metrics = self.result['metrics']
        self.assertEqual(metrics['physical_fps'], 'NOT_MEASURED')
        self.assertEqual(metrics['queue_capacity'], 1)
        self.assertGreater(metrics['source_duplicates'], 0)
        self.assertGreater(metrics['source_gaps'], 0)
        self.assertLess(metrics['source_frames'], 24)
        self.assertEqual(metrics['frames_written'], metrics['source_frames'])
        self.assertEqual(metrics['encoder_frames'], metrics['source_frames'])
        self.assertGreater(metrics['source_span'], .6)
        # No constant20fps packing of the remaining frames to a shortened clip.
        self.assertGreater(metrics['encoded_duration'], metrics['source_frames'] / 20)
        self.assertNotEqual(metrics['source_timestamp_basis'], metrics['encoder_timestamp_basis'])

    def test_ranges_read_the_saved_mp4_not_a_preview(self):
        full = self.recorder.read_video(self.result['video_id'])
        self.assertEqual(full['mime'], 'video/mp4')
        self.assertEqual(full['status'], 200)
        first = self.recorder.read_video(self.result['video_id'], 'bytes=0-31')
        self.assertEqual(first['status'], 206)
        self.assertEqual(first['body'], full['body'][:32])
        self.assertEqual(first['total'], len(full['body']))
        self.assertEqual(self.recorder.read_video(self.result['video_id'], 'bytes=-13')['body'], full['body'][-13:])
        self.assertEqual(self.recorder.read_video(self.result['video_id'], 'bytes=20-')['body'], full['body'][20:])
        for value in ('bytes=', 'bytes=1-2,4-5', 'bytes=-0', 'bytes=99-5', f'bytes={len(full["body"])}-'):
            with self.subTest(value=value), self.assertRaises(FeatureError):
                self.recorder.read_video(self.result['video_id'], value)

    def test_old_request_is_never_encoded_again(self):
        recorder = CompressedVideoRecorder(self.root, frame_source=CacheSource(), simulated=True)
        with patch.object(recorder, '_spawn', side_effect=AssertionError('duplicate encode')):
            recorder.start('synthetic_gap', generation=4, consent=True, duration=1.2)
            result = wait_done(recorder)
            self.assertEqual(result['sha256'], self.result['sha256'])
        recorder.close()

    def test_generation_cancel_and_app_exit_preserve_playable_interrupted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = CompressedVideoRecorder(Path(tmp) / 'clips', frame_source=CacheSource(), simulated=True)
            recorder.start('interrupted', generation=1, consent=True)
            deadline = time.monotonic() + 3
            while recorder.snapshot()['metrics'].get('frames_written', 0) < 5 and time.monotonic() < deadline:
                time.sleep(.01)
            recorder.poll(generation=2)
            result = wait_done(recorder)
            self.assertEqual(result['status'], 'cancelled')
            self.assertEqual(result['code'], 'video_interrupted')
            self.assertTrue(result['interrupted'])
            self.assertTrue(result['playable'])
            self.assertEqual(result['stop_reason'], 'stale_video_generation')
            self.assertIn(b'ftyp', recorder.read_video(result['video_id'])['body'][:32])
            self.assertFalse(recorder.snapshot()['worker_alive'])
            recorder.close()

    def test_duplicate_and_stale_sources_do_not_fabricate_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            stamp = time.monotonic()
            frame = PreviewFrame(1, 1, stamp, 640, 360, jpeg_bytes(640, 360), sensor_timestamp=1)
            recorder = CompressedVideoRecorder(Path(tmp) / 'clips', frame_source=lambda: frame, simulated=True)
            recorder.start('frozen', generation=1, consent=True, duration=.7)
            result = wait_done(recorder)
            self.assertEqual(recorder.snapshot()['metrics']['frames_written'], 1)
            self.assertLessEqual(result.get('metrics', {}).get('encoder_frames', 1), 1)
            recorder.close()

    def test_saved_content_tamper_is_rejected_before_http_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = CompressedVideoRecorder(Path(tmp) / 'clips', frame_source=CacheSource(), simulated=True)
            recorder.start('tampered', generation=1, consent=True, duration=.5)
            result = wait_done(recorder)
            self.assertTrue(result['playable'])
            path = Path(tmp) / 'clips' / (result['video_id'] + '.mp4')
            raw = path.read_bytes()
            path.write_bytes(raw[:-1] + bytes((raw[-1] ^ 1,)))
            with self.assertRaises(FeatureError):
                recorder.read_video(result['video_id'])
            for bad in ('../file', result['video_id'] + '.mp4', 'video_' + '0' * 31):
                with self.assertRaises(FeatureError):
                    recorder.read_video(bad)
            recorder.close()

    def test_source_pts_survive_instant_startup_burst_and_irregular_gaps(self):
        # Feed all frames immediately: encoder startup/pipe buffering must not
        # turn .25s of source video into .06s, or erase a real capture gap.
        for fmt in ('jpeg', 'png'):
            payload = jpeg_bytes(640, 360) if fmt == 'jpeg' else png_bytes(640, 360)
            frame = PreviewFrame(1, 1, 10, 640, 360, payload, format=fmt)
            for timestamps in ([0., .05, .10, .15, .20], [0., .05, .40, .45, .50]):
                with self.subTest(fmt=fmt, timestamps=timestamps), tempfile.TemporaryDirectory() as tmp:
                    recorder = CompressedVideoRecorder(Path(tmp) / 'clips', simulated=True)
                    recorder.store.begin('instant', 'a' * 64, recorder._base('instant', 'running', 'video_starting'))
                    recorder._metrics = dict(frames_written=len(timestamps))
                    process = thread = None
                    try:
                        with patch('bmo_ai.features.camera.compressed_video.subprocess.Popen', wraps=subprocess.Popen) as spawn:
                            process, thread = recorder._spawn(frame)
                        command = spawn.call_args.args[0]
                        self.assertEqual(command[command.index('-f') + 1], 'matroska')
                        self.assertNotIn('-use_wallclock_as_timestamps', command)
                        self.assertIn('-an', command)
                        self.assertFalse(spawn.call_args.kwargs.get('shell', False))
                        recorder._write_frame(process, _matroska_header(frame, 20))
                        for stamp in timestamps:
                            recorder._write_frame(process, _matroska_packet(payload, stamp))
                        process.stdin.close()
                        self.assertEqual(process.wait(timeout=5), 0)
                        meta = recorder._probe(recorder.store._output)
                        self.assertEqual(meta['frames'], len(timestamps))
                        self.assertAlmostEqual(meta['duration'], timestamps[-1] + .05, delta=.003)
                        # Decode metadata validates individual packet PTS too,
                        # rather than accepting a corrected header duration.
                        fd = recorder.store._output
                        probe = subprocess.run(['/usr/bin/ffprobe', '-v', 'error', '-show_entries',
                            'packet=pts_time', '-of', 'json', f'/proc/self/fd/{fd}'],
                            pass_fds=(fd,), capture_output=True, check=True, timeout=5)
                        pts = [float(x['pts_time']) for x in json.loads(probe.stdout)['packets']]
                        self.assertEqual(pts, timestamps)
                    finally:
                        if process is not None:
                            if process.poll() is None:
                                process.kill()
                                process.wait(timeout=2)
                            if not process.stdin.closed:
                                process.stdin.close()
                            thread.join(1)
                            process.stderr.close()
                        recorder.store.close()

    def test_encoder_cannot_exceed_absolute_private_file_byte_limit(self):
        # Incompressible synthetic pixels force the first packet over a tiny
        # test ceiling. The partial artifact remains bounded and is not Saved.
        def chunk(kind, body):
            return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))
        randomizer = random.Random(91)
        rows = b''.join(b'\0' + randomizer.randbytes(640 * 3) for _ in range(360))
        payload = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 640, 360, 8, 2, 0, 0, 0))
                   + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
        with tempfile.TemporaryDirectory() as tmp:
            config = CompressedVideoConfig(max_bytes=64 * 1024)
            def source():
                now = time.monotonic()
                return Frame(1, int(now * 20), now, 640, 360, payload)
            recorder = CompressedVideoRecorder(Path(tmp) / 'clips', config=config,
                frame_source=source, simulated=True)
            recorder.start('byte_ceiling', generation=1, consent=True, duration=.8)
            result = wait_done(recorder)
            self.assertFalse(result['completed'])
            self.assertFalse(result['playable'])
            self.assertIn(result['code'], ('video_byte_limit', 'video_verification_failed'))
            self.assertFalse(list((Path(tmp) / 'clips').glob('video_*.mp4')))
            for partial in (Path(tmp) / 'clips').glob('*.partial'):
                self.assertLessEqual(partial.stat().st_size, config.max_bytes)
            self.assertFalse(recorder.snapshot()['worker_alive'])
            recorder.close()


if __name__ == '__main__':
    unittest.main()
