"""Allowlisted Fish prosody for one BMO voice; never modifies the reply or Piper input.

Fish S2 accepts inline natural-language square-bracket directions. Existing
Aaron Soft timbre and the single paid synthesis request remain unchanged.
This is an opt-in audio experiment until the user approves real listening.
"""
from .fish_tts import SOFT_AARON_DIRECTION

# Keep instructions modest: users explicitly disliked harsh or helium voices.
EMOTION_DIRECTIONS = {
    "happy": "[smiling, warm and playfully delighted] ",
    "sad": "[gentle, concerned, slightly wistful] ",
    "crying": "[softly tearful, slightly trembling, still understandable] ",
    "surprised": "[genuinely surprised, delighted and lively] ",
    "angry": "[cute pout, mildly grumpy and teasing, never shouting] ",
}
APPROVED_VALIDATIONS = frozenset(("manual", "validated"))


def fish_styled_text(text, *, emotion="neutral", validation="none",
                     expressive=False, soft=True, notice=False):
    """Format a speech-only TTS request from trusted, validated host metadata.

    The text is the already normalized spoken sentence; neither it nor model
    output may supply directions. Directives never enter dialogue history.
    If no validated emotion exists, the pre-existing voice profile is exact.
    """
    if type(text) is not str or type(expressive) is not bool or type(soft) is not bool or type(notice) is not bool:
        raise ValueError("invalid_voice_performance")
    if type(emotion) is not str or type(validation) is not str:
        raise ValueError("invalid_voice_performance")
    base = SOFT_AARON_DIRECTION if soft else ""
    direction = EMOTION_DIRECTIONS.get(emotion, "") if (expressive and not notice and
                           validation in APPROVED_VALIDATIONS) else ""
    return base + direction + text
