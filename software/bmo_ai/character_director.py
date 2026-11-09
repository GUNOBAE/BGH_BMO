"""Allowlisted performance intent for the existing one-call dialogue path.

This module resolves metadata, not a second model or a diagnosis of the user.
It has no clocks, device access, text logging or TTS/network side effects.
"""
from dataclasses import dataclass
import re

from .expression_contract import FACE_EMOTIONS, ExpressionFormatError
from .acting_request import requested_acting


DELIVERIES = ("calm", "warm", "excited", "curious", "proud", "playfully_annoyed",
              "soft_sad", "tearful", "theatrical_crying", "theatrical_angry", "empathetic",
              "playful", "gentle_correction")
INTENSITIES = ("low", "medium", "high")
GAZES = ("none", "attentive", "curious", "soft_down", "celebratory")
SOURCES = ("dialogue", "acting", "boot", "idle")
REACTION_IDS = ("none", "chuckle", "gasp", "playful_puff", "sob",
                "hmm", "ooh", "aha", "tada", "oops")
GESTURE_IDS = ("none", "wave", "celebrate", "shrug")
GESTURE_CONTEXTS = ("none", "greeting", "achievement", "playful", "hesitation", "surprise")
GESTURE_REASONS = ("not_requested", "eligible", "sensitive_context", "action_request",
                   "acting", "context_mismatch", "emotion_mismatch", "vocal_flourish")
REACTION_REASONS = ("not_requested", "eligible", "sensitive_context", "action_request",
                    "acting_required", "emotion_mismatch", "success_event_only")
_CONGRUENT = {
    "calm": ("neutral",), "warm": ("happy",), "excited": ("happy",),
    "curious": ("neutral", "surprised"), "proud": ("happy",),
    "playfully_annoyed": ("angry",), "soft_sad": ("sad",),
    "tearful": ("crying",), "theatrical_crying": ("crying",),
    "theatrical_angry": ("angry",), "empathetic": ("neutral", "sad"),
    "playful": ("neutral", "happy"), "gentle_correction": ("neutral",),
}
_DEFAULT = {"neutral": "calm", "happy": "warm", "sad": "soft_sad",
            "surprised": "curious", "angry": "playfully_annoyed", "crying": "tearful"}

# Host-authored cues only. Fish's official S2/Drama3 documentation supports
# bracket delivery/sound descriptions; these requests still need A/B listening.
# https://docs.fish.audio/developer-guide/core-features/emotions
# https://docs.fish.audio/features/text-to-speech/drama-3
VOICE_DIRECTIONS = {
    "calm": "[calm, clear conversational delivery, natural phrasing and gentle emphasis, not flat] ",
    "warm": "[warm, gently happy, small smile] ",
    "excited": "[delighted, brief restrained excitement, no shouting] ",
    "curious": "[curious, attentive, gentle wonder] ",
    "proud": "[proud, pleased, quiet confidence] ",
    "playfully_annoyed": "[playfully angry, small cartoon protest, not hostile or shouting] ",
    "soft_sad": "[sad, quiet and gentle] ",
    "tearful": "[moved, voice catching with restrained tears] ",
    "theatrical_crying": "[sobbing, voice catching, controlled theatrical tears, no prolonged wailing] ",
    "theatrical_angry": "[angry, playful theatrical protest, clearly acted, never threatening or shouting] ",
    "empathetic": "[empathetic, calm, attentive, no comic reaction] ",
    "playful": "[lightly playful, a small smile and gentle rhythmic contrast, no forced laugh] ",
    "gentle_correction": "[gentle, matter-of-fact correction, a brief thoughtful pause, never scolding] ",
}
IDENTITY_DIRECTION = "[Keep the same familiar Aaron character voice and clear conversational pronunciation.] "


def latest_user_text(request):
    for item in reversed(request.get("input", ())):
        if type(item) is dict and item.get("role") == "user":
            value = item.get("content")
            return value[:8000] if type(value) is str else ""
    return ""


def sensitive_context(message):
    """A conservative suppression guard, never a sentiment label or diagnosis."""
    if type(message) is not str:
        return False
    return bool(re.search(
        r"\b(?:my (?:mom|mum|dad|mother|father|friend|partner|child) (?:died|passed away)|"
        r"i(?:'m| am) (?:grieving|depressed|scared|in pain)|i lost my|i want to die|"
        r"i (?:feel|am) (?:suicidal|hopeless))\b|"
        r"(?:돌아가셨|돌아가셨어|세상을\s*떠|죽고\s*싶|너무\s*힘들|괴로워|우울해서|상실감)",
        message[:8000], re.I))


@dataclass(frozen=True, slots=True)
class CharacterPlan:
    face_emotion: str
    delivery_id: str
    intensity: str = "low"
    reaction_id: str = "none"
    gaze_id: str = "none"
    gesture_id: str = "none"
    source: str = "dialogue"
    explicit_acting: bool = False
    sensitive_context: bool = False
    gesture_context: str = "none"

    def __post_init__(self):
        if (self.face_emotion not in FACE_EMOTIONS or self.delivery_id not in DELIVERIES
                or self.intensity not in INTENSITIES or self.reaction_id not in REACTION_IDS
                or self.gaze_id not in GAZES or self.gesture_id not in GESTURE_IDS
                or self.gesture_context not in GESTURE_CONTEXTS
                or self.source not in SOURCES or type(self.explicit_acting) is not bool
                or type(self.sensitive_context) is not bool
                or self.face_emotion not in _CONGRUENT[self.delivery_id]):
            raise ExpressionFormatError()

    def public(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_public(cls, value):
        if type(value) is not dict or set(value) not in (
                set(cls.__dataclass_fields__), set(cls.__dataclass_fields__) - {"gesture_context"}):
            raise ExpressionFormatError()
        try:
            return cls(**value)
        except (TypeError, ValueError):
            raise ExpressionFormatError() from None


class CharacterDirector:
    """Resolve one congruent, restrained performance from existing reply metadata."""
    def resolve(self, emotion, reaction="none", gesture="none", *, metadata=None,
                message="", has_intent=False, acting_request=None):
        if emotion not in FACE_EMOTIONS or reaction not in REACTION_IDS or gesture not in GESTURE_IDS:
            raise ExpressionFormatError()
        value = metadata if type(metadata) is dict else {}
        # V4 supplies a validated interpretation from the same dialogue call.
        # The local literal matcher remains a fast path, never a miss veto.
        if acting_request not in (None, "none", "happy", "sad", "angry", "crying"):
            raise ExpressionFormatError()
        acting = None if acting_request == "none" else acting_request or requested_acting(message)
        sensitive = sensitive_context(message)
        source = value.get("source", "dialogue")
        source = source if source in SOURCES else "dialogue"
        # Dialogue providers cannot schedule boot or idle work through a reply.
        if source in ("boot", "idle"):
            source = "dialogue"
        intensity = value.get("intensity", "low")
        intensity = intensity if intensity in INTENSITIES else "low"
        delivery = value.get("delivery_id", _DEFAULT[emotion])
        gaze = value.get("gaze_id", "none")
        gaze = gaze if gaze in GAZES else "none"
        gesture_context = value.get("gesture_context", "none")
        gesture_context = gesture_context if gesture_context in GESTURE_CONTEXTS else "none"
        if sensitive:
            emotion = "sad" if emotion != "neutral" else "neutral"
            return CharacterPlan(emotion, "empathetic", "low", source="dialogue",
                                 sensitive_context=True, gaze_id="attentive")
        if acting in ("crying", "angry", "happy", "sad"):
            emotion, source = acting, "acting"
            delivery = {"crying": "theatrical_crying", "angry": "theatrical_angry",
                        "happy": "excited", "sad": "soft_sad"}[acting]
            intensity = "medium" if intensity != "low" else "low"
            gesture, gaze = "none", "soft_down" if acting in ("crying", "sad") else "attentive"
        elif source == "acting":
            # Only the current user's explicit request grants theatrical output.
            # An untrusted source label does not erase otherwise restrained
            # natural happiness/sympathy/surprise. Anger/tears remain subject to
            # explicit acting permission; the label alone cannot grant it.
            emotion = "neutral" if emotion in ("angry", "crying") else emotion
            delivery, source, intensity = _DEFAULT[emotion], "dialogue", "low"
            reaction, gesture, gaze = "none", "none", "none"
        if delivery not in DELIVERIES or emotion not in _CONGRUENT[delivery]:
            delivery = _DEFAULT[emotion]
        congruent_reaction = {"chuckle": ("happy",), "gasp": ("surprised",),
                              "playful_puff": ("happy", "angry"), "sob": ("crying",),
                              "hmm": ("neutral", "surprised"), "ooh": ("neutral", "surprised", "happy"),
                              "aha": ("neutral", "happy"), "tada": ("happy",), "oops": ("neutral", "sad",)}
        if (reaction != "none" and emotion not in congruent_reaction[reaction]
                or reaction == "sob" and acting != "crying"
                or reaction == "playful_puff" and acting != "angry"
                # The local Ta-da asset claims an action succeeded. Only the
                # Host's actual-success event may play it, never model metadata.
                or reaction == "tada"):
            reaction = "none"
        if emotion in ("sad", "crying", "angry") or has_intent:
            gesture = "none"
        # Context comes from the existing semantic reply, not an extra classifier
        # or a happy-face-to-arm rule. Legacy replies with no context keep their
        # speech and expression but cannot grant an automatic body flourish.
        allowed_context = {"wave": ("greeting",), "celebrate": ("achievement",),
                           "shrug": ("playful", "hesitation", "surprise")}
        if (gesture != "none" and (gesture_context not in allowed_context[gesture]
                or gesture == "celebrate" and emotion != "happy"
                or gesture_context == "surprise" and emotion != "surprised")):
            gesture = "none"
        # Surprise is one coordinated beat: the approved surprised face, one
        # optional gasp and the fixed surprise arm profile. A surprise face or
        # gasp alone cannot grant movement. The Host owns actual onset/return,
        # duration limits and STOP priority; metadata never contains angles.
        paired_surprise = (reaction == "gasp" and gesture == "shrug"
            and gesture_context == "surprise" and emotion == "surprised")
        if reaction != "none":
            if reaction not in ("hmm", "aha") and not paired_surprise:
                gesture = "none"
            gaze = "attentive"
        if has_intent:
            reaction, gaze = "none", "none"
        return CharacterPlan(emotion, delivery, intensity, reaction, gaze, gesture,
                             source, explicit_acting=bool(acting), gesture_context=gesture_context)


def performance_resolution(plan, *, reaction, gesture, has_intent=False):
    """Bounded model-to-local diagnostics without transcripts or provider text."""
    if reaction == "none":
        reaction_reason = "not_requested"
    elif plan.reaction_id != "none":
        reaction_reason = "eligible"
    elif plan.sensitive_context:
        reaction_reason = "sensitive_context"
    elif has_intent:
        reaction_reason = "action_request"
    elif reaction == "tada":
        reaction_reason = "success_event_only"
    elif reaction in ("sob", "playful_puff") and not plan.explicit_acting:
        reaction_reason = "acting_required"
    else:
        reaction_reason = "emotion_mismatch"
    if gesture == "none":
        gesture_reason = "not_requested"
    elif plan.gesture_id != "none":
        gesture_reason = "eligible"
    elif plan.sensitive_context:
        gesture_reason = "sensitive_context"
    elif has_intent:
        gesture_reason = "action_request"
    elif plan.explicit_acting:
        gesture_reason = "acting"
    elif (plan.face_emotion in ("sad", "crying", "angry")
            or plan.gesture_context == "surprise" and plan.face_emotion != "surprised"):
        gesture_reason = "emotion_mismatch"
    elif plan.reaction_id not in ("none", "hmm", "aha"):
        gesture_reason = "vocal_flourish"
    else:
        gesture_reason = "context_mismatch"
    return dict(model_reaction=reaction, resolved_reaction=plan.reaction_id,
                reaction_reason=reaction_reason, model_gesture=gesture,
                resolved_gesture=plan.gesture_id, gesture_context=plan.gesture_context,
                gesture_reason=gesture_reason)
