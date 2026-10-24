"""Read-only loopback face bridge and the approved v13.2 WAV mouth algorithm.

The copyrighted face stays at its explicit local path; this module never copies
it into the repository and never opens an audio device or invokes a player.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
from pathlib import Path
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import wave

from .screen_profiles import SCREEN_PROFILES


FACE_SHA256 = SCREEN_PROFILES["v13.2"]["sha256"]
LADDER = ("closed", "parted", "small", "low", "mid", "body", "open", "high", "wide")
MOUTHS = frozenset((*LADDER, "rest", "round", "tight", "flat", "grit"))
EMOTIONS = frozenset(("neutral", "happy", "angry", "sad", "crying", "surprised"))
EMOTION_MODES = frozenset(("manual", "auto"))
PHASES = frozenset(("idle", "listening", "thinking", "speaking"))
PLAYBACK = frozenset(("idle", "prepared", "started", "ended", "cancelled", "error"))


def _js_round(value):
    return math.floor(value + 0.5)


def bridge(previous: str, target: str, elapsed: float, duration: float) -> str:
    """Port of the approved HTML's _test.bridge, including its 30 fps holds."""
    if previous == target or duration < .105 or elapsed >= .066667:
        return target
    i = LADDER.index(previous) if previous in LADDER else -1
    j = LADDER.index(target) if target in LADDER else -1
    if i >= 0 and j >= 0 and abs(i - j) >= 3:
        steps = 2 if abs(i - j) >= 6 else 1
        phase = math.floor(elapsed / (1 / 30))
        if phase >= steps:
            return target
        return LADDER[_js_round(i + (j - i) * (phase + 1) / (steps + 1))]
    if target in ("round", "tight") and previous == "closed" and elapsed < 1 / 30:
        return "tight"
    return target


def envelope(channels: list[list[float]], rate: int) -> list[dict]:
    """Exact RMS/envelope quantizer from v13.2, without WebAudio or playback."""
    if not channels or rate <= 0 or not channels[0]:
        raise ValueError("Nonempty PCM channels and positive sample rate are required")
    size = len(channels[0])
    if any(len(channel) != size for channel in channels):
        raise ValueError("PCM channel lengths differ")
    step = max(1, _js_round(rate / 120))
    rms = []
    for offset in range(0, size, step):
        total, count = 0.0, 0
        for channel in channels:
            for sample in channel[offset:min(offset + step, size)]:
                total += sample * sample
                count += 1
        rms.append(math.sqrt(total / max(1, count)))
    nonzero = sorted(value for value in rms if value > .0004)
    p90 = nonzero[math.floor(len(nonzero) * .90)] if nonzero else .05
    scale = max(.012, p90)
    floor = max(.001, scale * .035)
    events = [{"time": 0.0, "mouth": "closed", "level": 0.0}]
    env, active, last_switch = 0.0, 0, -1.0
    thresholds = (.06, .12, .21, .33, .47, .62, .78, .91)
    for i, rms_value in enumerate(rms):
        raw = max(0.0, min(1.0, (rms_value - floor) / (scale - floor)))
        coef = .42 if raw > env else .24
        env += (raw - env) * coef
        q = next((j for j, threshold in enumerate(thresholds) if env < threshold), 8)
        time = i * step / rate
        if q != active and (time - last_switch >= .05 or q == 0):
            if abs(q - active) == 1 and time - last_switch < .075:
                continue
            active, last_switch = q, time
            events.append({"time": time, "mouth": LADDER[q], "level": env})
    events.append({"time": size / rate, "mouth": "closed", "level": 0.0})
    return events


def expand_cues(events: list[dict], duration: float) -> list[dict]:
    """Expand the original raw targets through bridge before external setMouth.

    Expansion uses each *raw* preceding target, as timelineMouth does. It is not
    a smoothed interpolation or a replacement animation. Equal-time targets
    use the final event, matching the original binary search.
    """
    result = []
    for i, event in enumerate(events):
        start = float(event["time"])
        end = float(events[i + 1]["time"]) if i + 1 < len(events) else duration
        if end <= start and i + 1 < len(events):
            continue
        previous = events[i - 1]["mouth"] if i else "closed"
        target = event["mouth"]
        for elapsed in (0.0, 1 / 30, 2 / 30):
            if elapsed and start + elapsed >= end:
                continue
            mouth = bridge(previous, target, elapsed, end - start)
            if not result or result[-1]["mouth"] != mouth:
                result.append({"time": start + elapsed, "mouth": mouth})
    return result


def wav_cues(path) -> list[dict]:
    """Read a PCM WAV and return bridge-expanded relative mouth cues.

    WAVs are bounded at three minutes/50 MB, matching the approved face loader.
    Reading this function never synthesizes or plays audio. The actual final
    (already gained) output WAV is the intended input.
    """
    path = Path(path)
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("WAV exceeds 50 MB")
    with wave.open(str(path), "rb") as wav:
        count, width, rate, frames = (wav.getnchannels(), wav.getsampwidth(),
                                      wav.getframerate(), wav.getnframes())
        if wav.getcomptype() != "NONE" or width not in (1, 2, 3, 4):
            raise ValueError("Only uncompressed 8/16/24/32-bit integer PCM is supported")
        if count not in (1, 2) or rate <= 0 or frames <= 0 or frames / rate > 180:
            raise ValueError("WAV must have 1-2 channels and 0-180 seconds of PCM")
        data = wav.readframes(frames)
    if len(data) != frames * width * count:
        raise ValueError("Truncated PCM WAV")
    channels = [[] for _ in range(count)]
    for frame in range(frames):
        for channel in range(count):
            offset = (frame * count + channel) * width
            sample = int.from_bytes(data[offset:offset + width], "little", signed=width != 1)
            value = (sample - 128) / 128 if width == 1 else sample / (1 << (width * 8 - 1))
            # AudioBuffer.getChannelData returns Float32Array, including PCM32.
            channels[channel].append(struct.unpack("f", struct.pack("f", value))[0])
    return expand_cues(envelope(channels, rate), frames / rate)


def _public_snapshot(value):
    """Whitelist the browser contract; never serialize arbitrary app objects."""
    if not isinstance(value, dict):
        raise ValueError("Snapshot must be a dictionary")
    out = {}
    for field in ("session_id", "turn_id", "utterance_id", "source"):
        item = value.get(field)
        if item is not None and (not isinstance(item, str) or len(item) > 160):
            raise ValueError("Invalid snapshot identifier/source")
        out[field] = item if item is not None else ""
    sequence = value.get("sequence", 0)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ValueError("Invalid snapshot sequence")
    out["sequence"] = sequence
    for field, allowed, default in (("phase", PHASES, "idle"),
                                     ("emotion", EMOTIONS, "neutral"),
                                     ("emotion_mode", EMOTION_MODES, "manual"),
                                     ("playback", PLAYBACK, "idle")):
        item = value.get(field, default)
        if not isinstance(item, str) or item not in allowed:
            raise ValueError("Invalid snapshot state")
        out[field] = item
    position = value.get("position", 0)
    if isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(position):
        raise ValueError("Invalid playback position")
    out["position"] = max(0, min(180, position))
    cues = value.get("cues", [])
    if not isinstance(cues, list) or len(cues) > 10000:
        raise ValueError("Invalid mouth timeline")
    previous = -1
    out["cues"] = []
    for cue in cues:
        time, mouth = cue.get("time"), cue.get("mouth")
        if (isinstance(time, bool) or not isinstance(time, (int, float))
                or not math.isfinite(time) or time < previous or time < 0 or time > 180
                or mouth not in MOUTHS):
            raise ValueError("Invalid mouth cue")
        out["cues"].append({"time": time, "mouth": mouth})
        previous = time
    # Prepared/terminal snapshots cannot accidentally hand out old cues.
    if out["playback"] != "started":
        out["cues"] = []
    return out


class FaceServer:
    """GET-only loopback server for one pinned local screen and wrapper."""

    def __init__(self, face_path, *, snapshot, host="127.0.0.1", port=0,
                 screen_profile="v13.2"):
        host = "127.0.0.1" if host == "localhost" else host
        if not ipaddress.ip_address(host).is_loopback or ":" in host:
            raise ValueError("An IPv4 loopback host is required")
        if not isinstance(screen_profile, str) or screen_profile not in SCREEN_PROFILES:
            raise ValueError("Unknown screen profile")
        self.screen_profile = screen_profile
        profile = SCREEN_PROFILES[screen_profile]
        self.face_path = Path(face_path).expanduser().resolve(strict=True)
        self._face = self.face_path.read_bytes()
        self.sha256 = hashlib.sha256(self._face).hexdigest()
        # Keep the original v13.2 patch boundary for existing fixture tests.
        # Patching it cannot admit another file through the explicit R10 pin.
        expected = FACE_SHA256 if screen_profile == "v13.2" else profile["sha256"]
        if self.sha256 != expected:
            raise ValueError("Face SHA-256 does not match selected screen profile")
        self._screen_metadata = json.dumps({
            "version": screen_profile,
            "sha256": self.sha256,
            "approval_status": profile["approval_status"],
            "pi_live_verified": profile["pi_live_verified"],
        }).encode()
        self.snapshot, self.host, self.port = snapshot, host, port
        self._server = self._thread = None
        root = Path(__file__).with_name("integration_web")
        self._resources = {"/": ("text/html; charset=utf-8", (root / "wrapper.html").read_bytes()),
                           "/adapter.js": ("text/javascript; charset=utf-8", (root / "adapter.js").read_bytes()),
                           "/face.html": ("text/html; charset=utf-8", self._face)}

    @property
    def url(self):
        if self._server is None:
            raise RuntimeError("Face server has not started")
        return f"http://{self.host}:{self._server.server_port}/"

    def start(self):
        if self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # No request/voice/personal data log.

            def _respond(self, status, body=b"", content_type="text/plain; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'none'; connect-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
                try:
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # A browser may disappear while a snapshot is being sent.

            def do_GET(self):
                # The request owns its server even if FaceServer.close() has
                # already detached owner._server during an in-flight request.
                authority = f"{owner.host}:{self.server.server_port}"
                if self.headers.get("Host") not in (authority, f"localhost:{self.server.server_port}"):
                    self._respond(403)
                    return
                if self.headers.get("Origin") not in (None, f"http://{authority}"):
                    self._respond(403)
                    return
                path = urlsplit(self.path).path
                if path == "/api/screen":
                    self._respond(200, owner._screen_metadata, "application/json")
                elif path == "/api/state":
                    try:
                        body = json.dumps(_public_snapshot(owner.snapshot()), allow_nan=False).encode()
                    except Exception:
                        self._respond(503, b"State unavailable")
                        return
                    self._respond(200, body, "application/json")
                elif path in owner._resources:
                    content_type, body = owner._resources[path]
                    self._respond(200, body, content_type)
                else:
                    self._respond(404)

            def do_POST(self):
                self._respond(405)

            do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="bmo-face-loopback", daemon=True)
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
