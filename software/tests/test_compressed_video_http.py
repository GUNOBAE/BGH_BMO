"""Real MP4 bytes and Range responses over the product's loopback HTTP server."""
import hashlib
import http.client
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.camera.compressed_video import CompressedVideoRecorder, probe_compressed_video
from bmo_ai.features.server import FeatureServer
from test_compressed_video import CacheSource, wait_done
from test_feature_http import FakeFaceBridge, FakeHost


class SavedVideoHost(FakeHost):
    def __init__(self, recorder):
        super().__init__()
        self.recorder = recorder
        self.opened = []
        self.closed_descriptors = []
        self.legacy_fallbacks = []

    def open_video(self, video_id):
        self.opened.append(video_id)
        try:
            opened = self.recorder.open_video(video_id)
            self.closed_descriptors.append(opened[0])
            return opened
        except Exception:
            return None

    def video_bytes(self, video_id):
        self.legacy_fallbacks.append(video_id)
        return None


@unittest.skipUnless(probe_compressed_video()['available'], 'installed local ffmpeg/libx264/ffprobe required')
class CompressedVideoHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name) / 'clips'
        cls.recorder = CompressedVideoRecorder(cls.root, frame_source=CacheSource(), simulated=True)
        cls.addClassCleanup(cls.recorder.close)
        cls.recorder.start('http_saved_mp4', generation=1, consent=True, duration=.65)
        cls.result = wait_done(cls.recorder)
        if cls.result.get('playable') is not True:
            raise AssertionError(cls.result)
        cls.payload = cls.recorder.read_video(cls.result['video_id'])['body']
        cls.host = SavedVideoHost(cls.recorder)
        with patch('bmo_ai.features.server.FaceServer', FakeFaceBridge):
            cls.server = FeatureServer(cls.host, 'synthetic-face', port=0).start()
        cls.addClassCleanup(cls.server.close)
        cls.port = cls.server._server.server_port
        cls.path = '/api/video/' + cls.result['video_id']

    def request(self, method='GET', path=None, *, headers=()):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.putrequest(method, self.path if path is None else path)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_get_streams_verified_compressed_file_with_browser_headers(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(body, self.payload)
        self.assertEqual(hashlib.sha256(body).hexdigest(), self.result['sha256'])
        self.assertIn(b'ftyp', body[:32])
        self.assertEqual(headers['Content-Type'], 'video/mp4')
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(headers['Content-Length'], str(len(body)))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertEqual(headers['Content-Disposition'], f'inline; filename="{self.result["video_id"]}.mp4"')
        self.assertNotIn(str(self.root), str(headers))
        self.assertFalse(self.host.legacy_fallbacks)

    def test_partial_suffix_open_ended_and_clamped_ranges_match_saved_mp4(self):
        total = len(self.payload)
        cases = [('bytes=0-31', 0, 31), ('bytes=-17', total-17, total-1),
                 ('bytes=32-', 32, total-1), (f'bytes=0-{total+500}', 0, total-1)]
        for value, start, end in cases:
            with self.subTest(value=value):
                status, headers, body = self.request(headers=[('Range', value)])
                self.assertEqual(status, 206)
                self.assertEqual(body, self.payload[start:end+1])
                self.assertEqual(headers['Content-Range'], f'bytes {start}-{end}/{total}')
                self.assertEqual(headers['Content-Length'], str(end-start+1))

    def test_bad_unsatisfiable_and_duplicate_range_headers_are_416(self):
        total = len(self.payload)
        ranges = [('bytes=',), ('bytes=-0',), ('bytes=10-2',), (f'bytes={total}-',),
                  ('bytes=0-1,5-9',), ('items=0-5',), ('bytes=0-5', 'bytes=10-15')]
        for values in ranges:
            with self.subTest(values=values):
                status, headers, body = self.request(headers=[('Range', value) for value in values])
                self.assertEqual(status, 416)
                self.assertEqual(headers['Content-Range'], 'bytes */'+str(total))
                self.assertEqual(headers['Content-Length'], '0')
                self.assertEqual(body, b'')

    def test_head_keeps_range_metadata_without_body_and_closes_owned_descriptor(self):
        for headers, expected, length in (([], 200, len(self.payload)), ([('Range','bytes=0-31')], 206, 32)):
            status, response, body = self.request('HEAD', headers=headers)
            self.assertEqual(status, expected)
            self.assertEqual(response['Content-Length'], str(length))
            self.assertEqual(body, b'')
            descriptor = self.host.closed_descriptors[-1]
            # HEAD's headers can reach the client just before the server's
            # finally closes its owned FD; wait only for that bounded cleanup.
            deadline = time.monotonic() + .5
            while True:
                try:
                    os.fstat(descriptor)
                except OSError:
                    break
                if time.monotonic() >= deadline:
                    self.fail('HTTP handler did not close its owned video descriptor')
                time.sleep(.001)

    def test_invalid_path_query_and_origin_do_not_open_private_file(self):
        before = len(self.host.opened)
        invalid = [self.path+'?download=1', self.path+'.mp4', '/api/video/../secret',
                   '/api/video/video_'+('a'*31), '/api/video/%2e%2e%2fprivate']
        for path in invalid:
            with self.subTest(path=path):
                status, _, _ = self.request(path=path)
                self.assertEqual(status, 404)
        status, _, _ = self.request(headers=[('Origin', 'https://unrelated.invalid')])
        self.assertEqual(status, 403)
        self.assertEqual(len(self.host.opened), before)


if __name__ == '__main__':
    unittest.main()
