"""Opt-in, turn-scoped emotional gestures. Zero GPIO, USB, PWM or I/O.

This policy selects *existing* named gestures, not servo angles. It never
changes the legacy GP7 firmware, verified 600..2400us arm limits or R10 face.
Physical movement remains blocked without separately verified host firmware.
"""
from ..contracts import GESTURES

# Existing allowlisted gestures only. Understated/neutral/distressed replies
# are silent; the speech model may still explicitly choose an allowed gesture.
EMOTION_GESTURES = {"happy": "celebrate", "surprised": "shrug"}


def select_emotion_gesture(emotion, proposed="none", *,
                           previous_spoken_emotion=None, intent=None, enabled=False):
    """Return an allowlisted gesture or 'none'. Calls must be at player start.

    Explicit model gestures take precedence. Repeated emotion doesn't create
    a new automatic movement; a mode change or repeat never calls this itself.
    Model intents suppress inferred motions to avoid moving while launching
    games, cameras or other actions. No calibration is inferred from emotions.
    """
    if type(proposed) is not str or proposed not in GESTURES:
        return "none"
    if proposed != "none":
        return proposed
    if not enabled or type(emotion) is not str or intent is not None:
        return "none"
    if emotion == previous_spoken_emotion:
        return "none"
    return EMOTION_GESTURES.get(emotion, "none")
