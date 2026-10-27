"""Deterministic BMO arm gestures, not model-generated motor commands.

This file never opens a port or allocates PWM. Full end points come from the
previously physically verified original GP7 0/180 firmware:
    physical UP   -> left GP11=600us, right GP10=2400us
    physical DOWN -> left GP11=2400us, right GP10=600us
Actual joint position has no encoder feedback. Model replies can name only
allowed gestures (wave, celebrate, shrug), never arbitrary pulse/angles.
"""
from dataclasses import dataclass


LEGACY_REVIEWED_LIMITS = {
    "reviewed": True,
    "left": {"min_us": 600, "home_us": 600, "max_us": 2400},
    "right": {"min_us": 600, "home_us": 2400, "max_us": 2400},
}

# These are conservative animation patterns, NOT new calibration limits.
@dataclass(frozen=True, slots=True)
class GestureStep:
    left: int | None
    right: int | None
    dwell_s: float


# At every stage, one or both sides can move. All values stay within the
# legacy verified pulse range; motion itself requires a reviewed firmware
# build, a physical cutoff and the explicit host runtime opt-in.
_PLANS = {
    "wave": (GestureStep(None, 2050, .42),
             GestureStep(None, 2400, .42),
             GestureStep(None, 2050, .42),
             GestureStep(None, 2400, .42)),
    "celebrate": (GestureStep(1050, 1950, .55),
                  GestureStep(600, 2400, .55)),
    "shrug": (GestureStep(780, 2220, .28),
              GestureStep(600, 2400, .28)),
    "raise_left_arm": (GestureStep(600, None, .2),),
    "raise_right_arm": (GestureStep(None, 2400, .2),),
    "both_up": (GestureStep(600, 2400, .2),),
    "both_down": (GestureStep(2400, 600, 1.5),),
}
MODEL_GESTURES = frozenset(("wave", "celebrate", "shrug"))
EXPLICIT_ACTIONS = frozenset(_PLANS)


def plan_gesture(name, *, from_model=False):
    """Bounded immutable steps. Never accept raw angles, PWM, or free text."""
    if type(name) is not str or name not in (MODEL_GESTURES if from_model else EXPLICIT_ACTIONS):
        raise ValueError("unapproved_arm_gesture")
    steps = _PLANS[name]
    for step in steps:
        for value in (step.left, step.right):
            if value is not None and (type(value) is not int or not 600 <= value <= 2400):
                raise ValueError("invalid_arm_gesture_plan")
    return tuple(steps)
