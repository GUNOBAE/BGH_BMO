"""Explicit finite PCM input and one-shot transcript handoff for PTT.

Construction/import never opens ALSA or creates clients.  ``MicPcmSource`` may
spawn its single owned arecord child only with a literal live opt-in and an
explicit call to ``frames``.  Tests inject synthetic sources/processes instead.
RMS is an amplitude/silence gate, not speaker recognition or a wake-word model.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
import selectors
import struct
import subprocess
from threading import Event, Lock, RLock
import time
import wave

from ... import audio
from ...conversation import TurnCancelled
from ..contracts import FeatureError, request_id_ok
from .pcm import PcmCapture, PcmTurnGate


class MicError(FeatureError):
    """Only a fixed error code escapes; never child stderr/device/PCM contents."""


@dataclass(frozen=True, slots=True, repr=False)
class PcmFrame:
    pcm: bytes
    sequence: int
    timestamp: float
    generation: int

    def __repr__(self):
        return f"PcmFrame(sequence={self.sequence!r}, byte_count={len(self.pcm)})"


def _positive(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum


def _clock_now(clock):
    now = clock()
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise MicError("invalid_capture_clock")
    return now


def _microphone_environment():
    # Do not inherit API credentials, Python hooks, LD_PRELOAD or arbitrary PATH.
    permitted = {"LANG", "LC_ALL", "LC_CTYPE", "XDG_RUNTIME_DIR", "PULSE_SERVER"}
    result = {name: value for name, value in os.environ.items() if name in permitted}
    result["PATH"] = "/usr/bin:/bin"
    return result


class MicPcmSource:
    """Finite, raw 16-kHz S16_LE mono pipe owned by one PTT request.

    No sound devices are probed.  Production callers must explicitly consent
    and verify their configured ALSA format during supervised acceptance.
    arecord does not need a shell or inherited input/file descriptors.  Only its
    direct child is signalled/reaped; no process-wide or firmware operations.
    """
    sample_rate = 16000
    simulated = False

    def __init__(self, device="plughw:CARD=Device,DEV=0", *, live_opt_in=False,
                 frame_ms=40, max_duration=12., clock=time.monotonic,
                 process_factory=None):
        if (type(live_opt_in) is not bool or type(frame_ms) is not int
                or not 20 <= frame_ms <= 100 or not _positive(max_duration, 12)):
            raise MicError("invalid_mic_configuration")
        try:
            self._device_args = audio._device_args(device)
        except audio.AudioError:
            raise MicError("invalid_mic_configuration") from None
        self.live_opt_in, self.frame_ms = live_opt_in, frame_ms
        self.max_duration, self.clock = float(max_duration), clock
        self.frame_bytes = self.sample_rate * 2 * frame_ms // 1000
        self.max_bytes = int(self.sample_rate * 2 * max_duration) // 2 * 2
        self._process_factory = process_factory or subprocess.Popen
        self._lock, self._capture_lock = RLock(), Lock()
        self._closed, self._failed = Event(), False
        self._process, self._state, self._code = None, "idle", "disabled" if not live_opt_in else "ok"
        self._selector = None
        self._bytes = 0

    def snapshot(self):
        with self._lock:
            return dict(state=self._state, reason_code=self._code,
                        microphone_open=self._process is not None,
                        captured_bytes=self._bytes, max_bytes=self.max_bytes,
                        frame_ms=self.frame_ms, automatic_capture=False,
                        enabled=self.live_opt_in and not self._closed.is_set() and not self._failed)

    def _check(self, cancel_check):
        if self._closed.is_set():
            raise TurnCancelled()
        if cancel_check is not None:
            cancel_check()

    def frames(self, capture_id, *, generation, cancel_check=None):
        if not request_id_ok(capture_id) or type(generation) is not int or generation < 0:
            raise MicError("invalid_capture")
        if not self.live_opt_in:
            raise MicError("microphone_opt_in_required")
        if not self._capture_lock.acquire(blocking=False):
            raise MicError("microphone_busy")
        selector = iterator_error = None
        process = None
        try:
            if self._failed:
                raise MicError("pcm_cleanup_failed")
            self._check(cancel_check)
            began = _clock_now(self.clock)
            command = ["/usr/bin/arecord", "-q", "-t", "raw", "-f", "S16_LE",
                       "-r", "16000", "-c", "1", *self._device_args]
            try:
                process = self._process_factory(command, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                        close_fds=True, start_new_session=True, env=_microphone_environment())
                with self._lock:
                    self._process, self._state, self._code, self._bytes = process, "recording", "ok", 0
                fd = process.stdout.fileno()
                os.set_inheritable(fd, False)
                os.set_blocking(fd, False)
                selector = selectors.DefaultSelector()
                with self._lock:
                    self._selector = selector
                selector.register(fd, selectors.EVENT_READ)
            except FileNotFoundError:
                raise MicError("microphone_tool_unavailable") from None
            except (OSError, AttributeError, ValueError):
                raise MicError("microphone_start_failed") from None
            pending = bytearray()
            sequence = 0
            while True:
                self._check(cancel_check)
                now = _clock_now(self.clock)
                if now < began:
                    raise MicError("invalid_capture_clock")
                if now - began >= self.max_duration or self._bytes >= self.max_bytes:
                    if pending:
                        raise MicError("pcm_truncated")
                    raise MicError("pcm_duration_limit")
                events = selector.select(min(.04, self.max_duration - (now - began)))
                self._check(cancel_check)
                if not events:
                    continue
                try:
                    chunk = os.read(fd, min(self.frame_bytes - len(pending), self.max_bytes - self._bytes))
                except BlockingIOError:
                    continue
                except OSError:
                    raise MicError("microphone_read_failed") from None
                if not chunk:
                    raise MicError("pcm_truncated" if pending else "microphone_stream_closed")
                pending.extend(chunk)
                with self._lock:
                    self._bytes += len(chunk)
                if len(pending) == self.frame_bytes:
                    self._check(cancel_check)
                    stamp = _clock_now(self.clock)
                    yield PcmFrame(bytes(pending), sequence, stamp, generation)
                    pending.clear()
                    sequence += 1
        except BaseException as error:
            iterator_error = error
            if isinstance(error, MicError):
                with self._lock:
                    self._code = error.code
            raise
        finally:
            try:
                cleaned = self.close()
            finally:
                self._capture_lock.release()
            if not cleaned and not isinstance(iterator_error, GeneratorExit):
                raise MicError("pcm_cleanup_failed") from None

    def close(self):
        """Bounded termination/reap; one cleanup failure never skips another.

        False retains uncertain process/selector ownership for a safe retry.
        """
        self._closed.set()
        with self._lock:
            process = self._process
            failed = False
            if process is not None:
                try:
                    try:
                        process.wait(timeout=0)
                    except subprocess.TimeoutExpired:
                        try:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=.2)
                        except subprocess.TimeoutExpired:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=.2)
                    if process.stdout is not None:
                        process.stdout.close()
                    self._process = None
                except BaseException:
                    failed = True
            if self._selector is not None:
                try:
                    self._selector.close()
                    self._selector = None
                except BaseException:
                    failed = True
            if failed:
                self._failed, self._state, self._code = True, "failed", "pcm_cleanup_failed"
                return False
            self._failed, self._state = False, "closed"
            return True


class SyntheticPcmSource:
    """Finite development tone/silence; no saved user sound, device or API.

    Source timestamps are monotonic receipt times. VAD durations still derive
    from actual PCM sample counts, so CI does not sleep to simulate a speaker.
    """
    sample_rate = 16000
    simulated = True

    def __init__(self, *, clock=time.monotonic):
        self.clock, self._closed = clock, False

    def frames(self, capture_id, *, generation, cancel_check=None):
        if not request_id_ok(capture_id) or type(generation) is not int or generation < 0:
            raise MicError("invalid_capture")
        sequence = 0
        for amplitude, count in ((0, 10), (10000, 8), (0, 23)):
            for _ in range(count):
                if self._closed:
                    raise TurnCancelled()
                if cancel_check is not None:
                    cancel_check()
                pcm = struct.pack("<" + "h" * 640, *(round(amplitude * math.sin(
                    2 * math.pi * 220 * i / 16000)) for i in range(640)))
                yield PcmFrame(pcm, sequence, _clock_now(self.clock), generation)
                sequence += 1

    def close(self):
        self._closed = True
        return True


class _Done(Exception):
    pass


class _Finish(Exception):
    pass


class _Cancelled(Exception):
    pass


class _NoSpeech(Exception):
    pass


class _Limit(Exception):
    pass


class _FocusProbe:
    def __init__(self, audio_focus, allowed_focus):
        self.focus, self.allowed = audio_focus, allowed_focus

    @property
    def owner(self):
        owner = self.focus.owner
        if owner is not None and self.allowed is not None and self.allowed() is True:
            return None  # Only the exact lease captured by the existing worker.
        return owner


class PttRecorder:
    """Blocking input provider for the existing single conversation worker.

    ``allowed_focus`` must recognize that worker's exact existing AudioLease;
    default denies every occupied focus. ``playback_active`` must mean actual
    output, not session.busy (this input itself is part of that same session).
    Never hold a second brain, voice cache, microphone daemon or STT client.
    Only capture() opens a source. A completed result is one-shot and private.
    """
    def __init__(self, audio_focus, source_factory, *, clock=time.monotonic,
                 playback_active=None, allowed_focus=None, on_state=None,
                 threshold=.02, min_speech=.24, silence_timeout=.9,
                 no_speech_timeout=3., max_duration=12., calibration=.4,
                 noise_multiplier=2.5, max_noise=.12):
        if (not callable(source_factory) or not _positive(calibration, .5) or calibration < .3
                or not _positive(noise_multiplier, 10) or noise_multiplier < 1
                or not _positive(max_noise, .5) or not _positive(max_duration, 12)):
            raise MicError("invalid_mic_configuration")
        # Reuse and validate the existing PCM gate's contract without opening IO.
        self.focus = _FocusProbe(audio_focus, allowed_focus)
        self.playback_active = playback_active or (lambda: False)
        validation = PcmTurnGate(self.focus, enabled=True, clock=clock,
                     playback_active=self.playback_active, threshold=threshold,
                     min_speech=min_speech, silence_timeout=silence_timeout,
                     no_speech_timeout=no_speech_timeout, max_duration=max_duration)
        validation.close()
        self.source_factory, self.clock, self.on_state = source_factory, clock, on_state
        self.settings = dict(threshold=threshold, min_speech=min_speech, silence_timeout=silence_timeout,
                             no_speech_timeout=no_speech_timeout, max_duration=max_duration)
        self.calibration, self.noise_multiplier, self.max_noise = calibration, noise_multiplier, max_noise
        self._lock, self._operation = RLock(), Lock()
        self._cancel, self._finish, self._closed, self._idle = Event(), Event(), Event(), Event()
        self._idle.set()
        self._state, self._code, self._microphone_open = "idle", "ok", False
        self._last_generation, self._ready = -1, None
        self._source, self._captured_bytes = None, 0
        # Only coarse PCM energy metadata is observable; never speech text/bytes.
        self._vad_frames = self._vad_speech_frames = 0
        self._vad_noise_band = self._vad_input_band = "unmeasured"
        self._vad_threshold = None
        self._cleanup_failed = False
        self._pending_wav = None

    def snapshot(self):
        with self._lock:
            return dict(state=self._state, reason_code=self._code, microphone_open=self._microphone_open,
                        generation=self._last_generation, captured_bytes=self._captured_bytes,
                        speech_detected=self._state in ("speech", "ready", "transcribing"),
                        automatic_capture=False, wake_word_enabled=False,
                        cleanup_pending=self._cleanup_failed or self._pending_wav is not None,
                        vad="rms_energy", calibration_seconds=self.calibration,
                        vad_frames=self._vad_frames, speech_frames=self._vad_speech_frames,
                        input_level=self._vad_input_band, noise_level=self._vad_noise_band,
                        effective_threshold=self._vad_threshold,
                        silence_seconds=self.settings["silence_timeout"],
                        max_seconds=self.settings["max_duration"])

    @staticmethod
    def _rms_band(value):
        # Coarse non-reversible volume indicators, not recorded sample content.
        if value < .004:
            return "silent"
        if value < .015:
            return "quiet"
        if value < .08:
            return "audible"
        return "loud"

    def _publish(self, state, code="ok", *, microphone_open=None):
        with self._lock:
            self._state, self._code = state, code
            if microphone_open is not None:
                self._microphone_open = microphone_open
            projection = self.snapshot()
        if self.on_state is not None:
            self.on_state(projection)

    def _busy_output(self):
        try:
            return self.focus.owner is not None or self.playback_active() is not False
        except Exception:
            return True

    def capture(self, capture_id, *, generation, cancel_check=None):
        if not self._operation.acquire(blocking=False):
            raise MicError("microphone_busy")
        self._idle.clear()
        try:
            return self._capture_turn(capture_id, generation=generation, cancel_check=cancel_check)
        finally:
            self._idle.set()
            self._operation.release()

    def _capture_turn(self, capture_id, *, generation, cancel_check=None):
        if not request_id_ok(capture_id) or type(generation) is not int or generation < 0:
            raise MicError("invalid_capture")
        source = frames = gate = None
        result = None
        try:
            with self._lock:
                if self._closed.is_set():
                    raise MicError("input_closed")
                if self._cleanup_failed:
                    raise MicError("pcm_cleanup_failed")
                if generation <= self._last_generation:
                    raise MicError("duplicate_capture")
                self._last_generation, self._ready, self._captured_bytes = generation, None, 0
                self._vad_frames = self._vad_speech_frames = 0
                self._vad_noise_band = self._vad_input_band = "unmeasured"
                self._vad_threshold = None
                self._cancel.clear()
                self._finish.clear()
            self._idle.clear()
            began = _clock_now(self.clock)
            gate = PcmTurnGate(self.focus, enabled=True, clock=self.clock,
                               playback_active=self.playback_active, **self.settings)
            gate_started = False
            def check():
                if cancel_check is not None:
                    cancel_check()
                if self._closed.is_set() or self._cancel.is_set():
                    raise _Cancelled()
                if self._finish.is_set():
                    raise _Finish()
                if self._busy_output():
                    raise MicError("audio_focus_busy")
                now = _clock_now(self.clock)
                if now < began:
                    raise MicError("invalid_capture_clock")
                speech_seen = gate.snapshot()["speech_detected"]
                if not speech_seen and now - began >= self.settings["no_speech_timeout"]:
                    raise _NoSpeech()
                if now - began >= self.settings["max_duration"]:
                    raise _Limit()
                if gate_started:
                    gate.tick()
                    if gate.snapshot()["result_pending"]:
                        raise _Done()
            check()
            self._publish("calibrating")
            source = self.source_factory()
            with self._lock:
                self._source, self._microphone_open = source, True
            frames = iter(source.frames(capture_id, generation=generation, cancel_check=check))
            noise_squares, noise_count = 0, 0
            calibration_samples = round(16000 * self.calibration)
            previous_sequence, previous_time = -1, began
            for frame in frames:
                check()
                if (not isinstance(frame, PcmFrame) or type(frame.generation) is not int
                        or frame.generation != generation or type(frame.sequence) is not int
                        or frame.sequence != previous_sequence + 1 or type(frame.pcm) is not bytes
                        or not 640 <= len(frame.pcm) <= 3200 or len(frame.pcm) % 2
                        or type(frame.timestamp) not in (int, float) or not math.isfinite(frame.timestamp)
                        or frame.timestamp < previous_time or frame.timestamp > self.clock() + .05
                        or self.clock() - frame.timestamp > .25):
                    raise MicError("invalid_pcm_frame")
                previous_sequence, previous_time = frame.sequence, frame.timestamp
                with self._lock:
                    self._captured_bytes += len(frame.pcm)
                    self._microphone_open = True
                if self._captured_bytes > int(16000 * 2 * self.settings["max_duration"]):
                    raise MicError("pcm_byte_limit")
                with self._lock:
                    self._vad_frames += 1
                if not gate_started:
                    samples = tuple(v[0] for v in struct.iter_unpack("<h", frame.pcm))
                    noise_squares += sum(sample * sample for sample in samples)
                    noise_count += len(samples)
                    if noise_count >= calibration_samples:
                        noise = math.sqrt(noise_squares / noise_count) / 32768
                        with self._lock:
                            self._vad_noise_band = self._rms_band(noise)
                        if noise > self.max_noise:
                            raise MicError("calibration_noisy")
                        gate.threshold = max(self.settings["threshold"], noise * self.noise_multiplier)
                        with self._lock:
                            self._vad_threshold = round(gate.threshold, 4)
                        if not gate.start_ptt(capture_id, generation=generation):
                            raise MicError(gate.snapshot()["reason_code"])
                        gate_started = True
                        self._publish("listening", microphone_open=True)
                    continue
                # This level is deliberately bucketed for safe field diagnosis.
                # Never expose a waveform, utterance, timing fingerprint or mic path.
                level = math.sqrt(sum(sample[0] * sample[0] for sample in
                    struct.iter_unpack("<h", frame.pcm)) / (len(frame.pcm) // 2)) / 32768
                with self._lock:
                    self._vad_input_band = self._rms_band(level)
                    if level >= gate.threshold:
                        self._vad_speech_frames += 1
                accepted = gate.feed(frame.pcm, sequence=frame.sequence,
                                     timestamp=frame.timestamp, generation=generation)
                pending = gate.poll()
                if pending:
                    result, = pending
                    break
                if not accepted:
                    raise MicError("invalid_pcm_frame")
                if self._captured_bytes >= int(16000 * 2 * self.settings["max_duration"]):
                    gate.release_ptt(generation=generation)
                    pending = gate.poll()
                    result = replace(pending[0], code="max_buffer") if pending else None
                    break
                desired = "speech" if gate.snapshot()["speech_detected"] else "listening"
                if self._state != desired:
                    self._publish(desired, microphone_open=True)
            else:
                raise MicError("microphone_stream_closed")
        except _Done:
            pending = gate.poll()
            result = pending[0] if pending else None
        except _Finish:
            if gate_started:
                gate.release_ptt(generation=generation)
                pending = gate.poll()
                result = pending[0] if pending else None
            if result is None:
                result = PcmCapture(capture_id, generation, "cancelled", "no_speech", b"", 16000)
        except _Limit:
            if gate_started:
                gate.release_ptt(generation=generation)
                pending = gate.poll()
                result = replace(pending[0], code="max_duration") if pending else None
            if result is None:
                result = PcmCapture(capture_id, generation, "cancelled", "no_speech", b"", 16000)
        except _NoSpeech:
            result = PcmCapture(capture_id, generation, "cancelled", "no_speech", b"", 16000)
        except _Cancelled:
            result = PcmCapture(capture_id, generation, "cancelled", "user_cancelled", b"", 16000)
        except MicError as error:
            if error.code == "pcm_duration_limit" and gate is not None and gate.snapshot()["speech_detected"]:
                gate.release_ptt(generation=generation)
                pending = gate.poll()
                result = replace(pending[0], code="max_duration") if pending else None
            else:
                self._publish("failed", error.code)
                raise
        except (TurnCancelled, KeyboardInterrupt):
            self._publish("idle", "user_cancelled")
            raise
        except Exception:
            self._publish("failed", "microphone_capture_failed")
            raise MicError("microphone_capture_failed") from None
        finally:
            cleaned = True
            try:
                if frames is not None and hasattr(frames, "close"):
                    frames.close()
            except BaseException:
                cleaned = False
            try:
                if source is not None and source.close() is False:
                    cleaned = False
            except BaseException:
                cleaned = False
            if gate is not None:
                gate.close()
            with self._lock:
                if source is not None:
                    self._source, self._microphone_open = (None, False) if cleaned else (source, True)
                    self._cleanup_failed = not cleaned
            if not cleaned:
                self._publish("failed", "pcm_cleanup_failed")
                raise MicError("pcm_cleanup_failed") from None
        if result is None:
            raise MicError("invalid_pcm_result")
        with self._lock:
            if self._cancel.is_set() or self._closed.is_set():
                result = replace(result, status="cancelled", code="user_cancelled", pcm=b"")
            if result.status == "ready":
                self._ready = result
        self._publish("ready" if result.status == "ready" else "idle", result.code, microphone_open=False)
        return result

    def transcribe_capture(self, capture, client, *, model="gpt-4o-mini-transcribe", language=None,
                           cancel_check=None, transcriber=None):
        """One consumed ready capture -> existing STT; never record a second time."""
        if not self._operation.acquire(blocking=False):
            raise MicError("microphone_busy")
        path = None
        try:
            with self._lock:
                if self._closed.is_set() or self._cancel.is_set():
                    raise TurnCancelled()
                if not isinstance(capture, PcmCapture):
                    raise MicError("invalid_pcm_result")
                if capture.status != "ready":
                    return None
                if capture is not self._ready or capture.generation != self._last_generation:
                    raise MicError("stale_transcript")
                self._ready = None  # Mark consumed before calling an untrusted/reentrant provider.
            self._idle.clear()
            def check():
                if self._closed.is_set() or self._cancel.is_set():
                    raise TurnCancelled()
                if cancel_check is not None:
                    cancel_check()
                if self._busy_output():
                    raise MicError("audio_focus_busy")
            check()
            if (capture.sample_rate != 16000 or type(capture.pcm) is not bytes or not capture.pcm
                    or len(capture.pcm) % 2 or len(capture.pcm) > int(16000 * 2 * self.settings["max_duration"])):
                raise MicError("invalid_pcm_result")
            self._publish("transcribing", capture.code)
            path = audio._new_wav(audio.AudioRecordingError)
            # mkstemp is private 0600, has no inherited FD, and never uses a transcript filename.
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(capture.pcm)
            info = audio._wav_info(Path(path))
            if info.get("sample_rate") != 16000 or info.get("channels") != 1:
                raise MicError("invalid_pcm_result")
            check()
            result = (transcriber or audio.transcribe)(client, Path(path), model=model, language=language)
            check()
            if type(result) is not str or len(result) > 16384:
                raise MicError("invalid_transcript")
            self._publish("idle", "transcribed")
            return result
        except (TurnCancelled, KeyboardInterrupt):
            self._publish("idle", "user_cancelled")
            raise
        except BaseException:
            self._publish("failed", "transcription_failed")
            raise
        finally:
            try:
                if path is not None:
                    try:
                        audio._discard(Path(path))
                    except BaseException:
                        with self._lock:
                            self._pending_wav, self._cleanup_failed = Path(path), True
                        self._publish("failed", "audio_cleanup_failed")
                        raise
            finally:
                self._idle.set()
                self._operation.release()

    def finish(self):
        """Second intentional press: end capture; valid speech may be transcribed."""
        with self._lock:
            if self._state not in ("calibrating", "listening", "speech"):
                return False
            self._finish.set()
            return True

    def cancel(self):
        """Stop/visibility/mode invalidation: discard partial and late transcripts."""
        with self._lock:
            self._cancel.set()
            self._ready = None
            return self._state not in ("idle", "closed")

    def close(self, timeout=1.):
        if not _positive(timeout, 5):
            raise MicError("invalid_cleanup_timeout")
        self._closed.set()
        self.cancel()
        if not self._idle.wait(timeout):
            self._publish("failed", "pcm_cleanup_pending")
            return False
        with self._lock:
            if self._source is not None:
                try:
                    cleaned = self._source.close() is not False
                except Exception:
                    cleaned = False
                if not cleaned:
                    self._publish("failed", "pcm_cleanup_failed")
                    return False
                self._source = None
            if self._pending_wav is not None:
                try:
                    audio._discard(self._pending_wav)
                except BaseException:
                    self._publish("failed", "audio_cleanup_failed")
                    return False
                self._pending_wav = None
            self._cleanup_failed = False
        self._publish("closed", "input_closed", microphone_open=False)
        return True
