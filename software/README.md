# BMO conversational software

This development branch extends PR #1 with cloud text and push-to-talk voice
conversation. English is the default reply language; Korean input receives Korean
replies. The default speech recognizer detects language automatically. Hardware
adapters are separate from conversation, and arm movement is disabled in code.

The application and real SDK request contracts are tested offline on Raspberry Pi
5, Debian 13, Python 3.13.5. Live cloud conversation, microphone capture, speaker
playback, face animation and camera capture remain unverified. The current
MPU6050 has pre-existing fault evidence; do not use it for movement or orientation.

## Setup

Use a project virtual environment. `alsa-utils` and `python3-venv` must already be
available for the relevant commands; they were present on this Pi.

```bash
cd /home/bgh/BGH_BMO/software
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip check
.venv/bin/python -m bmo_ai --doctor
.venv/bin/python -m unittest discover -s tests -v
```

Use another checkout path if appropriate. `requirements.txt` pins the tested SDK;
`requirements-lock.txt` pins resolved dependencies. This Pi's isolated `.venv`
is already installed. Python 3.11+ is the intended baseline; only 3.13.5 was
executed locally. A GitHub Actions matrix covers 3.11/3.13, but hosted CI has not
been run during this task. Its action versions follow the current upstream
[setup-python example](https://github.com/actions/setup-python).

Set the key privately in an interactive Bash terminal, keeping it out of history:

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
export OPENAI_API_KEY
.venv/bin/python -m bmo_ai --text
```

The key is required only for cloud conversation. `--help`, `--doctor`,
`--list-audio` and `--benchmark` need neither a key nor the SDK. `.env.example`
documents settings; files are **not loaded automatically**. Do not commit a real
key. The client uses the official API origin explicitly. Account/model access is
not inferred from a ChatGPT subscription.

## Conversation

```bash
# Text only: no recording, synthesis or playback
.venv/bin/python -m bmo_ai --text
# Korean preference; English available when explicitly requested
.venv/bin/python -m bmo_ai --text --language ko
# Voice: Enter starts a fixed five-second recording
.venv/bin/python -m bmo_ai
# Detect the input language and match replies
.venv/bin/python -m bmo_ai --language auto --seconds 5
# Microphone and GPT without speech generation or playback
.venv/bin/python -m bmo_ai --mute
```

`/quit` and `/exit` stop the loop; `/reset` clears local conversation without an
API request. `/repeat` repeats the last completed answer without another GPT or
microphone request; unmuted voice mode re-synthesizes it and incurs TTS cost.
In voice mode only an empty Enter records; unrelated typed commands
do not start the microphone. This is half-duplex push-to-talk: it does not listen
while speaking and does not implement a wake word, barge-in or continuous Realtime
audio. Voice mode identifies synthesized speech as AI generated. Conversation
text is shown on the terminal and is excluded from event logs.

The pipeline uses the Responses API, file transcription, and WAV synthesis:
[Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create),
[transcription reference](https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create),
[speech guide](https://developers.openai.com/api/docs/guides/text-to-speech).
The existing `gpt-4.1-mini`, `gpt-4o-mini-transcribe` and `gpt-4o-mini-tts` defaults
are retained to preserve the owner's model selection. They are configurable;
actual account access and live response quality remain unverified.

## Audio device selection

```bash
.venv/bin/python -m bmo_ai --list-audio
```

Discovery lists ALSA hardware and named PCMs without recording or playback.
Omitted names retain existing ALSA defaults; discovery does not select a card or
change ALSA, PipeWire, mixer or I2S configuration. Numeric card indexes can change
after reconnects. Host inventory observed USB capture `Device` and playback
`MAX98357A`. These optional identified-device examples still require a supervised
capture/playback check:

```bash
.venv/bin/python -m bmo_ai \
  --mic-device 'plughw:CARD=Device,DEV=0' \
  --speaker-device 'plughw:CARD=MAX98357A,DEV=0'
```

Retain your working speaker selection if it differs. A `plughw` PCM can convert
the USB microphone's supported rate to requested mono 16-kHz PCM. Do not infer
GPIO wiring, reset the RP2040 or change boot overlays to fix an audio error.
See [audio deployment and recovery](../docs/audio.md).

## Configuration and recovery

CLI options override corresponding exported BMO settings. Invalid settings fail
before cloud client creation; error messages do not echo raw values.
Configuration never stores the API key.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BMO_CHAT_MODEL` | `gpt-4.1-mini` | Responses model |
| `BMO_STT_MODEL` | `gpt-4o-mini-transcribe` | File transcription model |
| `BMO_TTS_MODEL` | `gpt-4o-mini-tts` | Speech model |
| `BMO_VOICE` | `coral` | Supported built-in voice |
| `BMO_LANGUAGE` | `en` | `en`, `ko`, or `auto`; default supports Korean input |
| `BMO_SECONDS` | `5` | Recording duration, 1–20 seconds |
| `BMO_MIC_DEVICE`, `BMO_SPEAKER_DEVICE` | unset | Existing ALSA default |
| `BMO_TIMEOUT` | `30` | SDK per-request/network inactivity timeout, seconds |
| `BMO_MAX_RETRIES` | `1` | SDK retry count; `0` disables retries |
| `BMO_MAX_TURNS` | `8` | Retained complete conversation turns |
| `BMO_MAX_INPUT_CHARS` | `4000` | Reject oversized input before requests |
| `BMO_MAX_HISTORY_CHARS` | `16000` | Bound outgoing and retained message text |
| `BMO_MAX_OUTPUT_TOKENS` | `180` | Requested response token budget |
| `BMO_LOG_LEVEL` | `INFO` | Event logging severity |
| `BMO_LOG_FILE` | unset | Optional private rotating JSON event log |

Context trimming removes whole old turns. Failed, empty or incomplete responses
do not change history. No persistent memories or raw audio caches are created.
`store=False` disables Responses API response storage, without claiming to disable
all provider abuse-monitoring retention. Incomplete answers are rejected. If a
selected model repeatedly needs more output tokens, deliberately increase its
budget and assess cost; reasoning models can consume tokens internally.

The SDK alone retries connection/timeouts and retryable HTTP failures within its
configured budget. The application never automatically retries a whole turn;
timeout retries can still duplicate provider work or cost. `BMO_TIMEOUT` is not
a strict total wall-clock deadline, especially with retries or streaming. TTS
downloads have a byte cap and a cooperative 60-second chunk-boundary deadline;
a blocking network read remains governed by SDK timeouts. After a failure the
CLI returns to its prompt with a safe, actionable error. Failed playback leaves
the GPT answer on screen and in history, without resubmitting chat.

Temporary audio files are deleted on completion and catchable errors/interrupts.
Cleanup failures are reported safely and preserve the original failure. Abrupt
termination or power loss cannot guarantee cleanup. Terminal content itself can
be recorded by external terminal/session logging; application event logs exclude it.

## Logs, tests and performance

```bash
.venv/bin/python -m bmo_ai --text --log-file logs/events.jsonl
.venv/bin/python -m bmo_ai --benchmark
.venv/bin/python -m unittest discover -s tests -v
```

Logs contain fixed events, failure categories and stage durations, without keys,
transcripts, request bodies, audio or provider exception text. Files use mode
0600 and rotate at 1 MB with three backups. SDK/HTTP debug logging is contained
including externally configured `OPENAI_LOG`. Monotonic stage timings cover
recording, transcription, chat, combined synthesis/playback, and the total turn;
they do not measure time to first sound.

`--benchmark` runs 200 mock conversations with zero cloud or hardware calls.
It measures host overhead with memory tracing enabled, **not GPT latency**.
Actual microphone-to-speaker latency needs a later supervised deployment. SDK
contract tests use `httpx.MockTransport` for Responses, multipart STT, streamed
TTS, retries/timeouts, authentication errors and bilingual request text.

See [architecture](../docs/architecture.md), [hardware contracts](../docs/hardware.md),
and the [development report](../docs/reports/20261013-development-report.md) for
verified results and remaining physical checks.

## Optional Fish Audio BMO voice (Aaron)

This experimental branch keeps the OpenAI chat and transcription services but
can replace **only** speech output with Fish Audio. OpenAI remains the default.
The public BMO voice by Aaron Hinojosa uses reference ID
`106f41ea392c4cc48c6eee76f69ff17e`.
The voice page is https://fish.audio/m/106f41ea392c4cc48c6eee76f69ff17e/.

```bash
# Set OPENAI_API_KEY first as described above.
read -rsp 'Fish Audio API key: ' FISH_API_KEY; echo
export FISH_API_KEY
.venv/bin/python -m bmo_ai --tts-provider fish --fish-model s2.1-pro-free \
  --mic-device 'plughw:CARD=Device,DEV=0' \
  --speaker-device 'plughw:CARD=MAX98357A,DEV=0'
# Explicit paid choice (charges may apply):
# .venv/bin/python -m bmo_ai --tts-provider fish --fish-model drama-3
```

`BMO_TTS_PROVIDER` is `openai` or `fish`, and `BMO_FISH_MODEL` is
`s2.1-pro-free`, `s2.1-pro`, or `drama-3`. The defaults are OpenAI voice
and free Fish model if Fish is chosen. `BMO_FISH_VOICE_ID` can override Aaron.
Missing Fish credentials are rejected before cloud chat in voice mode.
`--text` and `--mute` do not require a Fish key.

Fish S2.1 Pro Free has a fair-use policy and a published free-access window
through November 30, 2026, not a lifetime guarantee. The Fish page's BMO demo
runs Drama 3; output with S2.1 Pro Free may sound different or the model may
require account-specific permission. Fish lists S2.1 Pro at $15/million UTF-8
bytes and Drama 3 at $10/million characters during October 13–November 12,
2026 ($50 regular). OpenAI chat/STT are billed separately. Only one TTS
provider is called per reply, never both and never an automatic paid fallback.
Replay still sends a fresh TTS request. API usage is not the same as website
subscription credits.

The Fish adapter requests PCM WAV, validates length/format, plays through the
unchanged ALSA path, and deletes its temporary file. No Fish credentials,
prompts or provider responses enter application event logs. **Live Fish API,
reference-ID availability and Raspberry Pi playback remain unverified.**

Docs: https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits
and https://fish.audio/blog/fish-audio-drama-3/ .
### Head-to-head Aaron audition: free S2.1 versus paid Drama 3

This standalone audition calls Fish Audio only — never GPT, STT, mic or speakers.
It saves local WAV files and an HTML page for same-text side-by-side listening.

```bash
cd /home/bgh/BGH_BMO/software
.venv/bin/python -m bmo_ai.fish_compare   # plan: no network or key
read -rsp 'Fish Audio key: ' FISH_API_KEY; echo
export FISH_API_KEY
.venv/bin/python -m bmo_ai.fish_compare --mode free --sample hello
.venv/bin/python -m bmo_ai.fish_compare --mode compare --sample hello --confirm-paid
.venv/bin/python -m bmo_ai.fish_compare --mode compare --sample all --confirm-paid
```

Open `fish_aaron_tests/bmo-aaron-*/report.html` in a browser. Saved WAV and
JSON metadata stay local and are gitignored. Paid requests require explicit
`--mode compare --confirm-paid`. The default 0.05 USD estimated paid limit
is not a provider-side billing ceiling. Failed free requests never trigger a
paid request. The free tier must be rechecked after Nov 30, 2026;
`--reviewed-free-terms` acknowledges that review.
