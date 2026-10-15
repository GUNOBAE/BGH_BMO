# Offline conversation integration status

Status: SOFTWARE VERIFIED — first implementation checkpoint (2026-10-16 KST).

- Start commit: `a51d6063288a033a42b108632a421c9c65581d8b`.
- Inspected code: working changes based on that commit; final report will name
  the committed implementation checkpoint rather than predict its own hash.
- Target branch: `feature/bmo-robust-conversation`; local and remote matched at start.
- Initial tracked/untracked changes: none. Ignored operating documents and local
  assets remain in place; ignored does not imply deleted or uploaded.
- No other development process or additional worktree was observed at start.
- Preserved assets: all 13 local face PNGs match the existing provenance hashes;
  assets and local management documents are excluded from this upload.

Commands executed from `software/` (existing interpreters; no installation):

| Command | Scope | Actual result |
| --- | --- | --- |
| `timeout 90s .venv/bin/python -B -m unittest discover -s tests` | Mock regression, including SDK mock HTTP | 130 passed, 0 failed, 0 skipped |
| `PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. timeout 30s /usr/bin/python3 -B checks/check_face_offscreen.py` | In-memory Pygame; local asset preservation | 3 passed, 0 failed, 0 skipped |
| `PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. timeout 30s /usr/bin/python3 -B checks/check_display_offscreen.py` | SDL dummy display | 2 passed, 0 failed, 0 skipped |

Implemented in `software/bmo_ai/`:

- `conversation.py`: one bounded daemon worker, queue-only state adapter, tagged
  turns, duplicate input rejection, stale-output filtering, timeout/cancel requests
  and actual worker completion as separate events. Closing never joins the UI.
- `offline_demo.py`: finite, fixed synthetic STT/LLM/TTS/player scenarios with
  success, slow response, authentication/request-limit failures, empty STT,
  invalid TTS, player failure, cancellation and timeout; recovery is tested.
- `app.py` and `audio.py`: injected voice and cooperative cancellation checks;
  THINKING during synthesis, SPEAKING on playback-start callback, IDLE cleanup.
  Default playback notification follows validated WAV and successful player
  process creation. It does not prove acoustic output.
- `display_app.py`: optional session hook reusing the existing UI loop and
  `PygameFace`; all SDL/event/render/state application remains on the UI thread.
  Existing ESC/Q and dev keys, frame order, 10fps and default size policy remain.
  `face.py` exposes missing states for an explicit fallback warning.
- Public commands and cancellation limitations: `software/CONVERSATION_DEMO.md`.

Final implementation checks executed from `software/`:

| Command | Scope | Actual result |
| --- | --- | --- |
| `timeout 90s .venv/bin/python -B checks/check_conversation_safety.py` | All 130 baseline + 39 new regressions; audit guards, fake HTTP/audio, gates/fake clock | 169 passed; 0 failures/errors/skips; 0 side-effect violations |
| `PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. timeout 30s /usr/bin/python3 -B checks/check_face_offscreen.py` | Existing local 13 PNG hashes and in-memory pixels | 3 passed; 0 failures/errors/skips |
| `PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. timeout 30s /usr/bin/python3 -B checks/check_display_offscreen.py` | Existing display app, SDL dummy | 2 passed; 0 failures/errors/skips |
| `PYGAME_HIDE_SUPPORT_PROMPT=1 PYTHONPATH=. timeout 30s /usr/bin/python3 -B checks/check_conversation_offscreen.py` | New conversation UI, SDL dummy, missing assets, late response at quit | 3 passed; 0 failures/errors/skips |
| `timeout 30s .venv/bin/python -B -m bmo_ai.offline_demo --headless` | Nine synthetic scenarios | Exit 0; expected scenario outcomes observed |

The same guarded 169-test suite and new 3-test dummy suite also passed in a
temporary Python-code-only copy without PNGs, models, private documents, settings
or a copied virtual environment, using existing interpreters. A standard-library
headless success demo in that copy exited 0. These are repeated validations, not
additional unique tests. Local final unique total: **177** (169 mock + 8
memory/dummy), zero final failures/errors/skips. No dependency checks were blocked.

Regression coverage includes blocked input/LLM/TTS/playback with continued UI
events/render/quit, full state queue cancellation, uncooperative late responses,
sanitized error codes, small-queue backpressure, stale turns and repeated inputs.
Finite worker and demo runs each exercised 100 turns; a separate warmup + 100-turn
tracemalloc check asserted retained Python growth below 256 KiB. Histories/queues
are bounded and completed threads are released. This does not establish native
SDL memory behavior or indefinite-run stability. Original tests were retained;
one existing face test now has its mock player explicitly signal playback start
instead of relying on the old premature SPEAKING transition.

Monotonic timing records distinguish prepare, input (fake STT), LLM, TTS and
playback. All demo timings are labeled synthetic and include artificial waits;
no API/device latency, voice quality or lip-sync improvement is claimed.

During development, a new test's tuple/list harness error was corrected. The
audit harness initially rejected the existing sleep-child spawn implementation
and SDK platform-header metadata probing; it now permits only that exact synthetic
timeout child and stubs the SDK metadata. A review probe found a full-queue cancel
race; post-publication cancellation checks and an uncooperative fake regression
corrected it. Final checks above passed without weakening/removing/skipping
baseline tests. The original 130-test run also passed before implementation.

Remote read initially failed inside the restricted network environment; the
authorized read with network access succeeded and confirmed the start SHA.
No implementation blocker is currently known. Commit/push is pending at this
checkpoint; the remote was re-read and still matches the start SHA. No reset,
rebase, pull or fetch was performed.

Publication review selected exactly 14 Python/Markdown files. Current file
contents passed UTF-8/type/symlink checks and limited credential, private-key,
personal-path and email patterns. Staged contents and allowlist will be checked
again before commit. This is a bounded pattern/manual review, not a guarantee of
secret absence or a new full-history audit; no assets, raw logs or local operating
documents are selected. Existing exclusion rules are unchanged.

Remaining limits / manual confirmation:

- Cancellation is cooperative. A dependency already in blocking IO can continue;
  the UI suppresses results and accepts no replacement until that worker exits.
- Actual desktop behavior, audio, API, camera, controller and motor checks remain
  outside authorization and unverified. No live backend launcher was added.
- Optional emotion data contract and sample-level lip sync are not implemented.
- PNGs remain local and unpublished; fallback warning is expected in a checkout.
- From `software/`, a person may run
  `PYGAME_HIDE_SUPPORT_PROMPT=1 /usr/bin/python3 -B -m bmo_ai.offline_demo --windowed`
  for a finite synthetic desktop demo. ESC/Q/window close or Ctrl-C exits; C
  requests cancellation. Real device/API integration requires separate approval.

All results above are from this run, separate from archived records. No physical
display, microphone, speaker, camera, GPIO, fan, servo, RP2040 or IMU was accessed.
No paid API or local AI model was run. Fan, firmware, motor, OS/service/security
settings, dependencies, CI, exclusions, original projects and backups are intact.
