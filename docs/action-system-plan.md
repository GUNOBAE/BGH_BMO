# BMO Action System — isolated integration plan (2026-10-21)

## Verified baseline and parallel work

- Raspberry Pi 5 (8GB) with Debian 13, 7-inch display and RP2040-Zero PCB.
- Live dialogue works: GPT-4.1-mini + GPT-4o-mini-transcribe + Fish Aaron Soft (S2.1 Pro Free, spoken Beemo, 25% gain).
- Local Piper voice is an independent ongoing test. Do not modify its model or environment.
- Screen HTML v7-based v9, Game Center and firmware work happen in other chats. They own those assets.
- The arm backend is disabled and MPU6050 readings are not trusted. This branch does not access motors, serial, GPIO, camera or the real display.

## Action proposals, never raw command execution

| Request | Allowlisted action | Present behavior | Future verified adapter |
| --- | --- | --- | --- |
| "노래 불러줘" | open_music_menu | Preview only | Select verified saved tracks |
| Selected song | play_song(track_id) | Preview after catalog check | Play local audio |
| "음악 꺼줘" | stop_song | Preview only | Stop BMO music only |
| "게임하자" | open_game_menu | Preview only | Launch BMO Game Center |
| Selected game | launch_game(game_id) | Preview after catalog check | Verified HTML5 game |
| "게임 종료" | close_game | Preview only | Restore BMO face |
| "사진 찍어줘" | take_photo | Preview only | Say Cheese, capture, confirm save |
| "웃어봐" | show_expression(emotion) | Preview only | Connect to finalized face |
| "조용히 해" | stop_speaking | Preview only | Interrupt BMO TTS |
| "팔 들어/춤춰" | Arm movement NOT exposed | Blocked | Supervised RP2040 test later |

1. **GPT never executes shell code or drives GPIO directly.** Function calls become strictly validated action proposals.
2. Assets have trusted game_id/track_id catalogs. No arbitrary paths or subprocess arguments are accepted.
3. A camera should speak "Cheese!" before capture but should only claim success after file saving succeeds.
4. Servo motion must remain disabled until safe angles, speed, direction, emergency stop, RP2040 firmware, and button mappings (notably old red GP7 arm-toggle conflict) are audited.
5. IMU data stays out of control logic until calibration and self-test issues are resolved.
6. One audio manager must coordinate music/game sound, Fish TTS, and cancel/ducking.
7. GPT responses and the face should reflect true executed results, not successful-looking placeholders.

## Speed and intelligence evaluation

Use the same BMO prompt, cases and tool schemas for:
- Existing `gpt-4.1-mini`: control baseline; no reasoning parameter.
- `gpt-6-luna`: `reasoning.effort=none`.
- `gpt-6.1-sol`: `reasoning.effort=low`.

The lab tests 7 canned examples (English/Korean conversation, photo, game, music, hypothetical quote, unsafe motor), reports tool match, token usage, response latency and estimated GPT cost. The evaluation parses tool calls but **never executes any actions**. Final model choice needs subjective voice/chat evaluation and real response timing, not just a score.

### Dry-run commands — zero cloud cost, zero physical actions

```bash
cd /home/bgh/BGH_BMO/software
.venv/bin/python -m bmo_ai.action_lab --say '비모 사진 찍어줘'
.venv/bin/python -m bmo_ai.action_lab --say '게임하자!'
.venv/bin/python -m bmo_ai.model_lab
.venv/bin/python -m unittest discover -s tests -p 'test_action_lab.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_model_lab.py' -v
```

### Optional API benchmark (requires explicit paid opt-in)

```bash
# Export OPENAI_API_KEY securely first. Do not paste the key into this command.
.venv/bin/python -m bmo_ai.model_lab --run --confirm-cost
```

This sends 7 fixed prompts per model (21 API requests total by default), with an output cap and zero SDK retries. `--max-estimate-usd 0.25` provides **only a local preflight estimate**, not a real account spending limit. No generated response can reach motors, camera, games, or speakers in the benchmark.

## Integration order / owners

1. **Screen thread**: finish v7-based v9 face. Publish only its face event contract: IDLE / LISTENING / THINKING / SPEAKING plus separate emotion and speech timing.
2. **Game thread**: develop independent legal/owned HTML5 games and a Game Center with launch, exit, controller and return-to-face.
3. **Local Piper thread**: continue voice experimentation in isolated environment. Fish remains primary.
4. **This action lab**: action schemas, conservative exact-command fast path, safe preview, model A/B harness. Do not merge into current Pi voice runtime yet.
5. **Camera thread**: prove local still-photo capture with verified Pi Camera Module 3 Wide before attaching take_photo.
6. **Hardware/Codex**: read-only diagnostics until user reviews RP2040 firmware, servos and IMU; no spontaneous movement.
7. **Integration**: connect GPT tool calls and fixed commands to verified adapters, with status/result events for the face.
8. **Latency work**: measure end-of-user-speech to first BMO sound; then consider VAD and streaming Fish. Present 5-second recording + sequential STT/GPT/TTS is a real latency bottleneck.

Keep `feature/bmo-action-lab` separate from `feature/bmo-local-piper-fallback` and current screen/game work. No current branch or Pi setup is replaced.

Sources:
- https://developers.openai.com/api/docs/models
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/docs/guides/latency-optimization
