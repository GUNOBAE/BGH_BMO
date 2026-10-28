"""One microphone owner for finite PTT and optional local wake listening.

The normal/default path delegates to the existing finite ``MicPcmSource``.
Explicit wake opt-in with a READY custom detector starts one reader. A PTT
cursor consumes that reader's stream rather than opening a second arecord.
No source is created by import, construction, snapshots or missing-model use.
"""
from __future__ import annotations

from collections import deque
from threading import Condition, Event, RLock, Thread, current_thread
import math
import time

from ...conversation import TurnCancelled
from ..contracts import request_id_ok
from .microphone import MicError, PcmFrame


class _Suppressed(Exception):
    pass


class MicrophoneBroker:
    def __init__(self, source_factory, *, detector=None, on_trigger=None,
                 playback_active=None, allowed=None, clock=time.monotonic,
                 preroll_seconds=.4, cooldown=.8):
        if (not callable(source_factory) or type(preroll_seconds) not in (int, float)
                or not math.isfinite(preroll_seconds) or not .3 <= preroll_seconds <= .5
                or type(cooldown) not in (int, float) or not math.isfinite(cooldown)
                or not .3 <= cooldown <= 5):
            raise MicError("invalid_mic_configuration")
        self._factory, self.detector, self.on_trigger = source_factory, detector, on_trigger
        self.playback_active, self.allowed = playback_active or (lambda: False), allowed or (lambda: True)
        self.clock, self.cooldown = clock, float(cooldown)
        self._condition = Condition(RLock())
        self._stop = Event()
        self._thread = self._source = self._capture = None
        self._wake, self._closed, self._uncertain = False, False, False
        self._ring, self._ring_bytes = deque(), 0
        self._preroll_bytes = round(16000 * 2 * preroll_seconds)
        self._pending_until, self._cooldown_until, self._serial = 0., 0., 0
        self._state, self._code = "PTT_ONLY", "disabled"
        self._blocked = False

    def _now(self):
        stamp = self.clock()
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
            raise MicError("invalid_capture_clock")
        return stamp

    def _busy(self, *, include_allowed=True):
        try:
            return self.playback_active() is not False or include_allowed and self.allowed() is not True
        except Exception:
            return True

    def snapshot(self):
        with self._condition:
            return dict(state=self._state, reason_code=self._code, wake_enabled=self._wake,
                        microphone_open=self._source is not None,
                        capture_active=self._capture is not None,
                        buffered_bytes=self._ring_bytes,
                        preroll_ms=round(self._preroll_bytes / 32),
                        reader_alive=bool(self._thread and self._thread.is_alive()),
                        cleanup_verified=not self._uncertain,
                        automatic_capture=False, real_microphone="NOT_RUN",
                        real_wake_word="NOT_RUN", contains_audio=False)

    def source_factory(self):
        """PttRecorder-compatible lazy source; this call never opens hardware."""
        return _BrokerSource(self)

    def start_wake(self, *, live_opt_in=False):
        if live_opt_in is not True:
            with self._condition:
                self._code = "microphone_opt_in_required"
            return False
        with self._condition:
            if self._closed or self._uncertain or self._capture is not None:
                self._code = "input_closed" if self._closed else "microphone_busy"
                return False
            if self._wake:
                return True
            try:
                ready = self.detector is not None and self.detector.load() is True
                snapshot = self.detector.snapshot() if self.detector is not None else {}
            except Exception:
                self._state, self._code = "MODEL_ERROR", "wake_detector_failed"
                return False
            if not ready:
                self._state = snapshot.get("status", "MODEL_MISSING")
                self._code = snapshot.get("reason_code", "wake_model_missing")
                return False  # PTT remains available; no microphone source made.
            self._stop.clear()
            self._wake, self._state, self._code = True, "WAKE_LISTENING", "ok"
            self._thread = Thread(target=self._read, name="bmo-local-mic-owner", daemon=True)
            self._thread.start()
            return True

    def _clear_ring(self):
        self._ring.clear()
        self._ring_bytes = 0

    def _reset_detector(self):
        if self.detector is not None:
            try:
                self.detector.reset()
                if self._wake and self.detector.snapshot().get("status") != "READY":
                    self._wake, self._state, self._code = False, "WAKE_ERROR", "wake_detector_failed"
            except Exception:
                self._wake, self._state, self._code = False, "WAKE_ERROR", "wake_detector_failed"

    def _release_source(self, source):
        try:
            clean = source.close() is not False
        except BaseException:
            clean = False
        with self._condition:
            if clean and self._source is source:
                self._source = None
            if not clean:
                self._uncertain, self._wake = True, False
                self._state, self._code = "CLEANUP_FAILED", "pcm_cleanup_failed"
            self._condition.notify_all()
        return clean

    def _reader_check(self):
        with self._condition:
            if self._stop.is_set() or not self._wake:
                raise TurnCancelled()
            if self._busy(include_allowed=self._capture is None):
                raise _Suppressed()

    def _read(self):
        try:
            while not self._stop.is_set():
                with self._condition:
                    if not self._wake or self._uncertain:
                        return
                    now = self._now()
                    busy = self._busy(include_allowed=self._capture is None)
                    if busy:
                        self._blocked = True
                        self._cooldown_until = now + self.cooldown
                        self._clear_ring()
                        self._pending_until = 0.
                        self._state = "SUPPRESSED"
                    elif self._blocked:
                        self._blocked = False
                        self._reset_detector()
                    if not self._wake:
                        return
                    if busy or now < self._cooldown_until:
                        self._condition.wait(.04)
                        continue
                    self._serial += 1
                    serial = self._serial
                    source = self._factory()
                    self._source = source
                    self._state = "CAPTURING" if self._capture else "WAKE_LISTENING"
                rollover = False
                previous_sequence = -1
                previous_time = None
                try:
                    if getattr(source, "sample_rate", None) != 16000:
                        raise MicError("pcm_audio_format_failed")
                    for frame in source.frames(f"wake-{serial}", generation=serial,
                                               cancel_check=self._reader_check):
                        self._reader_check()
                        if (not isinstance(frame, PcmFrame) or type(frame.pcm) is not bytes
                                or not frame.pcm or len(frame.pcm) % 2 or len(frame.pcm) > 3200):
                            raise MicError("invalid_pcm_frame")
                        trigger = False
                        with self._condition:
                            now = self._now()
                            if (type(frame.sequence) is not int or frame.sequence <= previous_sequence
                                    or type(frame.generation) is not int or frame.generation != serial
                                    or type(frame.timestamp) not in (int, float)
                                    or not math.isfinite(frame.timestamp) or frame.timestamp < 0
                                    or frame.timestamp > now + .05 or now - frame.timestamp > .25
                                    or previous_time is not None and frame.timestamp < previous_time):
                                raise MicError("invalid_pcm_frame")
                            previous_sequence, previous_time = frame.sequence, frame.timestamp
                            if self._capture is not None:
                                capture = self._capture
                                if capture._queued_bytes + len(frame.pcm) > 400000:
                                    capture._error = "pcm_byte_limit"
                                elif capture._error is None:
                                    capture._queue.append((frame.pcm, False, frame.timestamp))
                                    capture._queued_bytes += len(frame.pcm)
                                self._condition.notify_all()
                                continue
                            self._ring.append((frame.pcm, frame.timestamp))
                            self._ring_bytes += len(frame.pcm)
                            while self._ring_bytes > self._preroll_bytes:
                                self._ring_bytes -= len(self._ring.popleft()[0])
                            if self._pending_until and now >= self._pending_until:
                                self._pending_until = 0.
                                self._clear_ring()
                                self._reset_detector()
                                if not self._wake:
                                    raise MicError("wake_detector_failed")
                            if now >= self._pending_until and now >= self._cooldown_until:
                                trigger = self.detector.feed(frame.pcm) is True
                                if self.detector.snapshot().get("status") != "READY":
                                    raise MicError("wake_detector_failed")
                                if trigger:
                                    self._pending_until = now + 2.
                                    self._state = "WAKE_PENDING"
                        if trigger:
                            try:
                                if self.on_trigger is not None:
                                    self.on_trigger()
                            except Exception:
                                with self._condition:
                                    self._pending_until = 0.
                                    self._cooldown_until = self._now() + self.cooldown
                                    self._clear_ring()
                                    self._reset_detector()
                    raise MicError("microphone_stream_closed")
                except _Suppressed:
                    with self._condition:
                        if self._capture is not None:
                            self._capture._error = "audio_focus_busy"
                        self._clear_ring()
                        self._pending_until = 0.
                        self._blocked = True
                        self._cooldown_until = self._now() + self.cooldown
                        self._state = "SUPPRESSED"
                except TurnCancelled:
                    return
                except MicError as error:
                    if error.code == "pcm_duration_limit":
                        rollover = True  # Finite child retires before the next one.
                    else:
                        with self._condition:
                            if self._capture is not None:
                                self._capture._error = error.code
                            self._wake, self._state, self._code = False, "WAKE_ERROR", error.code
                        return
                finally:
                    if not self._release_source(source):
                        return
                if rollover:
                    continue
        except Exception:
            with self._condition:
                self._wake, self._state, self._code = False, "WAKE_ERROR", "wake_reader_failed"
                if self._capture is not None:
                    self._capture._error = "microphone_read_failed"
        finally:
            with self._condition:
                self._condition.notify_all()

    def _acquire(self, capture):
        with self._condition:
            if self._closed or self._uncertain or self._capture is not None:
                raise MicError("input_closed" if self._closed else "microphone_busy")
            if self._busy(include_allowed=False):
                raise MicError("audio_focus_busy")
            # An old failed reader must finish cleanup before finite PTT starts.
            if self._source is not None and not self._wake:
                raise MicError("microphone_busy")
            self._capture = capture
            capture._streaming = self._wake
            self._pending_until = 0.
            if self._wake:
                for pcm, stamp in self._ring:
                    capture._queue.append((pcm, True, stamp))
                    capture._queued_bytes += len(pcm)
                self._clear_ring()
                self._state = "CAPTURING"
            else:
                try:
                    source = self._factory()
                except Exception:
                    self._capture = None
                    raise MicError("microphone_start_failed") from None
                self._source, capture._finite = source, source
                if getattr(source, "sample_rate", None) != 16000:
                    if self._release_source(source):
                        self._capture = None
                    raise MicError("pcm_audio_format_failed")
                self._state = "CAPTURING"
            self._condition.notify_all()

    def _release_capture(self, capture):
        with self._condition:
            if self._capture is not capture:
                return
            self._capture = None
            self._clear_ring()
            self._pending_until = 0.
            self._cooldown_until = self._now() + self.cooldown
            if self.detector is not None:
                self._reset_detector()
            if not self._uncertain:
                self._state = "WAKE_LISTENING" if self._wake else "PTT_ONLY"
            self._condition.notify_all()

    def stop_wake(self):
        """Disable persistent ownership; a concurrent capture is cancelled too."""
        with self._condition:
            self._wake = False
            self._stop.set()
            self._clear_ring()
            self._pending_until = 0.
            if self._capture is not None and self._capture._streaming:
                self._capture._error = "user_cancelled"
            source, thread = self._source, self._thread
            self._condition.notify_all()
        # Only the persistent reader's owned child may be stopped here. Finite
        # GP8 capture is independent of wake OFF and remains usable.
        persistent = thread is not None and thread.is_alive()
        clean = self._release_source(source) if persistent and source is not None else True
        if thread is not None and thread is not current_thread():
            thread.join(timeout=2.)
            clean = clean and not thread.is_alive()
        if self.detector is not None:
            try:
                clean = self.detector.close() is not False and clean
            except Exception:
                clean = False
        with self._condition:
            clean = clean and not self._uncertain
            if not clean:
                self._uncertain, self._state, self._code = True, "CLEANUP_FAILED", "pcm_cleanup_failed"
            elif not self._closed:
                self._state, self._code = "CAPTURING" if self._capture is not None else "PTT_ONLY", "disabled"
            self._condition.notify_all()
        return clean

    def close(self):
        with self._condition:
            self._closed = True
            capture = self._capture
        clean = self.stop_wake()
        if capture is not None:
            clean = capture.close() is not False and clean
        with self._condition:
            clean = clean and not self._uncertain
            if clean:
                self._state, self._code = "CLOSED", "input_closed"
        return clean


class _BrokerSource:
    sample_rate = 16000
    simulated = False

    def __init__(self, owner):
        self.owner = owner
        self._queue, self._queued_bytes = deque(), 0
        self._finite, self._error, self._observer = None, None, None
        self._streaming, self._started, self._closed = False, False, False
        self._close_ok = None
        self._close_lock = RLock()

    def set_timing_observer(self, observer):
        self._observer = observer

    def frames(self, capture_id, *, generation, cancel_check=None):
        if not request_id_ok(capture_id) or type(generation) is not int or generation < 0:
            raise MicError("invalid_capture")
        if self._started or self._closed:
            raise MicError("duplicate_capture")
        self._started = True
        self.owner._acquire(self)
        try:
            if not self._streaming:
                if callable(getattr(self._finite, "set_timing_observer", None)):
                    self._finite.set_timing_observer(self._observer)
                yield from self._finite.frames(capture_id, generation=generation, cancel_check=cancel_check)
                return
            sequence = 0
            while True:
                if cancel_check is not None:
                    cancel_check()
                with self.owner._condition:
                    if self._closed:
                        raise TurnCancelled()
                    if self._error is not None:
                        if self._error == "user_cancelled":
                            raise TurnCancelled()
                        raise MicError(self._error)
                    if not self.owner._wake:
                        raise MicError("microphone_stream_closed")
                    if not self._queue:
                        self.owner._condition.wait(.04)
                        continue
                    pcm, buffered, stamp = self._queue.popleft()
                    self._queued_bytes -= len(pcm)
                age = self.owner._now() - stamp
                if buffered and age > .5:
                    continue  # Expired preroll is never replayed as fresh speech.
                if not buffered and age > .25:
                    raise MicError("stale_pcm")
                # Buffered samples keep their PCM; the monotonic timestamp is
                # their receipt by this finite cursor, not a fabricated device
                # capture time before the newly admitted PTT turn existed.
                yield PcmFrame(pcm, sequence, self.owner._now(), generation)
                sequence += 1
        finally:
            self.close()

    def close(self):
        with self._close_lock:
            if self._closed and self._close_ok is True:
                return True
            self._closed = True
            clean = True
            if self._finite is not None:
                clean = self.owner._release_source(self._finite)
            with self.owner._condition:
                self._queue.clear()
                self._queued_bytes = 0
            if clean:
                self.owner._release_capture(self)
            self._close_ok = clean
            return clean
