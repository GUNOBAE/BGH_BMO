# Offline conversation and face demo

This finite demo reuses `ConversationApp` and `PygameFace` with fixed synthetic
text and fake STT, LLM, TTS and playback. No API key, SDK client, AI model,
microphone, speaker, camera, controller or motor is used. It never reads `.env`.

From `software/`, use an existing Python interpreter:

```bash
python3 -B -m bmo_ai.offline_demo --headless
```

Headless mode uses only the standard library and requires neither Pygame nor PNGs.
It automatically finishes nine scenarios: success, slow response, authentication
failure, request limit, empty STT, invalid TTS, player failure, cancellation and
timeout. Expected synthetic errors are reported as codes; they are demo outcomes.
Use `--scenario success` for just one conversation, or `--cycles 2` to repeat.
Cycles are limited to 20 and the whole demo has a 120-second safety deadline.

For a **manual desktop check**, from the same directory with an existing Pygame
installation:

```bash
PYGAME_HIDE_SUPPORT_PROMPT=1 /usr/bin/python3 -B -m bmo_ai.offline_demo --windowed
```

This command opens a real desktop window; it was not run against a physical
display during development. ESC, Q or window close exits immediately; C requests
cancellation. Ctrl-C also exits. The scripted demo finishes automatically. Without
`--windowed`, it uses the existing desktop fullscreen mode without changing the
monitor mode. The original display-only app and its `--dev` keys 1–5 still work.

Face PNGs are local assets, excluded from the public repository. Missing or
unreadable states produce an explicit terminal warning and the renderer's
existing idle/color fallback. A GitHub checkout can still run the entire demo and
mock tests without these assets. Original PNG order, 10fps animation and default
size policy are unchanged. The colored fallback is not a new face design.

`conversation.ConversationWorker` accepts a factory returning `ConversationApp`.
The factory receives a queue-only face and a cancellation event. Only the UI
thread polls events, applies states, renders and handles SDL events. One turn is
accepted at a time; repeated inputs are rejected. The event queue defaults to 32
entries, turn tags discard stale results, and completion is delivered only after
the worker has actually exited. Demo completion history retains at most 64 items.

Cancellation invalidates pending states/results immediately and checks again
before each next operation, including after a blocked state publication. Adapters
must cooperate with cancellation and bound their own calls. A Python thread cannot
interrupt arbitrary blocking IO. While an uncooperative worker is alive, no new
turn is accepted. Closing never joins on the UI thread; its single daemon worker
may finish later, with results suppressed. This is **cancellation requested**, not
proof that a dependency stopped. No new live cloud/device launcher is supplied;
the existing CLI keeps its separate behavior.

`ConversationApp` accepts an injected `voice` with `listen()` and
`speak(text, on_playback_start=callback)`. TTS generation stays THINKING; SPEAKING
starts on the player's callback and playback exits through IDLE even on failure.
The default `audio.speak()` API remains compatible and optionally emits its
callback after WAV validation and successful `aplay` process creation. That
boundary identifies playback operation start, **not measured acoustic output**.
Download limits, error classification and temporary WAV cleanup are retained.

Printed timing records use monotonic seconds and `synthetic: true`. Stages are
`prepare`, `input` (fake STT here), `llm`, `tts` and `playback` when reached.
For a general injected app, input includes its recording/STT work. Durations are
observed host elapsed times around artificial waits, not API/device latency.
No real voice improvement, sample-level lip sync, emotion inference or physical
latency result is claimed. Processing states remain separate from emotion;
an optional emotion contract is not added in this change.

Safe checks from `software/`:

```bash
timeout 90s .venv/bin/python -B checks/check_conversation_safety.py
PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. timeout 30s /usr/bin/python3 -B checks/check_conversation_offscreen.py
```

The first runs all regression tests behind audit guards for real network/device
and unexpected process access, even if an application catches the denied error.
SDK HTTP uses MockTransport and synthetic metadata. Its one allowed child is the
existing Python sleep-process timeout/reaping test. The second selects SDL dummy
before importing Pygame and also checks missing assets and quit during a blocked
response. Existing `check_face_offscreen.py` and `check_display_offscreen.py`
add five local-asset checks; they require the preserved PNGs and are separate from
the asset-free suite. Missing dependencies must be reported, not installed by
these commands. See `docs/reports/NIGHTLY_STATUS.md` for actual results.
