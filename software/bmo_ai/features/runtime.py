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
    arms_commissioned: bool = False  # reviewed daily B1 profile; current link still required
    music_supervised: bool = False  # explicit reviewed local WAV output only
    native_supertux: bool = False  # explicit fixed distro game; never arbitrary commands

    def __post_init__(self):
        if any(type(value) is not bool for value in (self.simulate, self.conversation_ptt_microphone,
                                                    self.camera_supervised, self.camera_video_supervised,
                                                    self.arms_supervised, self.arms_commissioned,
                                                    self.music_supervised, self.native_supertux)):
            raise FeatureError("invalid_runtime")
        if ((self.camera_supervised or self.camera_video_supervised or self.arms_enabled
             or self.music_supervised or self.native_supertux) and self.simulate
                or self.camera_video_supervised and not self.camera_supervised
                or self.arms_supervised and self.arms_commissioned):
            raise FeatureError("invalid_camera_runtime")

    @property
    def arms_enabled(self):
        return self.arms_supervised or self.arms_commissioned

    def require(self, capability):
        if capability == "conversation_ptt_microphone":
            if not self.conversation_ptt_microphone:
                raise FeatureError("microphone_opt_in_required")
            return
        if capability not in DEVICE_CAPABILITIES:
            raise FeatureError("unknown_capability")
        if capability == "arms" and self.arms_enabled:
            return
        if capability == "music" and self.music_supervised:
            return
        if capability == 'external_games' and self.native_supertux:
            return  # Host additionally checks the fixed native catalogue ID.
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
                **({"arms": "explicit_commissioned"} if self.arms_commissioned else {}),
                **({"music": "explicit_supervised"} if self.music_supervised else {}),
                **({'external_games': 'explicit_supertux_only'} if self.native_supertux else {}),
                "real_features_enabled": self.camera_supervised or self.arms_enabled or self.music_supervised or self.native_supertux,
                "pi_live_verified": False}
