"""Bounded ALSA recording/playback and cloud speech without configuration changes.

The caller supplies an SDK client with its own timeout/retry policy. This module
does not import OpenAI, repeat cloud requests, select discovered cards, or log
transcripts. Successful recordings belong to the caller and must be deleted.
"""
from __future__ import annotations

import math
import os
import re
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from .cloud import cloud_error
from .telemetry import event

DEFAULT_SPEECH_INSTRUCTIONS = (
    "Speak like an upbeat, friendly, playful little robot. Clear and natural. "
    "Use the language of the supplied text, including Korean when requested."
)
MAX_RECORD_SECONDS = 60
MAX_AUDIO_BYTES = 24 * 1024 * 1024
MAX_SPEECH_CHARACTERS = 4096
DISCOVERY_TIMEOUT_SECONDS = 3
_STREAMING_SIZE = 0xFFFFFFFF
_PCM_GUID = bytes.fromhex("0100000000001000800000aa00389b71")
_VOICES = {
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
    "sage", "shimmer", "verse", "marin", "cedar",
}
_LEGACY_VOICES = {"alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"}


class AudioError(RuntimeError):
    """An actionable error whose message/code are safe to show or log."""

    def __init__(self, message: str, code: str = "audio_error") -> None:
        super().__init__(message)
        self.code = code


class AudioRecordingError(AudioError):
    pass


class AudioPlaybackError(AudioError):
    pass


class AudioTranscriptionError(AudioError):
    pass


class AudioSynthesisError(AudioError):
    pass


class AudioValidationError(AudioError):
    pass


class AudioCleanupError(AudioError):
    pass


def _device_args(device: str | None) -> list[str]:
    if device is None:
        return []
    if not isinstance(device, str) or not device.strip() or len(device) > 512 or any(
        ord(character) < 32 for character in device
    ):
        raise AudioValidationError("Configure a nonempty ALSA device name or use the default device.", "invalid_device")
    return ["-D", device]


def _stop_owned(process) -> None:
    """Terminate and reap this child only, escalating if it ignores SIGTERM."""
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=0.2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def _run(command: list[str], *, timeout: float, error_type: type[AudioError],
         on_start=None, cancel_check=None, env=None) -> None:
    """Run one owned child; cooperative cancellation also stops active playback.

    A start callback marks process creation, not measured acoustic output. The
    cancellable path polls at 20 ms and never signals unrelated processes.
    """
    try:
        options = {} if env is None else {"env": env}
        if cancel_check is not None:
            cancel_check()
            with subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, **options) as process:
                try:
                    cancel_check()
                    if on_start is not None:
                        on_start()
                    deadline = time.monotonic() + timeout
                    while True:
                        cancel_check()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, timeout)
                        try:
                            process.communicate(timeout=min(0.02, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            pass
                    cancel_check()
                except BaseException:
                    _stop_owned(process)
                    raise
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
        elif on_start is None:
            subprocess.run(command, check=True, timeout=timeout, capture_output=True, **options)
        else:
            # A successful spawn marks the playback operation's start. It does
            # not prove that a device emitted sound or measure acoustic latency.
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options) as process:
                try:
                    on_start()
                    process.communicate(timeout=timeout)
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
    except FileNotFoundError:
        raise error_type("ALSA tools are unavailable; install alsa-utils before using voice mode.", "tool_unavailable") from None
    except subprocess.TimeoutExpired:
        raise error_type("The ALSA operation timed out. Check the configured device and retry.", "audio_timeout") from None
    except subprocess.CalledProcessError:
        raise error_type(
            "ALSA could not use the configured device. Check availability and permissions; "
            "use a plughw capture device if the USB microphone requires 48 kHz.",
            "device_unavailable",
        ) from None
    except OSError:
        raise error_type("The ALSA process could not start. Check installation and device permissions.", "audio_process_failed") from None


def _new_wav(error_type: type[AudioError]) -> Path:
    try:
        fd, name = tempfile.mkstemp(prefix="bmo_audio_", suffix=".wav")
    except OSError:
        raise error_type("Cannot create a temporary audio file. Check temporary storage and permissions.", "audio_storage_failed") from None
    try:
        os.close(fd)
        return Path(name)
    except BaseException as error:
        _discard(Path(name), suppress=True)
        if not isinstance(error, OSError):
            raise
        raise error_type("Cannot prepare the temporary audio file. Check temporary storage and permissions.", "audio_storage_failed") from None


def _discard(path: Path, *, suppress: bool = False) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        event("audio_temp_cleanup_failed")
        if not suppress:
            raise AudioCleanupError(
                "Temporary audio file cleanup failed. Check temporary-directory permissions.",
                "audio_cleanup_failed",
            ) from None


def _wav_info(path: Path, *, allow_streaming_header: bool = False) -> dict[str, int | float]:
    """Check RIFF/PCM metadata against actual bytes, including truncated files.

    Downloaded TTS WAV streams may use 0xffffffff for RIFF/data length. Only
    those explicit placeholders are accepted, and duration uses actual bytes.
    """
    message = "Audio must be a complete, nonempty PCM WAV file under 24 MiB."
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_AUDIO_BYTES + 1)
    except OSError:
        raise AudioValidationError("Cannot read the audio file. Check its path and permissions.", "audio_file_unreadable") from None
    if len(raw) < 44 or len(raw) > MAX_AUDIO_BYTES or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise AudioValidationError(message, "invalid_wav")
    riff_size = struct.unpack_from("<I", raw, 4)[0]
    if riff_size != len(raw) - 8 and not (allow_streaming_header and riff_size == _STREAMING_SIZE):
        raise AudioValidationError(message, "invalid_wav")
    offset, fmt, pcm = 12, None, None
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise AudioValidationError(message, "invalid_wav")
        tag = raw[offset:offset + 4]
        size = struct.unpack_from("<I", raw, offset + 4)[0]
        start = offset + 8
        if size == _STREAMING_SIZE and allow_streaming_header and tag == b"data":
            size = len(raw) - start
        end = start + size
        if end > len(raw):
            raise AudioValidationError(message, "invalid_wav")
        if tag == b"fmt ":
            if fmt is not None or size < 16:
                raise AudioValidationError(message, "invalid_wav")
            encoding, channels, rate, byte_rate, block_align, bits = struct.unpack_from("<HHIIHH", raw, start)
            if encoding == 0xFFFE:
                if (
                    size < 40 or not 22 <= struct.unpack_from("<H", raw, start + 16)[0] <= size - 18
                    or not 1 <= struct.unpack_from("<H", raw, start + 18)[0] <= bits
                    or raw[start + 24:start + 40] != _PCM_GUID
                ):
                    raise AudioValidationError(message, "invalid_wav")
                encoding = 1
            if (
                encoding != 1 or channels not in (1, 2) or bits not in (8, 16, 24, 32)
                or not 8000 <= rate <= 192000 or block_align != channels * (bits // 8)
                or byte_rate != rate * block_align
            ):
                raise AudioValidationError(message, "invalid_wav")
            fmt = (channels, rate, bits // 8, block_align)
        elif tag == b"data":
            if fmt is None or pcm is not None:
                raise AudioValidationError(message, "invalid_wav")
            pcm = size
        offset = end + (size % 2)
        if offset > len(raw):
            raise AudioValidationError(message, "invalid_wav")
    if fmt is None or pcm is None or pcm == 0 or pcm % fmt[3]:
        raise AudioValidationError(message, "invalid_wav")
    channels, rate, width, block_align = fmt
    return {"channels": channels, "sample_rate": rate, "sample_width": width, "frames": pcm // block_align, "duration_seconds": pcm / block_align / rate}


def record_wav(seconds: int = 5, device: str | None = None, *,
               cancel_check=None, env=None) -> Path:
    """Record mono 16-kHz PCM; caller owns the returned temporary file.

    Only an explicitly configured ALSA name or the existing default is used.
    No discovered card is selected and no system audio settings are altered.
    A supplied cancellation check also stops and reaps an active owned capture
    process. Existing callers retain their original blocking/default-env path.
    """
    if type(seconds) is not int or not 1 <= seconds <= MAX_RECORD_SECONDS:
        raise AudioValidationError("Recording duration must be an integer from 1 to 60 seconds.", "invalid_duration")
    device_args = _device_args(device)
    path = _new_wav(AudioRecordingError)
    try:
        options = {}
        if cancel_check is not None:
            options["cancel_check"] = cancel_check
        if env is not None:
            options["env"] = env
        _run(
            ["arecord", "-q", *device_args, "-t", "wav", "-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), str(path)],
            timeout=seconds + 10,
            error_type=AudioRecordingError,
            **options,
        )
        info = _wav_info(path)
        if (info["channels"], info["sample_rate"], info["sample_width"]) != (1, 16000, 2):
            raise AudioValidationError("The microphone did not supply mono 16-kHz 16-bit PCM. Use a configured plughw device.", "unexpected_recording_format")
        if abs(info["duration_seconds"] - seconds) > max(0.1, seconds * 0.05):
            raise AudioValidationError("The microphone recording ended early or has an unexpected duration. Check the device and retry.", "unexpected_recording_duration")
        return path
    except BaseException:
        _discard(path, suppress=True)
        raise


def _cloud_error(error: Exception, error_type: type[AudioError]) -> AudioError:
    """Classify safely without importing the SDK or exposing exception text."""
    safe = cloud_error(error)
    return error_type(str(safe), safe.code)


def _validate_model(model: str) -> None:
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", model):
        raise AudioValidationError("Configure a valid speech model identifier.", "invalid_model")


def validate_speech_settings(model: str, voice: str) -> None:
    """Validate speech configuration before a conversation incurs cloud charges."""
    _validate_model(model)
    if (
        not isinstance(voice, str) or voice not in _VOICES
        or (model in {"tts-1", "tts-1-hd"} and voice not in _LEGACY_VOICES)
    ):
        raise AudioValidationError("Configure a supported built-in voice for the selected speech model.", "invalid_voice")


def _download_wav(response, path: Path, deadline: float) -> None:
    """Bound downloaded bytes and check elapsed time at native stream boundaries.

    Avoid SDK chunk coalescing: a large target chunk can buffer a slow stream
    across many successful socket reads without giving us a deadline check.
    Blocking network reads still use the caller's SDK timeout policy.
    """
    def check_deadline() -> None:
        if time.monotonic() >= deadline:
            raise AudioSynthesisError(
                "Speech download exceeded its time limit. Check connectivity and retry when ready.",
                "audio_download_timeout",
            )

    try:
        handle = path.open("wb")
    except OSError:
        raise AudioSynthesisError("Cannot save speech audio. Check temporary storage and permissions.", "audio_storage_failed") from None
    with handle:
        chunks = iter(response.iter_bytes())
        downloaded = 0
        while True:
            check_deadline()
            try:
                chunk = next(chunks)
            except StopIteration:
                break
            check_deadline()
            if not isinstance(chunk, bytes):
                raise AudioSynthesisError("Speech download returned invalid audio bytes.", "invalid_speech_download")
            if downloaded + len(chunk) > MAX_AUDIO_BYTES:
                raise AudioSynthesisError("Speech download exceeds the 24-MiB size limit.", "speech_download_too_large")
            try:
                handle.write(chunk)
            except OSError:
                raise AudioSynthesisError("Cannot save speech audio. Check temporary storage and permissions.", "audio_storage_failed") from None
            downloaded += len(chunk)


def transcribe(client, wav_path, model: str = "gpt-4o-mini-transcribe", language: str | None = None) -> str:
    """Transcribe a validated WAV; None allows language detection, en/ko give hints."""
    _validate_model(model)
    if language is not None and (not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language)):
        raise AudioValidationError("Use a two-letter transcription language code such as en or ko, or omit it.", "invalid_language")
    path = Path(wav_path)
    _wav_info(path)
    params = {"model": model, "response_format": "json"}
    if language is not None:
        if model == "gpt-transcribe":
            params["languages"] = [language]
        else:
            params["language"] = language
    try:
        with path.open("rb") as audio_file:
            response = client.audio.transcriptions.create(file=audio_file, **params)
        text = getattr(response, "text", None)
        if not isinstance(text, str):
            raise AudioTranscriptionError("Cloud transcription returned an invalid response. Retry when ready.", "invalid_transcription_response")
        return text.strip()
    except AudioError:
        raise
    except Exception as error:
        raise _cloud_error(error, AudioTranscriptionError) from None


def speak(
    client, text: str, voice: str = "coral", device: str | None = None,
    model: str = "gpt-4o-mini-tts", instructions: str | None = DEFAULT_SPEECH_INSTRUCTIONS,
    download_timeout: float = 60.0,
    *, on_playback_start=None, cancel_check=None,
) -> None:
    """Download validated WAV speech and play it with bounded ALSA execution.

    Text is never truncated or logged. Instructions apply to GPT TTS models;
    legacy tts-1/tts-1-hd requests omit unsupported instructions.
    Optional on_playback_start runs only after validated synthesis and successful
    player process creation; it is an operation boundary, not acoustic proof.
    """
    if not isinstance(text, str):
        raise AudioValidationError("Speech input must be text.", "invalid_speech_text")
    if not text.strip():
        return
    if len(text) > MAX_SPEECH_CHARACTERS:
        raise AudioValidationError("Speech input exceeds 4096 characters. Shorten the reply before synthesis.", "speech_text_too_long")
    validate_speech_settings(model, voice)
    if instructions is not None and (not isinstance(instructions, str) or len(instructions) > 4096):
        raise AudioValidationError("Speech instructions must be text of at most 4096 characters.", "invalid_speech_instructions")
    if (
        isinstance(download_timeout, bool) or not isinstance(download_timeout, (int, float))
        or not math.isfinite(download_timeout) or not 0 < download_timeout <= 300
    ):
        raise AudioValidationError("Speech download timeout must be a finite number from above zero to 300 seconds.", "invalid_download_timeout")
    device_args = _device_args(device)
    if cancel_check is not None:
        cancel_check()
    path = _new_wav(AudioSynthesisError)
    succeeded = False
    try:
        params = {"model": model, "voice": voice, "input": text, "response_format": "wav"}
        if instructions and model not in {"tts-1", "tts-1-hd"}:
            params["instructions"] = instructions
        deadline = time.monotonic() + download_timeout
        try:
            with client.audio.speech.with_streaming_response.create(**params) as response:
                _download_wav(response, path, deadline)
        except AudioError:
            raise
        except Exception as error:
            raise _cloud_error(error, AudioSynthesisError) from None
        info = _wav_info(path, allow_streaming_header=True)
        if info["duration_seconds"] > 300:
            raise AudioValidationError("Synthesized speech exceeds the five-minute playback limit.", "speech_audio_too_long")
        if cancel_check is not None:
            cancel_check()
        playback_options = {} if on_playback_start is None else {"on_start": on_playback_start}
        if cancel_check is not None:
            playback_options["cancel_check"] = cancel_check
        _run(["aplay", "-q", *device_args, str(path)], timeout=info["duration_seconds"] + 10,
             error_type=AudioPlaybackError, **playback_options)
        succeeded = True
    finally:
        _discard(path, suppress=not succeeded)


def _hardware_devices(output: str) -> list[dict[str, str | int]]:
    devices = []
    for line in output.splitlines():
        match = re.match(r"^card\s+(\d+):\s+(\S+)\s+\[([^\]]+)\],\s+device\s+(\d+):\s+(.+)$", line)
        if match:
            card, card_id, card_name, device, device_name = match.groups()
            devices.append({"card": int(card), "card_id": card_id, "card_name": card_name, "device": int(device), "device_name": device_name, "alsa_name": f"hw:CARD={card_id},DEV={device}"})
    return devices


def _pcm_devices(output: str) -> list[dict[str, str]]:
    devices = []
    for line in output.splitlines():
        if line and not line[0].isspace():
            devices.append({"name": line.strip(), "description": ""})
        elif line.strip() and devices:
            devices[-1]["description"] = (devices[-1]["description"] + " " + line.strip()).strip()[:512]
    return devices


def discover_devices() -> dict:
    """Read-only JSON-serializable ALSA inventory, including missing-tool errors.

    Listing PCMs does not verify recording/playback, supported rates, wiring,
    speaker function, or permission to open a device.
    """
    inventory = {}
    for direction, tool in (("capture", "arecord"), ("playback", "aplay")):
        result = {"hardware": [], "pcm": [], "errors": []}
        for flag, key, parser in (("-l", "hardware", _hardware_devices), ("-L", "pcm", _pcm_devices)):
            try:
                response = subprocess.run(
                    [tool, flag], check=True, timeout=DISCOVERY_TIMEOUT_SECONDS,
                    capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"},
                )
                result[key] = parser(response.stdout[:65536])
            except FileNotFoundError:
                result["errors"].append({"query": flag, "code": "tool_unavailable", "message": "Install alsa-utils to list ALSA devices."})
            except subprocess.TimeoutExpired:
                result["errors"].append({"query": flag, "code": "audio_timeout", "message": "ALSA device listing timed out."})
            except (subprocess.CalledProcessError, OSError):
                result["errors"].append({"query": flag, "code": "discovery_failed", "message": "ALSA devices could not be listed. Check device access."})
        inventory[direction] = result
    return inventory
