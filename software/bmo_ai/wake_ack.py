"""Read-only approved wake acknowledgment contract; no generation or player.

The existing Host owns AudioFocus and plays a returned GreetingAsset on its
existing session voice. Missing/unapproved/corrupt assets select visual listening
only. This module never publishes, approves, creates clients, or requests audio.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import fish_tts
from .greeting_cache import GreetingCache, GreetingError, GreetingSpec
from .pronunciation import spoken_bmo_name


WAKE_ACK_ID = "wake_ack_01"
WAKE_ACK_TEXT = "Yeah? What's up?"
WAKE_ACK_SCHEMA = "bmo_wake_ack_v1"
MAX_ACK_SECONDS = 3.0


def default_wake_ack_directory(*, home=None):
    root = Path.home() if home is None else Path(home)
    return root/".cache/bmo/wake-ack-v1"


@dataclass(frozen=True, slots=True)
class WakeAckSpec(GreetingSpec):
    @property
    def spoken(self):
        value = spoken_bmo_name(WAKE_ACK_TEXT, self.fish_name_mode)
        return fish_tts.SOFT_AARON_DIRECTION+value if self.fish_delivery == "soft" else value

    def public(self):
        return dict(version=1, schema=WAKE_ACK_SCHEMA, clip_id=WAKE_ACK_ID,
            text=WAKE_ACK_TEXT, provider="fish", model=self.fish_model,
            voice_id=self.fish_voice_id, name_mode=self.fish_name_mode,
            delivery=self.fish_delivery, gain=self.gain, spoken=self.spoken,
            max_duration_seconds=MAX_ACK_SECONDS, cue_algorithm="original_bridge_30fps_v1")


class WakeAckCache:
    """One approved immutable bundle, separate from boot/idle/dialogue caches.

    Construction and snapshot are side-effect free. load() checks the existing
    GreetingCache format/SHA/cues/spec/approval and yields either its private
    temporary asset or None. Cancellation and caller playback errors propagate;
    they are never disguised as an absent cache or retried.
    """
    def __init__(self, config, directory=None, *, home=None, organized_pack=None):
        from .voice_pack import OrganizedVoicePack
        if organized_pack is not None and not isinstance(organized_pack, OrganizedVoicePack):
            raise ValueError('organized_voice_pack_required')
        self.spec = WakeAckSpec.from_config(config)
        self.directory = Path(directory) if directory is not None else default_wake_ack_directory(home=home)
        self._cache = GreetingCache(self.directory, self.spec)
        self.organized_pack = organized_pack
        self._source_model, self._asset_source = None, None
        self._status, self._reason, self._sha = "NOT_CHECKED", "not_checked", None

    def snapshot(self):
        return dict(schema=WAKE_ACK_SCHEMA, clip_id=WAKE_ACK_ID, status=self._status,
            reason_code=self._reason, approved=self._status == "APPROVED_READY",
            sha256=self._sha, spec_fingerprint=self.spec.fingerprint,
            source_model=self._source_model, asset_source=self._asset_source,
            automatic_generation=False, automatic_approval=False, player_owned=False,
            missing_asset_fallback="VISUAL_LISTENING", maximum_seconds=MAX_ACK_SECONDS)

    @contextmanager
    def load(self, *, simulate=False, cancel_check=None):
        self._status, self._reason, self._sha = "CHECKING", "checking", None
        self._source_model, self._asset_source = None, None
        if self.organized_pack is not None:
            from .voice_bank import VoiceBankError
            try:
                registered = self.organized_pack.snapshot()['wake_ack_registered']
                if registered:
                    asset = self.organized_pack.load_wake_ack(cancel_check=cancel_check)
            except VoiceBankError:
                self._status, self._reason = 'ASSET_INVALID', 'wake_ack_pack_invalid'
                yield None
                return
            if registered:
                if asset is None:
                    self._status, self._reason = 'ASSET_UNAPPROVED', 'wake_ack_unapproved'
                    yield None
                    return
                try:
                    self._status, self._reason, self._sha = 'APPROVED_READY', 'ok', asset.sha256
                    self._source_model, self._asset_source = asset.source_model, 'organized_voice_pack'
                    yield asset
                finally:
                    asset.close()
                return
        try:
            asset = self._cache.load(simulate=simulate, require_approved=True,
                                     cancel_check=cancel_check)
        except GreetingError as error:
            self._status = {
                "greeting_missing": "ASSET_MISSING", "greeting_unapproved": "ASSET_UNAPPROVED",
                "greeting_spec_changed": "ASSET_SPEC_CHANGED",
                "greeting_simulated_only": "SIMULATED_ASSET_REJECTED",
            }.get(error.code, "ASSET_INVALID")
            self._reason = "wake_ack_"+error.code.removeprefix("greeting_")
            yield None
            return
        if asset.info["duration_seconds"] > MAX_ACK_SECONDS:
            asset.close()
            self._status, self._reason = "ASSET_INVALID", "wake_ack_duration"
            yield None
            return
        try:
            self._status, self._reason, self._sha = "APPROVED_READY", "ok", asset.sha256
            self._source_model, self._asset_source = self.spec.fish_model, 'legacy_wake_cache'
            yield asset
        finally:
            asset.close()
