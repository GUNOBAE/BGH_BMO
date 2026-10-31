"""Host-owned Fish S2 directions; model text never becomes a TTS instruction.

The selected voice, gain and pronunciation stay outside this policy. These cues
request delivery, not proof that the generated audio sounds expressive. Approval
still requires listening. Construction and planning perform no IO.
"""
from dataclasses import dataclass

from .expression_contract import FACE_EMOTIONS, ExpressionReply
from .character_director import CharacterPlan, VOICE_DIRECTIONS, IDENTITY_DIRECTION


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
    override_soft: bool = False
    explicit_acting: bool = False


class ExpressivePolicy:
    """At most one reaction per three successful new utterances, never random.

    Repeat plays its exact saved WAV; it does not advance this budget. Failed or
    cancelled synthesis/playback does not consume it. Planning itself is pure.
    """
    def __init__(self):
        self._successful = 0
        self._last_reaction = -3
        self._last_character_reaction = None
        self._last_character_at = -3

    def plan(self, spoken, metadata, *, model):
        value = metadata if type(metadata) is dict else {}
        emotion = value.get("emotion", "neutral")
        if emotion not in FACE_EMOTIONS:
            emotion = "neutral"
        # Keep the same strict clean-speech boundary before adding host tags.
        ExpressionReply(spoken, emotion)
        if "character_plan" in value:
            return self._character_plan(spoken, value, model=model)
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

    def _character_plan(self, spoken, metadata, *, model):
        try:
            plan = CharacterPlan.from_public(metadata["character_plan"])
        except (ValueError, TypeError):
            return VoiceDelivery(spoken, "neutral", "none", "plain_fallback")
        if (model not in (*SUPPORTED_MODELS, "drama-3")
                or metadata.get("emotion_validation") != "validated"
                or metadata.get("emotion") != plan.face_emotion):
            return VoiceDelivery(spoken, "neutral", "none", "plain_fallback")
        reaction = plan.reaction_id
        if not plan.explicit_acting and (
                self._successful - self._last_character_at < 3
                or reaction == self._last_character_reaction and self._successful - self._last_character_at < 6):
            reaction = "none"
        cues = {"none": "", "chuckle": "[chuckling] ", "gasp": "[gasping] ",
                "playful_puff": "[sighing, brief playful puff] ", "sob": "[sobbing, one short breath] "}
        # The default Soft instruction used to precede and conflict with acted
        # crying/anger. Preserve voice identity separately and use one congruent
        # host delivery, without changing voice ID, pronunciation or gain.
        text = IDENTITY_DIRECTION + VOICE_DIRECTIONS[plan.delivery_id] + cues[reaction] + spoken
        return VoiceDelivery(text, plan.face_emotion, reaction,
            "fish_character_drama3_v1" if model == "drama-3" else "fish_character_s2_v1",
            override_soft=True, explicit_acting=plan.explicit_acting)

    def completed(self, delivery):
        if delivery.mechanism.startswith("fish_character_"):
            if delivery.reaction != "none" and not delivery.explicit_acting:
                self._last_character_at = self._successful
                self._last_character_reaction = delivery.reaction
            self._successful += 1
            return
        if delivery.reaction != "none":
            self._last_reaction = self._successful
        self._successful += 1
