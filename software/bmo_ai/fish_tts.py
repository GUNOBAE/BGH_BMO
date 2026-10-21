"""Optional Fish Audio WAV output for BMO; no extra dependencies or implicit fallback.

Only Fish gets the spoken reply. GPT chat and OpenAI transcription remain in
existing modules. FISH_API_KEY is read at call time; no secrets enter config or logs.
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import audio

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
FISH_MODELS = frozenset(("s2.1-pro-free", "s2.1-pro", "drama-3"))
AARON_BMO_VOICE_ID = "106f41ea392c4cc48c6eee76f69ff17e"



# The Fish WAV transport may use placeholder sizes instead of actual bytes.
FISH_RIFF_PLACEHOLDER = 4294967076
FISH_DATA_PLACEHOLDER = 4294967040


def _normalize_fish_wav(path):
    """Patch Fish's documented placeholder chunk sizes, then validate strictly."""
    length = path.stat().st_size
    if not 44 <= length <= audio.MAX_AUDIO_BYTES:
        return
    with path.open("r+b") as handle:
        header = handle.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return
        if struct.unpack_from("<I", header, 4)[0] != FISH_RIFF_PLACEHOLDER:
            return
        pos = 12
        while pos + 8 <= length:
            handle.seek(pos)
            tag, count = struct.unpack("<4sI", handle.read(8))
            start = pos + 8
            if tag == b"data":
                if count != FISH_DATA_PLACEHOLDER or start >= length:
                    raise audio.AudioValidationError("Fish WAV has an invalid data header.", "invalid_wav")
                handle.seek(4)
                handle.write(struct.pack("<I", length - 8))
                handle.seek(pos + 4)
                handle.write(struct.pack("<I", length - start))
                return
            pos = start + count + (count % 2)
    raise audio.AudioValidationError("Fish WAV has an invalid streaming header.", "invalid_wav")


def _error_for_http_status(status: int) -> audio.AudioSynthesisError:
    """Only fixed safe error messages. Never display provider payloads or headers."""
    messages = {
        400: ("fish_request", "Fish Audio rejected the synthesis request; check the model and voice ID."),
        401: ("fish_authentication", "Fish Audio authentication failed; check FISH_API_KEY."),
        402: ("fish_balance", "Fish Audio has insufficient balance or credits. Check billing before retrying."),
        403: ("fish_permission", "Fish Audio denied access to this voice or model."),
        404: ("fish_model", "Fish Audio could not find the selected voice or model."),
        422: ("fish_request", "Fish Audio rejected the voice or text settings."),
        429: ("fish_rate_limit", "Fish Audio rate or usage limit reached; do not retry immediately."),
    }
    code, message = messages.get(status, (
        "fish_server" if status >= 500 else "fish_request",
        "Fish Audio is temporarily unavailable." if status >= 500 else "Fish Audio rejected the request.",
    ))
    return audio.AudioSynthesisError(message, code)


def synthesize_wav(
    text: str,
    *,
    model: str = "s2.1-pro-free",
    reference_id: str = AARON_BMO_VOICE_ID,
    timeout: float = 30.0,
    download_timeout: float = 60.0,
    cancel_check=None,
):
    """Call Fish once and return a validated temporary WAV path.

    The caller owns the returned file and MUST delete it. HTTP requests never
    retry or switch providers. The explicit model header prevents accidental
    use of Fish's paid default when requesting the free model.
    """
    if not isinstance(text, str):
        raise audio.AudioValidationError("Speech input must be text.", "invalid_speech_text")
    if not text.strip():
        raise audio.AudioValidationError("Speech input must not be empty.", "invalid_speech_text")
    if len(text) > audio.MAX_SPEECH_CHARACTERS:
        raise audio.AudioValidationError("Speech input exceeds 4096 characters.", "speech_text_too_long")
    if model not in FISH_MODELS:
        raise audio.AudioValidationError("BMO_FISH_MODEL must select a supported Fish model.", "invalid_fish_model")
    if not isinstance(reference_id, str) or not re.fullmatch(r"[a-f0-9]{32}", reference_id):
        raise audio.AudioValidationError("BMO_FISH_VOICE_ID must be a 32-character lowercase hex ID.", "invalid_fish_voice")
    for duration in (timeout, download_timeout):
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 300:
            raise audio.AudioValidationError("Fish Audio timeouts must be finite values between 0 and 300 seconds.", "invalid_fish_timeout")
    key = os.environ.get("FISH_API_KEY", "").strip()
    if not key or key == "YOUR_KEY_HERE" or key.startswith("YOUR_FISH"):
        raise audio.AudioSynthesisError("Set FISH_API_KEY before selecting Fish Audio.", "missing_fish_key")
    if cancel_check is not None:
        cancel_check()
    request = Request(
        FISH_TTS_URL,
        data=json.dumps({"text": text, "reference_id": reference_id, "format": "wav"}, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "model": model},
        method="POST",
    )
    path = audio._new_wav(audio.AudioSynthesisError)
    succeeded = False
    try:
        deadline = time.monotonic() + download_timeout
        try:
            with urlopen(request, timeout=timeout) as response:
                try:
                    out = path.open("wb")
                except OSError:
                    raise audio.AudioSynthesisError("Cannot save synthesized audio. Check storage.", "audio_storage_failed") from None
                with out:
                    count = 0
                    while True:
                        if time.monotonic() >= deadline:
                            raise audio.AudioSynthesisError("Fish speech download timed out.", "audio_download_timeout")
                        chunk = response.read(65536)
                        if time.monotonic() >= deadline:
                            raise audio.AudioSynthesisError("Fish speech download timed out.", "audio_download_timeout")
                        if not chunk:
                            break
                        if not isinstance(chunk, bytes) or count + len(chunk) > audio.MAX_AUDIO_BYTES:
                            raise audio.AudioSynthesisError("Fish speech download is invalid or too large.", "speech_download_too_large")
                        try:
                            out.write(chunk)
                        except OSError:
                            raise audio.AudioSynthesisError("Cannot save synthesized audio. Check storage.", "audio_storage_failed") from None
                        count += len(chunk)
        except HTTPError as error:
            raise _error_for_http_status(error.code) from None
        except URLError:
            raise audio.AudioSynthesisError("Cannot connect to Fish Audio. Check network.", "fish_connection") from None
        except TimeoutError:
            raise audio.AudioSynthesisError("Fish Audio timed out. Check network.", "fish_timeout") from None
        except OSError:
            raise audio.AudioSynthesisError("Fish Audio connection failed. Check network.", "fish_connection") from None
        _normalize_fish_wav(path)
        info = audio._wav_info(path, allow_streaming_header=True)
        if info["duration_seconds"] > 300:
            raise audio.AudioValidationError("Synthesized speech exceeds five minutes.", "speech_audio_too_long")
        if cancel_check is not None:
            cancel_check()
        succeeded = True
        return path
    finally:
        if not succeeded:
            audio._discard(path, suppress=True)


def speak(
    text: str,
    *,
    device: str | None = None,
    model: str = "s2.1-pro-free",
    reference_id: str = AARON_BMO_VOICE_ID,
    timeout: float = 30.0,
    download_timeout: float = 60.0,
    on_playback_start=None,
    cancel_check=None,
) -> None:
    """Synthesize via Fish, validate WAV and play once through existing ALSA."""
    if not isinstance(text, str):
        raise audio.AudioValidationError("Speech input must be text.", "invalid_speech_text")
    if not text.strip():
        return
    device_args = audio._device_args(device)
    path = synthesize_wav(
        text, model=model, reference_id=reference_id,
        timeout=timeout, download_timeout=download_timeout,
        cancel_check=cancel_check,
    )
    succeeded = False
    try:
        info = audio._wav_info(path, allow_streaming_header=True)
        if cancel_check is not None:
            cancel_check()
        playback_options = {} if on_playback_start is None else {"on_start": on_playback_start}
        audio._run(
            ["aplay", "-q", *device_args, str(path)],
            timeout=info["duration_seconds"] + 10,
            error_type=audio.AudioPlaybackError, **playback_options,
        )
        succeeded = True
    finally:
        audio._discard(path, suppress=not succeeded)
