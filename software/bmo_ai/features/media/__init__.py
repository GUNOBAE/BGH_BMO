"""Local media admission with a silent fake backend and an empty default list."""
from collections import deque
import hashlib
import math
import os
from pathlib import Path
import time
import wave
from bisect import bisect_right

from ..contracts import CatalogItem, FeatureError, FeatureEvent, HostResult, request_id_ok, valid_id
from .assets import AssetHandle, MediaAsset, import_manifest


class FakeMediaBackend:
    """Silent clock-driven playback; no audio output or vocal lip-sync claim."""
    simulated = verified = True
    supported_types = frozenset({"audio/wav"})

    def __init__(self, *, clock=time.monotonic, pausable=False):
        self.clock, self.pausable = clock, pausable
        self.handle = None
        self._started = self._paused = None
        self._offset = 0.0
        self._failed = False

    @property
    def capabilities(self):
        return ("stop", "pause", "resume") if self.pausable else ("stop",)

    def start(self, handle):
        self.handle, self._started, self._paused = handle, self.clock(), None
        self._offset, self._failed = 0.0, False

    def position(self):
        return self._offset + (max(0, self.clock() - self._started) if self.handle and self._paused is None else 0)

    def pause(self):
        self._offset, self._paused = self.position(), self.clock()

    def resume(self):
        self._started, self._paused = self.clock(), None

    def fail(self):
        self._failed = True

    def poll(self):
        return "failed" if self._failed else "ended" if self.handle and self.handle.duration is not None and self.position() >= self.handle.duration else None

    def stop(self):
        self.handle = self._started = self._paused = None

    close = stop


class AlsaWavBackend:
    """Opt-in WAV process adapter; disabled until separate volume review.

    No Fish gain is inherited. No pause/seek is advertised. Process start and
    the monotonic position are approximations, not proof of audible output.
    The verified descriptor is inherited by this owned child, avoiding a second
    path lookup after hashing. Automatic tests never invoke the real player.
    """
    simulated = False
    supported_types = frozenset({"audio/wav"})
    capabilities = ("stop",)

    def __init__(self, *, device=None, enabled=False, volume_reviewed=False, clock=time.monotonic):
        from ...audio import _device_args
        if type(enabled) is not bool or type(volume_reviewed) is not bool:
            raise FeatureError("invalid_media_backend")
        self.device_args = tuple(_device_args(device))
        self.verified, self.clock = enabled and volume_reviewed, clock
        self._runner = self._started = None

    def start(self, handle):
        if not self.verified:
            raise FeatureError("media_backend_unverified")
        if self._runner is not None:
            raise FeatureError("media_busy")
        from ..games import OwnedProcessRunner
        self._runner = OwnedProcessRunner(
            ("aplay", "-q", *self.device_args, f"/proc/self/fd/{handle.fd}"),
            verified=True, ready_probe=lambda process: True, pass_fds=(handle.fd,))
        self._runner.start(handle.asset)
        self._started = self.clock()

    def position(self):
        return max(0, self.clock() - self._started) if self._started is not None else 0

    def poll(self):
        return self._runner.poll() if self._runner else None

    def stop(self):
        if self._runner is not None:
            self._runner.stop()
            self._runner = self._started = None

    close = stop


class MediaFeature:
    def __init__(self, content_root, items=(), backend=None, clock=time.monotonic):
        self.content_root, self.clock = Path(content_root), clock
        self._items = tuple(items)
        if any(type(item) is not MediaAsset for item in self._items) or len({i.id for i in self._items}) != len(self._items):
            raise FeatureError("invalid_media_manifest")
        self.backend = backend
        self._handle = self._active = None
        self._request, self._generation, self._state = "media:idle", 0, "idle"
        self._events, self._attempts = deque(maxlen=64), {}
        self._closed = False
        self._asset_status = {}
        self._inspect_assets()

    def _inspect_assets(self):
        self._asset_status = {}
        for item in self._items:
            try:
                with AssetHandle(self.content_root, item):
                    pass
            except FeatureError as error:
                self._asset_status[item.id] = ("ASSET_PENDING" if error.code == "asset_pending" else "UNVERIFIED", error.code)

    def catalog(self):
        result = []
        for item in self._items:
            reason, readiness = "ok", "READY"
            if item.id in self._asset_status:
                readiness, reason = self._asset_status[item.id]
            elif self.backend is None:
                reason, readiness = "media_runtime_missing", "RUNTIME_MISSING"
            elif not self.backend.verified:
                reason, readiness = "media_backend_unverified", "UNVERIFIED"
            elif item.media_type not in self.backend.supported_types:
                reason, readiness = "unsupported_codec", "UNSUPPORTED"
            result.append(CatalogItem(item.id, item.title, readiness, reason,
                tuple(self.backend.capabilities) if self.backend else (),
                bool(self.backend and self.backend.simulated)))
        return tuple(result)

    def snapshot(self):
        return dict(state=self._state, active_id=self._active.id if self._active else None,
                    position=self.backend.position() if self._handle and hasattr(self.backend, "position") else 0,
                    readiness="READY" if any(i.readiness == "READY" for i in self.catalog()) else "ASSET_PENDING" if not self._items else "UNVERIFIED",
                    reason_code="user_media_missing" if not self._items else "ok",
                    capabilities=list(self.backend.capabilities) if self.backend else [],
                    simulated=bool(self.backend and self.backend.simulated),
                    cue_kind="provided" if self._handle and self._handle.cues else "none")

    def cue_snapshot(self):
        """Only explicitly supplied cues, sampled from the backend playback clock.

        No RMS inference, resynthesis or claim of acoustic synchronization. A
        paused player exposes closed, and absent cues leave the face untouched.
        """
        if self._handle is None or not self._handle.cues:
            return None
        position = float(self.backend.position())
        if not math.isfinite(position):
            raise FeatureError("invalid_media_clock")
        position = max(0., position)
        cues = self._handle.cues
        index = bisect_right([cue["time"] for cue in cues], position) - 1
        mouth = cues[index]["mouth"] if index >= 0 and self._state == "playing" else "closed"
        return {"request_id": self._request, "mode_generation": self._generation,
                "playing": self._state == "playing", "position": position, "mouth": mouth,
                "cue_kind": "provided"}

    def import_manifest(self, payload):
        if self._handle is not None:
            raise FeatureError("media_busy")
        candidate = import_manifest(payload, content_root=self.content_root)
        self._items = candidate
        self._inspect_assets()
        return self.catalog()

    def start(self, catalog_id, *, request_id, mode_generation):
        if not valid_id(catalog_id) or not request_id_ok(request_id) or type(mode_generation) is not int or mode_generation < 0:
            raise FeatureError("invalid_request")
        if self._closed:
            return HostResult(request_id, "rejected", "feature_closed")
        if request_id in self._attempts:
            return HostResult(request_id, "rejected", "duplicate_request" if self._attempts[request_id] == catalog_id else "request_id_conflict")
        if self._handle is not None:
            return HostResult(request_id, "rejected", "media_busy")
        if len(self._attempts) >= 1024:
            return HostResult(request_id, "rejected", "request_capacity_exceeded")
        item = next((i for i in self._items if i.id == catalog_id), None)
        public = next((i for i in self.catalog() if i.id == catalog_id), None)
        if public is None or public.readiness != "READY":
            return HostResult(request_id, "rejected", "unknown_media" if public is None else public.reason_code)
        self._attempts[request_id] = catalog_id
        self._request, self._generation = request_id, mode_generation
        try:
            self._handle = AssetHandle(self.content_root, item)  # Reverify immediately before playback.
            self._active = item
            self.backend.start(self._handle)
            self._state = "playing"
            self._events.append(FeatureEvent("media_started", mode_generation, request_id,
                "synthetic_fixture" if self.backend.simulated else "playback_started",
                {"catalog_id": catalog_id, "simulated": self.backend.simulated}))
            simulated = self.backend.simulated
            return HostResult(request_id, "simulated" if simulated else "running", "ok", simulated,
                              not simulated, simulated, "simulated" if simulated else "running")
        except Exception as error:
            code = error.code if isinstance(error, FeatureError) else "media_start_failed"
            if isinstance(error, FeatureError) and self._handle is None:
                self._asset_status[catalog_id] = ("ASSET_PENDING" if code == "asset_pending" else "UNVERIFIED", code)
            result = self._release()
            self._events.append(FeatureEvent("media_failed", mode_generation, request_id,
                code if result else "cleanup_failed", {"resource_released": result}))
            return HostResult(request_id, "failed" if result else "unknown", code if result else "cleanup_failed")

    def _release(self):
        try:
            if self.backend is not None and self._handle is not None:
                self.backend.stop()
        except Exception:
            self._state = "cleanup_failed"
            return False
        if self._handle is not None:
            self._handle.close()
        self._handle = self._active = None
        self._state = "idle"
        return True

    def stop(self, reason="user"):
        simulated = bool(self.backend is None or self.backend.simulated)
        if self._handle is None:
            return HostResult(self._request, "simulated" if simulated else "succeeded", "already_stopped",
                              simulated, False, True, "simulated" if simulated else "stopped")
        if not self._release():
            return HostResult(self._request, "unknown", "cleanup_failed", simulated, not simulated)
        code = reason if reason in {"user", "mode_change", "shutdown", "ended", "media_failed", "next_track"} else "user"
        self._events.append(FeatureEvent("media_failed" if code == "media_failed" else "media_ended",
            self._generation, self._request, code, {"resource_released": True, "simulated": simulated}))
        return HostResult(self._request, "simulated" if simulated else "succeeded", code,
                          simulated, False, True, "simulated" if simulated else "stopped")

    def pause(self):
        return self._pause(True)

    def resume(self):
        return self._pause(False)

    def _pause(self, pause):
        capability = "pause" if pause else "resume"
        simulated = bool(self.backend is None or self.backend.simulated)
        if self._handle is None or capability not in self.backend.capabilities:
            return HostResult(self._request, "unsupported", "pause_unsupported", simulated)
        if self._state == ("paused" if pause else "playing"):
            return HostResult(self._request, "simulated" if simulated else "succeeded", "already_" + self._state,
                              simulated, False, True, "simulated" if simulated else "operation_completed")
        try:
            getattr(self.backend, capability)()
            self._state = "paused" if pause else "playing"
            return HostResult(self._request, "simulated" if simulated else "succeeded", "ok", simulated,
                              not simulated, True, "simulated" if simulated else "operation_completed")
        except Exception:
            self.stop("media_failed")
            return HostResult(self._request, "failed", "media_control_failed", simulated)

    def next(self, *, request_id, mode_generation):
        current = next((n for n, item in enumerate(self._items) if self._active and item.id == self._active.id), -1)
        if not request_id_ok(request_id) or type(mode_generation) is not int or mode_generation < 0:
            raise FeatureError("invalid_request")
        if self._closed:
            return HostResult(request_id, "rejected", "feature_closed")
        if request_id in self._attempts:
            return HostResult(request_id, "rejected", "duplicate_request")
        if len(self._attempts) >= 1024:
            return HostResult(request_id, "rejected", "request_capacity_exceeded")
        following = current + 1
        if following >= len(self._items):
            self._attempts[request_id] = "end_of_catalog"
            result = self.stop("ended")
            return HostResult(request_id, result.status, "end_of_catalog", result.simulated,
                              False, result.completed, result.completion)
        if not self._release():
            return HostResult(request_id, "unknown", "cleanup_failed")
        return self.start(self._items[following].id, request_id=request_id, mode_generation=mode_generation)

    def poll(self):
        if self._handle is not None:
            try:
                terminal = self.backend.poll()
                if terminal is not None:
                    self.stop("media_failed" if terminal == "failed" else "ended")
            except Exception:
                self.stop("media_failed")
        events = tuple(self._events)
        self._events.clear()
        return events

    def close(self):
        result = self.stop("shutdown")
        if result.status == "unknown":
            raise FeatureError("cleanup_failed")
        self._closed = True


def make_fixture_media(content_root):
    """Explicitly create two generated PCM fixtures in an existing test root.

    Existing files are never overwritten. This is called only by simulation or
    tests, not by normal constructors and not for the user's missing songs.
    """
    root = Path(content_root)
    assets, created, root_fd = [], [], None
    try:
        # Verify every root component without following a symlink.
        from .assets import _open_root
        root_fd = _open_root(root)
        for n, suffix in enumerate(("a", "b")):
            name = f"fixture_tone_{suffix}.wav"
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=root_fd)
            created.append(name)
            with os.fdopen(fd, "wb") as stream, wave.open(stream, "wb") as wav:
                rate = 8000
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                data = b"".join(round(1000 * math.sin(2 * math.pi * (220 + n * 110) * i / rate)).to_bytes(2, "little", signed=True)
                                for i in range(rate // 2))
                wav.writeframes(data)
            read_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
            with os.fdopen(read_fd, "rb") as stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
            assets.append(MediaAsset(f"fixture_song_{suffix}", f"Synthetic song {suffix.upper()}",
                "generated_pcm_fixture", name, digest, "audio/wav", .5))
        return tuple(assets)
    except BaseException:
        for name in created:
            try:
                os.unlink(name, dir_fd=root_fd)
            except OSError:
                pass
        raise FeatureError("fixture_creation_failed") from None
    finally:
        if root_fd is not None:
            os.close(root_fd)
