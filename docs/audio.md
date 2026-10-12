# Audio deployment and recovery

`software/bmo_ai/audio.py` records through ALSA, sends WAV audio to cloud
transcription, downloads synthesized WAV speech, and plays it through ALSA.
The module imports only Python standard-library modules and the local cloud error
classifier; reading the device inventory does not require OpenAI or an API key.

The application uses English by default. `--language ko` selects Korean replies
and supplies `ko` as the transcription language hint; `--language auto` leaves the
hint unset. Transcription preserves spoken language. Synthesized Korean uses
Korean input text; the available built-in voices are optimized for English, so
actual pronunciation must be evaluated with the owner present. These behaviors
follow the [official transcription reference](https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create)
and [speech language documentation](https://developers.openai.com/api/docs/guides/text-to-speech).

## Discover devices without using them

Run from the repository root:

```bash
PYTHONPATH=software python3 -m bmo_ai --list-audio
```

Discovery issues only `arecord -l`, `arecord -L`, `aplay -l`, and `aplay -L`, each
with a three-second timeout. The JSON groups capture and playback hardware and
PCM names, and reports sanitized errors if tools or device access are absent.
A listed device is not proof of supported sample rates, microphone capture,
speaker output, or correct wiring. It does not open an audio stream.

Set `BMO_MIC_DEVICE` and `BMO_SPEAKER_DEVICE`, or the matching command-line
options, only to ALSA names you have identified. If a name is omitted, the
existing ALSA default is used. Discovery never chooses an arbitrary card.
Named card identifiers are generally easier to maintain than numeric card
indexes, which can change after reconnecting USB devices.

A USB microphone can require 48-kHz hardware capture even though the application
requests mono 16-kHz signed 16-bit PCM. After identifying the actual microphone
card, use its `plughw:CARD=<identified-card>,DEV=<identified-device>` PCM name so
ALSA can convert rate/channel/format. Do not copy a guessed card number. ALSA's
[PCM plugin documentation](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html)
explains that `hw` performs raw communication while the `plug` plugin converts
channels, sample rate, and format. This application does not write `/etc/asound.conf`,
`~/.asoundrc`, mixer settings, boot overlays, or speaker configuration.

If the configured speaker already works, retain that device name and its current
OS configuration. Confirm microphone and playback behavior manually with the
owner present before declaring the USB microphone or MAX98357A functional.

## API and file lifecycle

- `record_wav(seconds=5, device=None)` returns a temporary `Path` containing
  validated mono 16-kHz 16-bit PCM. The caller owns and must delete successful
  recordings in a `finally` block. The module accepts integer durations 1–60
  seconds; the application configuration imposes the stricter 1–20-second limit.
- `transcribe(client, wav_path, model="gpt-4o-mini-transcribe", language=None)`
  validates the WAV before upload, requests a JSON transcript, and closes the
  file even on errors. It leaves the caller-owned recording in place.
  When the model is exactly `gpt-transcribe`, a provided language hint is sent
  as its supported `languages=[code]` parameter; other models use `language=code`.
- `speak(client, text, voice="coral", device=None, model="gpt-4o-mini-tts",
  instructions=DEFAULT_SPEECH_INSTRUCTIONS, download_timeout=60.0)` downloads
  and validates a WAV before starting `aplay`. It deletes synthesized temporary audio on success, errors,
  and keyboard interrupts. Blank text causes no request or playback. Input over
  4096 characters fails visibly rather than losing part of the reply.
- Speech instructions are optional and omitted for `tts-1` and `tts-1-hd`, which
  do not support them. The model, built-in voice, and instructions can be supplied
  by embedding applications. See the [official speech reference](https://developers.openai.com/api/reference/python/resources/audio/subresources/speech/methods/create).

RIFF/container lengths, actual PCM data bytes, encoding, channel count, sample
rate, byte rate, block alignment, and nonempty audio are checked before upload or
playback. Recordings also require the expected duration. TTS downloads may use
explicit `0xffffffff` streaming length placeholders; playback duration is then
calculated from actual PCM bytes. Arbitrary truncated files are rejected. Files
over 24 MiB and synthesized speech longer than five minutes are rejected.
The download byte limit is enforced before each disk write, so a malformed or
oversized response cannot grow the temporary audio file past that limit.

## Recovering from errors

ALSA capture times out after the requested duration plus ten seconds. Playback
times out after actual WAV duration plus ten seconds. `subprocess.run` kills and
waits for timed-out child processes. Failed or interrupted capture removes its
temporary file. Missing tools, blocked devices, timeouts, malformed WAVs, failed
cloud transcription/synthesis, and cleanup failures have typed `AudioError`
subclasses with safe messages and codes. Raw API exception bodies, subprocess
stderr, keys, and transcripts are never placed in those messages or logs.

The SDK client owns cloud timeouts and retry policy. Audio operations do not add
another retry loop or repeat a complete conversation turn. A failed playback
therefore does not duplicate the user's cloud conversation. After a device or
cloud error, correct the indicated problem and begin another turn. If temporary
file removal itself fails, the original error or interrupt is preserved and an
`audio_temp_cleanup_failed` event is logged; check temporary-directory
permissions. A cleanup failure after otherwise successful playback is surfaced
as `AudioCleanupError`.

Speech downloads also have a configurable monotonic deadline, defaulting to
60 seconds, checked before and after every native SDK response chunk. The
implementation avoids coalescing small network chunks into larger chunks that
could hide slow progress. This is a cooperative deadline: a blocking network
read is governed by the SDK's timeout policy and can extend past the deadline
before the next check. It is not a strict cancellation timer. Size and deadline
errors close the stream, delete partial audio, and prevent playback.

## Offline verification

```bash
PYTHONPATH=software python3 -m unittest discover -s software/tests -p test_audio.py -v
```

Tests use synthetic PCM WAVs, fake speech-stream context managers, and mocked
ALSA commands. They cover language/model/voice/instruction requests, malformed
and truncated WAVs, format/rate/duration checks, file ownership, cleanup on
failure and interruption, safe error classification, no extra cloud retries,
read-only device discovery, bounded downloads, and recovery when cleanup fails. One test uses a
short-lived Python subprocess to verify timeout child cleanup; it uses no audio
hardware. These tests do not validate physical audio, cloud account/model access,
real Korean pronunciation, or live voice latency.
