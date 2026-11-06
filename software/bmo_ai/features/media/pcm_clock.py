"""One owned nonblocking ALSA PCM stream with a hardware-delay sample clock.

This is a local music backend, not another audio owner. FeatureHost must hold
its existing AudioFocus lease before calling start. No browser audio, mixer
change, shell, provider, or daemon is involved. Tests inject a silent sink and
never open ALSA. Position is submitted PCM frames minus ALSA's queued delay;
it is evidence of the output stream's progress, not proof of audible sound.
"""
import ctypes
import ctypes.util
import errno
import math
import os
import threading
import time
import wave

from ..contracts import FeatureError


class AlsaPcmSink:
    """Small libasound adapter; all calls run only on the playback worker."""
    def __init__(self, device, rate, channels):
        name = ctypes.util.find_library("asound")
        if not name:
            raise FeatureError("media_alsa_unavailable")
        self.lib = ctypes.CDLL(name)
        self.handle = ctypes.c_void_p()
        for method, restype, args in (
            ("snd_pcm_open", ctypes.c_int, [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p, ctypes.c_int, ctypes.c_int]),
            ("snd_pcm_set_params", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_uint]),
            ("snd_pcm_writei", ctypes.c_long, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]),
            ("snd_pcm_delay", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)]),
            ("snd_pcm_prepare", ctypes.c_int, [ctypes.c_void_p]),
            ("snd_pcm_state", ctypes.c_int, [ctypes.c_void_p]),
            ("snd_pcm_start", ctypes.c_int, [ctypes.c_void_p]),
            ("snd_pcm_drop", ctypes.c_int, [ctypes.c_void_p]),
            ("snd_pcm_close", ctypes.c_int, [ctypes.c_void_p]),
        ):
            function = getattr(self.lib, method)
            function.restype, function.argtypes = restype, args
        # NONBLOCK: a stalled device can never trap shutdown in write/drain.
        if self.lib.snd_pcm_open(ctypes.byref(self.handle), (device or "default").encode(), 0, 1) < 0:
            raise FeatureError("media_output_unavailable")
        self.frame_bytes = channels * 2
        try:
            # S16_LE / RW_INTERLEAVED, 100ms target buffer, existing device.
            if self.lib.snd_pcm_set_params(self.handle, 2, 3, channels, rate, 1, 100000) < 0:
                raise FeatureError("media_output_format")
        except BaseException:
            self.close()
            raise

    def write(self, block):
        pointer = ctypes.create_string_buffer(block)
        count = self.lib.snd_pcm_writei(self.handle, pointer, len(block) // self.frame_bytes)
        if count in (-errno.EAGAIN, -errno.EINTR):
            return 0
        if count < 0:
            # An xrun loses precise continuity; report failure instead of
            # presenting guessed sample positions as synchronized playback.
            raise FeatureError("media_output_underrun" if count == -errno.EPIPE else "media_output_failed")
        return count

    def delay(self):
        count = ctypes.c_long()
        result = self.lib.snd_pcm_delay(self.handle, ctypes.byref(count))
        if result == -errno.EPIPE:
            # An already empty output has zero queued frames. A subsequent
            # write still reports xrun as a discontinuity/failure.
            return 0
        if result < 0:
            raise FeatureError("media_output_clock_failed")
        return max(0, count.value)

    def finish_input(self):
        # set_params may choose a start threshold larger than a very short
        # WAV. Such a prepared stream otherwise queues forever without ever
        # consuming a sample. Start its final partial buffer explicitly; a
        # long stream that already auto-started keeps running untouched.
        if self.lib.snd_pcm_state(self.handle) == 2:  # SND_PCM_STATE_PREPARED
            if self.lib.snd_pcm_start(self.handle) < 0:
                raise FeatureError("media_output_start_failed")

    def close(self):
        if self.handle:
            self.lib.snd_pcm_drop(self.handle)
            self.lib.snd_pcm_close(self.handle)
            self.handle = ctypes.c_void_p()


class SampleClockWavBackend:
    """PCM16 music output with pause/seek at the measured consumed frame.

    Pause stops and drops only this owned stream after reading its last frame
    position; resume opens at that exact source frame. Volume changes do the
    same. This works on devices lacking native snd_pcm_pause support and never
    starts old/new output concurrently. Failure to stop retains ownership.
    """
    simulated = False
    supported_types = frozenset({"audio/wav"})
    capabilities = ("stop", "pause", "resume", "seek", "replay")
    clock_quality = "alsa_submitted_minus_delay"

    def __init__(self, *, device=None, enabled=False, volume_reviewed=False,
                 clock=time.monotonic, volume=None, sink_factory=None):
        from ...audio import _device_args
        _device_args(device)
        if type(enabled) is not bool or type(volume_reviewed) is not bool:
            raise FeatureError("invalid_media_backend")
        self.verified = enabled and volume_reviewed
        self.device, self.clock, self.volume = device, clock, volume
        self._sink_factory = sink_factory or AlsaPcmSink
        self._thread = None
        self._stop = threading.Event()
        self._guard = threading.Lock()
        self._handle = None
        self._frame = self._rate = 0
        self._paused = False
        self._terminal = self._error = None
        self._ready = threading.Event()
        self._output_started = False
        self._retained_sink = None

    def start(self, handle):
        if not self.verified:
            raise FeatureError("media_backend_unverified")
        if self._handle is not None:
            raise FeatureError("media_busy")
        self._handle, self._frame, self._terminal, self._error = handle, 0, None, None
        self._paused = False
        try:
            self._launch()
        except BaseException:
            self.stop()
            raise

    def _launch(self):
        self._stop.clear(); self._ready.clear()
        self._output_started = False
        self._thread = threading.Thread(target=self._run, name="bmo-owned-music-pcm", daemon=True)
        self._thread.start()
        # Ready means ALSA opened + PCM validation, not sound heard. The
        # position remains unchanged until the sink reports consumed frames.
        if not self._ready.wait(2.):
            self._halt()
            raise FeatureError("media_output_start_timeout")
        if self._error is not None:
            self._halt()
            raise FeatureError(self._error)

    def _run(self):
        sink = None
        try:
            with open("/proc/self/fd/" + str(self._handle.fd), "rb") as source, wave.open(source, "rb") as wav:
                channels, rate, frames = wav.getnchannels(), wav.getframerate(), wav.getnframes()
                if (wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or channels not in (1, 2)
                        or not 8000 <= rate <= 96000 or not 0 < frames / rate <= 7200):
                    raise FeatureError("invalid_media_wav")
                base = min(self._frame, frames)
                wav.setpos(base)
                with self._guard:
                    self._rate = rate
                sink = self._sink_factory(self.device, rate, channels)
                submitted, block, ended, input_finished = 0, b"", False, False
                last_progress = self.clock()
                self._ready.set()
                while not self._stop.is_set():
                    if not block and not ended:
                        block = wav.readframes(max(1, rate // 50))
                        ended = not block
                        if block and self.volume is not None:
                            from ...safe_playback import _reduce_chunk
                            block = _reduce_chunk(block, 2, self.volume.snapshot()["effective_level"])
                    if block:
                        accepted = sink.write(block)
                        if type(accepted) is not int or not 0 <= accepted <= len(block) // (channels * 2):
                            raise FeatureError("media_output_failed")
                        submitted += accepted
                        block = block[accepted * channels * 2:]
                    if ended and not input_finished:
                        finish = getattr(sink, "finish_input", None)
                        if callable(finish):
                            finish()
                        input_finished = True
                    delay = sink.delay()
                    consumed = base + max(0, submitted - min(submitted, delay))
                    with self._guard:
                        previous, self._frame = self._frame, max(self._frame, consumed)
                        if consumed > base:
                            self._output_started = True
                    if self._frame > previous:
                        last_progress = self.clock()
                    if ended and delay == 0:
                        with self._guard:
                            self._frame = frames
                            self._terminal = "ended"
                        break
                    if self.clock() - last_progress > 3.:
                        raise FeatureError("media_output_stalled")
                    self._stop.wait(.005)
                # Freeze the final consumed frame before dropping the buffer.
                if self._stop.is_set():
                    delay = sink.delay()
                    with self._guard:
                        self._frame = max(self._frame, base + max(0, submitted - min(submitted, delay)))
        except BaseException as error:
            with self._guard:
                self._error = error.code if isinstance(error, FeatureError) else "media_output_failed"
                self._terminal = "failed"
        finally:
            if sink is not None:
                try:
                    sink.close()
                except BaseException:
                    with self._guard:
                        self._error, self._terminal = "media_output_cleanup_failed", "failed"
                        self._retained_sink = sink
            self._ready.set()

    def _halt(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.)
            if self._thread.is_alive():
                raise FeatureError("cleanup_failed")
            self._thread = None
        if self._retained_sink is not None:
            try:
                self._retained_sink.close()
            except BaseException:
                raise FeatureError("cleanup_failed") from None
            self._retained_sink = None

    def position(self):
        with self._guard:
            return self._frame / self._rate if self._rate else 0.

    @property
    def output_started(self):
        with self._guard:
            return self._output_started

    def poll(self):
        with self._guard:
            return self._terminal

    def pause(self):
        if self._handle is None:
            raise FeatureError("media_inactive")
        if not self._paused:
            self._halt()
            self._paused = True

    def resume(self):
        if self._handle is None:
            raise FeatureError("media_inactive")
        if self._paused:
            self._paused = False
            if self._terminal != "ended":
                self._launch()

    def seek(self, seconds):
        if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                or self._handle is None or not 0 <= seconds <= self._handle.duration):
            raise FeatureError("invalid_media_position")
        self._halt()
        with self._guard:
            frames = round(self._handle.duration * self._rate)
            at_end = seconds == self._handle.duration
            self._frame = frames if at_end else min(frames - 1, round(seconds * self._rate))
            self._terminal, self._error = "ended" if at_end else None, None
            self._output_started = False
        if not self._paused and not at_end:
            self._launch()

    def volume_changed(self):
        # Live level is applied to each next small PCM block. Mute additionally
        # drops queued output and resumes from its consumed source frame.
        if self._handle is None:
            return False
        if self.volume is not None and self.volume.snapshot()["effective_level"] == 0:
            self._halt()
            if not self._paused and self._terminal != "ended":
                self._launch()
        return True

    def stop(self):
        self._halt()
        self._handle = None
        with self._guard:
            self._frame = self._rate = 0
            self._terminal = self._error = None
            self._output_started = False
        self._paused = False

    close = stop
