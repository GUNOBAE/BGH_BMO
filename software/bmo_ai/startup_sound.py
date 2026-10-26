"""A fixed, privately cached BMO-inspired startup chime; no cloud or TTS.

This is an original synthesized three-note computer boot sound, NOT an
Adventure Time/Apple recording. Users may install a properly owned local
WAV as ~/.config/bmo/startup_chime.wav; it never leaves the Pi.
"""
from __future__ import annotations

import io
import math
import os
from pathlib import Path
import stat
import struct
import wave

from . import audio

RATE = 24000
SECONDS = 1.20
NOTES = (
    (.05, .56, 392.0, .24),
    (.18, .70, 523.25, .20),
    (.40, .78, 659.25, .16),
)


class StartupSoundError(RuntimeError):
    def __init__(self, code="startup_sound_invalid"):
        self.code = code
        super().__init__("BMO startup audio was not available safely.")


def synthesized_wav_bytes():
    """Original fixed sound, deterministically encoded as 16-bit mono WAV."""
    pcm = bytearray()
    for i in range(round(SECONDS * RATE)):
        t = i / RATE
        value = 0.0
        for start, duration, hz, gain in NOTES:
            local = t - start
            if not 0 <= local <= duration:
                continue
            envelope = ((1 - math.exp(-local * 85)) * math.exp(-local * 5.0)
                        * min(1.0, max(0.0, (duration - local) / .07)))
            value += gain * envelope * (
                math.sin(math.tau * hz * local)
                + .25 * math.sin(math.tau * 2.01 * hz * local)
                + .08 * math.sin(math.tau * 3.9 * hz * local))
        fade = min(1.0, t / .013, max(0.0, (SECONDS - t) / .08))
        pcm.extend(struct.pack("<h", round(max(-.95, min(.95, value * fade)) * 32767)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm)
    return buffer.getvalue()


def _verified_wav(path):
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or not 44 <= info.st_size <= 384_000):
            raise StartupSoundError()
        details = audio._wav_info(path)
        if (details["channels"] != 1 or details["sample_width"] != 2
                or not .15 <= details["duration_seconds"] <= 3):
            raise StartupSoundError()
        return path
    except (OSError, ValueError, TypeError, audio.AudioError):
        raise StartupSoundError() from None


def _private_directory(path):
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise StartupSoundError("startup_sound_unsafe")
    except (OSError, ValueError):
        raise StartupSoundError("startup_sound_unsafe") from None


def startup_chime_file(*, home=None):
    """Read a locally supplied recording or create one fixed offline WAV once.

    Called only after face readiness, within the greeting AudioFocus lease.
    Never touches Fish/OpenAI, and never replaces a prior audio file.
    """
    root = Path.home() if home is None else Path(home)
    if not root.is_absolute() or ".." in root.parts:
        raise StartupSoundError("startup_sound_unsafe")
    custom = root / ".config" / "bmo" / "startup_chime.wav"
    if custom.exists() or custom.is_symlink():
        return _verified_wav(custom)
    cache = root / ".cache" / "bmo" / "startup-chime-v1"
    _private_directory(cache)
    target = cache / "boot.wav"
    if target.exists() or target.is_symlink():
        return _verified_wav(target)
    payload = synthesized_wav_bytes()
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        return _verified_wav(target)
    except OSError:
        raise StartupSoundError("startup_sound_storage_failed") from None
    try:
        with os.fdopen(descriptor, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        return _verified_wav(target)
    except (OSError, StartupSoundError):
        # Only our newly created, expected regular leaf may be removed.
        try:
            info = target.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                target.unlink()
        except OSError:
            pass
        raise StartupSoundError("startup_sound_storage_failed") from None
