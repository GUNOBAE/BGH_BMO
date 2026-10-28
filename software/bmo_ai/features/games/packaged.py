"""Reviewed local web games; no subprocess, device, ROM or arbitrary asset path.

The bundle is verified once and served from an immutable bytes snapshot. Python
owns admission/input/lifetime; the original JavaScript engines perform gameplay.
Browser readiness is a software handshake, never Pi or physical-pad evidence.
"""
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from uuid import uuid4

from ..contracts import CatalogItem, FeatureError, FeatureEvent, HostResult
from . import GamesFeature


PACK_ROOT = Path(__file__).with_name("pack")
PACK_ID = "bmo_original_games_v1"
GAME_IDS = ("bmo_blocks", "bmo_maze", "bmo_snake")
FILES = frozenset(("engines.js", "LICENSE.txt"))
# Updated with the original bundle manifest by the integration owner. Manifest
# and asset pins must both change in a review when a packaged source changes.
MANIFEST_SHA256 = "13b307af9d5938bc5d07faf8d211ba95677d82f90eb08d6db068e8174bda68e6"
INPUT_KEYS = frozenset(("UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT"))


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise FeatureError("invalid_game_manifest")
        value[key] = item
    return value


def _read_local(root, name, maximum):
    directory = descriptor = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=directory)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid not in (0, os.geteuid()) or before.st_mode & 0o0022
                or not 0 < before.st_size <= maximum):
            raise FeatureError("unsafe_game_bundle")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(maximum + 1)
        after = os.fstat(descriptor)
        fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) != before.st_size or fields(before) != fields(after):
            raise FeatureError("game_bundle_changed")
        return raw
    except FeatureError:
        raise
    except (OSError, ValueError, TypeError):
        raise FeatureError("game_bundle_unavailable") from None
    finally:
        for fd in (descriptor, directory):
            if fd is not None:
                os.close(fd)


@dataclass(frozen=True)
class GameBundle:
    manifest_sha256: str
    catalog: tuple
    resources: object


def load_bundle(*, root=None, manifest_sha256=None):
    """Fixed allowlist, exact manifest identity and per-file SHA; no execution."""
    root = PACK_ROOT if root is None else Path(root)
    expected = MANIFEST_SHA256 if manifest_sha256 is None else manifest_sha256
    if type(expected) is not str or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise FeatureError("invalid_game_manifest")
    manifest_raw = _read_local(root, "manifest.json", 16384)
    if hashlib.sha256(manifest_raw).hexdigest() != expected:
        raise FeatureError("game_manifest_hash_mismatch")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"), object_pairs_hook=_unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(FeatureError("invalid_game_manifest")))
        if (type(manifest) is not dict or set(manifest) != {"version", "pack_id", "license", "files", "games"}
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["pack_id"] != PACK_ID or manifest["license"] != "MIT"
                or type(manifest["files"]) is not dict or set(manifest["files"]) != FILES
                or type(manifest["games"]) is not list or len(manifest["games"]) != 3):
            raise FeatureError("invalid_game_manifest")
        catalog = []
        for expected_id, game in zip(GAME_IDS, manifest["games"]):
            if (type(game) is not dict or set(game) != {"id", "title"} or game["id"] != expected_id
                    or type(game["title"]) is not str or not 0 < len(game["title"]) <= 80):
                raise FeatureError("invalid_game_manifest")
            catalog.append(CatalogItem(expected_id, game["title"], "READY", "verified_local_web",
                ("packaged_web", "normalized_input", "exit_hold", "exit_escape", "silent_game"), False))
        resources = {}
        for name in sorted(FILES):
            digest = manifest["files"][name]
            if type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                raise FeatureError("invalid_game_manifest")
            raw = _read_local(root, name, 524288)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise FeatureError("game_asset_hash_mismatch")
            mime = "text/javascript; charset=utf-8" if name.endswith(".js") else "text/plain; charset=utf-8"
            resources["/games/" + name] = (mime, raw)
        return GameBundle(expected, tuple(catalog), MappingProxyType(resources))
    except FeatureError:
        raise
    except (UnicodeError, ValueError, KeyError, TypeError):
        raise FeatureError("invalid_game_manifest") from None


class PackagedWebRunner:
    simulated = False
    verified = True
    capabilities = frozenset(("packaged_web", "normalized_input", "exit_hold", "exit_escape", "audio_stop"))
    READY_TIMEOUT = 8.

    def __init__(self, bundle, clock):
        if not isinstance(bundle, GameBundle):
            raise FeatureError("game_bundle_required")
        self.bundle, self.clock = bundle, clock
        self.active = self.ready = False
        self.game_id = self.launch_id = None
        self.sequence = 0
        self.inputs, self.held = deque(maxlen=128), set()
        self._deadline = None

    def start(self, item):
        if item not in self.bundle.catalog or self.active:
            raise FeatureError("invalid_packaged_game")
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise FeatureError("invalid_game_clock")
        self.active, self.ready = True, False
        self.game_id, self.launch_id = item.id, "webgame_" + uuid4().hex
        self.sequence = 0
        self.inputs.clear(); self.held.clear()
        self._deadline = now + self.READY_TIMEOUT

    def browser_ready(self, launch_id):
        if not self.active or launch_id != self.launch_id:
            raise FeatureError("stale_game_launch")
        self.ready = True

    def input(self, key, pressed):
        if not self.active or not self.ready or key not in INPUT_KEYS:
            return
        if pressed == (key in self.held):
            return
        self.held.add(key) if pressed else self.held.discard(key)
        self.sequence += 1
        self.inputs.append({"sequence": self.sequence, "key": key, "pressed": pressed})

    def poll(self):
        if self.active and not self.ready:
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or now >= self._deadline:
                return "failed"
        return None

    def stop(self):
        self.active = self.ready = False
        self.inputs.clear(); self.held.clear()
        self._deadline = None

    close = stop

    def snapshot(self):
        return {"game_id": self.game_id, "launch_id": self.launch_id, "active": self.active,
                "ready": self.ready, "input_sequence": self.sequence,
                "inputs": [dict(item) for item in self.inputs], "held": sorted(self.held),
                "entry": "/games/engines.js", "physical_verified": False}


class PackagedGamesFeature(GamesFeature):
    """Same mode/exit contracts as existing games, with a browser-ready gate."""
    def __init__(self, *, clock, bundle=None):
        self.bundle = load_bundle() if bundle is None else bundle
        if not isinstance(self.bundle, GameBundle):
            raise FeatureError("game_bundle_required")
        super().__init__(catalog_items=self.bundle.catalog,
            runner_factory=lambda _: PackagedWebRunner(self.bundle, clock), clock=clock)

    def is_packaged(self, game_id):
        return game_id in GAME_IDS and any(item.id == game_id for item in self.bundle.catalog)

    def resources(self):
        return dict(self.bundle.resources)

    def start(self, catalog_id, *, request_id, mode_generation):
        result = super().start(catalog_id, request_id=request_id, mode_generation=mode_generation)
        if result.status == "running":
            # The base process runner is synchronous, but a web engine must
            # first be created/rendered by the admitted Desktop browser.
            self._events.pop()
            self._state = "loading"
            return HostResult(request_id, "accepted", "web_game_loading", simulated=False)
        return result

    def browser_ready(self, launch_id, generation):
        if generation != self._generation or not isinstance(self._runner, PackagedWebRunner):
            raise FeatureError("stale_game_launch")
        was_ready = self._runner.ready
        self._runner.browser_ready(launch_id)
        self._state = "running"
        if not was_ready:
            self._events.append(FeatureEvent("game_started", self._generation, self._request,
                "web_game_ready", {"simulated": False, "catalog_id": self._active.id}))

    def snapshot(self):
        return {**super().snapshot(), "packaged": True,
                "bundle_sha256": self.bundle.manifest_sha256,
                "web": self._runner.snapshot() if isinstance(self._runner, PackagedWebRunner) else None,
                "physical_verified": False}
