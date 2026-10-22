"""Single-output voice boundary for the integration launcher.

No devices, SDK clients, or processes are created on import/construction. Real
backends run only in explicit listen/speak calls; simulation creates a synthetic
PCM fixture and a harmless owned child instead of ALSA or cloud synthesis.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import sys
from threading import Event, Lock
import wave

from . import audio, fish_tts, local_piper
from .app import OFFLINE_KOREAN_UNSUPPORTED_EN, OFFLINE_NOTICE_EN
from .conversation import TurnCancelled
from .expression_contract import FACE_EMOTIONS
from .integration_face import wav_cues
from .pronunciation import spoken_bmo_name
from .safe_playback import attenuated_copy, validate_gain


def _player_environment():
    # The player has no reason to inherit credentials used by synthesis/brain.
    return {name: value for name, value in os.environ.items()
            if not name.startswith(("OPENAI_", "FISH_"))}


def synthetic_wav():
    """A finite voiced/silent PCM fixture; no recorded or model-generated audio."""
    path = audio._new_wav(audio.AudioSynthesisError)
    try:
        rate = 16000
        samples = []
        for index in range(int(rate * 0.8)):
            seconds = index / rate
            level = 12000 if 0.10 <= seconds < 0.35 else 7000 if 0.50 <= seconds < 0.70 else 0
            samples.append(round(level * math.sin(2 * math.pi * 220 * seconds)))
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        return path
    except BaseException:
        audio._discard(path, suppress=True)
        raise


class IntegrationVoice:
    """Persistent voice adapter; update emit/cancel_check before each worker turn.

    Injected synthesizers follow the existing synthesize_wav signatures and
    transfer ownership of their temporary result to this adapter. An injected
    player follows audio._run's signature. No raw reply text enters emit().
    """

    def __init__(self, config, client, *, emit, cancel_check=None, simulate=False,
                 fish_synthesizer=None, piper_synthesizer=None, player=None):
        self.config = config
        self.client = client
        self.emit = emit
        self.cancel_check = cancel_check
        self.simulate = bool(simulate)
        self.message = "Hello BMO."
        self._fish_synthesizer = fish_synthesizer
        self._piper_synthesizer = piper_synthesizer
        self._player = player
        self._closed = Event()
        self._idle = Event()
        self._idle.set()
        self._speaking = Lock()
        self._cache_lock = Lock()
        self._cache = None
        self._cache_generation = 0
        self.expression_metadata = {"emotion": "neutral", "emotion_validation": "none"}

    def _expression(self, *, notice=False):
        """Copy only safe expression metadata, never a reply or TTS direction."""
        value = self.expression_metadata
        emotion = value.get("emotion") if type(value) is dict else None
        validation = value.get("emotion_validation") if type(value) is dict else None
        return dict(emotion="neutral" if notice or emotion not in FACE_EMOTIONS else emotion,
                    emotion_validation="notice" if notice else validation if validation in
                    ("none", "manual", "validated", "degraded") else "none",
                    notice=bool(notice))

    @property
    def has_cache(self):
        """Whether this open session retains its last successful gained WAV."""
        with self._cache_lock:
            return not self._closed.is_set() and self._cache is not None

    def clear_cache(self):
        """Forget the sole private WAV and reject any in-flight cache promotion."""
        with self._cache_lock:
            previous, self._cache = self._cache, None
            self._cache_generation += 1
        if previous is not None:
            audio._discard(previous["path"], suppress=True)

    def invalidate_pending(self, cancel=None):
        """Invalidate a replacement without deleting the last success.

        The session can set its cancellation event under this same lock, so
        cancellation and successful cache promotion have one commit boundary.
        """
        with self._cache_lock:
            self._cache_generation += 1
            return cancel() if cancel is not None else None

    def _check(self, cancellation):
        if self._closed.is_set():
            raise TurnCancelled()
        if cancellation is not None:
            cancellation()

    def listen(self):
        cancellation = self.cancel_check
        self._check(cancellation)
        if self.simulate:
            return self.message
        path = audio.record_wav(self.config.seconds, self.config.mic_device,
                                cancel_check=lambda: self._check(cancellation),
                                env=_player_environment())
        try:
            self._check(cancellation)
            language = "ko" if self.config.language == "ko" else None
            message = audio.transcribe(self.client, path, model=self.config.stt_model,
                                       language=language)
            self._check(cancellation)
            return message
        finally:
            audio._discard(Path(path), suppress=True)

    def _synthesize(self, text, check, *, force_piper=False, notice=False):
        check()
        if self.simulate:
            return synthetic_wav(), "synthetic", notice
        if force_piper:
            synthesize = self._piper_synthesizer or local_piper.synthesize_wav
            return Path(synthesize(text, binary=self.config.local_piper_binary,
                                   model=self.config.local_piper_model,
                                   name_mode=self.config.fish_name_mode)), "piper", notice
        if self.config.tts_provider != "fish":
            raise audio.AudioValidationError(
                "The integration voice profile requires Fish Audio.", "invalid_tts_provider")
        spoken = spoken_bmo_name(text, self.config.fish_name_mode)
        if self.config.fish_delivery == "soft":
            spoken = fish_tts.SOFT_AARON_DIRECTION + spoken
        synthesize = self._fish_synthesizer or fish_tts.synthesize_wav
        try:
            return Path(synthesize(spoken, model=self.config.fish_model,
                                   reference_id=self.config.fish_voice_id,
                                   timeout=self.config.timeout, cancel_check=check)), "fish", notice
        except audio.AudioSynthesisError:
            check()
            if not self.config.offline_voice:
                raise
            english = local_piper.is_english_text(text)
            fallback = text if english else OFFLINE_KOREAN_UNSUPPORTED_EN
            return self._synthesize(fallback, check, force_piper=True, notice=notice or not english)

    def speak(self, text, *, on_playback_start=None, _force_piper=False, _notice=False):
        if not isinstance(text, str):
            raise audio.AudioValidationError("Speech input must be text.", "invalid_speech_text")
        if not text.strip():
            return
        if not self._speaking.acquire(blocking=False):
            raise audio.AudioPlaybackError("The voice player is busy.", "player_busy")
        self._idle.clear()
        with self._cache_lock:
            generation = self._cache_generation
        cancellation, emit = self.cancel_check, self.emit
        check = lambda: self._check(cancellation)
        raw = output = None
        source = "synthetic" if self.simulate else "piper" if _force_piper else "fish"
        try:
            check()
            validate_gain(self.config.fish_gain)
            raw, source, notice = self._synthesize(text, check, force_piper=_force_piper,
                                                  notice=_notice)
            expression = self._expression(notice=notice)
            # A synchronous provider may finish after cancellation. Discard its
            # late result before any prepared/start event or process creation.
            check()
            output = attenuated_copy(raw, self.config.fish_gain)
            info = audio._wav_info(output)
            cues = tuple(dict(cue) for cue in wav_cues(output))
            self._play(output, source, info, cues, check, emit, on_playback_start, expression)
            check()
            # A failed/cancelled replacement keeps the previous successful WAV.
            # Promotion transfers the final gained file, without another copy.
            previous = None
            with self._cache_lock:
                check()
                if generation == self._cache_generation and not self._closed.is_set():
                    previous, self._cache = self._cache, dict(
                        path=output, source=source, info=info, cues=cues, expression=expression)
                    output = None
            if previous is not None:
                audio._discard(previous["path"], suppress=True)
        except (TurnCancelled, KeyboardInterrupt):
            emit("cancelled", source=source)
            raise
        except BaseException:
            emit("error", source=source)
            raise
        finally:
            for path in (output, raw):
                if path is not None:
                    audio._discard(Path(path), suppress=True)
            self._speaking.release()
            self._idle.set()

    def _play(self, output, source, info, cues, check, emit, on_playback_start, expression):
        """The only player/event path, shared by a new utterance and repeat."""
        check()
        emit("prepared", path=str(output), source=source,
             cues=[dict(cue) for cue in cues], **expression)
        check()

        def started():
            check()
            if on_playback_start is not None:
                on_playback_start()
            check()
            emit("started", path=str(output), source=source, **expression)

        if self.simulate:
            command = [sys.executable, "-c", "import time; time.sleep(0.8)"]
        else:
            command = ["aplay", "-q", *audio._device_args(self.config.speaker_device), str(output)]
        player = self._player or audio._run
        player(command, timeout=info["duration_seconds"] + 10,
               error_type=audio.AudioPlaybackError, on_start=started,
               cancel_check=check, env=_player_environment())
        check()
        emit("ended", source=source)

    def repeat(self, *, on_playback_start=None):
        """Play the exact last success, without synthesis, gain or cue rebuild.

        Return False without spawning when empty/closed/busy. A failed or
        cancelled repeat retains the cache for the next explicit request.
        """
        if not self._speaking.acquire(blocking=False):
            return False
        self._idle.clear()
        cancellation, emit = self.cancel_check, self.emit
        check = lambda: self._check(cancellation)
        source = None
        try:
            with self._cache_lock:
                cached = self._cache
            if cached is None or self._closed.is_set():
                return False
            source = cached["source"]
            self._play(cached["path"], source, cached["info"], cached["cues"],
                       check, emit, on_playback_start, cached["expression"])
            return True
        except (TurnCancelled, KeyboardInterrupt):
            emit("cancelled", source=source)
            raise
        except BaseException:
            emit("error", source=source)
            raise
        finally:
            self._speaking.release()
            self._idle.set()

    def offline_notice(self, *, on_playback_start=None):
        """Existing fixed English notice; this is not an offline chat brain."""
        if self.config.offline_voice:
            self.speak(OFFLINE_NOTICE_EN, on_playback_start=on_playback_start,
                       _force_piper=True, _notice=True)

    def close(self):
        """Cancel owned playback; late synthesis cleans itself on return.

        Never wait indefinitely for synchronous cloud/Piper synthesis. Return
        whether the current operation has completed its cleanup.
        """
        self._closed.set()
        stopped = self._idle.wait(0.5)
        self.clear_cache()
        return stopped
