"""Explicit runtime boundary; a model response cannot grant device permission.

The only live route delivered in offline v2 is supervised conversation. Device
capabilities remain denied until a later, separately reviewed activation change.
Injected test IO is supported without inheriting production credentials.
"""
from dataclasses import dataclass

from .contracts import FeatureError


DEVICE_CAPABILITIES = ("arms", "imu", "camera", "external_games", "music")


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    simulate: bool = True
    conversation_ptt_microphone: bool = False

    def __post_init__(self):
        if type(self.simulate) is not bool or type(self.conversation_ptt_microphone) is not bool:
            raise FeatureError("invalid_runtime")

    def require(self, capability):
        if capability == "conversation_ptt_microphone":
            if not self.conversation_ptt_microphone:
                raise FeatureError("microphone_opt_in_required")
            return
        if capability not in DEVICE_CAPABILITIES:
            raise FeatureError("unknown_capability")
        if not self.simulate:
            raise FeatureError("capability_denied")

    def public(self):
        return {"conversation": "mock" if self.simulate else "explicit_live",
                "conversation_ptt_microphone": ("mock" if self.simulate else "explicit_live")
                    if self.conversation_ptt_microphone else "denied",
                **{name: "mock" if self.simulate else "denied" for name in DEVICE_CAPABILITIES},
                "real_features_enabled": False, "pi_live_verified": False}
