# Local media

`MediaFeature(content_root)` defaults to an empty catalogue and `ASSET_PENDING`.
The default remains empty until private media is explicitly imported. No song
download, scraping, singing synthesis or voice conversion is performed. `make_fixture_media(root)`
is an explicit simulation helper: it creates two short generated PCM files in an
existing private test directory and never overwrites existing files.

The host owns mode/audio focus and request/generation identifiers. Public
`catalog()`/`snapshot()` data contains safe IDs/status/capabilities, not local
paths. `start`, `stop`, `poll`, `close`, `pause`, `resume` and `next` share host
results/events. Fake playback is silent and reports `simulated=True`,
`executed=False`, `completed=True`, `completion='simulated'`. The coordinator
returns to the face on `media_ended`/`media_failed` only after resource release.

`next(request_id=..., mode_generation=...)` replaces the current track without an
intermediate terminal event. At the end it stops with `end_of_catalog`; it does
not silently loop. Repeated logical request IDs do not restart/advance again.
The bounded admission ledger is in memory, not a restart-proof journal.

## Local import

Both `import_manifest(payload, content_root=root)` and
`feature.import_manifest(payload)` validate the complete manifest before
registration. The latter rejects replacement while playing.

```json
{
  "version": 1,
  "items": [{
    "id": "user_song_one",
    "title": "User-provided title",
    "source": "user_provided_local_file",
    "path": "songs/song.wav",
    "sha256": "REPLACE_WITH_THE_ACTUAL_64_LOWERCASE_HEX_HASH",
    "media_type": "audio/wav"
  }]
}
```

Optional fields are `duration`, `cue_path`, `cue_sha256` (the cue pair must be
provided together). Unknown fields, commands, URLs, unsafe IDs, absolute paths,
parent traversal, encoded path tricks, symlinks in any component and hardlinked
files are rejected. The approved content root is absolute and no-follow opened.
Files must be bounded regular files with a matching SHA-256. Readiness is checked
at registration and the file/hash is checked again immediately before playback.
A pinned verified descriptor is held until stop; the playback backend does not
re-open an unverified path. Default constructors do not create content roots.

PCM WAV duration is measured and any supplied duration must agree. Other known
types retain optional metadata and remain `UNSUPPORTED` if the backend lacks
that codec; the importer does not invoke ffprobe or guess installed codecs.
Provided cue JSON consists only of bounded `time`/`mouth` records, verified by its
own hash and ordered relative to the same playback clock. Cue files are explicit
user input, not automatically inferred vocal alignment. Mixed-song RMS is not
advertised as accurate singing lip-sync.

## Backends and live boundary

`FakeMediaBackend` uses an injected clock, emits no sound and defaults to
pause/resume unsupported. Tests can explicitly enable its fake pause capability.
`AlsaWavBackend` is an explicit WAV-only owned-process adapter. It remains
`UNVERIFIED` until **both** `enabled=True` and `volume_reviewed=True` are supplied
by trusted host configuration. It inherits no Fish gain setting: conversation
gain stays 0.25, and media volume requires a separate physical review. It supports
stop, not pause/seek. The inherited file descriptor goes directly to the owned
player; no credential-bearing environment, shell, arbitrary URL or filename
command is passed. Process-start/position is an approximation, not proof of sound.

Actual user media, codec availability, audible volume and on-device playback are
not tested in this task. Tests use generated private fixtures and mocked player
boundaries; no real ALSA playback occurs. Cleanup failure remains `unknown` with
the resource retained, so the host must not start overlapping sound.

## Supplied Space Potatoes seed

`MEDIA_SEED.json` in the private handoff is **not** loaded as a runtime manifest.
The explicit adapter recognizes the reviewed MP3, PCM WAV and H264/AAC MP4 by
their independent SHA-256 values. It validates the bounded archive without
following links or executing its Python/HTML, probes/decodes local files with
timeouts and resource limits, then creates a private v2 library entry. No new
Python package is needed; import requires installed `ffmpeg` and `ffprobe`.

```sh
python -m bmo_ai.media_assets \
  --media-seed /absolute/private/BMO_Media_Seed_SpacePotatoes_v1.zip \
  --sha256 5de21e50e275e37207f185247f7062f14bd58d46686eb92c8b7fd0f78ff2cde2 \
  --dry-run

python -m bmo_ai.media_assets \
  --media-seed /absolute/private/BMO_Media_Seed_SpacePotatoes_v1.zip \
  --sha256 5de21e50e275e37207f185247f7062f14bd58d46686eb92c8b7fd0f78ff2cde2 \
  --content-root /absolute/private/music \
  --private-use-confirmed --install
```

Both commands are import-only: no speaker/device/network/provider call, no
listening approval and no runtime enablement. `--private-use-confirmed` records
local import permission; source remains `user_provided` and public distribution
rights `NOT_VERIFIED`. It does not invent a copyright licence. Originals remain
unchanged, existing library files are never overwritten, and retirement hides
only the matching manifest while retaining all assets/provenance.

There is one catalogue ID, `space_potatoes`, with `face` and `video` variants.
Face plays the supplied PCM. Video uses PCM decoded **from that MP4's embedded
audio**, through the same owned Python output; the browser video stays muted.
The separate MP3 is retained as source evidence and is never layered on Video.
`open_video()` duplicates a pinned verified MP4 descriptor for the existing
bounded HTTP Range bridge; there is no external player or full-video RAM buffer.

Prepared energy metadata is used only when bound to the exact face WAV hash and
sample zero with a supported ordered numeric format. Other handoff layouts are
preserved, and a bounded 20ms RMS envelope is measured from the verified WAV.
Both paths are labelled `whole_mix_energy_approximation`: accompaniment can
move the mouth, and this is not phoneme/lyric lip-sync. No timed instructions
from a seed script or label are executed. Cues are sampled at the owned audio
position; pause closes the mouth, seek selects the current cue without replaying
old events, and stop releases the descriptor/cues.

`SampleClockWavBackend` uses the installed system `libasound` with nonblocking
PCM IO. Its position is accepted PCM frames minus ALSA's queued frame delay,
not a process-spawn timer. `output_started` remains false until consumed frames
advance; muted video and face cues wait for this observation. A stopped/paused
stream is fully closed before seek/resume creates its replacement. Native pause
support is not required. MasterVolume scales each small block once at raw gain
1.0; mute also drops queued output. ALSA progress is not proof of audible output
or the correct physical MAX98357A route: those require supervised Pi listening.
An underrun, invalid delay or stalled output is a visible failure rather than a
guessed synchronization clock. The previous `AlsaWavBackend` is retained for
legacy adapters, where `clock_quality=process_start_approximation` is explicit.

The private seed itself was not available in the Cloud checkout. Synthetic
short H264/AAC/MP3/PCM fixtures verify decode/import/hash/seek/ownership; actual
seed registration, browser playback and listening on the Pi remain separate.
