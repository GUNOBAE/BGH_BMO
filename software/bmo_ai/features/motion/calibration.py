"""Validate explicitly supplied calibration data; never load hardware/settings."""
from dataclasses import dataclass
import json
import math

from ..contracts import FeatureError


@dataclass(frozen=True, slots=True)
class ArmLimits:
    min_degrees: float
    neutral_degrees: float
    max_degrees: float
    direction: int

    def __post_init__(self):
        values = (self.min_degrees, self.neutral_degrees, self.max_degrees)
        if (any(type(value) not in (int, float) or not 0 <= value <= 180
                or not math.isfinite(value) for value in values)
                or not 0 <= self.min_degrees < self.neutral_degrees < self.max_degrees <= 180
                or type(self.direction) is not int or self.direction not in (-1, 1)):
            raise FeatureError("invalid_calibration")


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    left: ArmLimits
    right: ArmLimits
    verified: bool = False
    safe_stop_verified: bool = False

    def __post_init__(self):
        if (not isinstance(self.left, ArmLimits) or not isinstance(self.right, ArmLimits)
                or type(self.verified) is not bool or type(self.safe_stop_verified) is not bool):
            raise FeatureError("invalid_calibration")

    @property
    def reviewed(self):
        return self.verified and self.safe_stop_verified


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise FeatureError("invalid_calibration")
        result[name] = value
    return result


def load_profile(value):
    """Accept a bounded JSON document or mapping, never a path from model output.

    A reviewed profile records prerequisites; it cannot enable real motion in
    this implementation. Both arms require independently checked limits.
    """
    if isinstance(value, (str, bytes)):
        if len(value) > 16384:
            raise FeatureError("invalid_calibration")
        try:
            value = json.loads(value, object_pairs_hook=_unique)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise FeatureError("invalid_calibration") from None
    if (type(value) is not dict or set(value) !=
            {"version", "left", "right", "verified", "safe_stop_verified"}
            or type(value["version"]) is not int or value["version"] != 1):
        raise FeatureError("invalid_calibration")
    try:
        arms = []
        for side in ("left", "right"):
            arm = value[side]
            if type(arm) is not dict or set(arm) != {
                "min_degrees", "neutral_degrees", "max_degrees", "direction"
            }:
                raise FeatureError("invalid_calibration")
            arms.append(ArmLimits(**arm))
        return CalibrationProfile(*arms, value["verified"], value["safe_stop_verified"])
    except (TypeError, KeyError, OverflowError):
        raise FeatureError("invalid_calibration") from None
