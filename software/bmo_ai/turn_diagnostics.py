"""Opt-in bounded stage evidence. Never accepts text, PCM, paths or exceptions."""
from collections import deque
import math
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
    "request_started",
})
METRICS = {"rms_min": 1., "rms_max": 1., "threshold": 1., "ambient_rms": 1.,
           "captured_bytes": 384000, "frames": 10000, "duration_ms": 180000,
           "sample_rate": 48000, "channels": 2, "preroll_ms": 500,
           "trimmed_leading_bytes": 384000}
BOOLEANS = frozenset({"microphone_open", "voice_detected", "close_verified", "ambient_verified",
                      "first_frame_received"})

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
}
_SPANS = {
    "host_to_mic_ready_ms": ("host_input_received", "microphone_ready"),
    "host_to_capture_pipe_ms": ("host_input_received", "capture_pipe_ready"),
    "host_to_first_frame_ms": ("host_input_received", "first_pcm_frame"),
    "last_voiced_to_vad_close_ms": ("last_voiced_frame", "vad_closed"),
    "vad_to_capture_close_ms": ("vad_closed", "capture_closed"),
    "stt_ms": ("stt_started", "stt_completed"),
    "llm_ms": ("llm_started", "llm_completed"),
    "tts_to_first_byte_ms": ("tts_started", "tts_first_byte"),
    "tts_to_download_ms": ("tts_started", "tts_downloaded"),
    "download_to_audio_ready_ms": ("tts_downloaded", "audio_ready"),
    "host_to_player_start_ms": ("host_input_received", "player_started"),
}


def _finite_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class TurnDiagnostics:
    def __init__(self, *, enabled=False, clock=time.monotonic, capacity=96):
        if type(enabled) is not bool or type(capacity) is not int or not 8 <= capacity <= 128:
            raise ValueError("invalid safe trace configuration")
        self.enabled, self.clock = enabled, clock
        self._lock, self._events = RLock(), deque(maxlen=capacity)
        self._turn, self._began = 0, clock()
        self._last, self._failure, self._terminal = None, None, None
        self._pending_input, self._markers = None, {}

    def mark_input(self, at=None):
        """Retain one host-receipt timestamp until the admitted turn begins.

        Browser and physical-button clocks are intentionally not accepted as a
        separate clock domain. Calling this never admits a microphone or turn.
        """
        try:
            now = self.clock()
        except Exception:
            return
        stamp = now if at is None else at
        with self._lock:
            self._pending_input = (stamp if _finite_time(now) and _finite_time(stamp)
                                   and 0 <= now - stamp <= 5 else None)

    def begin(self, turn):
        with self._lock:
            now = self.clock()
            pending, self._pending_input = self._pending_input, None
            self._turn, self._began = turn, now
            if (_finite_time(pending) and _finite_time(now)
                    and 0 <= now - pending <= 5):
                self._began = pending
            self._events.clear()
            self._markers.clear()
            self._last = self._failure = self._terminal = None
            if self._began != now or pending == now:
                self.record("input", "host_input_received", turn=turn, at=pending)
            self.record("admission", "started", turn=turn, at=now)

    def record(self, stage, code="ok", *, outcome="ok", turn=None, at=None, **metrics):
        if stage not in STAGES or outcome not in ("ok", "started", "error", "empty", "cancelled"):
            return
        code = code if type(code) is str and code in CODES else "operation_failed"
        with self._lock:
            if turn is not None and turn != self._turn:
                return  # A cancelled provider cannot overwrite a newer turn's diagnosis.
            safe = {}
            if self.enabled:
                for name, value in metrics.items():
                    if name in BOOLEANS and type(value) is bool:
                        safe[name] = value
                    elif (name in METRICS and type(value) in (int, float)
                          and math.isfinite(value) and 0 <= value <= METRICS[name]):
                        safe[name] = round(value, 4)
            entry = dict(stage=stage, code=code, outcome=outcome)
            self._last = entry
            if outcome in ("error", "empty"):
                self._failure = dict(entry)
            if self.enabled:
                try:
                    now = self.clock()
                except Exception:
                    return
                stamp = now if at is None else at
                if (not _finite_time(now) or not _finite_time(stamp)
                        or not _finite_time(self._began) or stamp < self._began
                        or stamp > now + .05 or stamp - self._began > 180):
                    return  # Invalid clock evidence must not become a measurement.
                elapsed = round((stamp - self._began) * 1000, 1)
                marker = _MARKERS.get((stage, code))
                if marker is not None:
                    if marker == "last_voiced_frame" or marker not in self._markers:
                        self._markers[marker] = elapsed
                if code != "last_voiced":
                    self._events.append(dict(entry, elapsed_ms=elapsed, metrics=safe))

    def finish(self, code):
        with self._lock:
            self._terminal = code if code in CODES else "operation_failed"

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
                        last=dict(self._last) if self._last else None,
                        failure=dict(self._failure) if self._failure else None,
                        events=[dict(item, metrics=dict(item["metrics"])) for item in self._events],
                        markers_ms=dict(self._markers), timings=timings,
                        timing_origin="host_input_received" if "host_input_received" in self._markers else "turn_admission",
                        clock="host_monotonic", acoustic_measurement=False,
                        contains_audio=False, contains_transcript=False)
