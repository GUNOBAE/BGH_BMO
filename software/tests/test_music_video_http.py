"""Pinned music MP4 HTTP ranges under concurrent browser seeking.

The media, audio backend and face are synthetic; the threaded product server,
MediaFeature descriptor ownership and FeatureHost playback-token gate are real.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import os
from pathlib import Path
import struct
import tempfile
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from bmo_ai.features.camera.compressed_video import CompressedVideoRecorder, probe_compressed_video
from bmo_ai.features.host import FeatureHost
from bmo_ai.features.media import FakeMediaBackend, MediaFeature
from bmo_ai.features.media.assets import MediaAsset
from bmo_ai.features.server import FeatureServer
from test_compressed_video import CacheSource, wait_done
from test_feature_http import FakeFaceBridge, FakeHost


class MusicVideoHost(FakeHost):
    open_music_video = FeatureHost.open_music_video

    def __init__(self, media):
        super().__init__()
        self.media = media
        self.modes = SimpleNamespace(mode='music', generation=3)
        self._active_media_request = 'private_song_request'
        self.legacy_fallbacks = 0

    def video_bytes(self, _video_id):
        self.legacy_fallbacks += 1
        raise AssertionError('Music must never use the recorded/developer video fallback')


@unittest.skipUnless(probe_compressed_video()['available'], 'local ffmpeg/libx264/ffprobe required')
class MusicVideoHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='bmo-music-range-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        recorder = CompressedVideoRecorder(cls.root/'recorded', frame_source=CacheSource(), simulated=True)
        cls.addClassCleanup(recorder.close)
        recorder.start('synthetic_music_container', generation=1, consent=True, duration=.65)
        result = wait_done(recorder)
        if result.get('playable') is not True:
            raise AssertionError(result)
        original = recorder.read_video(result['video_id'])['body']
        # A valid ignorable MP4 free box gives HTTP readers several distinct
        # 64KiB chunks; a tiny synthetic clip alone may fit in one read.
        padding = b''.join(hashlib.sha256(str(index).encode()).digest() for index in range(20000))
        cls.payload = original + struct.pack('>I', len(padding)+8) + b'free' + padding
        cls.video = cls.root/'song.mp4'
        cls.video.write_bytes(cls.payload)
        cls.wav = cls.root/'song.wav'
        with wave.open(str(cls.wav), 'wb') as wav:
            wav.setparams((1, 2, 8000, 8000, 'NONE', 'not compressed'))
            wav.writeframes(bytes(16000))
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        cls.asset = MediaAsset('space_potatoes', 'Synthetic two-mode song', 'synthetic_test_only',
            cls.wav.name, sha(cls.wav), 'audio/wav', 1., video_path=cls.video.name,
            video_sha256=sha(cls.video), video_audio_path=cls.wav.name,
            video_audio_sha256=sha(cls.wav), video_audio_duration=1.)

    def setUp(self):
        self.media = MediaFeature(self.root, (self.asset,), backend=FakeMediaBackend(pausable=True))
        self.addCleanup(self.media.close)
        result = self.media.start('space_potatoes', request_id='private_song_request', mode_generation=3, variant='video')
        self.assertEqual(result.status, 'simulated')
        self.host = MusicVideoHost(self.media)
        with patch('bmo_ai.features.server.FaceServer', FakeFaceBridge):
            self.server = FeatureServer(self.host, 'synthetic-face').start()
        self.addCleanup(self.server.close)
        self.port = self.server._server.server_port
        self.path = self.current_path()

    def current_path(self):
        state = self.media.snapshot()
        return '/api/music-video/'+state['request_id']+'_'+str(state['playback_generation'])

    def request(self, method='GET', path=None, *, headers=()):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        try:
            connection.putrequest(method, self.path if path is None else path)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_concurrent_seek_ranges_and_head_return_exact_independent_bytes(self):
        """Two duplicated pinned FDs share one open-file description.

        Synchronize the first offset-dependent reads, if used, as two browser
        seek requests arriving together. Independent reads must still return
        their requested source bytes; no timing luck can make shared lseek safe.
        """
        inode = self.video.stat().st_ino
        real_read = os.read
        readers, lock, barrier = set(), Lock(), Barrier(2)

        def overlapping_read(fd, count):
            try:
                is_video = os.fstat(fd).st_ino == inode
            except OSError:
                is_video = False
            first = False
            if is_video:
                with lock:
                    identity = get_ident()
                    first = identity not in readers
                    readers.add(identity)
            if first:
                barrier.wait(timeout=3)
            return real_read(fd, count)

        cases = [('GET', 0, 98303), ('GET', 131072, 229375), ('HEAD', 262144, 360447)]
        with patch('bmo_ai.features.server.os.read', overlapping_read), ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.request, method, headers=[('Range', f'bytes={start}-{end}')])
                       for method, start, end in cases]
            results = [future.result(timeout=6) for future in futures]
        for (method, start, end), (status, headers, body) in zip(cases, results):
            with self.subTest(method=method, start=start):
                self.assertEqual(status, 206)
                self.assertEqual(headers['Content-Type'], 'video/mp4')
                self.assertEqual(headers['Content-Range'], f'bytes {start}-{end}/{len(self.payload)}')
                self.assertEqual(headers['Content-Length'], str(end-start+1))
                self.assertEqual(body, b'' if method == 'HEAD' else self.payload[start:end+1])
        self.assertEqual(self.host.legacy_fallbacks, 0)

    def test_seek_retires_old_url_and_back_cannot_fall_back_to_another_video(self):
        old = self.path
        self.media.pause()
        self.media.seek(.2)
        current = self.current_path()
        self.assertNotEqual(old, current)
        self.assertEqual(self.request(path=old)[0], 404)
        status, headers, body = self.request(path=current, headers=[('Range', 'bytes=-23')])
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[-23:])
        self.assertEqual(headers['Content-Length'], '23')
        self.media.stop('user')
        self.host.modes.mode = 'conversation'
        for method in ('GET', 'HEAD'):
            self.assertEqual(self.request(method, path=current)[0], 404)
        self.assertEqual(self.host.legacy_fallbacks, 0)

    def test_unrelated_origin_query_or_path_never_serves_private_bytes(self):
        for path in (self.path+'?download=1', self.path+'/extra', '/api/music-video/../song.mp4',
                     '/api/music-video/%2e%2e%2fsong.mp4'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 404)
        self.assertEqual(self.request(headers=[('Origin', 'https://unrelated.invalid')])[0], 403)
        self.assertEqual(self.host.legacy_fallbacks, 0)


if __name__ == '__main__':
    unittest.main()
