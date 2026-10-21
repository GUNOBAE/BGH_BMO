"""Safe, per-playback software attenuation for BMO (no mixer/system changes).

The native MAX98357A I2S route can bypass desktop software volume when ALSA
plughw is used. This module makes a temporary PCM WAV at a lower amplitude
before ALSA playback, without changing the source WAV or system configuration.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import wave

from . import audio

DEFAULT_GAIN = 0.30


def validate_gain(gain: float) -> float:
    if isinstance(gain, bool) or not isinstance(gain, (float, int)):
        raise audio.AudioValidationError("BMO playback gain must be a number.", "invalid_playback_gain")
    if not math.isfinite(gain) or not 0 < gain <= 1:
        raise audio.AudioValidationError("BMO playback gain must be above 0 and at most 1.", "invalid_playback_gain")
    return float(gain)


def _reduce_chunk(block: bytes, width: int, gain: float) -> bytes:
    """Scale signed little-endian 16/24/32-bit PCM or unsigned 8-bit PCM."""
    if width not in (1, 2, 3, 4) or len(block) % width:
        raise audio.AudioValidationError("Unsupported PCM sample width.", "invalid_wav")
    output = bytearray(len(block))
    signed = width > 1
    center = 128 if width == 1 else 0
    lo, hi = (0, 255) if width == 1 else (-(1 << (width * 8 - 1)), (1 << (width * 8 - 1)) - 1)
    for start in range(0, len(block), width):
        value = int.from_bytes(block[start:start + width], "little", signed=signed)
        scaled = round((value - center) * gain) + center
        scaled = min(hi, max(lo, scaled))
        output[start:start + width] = int(scaled).to_bytes(width, "little", signed=signed)
    return bytes(output)


def attenuated_copy(source: Path | str, gain: float) -> Path:
    """Create a validated, safe-to-play, volume-reduced PCM WAV.

    Caller owns the returned copy. Always cleans incomplete files.
    """
    gain = validate_gain(gain)
    source = Path(source)
    audio._wav_info(source, allow_streaming_header=True)
    output = audio._new_wav(audio.AudioPlaybackError)
    success = False
    try:
        try:
            with wave.open(str(source), "rb") as original, wave.open(str(output), "wb") as written:
                if original.getcomptype() != "NONE":
                    raise audio.AudioValidationError("Compressed WAV is unsupported.", "invalid_wav")
                written.setnchannels(original.getnchannels())
                written.setsampwidth(original.getsampwidth())
                written.setframerate(original.getframerate())
                while True:
                    block = original.readframes(4096)
                    if not block:
                        break
                    written.writeframesraw(_reduce_chunk(block, original.getsampwidth(), gain))
        except (wave.Error, EOFError, ValueError):
            raise audio.AudioValidationError("Unsupported or malformed PCM WAV.", "invalid_wav") from None
        info = audio._wav_info(output)
        if info["duration_seconds"] > 300:
            raise audio.AudioValidationError("Playback exceeds five minutes.", "speech_audio_too_long")
        success = True
        return output
    except OSError:
        raise audio.AudioPlaybackError("Cannot adjust audio volume. Check local storage.", "audio_storage_failed") from None
    finally:
        if not success:
            audio._discard(output, suppress=True)


def play_wav(path: Path | str, *, device=None, gain=DEFAULT_GAIN,
             on_playback_start=None, cancel_check=None):
    """Play via explicit ALSA PCM or current default; 30% gain by default.

    'default'/'pipewire' may honor desktop volume (depends on Pi ALSA routing).
    'plughw' can bypass it, but local attenuation remains effective.
    """
    gain = validate_gain(gain)
    device_args = audio._device_args(device)
    info = audio._wav_info(Path(path), allow_streaming_header=True)
    if info["duration_seconds"] > 300:
        raise audio.AudioValidationError("Playback exceeds five minutes.", "speech_audio_too_long")
    temp = None
    try:
        temp = attenuated_copy(path, gain) if gain < 1 else None
        if cancel_check is not None:
            cancel_check()
        options = {} if on_playback_start is None else {"on_start": on_playback_start}
        if cancel_check is not None:
            options["cancel_check"] = cancel_check
        audio._run(
            ["aplay", "-q", *device_args, str(temp if temp is not None else path)],
            timeout=info["duration_seconds"] + 10,
            error_type=audio.AudioPlaybackError, **options,
        )
    finally:
        if temp is not None:
            audio._discard(temp, suppress=True)


def main(argv=None):
    cli = argparse.ArgumentParser(description="Play a local WAV at safe BMO software volume")
    cli.add_argument("wav", help="Path to a saved, valid PCM WAV")
    cli.add_argument("--gain", type=float, default=DEFAULT_GAIN,
                     help="0.01 to 1.0 (default 0.30)")
    cli.add_argument("--device", default="default",
                     help="ALSA PCM name; 'default' may follow system volume")
    args = cli.parse_args(argv)
    try:
        play_wav(args.wav, device=args.device, gain=args.gain)
    except (audio.AudioError, OSError) as exc:
        print("Playback failed: " + str(exc))
        return 1
    print("Playback finished at " + str(round(args.gain * 100)) + "% BMO software gain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
