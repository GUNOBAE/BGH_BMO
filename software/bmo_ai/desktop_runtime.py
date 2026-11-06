"""Desktop lifetime around the existing feature engine, never another brain.

HTTP threads admit readiness/control only. The owner thread polls one Host and
one greeting worker. No key, device, process or network is opened on import.
"""
from collections import deque
import hashlib
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
import time
from uuid import uuid4

from .conversation import TurnCancelled
from .features.contracts import FeatureError, request_id_ok
from .features.host import HostControl


_BROWSER_CLEANUP_STAGES = frozenset({
    "not_started", "observe_identity", "inspect_owned_group", "term_owned_group",
    "wait_owned_group", "kill_owned_group", "wait_owned_root", "reap_owned_root",
    "remove_owned_profile", "complete",
})


def _browser_cleanup_detail(evidence):
    if type(evidence) is not dict:
        return {"stage": "unknown", "code": "unknown"}
    stage, code = evidence.get("stage"), evidence.get("code")
    return {"stage": stage if type(stage) is str and stage in _BROWSER_CLEANUP_STAGES else "unknown",
            "code": code if type(code) is str and code in {"pending", "unknown", "not_acquired", "resources_cleaned"} else "unknown"}


def _host_cleanup_detail(evidence):
    """Forward selected booleans only; adapter exception/code text is untrusted."""
    if type(evidence) is not dict:
        return {"code": "unknown", "evidence_valid": False}
    code = evidence.get("code")
    allowed = {"resources_cleaned", "owned_cleanup_pending", "session_cleanup_pending", "camera_cleanup_unknown"}
    detail = {"code": code if type(code) is str and code in allowed else "unknown",
              "evidence_valid": type(evidence.get("resources_cleaned")) is bool}
    groups = (("", evidence, ("resources_cleaned", "microphone_closed", "microphone_cleanup_pending")),
              ("session_", evidence.get("session"), ("worker_stopped", "voice_client_closed")),
              ("camera_", evidence.get("camera"), ("resources_cleaned", "sensor_closed", "photo_active", "close_unknown")))
    for prefix, group, fields in groups:
        if group is None:
            continue
        if type(group) is not dict:
            detail["evidence_valid"] = False
            continue
        for name in fields:
            if name in group:
                if type(group[name]) is bool:
                    detail[prefix + name] = group[name]
                else:
                    detail["evidence_valid"] = False
    return detail


def _host_cleanup_pending(detail):
    required_true = ("resources_cleaned", "microphone_closed", "session_worker_stopped",
                     "session_voice_client_closed", "camera_resources_cleaned", "camera_sensor_closed")
    required_false = ("microphone_cleanup_pending", "camera_photo_active", "camera_close_unknown")
    return (not detail["evidence_valid"]
            or any(detail.get(name) is False for name in required_true)
            or any(detail.get(name) is True for name in required_false))


def public_code(code, default="startup_failed"):
    from .greeting_cache import _CODES
    from .private_credentials import _ERRORS
    from .desktop_browser import _MESSAGES
    from .startup_sound import CODES as startup_codes
    allowed = _CODES | startup_codes | _ERRORS.keys() | _MESSAGES.keys() | {
        "startup_failed", "engine_startup_failed", "host_startup_timeout", "face_ready_timeout",
        "browser_failed", "browser_disconnected", "greeting_failed", "greeting_cancelled",
        "greeting_completed", "greeting_timeout", "missing_key", "missing_sdk"}
    return code if type(code) is str and code in allowed else default


class DesktopRuntime:
    def __init__(self, *, clock=time.monotonic, simulate=False, developer=False,
                 startup_timeout=15., greeting_timeout=30., disconnect_timeout=5., require_face_presented=False,
                 character_life=False, presence_mode="off", silent_start=False, preferences=None):
        if any(type(value) not in (float, int) or not 0 < value <= 120 for value in
               (startup_timeout, greeting_timeout, disconnect_timeout)):
            raise FeatureError("invalid_desktop_timeout")
        self.clock, self.simulate, self.developer = clock, simulate, developer
        if type(character_life) is not bool or type(silent_start) is not bool or presence_mode not in ("normal", "quiet", "sleep", "off"):
            raise FeatureError("invalid_character_configuration")
        self.character_life = character_life
        self.preferences = preferences
        if preferences is not None:
            try:
                stored = preferences.load().get('presence_mode')
                if stored in ('normal', 'quiet', 'sleep', 'off'):
                    presence_mode = stored
            except Exception:
                # Optional local preferences must never prevent conversation.
                pass
        self.silent_start = silent_start
        self._presence = None
        if character_life:
            from .character_presence import PresenceScheduler
            self._presence = PresenceScheduler(clock=clock, mode=presence_mode)
        self._requested_presence_mode = None
        self._ui_active_until = 0.
        self._ui_activity_requested = False
        self._cutscene_category = "boot"
        self._cutscene_heard = False
        self._cutscene_started_at = None
        self._cutscene_charged = False
        self._cutscene_manual = False
        self._manual_test_requested = False
        self._manual_test_epoch = 0
        self._manual_test_nonces = deque(maxlen=64)
        self._manual_test = dict(state='idle', code='presence_test_idle', attempts=0, started_count=0)
        self._idle_restore_code = None
        self._last_idle_code = None
        self._boot_attempts = self._idle_attempts = 0
        self._pending_user_input = False
        self._input_waiting = False
        self._input_epoch = 0
        self._deferred_input_result = None
        self._wake_attempted = False
        self.startup_timeout, self.greeting_timeout = startup_timeout, greeting_timeout
        self.disconnect_timeout = disconnect_timeout
        self.run_id = uuid4().hex
        self.host = self.server = self.browser = self.greeting = None
        self._lock = RLock()
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
        self._native_was_foreground = False
        self._native_return_grace_until = None
        self._events = Queue(32)
        self._worker = self._lease = None
        self._result = None
        self._attempt, self._sequence = 0, 0
        self._presentation_epoch = 0
        self._overlay = None
        self._overlay_captions = {}
        self._overlay_caption_asset = None
        self._overlay_performance = None
        self._overlay_generation = None
        self._greeting_started = None
        self._ever_played = False
        self._spoken_greeting_status = 'not_attempted'
        self._greeting_timed_out = False
        self._greeting_suppressed = False
        self._audit = deque(maxlen=64)
        self._disconnect_restore_code = None
        self._cleanup_resources = {}
        self._cleanup_errors = []
        self._cleanup_complete = False
        self._cleanup_details = {}

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
                "face_ready": self._face_signal.is_set(),
                "greeting_attempts": self._boot_attempts if self.character_life else self._attempt,
                "progress_percent": self._progress_percent,
                "progress_stage": self._progress_stage,
                "progress_status": "error" if self._phase == "error" else "complete" if self._progress_percent == 100 else "loading",
                "progress_error": self._code if self._phase == "error" else None,
                "face_presented": self._face_presented.is_set(),
                "greeting_played": self._ever_played,
                "spoken_greeting_status": self._spoken_greeting_status,
                "startup_chime_status": getattr(getattr(self.greeting, "greeting_player", self.greeting),
                                                "startup_chime_status", "NOT_ATTEMPTED"),
                "developer_enabled": self.developer, "external_api_ready": "NOT_CHECKED",
                "live_verified": False, "exit_requested": self.exiting,
                "character_life": {"enabled": self.character_life,
                    "presence": self._presence_snapshot(),
                    "manual_test": dict(self._manual_test),
                    "preferences": self._preferences_status(),
                    "settings_active": self.clock() < self._ui_active_until,
                    "last_idle_code": self._last_idle_code,
                    "voice_bank": self.greeting.bank.snapshot() if self.character_life
                        and hasattr(self.greeting, "bank") else {"status": "AUDIO_NOT_GENERATED"}}}

    def _preferences_status(self):
        if self.preferences is None:
            return {'enabled': False, 'code': 'not_configured'}
        try:
            return self.preferences.snapshot()
        except Exception:
            return {'enabled': False, 'code': 'preferences_unavailable'}

    def _set_presence_mode(self, mode):
        self._presence.set_mode(mode)
        if self.preferences is not None:
            try:
                self.preferences.update(presence_mode=mode)
            except Exception:
                pass

    def _presence_context(self):
        if self.host is None:
            return {}
        face = self.host.session.snapshot()
        return dict(emotion=face['emotion'], phase=face['phase'], activity_mode=self.host.modes.mode,
            conversation_busy=self.host.session.busy or self._input_waiting,
            audio_busy=self.host.audio.owner is not None,
            game_active=self.host.modes.mode in ('game', 'game_menu'),
            video_active=self.host.video.snapshot()['recording'],
            muted=self.host.volume.snapshot()['effective_level'] <= 0,
            settings_active=self.clock() < self._ui_active_until,
            sensitive=bool((face.get('character') or {}).get('sensitive_context')))

    def _presence_snapshot(self):
        if self._presence is None:
            return {'mode': 'off'}
        context = self._presence_context()
        value = self._presence.snapshot(**context)
        inspect = getattr(self.greeting, 'idle_status', None)
        if callable(inspect):
            assets = inspect(context.get('emotion', 'neutral'))
            value['assets'] = assets
            value['eligible_candidate_count'] = assets.get('eligible_candidate_count')
            if (value['suppression_reason'] in ('ready', 'interval')
                    and assets.get('eligible_candidate_count') == 0):
                value['suppression_reason'] = assets.get('blocked_reason', 'no_eligible_assets')
        else:
            value['eligible_candidate_count'] = None
        if self._worker is not None and self._cutscene_category == 'idle':
            value['suppression_reason'] = 'playing' if self._greeting_started is not None else 'preparing'
        return value

    def _performance_active(self):
        return (self._cutscene_category == 'idle' and self._overlay is not None
            and self._overlay.get('playback') == 'started' and self._overlay_performance is not None
            and not self._cancel.is_set() and not self._user_cancel.is_set() and not self._input_waiting
            and self.host is not None and self.host.modes.mode == 'conversation'
            and self.host.modes.generation == self._overlay_generation
            and self.host.session.snapshot()['phase'] not in ('listening', 'thinking'))

    def _clear_presence_gaze(self):
        gaze = getattr(self.host, 'gaze', None)
        if gaze is not None:
            gaze.clear('presence')

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
        elif action == "presence_mode" and self.character_life and value in ("normal", "quiet", "sleep", "off"):
            with self._lock:
                self._requested_presence_mode = value
        elif action == "presence_test" and self.character_life and value is None:
            with self._lock:
                if nonce in self._manual_test_nonces:
                    return {'status': 'accepted', 'code': 'already_requested'}
                self._manual_test_nonces.append(nonce)
                reason = self._manual_presence_blocked()
                if reason is not None:
                    # An extra click cannot overwrite the active attempt's
                    # receipt or enqueue another clip behind it.
                    if self._manual_test['state'] not in ('queued', 'preparing', 'playing'):
                        self._manual_test.update(state='blocked', code='presence_test_'+reason)
                    return {'status': 'blocked', 'code': 'presence_test_'+reason}
                self._manual_test_requested = True
                self._manual_test_epoch = self._input_epoch
                self._manual_test.update(state='queued', code='presence_test_queued')
            return {'status': 'accepted', 'code': 'presence_test_queued'}
        elif action == "ui_activity" and self.character_life and type(value) is bool:
            # The GUI renews this short lease while a dialog/HUD is open.
            # HTTP admission never touches the audio owner or worker.
            with self._lock:
                self._ui_active_until = self.clock()+10. if value else 0.
                self._ui_activity_requested = self._ui_activity_requested or value
        elif action == "wake_off" and value is None and self.character_life:
            # Owner poll performs the join/close; HTTP never touches a device.
            with self._lock:
                self._wake_off_requested = True
        else:
            raise FeatureError("invalid_desktop_control")
        return {"status": "accepted", "code": "queued"}

    def _manual_presence_blocked(self, *, queued=False):
        if (self._closed or not self._ready or self._phase != 'ready' or self.host is None
                or self.greeting is None or not callable(getattr(self.greeting, 'play_idle', None))
                or self._require_face_presented and not self._face_presented.is_set()):
            return 'not_ready'
        if self._worker is not None or self._manual_test_requested and not queued:
            return 'busy'
        if self._input_waiting or self._user_cancel.is_set():
            return 'conversation_active'
        return self._presence.manual_blocked_reason(**self._presence_context())

    def _run_manual_presence(self):
        with self._lock:
            if not self._manual_test_requested:
                return
            reason = self._manual_presence_blocked(queued=True)
            if self._manual_test_epoch != self._input_epoch:
                reason = 'conversation_active'
            self._manual_test_requested = False
            if reason is not None:
                self._manual_test.update(state='blocked', code='presence_test_'+reason)
                return
            # A deliberate test never modifies mode or the rolling automatic
            # counter. It does postpone the next natural opportunity.
            self._presence.cancel()
            self._manual_test.update(state='preparing', code='presence_test_preparing',
                attempts=self._manual_test['attempts']+1)
            if not self._start_greeting('idle', manual=True):
                self._manual_test.update(state='blocked', code='presence_test_busy')

    def heartbeat(self):
        self._last_browser = self.clock()
        self._native_return_grace_until = None
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
            cancelled = self._cancel.is_set() or self._user_cancel.is_set() or self._input_waiting
            current_owner = (self._cutscene_category != 'idle' or self.host is not None
                and self.host.modes.generation == self._overlay_generation
                and self.host.modes.mode == 'conversation' and value['phase'] not in ('listening', 'thinking'))
            if self._overlay is not None and not cancelled and current_owner:
                value.update(self._overlay)
                value["position"] = (max(0., self.clock() - self._greeting_started)
                                     if self._greeting_started is not None else 0.)
                subtitle_state = getattr(self.host.session, 'subtitle_snapshot', None) if self.host else None
                mode = subtitle_state().get('mode', 'off') if callable(subtitle_state) else 'off'
                visible = (mode in ('en', 'ko') and self._overlay.get('playback') == 'started'
                    and self._overlay.get('phase') == 'speaking' and not self._cancel.is_set()
                    and not self._user_cancel.is_set() and not self._input_waiting)
                text = self._overlay_captions.get(mode, '') if visible else ''
                value['caption'] = dict(mode=mode, language=mode if text else '', text=text)
                value.pop('caption_asset', None)
                if text and self._overlay_caption_asset is not None:
                    value['caption_asset'] = dict(self._overlay_caption_asset)
                if self._performance_active():
                    value['performance_plan'] = dict(self._overlay_performance)
            return value

    def snapshot(self):
        if self.host is not None:
            value = self.host.snapshot()
            # Gaze arbitration remains on the Host owner thread. HTTP only
            # masks an already-retired presence lease until its next poll;
            # manual, tracking and native input projections remain untouched.
            if (self.character_life and not self._performance_active()
                    and value.get('gaze', {}).get('owner') == 'presence'):
                value['gaze'] = dict(owner='idle', apply=True, x=0., y=0.)
            return value
        return {"version": 1, "simulation": self.simulate, "engine_ready": False}

    def game_resources(self):
        """Only admitted immutable packaged assets, never a browser path read."""
        resources = getattr(self.host, "game_resources", None)
        return resources() if callable(resources) else {}

    def preview_frame(self, after=None):
        return self.host.preview_frame(after=after) if self.host else None

    def preview_bytes(self):
        return self.host.preview_bytes() if self.host is not None else None

    def photo_bytes(self, photo_id):
        return self.host.photo_bytes(photo_id) if self.host is not None else None

    def video_bytes(self, video_id):
        return self.host.video_bytes(video_id) if self.host is not None else None

    def open_music_video(self, token):
        return self.host.open_music_video(token) if self.host is not None else None

    def open_video(self, video_id):
        return self.host.open_video(video_id) if self.host is not None else None

    def queue_command(self, nonce, command):
        if (isinstance(command, HostControl) and self.host is not None
                and (command.action == 'voice_batch_disable' or command.action == 'voice_batch_event'
                     and command.value.get('event') == 'idle' and command.value.get('enabled') is False)):
            with self._lock:
                self._idle_revoke_requested = True
            return self.host.queue_command(nonce, command)
        if isinstance(command, HostControl) and command.action == 'subtitles' and self.host is not None:
            # Captions are a live display setting, including during a cached
            # boot/idle utterance; they need no second audio owner.
            return self.host.queue_command(nonce, command)
        if isinstance(command, HostControl) and command.action == 'volume' and self.host is not None:
            if command.value['muted'] or command.value['level'] == 0:
                with self._lock:
                    self._output_mute_requested = True
            return self.host.queue_command(nonce, command)
        if (isinstance(command, HostControl) and command.action in ("stop", "cancel")):
            with self._lock:
                self._cancel.set()
                self._user_cancel.set()
            if self.host is not None:
                return self.host.queue_command(nonce, command)
        priority_input = isinstance(command, HostControl) and command.action in ("ptt", "question")
        if self.character_life and priority_input and self.host is not None and self._ready and self._phase != "error":
            with self._lock:
                if self._presence:
                    self._presence.cancel()
                if self._pending_user_input and command.action == "ptt":
                    return dict(self._deferred_input_result)
                result = self.host.queue_command(nonce, command)
                self._input_epoch += 1
                self._input_waiting = True
                self._greeting_suppressed = self._auto_attempted = True
                self._retry.clear()
                if self._worker is not None:
                    self._pending_user_input = True
                    self._deferred_input_result = result
                    self._cancel.set()
                    self.greeting.cancel()
                return result
        if self.host is None or not self._ready or self._worker is not None or self._phase == "error":
            raise FeatureError("desktop_not_ready")
        return self.host.queue_command(nonce, command)

    def _start_greeting(self, category="boot", *, manual=False):
        # Serialize admission with HTTP priority input. The worker only takes
        # this re-entrant lock for bounded event/result publication.
        with self._lock:
            return self._start_cutscene(category, manual=manual)

    def _start_cutscene(self, category, *, manual=False):
        if self._require_face_presented and not self._face_presented.is_set():
            return
        if self._worker is not None or self.host is None or self.greeting is None or self.host.session.busy:
            return
        with self._lock:
            if self.character_life and self._input_waiting:
                if category == "idle" and not manual:
                    self._presence.completed(False)
                return
            if self._user_cancel.is_set():
                self._greeting_suppressed = True
                return
            self._cancel.clear()
        self._attempt += 1
        self._cutscene_category = category
        self._cutscene_manual = manual
        self._cutscene_heard = False
        self._cutscene_started_at = None
        self._cutscene_charged = False
        if category == "idle":
            self._idle_restore_code = self._code
        if category == "boot":
            self._boot_attempts += 1
            self._spoken_greeting_status = 'loading_cached_greeting'
        else:
            self._idle_attempts += 1
        attempt = self._attempt
        try:
            self._lease = self.host.audio.acquire("greeting" if category == "boot" else "idle", stop=self.cancel_greeting)
        except FeatureError:
            if category == "idle" and self._presence and not manual:
                self._presence.completed(False)
            if category == 'boot':
                self._spoken_greeting_status = 'greeting_audio_busy'
            self._state("ready", "greeting_audio_busy")
            return
        self._result = None
        self._greeting_started = None
        self._overlay_captions = {}
        self._overlay_caption_asset = None
        self._overlay_performance = None
        self._overlay_generation = self.host.modes.generation if self.character_life else None
        self._clear_presence_gaze()
        self._greeting_timed_out = False
        if category == "boot":
            self._state("greeting", "loading_cached_greeting")
        self._greeting_deadline = self.clock() + self.greeting_timeout
        def check():
            if self._cancel.is_set() or self._user_cancel.is_set() or self._closed:
                raise TurnCancelled()
        def emit(kind, **fields):
            # Captions stay in this private projection queue, never audit/logs.
            # No WAV path, STT text, key or provider error is forwarded.
            if kind not in ("prepared", "started", "ended", "cancelled", "error"):
                return
            safe = {"kind": kind, "attempt": attempt, "at": self.clock()}
            if kind == "started":
                # A subsequent cancellation may retire UI events, but cannot
                # undo the fact that the Python player already started.
                with self._lock:
                    if not self._cutscene_heard:
                        self._cutscene_started_at = safe['at']
                    self._cutscene_heard = True
                    if category == "boot":
                        self._ever_played = True
                        self._spoken_greeting_status = 'greeting_playing'
            if kind == "prepared":
                safe["cues"] = fields.get("cues", [])
                from .expression_contract import FACE_EMOTIONS
                emotion = fields.get("emotion")
                safe["emotion"] = emotion if emotion in FACE_EMOTIONS else "happy"
                from .captions import clean_caption
                captions = fields.get('caption_payload')
                if type(captions) is dict:
                    safe['caption_payload'] = {lang: clean_caption(captions.get(lang)) for lang in ('en', 'ko')}
                asset = fields.get('caption_asset')
                if type(asset) is dict:
                    import re
                    if (all(type(asset.get(key)) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,96}', asset[key])
                            for key in ('clip_id', 'candidate_id'))
                            and type(asset.get('sha256')) is str and re.fullmatch(r'[a-f0-9]{64}', asset['sha256'])):
                        safe['caption_asset'] = {key: asset[key] for key in ('clip_id', 'candidate_id', 'sha256')}
                if category == 'idle':
                    from .voice_pack_context import validated_performance
                    plan = validated_performance(fields.get('performance_plan'), safe.get('caption_asset'))
                    if plan is not None:
                        safe['performance_plan'] = plan
            try:
                self._events.put_nowait(safe)
            except Full:
                self._cancel.set()
        def play():
            try:
                if category == "idle":
                    played = self.greeting.play_idle(self.host.session.snapshot()["emotion"],
                        emit=emit, cancel_check=check)
                    complete = bool(played and played.get('completed'))
                    result = (complete, 'idle_completed' if complete else 'idle_unavailable')
                else:
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
            if category == "idle" and self._presence and not manual:
                self._presence.completed(False)
            if category == 'boot':
                self._spoken_greeting_status = 'greeting_worker_failed'
            self._state("ready", "greeting_worker_failed")
            return False
        return True

    def cancel_greeting(self):
        self._cancel.set()
        with self._lock:
            self._overlay_captions = {}
            self._overlay_caption_asset = None
            self._overlay_performance = None
        if self.character_life and self.greeting is not None:
            self.greeting.cancel()
        return self._worker is None or not self._worker.is_alive()

    def poll(self):
        if self._closed or self.exiting:
            return
        if self.character_life:
            with self._lock:
                requested, self._requested_presence_mode = self._requested_presence_mode, None
                mute_output = getattr(self, '_output_mute_requested', False)
                self._output_mute_requested = False
                wake_off = getattr(self, "_wake_off_requested", False)
                self._wake_off_requested = False
                settings_input, self._ui_activity_requested = self._ui_activity_requested, False
                revoke_idle = getattr(self, '_idle_revoke_requested', False)
                self._idle_revoke_requested = False
            if mute_output:
                self.cancel_greeting()
                self._presence.cancel()
            if settings_input:
                self._presence.cancel()
                if self._cutscene_category == 'idle':
                    self.cancel_greeting()
            if revoke_idle:
                self._presence.cancel()
                if self._cutscene_category == 'idle':
                    self.cancel_greeting()
            if requested is not None:
                self._set_presence_mode(requested)
                if requested != "normal" and self._cutscene_category == "idle":
                    self.cancel_greeting()
            if wake_off and self.host is not None:
                self.host.disable_wake_word()
                self._wake_attempted = True
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
            if self._spoken_greeting_status == 'not_attempted':
                self._spoken_greeting_status = 'greeting_cancelled'
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
            if self.silent_start:
                self._greeting_suppressed = True
                self._spoken_greeting_status = 'greeting_disabled'
                self._state('ready', 'greeting_cancelled')
            else:
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
                    face = self.host.face_snapshot()
                    self._overlay = dict(turn_id="startup", utterance_id=self.run_id + ":greeting:" + str(self._attempt),
                        source="cached_idle" if self._cutscene_category == "idle" else "cached_greeting",
                        phase="idle", playback="prepared", emotion=face['emotion'], cues=event["cues"])
                    self._overlay_captions = event.get('caption_payload', {})
                    self._overlay_caption_asset = event.get('caption_asset')
                    self._overlay_performance = event.get('performance_plan')
                    self._overlay_prepared_emotion = event.get('emotion', 'happy')
                elif event["kind"] == "started" and self._overlay is not None:
                    self._greeting_started = event["at"]
                    if self._cutscene_category == "boot":
                        self._ever_played = True
                    self._overlay.update(phase="speaking", playback="started")
                    plan = self._overlay_performance
                    self._overlay['emotion'] = plan['face_emotion'] if plan else self._overlay_prepared_emotion
                    if self._performance_active() and plan.get('gaze'):
                        target = plan['gaze']
                        self.host.gaze.offer('presence', target['x'], target['y'],
                            generation=self._overlay_generation, expires_at=event['at']+target['hold_seconds'])
                elif event["kind"] in ("ended", "cancelled", "error") and self._overlay is not None:
                    self._overlay.update(phase="idle", playback=event["kind"], cues=[],
                                         emotion=self.host.face_snapshot()['emotion'])
                    self._overlay_captions = {}
                    self._overlay_caption_asset = None
                    self._overlay_performance = None
                    self._clear_presence_gaze()
        if not self._performance_active():
            self._clear_presence_gaze()
        # A start may have been followed by GP8 cancellation before UI events
        # were consumed. It still counts toward the audible 3/15-minute quota.
        # Manual tests use the identical player path but a separate counter.
        with self._lock:
            if (self._cutscene_category == 'idle' and self._presence
                    and self._cutscene_heard and not self._cutscene_charged):
                if self._cutscene_manual:
                    self._manual_test.update(state='playing', code='presence_test_playing',
                        started_count=self._manual_test['started_count']+1)
                else:
                    self._presence.started(at=self._cutscene_started_at)
                self._cutscene_charged = True
        if self._worker is not None:
            if self._worker.is_alive() and self.clock() >= self._greeting_deadline:
                self._cancel.set()
                self._greeting_timed_out = True
                self._state("error", "greeting_timeout")
                with self._lock:
                    self._overlay = None
                    self._overlay_captions = {}
                    self._overlay_caption_asset = None
                    self._overlay_performance = None
                    self._clear_presence_gaze()
            if not self._worker.is_alive():
                self._worker.join()
                self._worker = None
                self.host.audio.release(self._lease)
                self._lease = None
                with self._lock:
                    self._overlay = None
                    self._overlay_captions = {}
                    self._overlay_caption_asset = None
                    self._overlay_performance = None
                    self._clear_presence_gaze()
                    self._presentation_epoch += 1
                    result = self._result or (False, "greeting_failed")
                    if self._greeting_timed_out:
                        result = (False, "greeting_timeout")
                    if self._cutscene_category == 'boot':
                        self._spoken_greeting_status = result[1]
                if self._cutscene_category == "idle" and self._presence:
                    if self._cutscene_manual:
                        self._presence.cancel()
                        if result[0] and self._cutscene_heard:
                            state, code = 'completed', 'completed'
                        elif result[1] == 'greeting_cancelled' or self._cancel.is_set() and not self._greeting_timed_out:
                            state, code = 'cancelled', 'cancelled'
                        elif result[1] == 'idle_unavailable':
                            state, code = 'blocked', 'assets_unavailable'
                        else:
                            state, code = 'error', 'failed'
                        self._manual_test.update(state=state, code='presence_test_'+code)
                    else:
                        self._presence.completed(bool(result[0] and self._cutscene_heard))
                    self._last_idle_code = result[1]
                    if self._phase == 'error' and self._code == 'browser_disconnected':
                        self._disconnect_restore_code = self._idle_restore_code or 'greeting_completed'
                    elif self._phase != "error" or self._code == "greeting_timeout":
                        self._state("ready", self._idle_restore_code or "greeting_completed")
                elif self._phase == 'error' and self._code == 'browser_disconnected':
                    self._disconnect_restore_code = result[1]
                elif self._phase != "error" or self._code == "greeting_timeout":
                    self._state("ready", result[1])
        if self.host is not None and not (self.character_life and self._worker is not None and self._pending_user_input):
            input_epoch = self._input_epoch
            self.host.poll()
            if self._worker is None:
                with self._lock:
                    self._pending_user_input = False
                    self._deferred_input_result = None
                    if self._input_epoch == input_epoch:
                        self._input_waiting = False
        if self.character_life and self.host is not None:
            consume = getattr(self.host, 'consume_presence_request', None)
            requested = consume() if callable(consume) else None
            if requested in ('normal', 'quiet', 'sleep', 'off'):
                self._set_presence_mode(requested)
                if requested != 'normal' and self._cutscene_category == 'idle':
                    self.cancel_greeting()
            if self.host.volume.snapshot()['effective_level'] <= 0:
                self.cancel_greeting()
        if self.character_life:
            self._run_manual_presence()
        if self.character_life and self.host is not None and self._ready and self._worker is None and self._phase == "ready":
            if not self._wake_attempted and not self.host.session.busy:
                self._wake_attempted = True
                self.host.enable_wake_word()
            snapshot = self.host.session.snapshot()
            plan = snapshot.get("character") or {}
            with self._lock:
                if (not self._input_waiting and self._presence.due(emotion=snapshot["emotion"], phase=snapshot["phase"],
                        activity_mode=self.host.modes.mode, conversation_busy=self.host.session.busy,
                        audio_busy=self.host.audio.owner is not None,
                        game_active=self.host.modes.mode in ("game", "game_menu"),
                        video_active=self.host.video.snapshot()["recording"],
                        muted=self.host.volume.snapshot()['effective_level'] <= 0,
                        settings_active=self.clock() < self._ui_active_until,
                        sensitive=bool(plan.get("sensitive_context")))):
                    if callable(getattr(self.greeting, "play_idle", None)):
                        self._start_greeting("idle")
                    else:
                        self._presence.completed(False)
        native_foreground = getattr(self.host, 'native_game_foreground', False) is True
        if self._native_was_foreground and not native_foreground:
            # External fullscreen games may occlude/suspend the browser's
            # page. Give the retained face a bounded opportunity to resume its
            # real heartbeat; never fabricate one or disable process checks.
            self._native_return_grace_until = self.clock()+self.disconnect_timeout
        self._native_was_foreground = native_foreground
        if (self._phase != "error" and self._last_browser is not None
                and self.clock() - self._last_browser > self.disconnect_timeout
                and not native_foreground
                and (self._native_return_grace_until is None or self.clock() > self._native_return_grace_until)):
            self._cancel.set()
            if self.host is not None:
                self.host.stop()
            self._disconnect_restore_code = (self._code if self._phase == "ready" else "greeting_cancelled")
            self.fail("browser_disconnected")

    def cleanup_snapshot(self):
        """A scoped post-close diagnostic, not a new authenticated BMO ACK."""
        return {"version": 1, "run_id": self.run_id, "scope": "desktop_owned_components",
                "resources_cleaned": self._closed and self._cleanup_complete,
                "resources": dict(self._cleanup_resources), "codes": list(self._cleanup_errors),
                "details": {name: dict(value) for name, value in self._cleanup_details.items()},
                "physical_release_verified": False}

    def close(self):
        if self._closed and self._cleanup_complete:
            return self.cleanup_snapshot()
        self._closed = True
        self._cancel.set()
        errors = []
        resources = {}
        details = {}

        def close_component(name, item):
            if item is None:
                resources[name] = "not_acquired"
                return
            resources[name] = "unknown"
            try:
                result = item.close()
                if result is False:
                    raise FeatureError("desktop_cleanup_failed")
                report = getattr(item, "cleanup_snapshot", None)
                if callable(report):
                    evidence = report()
                    if name == "browser" and type(evidence) is dict:
                        # Never forward an adapter's arbitrary paths/messages.
                        details[name] = _browser_cleanup_detail(evidence)
                    if name == "host" and type(evidence) is dict:
                        details[name] = _host_cleanup_detail(evidence)
                        if _host_cleanup_pending(details[name]):
                            raise FeatureError("desktop_cleanup_failed")
                    if type(evidence) is dict and evidence.get("resources_cleaned") is False:
                        raise FeatureError("desktop_cleanup_failed")
                resources[name] = "released"
            except Exception:
                errors.append(name + "_cleanup_failed")
                if name in ("browser", "host"):
                    # stop() can raise after updating its bounded stage report.
                    try:
                        evidence = item.cleanup_snapshot()
                        if type(evidence) is dict:
                            details[name] = (_browser_cleanup_detail(evidence) if name == "browser"
                                             else _host_cleanup_detail(evidence))
                    except Exception:
                        pass

        if self.greeting is not None:
            close_component("greeting", self.greeting)
        else:
            resources["greeting"] = "not_acquired"
        resources["greeting_worker"] = "not_acquired"
        if self._worker is not None:
            resources["greeting_worker"] = "unknown"
            join_failed = False
            try:
                self._worker.join(timeout=3.)
                alive = self._worker.is_alive()
            except Exception:
                errors.append("greeting_cleanup_failed")
                join_failed = True
                try:
                    alive = self._worker.is_alive()
                except Exception:
                    alive = True
            if alive:
                errors.append("greeting_cleanup_failed")
            elif self.host is not None:
                try:
                    self.host.audio.release(self._lease)
                    self._lease = self._worker = None
                    if not join_failed:
                        resources["greeting_worker"] = "released"
                except Exception:
                    errors.append("greeting_cleanup_failed")
            elif not join_failed:
                self._worker = None
                resources["greeting_worker"] = "released"
        for name, item in (("browser", self.browser), ("server", self.server), ("host", self.host)):
            close_component(name, item)
        self._cleanup_resources = resources
        self._cleanup_errors = sorted(set(errors))
        self._cleanup_details = details
        self._cleanup_complete = not errors and all(status != "unknown" for status in resources.values())
        if errors:
            raise FeatureError("desktop_cleanup_failed")
        return self.cleanup_snapshot()
