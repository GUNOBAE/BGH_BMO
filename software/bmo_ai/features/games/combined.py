"""Keep the existing reviewed web games beside one explicitly allowed native game."""
import math
import time

from ..contracts import FeatureError, HostResult, request_id_ok, valid_id


class CombinedGamesFeature:
    def __init__(self, packaged=None, native=None, *, clock=time.monotonic):
        self.packaged, self.native, self.clock = packaged, native, clock
        self._features = tuple(f for f in (packaged, native) if f is not None)
        self._items = tuple(item for feature in self._features for item in feature.catalog())
        if len({item.id for item in self._items}) != len(self._items):
            raise FeatureError("invalid_catalog")
        self._selected, self._active = 0, None
        self._attempts, self._closed = {}, False

    @property
    def native_active(self):
        return self.native is not None and self.native.native_active

    @property
    def native_foreground(self):
        return self.native_active

    def is_native(self, game_id):
        return self.native is not None and self.native.is_native(game_id)

    def is_packaged(self, game_id):
        check = getattr(self.packaged, "is_packaged", None)
        return callable(check) and check(game_id)

    def catalog(self):
        return self._items

    def resources(self):
        resources = getattr(self.packaged, "resources", None)
        return resources() if callable(resources) else {}

    def snapshot(self):
        active = self.native if self.native_active else self._active
        if active is not None:
            base = active.snapshot()
        elif self.packaged is not None:
            base = self.packaged.snapshot()
        elif self.native is not None:
            base = self.native.snapshot()
        else:
            base = dict(state="idle", active_id=None, capabilities=[], simulated=True, web=None)
        native = self.native.snapshot()["native"] if self.native is not None else None
        return {**base, "selected_id": self._items[self._selected].id if self._items else None,
                "readiness": "READY" if any(item.readiness == "READY" for item in self._items) else
                             self._items[0].readiness if self._items else "ASSET_PENDING",
                "reason_code": base.get("reason_code", "ok" if self._items else "game_pack_missing"),
                "native": native, "native_active": self.native_active,
                "packaged": active is not self.native and callable(getattr(self.packaged, "is_packaged", None)),
                "web": None if self.native_active else base.get("web"), "physical_verified": False}

    def start(self, catalog_id, *, request_id, mode_generation):
        if not valid_id(catalog_id) or not request_id_ok(request_id) or type(mode_generation) is not int or mode_generation < 0:
            raise FeatureError("invalid_request")
        if self._closed:
            return HostResult(request_id, "rejected", "feature_closed")
        if request_id in self._attempts:
            return HostResult(request_id, "rejected", "duplicate_request" if self._attempts[request_id] == catalog_id else "request_id_conflict")
        if self.native_active or self._active is not None and self._active.snapshot().get("active_id") is not None:
            return HostResult(request_id, "rejected", "game_busy")
        if len(self._attempts) >= 1024:
            return HostResult(request_id, "rejected", "request_capacity_exceeded")
        feature = next((f for f in self._features if any(item.id == catalog_id for item in f.catalog())), None)
        if feature is None:
            return HostResult(request_id, "rejected", "unknown_game")
        self._attempts[request_id] = catalog_id
        result = feature.start(catalog_id, request_id=request_id, mode_generation=mode_generation)
        if feature.snapshot().get("active_id") is not None:
            self._active = feature
        return result

    def stop(self, reason="user"):
        feature = self.native if self.native_active else self._active
        if feature is None:
            return HostResult("games:idle", "succeeded", "already_stopped", completed=True, completion="stopped")
        result = feature.stop(reason)
        if result.status != "unknown" and feature.snapshot().get("active_id") is None:
            self._active = None
        return result

    def poll(self):
        events = tuple(event for feature in self._features for event in feature.poll())
        if self._active is not None and self._active.snapshot().get("active_id") is None:
            self._active = None
        return events

    def game_input(self, logical, pressed=True, now=None):
        feature = self.native if self.native_active else self._active
        return feature.game_input(logical, pressed, now) if feature is not None else {"consumed": False, "action": "none"}

    def menu_input(self, logical, pressed=True, now=None):
        if (type(logical) is not str or logical.upper() not in ("UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT", "ESC", "PAD_DISCONNECTED")
                or type(pressed) is not bool or now is not None and (type(now) not in (int, float) or not math.isfinite(now))):
            raise FeatureError("invalid_input")
        if not pressed:
            return {"consumed": False, "action": "none"}
        key = logical.upper()
        if key in ("B", "ESC"):
            return {"consumed": True, "action": "return_to_face"}
        if self._items and key in ("UP", "DOWN", "LEFT", "RIGHT"):
            self._selected = (self._selected + (-1 if key in ("UP", "LEFT") else 1)) % len(self._items)
        item = self._items[self._selected] if self._items else None
        return {"consumed": True, "action": "launch" if key in ("A", "START") and item and item.readiness == "READY" else "select",
                "catalog_id": item.id if item else None, "code": item.reason_code if item else "game_pack_missing"}

    def browser_ready(self, launch_id, generation):
        ready = getattr(self.packaged, "browser_ready", None)
        if self.native_active or not callable(ready):
            raise FeatureError("stale_game_launch")
        return ready(launch_id, generation)

    def browser_outcome(self, value):
        outcome = getattr(self.packaged, "browser_outcome", None)
        if self.native_active or not callable(outcome):
            raise FeatureError("stale_game_outcome")
        return outcome(value)

    def pause(self):
        if self._active is not None:
            return self._active.pause()
        return HostResult("games:idle", "unsupported", "pause_unsupported")

    def resume(self):
        if self._active is not None:
            return self._active.resume()
        return HostResult("games:idle", "unsupported", "pause_unsupported")

    def close(self):
        failed = False
        for feature in self._features:
            try:
                feature.close()
            except Exception:
                failed = True
        if failed:
            raise FeatureError("cleanup_failed")
        self._closed = True
        self._active = None
