"""Simple push-to-talk audio using ALSA. Does not modify audio device configuration."""
import os
import subprocess
import tempfile
from pathlib import Path

def _run(command):
    subprocess.run(command, check=True, timeout=35)

def record_wav(seconds=5, device=None):
    """Record mono 16-kHz PCM for transcription; returns a temporary Path."""
    fd, name = tempfile.mkstemp(prefix="bmo_mic_", suffix=".wav")
    os.close(fd)
    path = Path(name)
    command = ["arecord", "-q"]
    if device:
        command += ["-D", device]
    command += ["-f", "S16_LE", "-r", "16000", "-c", "1", "-d", str(seconds), str(path)]
    try:
        _run(command)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise

def transcribe(client, wav_path, model="gpt-4o-mini-transcribe"):
    with open(wav_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(model=model, file=audio_file)
    return response.text.strip()

def speak(client, text, voice="coral", device=None):
    """Generate a WAV and play through ALSA (e.g. an I2S MAX98357A device)."""
    if not text.strip():
        return
    fd, name = tempfile.mkstemp(prefix="bmo_tts_", suffix=".wav")
    os.close(fd)
    path = Path(name)
    try:
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=text[:4000],
            response_format="wav",
            instructions="Speak like an upbeat, friendly, playful little robot. Clear and natural.",
        ) as response:
            response.stream_to_file(path)
        command = ["aplay", "-q"]
        if device:
            command += ["-D", device]
        command.append(str(path))
        _run(command)
    finally:
        path.unlink(missing_ok=True)
