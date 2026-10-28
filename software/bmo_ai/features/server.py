"""One loopback shell. Commands need exact origin, host and per-server token."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
from threading import Thread
from urllib.parse import urlsplit

from ..integration_face import FaceServer, _public_snapshot
from .contracts import FeatureError
from .host import HostControl
from .reply import HostIntent


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise FeatureError("invalid_request")
        result[name] = value
    return result


class FeatureServer:
    def __init__(self, host, face_file, *, screen_profile="v13.2", port=0,
                 desktop=None, developer=False):
        if type(port) is not int or not 0 <= port <= 65535:
            raise FeatureError("invalid_port")
        self.host, self.port, self.desktop = host, port, desktop
        self._bridge = FaceServer(face_file, snapshot=host.face_snapshot, screen_profile=screen_profile)
        self._token = secrets.token_urlsafe(32)
        root = Path(__file__).with_name("web")
        page = root.joinpath("index.html").read_text().replace("__COMMAND_TOKEN__", self._token).encode()
        self._resources = dict(self._bridge._resources)
        self._resources.update({"/": ("text/html; charset=utf-8", page),
            "/feature.js": ("text/javascript; charset=utf-8", root.joinpath("feature.js").read_bytes()),
            "/feature.css": ("text/css; charset=utf-8", root.joinpath("feature.css").read_bytes())})
        if desktop is not None:
            product = root.parent.parent / "desktop_web"
            product_page = product.joinpath("index.html").read_text().replace("__COMMAND_TOKEN__", self._token)
            if getattr(desktop, "character_life", False):
                product_page = (product_page.replace("<h1>BMO Loading...</h1>", "<h1>Starting BMO…</h1>")
                    .replace('id="loading-stage">시작 준비 중...', 'id="loading-stage">Getting ready…')
                    .replace('aria-label="BMO 시작 준비"', 'aria-label="Getting BMO ready"'))
            self._resources["/"] = ("text/html; charset=utf-8", product_page.encode())
            for name, mime in (("desktop.js", "text/javascript; charset=utf-8"),
                               ("desktop.css", "text/css; charset=utf-8")):
                self._resources["/" + name] = (mime, product.joinpath(name).read_bytes())
            if developer:
                development_page = page.replace(b"<body>", b'<body><a href="/">BMO product screen</a>')
                self._resources["/developer"] = ("text/html; charset=utf-8", development_page)
            else:
                self._resources.pop("/feature.js", None)
                self._resources.pop("/feature.css", None)
        self._server = self._thread = None

    @property
    def url(self):
        if self._server is None:
            raise FeatureError("server_not_started")
        return f"http://127.0.0.1:{self._server.server_port}/"

    def start(self):
        if self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2.0)

            def log_message(self, *_):
                pass

            def _origin(self, *, write=False):
                authority = f"127.0.0.1:{self.server.server_port}"
                origins, hosts = self.headers.get_all("Origin", []), self.headers.get_all("Host", [])
                if len(hosts) != 1 or hosts[0] != authority or len(origins) > 1:
                    return False
                expected = "http://" + authority
                return origins == [expected] if write else not origins or origins == [expected]

            def _send(self, status, body=b"", mime="text/plain; charset=utf-8"):
                if isinstance(body, dict):
                    body, mime = json.dumps(body, allow_nan=False).encode(), "application/json"
                self.send_response(status)
                for key, value in {"Content-Type": mime, "Content-Length": str(len(body)),
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "Cross-Origin-Resource-Policy": "same-origin",
                    "Referrer-Policy": "no-referrer", "X-Frame-Options": "SAMEORIGIN",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'none'; connect-src 'self'; frame-src 'self' about:; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"}.items():
                    self.send_header(key, value)
                try:
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                except OSError:
                    pass

            def do_GET(self):
                if not self._origin():
                    self._send(403)
                    return
                path = urlsplit(self.path).path
                try:
                    if path == "/api/state":
                        self._send(200, _public_snapshot(owner.host.face_snapshot()))
                    elif path == "/api/features":
                        owner.host.heartbeat()
                        self._send(200, owner.host.snapshot())
                    elif path == "/api/screen":
                        self._send(200, owner._bridge._screen_metadata, "application/json")
                    elif path == "/api/desktop" and owner.desktop is not None:
                        owner.host.heartbeat()
                        self._send(200, owner.desktop.startup_snapshot())
                    elif path == "/api/preview":
                        image = owner.host.preview_bytes()
                        self._send(200, image, "image/png") if image else self._send(404)
                    elif path.startswith("/api/photo/"):
                        photo_id = path.removeprefix("/api/photo/")
                        image = owner.host.photo_bytes(photo_id) if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", photo_id) else None
                        self._send(200, image, "image/png") if image else self._send(404)
                    elif path.startswith("/api/video/"):
                        video_id = path.removeprefix("/api/video/")
                        payload = owner.host.video_bytes(video_id) if re.fullmatch(r"video_[0-9a-f]{32}", video_id) else None
                        self._send(200, payload, "application/octet-stream") if payload else self._send(404)
                    elif path in owner._resources:
                        mime, body = owner._resources[path]
                        self._send(200, body, mime)
                    else:
                        self._send(404)
                except Exception:
                    self._send(503, b"State unavailable")

            def do_POST(self):
                token = self.headers.get("X-BMO-Token", "")
                if (not self._origin(write=True) or len(self.headers.get_all("X-BMO-Token", [])) != 1
                        or not token.isascii() or not hmac.compare_digest(token, owner._token)):
                    self._send(403)
                    return
                endpoints = ("/api/intent", "/api/control") + (("/api/desktop",) if owner.desktop is not None else ())
                if urlsplit(self.path).path not in endpoints:
                    self._send(404)
                    return
                if (self.headers.get_content_type() != "application/json"
                        or len(self.headers.get_all("Content-Type", [])) != 1
                        or self.headers.get("Transfer-Encoding") is not None
                        or len(self.headers.get_all("Content-Length", [])) != 1):
                    self._send(415)
                    return
                try:
                    length = int(self.headers["Content-Length"])
                    if not 0 < length <= 8192:
                        self._send(413)
                        return
                    value = json.loads(self.rfile.read(length).decode("utf-8"), object_pairs_hook=_unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(FeatureError("invalid_request")))
                    if (type(value) is not dict or set(value) != {"version", "nonce", "payload"}
                            or type(value["version"]) is not int or value["version"] != 1
                            or type(value["payload"]) is not dict):
                        raise FeatureError("invalid_request")
                    payload = value["payload"]
                    if urlsplit(self.path).path == "/api/desktop":
                        result = owner.desktop.queue_desktop_control(value["nonce"], payload)
                        self._send(202, result)
                        return
                    elif urlsplit(self.path).path == "/api/intent":
                        if set(payload) != {"name", "target"}:
                            raise FeatureError("invalid_request")
                        command = HostIntent(**payload)
                    else:
                        if set(payload) != {"action", "value"}:
                            raise FeatureError("invalid_request")
                        command = HostControl(**payload)
                    result = owner.host.queue_command(value["nonce"], command)
                    self._send(202, result)
                except Exception:
                    self._send(400, {"status": "rejected", "code": "invalid_request"})

            def _unsupported(self):
                self._send(405)

            do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _unsupported
            do_HEAD = do_GET

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        self._thread = Thread(target=self._server.serve_forever, name="bmo-feature-loopback", daemon=True)
        self._thread.start()
        return self

    def close(self):
        if self._server is not None:
            server, self._server = self._server, None
            server.shutdown()
            server.server_close()
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
