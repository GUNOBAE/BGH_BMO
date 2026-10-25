"""Desktop lifetime around the existing feature engine, never another brain.

HTTP threads admit readiness/control only. The owner thread polls one Host and
one greeting worker. No key, device, process or network is opened on import.
"""
from collections import deque
import hashlib
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time
from uuid import uuid4

from .conversation import TurnCancelled
from .features.contracts import FeatureError, request_id_ok
from .features.host import HostControl


def public_code(code, default="startup_failed"):
    from .greeting_cache import _CODES
    from .private_credentials import _ERRORS
    from .desktop_browser import _MESSAGES
    allowed = _CODES | _ERRORS.keys() | _MESSAGES.keys() | {
        "startup_failed", "engine_startup_failed", "host_startup_timeout", "face_ready_timeout",
        "browser_failed", "browser_disconnected", "greeting_failed", "greeting_cancelled",
        "greeting_completed", "greeting_timeout", "missing_key", "missing_sdk"}
    return code if type(code) is str and code in allowed else default


class DesktopRuntime:
    def __init__(self, *, clock=time.monotonic, simulate=False, developer=False,
                 startup_timeout=15., greeting_timeout=30., disconnect_timeout=5., require_face_presented=False):
        if any(type(value) not in (float, int) or not 0 < value <= 120 for value in
               (startup_timeout, greeting_timeout, disconnect_timeout)):
            raise FeatureError("invalid_desktop_timeout")
        self.clock, self.simulate, self.developer = clock, simulate, developer
        self.startup_timeout, self.greeting_timeout = startup_timeout, greeting_timeout
        self.disconnect_timeout = disconnect_timeout
        self.run_id = uuid4().hex
        self.host = self.server = self.browser = self.greeting = None
        self._lock = Lock()
        self._exit, self._retry, self._cancel = Event(), Event(), Event()
        self._user_cancel = Event()
        self._face_signal = Event()
        self._face_presented = Event()
        self._require_face_presented = require_face_presented
        self._ready = self._auto_attempted = self._closed = False
        self.failed = False
        self._face_version = None
        self._phase, self._code = "loading", "initializing"
        # Progress is a checkpoint score, never a time estimate or paid API readiness.
        self._progress_percent, self._progress_stage = 10, "lock_acquired"
        self._began, self._last_browser = self.clock(), None
        self._events = Queue(32)
        self._worker = self._lease = None
        self._result = None
        self._attempt, self._sequence = 0, 0
        self._presentation_epoch = 0
        self._overlay = None
        self._greeting_started = None
        self._ever_played = False
        self._greeting_timed_out = False
        self._greeting_suppressed = False
        self._audit = deque(maxlen=64)
        self._disconnect_restore_code = None

    def _state(self, phase, code):
        with self._lock:
            self._phase, self._code = phase, code
            self._audit.append({"phase": phase, "code": code})

    def _advance_progress(self, percent, stage):
        """Record only reached startup checkpoints; never animate an estimate."""
        with self._lock:
            if self._phase != "error" and percent > self._progress_percent:
                self._progress_percent, self._progress_stage = percent, stage

    def browser_started(self):
        """Called by the owner only after its Chromium spawn succeeds."""
        if self.browser is not None:
            self._advance_progress(45, "browser_spawned")

    @property
    def exiting(self):
        return self._exit.is_set()

    def set_server(self, server):
        self.server = server
        self._state("loading", "server_ready")
        self._advance_progress(25, "server_ready")

    def set_browser(self, browser):
        self.browser = browser
        self._state("loading", "waiting_for_face")
        self._advance_progress(35, "browser_registered")

    def set_host(self, host, greeting):
        if self.host is not None or self._closed:
            raise FeatureError("desktop_host_exists")
        self.host, self.greeting = host, greeting
        self._advance_progress(65, "engine_ready")
        if self.clock() - self._began > self.startup_timeout:
            self.fail("host_startup_timeout")

    def fail(self, code="startup_failed"):
        self._cancel.set()
        self.failed = True
        if self._phase != "error":
            self._state("error", public_code(code))

    def startup_snapshot(self):
        with self._lock:
            return {"version": 1, "run_id": self.run_id, "phase": self._phase, "code": self._code,
                "server_ready": self.server is not None, "engine_ready": self.host is not None,
                "face_ready": self._face_signal.is_set(), "greeting_attempts": self._attempt,
                "progress_percent": self._progress_percent,
                "progress_stage": self._progress_stage,
                "progress_status": "error" if self._phase == "error" else "complete" if self._progress_percent == 100 else "loading",
                "progress_error": self._code if self._phase == "error" else None,
                "face_presented": self._face_presented.is_set(),
                "greeting_played": self._ever_played,
                "developer_enabled": self.developer, "external_api_ready": "NOT_CHECKED",
                "live_verified": False, "exit_requested": self.exiting}

    def queue_desktop_control(self, nonce, payload):
        if (self._closed or not request_id_ok(nonce) or type(payload) is not dict
                or set(payload) != {"action", "value"}):
            raise FeatureError("invalid_desktop_control")
        action, value = payload["action"], payload["value"]
        if action == "face_ready":
            if (type(value) is not dict or set(value) != {"run_id", "screen_sha256", "frame_version"}
                    or value["run_id"] != self.run_id or self.server is None
                    or value["screen_sha256"] != self.server._bridge.sha256
                    or type(value["frame_version"]) is not str or not 0 < len(value["frame_version"]) <= 80
                    or not value["frame_version"].isascii()):
                raise FeatureError("invalid_face_ready")
            self._face_version = value["frame_version"]
            self._face_signal.set()  # Owner poll performs the greeting.
            self._advance_progress(90, "face_handshake_verified")
        elif action == "face_presented":
            if (type(value) is not dict or set(value) != {"run_id"} or value["run_id"] != self.run_id
                    or not self._ready or not self._face_signal.is_set()):
                raise FeatureError("invalid_face_presented")
            self._face_presented.set()
        elif action == "exit" and value is None:
            self._exit.set()
        elif action == "retry_greeting" and value is None:
            self._retry.set()  # Cached playback only, never synthesis.
        else:
            raise FeatureError("invalid_desktop_control")
        return {"status": "accepted", "code": "queued"}

    def heartbeat(self):
        self._last_browser = self.clock()
        if self.host is not None:
            self.host.heartbeat()

    def face_snapshot(self):
        self.heartbeat()
        value = (self.host.face_snapshot() if self.host is not None else
                 dict(session_id=self.run_id, turn_id="", utterance_id="", phase="idle",
                      emotion="neutral", emotion_mode="auto", playback="idle", position=0., cues=[], source=""))
        with self._lock:
            self._sequence += 1
            value["sequence"] = self._sequence
            # A greeting temporarily projects a different utterance. Restoring
            # a prior dialogue must have a fresh presentation identity because
            # the adapter correctly retires old utterances. The engine/cache
            # identifiers and dialogue history themselves remain untouched.
            if self._presentation_epoch:
                for key in ("turn_id", "utterance_id"):
                    if value.get(key):
                        digest = hashlib.sha256(value[key].encode()).hexdigest()[:24]
                        value[key] = f"desktop:{self._presentation_epoch}:{digest}"
            if self._overlay is not None:
                value.update(self._overlay)
                value["position"] = (max(0., self.clock() - self._greeting_started)
                                     if self._greeting_started is not None else 0.)
            return value

    def snapshot(self):
        if self.host is not None:
            return self.host.snapshot()
        return {"version": 1, "simulation": self.simulate, "engine_ready": False}

    def preview_bytes(self):
        return self.host.preview_bytes() if self.host is not None else None

    def photo_bytes(self, photo_id):
        return self.host.photo_bytes(photo_id) if self.host is not None else None

    def video_bytes(self, video_id):
        return self.host.video_bytes(video_id) if self.host is not None else None

    def queue_command(self, nonce, command):
        if (isinstance(command, HostControl) and command.action in ("stop", "cancel")):
            with self._lock:
                self._cancel.set()
                self._user_cancel.set()
            if self.host is not None:
                return self.host.queue_command(nonce, command)
        if self.host is None or not self._ready or self._worker is not None or self._phase == "error":
            raise FeatureError("desktop_not_ready")
        return self.host.queue_command(nonce, command)

    def _start_greeting(self):
        if self._require_face_presented and not self._face_presented.is_set():
            return
        if self._worker is not None or self.host is None or self.greeting is None or self.host.session.busy:
            return
        with self._lock:
            if self._user_cancel.is_set():
                self._greeting_suppressed = True
                return
            self._cancel.clear()
        self._attempt += 1
        attempt = self._attempt
        try:
            self._lease = self.host.audio.acquire("greeting", stop=self.cancel_greeting)
        except FeatureError:
            self._state("ready", "greeting_audio_busy")
            return
        self._result = None
        self._greeting_started = None
        self._greeting_timed_out = False
        self._state("greeting", "loading_cached_greeting")
        self._greeting_deadline = self.clock() + self.greeting_timeout
        def check():
            if self._cancel.is_set() or self._user_cancel.is_set() or self._closed:
                raise TurnCancelled()
        def emit(kind, **fields):
            # Never enqueue a private WAV path, raw text, key or provider error.
            if kind not in ("prepared", "started", "ended", "cancelled", "error"):
                return
            safe = {"kind": kind, "attempt": attempt, "at": self.clock()}
            if kind == "started":
                # A subsequent cancellation may retire UI events, but cannot
                # undo the fact that the Python player already started.
                with self._lock:
                    self._ever_played = True
            if kind == "prepared":
                safe["cues"] = fields.get("cues", [])
            try:
                self._events.put_nowait(safe)
            except Full:
                self._cancel.set()
        def play():
            try:
                self.greeting.play(emit=emit, cancel_check=check)
                result = (True, "greeting_completed")
            except TurnCancelled:
                result = (False, "greeting_cancelled")
            except Exception as error:
                code = getattr(error, "code", "greeting_failed")
                result = (False, public_code(code, "greeting_failed"))
            with self._lock:
                self._result = result
        self._worker = Thread(target=play, name="bmo-desktop-greeting", daemon=True)
        try:
            self._worker.start()
        except Exception:
            self._worker = None
            self.host.audio.release(self._lease)
            self._lease = None
            self._state("ready", "greeting_worker_failed")

    def cancel_greeting(self):
        self._cancel.set()
        return self._worker is None or not self._worker.is_alive()

    def poll(self):
        if self._closed or self.exiting:
            return
        if (self._phase == "error" and self._code == "browser_disconnected"
                and self._last_browser is not None
                and self.clock() - self._last_browser <= self.disconnect_timeout
                and self._worker is None):
            # Reconnection restores controls, never greeting playback. Active
            # work was cancelled on loss and requires a fresh user request.
            self.failed = False
            self._state("ready", self._disconnect_restore_code or "greeting_cancelled")
            self._disconnect_restore_code = None
        suppress_greeting = self._user_cancel.is_set()
        if suppress_greeting:
            self._user_cancel.clear()
            self._auto_attempted = True
            self._greeting_suppressed = True
            self._retry.clear()
        if self.browser is not None:
            try:
                status = self.browser.poll()
                if isinstance(status, dict):
                    if status.get("state") == "failed":
                        self.fail(status.get("code", "browser_failed"))
                        self._exit.set()
                        return
                    if status.get("state") in ("exited", "stopped", "closed"):
                        self._exit.set()
                        return
            except Exception:
                self.fail("browser_failed")
                self._exit.set()
                return
        if not self._ready and self._phase != "error":
            if self._face_signal.is_set() and self.host is not None:
                if self.browser is not None:
                    if self.browser.mark_ready() is False:
                        self.fail("browser_failed")
                        self._exit.set()
                        return
                self._ready = True
                self._advance_progress(100, "local_app_ready")
            elif self.clock() - self._began >= self.startup_timeout:
                self.fail("face_ready_timeout" if self.host is not None else "host_startup_timeout")
        presented = not self._require_face_presented or self._face_presented.is_set()
        if (self._ready and not presented and self._phase != "error"
                and self.clock() - self._began >= self.startup_timeout):
            self.fail("face_ready_timeout")
        if self._ready and presented and not self._auto_attempted and self._phase != "error":
            self._auto_attempted = True
            self._start_greeting()
        if (self._ready and self._greeting_suppressed and self._worker is None
                and self._phase != "error" and self._code != "greeting_completed"):
            self._state("ready", "greeting_cancelled")
        if self._retry.is_set() and not suppress_greeting:
            self._retry.clear()
            if self._ready and self._phase == "ready" and self._code != "greeting_completed":
                self._greeting_suppressed = False
                self._start_greeting()
        for _ in range(32):
            try:
                event = self._events.get_nowait()
            except Empty:
                break
            if event["attempt"] != self._attempt or self._cancel.is_set():
                continue
            with self._lock:
                if event["kind"] == "prepared":
                    self._overlay = dict(turn_id="startup", utterance_id=self.run_id + ":greeting:" + str(self._attempt),
                        source="cached_greeting", phase="idle", playback="prepared", emotion="happy", cues=event["cues"])
                elif event["kind"] == "started" and self._overlay is not None:
                    self._greeting_started = event["at"]
                    self._ever_played = True
                    self._overlay.update(phase="speaking", playback="started")
                elif event["kind"] in ("ended", "cancelled", "error") and self._overlay is not None:
                    self._overlay.update(phase="idle", playback=event["kind"], cues=[])
        if self._worker is not None:
            if self._worker.is_alive() and self.clock() >= self._greeting_deadline:
                self._cancel.set()
                self._greeting_timed_out = True
                self._state("error", "greeting_timeout")
                with self._lock:
                    self._overlay = None
            if not self._worker.is_alive():
                self._worker.join()
                self._worker = None
                self.host.audio.release(self._lease)
                self._lease = None
                with self._lock:
                    self._overlay = None
                    self._presentation_epoch += 1
                    result = self._result or (False, "greeting_failed")
                    if self._greeting_timed_out:
                        result = (False, "greeting_timeout")
                if self._phase == "error" and self._code == "browser_disconnected":
                    self._disconnect_restore_code = result[1]
                elif self._phase != "error" or self._code == "greeting_timeout":
                    self._state("ready", result[1])
        if self.host is not None:
            self.host.poll()
        if (self._phase != "error" and self._last_browser is not None
                and self.clock() - self._last_browser > self.disconnect_timeout):
            self._cancel.set()
            if self.host is not None:
                self.host.stop()
            self._disconnect_restore_code = (self._code if self._phase == "ready" else "greeting_cancelled")
            self.fail("browser_disconnected")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        errors = []
        if self.greeting is not None:
            try:
                self.greeting.close()
            except Exception:
                errors.append("greeting_cleanup_failed")
        if self._worker is not None:
            try:
                self._worker.join(timeout=3.)
                alive = self._worker.is_alive()
            except Exception:
                errors.append("greeting_cleanup_failed")
                try:
                    alive = self._worker.is_alive()
                except Exception:
                    alive = True
            if alive:
                errors.append("greeting_cleanup_failed")
            elif self.host is not None:
                self.host.audio.release(self._lease)
                self._lease = self._worker = None
        for item in (self.browser, self.server, self.host):
            if item is not None:
                try:
                    item.close()
                except Exception:
                    errors.append("desktop_cleanup_failed")
        if errors:
            raise FeatureError("desktop_cleanup_failed")
