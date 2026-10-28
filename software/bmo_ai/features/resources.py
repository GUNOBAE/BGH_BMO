"""Owner-thread foreground transactions, audio leases, logical input and gaze."""
from dataclasses import dataclass
import math
import time

from .contracts import FeatureError, MODES


class ModeManager:
    def __init__(self):
        self.mode = "conversation"
        self.generation = 0
        self.transitioning = False
        self.last_error = None
        self._participants = {}

    def register(self, mode, *, enter=lambda context: None, leave=lambda: None):
        if mode not in MODES:
            raise FeatureError("invalid_mode")
        self._participants[mode] = (enter, leave)

    def transition(self, target, context=None, *, force=False):
        if target not in self._participants or self.transitioning:
            raise FeatureError("mode_busy")
        if target == self.mode and not force:
            return self.generation
        self.transitioning = True
        self.generation += 1  # Invalidates old callbacks even if preparation fails.
        old = self.mode
        released = False
        try:
            self._participants[old][1]()
            released = True
            self._participants[target][0](dict(context or {}))
            self.mode, self.last_error = target, None
        except Exception:
            if not released:
                self.mode, self.last_error = old, "mode_release_failed"
                raise FeatureError(self.last_error) from None
            self.last_error = "mode_prepare_failed"
            try:
                self._participants[target][1]()
            except Exception:
                self.mode, self.last_error = target, "mode_recovery_failed"
                raise FeatureError(self.last_error) from None
            self.mode = "conversation"
            try:
                self._participants["conversation"][0]({"recovery": True})
            except Exception:
                self.last_error = "mode_recovery_failed"
            raise FeatureError(self.last_error) from None
        finally:
            self.transitioning = False
        return self.generation

    def accepts(self, generation):
        return type(generation) is int and generation == self.generation


@dataclass(frozen=True, slots=True)
class AudioLease:
    owner: str
    generation: int


class AudioFocus:
    """No new owner until the old owner's stop/pause confirms release."""
    def __init__(self):
        self.lease = None
        self._stop = None
        self._generation = 0
        self._transitioning = False

    @property
    def owner(self):
        return self.lease.owner if self.lease else None

    def acquire(self, owner, *, stop=None, preempt=False):
        if self._transitioning:
            raise FeatureError("audio_busy")
        if self.lease is not None:
            if self.lease.owner == owner:
                return self.lease
            if not preempt or self._stop is None:
                raise FeatureError("audio_busy")
            try:
                self._transitioning = True
                released = self._stop()
            except Exception:
                raise FeatureError("audio_release_failed") from None
            finally:
                self._transitioning = False
            if released is not True:
                raise FeatureError("audio_release_unsupported")
            self.lease, self._stop = None, None
        self._generation += 1
        self.lease = AudioLease(owner, self._generation)
        self._stop = stop
        return self.lease

    def release(self, lease):
        if self._transitioning:
            raise FeatureError("audio_busy")
        if lease != self.lease or lease is None:
            return False
        self.lease, self._stop = None, None
        return True

    def stop(self):
        if self._transitioning:
            return False
        if self.lease is None:
            return True
        if self._stop is None:
            return False
        try:
            self._transitioning = True
            if self._stop() is not True:
                return False
        except Exception:
            return False
        finally:
            self._transitioning = False
        self.lease, self._stop = None, None
        return True


LOGICAL_INPUTS = frozenset({"UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START",
                            "SELECT", "ESC", "TALK", "STOP", "MENU", "PAD_LOST"})


class InputRouter:
    def __init__(self, mode_manager, *, stop=None, talk=None):
        self.modes = mode_manager
        self.stop, self.talk = stop, talk
        self._handlers = {}
        self._physical = None

    def bind(self, mode, handler):
        if mode not in MODES:
            raise FeatureError("invalid_mode")
        self._handlers[mode] = handler

    def set_physical_map(self, mapping, *, verified=False):
        if verified is not True or any(value not in LOGICAL_INPUTS for value in mapping.values()):
            raise FeatureError("input_unverified")
        self._physical = dict(mapping)

    def route(self, key, pressed=True, *, now=None, physical=False):
        if type(pressed) is not bool:
            raise FeatureError("invalid_input")
        if physical:
            if self._physical is None or key not in self._physical:
                raise FeatureError("input_unverified")
            key = self._physical[key]
        if key not in LOGICAL_INPUTS:
            raise FeatureError("invalid_input")
        if pressed and key == "STOP" and self.stop:
            return self.stop()
        if pressed and key == "TALK" and self.talk:
            return self.talk()
        handler = self._handlers.get(self.modes.mode)
        return handler(key, pressed, time.monotonic() if now is None else now) if handler else None


@dataclass(frozen=True, slots=True)
class GazeOffer:
    owner: str
    x: float
    y: float
    expires_at: float
    generation: int


class GazeArbiter:
    """Never writes emotion/mouth. Native activities keep their original gaze."""
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._offers = {}

    def offer(self, owner, x, y, *, generation, expires_at):
        if (owner not in ("manual", "camera", "reaction", "dialogue", "idle")
                or type(generation) is not int or generation < 0
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y, expires_at))):
            raise FeatureError("invalid_gaze")
        self._offers[owner] = GazeOffer(owner, max(-1., min(1., x)), max(-1., min(1., y)),
                                        float(expires_at), generation)

    def clear(self, owner=None):
        if owner is None:
            self._offers.clear()
        else:
            self._offers.pop(owner, None)

    def resolve(self, *, phase, generation):
        valid = {name: value for name, value in self._offers.items()
                 if value.generation == generation and self.clock() <= value.expires_at}
        selected = valid.get("manual")
        if selected is None and phase in ("listening", "thinking"):
            return {"owner": "native", "apply": False, "x": 0., "y": 0.}
        if selected is None:
            selected = ((valid.get("dialogue") if phase == "speaking" else None)
                        or valid.get("camera") or valid.get("reaction") or valid.get("idle"))
        return ({"owner": selected.owner, "apply": True, "x": selected.x, "y": selected.y}
                if selected else {"owner": "idle", "apply": True, "x": 0., "y": 0.})
