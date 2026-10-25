"""Opt-in bounded stage evidence. Never accepts text, PCM, paths or exceptions."""
from collections import deque
import math
from threading import RLock
import time


STAGES = frozenset({"input", "admission", "microphone", "calibration", "vad",
                    "capture_close", "stt", "ai", "fish", "playback", "cleanup", "turn"})
CODES = frozenset({
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
})
METRICS = {"rms_min": 1., "rms_max": 1., "threshold": 1., "ambient_rms": 1.,
           "captured_bytes": 384000, "frames": 10000, "duration_ms": 180000,
           "sample_rate": 48000, "channels": 2}
BOOLEANS = frozenset({"microphone_open", "voice_detected", "close_verified", "ambient_verified",
                      "first_frame_received"})


class TurnDiagnostics:
    def __init__(self, *, enabled=False, clock=time.monotonic, capacity=96):
        if type(enabled) is not bool or type(capacity) is not int or not 8 <= capacity <= 128:
            raise ValueError("invalid safe trace configuration")
        self.enabled, self.clock = enabled, clock
        self._lock, self._events = RLock(), deque(maxlen=capacity)
        self._turn, self._began = 0, clock()
        self._last, self._failure, self._terminal = None, None, None

    def begin(self, turn):
        with self._lock:
            self._turn, self._began = turn, self.clock()
            self._events.clear()
            self._last = self._failure = self._terminal = None
            self.record("admission", "started", turn=turn)

    def record(self, stage, code="ok", *, outcome="ok", turn=None, **metrics):
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
                elapsed = max(0., min(180000., (self.clock() - self._began) * 1000))
                self._events.append(dict(entry, elapsed_ms=round(elapsed, 1), metrics=safe))

    def finish(self, code):
        with self._lock:
            self._terminal = code if code in CODES else "operation_failed"

    def snapshot(self):
        with self._lock:
            return dict(enabled=self.enabled, turn=self._turn, terminal=self._terminal,
                        last=dict(self._last) if self._last else None,
                        failure=dict(self._failure) if self._failure else None,
                        events=[dict(item, metrics=dict(item["metrics"])) for item in self._events],
                        contains_audio=False, contains_transcript=False)
