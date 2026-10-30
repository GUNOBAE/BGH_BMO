"""Shared host outcomes; these never claim a simulated action was executed."""
from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Mapping


MODES = frozenset({"conversation", "game_menu", "game", "photo", "video", "music"})
STATUSES = frozenset({"accepted", "running", "succeeded", "failed", "cancelled",
                      "rejected", "unknown", "unsupported", "simulated"})
READINESS = frozenset({"READY", "ASSET_PENDING", "RUNTIME_MISSING", "UNSUPPORTED", "UNVERIFIED"})
GESTURES = frozenset({"none", "wave", "celebrate", "shrug"})
INTENTS = frozenset({"open_game_menu", "launch_game", "exit_game", "return_to_face",
                     "play_song", "stop_media", "take_photo", "start_face_tracking",
                     "stop_face_tracking", "arm_gesture", "stop_arms", "start_video", "stop_video",
                     "set_volume", "mute_audio", "unmute_audio", "volume_up", "volume_down"})


def valid_id(value):
    return type(value) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) is not None


def request_id_ok(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value) is not None


class FeatureError(RuntimeError):
    def __init__(self, code="operation_failed"):
        self.code = code if valid_id(code) else "operation_failed"
        self.safe_message = "The feature operation could not be completed."
        super().__init__(self.safe_message)


@dataclass(frozen=True, slots=True)
class HostResult:
    request_id: str
    status: str
    code: str = "ok"
    simulated: bool = True
    executed: bool = False
    completed: bool = False
    completion: str = "not_started"

    def __post_init__(self):
        if (not request_id_ok(self.request_id) or self.status not in STATUSES
                or not valid_id(self.code) or any(type(value) is not bool for value in
                                                (self.simulated, self.executed, self.completed))
                or not valid_id(self.completion) or (self.simulated and self.executed)):
            raise FeatureError("invalid_result")

    def public(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class FeatureEvent:
    kind: str
    mode_generation: int
    request_id: str | None = None
    code: str = "ok"
    data: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if (not valid_id(self.kind) or type(self.mode_generation) is not int
                or self.mode_generation < 0 or not valid_id(self.code)
                or (self.request_id is not None and not request_id_ok(self.request_id))):
            raise FeatureError("invalid_event")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: str
    title: str
    readiness: str = "ASSET_PENDING"
    reason_code: str = "asset_pending"
    capabilities: tuple[str, ...] = ()
    simulated: bool = False

    def __post_init__(self):
        if (not valid_id(self.id) or type(self.title) is not str or not self.title.strip()
                or len(self.title) > 160 or self.readiness not in READINESS
                or not valid_id(self.reason_code) or type(self.simulated) is not bool):
            raise FeatureError("invalid_catalog")
        object.__setattr__(self, "capabilities", tuple(self.capabilities))

    def public(self):
        return {"id": self.id, "title": self.title, "readiness": self.readiness,
                "reason_code": self.reason_code, "capabilities": list(self.capabilities),
                "simulated": self.simulated}
