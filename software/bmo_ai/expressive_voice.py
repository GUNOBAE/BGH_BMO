"""Host-owned Fish S2 directions; model text never becomes a TTS instruction.

The selected voice, gain and pronunciation stay outside this policy. These cues
request delivery, not proof that the generated audio sounds expressive. Approval
still requires listening. Construction and planning perform no IO.
"""
from dataclasses import dataclass

from .expression_contract import FACE_EMOTIONS, ExpressionReply


REACTIONS = ("none", "chuckle", "gasp", "playful_puff", "sob")
SUPPORTED_MODELS = frozenset(("s2.1-pro-free", "s2.1-pro"))
EMOTION_CUES = {
    "neutral": "", "happy": "[happy] ", "sad": "[sad] ",
    "surprised": "[surprised] ", "angry": "[slightly angry] ",
    "crying": "[sad] ",
}
# Fixed, quiet short sounds. No speaker, pitch, gain, SSML or external clip IDs.
REACTION_CUES = {
    "chuckle": ("happy", "[chuckling] Hehe. "),
    "gasp": ("surprised", "[gasping] Oh! "),
    "playful_puff": ("happy", "[sighing] Pboo~. "),
    "sob": ("crying", "[sobbing] Oh... "),
}


@dataclass(frozen=True, slots=True)
class VoiceDelivery:
    text: str
    emotion: str
    reaction: str
    mechanism: str


class ExpressivePolicy:
    """At most one reaction per three successful new utterances, never random.

    Repeat plays its exact saved WAV; it does not advance this budget. Failed or
    cancelled synthesis/playback does not consume it. Planning itself is pure.
    """
    def __init__(self):
        self._successful = 0
        self._last_reaction = -3

    def plan(self, spoken, metadata, *, model):
        value = metadata if type(metadata) is dict else {}
        emotion = value.get("emotion", "neutral")
        if emotion not in FACE_EMOTIONS:
            emotion = "neutral"
        # Keep the same strict clean-speech boundary before adding host tags.
        ExpressionReply(spoken, emotion)
        if model not in SUPPORTED_MODELS:
            return VoiceDelivery(spoken, "neutral", "none", "plain_fallback")
        validated = value.get("emotion_validation") == "validated"
        if not validated:
            return VoiceDelivery(spoken, "neutral", "none", "plain_fallback")
        reaction = value.get("reaction", "none")
        if (type(reaction) is not str or reaction not in REACTION_CUES
                or REACTION_CUES[reaction][0] != emotion
                or self._successful - self._last_reaction < 3):
            reaction = "none"
        cue = REACTION_CUES[reaction][1] if reaction != "none" else ""
        return VoiceDelivery(EMOTION_CUES[emotion] + cue + spoken,
                             emotion, reaction, "fish_s2_allowlist_v1")

    def completed(self, delivery):
        if delivery.reaction != "none":
            self._last_reaction = self._successful
        self._successful += 1
