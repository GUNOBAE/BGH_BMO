"""Loopback-only HTTP security checks with fake host and synthetic face bytes."""
import http.client
from contextlib import redirect_stderr
from io import StringIO
import json
import socket
from threading import get_ident
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError, request_id_ok
from bmo_ai.features.server import FeatureServer


class FakeFaceBridge:
    def __init__(self, face_file, *, snapshot, screen_profile):
        self._resources = {"/face.html": ("text/html; charset=utf-8", b"<!doctype html><p>Fixture face</p>"),
                           "/adapter.js": ("text/javascript", b"/* fixture adapter */")}
        self._screen_metadata = json.dumps({"profile": screen_profile}).encode()


class FakeHost:
    def __init__(self):
        self.reset()

    def reset(self):
        self.queued = []
        self.queue_threads = []
        self.beats = 0
        self.photo_ids = []
        self.preview = b"\x89PNG\r\n\x1a\nfixture"
        self.frame = None
        self.frame_requests = []
        self.frame_error = False
        self.error = False

    def heartbeat(self):
        self.beats += 1

    def face_snapshot(self):
        return {"session_id": "fixture", "sequence": 1, "phase": "idle", "emotion": "crying",
                "emotion_mode": "auto", "playback": "idle", "position": 0, "cues": [],
                "speech": "private fixture speech", "api_key": "private fixture credential"}

    def snapshot(self):
        if self.error:
            raise RuntimeError("private fixture provider body")
        return {"simulation": True, "real_features_enabled": False, "mode": "conversation",
                "mode_generation": 0}

    def preview_bytes(self):
        return self.preview

    def preview_frame(self, after=None):
        self.frame_requests.append(after)
        if self.frame_error:
            raise RuntimeError('private fixture camera payload')
        return self.frame

    def photo_bytes(self, photo_id):
        self.photo_ids.append(photo_id)
        return self.preview if photo_id == "fixture_photo" else None

    def queue_command(self, nonce, command):
        if not request_id_ok(nonce):
            raise FeatureError("invalid_request")
        self.queued.append((nonce, command))
        self.queue_threads.append(get_ident())
        return {"request_id": "fixture_request", "status": "accepted", "code": "queued",
                "simulated": True, "executed": False, "completed": False, "completion": "not_started"}


class FeatureHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge_patch = patch("bmo_ai.features.server.FaceServer", FakeFaceBridge)
        cls.bridge_patch.start()
        cls.addClassCleanup(cls.bridge_patch.stop)
        cls.host = FakeHost()
        cls.server = FeatureServer(cls.host, "synthetic-face-only", port=0).start()
        cls.addClassCleanup(cls.server.close)
        cls.port = cls.server._server.server_port
        cls.authority = "127.0.0.1:" + str(cls.port)
        cls.origin = "http://" + cls.authority

    def setUp(self):
        self.host.reset()
        original = socket.socket.connect

        def connect(instance, address):
            if not isinstance(address, tuple) or address[0] != "127.0.0.1":
                raise AssertionError("Only this test's loopback transport is permitted")
            return original(instance, address)

        self.enterContext(patch("socket.socket.connect", connect))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No child process is permitted")))

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            pairs = [("Host", self.authority)] if headers is None else list(headers)
            for name, value in pairs:
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def envelope(self, *, control=False, **overrides):
        value = {"version": 1, "nonce": "fixture_nonce", "payload":
                 {"action": "stop", "value": None} if control else {"name": "open_game_menu", "target": None}}
        value.update(overrides)
        return json.dumps(value).encode()

    def authorized(self, body, *, change=None, extra=()):
        values = {"Host": self.authority, "Origin": self.origin,
                  "X-BMO-Token": self.server._token, "Content-Type": "application/json",
                  "Content-Length": str(len(body))}
        for name, value in (change or {}).items():
            if value is None:
                values.pop(name, None)
            else:
                values[name] = value
        return [*values.items(), *extra]

    def post(self, body=None, *, path="/api/intent", change=None, extra=()):
        body = self.envelope() if body is None else body
        return self.request("POST", path, body=body,
                            headers=self.authorized(body, change=change, extra=extra))

    def test_only_explicit_resources_and_read_endpoints_are_available(self):
        for path in ("/", "/feature.js", "/feature.css", "/face.html", "/adapter.js",
                     "/api/state", "/api/features", "/api/screen"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 200)
        for path in ("/private_credentials.py", "/.env", "/api/credentials", "/api/key",
                     "/api/intent", "/api/control", "/../credentials.json", "/%2e%2e/credentials.json"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.host.queued, [])

    def test_loopback_exact_host_and_read_origin_are_enforced(self):
        cases = ([], [("Host", "evil.invalid")], [("Host", "localhost:" + str(self.port))],
                 [("Host", self.authority), ("Host", self.authority)],
                 [("Host", self.authority), ("Origin", "https://evil.invalid")],
                 [("Host", self.authority), ("Origin", self.origin), ("Origin", self.origin)])
        for index, headers in enumerate(cases):
            with self.subTest(index=index):
                self.assertEqual(self.request("GET", "/api/features", headers=headers)[0], 403)
        self.assertEqual(self.host.beats, 0)

    def test_response_security_headers_block_cross_origin_embedding_and_browser_audio(self):
        status, headers, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertIn("media-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertTrue(b"__COMMAND_TOKEN__" not in page)

    def test_post_requires_exact_origin_host_and_single_private_token(self):
        cases = ({"Host": "evil.invalid"}, {"Origin": None}, {"Origin": "http://evil.invalid"},
                 {"X-BMO-Token": None}, {"X-BMO-Token": "wrong-fixture"}, {"X-BMO-Token": "é"})
        for index, change in enumerate(cases):
            with self.subTest(index=index):
                self.assertEqual(self.post(change=change)[0], 403)
        for extra in ((("Host", self.authority),), (("Origin", self.origin),),
                      (("X-BMO-Token", "wrong-fixture"),)):
            with self.subTest(header=extra[0][0]):
                self.assertEqual(self.post(extra=extra)[0], 403)
        self.assertEqual(self.host.queued, [])

    def test_token_is_per_server_and_is_not_exported_in_state_or_query_api(self):
        other = FeatureServer(FakeHost(), "synthetic-face-only", port=0).start()
        try:
            self.assertTrue(other._token != self.server._token)
            self.assertEqual(self.post(change={"X-BMO-Token": other._token})[0], 403)
        finally:
            other.close()
        for path in ("/api/state", "/api/features"):
            body = self.request("GET", path)[2]
            self.assertTrue(self.server._token.encode() not in body)
            self.assertNotIn(b"private fixture", body)
        self.assertEqual(self.request("GET", "/api/token")[0], 404)

    def test_valid_intent_and_control_are_queued_on_http_thread_without_executing(self):
        owner = get_ident()
        first = self.post()
        second = self.post(self.envelope(control=True), path="/api/control")
        self.assertEqual((first[0], second[0]), (202, 202))
        self.assertEqual(len(self.host.queued), 2)
        self.assertTrue(all(thread != owner for thread in self.host.queue_threads))
        self.assertEqual(self.host.queued[0][1].public(), {"name": "open_game_menu", "target": None})
        self.assertEqual(self.host.queued[1][1].public(), {"action": "stop", "value": None})
        self.assertFalse(json.loads(second[2])["executed"])

    def test_content_type_transfer_encoding_and_content_length_are_strict(self):
        for change, status in (({"Content-Type": "text/plain"}, 415),
                               ({"Content-Type": None}, 415), ({"Content-Length": None}, 415),
                               ({"Content-Length": "-1"}, 413), ({"Content-Length": "0"}, 413),
                               ({"Content-Length": "8193"}, 413), ({"Content-Length": "invalid"}, 400)):
            with self.subTest(header_names=tuple(change)):
                self.assertEqual(self.post(change=change)[0], status)
        for extra in ((("Transfer-Encoding", "chunked"),), (("Content-Length", "1"),),
                      (("Content-Type", "application/json"),)):
            with self.subTest(header=extra[0][0]):
                self.assertEqual(self.post(extra=extra)[0], 415)
        self.assertEqual(self.host.queued, [])

    def test_json_duplicates_nonfinite_truncation_invalid_utf8_and_trailing_data_are_rejected(self):
        payloads = (b'{"version":1,"version":1,"nonce":"n","payload":{"name":"open_game_menu","target":null}}',
                    b'{"version":1,"nonce":"n","payload":{"name":"open_game_menu","name":"take_photo","target":null}}',
                    b'{"version":NaN,"nonce":"n","payload":{}}', b'{"version":', b'\xff', b'{} {}',
                    b'"private fixture text"')
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                status, _, body = self.post(payload)
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(body), {"status": "rejected", "code": "invalid_request"})
                self.assertNotIn(b"private fixture", body)
        self.assertEqual(self.host.queued, [])

    def test_version_fields_nonce_and_intent_targets_do_not_accept_arbitrary_commands(self):
        bad = [self.envelope(version=value) for value in (True, 1.0, 2, "1")]
        bad += [self.envelope(nonce=value) for value in ("../private", [], "", None)]
        bad += [self.envelope(payload=value) for value in (
            {"name": "shell", "target": "command"}, {"name": "launch_game", "target": "../private"},
            {"name": "launch_game", "target": "https://invalid.example"},
            {"name": "arm_gesture", "target": "90"},
            {"name": "open_game_menu", "target": None, "pwm": 1500}, [])]
        for index, payload in enumerate(bad):
            with self.subTest(index=index):
                self.assertEqual(self.post(payload)[0], 400)
        self.assertEqual(self.host.queued, [])

    def test_unknown_control_and_unverified_physical_key_are_rejected(self):
        for payload in ({"action": "shell", "value": "private fixture"},
                        {"action": "input", "value": {"key": "GP7", "pressed": True}},
                        {"action": "input", "value": {"key": "B", "pressed": 1}},
                        {"action": "preview", "value": "true"},
                        {"action": "stop", "value": {"path": "private"}}):
            with self.subTest(action=payload["action"]):
                self.assertEqual(self.post(self.envelope(payload=payload), path="/api/control")[0], 400)
        self.assertEqual(self.host.queued, [])

    def test_preview_photo_and_head_routes_are_read_only_and_validate_photo_identifier(self):
        self.assertEqual(self.request("GET", "/api/preview")[1]["Content-Type"], "image/png")
        self.assertEqual(self.request("GET", "/api/photo/fixture_photo")[0], 200)
        for path in ("/api/photo/../../private", "/api/photo/%2e%2e", "/api/photo/", "/api/photo/unknown"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.host.photo_ids, ["fixture_photo", "unknown"])
        self.assertEqual(self.request("HEAD", "/api/state")[2], b"")
        self.host.preview = None
        self.assertEqual(self.request("GET", "/api/preview")[0], 404)
        self.assertEqual(self.host.queued, [])

    def test_unknown_write_path_and_other_methods_cannot_trigger_commands(self):
        self.assertEqual(self.post(path="/api/arbitrary")[0], 404)
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method, "/api/intent")[0], 405)
        self.assertEqual(self.host.queued, [])

    def test_latest_preview_frame_binary_route_returns_cached_jpeg_and_fixed_metadata(self):
        payload=b'\xff\xd8private fixture encoded image\xff\xd9'
        self.host.frame=dict(id='7:1',payload=payload,mime='image/jpeg',captured_at=123.125,age_ms=4.5)
        logged=StringIO()
        with redirect_stderr(logged):
            status,headers,body=self.request('GET','/api/preview/frame')
        self.assertEqual(status,200)
        self.assertEqual(body,payload)
        self.assertEqual(headers['Content-Type'],'image/jpeg')
        self.assertEqual(headers['Content-Length'],str(len(payload)))
        self.assertEqual(headers['X-BMO-Frame-Id'],'7:1')
        self.assertEqual(float(headers['X-BMO-Captured-At']),123.125)
        self.assertEqual(float(headers['X-BMO-Frame-Age-Ms']),4.5)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'],'same-origin')
        self.assertEqual(self.host.frame_requests,[None])
        self.assertEqual(self.host.beats,0)
        self.assertEqual(self.host.queued,[])
        self.assertEqual(logged.getvalue(),'')
        # Image bytes remain independent from both JSON snapshots and logs.
        for path in ('/api/features','/api/state'):
            self.assertNotIn(payload,self.request('GET',path)[2])

    def test_latest_preview_frame_empty_or_unchanged_is_204_without_body_or_busywait(self):
        self.assertEqual(self.request('GET','/api/preview/frame?after=')[::2],(204,b''))
        self.host.frame=dict(id='2:1',payload=self.host.preview,mime='image/png',captured_at=100.,age_ms=0.)
        self.assertEqual(self.request('GET','/api/preview/frame?after=2%3A1')[::2],(204,b''))
        self.host.frame=dict(self.host.frame,id='2:2')
        status,headers,body=self.request('GET','/api/preview/frame?after=2%3A1')
        self.assertEqual((status,headers['X-BMO-Frame-Id'],body),(200,'2:2',self.host.preview))
        self.assertEqual(headers['Content-Type'],'image/png')
        self.assertEqual(self.host.frame_requests,[None,'2:1','2:1'])

    def test_latest_preview_frame_head_has_no_payload_or_host_side_effects(self):
        self.host.frame=dict(id='2:1',payload=self.host.preview,mime='image/png',captured_at=100.,age_ms=0.)
        status,headers,body=self.request('HEAD','/api/preview/frame?after=2%3A0')
        self.assertEqual((status,body),(204,b''))
        self.assertEqual(headers['Content-Length'],'0')
        self.assertNotIn('X-BMO-Frame-Id',headers)
        self.assertEqual(self.host.frame_requests,[])

    def test_latest_preview_frame_cursor_is_strict_and_never_reaches_host_when_invalid(self):
        for query in ('after','after=1','after=1:2:3','after=-1:2','after=1:2&after=1:2',
                      'other=1:2','after=1:2&other=value','after=%0D%0AHeader:value',
                      'after='+('1'*64)+':2','after=%EF%BC%91:2'):
            with self.subTest(query=query):
                self.assertEqual(self.request('GET','/api/preview/frame?'+query)[0],400)
                self.assertEqual(self.request('HEAD','/api/preview/frame?'+query)[0],400)
        self.assertEqual(self.host.frame_requests,[])

    def test_latest_preview_frame_enforces_host_origin_and_rejects_header_injection(self):
        for headers in ([],[('Host','evil.invalid')],[('Host',self.authority),('Host',self.authority)],
                        [('Host',self.authority),('Origin','https://evil.invalid')],
                        [('Host',self.authority),('Origin',self.origin),('Origin',self.origin)]):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('GET','/api/preview/frame',headers=headers)[0],403)
        self.assertEqual(self.host.frame_requests,[])
        good=dict(id='3:1',payload=self.host.preview,mime='image/png',captured_at=100.,age_ms=0.)
        for bad in (dict(good,id='3:1\r\nInjected: true'),dict(good,mime='image/png\r\nInjected: true'),
                    dict(good,captured_at=float('nan')),dict(good,age_ms=float('inf')),
                    dict(good,age_ms=-1),dict(good,captured_at=True),dict(good,payload='private fixture image')):
            self.host.frame=bad
            status,headers,body=self.request('GET','/api/preview/frame')
            self.assertEqual((status,body),(503,b'State unavailable'))
            self.assertNotIn('Injected',headers)
            self.assertNotIn(b'private fixture',body)
        self.host.frame_error=True
        self.assertEqual(self.request('GET','/api/preview/frame')[::2],(503,b'State unavailable'))

    def test_short_body_has_finite_timeout_and_never_queues_partial_command(self):
        start = time.monotonic()
        status = self.post(b"{", change={"Content-Length": "100"})[0]
        self.assertEqual(status, 400)
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(self.host.queued, [])

    def test_state_failure_returns_fixed_public_error_without_provider_body(self):
        self.host.error = True
        status, _, body = self.request("GET", "/api/features")
        self.assertEqual(status, 503)
        self.assertEqual(body, b"State unavailable")
        self.assertNotIn(b"private", body)


if __name__ == "__main__":
    unittest.main()
