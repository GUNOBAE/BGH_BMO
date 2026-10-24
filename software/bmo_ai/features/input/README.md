# Injected input and PCM boundaries

This package admits finite synthetic input traces and caller-supplied PCM. It
does not import a device-reader library, open a device, discover mappings, start
STT, run a wake model, or start a conversation by itself. All methods run on the
host owner thread. Real controller, microphone and wake-word acceptance remains
`NOT_RUN`; synthetic verification does not change that status.

## Logical input

```python
mapping = VerifiedMapping("synthetic", {"button_a": "A", "halt": "STOP"},
                          verified=True, provenance="synthetic_fixture")
source = SyntheticEventSource(events, clock=clock)
adapter = InputAdapter(existing_router, source, mapping=mapping, clock=clock)
dispatches = adapter.poll()
```

`RawInputEvent(source, control, pressed, sequence, timestamp)` requires a known
source name, a bounded ASCII control ID, an exact boolean edge, a nonnegative
integer sequence and a finite monotonic timestamp. Source names describe the
injected adapter contract; they do not instantiate USB/HID/CDC readers.
`SyntheticEventSource` accepts at most 4,096 ordered events and polls at most
64 due events without sleeping. A future device source must provide bounded
`poll(limit)` and `close()` methods; no real implementation ships here.

Mappings require an explicit verification record and are copied. Missing
verification disables admission before source polling. There is no production
default mapping, inferred HID button number or firmware pin mapping. Literal
RP2040 GP7/GPIO7/PIN_GP7 bindings fail with `gp7_conflict`: the existing arm
toggle conflict is unresolved, and this package does not program firmware.

The adapter rejects wrong sources, stale/future/backward timestamps, duplicate
sequence numbers, unmapped controls and repeated presses. A fresh release is
delivered immediately; it is never delayed by debounce. Presses within the
default 30 ms after release are dropped, rather than scheduled for later.
TALK/MENU releases only update edge state. STOP is never delayed by the press
debounce: its first edge takes priority over TALK and mode input from the same
batch, clears held controls and suppresses the remaining batch. A repeated
held STOP is suppressed until release. A mode-generation change during a batch
rejects its remaining presses. Admission counts and public dispatches describe
delivery to the router, not successful physical execution.

`InputRouter` still owns STOP/TALK and mode dispatch. `GamesFeature` still owns
the continuous Start+Select 1.2-second exit hold and its exit debounce. B stays
with the running game. Source failure/disconnect releases held controls and
routes PAD_LOST once; host/game code owns cleanup and mode return. A failed
source is retired and must be explicitly replaced, including after a hardware
sequence counter reset. `close()` is idempotent and releases held controls.

## Explicit PTT and pure PCM VAD

```python
gate = PcmTurnGate(audio_focus, enabled=True, clock=clock,
                   playback_active=lambda: conversation_busy)
if gate.start_ptt("capture:1", generation=1):
    gate.feed(pcm16le_mono, sequence=0, timestamp=clock(), generation=1)
    gate.release_ptt(generation=1)
for result in gate.poll():
    if result.status == "ready":
        # An explicitly injected host consumer may use result.pcm once.
        pass
```

The default is disabled. Enabling it only admits injected bytes; it cannot open
a microphone. Capture requires a fresh explicit PTT ID and strictly increasing
capture generation. It accepts signed PCM16 little-endian mono at 8, 16 or
48 kHz, with at most 100 ms per frame. Sequence, timestamp and generation are
checked before buffer mutation. There is no automatic capture/restart.

The VAD uses RMS with consecutive minimum speech, followed by a silence timeout.
Default synthetic settings are threshold 0.02, minimum speech 80 ms, trailing
silence 300 ms, no-speech timeout 3 s and maximum duration 15 s. These values
are development fixtures, not calibrated microphone/noise measurements. Silence
alone or short interrupted blips produce no ready audio. Release completes an
already detected utterance; duration/byte limits bound continuous speech. At
16 kHz the default PCM budget is 480,000 bytes. There is one pending result,
and at most 4,096 accepted capture IDs per gate; capacity fails closed rather
than forgetting old IDs. Start a new host/session to replace a retired gate.

Any `AudioFocus.owner`, including BMO conversation playback, blocks capture.
Output starting during capture or before a ready result is consumed invalidates
the candidate and discards its PCM. An optional playback probe covers a busy
voice worker without an output lease; probe errors also block input. The gate
never acquires/releases/stops output ownership. Input must begin before output
is leased. This is conservative self-voice prevention, not acoustic echo
cancellation. Hardware echo cancellation remains `NOT_RUN`.

`PcmCapture.pcm` is an internal memory result; its repr/public view excludes the
samples. Cancel/close clear active and pending buffers. No PCM/transcript file,
environment variable or log is written. This is lifetime cleanup, not a secure
memory-erasure guarantee. A consumer must not retain audio without a separate
policy.

The simulation-only `FeatureHost` injection accepts `input_source`,
`input_mapping` and `pcm_transcriber`. `host.start_ptt(id)` supplies a capture
generation visible in `host.pcm.snapshot()`. A ready capture goes to that
injected transcriber once and then the existing `start_question`/persistent
brain. STOP/cancel, mode changes, newer captures and close invalidate late
results. Live runtime rejects these synthetic hooks with `input_disabled`;
they do not activate a production recorder or STT route.

## Wake word

`WakeWordGate` is always disabled. No model yields `wake_model_missing`; passing
an unverified model yields `wake_unverified`, without retaining or calling it.
`feed()` always returns false and `real_wake_word` remains `NOT_RUN`. There is
no pretend Hey BMO detection, background microphone or activation loop.

## Verification

Run the dedicated standard-library tests with an empty credential environment:

```sh
env -i PATH=/usr/bin:/bin HOME=/tmp LC_ALL=C.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=software:software/tests \
  .venv/bin/python -B -m unittest test_input_adapter test_pcm_input test_input_host
```

Tests use finite traces, fake clocks, in-memory PCM and simulated host backends.
They cover STOP precedence, debounce/release, existing game hold/B routing,
timeouts/budgets, stale generations, output focus, disabled wake, single brain
consumption and late-result cancellation. They do not verify physical mappings,
firmware GP7, real VAD accuracy, a real controller/microphone or live cloud IO.
