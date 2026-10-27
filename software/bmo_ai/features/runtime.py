"""Explicit runtime boundary; a model response cannot grant device permission.

The only live route delivered in offline v2 is supervised conversation. Device
capabilities remain denied until a later, separately reviewed activation change.
Injected test IO is supported without inheriting production credentials.
"""
from dataclasses import dataclass

from .contracts import FeatureError


DEVICE_CAPABILITIES = ("arms", "imu", "camera", "camera_video", "external_games", "music")


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    simulate: bool = True
    conversation_ptt_microphone: bool = False
    camera_supervised: bool = False
    camera_video_supervised: bool = False
    arms_supervised: bool = False  # explicitly reviewed injected motion; default OFF
    music_supervised: bool = False  # explicit reviewed local WAV output only

    def __post_init__(self):
        if any(type(value) is not bool for value in (self.simulate, self.conversation_ptt_microphone,
                                                    self.camera_supervised, self.camera_video_supervised,
                                                    self.arms_supervised, self.music_supervised)):
            raise FeatureError("invalid_runtime")
        if ((self.camera_supervised or self.camera_video_supervised or self.arms_supervised
             or self.music_supervised) and self.simulate
                or self.camera_video_supervised and not self.camera_supervised):
            raise FeatureError("invalid_camera_runtime")

    def require(self, capability):
        if capability == "conversation_ptt_microphone":
            if not self.conversation_ptt_microphone:
                raise FeatureError("microphone_opt_in_required")
            return
        if capability not in DEVICE_CAPABILITIES:
            raise FeatureError("unknown_capability")
        if capability == "arms" and self.arms_supervised:
            return
        if capability == "music" and self.music_supervised:
            return
        if capability == "camera" and self.camera_supervised:
            return
        if capability == "camera_video" and self.camera_video_supervised:
            return
        if not self.simulate:
            raise FeatureError("capability_denied")

    def public(self):
        return {"conversation": "mock" if self.simulate else "explicit_live",
                "conversation_ptt_microphone": ("mock" if self.simulate else "explicit_live")
                    if self.conversation_ptt_microphone else "denied",
                **{name: "mock" if self.simulate else "denied" for name in DEVICE_CAPABILITIES},
                **({"camera": "explicit_supervised"} if self.camera_supervised else {}),
                **({"camera_video": "explicit_supervised"} if self.camera_video_supervised else {}),
                **({"arms": "explicit_supervised"} if self.arms_supervised else {}),
                **({"music": "explicit_supervised"} if self.music_supervised else {}),
                "real_features_enabled": self.camera_supervised or self.arms_supervised or self.music_supervised,
                "pi_live_verified": False}
