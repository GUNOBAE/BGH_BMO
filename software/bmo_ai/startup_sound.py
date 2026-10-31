"""A fixed, privately cached BMO-inspired startup chime; no cloud or TTS.

This is an original synthesized three-note computer boot sound, NOT an
Adventure Time/Apple recording. Users may install a properly owned local
WAV as ~/.config/bmo/startup_chime.wav; it never leaves the Pi.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import math
import os
from pathlib import Path
import secrets
import struct
import wave

from . import audio
from .private_credentials import CredentialError, _close, _open_directory, _private_file

RATE = 24000
SECONDS = 1.20
NOTES = (
    (.05, .56, 392.0, .24),
    (.18, .70, 523.25, .20),
    (.40, .78, 659.25, .16),
)
CODES = frozenset(("startup_sound_invalid", "startup_sound_unsafe", "startup_sound_storage_failed",
                   "startup_sound_missing"))


class StartupSoundError(RuntimeError):
    def __init__(self, code="startup_sound_invalid"):
        self.code = code if type(code) is str and code in CODES else "startup_sound_invalid"
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


def _read_payload(path):
    directory = descriptor = None
    try:
        directory = _open_directory(path.parent)
        if directory is None:
            raise FileNotFoundError()
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                             os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        _private_file(before, size_limit=384_000)
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(384_001)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise StartupSoundError()
        with wave.open(io.BytesIO(payload), "rb") as source:
            channels, width, rate, count = (source.getnchannels(), source.getsampwidth(),
                                           source.getframerate(), source.getnframes())
            if (source.getcomptype() != "NONE" or channels != 1 or width != 2
                    or not 8000 <= rate <= 48000 or not .15 <= count / rate <= 3
                    or len(source.readframes(count + 1)) != count * channels * width):
                raise StartupSoundError()
        if not 44 <= len(payload) <= 384_000:
            raise StartupSoundError()
        return payload
    except FileNotFoundError:
        raise
    except (CredentialError, OSError, ValueError, TypeError, EOFError, wave.Error):
        raise StartupSoundError("startup_sound_unsafe") from None
    finally:
        _close(descriptor)
        _close(directory)


def startup_chime_file(*, home=None, allow_synthesized=True):
    """Use an owned local WAV or atomically cache one original offline chime.

    The descriptor walk refuses directory symlinks and existing data is never
    replaced. Call only in an explicit app startup, never during import/status.
    No audio device is opened here; playback remains in the greeting focus owner.
    """
    root = Path.home() if home is None else Path(home)
    if (type(allow_synthesized) is not bool or not root.is_absolute()
            or ".." in root.parts or "\0" in str(root)):
        raise StartupSoundError("startup_sound_unsafe")
    custom = root / ".config" / "bmo" / "startup_chime.wav"
    try:
        _read_payload(custom)
        return custom
    except FileNotFoundError:
        pass
    # Character Life's user-supplied recording must never silently become the
    # old three-note cache. Missing transfer is a quiet, explicit state; no
    # cache lookup, generation or write is permitted by this policy.
    if not allow_synthesized:
        raise StartupSoundError("startup_sound_missing")
    cache = root / ".cache" / "bmo" / "startup-chime-v1"
    target = cache / "boot.wav"
    try:
        _read_payload(target)
        return target
    except FileNotFoundError:
        pass
    directory = descriptor = None
    stage = None
    try:
        directory = _open_directory(cache, create=True)
        payload = synthesized_wav_bytes()
        stage = ".boot-" + secrets.token_hex(12) + ".tmp"
        descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as out:
            descriptor = None
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(stage, target.name, src_dir_fd=directory, dst_dir_fd=directory,
                    follow_symlinks=False)
            os.fsync(directory)
        except FileExistsError:
            pass  # Another completed publisher won; validate its data below.
        os.unlink(stage, dir_fd=directory)
        stage = None
    except (CredentialError, OSError, ValueError, TypeError):
        raise StartupSoundError("startup_sound_storage_failed") from None
    finally:
        _close(descriptor)
        if stage is not None and directory is not None:
            try:
                os.unlink(stage, dir_fd=directory)
            except OSError:
                pass
        _close(directory)
    try:
        _read_payload(target)
    except FileNotFoundError:
        raise StartupSoundError("startup_sound_storage_failed") from None
    return target


@contextmanager
def startup_chime_asset(*, home=None, allow_synthesized=True):
    """Play a verified private snapshot, never a mutable user-selected path."""
    path = None
    try:
        payload = _read_payload(startup_chime_file(home=home, allow_synthesized=allow_synthesized))
        path = audio._new_wav(audio.AudioSynthesisError)
        path.write_bytes(payload)
        yield path, audio._wav_info(path)
    except (OSError, audio.AudioError):
        raise StartupSoundError() from None
    finally:
        if path is not None:
            audio._discard(path, suppress=True)
