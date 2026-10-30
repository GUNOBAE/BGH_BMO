"""Approved local character clips, separate from dialogue history/repeat cache.

Construction performs no IO. Runtime selection/loading never resolves keys or
calls a provider. The existing private greeting bundle supplies atomic storage,
SHA/cue verification, gained snapshots and listening approval for each clip.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import io
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
from threading import Event
import time
import wave

from . import audio, fish_tts
from .conversation import TurnCancelled
from .greeting_cache import GreetingCache, GreetingSpec, GreetingError, MAX_WAV_BYTES
from .private_credentials import _open_directory, _private_file, _close, CredentialError
from .pronunciation import spoken_bmo_name

MODELS = ("s2.1-pro-free", "s2.1-pro", "drama-3")
EMOTIONS = frozenset(("neutral", "happy", "sad", "surprised", "angry", "crying"))
CATALOG_PATH = Path(__file__).with_name("policies") / "voice-bank-v1.json"
_CLIP = re.compile(r"(?:boot|idle|reaction)_[0-9]{2}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


class VoiceBankError(RuntimeError):
    def __init__(self, code="voice_bank_unavailable"):
        self.code = code
        super().__init__("The approved character voice asset is unavailable.")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def catalog():
    """Original scripts only. Never represents an ungenerated WAV as approved."""
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        rows = raw["clips"]
        if raw["version"] != 1 or len(rows) != 32:
            raise ValueError()
        result = {}
        for row in rows:
            clip = row["clip_id"]
            if (not _CLIP.fullmatch(clip) or clip in result
                    or row["category"] != clip.split("_")[0]
                    or type(row["text"]) is not str or not 0 < len(row["text"]) <= 512
                    or type(row["direction"]) is not str or not 0 < len(row["direction"]) <= 600
                    or any(c in row["direction"] for c in "[]<>")
                    or not row["allowed_emotions"] or any(e not in EMOTIONS for e in row["allowed_emotions"])
                    or row["voice_id"] != fish_tts.AARON_BMO_VOICE_ID
                    or row["name_mode"] != "beemo" or row["gain"] != .25
                    or row["approved"] is not False or row["audio_sha256"] is not None
                    or row["status"] != "AUDIO_NOT_GENERATED"
                    or not 0 < row["target_max_seconds"] <= 10):
                raise ValueError()
            result[clip] = row
        if [sum(r["category"] == c for r in rows) for c in ("boot", "idle", "reaction")] != [8, 12, 12]:
            raise ValueError()
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise VoiceBankError("voice_bank_catalog_invalid") from None


def default_voice_bank_directory(*, home=None):
    root = Path.home() if home is None else Path(home)
    return root / ".cache" / "bmo" / "character-voice-bank-v1"


def wav_quality(path):
    """Bounded sample statistics for listening review, never audio/log content."""
    count = clipped = 0; peak = 0.
    try:
        with wave.open(str(path), "rb") as source:
            width = source.getsampwidth()
            if source.getcomptype() != "NONE" or width not in (1, 2, 3, 4):
                raise ValueError()
            limit = 128 if width == 1 else 1 << (width * 8 - 1)
            while True:
                block = source.readframes(4096)
                if not block: break
                for offset in range(0, len(block), width):
                    value = int.from_bytes(block[offset:offset + width], "little", signed=width > 1)
                    if width == 1: value -= 128
                    peak = max(peak, abs(value) / limit)
                    clipped += int(value <= -limit or value >= limit - 1)
                    count += 1
                    if count > 48000 * 10 * 2: raise ValueError()
        return dict(peak=round(peak, 6), clipped_samples=clipped, samples=count,
                    clipping_detected=clipped > 0)
    except (OSError, EOFError, wave.Error, ValueError):
        raise VoiceBankError("voice_bank_audio_invalid") from None


@dataclass(frozen=True, slots=True)
class ClipSpec(GreetingSpec):
    clip_id: str
    category: str
    text: str
    direction: str
    target_max_seconds: float

    @property
    def spoken(self):
        # Keep the same Aaron identity, without a fixed cheerful prefix that
        # contradicts a separately requested theatrical tearful/pout sample.
        identity = ("Same familiar Aaron character voice; " if self.category == "reaction"
                    and self.clip_id in ("reaction_07", "reaction_08", "reaction_09", "reaction_10", "reaction_11")
                    else "Same gentle, warm Aaron character; ")
        return ("[" + identity + self.direction + "] "
                + spoken_bmo_name(self.text, self.fish_name_mode))

    def public(self):
        return dict(version=1, bank="bmo_character_voice_v1", clip_id=self.clip_id,
                    category=self.category, text=self.text, spoken=self.spoken,
                    provider="fish", model=self.fish_model, voice_id=self.fish_voice_id,
                    name_mode=self.fish_name_mode, delivery=self.fish_delivery, gain=self.gain,
                    target_max_seconds=self.target_max_seconds,
                    cue_algorithm="original_bridge_30fps_v1")


@dataclass(slots=True, repr=False)
class BankAsset:
    clip_id: str
    category: str
    emotion: str
    model: str
    asset: object

    @property
    def path(self): return self.asset.path
    @property
    def info(self): return self.asset.info
    @property
    def cues(self): return self.asset.cues
    @property
    def sha256(self): return self.asset.sha256
    def public(self):
        return dict(clip_id=self.clip_id, category=self.category, emotion=self.emotion,
                    model=self.model, quality=wav_quality(self.path), **self.asset.public())
    def close(self): self.asset.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class VoiceBank:
    def __init__(self, directory, config, *, clock=time.time, chooser=None, simulate=False):
        self.directory = Path(directory)
        if (not self.directory.is_absolute() or ".." in self.directory.parts or "\0" in str(self.directory)
                or config.fish_voice_id != fish_tts.AARON_BMO_VOICE_ID
                or config.fish_name_mode != "beemo" or config.fish_gain != .25
                or config.fish_model not in MODELS or type(simulate) is not bool):
            raise VoiceBankError("voice_bank_configuration_invalid")
        self.config, self.clock = config, clock
        self.simulate = simulate
        self.chooser = chooser or random.SystemRandom().choice
        self._inventory = None
        self._recent = deque(maxlen=3)
        self._history_loaded = False
        self._last_used = {}
        self._history_error = False

    def _spec(self, clip_id, model):
        row = catalog().get(clip_id)
        if row is None or model not in MODELS:
            raise VoiceBankError("voice_bank_clip_invalid")
        return ClipSpec(model, self.config.fish_voice_id, "beemo", "soft", .25,
                        clip_id, row["category"], row["text"], row["direction"], row["target_max_seconds"])

    def _cache(self, clip_id, model):
        return GreetingCache(self.directory / model / clip_id, self._spec(clip_id, model))

    def _model_order(self):
        return (self.config.fish_model, *(m for m in MODELS if m != self.config.fish_model))

    def inventory(self, *, refresh=False):
        if self._inventory is not None and not refresh:
            return json.loads(json.dumps(self._inventory))
        values = []
        for clip_id, row in catalog().items():
            variants = {}
            for model in self._model_order():
                cache = self._cache(clip_id, model)
                directory = None
                try:
                    directory = cache._directory()
                    record, payload = cache._read(directory)
                    with wave.open(io.BytesIO(payload), "rb") as source:
                        duration = source.getnframes() / source.getframerate()
                    status = ("SIMULATED_ONLY" if record["simulated"] else "READY" if record["approved"]
                              else "LISTENING_NOT_APPROVED")
                    variants[model] = dict(status=status, approved=record["approved"],
                        sha256=record["sha256"], simulated=record["simulated"],
                        duration_seconds=round(duration, 4), lip_cue_count=len(record["cues"]))
                except GreetingError as error:
                    variants[model] = dict(status="AUDIO_NOT_GENERATED" if error.code == "greeting_missing"
                        else "ASSET_INVALID", approved=False, sha256=None, simulated=False)
                except (EOFError, wave.Error, ValueError, ZeroDivisionError):
                    variants[model] = dict(status="ASSET_INVALID", approved=False, sha256=None, simulated=False)
                finally:
                    _close(directory)
            best = next((m for status in ("READY", "LISTENING_NOT_APPROVED", "SIMULATED_ONLY", "ASSET_INVALID")
                         for m in self._model_order() if variants[m]["status"] == status), self.config.fish_model)
            values.append(dict(clip_id=clip_id, category=row["category"],
                allowed_emotions=list(row["allowed_emotions"]), allowed_contexts=list(row["allowed_contexts"]),
                cooldown_seconds=row["cooldown_seconds"], model=best,
                status=variants[best]["status"], approved=variants[best]["approved"], variants=variants))
        self._inventory = values
        return json.loads(json.dumps(values))

    def snapshot(self):
        values = self.inventory()
        return dict(version=1, total=len(values), approved=sum(v["status"] == "READY" for v in values),
                    status="READY" if any(v["status"] == "READY" for v in values) else "AUDIO_NOT_GENERATED",
                    automatic_generation=False, api_calls=0, listening_approval="REQUIRED",
                    recent_boot=list(self._recent), history_available=not self._history_error)

    @contextmanager
    def _lock(self, name):
        directory = lock = None
        try:
            directory = _open_directory(self.directory, create=True)
            lock = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                           0o600, dir_fd=directory)
            _private_file(os.fstat(lock), size_limit=0)
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise VoiceBankError("voice_bank_busy") from None
            yield directory
        except (CredentialError, OSError):
            raise VoiceBankError("voice_bank_storage_unsafe") from None
        finally:
            _close(lock); _close(directory)

    def generation_lock(self):
        return self._lock(".generation.lock")

    def exists(self, clip_id, model):
        cache = self._cache(clip_id, model)
        directory = None
        try:
            directory = cache._directory()
            cache._read(directory)
            return True
        except GreetingError as error:
            if error.code == "greeting_missing": return False
            raise VoiceBankError("voice_bank_asset_invalid") from None
        finally: _close(directory)

    def publish(self, clip_id, raw_wav, *, model=None, simulated=False):
        """Explicit import. Never overwrites an existing candidate or approval."""
        model = model or self.config.fish_model
        cache = self._cache(clip_id, model)
        descriptor = source = None
        try:
            # Inspect an owned regular-file snapshot, never a symlink, FIFO or
            # device path supplied to an explicit import operation.
            descriptor = os.open(raw_wav, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            _private_file(os.fstat(descriptor), size_limit=MAX_WAV_BYTES)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                payload = stream.read(MAX_WAV_BYTES + 1)
            source, info = cache._snapshot(payload)
            quality = wav_quality(source)
            if not .1 <= info["duration_seconds"] <= self._spec(clip_id, model).target_max_seconds:
                raise VoiceBankError("voice_bank_duration_invalid")
            with self._lock(".bank.lock"):
                if self.exists(clip_id, model):
                    raise VoiceBankError("voice_bank_asset_exists")
                result = cache.publish(source, approved=False, simulated=simulated)
        except (CredentialError, OSError, GreetingError, audio.AudioError, ValueError, TypeError):
            raise VoiceBankError("voice_bank_audio_invalid") from None
        finally:
            _close(descriptor)
            if source is not None: audio._discard(source, suppress=True)
        self._inventory = None
        return dict(clip_id=clip_id, model=model, status="LISTENING_NOT_APPROVED", source_quality=quality, **result)

    def approve(self, clip_id, sha256, *, model=None, listening_approved=False):
        if listening_approved is not True or type(sha256) is not str or not _SHA.fullmatch(sha256):
            raise VoiceBankError("voice_bank_listening_approval_required")
        model = model or self.config.fish_model
        with self.load(clip_id, model=model, require_approved=False, simulate=True) as asset:
            if asset.sha256 != sha256:
                raise VoiceBankError("voice_bank_sha_mismatch")
        result = self._cache(clip_id, model).approve(sha256)
        self._inventory = None
        return dict(clip_id=clip_id, model=model, status="SIMULATED_ONLY" if result["simulated"] else "READY", **result)

    def load(self, clip_id, *, model=None, require_approved=True, simulate=False, cancel_check=None):
        if model is None:
            row = next((r for r in self.inventory() if r["clip_id"] == clip_id), None)
            if row is None: raise VoiceBankError("voice_bank_clip_invalid")
            model = row["model"]
        try:
            asset = self._cache(clip_id, model).load(simulate=simulate,
                require_approved=require_approved, cancel_check=cancel_check)
            if not .1 <= asset.info["duration_seconds"] <= self._spec(clip_id, model).target_max_seconds:
                asset.close(); raise VoiceBankError("voice_bank_duration_invalid")
            row = catalog()[clip_id]
            return BankAsset(clip_id, row["category"], row["allowed_emotions"][0], model, asset)
        except GreetingError as error:
            codes = {"greeting_missing": "AUDIO_NOT_GENERATED", "greeting_unapproved": "LISTENING_NOT_APPROVED",
                     "greeting_simulated_only": "SIMULATED_ONLY"}
            raise VoiceBankError(codes.get(error.code, "voice_bank_asset_invalid")) from None

    def _read_history(self):
        if self._history_loaded: return
        self._history_loaded = True
        directory = descriptor = None
        try:
            directory = _open_directory(self.directory)
            if directory is None: return
            descriptor = os.open("selection.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
            _private_file(os.fstat(descriptor), size_limit=16384)
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                value = json.loads(stream.read(16385))
            if (type(value) is not dict or set(value) != {"version", "recent_boot", "last_used"}
                    or value["version"] != 1 or type(value["recent_boot"]) is not list
                    or len(value["recent_boot"]) > 3 or type(value["last_used"]) is not dict
                    or len(value["last_used"]) > 32): raise ValueError()
            known = catalog()
            if (any(c not in known or not c.startswith("boot_") for c in value["recent_boot"])
                    or any(c not in known or type(t) not in (int, float) or not math.isfinite(t) or t < 0
                           for c, t in value["last_used"].items())): raise ValueError()
            self._recent.extend(value["recent_boot"]); self._last_used = value["last_used"]
        except FileNotFoundError: pass
        except (CredentialError, OSError, ValueError, TypeError): self._history_error = True
        finally: _close(descriptor); _close(directory)

    def select(self, category, *, emotion="neutral", context="settled", recent=()):
        if category not in ("boot", "idle", "reaction") or emotion not in EMOTIONS:
            return None
        self._read_history()
        if self._history_error: return None
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0: return None
        blocked = set(recent) | (set(self._recent) if category == "boot" else set())
        candidates = []
        for value in self.inventory():
            playable = value["status"] == "READY" or self.simulate and value["status"] == "SIMULATED_ONLY" and value["approved"]
            if value["category"] != category or not playable or value["clip_id"] in blocked: continue
            if category == "boot" and context in ("quiet", "sleep") and "neutral" not in value["allowed_emotions"]: continue
            if category != "boot" and (emotion not in value["allowed_emotions"] or context not in value["allowed_contexts"]): continue
            # Real distress is quiet; strong anger/crying clips are never idle.
            if category == "idle" and (emotion in ("angry", "crying") or context in ("distress", "quiet", "sleep")): continue
            last = self._last_used.get(value["clip_id"])
            if last is not None and now - last < value["cooldown_seconds"]: continue
            candidates.append(value["clip_id"])
        return self.chooser(candidates) if candidates else None

    def completed(self, clip_id):
        """Record a heard clip only after successful playback, never admission."""
        row = catalog().get(clip_id)
        if row is None: return False
        self._read_history()
        if self._history_error: return False
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0: return False
        if row["category"] == "boot": self._recent.append(clip_id)
        self._last_used[clip_id] = now
        stage = None
        try:
            with self._lock(".history.lock") as directory:
                try:
                    stage = ".selection." + secrets.token_hex(12)
                    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(_json(dict(version=1, recent_boot=list(self._recent), last_used=self._last_used)))
                        stream.flush(); os.fsync(stream.fileno())
                    os.replace(stage, "selection.json", src_dir_fd=directory, dst_dir_fd=directory)
                    stage = None; os.fsync(directory)
                finally:
                    if stage is not None:
                        try: os.unlink(stage, dir_fd=directory)
                        except OSError: pass
            return True
        except (VoiceBankError, OSError): return False


class BankPlayer:
    """Caller holds the existing AudioFocus. One player, no brain/cache writes."""
    def __init__(self, config, bank, *, simulate=False, player=None):
        from .integration_voice import IntegrationVoice
        if not isinstance(bank, VoiceBank) or type(simulate) is not bool:
            raise VoiceBankError("voice_bank_configuration_invalid")
        self.bank, self.simulate = bank, simulate
        self.voice = IntegrationVoice(config, None, simulate=simulate, emit=lambda *_a, **_kw: None, player=player)
        self._cancelled, self._closed = Event(), Event()

    def play(self, clip_id, *, emit, cancel_check=None, on_playback_start=None,
             model=None, allow_unapproved=False):
        if self._closed.is_set(): raise VoiceBankError("voice_bank_closed")
        if type(allow_unapproved) is not bool: raise VoiceBankError("voice_bank_configuration_invalid")
        if not self.voice._speaking.acquire(blocking=False): raise VoiceBankError("voice_bank_busy")
        self._cancelled.clear(); self.voice._idle.clear()
        def check():
            if self._closed.is_set() or self._cancelled.is_set(): raise TurnCancelled()
            self.voice._check(cancel_check)
        try:
            with self.bank.load(clip_id, model=model, simulate=self.simulate,
                                require_approved=not allow_unapproved, cancel_check=check) as asset:
                self.voice._play(asset.path, "voice_bank_" + asset.category, asset.info, asset.cues,
                    check, emit, on_playback_start,
                    dict(emotion=asset.emotion, emotion_validation="validated", notice=False))
                check()
                persisted = self.bank.completed(clip_id) if not allow_unapproved else False
                return dict(completed=True, history_persisted=persisted, **asset.public())
        except (TurnCancelled, KeyboardInterrupt):
            emit("cancelled", source="voice_bank"); raise
        except BaseException:
            emit("error", source="voice_bank"); raise
        finally:
            self.voice._speaking.release(); self.voice._idle.set()

    def cancel(self): self._cancelled.set()

    def play_event(self, event, *, emit, cancel_check=None, on_playback_start=None):
        """Use an imported approved pack without any live-synthesis fallback."""
        if self._closed.is_set():
            raise VoiceBankError('voice_bank_closed')
        self._cancelled.clear()
        def check():
            if self._closed.is_set() or self._cancelled.is_set():
                raise TurnCancelled()
            self.voice._check(cancel_check)
        completed = self.voice.play_event(event, emit=emit, cancel_check=check,
                                         on_playback_start=on_playback_start)
        return dict(completed=completed, source='organized_voice_pack', generated=False)

    def close(self):
        self._closed.set(); self.cancel(); return self.voice.close()
