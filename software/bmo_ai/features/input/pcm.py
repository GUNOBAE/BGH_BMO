"""Pure injected-PCM PTT/VAD state; no recorder, STT, wake model or device IO."""
from dataclasses import dataclass
from collections import deque
import math
import struct
import time

from ..contracts import FeatureError, request_id_ok, valid_id


def _finite(value, *, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


@dataclass(frozen=True, slots=True, repr=False)
class PcmCapture:
    capture_id: str
    generation: int
    status: str
    code: str
    pcm: bytes
    sample_rate: int

    def __repr__(self):
        return f"PcmCapture(status={self.status!r}, code={self.code!r}, byte_count={len(self.pcm)})"

    def public(self):
        return dict(capture_id=self.capture_id, generation=self.generation, status=self.status,
                    code=self.code, byte_count=len(self.pcm), duration=len(self.pcm) / (self.sample_rate * 2),
                    sample_rate=self.sample_rate, source="injected_pcm")


class PcmTurnGate:
    """Explicit PTT admission and bounded RMS/silence segmentation.

    enabled defaults to False and still never opens a microphone when True.
    The host must call start_ptt before feeding PCM, and explicitly consume a
    ready result; this class never starts a conversation or reacquires audio.
    Any output focus owner blocks/cancels capture, including BMO's own voice.
    Thresholds are synthetic development settings, not microphone calibration.
    """
    def __init__(self, audio_focus, *, enabled=False, clock=time.monotonic,
                 playback_active=None, sample_rate=16000, threshold=.02,
                 min_speech=.08, silence_timeout=.3, no_speech_timeout=3.,
                 max_duration=15., freshness=.25, speech_window=None):
        durations = (min_speech, silence_timeout, no_speech_timeout, max_duration, freshness)
        if (type(enabled) is not bool or type(sample_rate) is not int or sample_rate not in (8000, 16000, 48000)
                or not _finite(threshold, positive=True) or threshold > 1
                or any(not _finite(v, positive=True) or v > 60 for v in durations)
                or min_speech > max_duration or silence_timeout > max_duration
                or speech_window is not None and (not _finite(speech_window, positive=True)
                    or not min_speech <= speech_window <= min(1., max_duration))
                or no_speech_timeout > max_duration or freshness > 5):
            raise FeatureError("invalid_pcm_configuration")
        self.audio_focus, self.enabled, self.clock = audio_focus, enabled, clock
        self.playback_active = playback_active or (lambda: False)
        self.sample_rate, self.threshold = sample_rate, threshold
        self.min_speech, self.silence_timeout = min_speech, silence_timeout
        self.no_speech_timeout, self.max_duration, self.freshness = no_speech_timeout, max_duration, freshness
        self.speech_window = speech_window
        self.max_buffer_bytes = int(sample_rate * 2 * max_duration) // 2 * 2
        self.max_frame_bytes = sample_rate * 2 // 10
        self._closed, self._state, self._last_code = False, "idle", "disabled" if not enabled else "ok"
        self._buffer, self._pending, self._attempts = bytearray(), None, set()
        self._generation = -1
        self._capture_id, self._began = None, None
        self._sequence, self._frame_at = -1, None
        self._voiced, self._silent, self._speech_seen = 0., 0., False
        self._entry, self._entry_duration, self._entry_positive = deque(), 0., 0.
        self._entry_start_sample = self._entry_start_at = None

    def snapshot(self):
        return dict(state=self._state, enabled=self.enabled and not self._closed,
                    reason_code=self._last_code, generation=self._generation,
                    buffered_bytes=len(self._buffer), result_pending=self._pending is not None,
                    max_buffer_bytes=self.max_buffer_bytes, speech_detected=self._speech_seen,
                    speech_entry_window=self.speech_window,
                    entry_start_sample=self._entry_start_sample, entry_start_timestamp=self._entry_start_at,
                    microphone_open=False, real_audio="NOT_RUN", automatic_capture=False)

    def _output_busy(self):
        try:
            return self.audio_focus.owner is not None or self.playback_active() is not False
        except Exception:
            return True  # A failed ownership probe must never enable capture.

    def start_ptt(self, capture_id, *, generation):
        if not request_id_ok(capture_id) or type(generation) is not int or generation < 0:
            raise FeatureError("invalid_capture")
        if self._closed or not self.enabled:
            self._last_code = "input_closed" if self._closed else "disabled"
            return False
        if capture_id in self._attempts or generation <= self._generation:
            self._last_code = "duplicate_capture"
            return False
        if self._state in ("listening", "speech") or self._pending is not None:
            self._last_code = "capture_busy"
            return False
        if len(self._attempts) >= 4096:
            self._last_code = "capture_capacity"
            return False
        if self._output_busy():
            self._last_code = "audio_focus_busy"
            return False
        now = self.clock()
        if not _finite(now):
            raise FeatureError("invalid_capture_clock")
        self._attempts.add(capture_id)
        self._generation, self._capture_id, self._began = generation, capture_id, now
        self._buffer.clear()
        self._sequence, self._frame_at = -1, None
        self._voiced, self._silent, self._speech_seen = 0., 0., False
        self._entry.clear()
        self._entry_duration = self._entry_positive = 0.
        self._entry_start_sample = self._entry_start_at = None
        self._state, self._last_code = "listening", "ok"
        return True

    def _finish(self, code, *, ready=False):
        ready = ready and self._speech_seen
        self._pending = PcmCapture(self._capture_id, self._generation, "ready" if ready else "cancelled",
                                   code, bytes(self._buffer) if ready else b"", self.sample_rate)
        self._buffer.clear()
        self._state, self._last_code = "complete" if ready else "cancelled", code

    def tick(self):
        if self._closed:
            return
        if self._output_busy():
            if self._state in ("listening", "speech") or self._pending and self._pending.status == "ready":
                self._finish("audio_focus_busy")
            return
        if self._state not in ("listening", "speech"):
            return
        now = self.clock()
        if not _finite(now) or now < self._began:
            self._finish("invalid_capture_clock")
        elif not self._speech_seen and now - self._began >= self.no_speech_timeout:
            self._finish("no_speech")
        elif now - self._began >= self.max_duration:
            self._finish("max_duration", ready=True)

    def feed(self, pcm, *, sequence, timestamp, generation, voiced=None, voiced_seconds=None):
        self.tick()
        if self._state not in ("listening", "speech"):
            return False
        if type(generation) is not int or generation != self._generation:
            self._last_code = "stale_capture"
            return False
        if type(sequence) is not int or sequence < 0:
            self._finish("invalid_pcm_sequence")
            return False
        if sequence <= self._sequence:
            self._last_code = "duplicate_pcm"
            return False
        self._sequence = sequence
        now = self.clock()
        if (not _finite(timestamp) or timestamp < self._began or timestamp > now + .05
                or now - timestamp > self.freshness
                or self._frame_at is not None and timestamp < self._frame_at):
            self._last_code = "stale_pcm"
            return False
        self._frame_at = timestamp
        if type(pcm) is not bytes or not pcm or len(pcm) % 2 or len(pcm) > self.max_frame_bytes:
            self._finish("invalid_pcm")
            return False
        if voiced is not None and type(voiced) is not bool:
            self._finish("invalid_voicing")
            return False
        duration = len(pcm) / (self.sample_rate * 2)
        if (voiced_seconds is not None and (voiced is None or not _finite(voiced_seconds)
                or voiced_seconds > duration + 1e-9 or bool(voiced_seconds) != voiced)):
            self._finish("invalid_voicing")
            return False
        remaining = self.max_buffer_bytes - len(self._buffer)
        if len(pcm) > remaining:
            self._buffer.extend(pcm[:remaining])
            self._finish("max_buffer", ready=True)
            return False
        frame_start = len(self._buffer) // 2
        self._buffer.extend(pcm)
        if voiced is None:
            rms = math.sqrt(sum(sample[0] ** 2 for sample in struct.iter_unpack("<h", pcm)) / (len(pcm) // 2)) / 32768
            voiced = rms >= self.threshold
        if self.speech_window is not None and not self._speech_seen:
            positive = voiced_seconds if voiced_seconds is not None else duration if voiced else 0.
            self._entry.append((duration, positive, frame_start, timestamp))
            self._entry_duration += duration
            self._entry_positive += positive
            while self._entry_duration > self.speech_window + 1e-9:
                expired = self._entry.popleft()
                self._entry_duration -= expired[0]
                self._entry_positive -= expired[1]
            if self._entry_positive + 1e-9 >= self.min_speech:
                self._speech_seen, self._state = True, "speech"
                first = next(item for item in self._entry if item[1] > 0)
                self._entry_start_sample, self._entry_start_at = first[2], first[3]
            return True
        if voiced:
            self._voiced += duration
            self._silent = 0.
            if self._voiced + 1e-9 >= self.min_speech:
                self._speech_seen, self._state = True, "speech"
        else:
            if not self._speech_seen:
                self._voiced = 0.
            else:
                self._silent += duration
                if self._silent + 1e-9 >= self.silence_timeout:
                    self._finish("silence", ready=True)
        return True

    def release_ptt(self, *, generation):
        self.tick()
        if type(generation) is not int or generation != self._generation or self._state not in ("listening", "speech"):
            return False
        self._finish("ptt_released" if self._speech_seen else "no_speech", ready=True)
        return True

    def poll(self):
        self.tick()
        if self._pending is None:
            return ()
        pending, self._pending = self._pending, None
        self._state = "idle"
        return (pending,)

    def cancel(self, reason="user"):
        if self._closed or self._state not in ("listening", "speech", "complete"):
            return False
        self._finish(reason if valid_id(reason) else "cancelled")
        return True

    def close(self):
        self._buffer.clear()
        self._entry.clear()
        self._pending = None
        self._closed, self._state = True, "closed"


class WakeWordGate:
    """A truthful disabled boundary, not a pretend Hey BMO detector."""
    enabled = False

    def __init__(self, model=None):
        self._has_model = model is not None

    def feed(self, pcm, **metadata):
        return False

    def snapshot(self):
        return dict(enabled=False, reason_code="wake_unverified" if self._has_model else "wake_model_missing",
                    real_wake_word="NOT_RUN", automatic_capture=False)

    def close(self):
        pass
