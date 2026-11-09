"""Prepared singing profiles through the actual Desktop/Host HTTP boundary.

All media are generated fixtures. Only the loopback server and silent media
backend run; no hardware, paid provider or original user file is accessed.
"""
import hashlib
import http.client
import json
import math
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave

from bmo_ai.completed_turn_diagnostics import CompletedTurnStore
from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient
from bmo_ai.features.media import FakeMediaBackend, MediaFeature
from bmo_ai.features.media.assets import MediaAsset
from bmo_ai.features.media.singing_analysis import prepare_profile
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.features.server import FeatureServer
from test_feature_http import FakeFaceBridge


class MusicSingingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-singing-http-')))
        self.content = self.root/'content'; self.content.mkdir(mode=0o700)
        wav_path = self.content/'generated.wav'
        with wave.open(str(wav_path), 'wb') as wav:
            wav.setparams((1, 2, 8000, 16000, 'NONE', 'not compressed'))
            wav.writeframes(b''.join(struct.pack('<h', int(9000*math.sin(index*.17)*
                (.2 if index//1600%2 else 1.))) for index in range(16000)))
        wav_path.chmod(0o600)
        # This suite never fetches/decodes video: its opaque private descriptor
        # exists only to exercise the real Face→Video authorization boundary.
        video = self.content/'opaque-fixture.mp4'; video.write_bytes(b'synthetic-no-decoder-input')
        video.chmod(0o600)
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.asset = MediaAsset('fixture_song', 'Generated test song', 'synthetic_test_only',
            wav_path.name, sha(wav_path), 'audio/wav', 2., video_path=video.name,
            video_sha256=sha(video), video_audio_path=wav_path.name,
            video_audio_sha256=sha(wav_path), video_audio_duration=2.)
        self.prepared = prepare_profile(self.content, self.asset)
        self.raw = (self.content/('.singing-'+self.asset.sha256+'-v1.json')).read_bytes()
        self.now = 100.
        self.media = MediaFeature(self.content, (self.asset,),
            backend=FakeMediaBackend(clock=lambda:self.now, pausable=True), clock=lambda:self.now)
        self.host = FeatureHost(media=self.media, runtime=RuntimePolicy(simulate=True),
            client=FeatureSimulatedClient(), clock=lambda:self.now,
            completed_turn_store=CompletedTurnStore(self.root/'turns'))
        self.addCleanup(self.host.close)
        self.app = DesktopRuntime(simulate=True, silent_start=True, clock=lambda:self.now)
        self.app.set_host(self.host, None)
        self.enterContext(patch('bmo_ai.features.server.FaceServer', FakeFaceBridge))
        self.server = FeatureServer(self.app, 'synthetic-face', desktop=self.app).start()
        self.addCleanup(self.server.close)
        self.port = self.server._server.server_port
        self.origin = 'http://127.0.0.1:'+str(self.port)
        original_connect = socket.socket.connect
        def local_only(instance, address):
            if not isinstance(address, tuple) or address[0] != '127.0.0.1':
                raise AssertionError('Only the injected loopback HTTP server is permitted')
            return original_connect(instance, address)
        self.enterContext(patch('socket.socket.connect', local_only))
        for target in ('subprocess.Popen', 'bmo_ai.fish_tts.synthesize_wav',
                       'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                       'bmo_ai.features.media.singing_analysis.prepare_profile'):
            self.enterContext(patch(target, side_effect=AssertionError('No player/provider/analysis during HTTP')))
        self.start()

    def start(self, request='fixture_song_request', mode='face'):
        result = self.host.execute(HostIntent('play_song', self.asset.id, media_mode=mode), request_id=request)
        self.assertEqual(result.status, 'simulated')
        state = self.media.snapshot()
        self.path = ('/api/music-singing/'+state['request_id']+'_'+str(state['playback_generation'])
                     +'?mode_generation='+str(self.host.modes.generation))

    def request(self, path=None, headers=()):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        try:
            connection.putrequest('GET', self.path if path is None else path)
            for name, value in headers:connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:connection.close()

    def test_desktop_delegation_returns_exact_hash_bound_bytes_without_poll_or_heartbeat(self):
        before = self.host._last_shell_seen
        with patch.object(self.host, 'heartbeat', side_effect=AssertionError('GET must be passive')), \
             patch.object(self.host, 'poll', side_effect=AssertionError('GET must not advance owners')):
            for _ in range(3):
                status, headers, body = self.request(headers=[('Origin',self.origin)])
                self.assertEqual(status, 200)
                self.assertEqual(body, self.raw)
                self.assertEqual(hashlib.sha256(body).hexdigest(), self.prepared['profile_sha256'])
                self.assertEqual(json.loads(body)['audio_sha256'], self.asset.sha256)
                self.assertEqual(headers['Content-Type'], 'application/json')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertNotIn(str(self.root).encode(), body)
        self.assertEqual(self.host._last_shell_seen, before)
        self.assertEqual(self.host.audio.owner, 'music')
        self.assertEqual(self.host.client.requests, [])

    def test_origin_host_and_query_are_checked_before_the_private_getter(self):
        with patch.object(self.app, 'get_music_singing', wraps=self.app.get_music_singing) as getter:
            for headers in ([('Origin','https://unrelated.invalid')],
                            [('Origin',self.origin),('Origin',self.origin)],
                            [('Host','unrelated.invalid')]):
                with self.subTest(headers=headers):self.assertEqual(self.request(headers=headers)[0],403)
            token = self.path.split('?')[0]
            for suffix in ('', '?mode_generation=', '?mode_generation=-1', '?mode_generation=1.0',
                           '?mode_generation=1&mode_generation=1', '?mode_generation=1&download=true',
                           '?mode_generation=0000000000000', '?other=1'):
                with self.subTest(suffix=suffix):self.assertEqual(self.request(token+suffix)[0],400)
            self.assertEqual(getter.call_count,0)

    def test_pause_seek_and_replay_keep_bytes_but_retire_old_playback_tokens(self):
        old = self.path
        self.media.pause()
        self.assertEqual(self.request()[2],self.raw)
        self.media.seek(.4)
        self.assertEqual(self.request(old)[0],404)
        current = self.media.snapshot()['singing_profile']['url']
        self.assertEqual(self.request(current)[2],self.raw)
        self.media.resume();self.media.replay()
        self.assertEqual(self.request(current)[0],404)
        current = self.media.snapshot()['singing_profile']['url']
        self.assertEqual(self.request(current)[2],self.raw)
        self.assertEqual(self.host.audio.owner,'music')

    def test_back_video_wrong_request_and_mode_generation_do_not_serve_the_profile(self):
        old = self.path
        wrong_generation = old.split('?')[0]+'?mode_generation='+str(self.host.modes.generation+1)
        self.assertEqual(self.request(wrong_generation)[0],404)
        self.assertEqual(self.request(old.replace('fixture_song_request','unknown_request'))[0],404)
        self.host.execute(HostIntent('stop_media'),request_id='fixture_back')
        self.assertEqual(self.request(old)[0],404)
        self.assertEqual(self.host.modes.mode,'conversation')
        self.assertIsNone(self.host.audio.owner)
        self.start('fixture_video_request',mode='video')
        self.assertEqual(self.request()[0],404)
        self.assertEqual(self.request(old)[0],404)

    def test_missing_analysis_uses_existing_fallback_and_cannot_be_fabricated_by_http(self):
        self.host.execute(HostIntent('stop_media'),request_id='before_missing')
        missing = self.media._singing_cache.pop(self.asset.id)
        try:
            self.start('missing_profile_request')
            self.assertEqual(self.media.snapshot()['singing_profile']['status'],'missing')
            self.assertEqual(self.request()[0],404)
            self.assertEqual(self.host.audio.owner,'music')
            self.assertEqual(self.media.snapshot()['state'],'playing')
        finally:self.media._singing_cache[self.asset.id] = missing

    def test_no_host_and_internal_failure_do_not_leak_private_data(self):
        self.app.host = None
        try:self.assertEqual(self.request()[0],404)
        finally:self.app.host = self.host
        with patch.object(self.host,'get_music_singing',side_effect=RuntimeError('private path or credential')):
            status,_,body = self.request()
        self.assertEqual(status,503)
        self.assertEqual(body,b'State unavailable')
        self.assertNotIn(b'private',body)


if __name__ == '__main__':unittest.main()
