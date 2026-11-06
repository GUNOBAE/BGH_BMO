"""Per-session output level, independent of synthesis fingerprints/OS mixers.

The default effective level is the existing .25, never an additional .25. WAV
sources declare their baked gain: .25 previews and 1.0 RAW are treated equally.
Changing level affects the next owned playback; the host cancels on mute so an
active player cannot continue sounding after the operator mutes it.
"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import stat
from threading import Lock
import wave

from . import audio
from .safe_playback import _reduce_chunk


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


class MasterVolume:
    def __init__(self, level=.25, muted=False):
        self._guard = Lock()
        self._level, self._muted = .25, False
        self.set(level=level, muted=muted)

    def set(self, *, level=None, muted=None):
        if (level is not None and (not _number(level) or not 0 <= level <= 1)
                or muted is not None and type(muted) is not bool):
            raise audio.AudioValidationError('Choose an output level from zero to one.', 'invalid_master_volume')
        with self._guard:
            if level is not None:
                self._level = float(level)
            if muted is not None:
                self._muted = muted
        return self.snapshot()

    def snapshot(self):
        with self._guard:
            return dict(level=self._level, muted=self._muted,
                        effective_level=0. if self._muted else self._level,
                        default_level=.25, applies='next_owned_playback',
                        os_mixer_changed=False, fingerprint_changed=False)

    @contextmanager
    def prepare_music_fd(self, descriptor, *, start_seconds=0.):
        """Scale a verified local-library descriptor without the TTS 24MiB cap.

        Music keeps its existing 64MiB/two-hour limit and never reopens an
        unverified catalogue path. PCM is processed in small blocks.
        """
        if type(descriptor) is not int or descriptor < 0:
            raise audio.AudioValidationError('A verified music descriptor is required.', 'invalid_media_file')
        if not _number(start_seconds) or not 0 <= start_seconds <= 7200:
            raise audio.AudioValidationError('The local music position is invalid.', 'invalid_media_position')
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 44 <= info.st_size <= 64*1024*1024:
            raise audio.AudioValidationError('The local music file is unsupported.', 'invalid_media_file')
        output = audio._new_wav(audio.AudioPlaybackError)
        try:
            with open('/proc/self/fd/'+str(descriptor), 'rb') as handle, wave.open(handle, 'rb') as source:
                channels, width, rate, frames = source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getnframes()
                if (source.getcomptype() != 'NONE' or width != 2 or channels not in (1, 2)
                        or not 8000 <= rate <= 96000 or not 0 < frames/rate <= 7200):
                    raise audio.AudioValidationError('The local music PCM is unsupported.', 'invalid_media_file')
                skip = min(frames, round(start_seconds*rate))
                if skip == frames:
                    raise audio.AudioValidationError('The local music playback has finished.', 'media_ended')
                source.setpos(skip)
                ratio = self.snapshot()['effective_level']
                copied = 0
                with wave.open(str(output), 'wb') as target:
                    target.setnchannels(channels);target.setsampwidth(width);target.setframerate(rate)
                    while True:
                        block = source.readframes(4096)
                        if not block:
                            break
                        copied += len(block)
                        if copied > 64*1024*1024:
                            raise audio.AudioValidationError('The local music PCM exceeds its limit.', 'invalid_media_file')
                        target.writeframesraw(_reduce_chunk(block, width, ratio))
                if copied != (frames-skip)*channels*width:
                    raise audio.AudioValidationError('The local music PCM is incomplete.', 'invalid_media_file')
            yield output
        except (wave.Error, EOFError, ValueError, OSError):
            raise audio.AudioPlaybackError('The local music volume copy could not be prepared.', 'audio_volume_failed') from None
        finally:
            audio._discard(output, suppress=True)

    @contextmanager
    def prepare(self, source, *, baked_gain=.25):
        """Yield an owned-playback path; leave source/cache bytes untouched.

        Amplification relative to an existing .25 preview is allowed up to its
        original RAW level; saturation is bounded per PCM sample. No synthesis,
        cache mutation, external mixer operation or provider call occurs.
        """
        if not _number(baked_gain) or not 0 < baked_gain <= 1:
            raise audio.AudioValidationError('The source gain provenance is required.', 'invalid_baked_gain')
        source = Path(source)
        info = audio._wav_info(source, allow_streaming_header=True)
        if info['duration_seconds'] > 7200:
            raise audio.AudioValidationError('Output exceeds its playback limit.', 'audio_too_long')
        level = self.snapshot()['effective_level']
        ratio = level / baked_gain
        if ratio == 1:
            yield source
            return
        output = audio._new_wav(audio.AudioPlaybackError)
        try:
            with wave.open(str(source), 'rb') as original, wave.open(str(output), 'wb') as target:
                target.setnchannels(original.getnchannels())
                target.setsampwidth(original.getsampwidth())
                target.setframerate(original.getframerate())
                while True:
                    block = original.readframes(4096)
                    if not block:
                        break
                    target.writeframesraw(_reduce_chunk(block, original.getsampwidth(), ratio))
            audio._wav_info(output)
            yield output
        except (wave.Error, EOFError, ValueError, OSError):
            raise audio.AudioPlaybackError('The local volume copy could not be prepared.', 'audio_volume_failed') from None
        finally:
            audio._discard(output, suppress=True)
