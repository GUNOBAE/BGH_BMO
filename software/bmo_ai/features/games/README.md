# Games feature v1

`GamesFeature()` starts with an empty catalogue and `ASSET_PENDING`. The original
game ZIP is unavailable; this module does not claim that the historical list of
23 games is installed. `fixture=True` explicitly registers two synthetic games,
`fixture_game_a` and `fixture_game_b`. They are separate from Action v0's unchanged
`demo_game` placeholder.

The coordinator acquires mode/audio ownership before calling
`start(id, request_id=..., mode_generation=...)`. `catalog()` returns the shared
`CatalogItem` DTOs; `snapshot()` exposes state and safe IDs only. Starts, stops and
terminal events use the shared host contracts. Successful fake operations report
`simulated=True`, `executed=False`, `completed=True`, `completion='simulated'`.
The in-memory admission ledger bounds duplicates/conflicts and is not persistent.

`menu_input(logical, pressed=True, now=None)` returns a selection/launch/face
request for the coordinator. No physical button numbers are assumed. While a
game runs, B is forwarded to its game. The fixture runner supports Esc and a
continuous normalized Start+Select hold of 1.2 seconds, debounced once per launch.
`poll()` advances the hold using the injected monotonic clock. Exit requests are
events; the coordinator performs stop and mode return. Pad loss stops the owned
runner and reports a released resource. There is no always-listening voice stop.

| Runner | Input/exit | Pause | Enable condition |
| --- | --- | --- | --- |
| `FakeGameRunner` | Verified synthetic logical input, Esc and hold | Optional explicit fake capability | Simulation only |
| `OwnedProcessRunner`, native | App-specific, not assumed to receive browser input | Unsupported | Trusted command configuration, explicit verification and readiness probe |
| `OwnedProcessRunner`, RetroArch | Menu Toggle/Close Content are app-specific; universal hold is not claimed | Unsupported | Same explicit verification, real pad/exit review still required |

A real catalogue entry must be prepared by the host after checking its assets,
runtime and usable exit path. No command or path comes from an LLM/catalog ID.
Unverified runners refuse launch. The process adapter starts a dedicated owned
session/group, tries its optional graceful quit, then performs bounded TERM/KILL
and child reaping. It never uses killall or signals another application's group.
Readiness probing is caller supplied; a process existing alone does not prove a
game displayed correctly. Real output, native/RetroArch mapping and progress
preservation remain unverified. `progress_may_be_lost` is explicit.

Tests run only generated states and harmless owned Python children. They cover
menu → game → list → another game → face, B forwarding, hold/release/debounce,
crash, pad loss, cleanup failure and TERM-resistant child cleanup. No real game,
game server, media output, device or downloaded asset is used.
