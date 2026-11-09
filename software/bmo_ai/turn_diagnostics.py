"""Opt-in bounded stage evidence. Never accepts text, PCM, paths or exceptions."""
from collections import deque
import math
import re
from threading import RLock
import time


STAGES = frozenset({"input", "admission", "microphone", "calibration", "vad",
                    "capture_close", "stt", "ai", "fish", "playback", "cleanup", "turn"})
CODES = frozenset({
    "vad_backend_missing", "vad_backend_failed", "invalid_vad_configuration", "invalid_vad_frame",
    "started", "ok", "waiting_speech", "speech_detected", "no_speech", "stt_empty",
    "calibration_unverified", "ambient_verified", "capture_ready", "cancelled", "finished",
    "disabled", "audio_focus_busy", "microphone_busy", "capture_busy", "duplicate_capture",
    "pcm_audio_format_failed", "microphone_tool_unavailable", "microphone_start_failed",
    "microphone_read_failed", "microphone_stream_closed", "microphone_capture_failed",
    "pcm_truncated", "pcm_duration_limit", "pcm_byte_limit", "pcm_cleanup_failed",
    "pcm_cleanup_pending", "invalid_pcm_frame", "invalid_pcm_result", "calibration_noisy",
    "invalid_capture_clock", "stale_transcript", "invalid_transcript", "transcription_failed",
    "user_cancelled", "silence", "ptt_released", "max_duration", "max_buffer",
    "authentication", "permission", "model", "rate_limit", "timeout", "connection", "server",
    "request", "cloud", "expression_format", "invalid_wav", "audio_timeout", "device_unavailable",
    "audio_cleanup_failed", "audio_download_timeout", "speech_download_too_large",
    "invalid_speech_download", "invalid_transcription_response", "tool_unavailable",
    "operation_failed", "empty_stt", "input_closed", "microphone_opt_in_required",
    "capture_cancelled", "playback_started", "prepared", "ended", "error",
    "fish_authentication", "fish_permission", "fish_model", "fish_rate_limit", "fish_balance",
    "fish_timeout", "fish_connection", "fish_server", "fish_request", "fish_response",
    "fish_invalid_audio", "fish_missing_key", "fish_invalid_input", "fish_download_failed",
    "fish_synthesis_failed", "fish_invalid_configuration",
    "host_input_received", "mic_ready", "capture_pipe_ready", "first_frame", "speech_start", "last_voiced",
    "vad_closed", "capture_closed", "calibration_skipped", "wav_ready",
    "audio_first_byte", "audio_downloaded", "audio_validated", "audio_ready",
    "request_started", "first_pcm", "answer_pcm", "answer_started", "reaction_started",
    "missing_key", "missing_sdk", "missing_fish_key", "fish_redirect",
    "invalid_fish_model", "invalid_fish_voice", "invalid_fish_timeout", "invalid_fish_delivery",
    "invalid_name_mode", "invalid_speech_text", "speech_text_too_long", "speech_audio_too_long",
    "audio_storage_failed", "audio_process_failed", "audio_start_failed", "audio_playback_failed",
    "speech_cache_failed",
    "missing_piper", "piper_failed", "piper_timeout", "capture_limit_reached",
    "llm_incomplete_output", "llm_content_filter", "llm_incomplete", "llm_response_failed",
    "llm_empty_reply", "llm_input_too_long",
})
METRICS = {"rms_min": 1., "rms_max": 1., "threshold": 1., "ambient_rms": 1.,
           "captured_bytes": 960000, "frames": 10000, "duration_ms": 180000,
           "sample_rate": 48000, "channels": 2, "preroll_ms": 500,
           "trimmed_leading_bytes": 960000, "prefix_duration_ms": 180000,
           "answer_offset_frames": 34560000,
           "capture_limit_ms": 30000, "transcript_chars": 16384,
           "input_tokens": 1000000, "output_tokens": 1000000, "reasoning_tokens": 1000000,
           "max_output_tokens": 1000000, "request_input_chars": 1000000,
           "request_instruction_chars": 1000000, "request_schema_chars": 1000000,
           "response_output_chars": 1000000}
BOOLEANS = frozenset({"microphone_open", "voice_detected", "close_verified", "ambient_verified",
                      "first_frame_received"})

# Persist only these diagnostic scalar fields, not provider objects, text,
# exception messages or arbitrary codes. Previous v3 receipts omit this field.
STAGE_FIELDS = {
    "ai": frozenset({"input_tokens", "output_tokens", "reasoning_tokens", "max_output_tokens",
        "request_input_chars", "request_instruction_chars", "request_schema_chars",
        "response_output_chars", "response_status", "incomplete_reason"}),
    "stt": frozenset({"captured_bytes", "transcript_chars", "duration_ms"}),
    "microphone": frozenset({"captured_bytes", "duration_ms", "capture_limit_ms", "capture_end_reason"}),
    "capture_close": frozenset({"captured_bytes", "duration_ms", "capture_limit_ms", "capture_end_reason"}),
}
METRIC_ENUMS = {
    "response_status": frozenset({"completed", "incomplete", "failed", "cancelled", "in_progress", "queued", "unknown"}),
    "incomplete_reason": frozenset({"max_output_tokens", "content_filter", "unknown", "none"}),
    "capture_end_reason": frozenset({"max_duration", "max_buffer", "pcm_duration_limit", "pcm_byte_limit",
        "silence", "ptt_released", "no_speech", "user_cancelled"}),
}


def safe_stage_metadata(value):
    if type(value) is not dict:
        return {}
    result = {}
    for stage, fields in STAGE_FIELDS.items():
        raw = value.get(stage)
        if type(raw) is not dict:
            continue
        clean = {}
        for name in fields:
            item = raw.get(name)
            if name in METRIC_ENUMS and type(item) is str and item in METRIC_ENUMS[name]:
                clean[name] = item
            elif (name in METRICS and type(item) in (int, float) and math.isfinite(item)
                  and 0 <= item <= METRICS[name]):
                clean[name] = round(item, 4)
        if clean:
            result[stage] = clean
    return result


PLAYBACK_PATHS = frozenset({"fish_stream", "fish_reaction_stream", "fish_reaction_wav",
                            "fish_wav", "stored_voice", "offline", "synthetic", "repeat"})
ANSWER_ONSET_EVIDENCE = frozenset({"body_player_boundary", "body_pcm_submission",
                                   "unlocated_vocal_cue", "cached_only", "unknown"})
_RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
_REVISION = re.compile(r"[a-f0-9]{40}\Z")
_TURN_ID = re.compile(r"[a-f0-9]{32}:[1-9][0-9]{0,6}\Z")

# These are host/source observations, not estimates of provider internals or
# acoustic speaker output. Missing markers always stay unmeasured.
_MARKERS = {
    ("input", "host_input_received"): "host_input_received",
    ("admission", "started"): "turn_admitted",
    ("microphone", "mic_ready"): "microphone_ready",
    ("microphone", "capture_pipe_ready"): "capture_pipe_ready",
    ("microphone", "first_frame"): "first_pcm_frame",
    ("vad", "speech_start"): "speech_start",
    ("vad", "last_voiced"): "last_voiced_frame",
    ("vad", "vad_closed"): "vad_closed",
    ("capture_close", "capture_closed"): "capture_closed",
    ("stt", "wav_ready"): "stt_wav_ready",
    ("stt", "started"): "stt_preparation_started",
    ("stt", "request_started"): "stt_started", ("stt", "ok"): "stt_completed",
    ("ai", "started"): "llm_started", ("ai", "ok"): "llm_completed",
    ("fish", "started"): "tts_started",
    ("fish", "audio_first_byte"): "tts_first_byte",
    ("fish", "audio_downloaded"): "tts_downloaded",
    ("fish", "audio_validated"): "tts_validated",
    ("fish", "audio_ready"): "audio_ready",
    ("playback", "started"): "player_started",
    ("playback", "playback_started"): "player_started",
    ("playback", "first_pcm"): "first_output_pcm",
    ("playback", "answer_pcm"): "first_answer_pcm",
    ("playback", "answer_started"): "answer_started",
    ("playback", "reaction_started"): "reaction_started",
    ("turn", "finished"): "turn_finished",
}
END_MARKERS = {"stt": "stt_ended", "ai": "llm_ended", "fish": "tts_ended", "playback": "playback_ended"}
MARKER_NAMES = frozenset(_MARKERS.values()) | frozenset(END_MARKERS.values())
_SPANS = {
    "host_to_mic_ready_ms": ("host_input_received", "microphone_ready"),
    "host_to_capture_pipe_ms": ("host_input_received", "capture_pipe_ready"),
    "host_to_first_frame_ms": ("host_input_received", "first_pcm_frame"),
    "last_voiced_to_vad_close_ms": ("last_voiced_frame", "vad_closed"),
    "vad_to_capture_close_ms": ("vad_closed", "capture_closed"),
    "capture_close_to_stt_ms": ("capture_closed", "stt_started"),
    "stt_to_llm_ms": ("stt_completed", "llm_started"),
    "llm_to_tts_ms": ("llm_completed", "tts_started"),
    "stt_ms": ("stt_started", "stt_completed"),
    "llm_ms": ("llm_started", "llm_completed"),
    "tts_to_first_byte_ms": ("tts_started", "tts_first_byte"),
    "tts_to_download_ms": ("tts_started", "tts_downloaded"),
    "first_byte_to_player_ms": ("tts_first_byte", "player_started"),
    "playback_download_overlap_ms": ("player_started", "tts_downloaded"),
    "download_to_audio_ready_ms": ("tts_downloaded", "audio_ready"),
    "audio_ready_to_player_ms": ("audio_ready", "player_started"),
    "last_voiced_to_player_start_ms": ("last_voiced_frame", "player_started"),
    "host_to_player_start_ms": ("host_input_received", "player_started"),
    "last_voiced_to_answer_start_ms": ("last_voiced_frame", "answer_started"),
    "last_voiced_to_answer_pcm_ms": ("last_voiced_frame", "first_answer_pcm"),
    "first_byte_to_pcm_ms": ("tts_first_byte", "first_output_pcm"),
    "llm_attempt_ms": ("llm_started", "llm_ended"),
    "stt_attempt_ms": ("stt_started", "stt_ended"),
    "tts_attempt_ms": ("tts_started", "tts_ended"),
    "playback_ms": ("player_started", "playback_ended"),
}


def _finite_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class TurnDiagnostics:
    def __init__(self, *, enabled=False, clock=time.monotonic, capacity=96, completed_sink=None,
                 runtime_revision=None, run_id=None):
        if type(enabled) is not bool or type(capacity) is not int or not 8 <= capacity <= 128:
            raise ValueError("invalid safe trace configuration")
        if completed_sink is not None and not callable(completed_sink):
            raise ValueError("invalid safe trace sink")
        self.enabled, self.clock = enabled, clock
        self._completed_sink = completed_sink
        self._saved = self._finished = False
        self._lock, self._events = RLock(), deque(maxlen=capacity)
        self._turn, self._began = 0, clock()
        self._last, self._failure, self._terminal = None, None, None
        self._failure_at = self._terminal_at = self._cancel_at = None
        self._cancellation = self._cleanup_failure = None
        self._cleanup_failure_at = None
        self._pending_input, self._markers = None, {}
        self._runtime_revision = self._run_id = self._turn_id = None
        self._playback_path = None
        self._answer_onset_evidence = "unknown"
        self._playback_metadata = {}
        self._stage_metadata = {}
        self.bind_runtime(run_id=run_id, runtime_revision=runtime_revision)

    def bind_runtime(self, *, run_id=None, runtime_revision=None):
        """Bind launch identity before admission; never relabel previous turns."""
        with self._lock:
            if self._turn:
                return False
            for value, pattern, attribute in ((run_id, _RUN_ID, '_run_id'),
                    (runtime_revision, _REVISION, '_runtime_revision')):
                if value is None:
                    continue
                if type(value) is not str or pattern.fullmatch(value) is None:
                    return False
                current = getattr(self, attribute)
                if current is not None and current != value:
                    return False
            if run_id is not None:
                self._run_id = run_id
            if runtime_revision is not None:
                self._runtime_revision = runtime_revision
            return True

    def mark_input(self, at=None):
        """Retain one host-receipt timestamp until the admitted turn begins."""
        try:
            now = self.clock()
        except Exception:
            return
        stamp = now if at is None else at
        with self._lock:
            self._pending_input = (stamp if _finite_time(now) and _finite_time(stamp)
                                   and 0 <= now - stamp <= 5 else None)

    def begin(self, turn, *, turn_id=None):
        with self._lock:
            now = self.clock()
            pending, self._pending_input = self._pending_input, None
            self._turn, self._began = turn, now
            self._turn_id = (turn_id if type(turn_id) is str and _TURN_ID.fullmatch(turn_id)
                             and turn_id.rsplit(':', 1)[1] == str(turn) else None)
            if (_finite_time(pending) and _finite_time(now)
                    and 0 <= now - pending <= 5):
                self._began = pending
            self._saved = self._finished = False
            self._events.clear()
            self._markers.clear()
            self._playback_metadata.clear()
            self._stage_metadata.clear()
            self._answer_onset_evidence = "unknown"
            self._last = self._failure = self._terminal = self._playback_path = None
            self._failure_at = self._terminal_at = self._cancel_at = self._cleanup_failure_at = None
            self._cancellation = self._cleanup_failure = None
            if self._began != now or pending == now:
                self.record("input", "host_input_received", turn=turn, at=pending)
            self.record("admission", "started", turn=turn, at=now)

    def _elapsed(self, at=None):
        try:
            now = self.clock()
        except Exception:
            return None
        stamp = now if at is None else at
        if (not _finite_time(now) or not _finite_time(stamp)
                or not _finite_time(self._began) or stamp < self._began
                or stamp > now + .05 or stamp - self._began > 180):
            return None
        return round((stamp - self._began) * 1000, 1)

    def record(self, stage, code="ok", *, outcome="ok", turn=None, at=None,
               playback_path=None, answer_onset_evidence=None, **metrics):
        if stage not in STAGES or outcome not in ("ok", "started", "error", "empty", "cancelled"):
            return
        code = code if type(code) is str and code in CODES else "operation_failed"
        with self._lock:
            if self._finished or turn is not None and turn != self._turn:
                return  # A cancelled/finished provider cannot rewrite a receipt.
            safe = {}
            if self.enabled:
                for name, value in metrics.items():
                    if name in METRIC_ENUMS and type(value) is str and value in METRIC_ENUMS[name]:
                        safe[name] = value
                    elif name in BOOLEANS and type(value) is bool:
                        safe[name] = value
                    elif (name in METRICS and type(value) in (int, float)
                          and math.isfinite(value) and 0 <= value <= METRICS[name]):
                        safe[name] = round(value, 4)
                if type(playback_path) is str and playback_path in PLAYBACK_PATHS:
                    self._playback_path = playback_path
                if type(answer_onset_evidence) is str and answer_onset_evidence in ANSWER_ONSET_EVIDENCE:
                    self._answer_onset_evidence = answer_onset_evidence
                stage_values = safe_stage_metadata({stage: safe}).get(stage)
                if stage_values:
                    self._stage_metadata.setdefault(stage, {}).update(stage_values)
                for name in ('prefix_duration_ms', 'answer_offset_frames'):
                    if name in safe:
                        self._playback_metadata[name] = safe[name]
            elapsed = self._elapsed(at) if self.enabled else None
            entry = dict(stage=stage, code=code, outcome=outcome)
            self._last = entry
            if outcome in ("error", "empty"):
                if stage == 'cleanup':
                    if self._cleanup_failure is None:
                        self._cleanup_failure, self._cleanup_failure_at = dict(entry), elapsed
                    if self._failure is None:
                        self._failure, self._failure_at = dict(entry), elapsed
                elif (self._failure is None or self._failure['stage'] == 'cleanup'
                        or self._failure['code'] in ('error', 'operation_failed')
                        and code not in ('error', 'operation_failed')):
                    if self._failure is None or self._failure['stage'] == 'cleanup':
                        self._failure_at = elapsed
                    self._failure = dict(entry)
            if outcome == 'cancelled' and self._cancellation is None:
                self._cancellation, self._cancel_at = dict(entry), elapsed
            if self.enabled and elapsed is not None:
                marker = _MARKERS.get((stage, code))
                if marker is not None:
                    if marker == "last_voiced_frame" or marker not in self._markers:
                        self._markers[marker] = elapsed
                if stage in END_MARKERS and (outcome in ('error', 'empty', 'cancelled')
                        or stage in ('stt', 'ai') and code == 'ok'
                        or stage == 'fish' and code == 'audio_validated'
                        or stage == 'playback' and code == 'ended'):
                    self._markers.setdefault(END_MARKERS[stage], elapsed)
                if code != "last_voiced":
                    self._events.append(dict(entry, elapsed_ms=elapsed, metrics=safe))

    def finish(self, code):
        # Freeze the actual terminal instant and original failure, even if a
        # cleanup callback/repeated finish arrives after a completed receipt.
        completed = None
        with self._lock:
            if self._finished:
                return
            self._terminal = code if code in CODES else "operation_failed"
            self._terminal_at = self._elapsed() if self.enabled else None
            if self._terminal_at is not None:
                self._markers['turn_finished'] = self._terminal_at
            if self._terminal == 'cancelled' and self._cancellation is None:
                self._cancellation = dict(stage='turn', code='cancelled', outcome='cancelled')
                self._cancel_at = self._terminal_at
            self._finished = True
            if self.enabled and not self._saved and self._completed_sink is not None:
                self._saved = True
                completed = self.snapshot()
        if completed is not None:
            try:
                self._completed_sink(completed)
            except Exception:
                pass  # Diagnostics never repeat or fail a provider operation.

    def snapshot(self):
        with self._lock:
            timings = {}
            for name, (start, end) in _SPANS.items():
                left, right = self._markers.get(start), self._markers.get(end)
                measured = left is not None and right is not None and right >= left
                timings[name] = {"status": "MEASURED" if measured else "UNMEASURED",
                                 "value_ms": round(right - left, 1) if measured else None}
            timings["acoustic_first_sound_ms"] = {"status": "UNMEASURED", "value_ms": None}
            return dict(enabled=self.enabled, turn=self._turn, terminal=self._terminal,
                        runtime_revision=self._runtime_revision, run_id=self._run_id, turn_id=self._turn_id,
                        playback_path=self._playback_path, playback_metadata=dict(self._playback_metadata),
                        answer_onset_evidence=self._answer_onset_evidence,
                        stage_metadata=safe_stage_metadata(self._stage_metadata),
                        terminal_elapsed_ms=self._terminal_at, failure_elapsed_ms=self._failure_at,
                        cancellation=dict(self._cancellation) if self._cancellation else None,
                        cancellation_elapsed_ms=self._cancel_at,
                        cleanup_failure=dict(self._cleanup_failure) if self._cleanup_failure else None,
                        cleanup_failure_elapsed_ms=self._cleanup_failure_at,
                        last=dict(self._last) if self._last else None,
                        failure=dict(self._failure) if self._failure else None,
                        events=[dict(item, metrics=dict(item["metrics"])) for item in self._events],
                        markers_ms=dict(self._markers), timings=timings,
                        timing_origin="host_input_received" if "host_input_received" in self._markers else "turn_admission",
                        clock="host_monotonic", acoustic_measurement=False,
                        contains_audio=False, contains_transcript=False)
