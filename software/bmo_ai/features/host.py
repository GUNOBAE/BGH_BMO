"""One owner-thread feature host and persistent dialogue session.

Runtime policy is injected explicitly; live conversation never enables devices.
"""
from collections import deque
from dataclasses import dataclass, replace
import json
import hashlib
import math
from pathlib import Path
from queue import Empty, Full, Queue, PriorityQueue
from tempfile import TemporaryDirectory
from threading import Lock
import time
from types import SimpleNamespace
from uuid import uuid4, uuid5, UUID

from ..integration_profile import integration_config
from ..integration_session import IntegrationSession
from ..conversation import TurnCancelled
from .contracts import FeatureError, HostResult, request_id_ok
from .journal import ActionJournal
from .reply import FeatureClient, HostIntent
from .resources import ModeManager, AudioFocus, InputRouter, GazeArbiter, LOGICAL_INPUTS
from .runtime import RuntimePolicy


@dataclass(frozen=True, slots=True)
class HostControl:
    action: str
    value: object = None

    def __post_init__(self):
        from ..integration_session import EMOTIONS
        if self.action == "input":
            valid = (type(self.value) is dict and set(self.value) == {"key", "pressed"}
                     and type(self.value["key"]) is str and self.value["key"] in LOGICAL_INPUTS
                     and type(self.value["pressed"]) is bool)
        elif self.action == "gamepad":
            from .input.gamepad import validate_gamepad
            valid = validate_gamepad(self.value)
        elif self.action == "question":
            valid = type(self.value) is str and 0 < len(self.value) <= 512
        elif self.action == "emotion":
            valid = type(self.value) is str and self.value in (*EMOTIONS, "auto", "manual")
        elif self.action == "preview":
            valid = type(self.value) is bool
        elif self.action == "video_start":
            valid = (type(self.value) is dict and set(self.value) == {"consent"}
                     and type(self.value["consent"]) is bool)
        else:
            valid = (self.action in ("cancel", "reset", "repeat", "pause", "resume", "next", "stop", "ptt", "ptt_cancel", "video_stop")
                     and self.value is None)
        if not valid:
            raise FeatureError("invalid_control")

    def public(self):
        return {"action": self.action, "value": self.value}


class FeatureSimulatedClient:
    """Explicit fixtures only; no production natural-language classifier."""
    def __init__(self, replies=()):
        self.responses = self
        self.requests = []
        self.replies = deque(replies)

    def create(self, **request):
        self.requests.append(request)
        reply = self.replies.popleft() if self.replies else {
            "version": 1, "speech": "We can try a little simulated adventure.",
            "emotion": "happy", "gesture": "none", "intent": None}
        structured = request.get("text", {}).get("format", {}).get("name") in (
            "bmo_feature_reply_v1", "bmo_feature_reply_v2", "bmo_feature_reply_v3")
        text = json.dumps(reply) if structured else reply["speech"]
        return SimpleNamespace(status="completed", incomplete_details=None, error=None, output_text=text)

    def close(self):
        pass


class FeatureHost:
    def __init__(self, *, demo_assets=False, clock=time.monotonic, client=None, voice=None,
                 games=None, media=None, camera=None, motion=None, journal=None,
                 config=None, brain=None, runtime=None, fish_api_key=None,
                 input_source=None, input_mapping=None, pcm_transcriber=None,
                 ptt_source_factory=None, ptt_transcriber=None, video_directory=None, music_directory=None,
                 ptt_trace=False, ptt_ambient_profile=None, expressive_voice=False,
                 camera_supervised=False, photo_directory=None, camera_video_supervised=False,
                 ptt_speech_safe=False, emotion_arms=False, music_supervised=False,
                 usb_gamepad=False, character_life=False, ptt_vad_profile="baseline",
                 wake_word=False, wake_model_manifest=None, ptt_vad_mode="rms",
                 wake_question_wait=5.):
        from .games import GamesFeature
        from .media import MediaFeature, FakeMediaBackend, make_fixture_media
        from .camera import CameraFeature, DemoBackend, FixtureDetector, PhotoStore
        from .motion import MotionFeature, IMUHealthMonitor
        self.runtime = runtime if runtime is not None else RuntimePolicy()
        if (type(character_life) is not bool or type(wake_word) is not bool
                or ptt_vad_profile not in ("baseline", "balanced", "quick")
                or ptt_vad_mode not in ("rms", "webrtc")
                or type(wake_question_wait) not in (int, float)
                or not math.isfinite(wake_question_wait) or not 1 <= wake_question_wait <= 10):
            raise FeatureError("invalid_character_configuration")
        self.character_life = character_life
        self.ptt_vad_profile = ptt_vad_profile
        self.ptt_vad_mode = ptt_vad_mode
        self.wake_question_wait = float(wake_question_wait)
        self._wake_input_active = False
        self._wake_handoff = dict(phase="idle", acknowledgement="NOT_CHECKED",
                                  wait_seconds=self.wake_question_wait, trigger_preroll_reused=False)
        self._last_character_plan = None
        if not isinstance(self.runtime, RuntimePolicy):
            raise FeatureError("invalid_runtime")
        if (wake_word and (not character_life or not self.runtime.conversation_ptt_microphone)
                or wake_model_manifest is not None and (not wake_word
                    or not isinstance(wake_model_manifest, (str, Path))
                    or not 0 < len(str(wake_model_manifest)) <= 4096)):
            raise FeatureError("wake_word_requires_character_microphone")
        self._wake_requested, self._wake_armed = wake_word, False
        self._microphone_broker = None
        self._wake_ack_cache = None
        if any(type(value) is not bool for value in (music_supervised, usb_gamepad)):
            raise FeatureError("invalid_runtime")
        if music_supervised:
            if self.runtime.simulate or music_directory is None or media is not None:
                raise FeatureError("music_supervision_requires_library")
            self.runtime = replace(self.runtime, music_supervised=True)
        elif self.runtime.music_supervised:
            raise FeatureError("music_configuration_conflict")
        if (type(emotion_arms) is not bool or
                (emotion_arms and not (self.runtime.simulate or self.runtime.arms_supervised))):
            raise FeatureError("emotion_arms_requires_supervision")
        self._emotion_arms = emotion_arms
        self._last_spoken_emotion = None
        from .motion.live_feature_v2 import LiveArmMotionFeatureV2
        from .motion.legacy_host import LegacyMotionFeature
        physical_motion = (LiveArmMotionFeatureV2, LegacyMotionFeature)
        if ((self.runtime.arms_supervised and not isinstance(motion, physical_motion))
                or (isinstance(motion, physical_motion) and not self.runtime.arms_supervised)):
            raise FeatureError("arm_supervision_injection_required")
        if any(type(value) is not bool for value in (camera_supervised, camera_video_supervised)):
            raise FeatureError("invalid_camera_runtime")
        if (camera_supervised and (photo_directory is None or camera is not None)
                or photo_directory is not None and not camera_supervised
                or camera_video_supervised and (not camera_supervised or video_directory is None)):
            raise FeatureError("camera_configuration_conflict")
        if camera_supervised:
            self.runtime = replace(self.runtime, camera_supervised=True,
                                   camera_video_supervised=camera_video_supervised)
        elif self.runtime.camera_supervised or self.runtime.camera_video_supervised:
            raise FeatureError("camera_configuration_conflict")
        if not self.runtime.simulate and (client is None or demo_assets):
            raise FeatureError("invalid_runtime")
        if not self.runtime.simulate and (input_source is not None or pcm_transcriber is not None):
            raise FeatureError("input_disabled")
        if ((ptt_source_factory is not None or ptt_transcriber is not None)
                and not self.runtime.conversation_ptt_microphone):
            raise FeatureError("microphone_opt_in_required")
        if music_directory is not None and (demo_assets or media is not None):
            raise FeatureError("media_library_conflict")
        selected_music = None
        if music_directory is not None:
            from .media.library import load_library
            selected_music = load_library(Path(music_directory))
        self.config = config if config is not None else integration_config()
        self.clock = clock
        self.modes, self.audio = ModeManager(), AudioFocus()
        self.gaze = GazeArbiter(clock=clock)
        from ..character_presence import IdleGaze
        self._idle_gaze = IdleGaze(clock=clock) if character_life else None
        self._temporary = TemporaryDirectory(prefix="bmo-feature-fixtures-")
        root = Path(self._temporary.name)
        content, photos = root / "media", root / "photos"
        content.mkdir(mode=0o700)
        photos.mkdir(mode=0o700)
        self.games = games or GamesFeature(fixture=demo_assets, clock=clock)
        if music_directory is not None:
            content = Path(music_directory)
            media_items = selected_music
        else:
            media_items = make_fixture_media(content) if demo_assets and media is None else ()
        self._music_library_selected = music_directory is not None
        if music_supervised:
            from .media import AlsaWavBackend
            music_backend = AlsaWavBackend(device=self.config.speaker_device, enabled=True,
                                         volume_reviewed=True, clock=clock)
        else:
            music_backend = FakeMediaBackend(clock=clock, pausable=True)
        self.media = media or MediaFeature(content, items=media_items,
            backend=music_backend, clock=clock)
        from .camera.live import LiveCameraOptions, build_live_camera, probe_camera_capability
        self._camera_capability = probe_camera_capability()
        if camera_supervised:
            self.camera = build_live_camera(LiveCameraOptions(enabled=True, supervised=True,
                tracking=self._camera_capability["tracking_available"]), Path(photo_directory).expanduser().absolute(),
                clock=clock, on_target=self._camera_target)
        else:
            self.camera = camera or CameraFeature(backend_factory=lambda: DemoBackend(clock=clock), detector=FixtureDetector(),
                store=PhotoStore(photos), clock=clock, on_target=self._camera_target)
        from .camera.video import VideoRecorder, VideoStore
        if camera_video_supervised:
            from .camera.live_video import LiveVideoRecorder, LiveVideoStore
            self.video = LiveVideoRecorder(self.camera.broker,
                store=LiveVideoStore(Path(video_directory).expanduser().absolute()), supervised=True, clock=clock)
        else:
            self.video = VideoRecorder(self.camera.broker,
                store=VideoStore(video_directory) if video_directory is not None else None,
                simulated=self.runtime.simulate, clock=clock)
        self._active_video_request = None
        from ..system_readiness import telemetry_snapshot, button_inventory
        self._system_diagnostics = {"telemetry": telemetry_snapshot(), "buttons": button_inventory()}
        from .motion.admission import arm_readiness
        self._system_diagnostics.update(camera=self._camera_capability, arms=arm_readiness())
        self.motion = motion or MotionFeature(clock=clock)
        self.imu = IMUHealthMonitor(clock=clock)
        self.journal = journal or ActionJournal()
        self._namespace = uuid4()
        self._commands, self._voice_events = PriorityQueue(32), Queue(64)
        self._gamepad_commands = deque(maxlen=64)
        self._command_sequence = self._command_epoch = 0
        self._records, self._nonce_map = {}, {}
        # Stop/cancel are idempotent safety controls, coalesced by action in two
        # reserved slots. They never depend on the ordinary nonce ledger/queue.
        self._safety_pending, self._safety_results = {}, {}
        self._safety_ids = {action: "web_safety_" + uuid5(self._namespace, action).hex
                            for action in ("stop", "cancel")}
        self._records_lock = Lock()
        self._snapshot_lock = Lock()
        self._public = {}
        self._state_sequence = 0
        self._turn_generations, self._proposals = {}, {}
        self._voice_lease = self._game_lease = self._music_lease = None
        self._voice_control_request = None
        self._voice_cleanup = {}
        self._playback_lock, self._voice_started_turns = Lock(), set()
        self._active_game_request = self._active_media_request = None
        self._pending_photo = self._photo_result = self._photo_restore = None
        self._last_result = None
        self._result_history = {}
        self._closed = False
        self._cleanup_errors = []
        self._transition_target = None
        self._last_shell_seen = None
        self._preview = self._photo_png = None
        self._face_sequence = 0
        self._media_cue = None
        self.client = client or FeatureSimulatedClient()
        from ..turn_diagnostics import TurnDiagnostics
        self.turn_diagnostics = TurnDiagnostics(enabled=ptt_trace or character_life, clock=clock)
        self.session = IntegrationSession(self.config, self.client, simulate=self.runtime.simulate,
            brain=brain, fish_api_key=fish_api_key,
            voice=voice, clock=clock, emotion_mode="auto",
            reply_client=FeatureClient(self.client, **({"expressive": True} if expressive_voice else {}),
                                       **({"character_life": True} if character_life else {})),
            on_reply=self._reply, on_playback_event=self._playback,
            diagnostics=self.turn_diagnostics, expressive_voice=expressive_voice,
            **({"character_life": True} if character_life else {}))
        self.modes.register("conversation", enter=self._enter_conversation, leave=self._leave_conversation)
        self.modes.register("game_menu", enter=lambda _: None, leave=lambda: None)
        self.modes.register("game", enter=self._enter_game, leave=self._leave_game)
        self.modes.register("music", enter=self._enter_music, leave=self._leave_music)
        self.modes.register("photo", enter=self._enter_photo, leave=self._leave_photo)
        self.inputs = InputRouter(self.modes, stop=self.stop, talk=self._talk_input)
        self.inputs.bind("game_menu", self._menu_input)
        self.inputs.bind("game", self._game_input)
        from .input.gamepad import BrowserGamepadGate
        self.gamepad = BrowserGamepadGate(self.inputs, enabled=usb_gamepad, clock=clock)
        from .input import InputAdapter, PcmTurnGate, WakeWordGate
        self.input_adapter = (InputAdapter(self.inputs, input_source, mapping=input_mapping, clock=clock)
                              if input_source is not None else None)
        self._pcm_transcriber = pcm_transcriber
        self._pcm_sequence, self._pcm_mode_generation, self._pcm_epoch = 0, None, 0
        self.pcm = PcmTurnGate(self.audio, enabled=pcm_transcriber is not None, clock=clock,
                              playback_active=lambda: self.session.busy)
        self.wake_word = WakeWordGate()
        self.microphone = None
        self._ptt_last_stage = "microphone"
        self._ptt_generation, self._ptt_lease, self._last_ptt_press = 0, None, None
        self._ptt_turn_active = False
        self._ptt_handoff_pending = False
        self._ptt_cleanup_fault = False
        if self.runtime.conversation_ptt_microphone:
            from .input.microphone import MicPcmSource, SyntheticPcmSource, PttRecorder
            if ptt_source_factory is not None:
                source = ptt_source_factory
            elif self.runtime.simulate:
                source = lambda: SyntheticPcmSource(clock=clock)
            else:
                source = lambda: MicPcmSource(self.config.mic_device, live_opt_in=True, clock=clock)
            if wake_word:
                from .input.wake_word import LocalWakeDetector
                from .input.microphone_owner import MicrophoneBroker
                from ..wake_ack import WakeAckCache
                self._wake_ack_cache = WakeAckCache(self.config,
                    directory=Path(self._temporary.name)/"wake-ack" if self.runtime.simulate else None)
                self.wake_word = LocalWakeDetector(wake_model_manifest)
                self._microphone_broker = MicrophoneBroker(source, detector=self.wake_word,
                    on_trigger=self._queue_wake, clock=clock,
                    playback_active=lambda: self.session.snapshot()["playback"] == "started"
                        or self.audio.owner is not None
                        and not (self._ptt_turn_active and self.audio.lease is self._ptt_lease),
                    allowed=self._broker_allowed)
                source = self._microphone_broker.source_factory
            self._ptt_transcriber = ptt_transcriber or (
                (lambda *_a, **_kw: "Synthetic button question.") if self.runtime.simulate else None)
            self.microphone = PttRecorder(self.audio, source, clock=clock,
                **({"vad_mode": ptt_vad_mode} if ptt_vad_mode != "rms" else {}),
                allowed_focus=lambda: self._ptt_lease is not None and self.audio.lease is self._ptt_lease,
                playback_active=lambda: self.session.snapshot()["playback"] == "started",
                on_state=self._ptt_state,
                **({"on_timing": self.turn_diagnostics.record} if ptt_trace or character_life else {}),
                **({"silence_timeout": {"balanced": .6, "quick": .45}[ptt_vad_profile]}
                   if ptt_vad_profile != "baseline" else {}),
                **self._ptt_settings(ptt_ambient_profile, ptt_speech_safe))
        self.motion.set_context(self.session.session_id, "idle", self.modes.generation)
        self._refresh()

    def _broker_allowed(self):
        if self._wake_allowed():
            return True
        # A worker may be scheduled after admission but before it has claimed
        # its finite stream cursor. Retain the existing wake reader/preroll in
        # that narrow handoff; trigger admission remains strictly idle-only.
        if (not self._ptt_handoff_pending or not self._ptt_turn_active
                or self._closed or not self._wake_requested or not self._wake_armed
                or self._ptt_cleanup_fault or self.modes.mode != "conversation"
                or self.modes.transitioning or self._transition_target is not None):
            return False
        try:
            return (not self.video.snapshot()["recording"]
                and self.session.snapshot()["playback"] != "started"
                and (self.audio.owner is None or self._ptt_lease is not None
                     and self.audio.lease is self._ptt_lease))
        except Exception:
            return False

    def _wake_allowed(self):
        """Fail closed while boot, foreground activity or any audio owns input."""
        if (self._closed or not self._wake_requested or not self._wake_armed
                or self._ptt_cleanup_fault or self._ptt_turn_active
                or self.modes.mode != "conversation" or self.modes.transitioning
                or self._transition_target is not None or self.session.busy
                or self.audio.owner is not None):
            return False
        try:
            return self.session.snapshot()["phase"] == "idle" and not self.video.snapshot()["recording"]
        except Exception:
            return False

    def _queue_wake(self):
        # Called by the broker reader. It cannot run a session or touch owner-
        # thread state; poll() rechecks its receipt age and activity guards.
        if (not self._wake_allowed() or self._microphone_broker is None
                or not self._microphone_broker.snapshot()["wake_enabled"]
                or not self.wake_word.snapshot().get("enabled", False)):
            return False
        try:
            self.queue_command("wake_" + uuid4().hex, HostControl("ptt"), _wake=True)
            return True
        except FeatureError:
            return False

    def enable_wake_word(self):
        """Explicit post-boot hook. Missing custom assets keep finite GP8 PTT."""
        if (self._closed or self._ptt_cleanup_fault or not self._wake_requested
                or self._microphone_broker is None):
            return False
        self._wake_armed = True
        enabled = self._microphone_broker.start_wake(live_opt_in=True)
        self._refresh()
        return enabled

    def disable_wake_word(self, *, refresh=True):
        self._wake_armed = False
        if self._wake_input_active:
            self.cancel_conversation()
        clean = self._microphone_broker.stop_wake() if self._microphone_broker is not None else True
        if not clean:
            self._ptt_cleanup_fault = True
        if refresh and not self._closed:
            self._refresh()
        return clean

    def _tick_idle_gaze(self):
        if self._idle_gaze is None:
            return
        face = self.session.snapshot()
        eligible = (not self._closed and not self._ptt_cleanup_fault
            and self.modes.mode == "conversation" and not self.modes.transitioning
            and not self.session.busy and self.audio.owner is None
            and face["phase"] == "idle" and not self.video.snapshot()["recording"])
        owner = self.gaze.resolve(phase=face["phase"], generation=self.modes.generation)["owner"]
        higher = owner in ("manual", "camera", "reaction", "dialogue", "native")
        proposal = self._idle_gaze.tick(eligible=eligible, higher_owner=higher)
        if not eligible or higher:
            self.gaze.clear("idle")
        elif proposal is not None:
            self.gaze.offer("idle", *proposal, generation=self.modes.generation,
                            expires_at=self.clock() + 2.)

    def _ptt_settings(self, profile, speech_safe):
        if profile is None:
            return {"speech_safe": True, "threshold": .008} if speech_safe else {}
        from ..ptt_diagnostics import load_ambient_profile
        ambient = load_ambient_profile(profile, self.config.mic_device)
        return {"speech_safe": True, "threshold": .008, "ambient_rms": ambient}

    def _ptt_state(self, value):
        state, code = value["state"], value["reason_code"]
        if self._wake_input_active and self._wake_handoff["phase"] != "cancelled":
            if state == "listening":
                self._wake_handoff["phase"] = "question_listening"
            elif state == "speech":
                self._wake_handoff["phase"] = "speech"
            elif state in ("ready", "transcribing"):
                self._wake_handoff["phase"] = "ready"
        if state == "failed":
            stage = "capture_close" if "cleanup" in code else self._ptt_last_stage
            self.turn_diagnostics.record(stage, code, outcome="error", **value)
        elif state == "transcribing":
            self._ptt_last_stage = "stt"
            self.turn_diagnostics.record("stt", "started", outcome="started")
        elif state == "idle" and code == "transcribed":
            self.turn_diagnostics.record("stt", "ok")
        elif state == "idle" and code == "no_speech":
            self.turn_diagnostics.record("vad", "no_speech", outcome="empty", **value)
        else:
            if state == "calibrating":
                self._ptt_last_stage = "microphone"
            stage = "calibration" if state == "calibrating" else "vad"
            public = ("ambient_verified" if value.get("ambient_verified") else "calibration_unverified") if state == "calibrating" else "speech_detected" if state == "speech" else "waiting_speech" if state == "listening" else "capture_ready" if state == "ready" else code
            self.turn_diagnostics.record(stage, public, **value)

    def _put_voice(self, value):
        try:
            self._voice_events.put_nowait(value)
        except Full:
            pass  # Drop decoration/intent, never hold up the voice owner.

    def _reply(self, reply, turn_id, utterance_id):
        self._put_voice(("reply", turn_id, (reply.gesture, reply.intent,
            getattr(reply, "character_plan", None)), self.clock()))

    def _playback(self, kind, turn_id, at):
        if kind == "started":
            with self._playback_lock:
                self._voice_started_turns.add(turn_id)
        self._put_voice((kind, turn_id, None, at))

    def _camera_target(self, target):
        if target.generation != self.modes.generation:
            return
        if target.status in ("tracking", "grace"):
            self.gaze.offer("camera", target.x, target.y, generation=target.generation,
                            expires_at=target.valid_until)
        else:
            self.gaze.clear("camera")

    def _transition(self, mode, context=None, *, force=False):
        if mode != self.modes.mode or force:
            self.gamepad.release()
            self._cancel_video("video_mode_changed")
            if self.microphone is not None:
                self.microphone.cancel()
            self.pcm.cancel("mode_changed")
            self._pcm_mode_generation = None
            self._pcm_epoch += 1
        self._transition_target = mode
        self.gaze.clear()
        self.motion.cancel()
        try:
            self.modes.transition(mode, context, force=force)
        finally:
            self._transition_target = None
        self.motion.set_context(self.session.session_id,
            self.session.snapshot()["turn_id"] or "idle", self.modes.generation)

    def _enter_conversation(self, _):
        if self.session.busy:
            raise FeatureError("conversation_busy")

    def _leave_conversation(self):
        if self.session.busy:
            self.cancel_conversation()
            raise FeatureError("conversation_busy")
        if self._transition_target in ("game", "game_menu", "music"):
            self.camera.off()

    def _check_start(self, result):
        if result.status not in ("succeeded", "simulated", "running", "accepted"):
            raise FeatureError(result.code)
        return result

    def _enter_game(self, context):
        self._game_lease = self.audio.acquire("game", stop=self._stop_game_audio)
        result = self.games.start(context["target"], request_id=context["request_id"],
                                  mode_generation=self.modes.generation)
        self._check_start(result)
        self._active_game_request = context["request_id"]

    def _stop_game_audio(self):
        return self.games.stop("mode_change").status != "unknown"

    def _leave_game(self):
        if not self._stop_game_audio():
            raise FeatureError("cleanup_failed")
        self.audio.release(self._game_lease)
        self._game_lease = self._active_game_request = None

    def _enter_music(self, context):
        self._music_lease = self.audio.acquire("music", stop=self._stop_music_audio)
        self._check_start(self.media.start(context["target"], request_id=context["request_id"],
                                           mode_generation=self.modes.generation))
        self._active_media_request = context["request_id"]

    def _stop_music_audio(self):
        result = self.media.stop("mode_change")
        if self.runtime.music_supervised:
            self._finish_media_terminal(result.request_id, "mode_change", result.status != "unknown")
        return result.status != "unknown"

    def _finish_media_terminal(self, request, code, released):
        previous = self._result_history.get(request)
        if previous is None or previous.status != "running" or previous.simulated:
            return
        natural = code == "ended"
        failed = code == "media_failed"
        status = "unknown" if not released else "failed" if failed else "succeeded" if natural else "cancelled"
        self._finish_result(HostResult(request, status,
            "cleanup_failed" if not released else "media_failed" if failed else "media_completed" if natural else "media_stopped",
            simulated=False, executed=previous.executed, completed=released and not failed,
            completion="unknown" if not released or failed else "playback_finished" if natural else "stopped"))

    def _leave_music(self):
        if not self._stop_music_audio():
            raise FeatureError("cleanup_failed")
        self.audio.release(self._music_lease)
        self._music_lease = self._active_media_request = None

    def _enter_photo(self, context):
        self._photo_restore = context.get("restore", (False, False))
        self.camera.set_preview(True, generation=self.modes.generation)
        self._pending_photo = {"request_id": context["request_id"],
            "generation": self.modes.generation, "deadline": self.clock() + 3.0}
        self._photo_result = None

    def _leave_photo(self):
        if self._pending_photo is not None:
            request = self._pending_photo["request_id"]
            self.camera.cancel_photo(request)
            self._finish_result(HostResult(request, "cancelled", "photo_cancelled"))
            self._pending_photo = None
        self.camera.off()
        if self._transition_target == "conversation" and self._photo_restore:
            tracking, preview = self._photo_restore
            self.camera.set_tracking(tracking, generation=self.modes.generation)
            self.camera.set_preview(preview, generation=self.modes.generation)
        self._photo_restore = None

    def _remember(self, result):
        self._last_result = result.public()
        self._result_history[result.request_id] = result
        if len(self._result_history) > 512:
            del self._result_history[next(iter(self._result_history))]
        with self._records_lock:
            record = self._records.get(result.request_id)
            if record is not None:
                record["result"] = result.public()
            if result.request_id in self._safety_ids.values():
                self._safety_results[result.request_id] = result.public()
        return result

    def _finish_result(self, result):
        try:
            self.journal.finish(result.request_id, result.status, result.code)
        except Exception:
            # The resource may already have started/saved. Do not report it as
            # unexecuted, complete, or retry it because the journal write failed.
            result = HostResult(result.request_id, "unknown", "journal_unavailable",
                                completion="unknown")
        return self._remember(result)

    def execute(self, intent, *, request_id=None, expected_generation=None):
        if not isinstance(intent, HostIntent) or self._closed:
            raise FeatureError("invalid_request")
        request = request_id or "host_" + uuid4().hex
        try:
            entry = self.journal.begin(request, intent.public())
        except Exception as error:
            code = error.code if isinstance(error, FeatureError) else "journal_unavailable"
            result = HostResult(request, "rejected", code)
            if code == "request_conflict":
                self._last_result = result.public()
                return result  # Keep the original operation's idempotent receipt.
            return self._remember(result)
        if entry["duplicate"]:
            result = self._result_history.get(request) or HostResult(request, entry["status"], entry["code"],
                completed=entry["status"] == "simulated", completion="simulated" if entry["status"] == "simulated" else "unknown")
            return self._remember(result)
        if expected_generation is not None and expected_generation != self.modes.generation:
            return self._finish_result(HostResult(request, "rejected", "stale_mode"))
        try:
            name = intent.name
            media_was_active = self.media.snapshot()["active_id"] is not None
            capability = {"launch_game": "external_games", "play_song": "music",
                "take_photo": "camera", "start_face_tracking": "camera", "arm_gesture": "arms"}.get(name)
            if capability is not None:
                self.runtime.require(capability)
                if capability == "camera" and not self.runtime.simulate and request.startswith("model_"):
                    raise FeatureError("camera_human_action_required")
            if name in ("launch_game", "play_song"):
                items = self.games.catalog() if name == "launch_game" else self.media.catalog()
                item = next((item for item in items if item.id == intent.target), None)
                if item is None or item.readiness != "READY":
                    raise FeatureError("unknown_game" if item is None and name == "launch_game" else
                                       "unknown_media" if item is None else item.reason_code)
                if item.simulated is not True and not (name == "play_song" and self.runtime.music_supervised):
                    raise FeatureError("real_disabled")
            if name == "open_game_menu":
                self._transition("game_menu")
            elif name == "launch_game":
                self._transition("game", {"target": intent.target, "request_id": request}, force=True)
            elif name == "exit_game":
                self._transition("game_menu")
            elif name == "return_to_face":
                self._cancel_video("video_return_to_face")
                self._transition("conversation")
            elif name == "play_song":
                self._transition("music", {"target": intent.target, "request_id": request}, force=True)
            elif name == "stop_media":
                if self.modes.mode == "music":
                    self._transition("conversation")
                else:
                    self.media.stop()
            elif name == "take_photo":
                self._cancel_video("video_photo_transition")
                state = self.camera.snapshot()
                restore = (self._photo_restore if self.modes.mode == "photo" else
                           (state.get("tracking", False), state.get("preview", False)))
                self._transition("photo", {"request_id": request, "restore": restore}, force=True)
                result = HostResult(request, "running", "photo_countdown",
                    simulated=self.runtime.simulate, executed=not self.runtime.simulate,
                    completion="not_started" if self.runtime.simulate else "camera_preview")
                return self._finish_result(result)
            elif name == "start_face_tracking":
                self._cancel_video("video_tracking_transition")
                self._transition("conversation")
                self.camera.set_tracking(True, generation=self.modes.generation)
            elif name == "stop_face_tracking":
                self._cancel_video("video_camera_off")
                self.camera.off()
                self.gaze.clear("camera")
            elif name == "arm_gesture":
                turn = self.session.snapshot()["turn_id"] or "idle"
                self.motion.set_context(self.session.session_id, turn, self.modes.generation)
                result = self.motion.submit(intent.target, request_id=request, session_id=self.session.session_id,
                    turn_id=turn, mode_generation=self.modes.generation, started_at=None, explicit=True)
                return self._finish_result(result)
            if name in ("start_face_tracking", "stop_face_tracking") and not self.runtime.simulate:
                result = HostResult(request, "succeeded", "ok", simulated=False,
                                    executed=True, completed=True, completion="camera_request_applied")
            elif name == "play_song" and self.runtime.music_supervised:
                result = HostResult(request, "running", "playback_started", simulated=False,
                                    executed=True, completed=False, completion="running")
            elif name == "stop_media" and self.runtime.music_supervised:
                result = HostResult(request, "succeeded", "media_stopped", simulated=False,
                                    executed=media_was_active, completed=True, completion="stopped")
            else:
                result = HostResult(request, "simulated", "ok", completed=True, completion="simulated")
        except FeatureError as error:
            result = HostResult(request, "rejected", error.code)
        except Exception:
            result = HostResult(request, "failed", "operation_failed")
        return self._finish_result(result)

    def queue_command(self, nonce, intent, *, _wake=False):
        """Thread-safe HTTP admission; only poll() executes side effects."""
        if self._closed:
            raise FeatureError("feature_closed")
        if not request_id_ok(nonce) or not isinstance(intent, (HostIntent, HostControl)):
            raise FeatureError("invalid_request")
        if (type(_wake) is not bool or _wake and not
                (isinstance(intent, HostControl) and intent.action == "ptt")):
            raise FeatureError("invalid_request")
        request = "web_" + uuid5(self._namespace, nonce).hex
        fingerprint = json.dumps(intent.public(), sort_keys=True)
        with self._records_lock:
            if isinstance(intent, HostControl) and intent.action == "gamepad":
                if not self.gamepad.enabled:
                    raise FeatureError("gamepad_disabled")
                if len(self._gamepad_commands) >= self._gamepad_commands.maxlen:
                    raise FeatureError("gamepad_queue_full")
                generation = self.snapshot()["mode_generation"]
                self._gamepad_commands.append((request, intent, generation, self._command_epoch, self.clock()))
                # Streaming edges never consume the bounded durable action ledger.
                # Their sequence high-water remains in the owner-thread gate.
                return HostResult(request, "accepted", "gamepad_queued").public()
            safety = (intent.action if isinstance(intent, HostControl) and intent.action in ("stop", "cancel")
                      else "stop" if isinstance(intent, HostControl) and intent.action == "input"
                      and intent.value == {"key": "STOP", "pressed": True} else None)
            if safety is not None:
                request = self._safety_ids[safety]
                if safety in self._safety_pending or request in self._voice_cleanup:
                    return dict(self._safety_results[request])
                result = HostResult(request, "accepted", "queued").public()
                self._safety_results[request] = result
                self._safety_pending[safety] = (request, HostControl(safety))
                return dict(result)
            if request in self._records:
                previous = self._records[request]
                if previous["fingerprint"] != fingerprint:
                    raise FeatureError("request_conflict")
                return dict(previous["result"])
            if len(self._records) >= 512:
                raise FeatureError("request_capacity")
            generation = self.snapshot()["mode_generation"]
            result = HostResult(request, "accepted", "queued").public()
            try:
                self._command_sequence += 1
                self._commands.put_nowait((1, self._command_sequence, request, intent, generation, self._command_epoch))
            except Full:
                raise FeatureError("command_queue_full") from None
            self._records[request] = {"fingerprint": fingerprint, "result": result,
                                      "received_at": self.clock(), "wake": _wake,
                                      "input_turn": self.session.worker.turn}
            return dict(result)

    def _await_voice_cleanup(self, request, action):
        if len(self._voice_cleanup) >= 64 and request not in self._voice_cleanup:
            raise FeatureError("cleanup_capacity")
        self._voice_cleanup[request] = action
        return self._remember(HostResult(request, "running", action + "_pending",
                                        simulated=self.runtime.simulate))

    def control(self, control, request):
        if self._closed:
            return self._remember(HostResult(request, "rejected", "feature_closed"))
        try:
            action, value = control.action, control.value
            if action == "question":
                if not self.start_question(value):
                    raise FeatureError("conversation_busy")
                self._voice_control_request = request
                return self._remember(HostResult(request, "running", "question_started"))
            elif action == "ptt":
                previous_turn = self.session.worker.turn
                with self._records_lock:
                    received = self._records.get(request, {}).get("received_at")
                if not self.ptt_button(received_at=received):
                    raise FeatureError("microphone_opt_in_required" if self.microphone is None else "ptt_unavailable")
                if self.session.worker.turn != previous_turn:
                    self._voice_control_request = request
                    return self._remember(HostResult(request, "running", "ptt_started",
                                                     simulated=self.runtime.simulate))
                return self._remember(HostResult(request, "accepted", "ptt_input_accepted",
                                                 simulated=self.runtime.simulate))
            elif action == "ptt_cancel":
                if self._ptt_turn_active:
                    self.cancel_conversation()
            elif action == "video_start":
                self.runtime.require("camera_video")
                if self.modes.mode != "conversation" or self.session.busy or self.audio.owner is not None:
                    raise FeatureError("video_mode_busy")
                result = self.video.start_video_recording(request,
                    generation=self.modes.generation, consent=value["consent"])
                if result.status == "running" and not self.video.snapshot()["recording"]:
                    raise FeatureError("video_busy")
                if result.status == "running":
                    self._active_video_request = request
                return self._remember(self._video_host_result(result))
            elif action == "video_stop":
                self.runtime.require("camera_video")
                state = self.video.snapshot()
                original = state["result"]
                if original is None or original["status"] == "running" and not state["recording"]:
                    raise FeatureError("video_not_recording")
                result = self.video.stop_video_recording(original["request_id"], generation=self.modes.generation)
                self._remember(self._video_host_result(result))
                self._active_video_request = None
                return self._remember(self._video_host_result(result, request=request))
            elif action == "cancel":
                self.cancel_conversation()
                self._cancel_video("video_cancelled")
                if self.modes.mode == "photo":
                    self._transition("conversation")
                if self.session.busy:
                    return self._await_voice_cleanup(request, "cancel")
                if self._ptt_cleanup_fault:
                    raise FeatureError("pcm_cleanup_failed")
            elif action == "reset":
                stopped = self.stop()
                self.session.reset()
                self._last_spoken_emotion = None
                if self.session.busy:
                    return self._await_voice_cleanup(request, "reset")
                if not stopped:
                    raise FeatureError("cleanup_failed")
            elif action == "stop":
                if not self.stop():
                    if self.session.busy and self.modes.mode == "conversation":
                        return self._await_voice_cleanup(request, "stop")
                    raise FeatureError("cleanup_failed")
            elif action == "repeat":
                if self._ptt_cleanup_fault:
                    raise FeatureError("pcm_cleanup_failed")
                if self.video.snapshot()["recording"]:
                    raise FeatureError("video_mode_busy")
                if self.session.busy or not self.session.voice.has_cache:
                    raise FeatureError("no_cached_voice")
                self._transition("conversation")
                self._voice_lease = self.audio.acquire("conversation", stop=self.cancel_conversation)
                if not self.session.repeat():
                    self.audio.release(self._voice_lease)
                    self._voice_lease = None
                    raise FeatureError("no_cached_voice")
                self._voice_control_request = request
                return self._remember(HostResult(request, "running", "repeat_started"))
            elif action == "emotion":
                self.session.set_emotion(value)
            elif action == "preview":
                if value:
                    self.runtime.require("camera")
                    self._cancel_video("video_preview_transition")
                if self.modes.mode not in ("conversation", "photo"):
                    raise FeatureError("camera_mode_unsupported")
                self.camera.set_preview(value, generation=self.modes.generation)
                if not self.runtime.simulate:
                    return self._remember(HostResult(request, "succeeded", "ok", simulated=False,
                        executed=True, completed=True, completion="camera_request_applied"))
            elif action == "input":
                if value == {"key": "TALK", "pressed": True} and self.microphone is not None:
                    with self._records_lock:
                        received = self._records.get(request, {}).get("received_at")
                    outcome = self.ptt_button(received_at=received)
                else:
                    outcome = self.inputs.route(value["key"], value["pressed"], now=self.clock())
                if outcome is False and value["pressed"] and value["key"] in ("STOP", "TALK"):
                    if value["key"] == "STOP" and self.session.busy and self.modes.mode == "conversation":
                        return self._await_voice_cleanup(request, "stop")
                    raise FeatureError("cleanup_failed" if value["key"] == "STOP" else "conversation_busy")
                if isinstance(outcome, HostResult):
                    return self._remember(HostResult(request, outcome.status, outcome.code,
                        outcome.simulated, outcome.executed, outcome.completed, outcome.completion))
            elif action == "gamepad":
                outcome = self.gamepad.feed(value)
                if isinstance(outcome, HostResult):
                    return self._remember(HostResult(request, outcome.status, outcome.code,
                        outcome.simulated, outcome.executed, outcome.completed, outcome.completion))
            elif action in ("pause", "resume"):
                if self.modes.mode != "music":
                    raise FeatureError("media_inactive")
                outcome = getattr(self.media, action)()
                return self._remember(HostResult(request, outcome.status, outcome.code,
                    outcome.simulated, outcome.executed, outcome.completed, outcome.completion))
            elif action == "next":
                if self.modes.mode != "music":
                    raise FeatureError("media_inactive")
                entry = self.journal.begin(request, {"control": "next"})
                if entry["duplicate"]:
                    return self._remember(self._result_history.get(request) or HostResult(
                        request, entry["status"], entry["code"]))
                previous = self._active_media_request
                previous_asset = self.media.snapshot()["active_id"]
                result = self.media.next(request_id=request, mode_generation=self.modes.generation)
                if (self.runtime.music_supervised and previous is not None
                        and previous_asset != self.media.snapshot()["active_id"]):
                    # next() intentionally emits no intermediate ended event.
                    # Its old descriptor is released before a different asset
                    # can become active; retire that original action here.
                    self._finish_media_terminal(previous, "next_track", True)
                if result.status in ("simulated", "running", "succeeded") and self.media.snapshot()["active_id"] is not None:
                    self._active_media_request = request
                return self._finish_result(result)
            result = HostResult(request, "simulated", "ok", completed=True, completion="simulated")
        except FeatureError as error:
            result = HostResult(request, "rejected", error.code)
        except Exception:
            result = HostResult(request, "failed", "operation_failed")
        return self._remember(result)

    def _menu_input(self, key, pressed, now):
        outcome = self.games.menu_input(key, pressed, now)
        if outcome["action"] == "launch":
            return self.execute(HostIntent("launch_game", outcome["catalog_id"]))
        if outcome["action"] == "return_to_face":
            return self.execute(HostIntent("return_to_face"))
        return outcome

    def _game_input(self, key, pressed, now):
        return self.games.game_input("PAD_DISCONNECTED" if key == "PAD_LOST" else key, pressed, now)

    @staticmethod
    def _video_host_result(result, *, request=None):
        return HostResult(request or result.request_id, result.status, result.code,
            simulated=result.simulated, executed=result.executed, completed=result.completed,
            completion=("simulated" if result.simulated else "camera_video_saved") if result.completed else "unknown" if result.status == "unknown" else "not_started")

    def _cancel_video(self, reason):
        state = self.video.snapshot()
        if state["recording"]:
            result = self.video.cancel(reason)
            self._remember(self._video_host_result(result))
            self._active_video_request = None
        if self.video.snapshot()["code"] == "video_close_failed":
            raise FeatureError("video_close_failed")

    def start_question(self, message="Synthetic feature question."):
        return self._begin_conversation(message=message)

    def _begin_conversation(self, *, message=None, input_provider=None):
        if self._closed or self.session.busy or self._ptt_cleanup_fault or self.video.snapshot()["recording"]:
            return False
        self._transition("conversation")
        self.session.expression_client.set_context({"version": 1, "simulation": self.runtime.simulate,
            "real_features_enabled": self.runtime.arms_supervised,
            "games": [{"id": item.id, "readiness": item.readiness} for item in self.games.catalog()],
            "songs": [{"id": item.id, "readiness": item.readiness} for item in self.media.catalog()],
            "camera": "synthetic_only" if self.runtime.simulate else "human_action_only" if self.runtime.camera_supervised else "denied",
            "arm": ("mock_ack_position_unverified" if self.runtime.simulate
                    else "explicit_supervised_position_unverified" if self.runtime.arms_supervised
                    else "denied"),
            "imu": "physical_unverified_no_control"})
        self._voice_lease = self.audio.acquire("conversation", stop=self.cancel_conversation)
        if input_provider is not None:
            self._ptt_lease = self._voice_lease
        turn_id = f"{self.session.session_id}:{self.session.worker.turn + 1}"
        self._turn_generations[turn_id] = self.modes.generation
        self.motion.set_context(self.session.session_id, turn_id, self.modes.generation)
        try:
            started = (self.session.start_with_input(input_provider) if input_provider is not None
                       else self.session.start(message))
        except BaseException:
            self.audio.release(self._voice_lease)
            self._voice_lease = self._ptt_lease = None
            self._ptt_turn_active = False
            self._ptt_handoff_pending = False
            raise
        if not started:
            self.audio.release(self._voice_lease)
            self._voice_lease = self._ptt_lease = None
            return False
        return True

    def _talk_input(self):
        if self.modes.mode != "conversation":
            return False
        return self.ptt_button() if self.microphone is not None else self.start_question()

    def _confirm_wake(self, check):
        """Optional approved local ACK on the existing voice/player, no synthesis.

        A follow-up already arriving after the trigger wins over the cutscene.
        Missing assets use the existing face listening activity and clear hint.
        """
        self._wake_handoff.update(phase="confirming", acknowledgement="MISSING")
        check()
        if self._microphone_broker.followup_started():
            self._wake_handoff["acknowledgement"] = "SKIPPED_FOLLOWUP"
            return
        with self._wake_ack_cache.load(simulate=self.runtime.simulate, cancel_check=check) as asset:
            if asset is None:
                return
            check()
            if self._microphone_broker.followup_started():
                self._wake_handoff["acknowledgement"] = "SKIPPED_FOLLOWUP"
                return
            voice = self.session.voice
            if not voice._speaking.acquire(blocking=False):
                self._wake_handoff["acknowledgement"] = "SKIPPED_BUSY"
                return
            voice._idle.clear()
            suspended = False
            previous_timing = voice.on_timing
            try:
                if self._microphone_broker.suspend_confirmation() is False:
                    self._wake_handoff["acknowledgement"] = "SKIPPED_FOLLOWUP"
                    return
                suspended = True
                check()
                self._wake_handoff["acknowledgement"] = "PLAYING"
                # ACK is input confirmation, never the question's first audio.
                # Preserve dialogue/repeat timing and cache on this same player.
                voice.on_timing = None
                voice._play(asset.path, "cached_wake_ack", asset.info, asset.cues,
                    check, lambda *_a, **_kw: None, None,
                    dict(emotion=self.session.snapshot()["emotion"], emotion_validation="none", notice=False))
                check()
                self._wake_handoff["acknowledgement"] = "PLAYED"
            except Exception as error:
                from ..audio import AudioPlaybackError
                if isinstance(error, AudioPlaybackError) and error.code == "audio_cleanup_failed":
                    self._ptt_cleanup_fault = True
                if not isinstance(error, AudioPlaybackError) or error.code == "audio_cleanup_failed":
                    raise
                check()
                self._wake_handoff["acknowledgement"] = "FAILED_VISUAL"
            finally:
                try:
                    if suspended:
                        self._microphone_broker.resume_question()
                finally:
                    voice.on_timing = previous_timing
                    voice._speaking.release()
                    voice._idle.set()

    def ptt_button(self, *, received_at=None, _wake=False):
        """One edge starts input; another ends capture or cancels output.

        HID Enter identity is verified by the human later. No RP2040 pin or
        GP7 binding is changed here. Games keep their existing Enter controls.
        """
        if (self._closed or self.modes.mode != "conversation" or self.microphone is None
                or self._ptt_cleanup_fault or self.video.snapshot()["recording"]):
            return False
        self.runtime.require("conversation_ptt_microphone")
        now = self.clock()
        if not _wake and self._last_ptt_press is not None and 0 <= now - self._last_ptt_press < .25:
            return True  # Coalesce duplicate browser/queued edges, never auto recapture.
        if not _wake:
            self._last_ptt_press = now
        if self.session.busy:
            if _wake:
                return False
            if self._ptt_turn_active and self.microphone.finish():
                return True
            self.cancel_conversation()
            return True
        if self.audio.owner is not None:
            return False
        if self.turn_diagnostics.enabled:
            # The HTTP/control queue's clock belongs to this host, unlike the
            # unmeasured physical GP8 edge or a browser's separate clock.
            self.turn_diagnostics.mark_input(now if received_at is None else received_at)
        self._ptt_generation += 1
        generation = self._ptt_generation
        capture_id = "ptt_" + uuid4().hex
        self._ptt_handoff_pending = True
        self._ptt_turn_active = True
        self._wake_input_active = _wake
        def provider(check):
            self._ptt_last_stage = "microphone"
            try:
                check()
                if _wake:
                    self._confirm_wake(check)
                    check()
                    self._wake_handoff["phase"] = "waiting_wake_end"
                self.turn_diagnostics.record("microphone", "started", outcome="started",
                                             sample_rate=16000, channels=1)
                capture = self.microphone.capture(capture_id, generation=generation, cancel_check=check,
                    **({"source_factory": self._microphone_broker.wake_source_factory,
                        "no_speech_timeout": self.wake_question_wait, "vad_mode": "webrtc"} if _wake else {}))
            except TurnCancelled:
                self.turn_diagnostics.record("microphone", "cancelled", outcome="cancelled")
                raise
            except Exception as error:
                code = getattr(error, "code", "microphone_capture_failed")
                stage = "capture_close" if code in ("pcm_cleanup_failed", "pcm_cleanup_pending", "audio_cleanup_failed") else "microphone"
                self.turn_diagnostics.record(stage, code, outcome="error")
                raise
            finally:
                # During STT/AI/TTS preparation there is no input handoff to
                # protect. The broker must suppress its persistent microphone.
                self._ptt_handoff_pending = False
            check()
            if capture.status != "ready":
                if _wake and self._wake_handoff["phase"] != "cancelled":
                    self._wake_handoff["phase"] = "timed_out" if capture.code == "no_speech" else "idle"
                return ""  # No speech means zero STT/brain/TTS calls.
            try:
                text = self.microphone.transcribe_capture(capture, self.client,
                    model=self.config.stt_model, language="ko" if self.config.language == "ko" else None,
                    cancel_check=check, transcriber=self._ptt_transcriber)
            except TurnCancelled:
                self.turn_diagnostics.record("stt", "cancelled", outcome="cancelled")
                raise
            except Exception as error:
                self.turn_diagnostics.record("stt", getattr(error, "code", "transcription_failed"), outcome="error")
                raise
            check()
            if not text or not text.strip():
                self.turn_diagnostics.record("stt", "stt_empty", outcome="empty")
            return text or ""
        try:
            result = self._begin_conversation(input_provider=provider)
        except FeatureError:
            result = False
        if not result:
            self._ptt_turn_active = False
            self._ptt_handoff_pending = False
            self._wake_input_active = False
        return result

    def start_ptt(self, capture_id):
        """Injected PCM development path only; no automatic microphone open.

        The source feeds host.pcm with its returned snapshot generation. A ready
        result uses an injected offline transcriber once, then the same session.
        """
        if self._closed or self.modes.mode != "conversation" or self.session.busy:
            return False
        self._pcm_sequence += 1
        if self.pcm.start_ptt(capture_id, generation=self._pcm_sequence):
            self._pcm_mode_generation = self.modes.generation
            self._pcm_epoch += 1
            return True
        return False

    def cancel_conversation(self):
        if self._wake_input_active:
            self._wake_handoff["phase"] = "cancelled"
        self._ptt_handoff_pending = False
        with self._records_lock:
            self._command_epoch += 1
        self.pcm.cancel("cancelled")
        if self.microphone is not None:
            self.microphone.cancel()
        self._pcm_mode_generation = None
        self._pcm_epoch += 1
        self._proposals.clear()
        self._turn_generations.clear()
        self.gaze.clear("reaction")
        if self.session.busy:
            self.session.cancel()
        self.motion.cancel()
        return not self.session.busy and not self._ptt_cleanup_fault

    def stop(self):
        self.cancel_conversation()
        success = self.disable_wake_word(refresh=False)
        try:
            self._cancel_video("video_cancelled")
            # A stop also retires commands admitted before it. A late queued
            # launch must not immediately reactivate a stopped resource.
            self._transition("conversation", force=True)
        except FeatureError:
            success = False
        try:
            self.motion.stop()
        except Exception:
            success = False
        try:
            self.camera.off()
            success = success and not self.camera.snapshot()["on"]
        except Exception:
            success = False
        self.gaze.clear()
        return success and not self.session.busy and not self._ptt_cleanup_fault

    def heartbeat(self):
        # Shell liveness is independent of whether the retained face iframe is
        # visible behind a game/photo/music foreground.
        self._last_shell_seen = self.clock()
        self.session.browser_snapshot()

    def face_snapshot(self):
        self.heartbeat()
        value = self.session.snapshot()
        with self._snapshot_lock:
            self._face_sequence += 1
            value["sequence"] = self._face_sequence
            cue = self._media_cue
            if cue is not None:
                value.update(turn_id=f"{self.session.session_id}:music:{cue['mode_generation']}",
                    utterance_id=f"{self.session.session_id}:music:{cue['request_id']}",
                    phase="speaking" if cue["playing"] else "idle", playback="started",
                    source="provided_media_cues", position=0.,
                    cues=[{"time": 0., "mouth": cue["mouth"]}])
        return value

    def poll(self):
        if self._closed:
            return
        if self.input_adapter is not None:
            self.input_adapter.poll()
        with self._records_lock:
            safety, self._safety_pending = self._safety_pending, {}
        for action in ("stop", "cancel"):
            if action in safety:
                request, control = safety[action]
                self.control(control, request)
        with self._records_lock:
            gamepad, self._gamepad_commands = self._gamepad_commands, deque(maxlen=64)
        for request, control, generation, epoch, received in gamepad:
            age = self.clock() - received
            if (epoch != self._command_epoch or generation != self.modes.generation
                    or not 0 <= age <= .5):
                self.gamepad.release(disconnected=True)
                continue
            self.control(control, request)
        self.gamepad.poll()
        for _ in range(32):
            try:
                _, _, request, intent, generation, epoch = self._commands.get_nowait()
            except Empty:
                break
            if epoch != self._command_epoch:
                self._remember(HostResult(request, "rejected", "stale_mode"))
                continue
            if isinstance(intent, HostControl):
                with self._records_lock:
                    receipt = self._records.get(request, {})
                if receipt.get("wake"):
                    age = self.clock() - receipt["received_at"]
                    if (not 0 <= age <= .5 or not self._wake_allowed()
                            or receipt.get("input_turn") != self.session.worker.turn):
                        self._remember(HostResult(request, "rejected", "wake_stale_or_busy"))
                        continue
                    started = self.ptt_button(received_at=receipt["received_at"], _wake=True)
                    if started:
                        self._voice_control_request = request
                    self._remember(HostResult(request, "running" if started else "rejected",
                                              "wake_question_started" if started else "wake_stale_or_busy"))
                    continue
                if intent.action not in ("stop", "cancel") and generation != self.modes.generation:
                    self._remember(HostResult(request, "rejected", "stale_mode"))
                else:
                    self.control(intent, request)
            else:
                self.execute(intent, request_id=request, expected_generation=generation)
        # STOP/cancel admitted before this poll must retire capture before its
        # result can initiate a new turn. Transcriber callbacks are injected and
        # may reenter cancellation, so validate the epoch again after returning.
        for capture in self.pcm.poll():
            if (capture.status == "ready" and self._pcm_mode_generation == self.modes.generation
                    and self._pcm_transcriber is not None and not self.session.busy
                    and self.modes.mode == "conversation"):
                epoch, generation = self._pcm_epoch, self.modes.generation
                self._pcm_mode_generation = None
                try:
                    message = self._pcm_transcriber(capture)
                    if (not self._closed and self._pcm_epoch == epoch
                            and self._pcm_sequence == capture.generation
                            and self.modes.generation == generation and not self.session.busy
                            and self.modes.mode == "conversation"
                            and type(message) is str and 0 < len(message.strip()) <= 512):
                        self.start_question(message)
                except Exception:
                    pass  # No raw PCM, transcript or provider detail in logs.
        finished = self.session.poll()
        for _ in range(64):
            try:
                kind, turn, data, at = self._voice_events.get_nowait()
            except Empty:
                break
            generation = self._turn_generations.get(turn)
            if generation != self.modes.generation:
                continue
            if kind == "reply":
                self._proposals[turn] = {"gesture": data[0], "intent": data[1], "ended": False,
                                         "character_plan": data[2] if len(data) > 2 else None}
            elif kind == "started" and turn in self._proposals:
                proposal = self._proposals[turn]
                fresh = 0 <= self.clock() - at <= .5
                if self.character_life:
                    plan = proposal.get("character_plan")
                    proposal["gesture"] = "none"
                    if (plan is not None and fresh and self.session.snapshot()["playback"] == "started"
                            and plan.face_emotion == self.session.snapshot()["emotion"]):
                        self._last_character_plan = plan.public()
                        proposal["gesture"] = plan.gesture_id
                        targets = {"attentive": (0., 0.), "curious": (.12, -.04),
                                   "soft_down": (0., .08), "celebratory": (.08, -.05)}
                        if plan.gaze_id in targets:
                            x, y = targets[plan.gaze_id]
                            self.gaze.offer("dialogue", x, y, generation=generation, expires_at=at + .8)
                # Never command an arm on idle/listening/thinking or in replay:
                # only use the emotion actually displayed at speech start.
                if (not self.character_life and self._emotion_arms and self.clock() - at <= .5
                        and self.session.snapshot()["playback"] == "started"):
                    from .motion.emotion_gestures import select_emotion_gesture
                    spoken_emotion = self.session.snapshot()["emotion"]
                    proposal["gesture"] = select_emotion_gesture(
                        spoken_emotion, proposal["gesture"],
                        previous_spoken_emotion=self._last_spoken_emotion,
                        intent=proposal["intent"], enabled=True)
                    self._last_spoken_emotion = spoken_emotion
                # A single short, deterministic look at actual speech start.
                # Native activity and tracking retain higher priority; no idle
                # timer, random motion or emotion/mouth setter is introduced.
                if not self.character_life and self.clock() - at <= .5:
                    self.gaze.offer("reaction", .12, -.04, generation=generation,
                                    expires_at=at + .65)
                if ((self.runtime.simulate or self.runtime.arms_supervised) and proposal["gesture"] != "none" and self.clock() - at <= .5
                        and self.session.snapshot()["playback"] == "started"):
                    request = "gesture_" + uuid5(self._namespace, turn).hex
                    try:
                        entry = self.journal.begin(request, {"gesture": proposal["gesture"], "turn": turn})
                    except Exception:
                        entry = {"duplicate": True}  # Never decorate without a reservation.
                    if not entry["duplicate"]:
                        try:
                            result = self.motion.submit(proposal["gesture"], request_id=request,
                                session_id=self.session.session_id, turn_id=turn, mode_generation=generation,
                                started_at=at)
                        except Exception:
                            # Decorative motion must not terminate PTT/session
                            # polling or leave a permanent accepted receipt.
                            result = HostResult(request, "failed", "motion_admission_failed",
                                                simulated=self.runtime.simulate)
                        if result.status not in ("accepted", "running"):
                            self._finish_result(result)
            elif kind == "ended" and turn in self._proposals:
                self._proposals[turn]["ended"] = True
        for event in finished:
            if event.kind != "finished":
                continue
            if self.microphone is not None and self.microphone.snapshot().get("cleanup_pending"):
                self._ptt_cleanup_fault = True
            else:
                self.audio.release(self._voice_lease)
                self._voice_lease = self._ptt_lease = None
            self._ptt_turn_active = False
            self._ptt_handoff_pending = False
            empty_wake = self._wake_input_active and event.code == "empty_stt"
            if self._wake_input_active:
                if self._wake_handoff["phase"] not in ("cancelled", "timed_out"):
                    self._wake_handoff["phase"] = "idle"
                self._wake_input_active = False
            turn = f"{self.session.session_id}:{event.turn}"
            with self._playback_lock:
                played = turn in self._voice_started_turns
                self._voice_started_turns.discard(turn)
            if self._voice_control_request is not None:
                request, self._voice_control_request = self._voice_control_request, None
                ok = event.code == "ok" or empty_wake
                self._remember(HostResult(request, ("simulated" if self.runtime.simulate else "succeeded") if ok else "cancelled" if event.code == "cancelled" else "failed",
                    "wake_no_question" if empty_wake else "voice_completed" if ok else "voice_cancelled" if event.code == "cancelled" else "voice_failed",
                    simulated=self.runtime.simulate, executed=not self.runtime.simulate and played,
                    completed=ok, completion="no_question" if empty_wake else ("simulated" if self.runtime.simulate else "voice_playback") if ok else
                    "partial_playback" if played else "unknown"))
            cleanup, self._voice_cleanup = self._voice_cleanup, {}
            stopped = self.stop() if any(action in ("stop", "reset") for action in cleanup.values()) else True
            for request, action in cleanup.items():
                done = (stopped if action in ("stop", "reset") else True) and not self._ptt_cleanup_fault
                self._remember(HostResult(request, ("simulated" if self.runtime.simulate else "succeeded") if done else "unknown",
                    ("reset_completed" if action == "reset" else "resources_stopped" if action == "stop" else "voice_cancelled") if done else "cleanup_failed",
                    simulated=self.runtime.simulate, completed=done,
                    completion=("simulated" if self.runtime.simulate else "resources_stopped") if done else "unknown"))
            proposal = self._proposals.pop(turn, None)
            generation = self._turn_generations.pop(turn, None)
            if (event.code == "ok" and proposal and proposal["ended"] and proposal["intent"] is not None
                    and generation == self.modes.generation):
                self.execute(proposal["intent"], request_id="model_" + uuid5(self._namespace, turn).hex,
                             expected_generation=generation)
        for event in self.games.poll():
            if (event.mode_generation == self.modes.generation and self.modes.mode == "game"
                    and event.request_id == self._active_game_request
                    and event.kind in ("game_ended", "game_failed", "game_exit_requested")):
                self._transition("game_menu")
        for event in self.media.poll():
            if (self.runtime.music_supervised and event.kind in ("media_ended", "media_failed")
                    and event.data.get("simulated") is False):
                self._finish_media_terminal(event.request_id, event.code,
                                            event.data.get("resource_released") is True)
            if (event.mode_generation == self.modes.generation and self.modes.mode == "music"
                    and event.request_id == self._active_media_request
                    and event.kind in ("media_ended", "media_failed")):
                self._transition("conversation")
        video = self.video.poll(generation=self.modes.generation)
        if self._active_video_request is not None and not video["recording"]:
            if video["result"] is not None:
                from .camera.video import VideoResult
                data = dict(video["result"])
                contract = data.pop("contract")
                from .camera.live_video import LIVE_CONTRACT, LiveVideoResult
                result_type = LiveVideoResult if contract == LIVE_CONTRACT else VideoResult
                self._remember(self._video_host_result(result_type(**data)))
            self._active_video_request = None
        self.camera.poll()
        for event in self.motion.poll():
            if event.kind == "motion_result":
                data = event.data
                result = HostResult(event.request_id, data["status"], event.code,
                                    data.get("simulated", True), data.get("executed", False),
                                    data.get("completed", False),
                                    data.get("completion", "position_unverified"))
                if event.mode_generation == self.modes.generation:
                    self._finish_result(result)
                else:
                    try:
                        self.journal.finish(result.request_id, result.status, result.code)
                    except Exception:
                        pass  # Old receipt failures never overwrite the new foreground.
        self.imu.poll()  # Health expiry only; IMU events never drive motors here.
        if self._pending_photo and self.clock() >= self._pending_photo["deadline"]:
            pending, self._pending_photo = self._pending_photo, None
            if pending["generation"] == self.modes.generation:
                photo = self.camera.take_photo(pending["request_id"], generation=pending["generation"])
                self._photo_result = photo.public()
                self._photo_png = self.camera.photo_bytes(photo.photo_id) if photo.photo_id else None
                result = HostResult(photo.request_id, photo.status, photo.code, photo.simulated,
                                    photo.executed, photo.completed, photo.completion)
                self._finish_result(result)
        if self._last_shell_seen is not None and self.clock() - self._last_shell_seen > 3.0:
            self.stop()
            self._last_shell_seen = None
        self._tick_idle_gaze()
        self._refresh()

    def _refresh(self):
        self._state_sequence += 1
        face = self.session.snapshot()
        camera = self.camera.snapshot()
        ptt = self.microphone.snapshot() if self.microphone is not None else self.pcm.snapshot()
        if not self.turn_diagnostics.enabled:
            ptt = {key: value for key, value in ptt.items()
                   if key not in ("rms_min", "rms_max", "threshold", "frames")}
        with self._records_lock:
            recent = ([dict(value["result"]) for value in list(self._records.values())[-8:]]
                      + [dict(value) for value in self._safety_results.values()])[-8:]
        state = {"version": 1, "sequence": self._state_sequence, "simulation": self.runtime.simulate,
            "capabilities": self.runtime.public(), "real_features_enabled": self.runtime.public()["real_features_enabled"],
            "mode": self.modes.mode, "mode_generation": self.modes.generation,
            "mode_error": self.modes.last_error, "audio_owner": self.audio.owner,
            "conversation_busy": self.session.busy, "emotion": face["emotion"],
            "phase": face["phase"], "gaze": self.gaze.resolve(phase=face["phase"], generation=self.modes.generation),
            "games": {"catalog": [item.public() for item in self.games.catalog()], **self.games.snapshot()},
            "media": {"catalog": [item.public() for item in self.media.catalog()], **self.media.snapshot()},
            "camera": camera, "camera_video": self.video.snapshot(), "photo": self._photo_result,
            "system_diagnostics": self._system_diagnostics,
            "turn_diagnostics": self.turn_diagnostics.snapshot(),
            "character_life": {"enabled": self.character_life, "ptt_vad_profile": self.ptt_vad_profile,
                               "ptt_vad_mode": self.ptt_vad_mode,
                               "expression_trace": face.get("expression_trace"),
                               "last_played_plan": self._last_character_plan,
                               "physical_latency": "UNMEASURED"},
            "photo_countdown": max(0, self._pending_photo["deadline"] - self.clock()) if self._pending_photo else None,
            "motion": self.motion.snapshot(), "imu": self.imu.snapshot(),
            "input": self.input_adapter.snapshot() if self.input_adapter else
                     {"enabled": False, "real_input": "NOT_RUN", "device_opens": 0},
            "usb_gamepad": self.gamepad.snapshot(),
            "ptt": (dict(ptt, enabled=True,
                        cleanup_fault=self._ptt_cleanup_fault,
                        source="synthetic_pcm" if self.runtime.simulate else "explicit_microphone",
                        hid_identity="keyboard_or_gp8_unverified", physical_verified=False)
                    if self.microphone is not None else ptt),
            "wake_word": dict(self.wake_word.snapshot(), requested=self._wake_requested,
                              armed=self._wake_armed, handoff=dict(self._wake_handoff),
                              acknowledgement=self._wake_ack_cache.snapshot() if self._wake_ack_cache else None),
            "microphone_broker": self._microphone_broker.snapshot() if self._microphone_broker else None,
            "last_result": self._last_result, "requests": recent,
            "asset_status": {"real_game_pack": "ASSET_PENDING",
                "user_songs": "AVAILABLE_FOR_REVIEW" if self._music_library_selected and self.media.catalog() else "ASSET_PENDING",
                "music_playback": "SILENT_MOCK" if self.runtime.simulate else "SUPERVISED_NOT_VERIFIED" if self.runtime.music_supervised else "DENIED",
                "extensions": {"games": "bmo_ai.features.games.GamesFeature",
                    "game_asset_review": "python -m bmo_ai.game_assets --dry-run",
                    "music_import": "python -m bmo_ai.media_assets --help",
                    "media_types": ["audio/wav"], "external_game_runtime": "NOT_CONFIGURED"},
                "real_camera_video": "NOT_RUN"}}
        with self._snapshot_lock:
            self._public = state
            self._preview = self.camera.preview_bytes()
            self._media_cue = self.media.cue_snapshot() if self.modes.mode == "music" else None

    def snapshot(self):
        with self._snapshot_lock:
            return json.loads(json.dumps(self._public, allow_nan=False))

    def preview_bytes(self):
        with self._snapshot_lock:
            return self._preview

    def photo_bytes(self, photo_id):
        with self._snapshot_lock:
            return self._photo_png if self._photo_result and self._photo_result["photo_id"] == photo_id else None

    def video_bytes(self, video_id):
        state = self.video.snapshot()
        result = state["result"]
        if (not (self.runtime.simulate or self.runtime.camera_video_supervised)
                or self.video.store is None or result is None
                or not result["completed"] or result["video_id"] != video_id):
            return None
        try:
            payload = self.video.store.read(video_id)
            if len(payload) != result["bytes"] or hashlib.sha256(payload).hexdigest() != result["sha256"]:
                return None
            return payload
        except Exception:
            return None

    def close(self):
        if self._closed and not self._cleanup_errors:
            return
        self._closed = True
        errors = []
        try:
            self.gamepad.close()
        except Exception:
            errors.append("cleanup_failed")
        try:
            self.stop()
        except Exception:
            errors.append("cleanup_failed")
        if self.microphone is not None:
            try:
                if self.microphone.close() is not True:
                    errors.append("pcm_cleanup_failed")
                    self._ptt_cleanup_fault = True
                else:
                    self._ptt_cleanup_fault = False  # Closing app only; no fresh turn is admitted.
            except Exception:
                errors.append("pcm_cleanup_failed")
                self._ptt_cleanup_fault = True
        if self._microphone_broker is not None:
            try:
                if self._microphone_broker.close() is not True:
                    errors.append("pcm_cleanup_failed")
                    self._ptt_cleanup_fault = True
            except Exception:
                errors.append("pcm_cleanup_failed")
                self._ptt_cleanup_fault = True
        try:
            self.session.close()
            if not self._ptt_cleanup_fault:
                self.audio.release(self._voice_lease)
                self._voice_lease = None
        except Exception:
            errors.append("cleanup_failed")
        try:
            if self.video.close() is not True:
                errors.append("video_close_failed")
        except Exception:
            errors.append("video_close_failed")
        for feature in (self.games, self.media, self.camera, self.motion, self.imu):
            try:
                feature.close()
            except Exception:
                errors.append("cleanup_failed")
        try:
            self.journal.close()
        except Exception:
            errors.append("cleanup_failed")
        for feature in (self.input_adapter, self.pcm, self.wake_word):
            if feature is not None:
                try:
                    feature.close()
                except Exception:
                    errors.append("cleanup_failed")
        try:
            self._temporary.cleanup()
        except Exception:
            errors.append("cleanup_failed")
        self._cleanup_errors = errors
        if errors:
            raise FeatureError("cleanup_failed")

    def cleanup_snapshot(self):
        session = self.session.cleanup_snapshot()
        microphone = self.microphone.snapshot() if self.microphone is not None else {}
        broker = self._microphone_broker.snapshot() if self._microphone_broker else {}
        camera = self.camera.cleanup_snapshot()
        motion_cleanup = getattr(self.motion, "cleanup_snapshot", None)
        try:
            motion = motion_cleanup() if callable(motion_cleanup) else {"resources_cleaned": not self._cleanup_errors}
        except Exception:
            motion = {"resources_cleaned": False, "code": "arm_cleanup_pending"}
        clean = (self._closed and session["resources_cleaned"] and not self._ptt_cleanup_fault
                 and not microphone.get("cleanup_pending", False)
                 and not microphone.get("microphone_open", False)
                 and not broker.get("microphone_open", False)
                 and not broker.get("reader_alive", False) and broker.get("cleanup_verified", True)
                 and not self.video.snapshot()["recording"] and camera["resources_cleaned"]
                 and self.video.snapshot()["code"] != "video_close_failed"
                 and motion.get("resources_cleaned") is True and not self._cleanup_errors
                 and self.games.snapshot()["state"] == "idle" and self.media.snapshot()["state"] == "idle")
        return dict(resources_cleaned=clean, session=session, camera=camera,
                    motion=motion,
                    microphone_closed=not microphone.get("microphone_open", False),
                    microphone_broker=broker or None,
                    microphone_cleanup_pending=microphone.get("cleanup_pending", False),
                    code="resources_cleaned" if clean else "owned_cleanup_pending",
                    scope="host_owned_software_only", physical_release_verified=False)
