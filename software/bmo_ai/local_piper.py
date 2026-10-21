"""Offline BMO voice backed by the user's independent Piper test installation.

This optional adapter does NOT install packages, touch ~/bmo-voice-test,
modify ALSA settings, or call any cloud service. It runs the already tested
Piper binary as a subprocess and plays a temporary validated WAV at reduced
gain using the existing BMO player. The original .onnx/.json are read-only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from . import audio
from .pronunciation import spoken_bmo_name
from .safe_playback import play_wav, validate_gain

DEFAULT_BINARY = "~/bmo-voice-test/piper/piper"
DEFAULT_MODEL = "~/bmo-voice-test/bmo-piper/en_US-bmo_voice.onnx"
MAX_LOCAL_CHARS = 2048
MAX_LOCAL_SECONDS = 120


def is_english_text(text: str) -> bool:
    """Avoid sending unsupported Korean/non-Latin letters into English Piper."""
    return isinstance(text, str) and all(
        not char.isalpha() or ord(char) < 128 for char in text
    )


def resolved_paths(binary: str = DEFAULT_BINARY, model: str = DEFAULT_MODEL):
    """Expand documented local paths without probing or modifying user files."""
    if not isinstance(binary, str) or not binary.strip() or not isinstance(model, str) or not model.strip():
        raise audio.AudioValidationError("Piper binary/model paths must be nonempty.", "invalid_local_path")
    if any(ord(ch) < 32 for ch in binary + model):
        raise audio.AudioValidationError("Piper binary/model paths contain control characters.", "invalid_local_path")
    return Path(binary).expanduser().resolve(), Path(model).expanduser().resolve()


def check_local_voice(*, binary: str = DEFAULT_BINARY, model: str = DEFAULT_MODEL):
    """Read-only diagnosis; does not synthesize, play or make network calls."""
    exe, weights = resolved_paths(binary, model)
    config = Path(str(weights) + ".json")
    return {
        "binary_exists": exe.is_file(),
        "binary_executable": exe.is_file() and os.access(exe, os.X_OK),
        "model_exists": weights.is_file(),
        "config_exists": config.is_file(),
    }


def synthesize_wav(text: str, *, binary: str = DEFAULT_BINARY, model: str = DEFAULT_MODEL,
                   name_mode: str = "beemo", timeout: float = MAX_LOCAL_SECONDS):
    """Return a validated temporary PCM WAV created entirely offline.

    Caller owns and removes the returned WAV. A failed process, timeout or bad
    WAV is cleaned up and never retried or sent to another service.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_LOCAL_CHARS:
        raise audio.AudioValidationError("Local Piper speech input is empty or too long.", "invalid_local_text")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_LOCAL_SECONDS:
        raise audio.AudioValidationError("Invalid local Piper timeout.", "invalid_local_timeout")
    try:
        script = spoken_bmo_name(text, name_mode)
    except ValueError:
        raise audio.AudioValidationError("Invalid local Piper BMO name mode.", "invalid_name_mode") from None
    exe, weights = resolved_paths(binary, model)
    ready = check_local_voice(binary=binary, model=model)
    if not all(ready.values()):
        raise audio.AudioSynthesisError(
            "Offline Piper binary, executable permission, ONNX or model config is missing.",
            "local_piper_unavailable",
        )
    # Use the parent of the bundled piper/ directory: the user's standalone
    # reference tests invoked ./piper/piper from ~/bmo-voice-test.
    home_dir = exe.parent.parent
    wav_path = audio._new_wav(audio.AudioSynthesisError)
    success = False
    try:
        # Never pass Fish/OpenAI credentials to an external local executable.
        env = {
            k: v for k, v in os.environ.items()
            if k not in {"OPENAI_API_KEY", "FISH_API_KEY"}
        }
        try:
            subprocess.run(
                [str(exe), "--model", str(weights), "--output_file", str(wav_path)],
                input=script + "\n",
                text=True,
                check=True,
                capture_output=True,
                timeout=timeout,
                cwd=str(home_dir),
                env=env,
            )
        except FileNotFoundError:
            raise audio.AudioSynthesisError("Offline Piper executable is unavailable.", "local_piper_unavailable") from None
        except subprocess.TimeoutExpired:
            raise audio.AudioSynthesisError("Offline Piper synthesis timed out.", "local_piper_timeout") from None
        except subprocess.CalledProcessError:
            raise audio.AudioSynthesisError("Offline Piper could not synthesize this text.", "local_piper_failed") from None
        except OSError:
            raise audio.AudioSynthesisError("Offline Piper process could not start.", "local_piper_unavailable") from None
        info = audio._wav_info(wav_path)
        if info["duration_seconds"] > 300:
            raise audio.AudioValidationError("Piper WAV exceeds five minutes.", "speech_audio_too_long")
        success = True
        return wav_path
    finally:
        if not success:
            audio._discard(wav_path, suppress=True)


def speak(text: str, *, binary: str = DEFAULT_BINARY, model: str = DEFAULT_MODEL,
          name_mode: str = "beemo", device=None, gain: float = .25,
          on_playback_start=None, cancel_check=None):
    """Produce and play one offline utterance with the selected BMO gain."""
    validate_gain(gain)
    audio._device_args(device)
    if cancel_check is not None:
        cancel_check()
    wav_path = synthesize_wav(text, binary=binary, model=model, name_mode=name_mode)
    try:
        play_wav(wav_path, device=device, gain=gain,
                 on_playback_start=on_playback_start, cancel_check=cancel_check)
    finally:
        audio._discard(wav_path, suppress=True)


def main(argv=None):
    cmd = argparse.ArgumentParser(description="Check or safely play offline BMO Piper")
    cmd.add_argument("--check", action="store_true", help="Read-only test: no synthesis or audio")
    cmd.add_argument("--say", help="Play one short text entirely locally")
    cmd.add_argument("--binary", default=DEFAULT_BINARY)
    cmd.add_argument("--model", default=DEFAULT_MODEL)
    cmd.add_argument("--device", default="plughw:CARD=MAX98357A,DEV=0")
    cmd.add_argument("--gain", type=float, default=.25)
    opts = cmd.parse_args(argv)
    if not opts.say and not opts.check:
        cmd.error("Choose --check or --say")
    try:
        condition = check_local_voice(binary=opts.binary, model=opts.model)
        for item, good in condition.items():
            print(item + ": " + ("OK" if good else "MISSING"))
        if not all(condition.values()):
            return 2
        if opts.say and not opts.check:
            speak(opts.say, binary=opts.binary, model=opts.model,
                  device=opts.device, gain=opts.gain)
            print("Offline BMO Piper played successfully.")
        return 0
    except audio.AudioError as failure:
        print("Offline Piper failed: " + str(failure))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
