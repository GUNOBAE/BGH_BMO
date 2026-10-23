# Media feature v1

`MediaFeature(content_root)` defaults to an empty catalogue and `ASSET_PENDING`.
The user's songs have not been provided. No song selection, download, scraping,
singing synthesis or voice conversion is performed. `make_fixture_media(root)`
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
