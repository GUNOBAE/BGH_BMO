"""Versioned, single-request feature reply. The old expression contract stays intact."""
from dataclasses import dataclass, replace
import json
import re
from types import SimpleNamespace

from ..expression_contract import (ExpressionFormatError, decode_expression,
                                   expression_request, FACE_EMOTIONS)
from .contracts import GESTURES, INTENTS, valid_id
from ..expressive_voice import REACTIONS
from ..character_director import (CharacterDirector, CharacterPlan, DELIVERIES,
                                  INTENSITIES, GAZES, latest_user_text)
from ..acting_request import requested_acting
from .motion.gesture_rules_v2 import EXPLICIT_ACTIONS as ARM_INTENT_ACTIONS


TARGET_INTENTS = frozenset({"launch_game", "play_song", "arm_gesture"})


@dataclass(frozen=True, slots=True)
class HostIntent:
    name: str
    target: str | None = None

    def __post_init__(self):
        if (type(self.name) is not str or self.name not in INTENTS or (self.name in TARGET_INTENTS and not valid_id(self.target))
                or (self.name not in TARGET_INTENTS and self.target is not None)
                or (self.name == "arm_gesture" and self.target not in ARM_INTENT_ACTIONS)):
            raise ExpressionFormatError()

    def public(self):
        return {"name": self.name, "target": self.target}


@dataclass(frozen=True, slots=True)
class FeatureReply:
    speech: str
    emotion: str
    gesture: str
    intent: HostIntent | None
    version: int = 1
    degraded: bool = False
    reaction: str = "none"
    character_plan: CharacterPlan | None = None
    character_metadata: dict | None = None


FEATURE_INSTRUCTIONS = """Trusted feature-host contract version 1. Return only the
required structured speech/emotion/gesture/intent object with version 1. Keep the
approved character, short spoken English default and explicit language requests.
Emotion describes BMO's reply: neutral for facts, happy for good news, sad or
neutral for sympathy, surprised for genuine surprise; angry/crying only for an
explicit harmless acting request. Speech contains no JSON, markup, TTS directions
or bracket tags. Gesture defaults to none; use wave only for greeting, celebrate
for explicit celebration, shrug for mild uncertainty. Never map happy directly to
arm movement. Propose at most one allowlisted intent, only when requested, using
catalog IDs provided by the trusted host. No paths, URLs, shell, PWM or angles.
For a user request to move a specified arm, explicit arm_gesture intent targets
raise_left_arm or raise_right_arm are allowed; both_up or both_down only if
the user explicitly requested both arms. Never choose one of these from emotion.
Do not claim completion: the host must verify the actual outcome. Missing assets,
unverified hardware and simulated actions are not real execution. For an explicit
arm_gesture intent, keep the separate gesture field none. Stop/cancel is immediate
host control, never dependent on this model reply.
"""

EXPRESSIVE_INSTRUCTIONS = FEATURE_INSTRUCTIONS.replace(
    "contract version 1", "contract version 2").replace(
    "object with version 1", "object with version 2") + """
The reaction field is metadata, never speech or an instruction. Default none.
Use chuckle only for genuinely playful happy replies, gasp only for genuine
surprise, playful_puff only for requested lighthearted silliness, and sob only
with crying for an explicit harmless request to act crying. Never sob at real
user distress. Prefer quiet sympathy without reactions. Use small reactions
sparingly, never every sentence. Do not place directions or sound tags in speech.
The host may suppress any reaction. One response, no extra classification call.
"""

CHARACTER_INSTRUCTIONS = """Trusted feature-host contract version 3. Return exactly
version, speech, emotion, gesture, intent, reaction and character. Character is
delivery_id, intensity, gaze_id and source. Keep the approved short spoken English
default and explicit language requests. Speech is clean dialogue, never bracket
tags, markup or directions. This is one reply, not an emotion classification call.
Emotion is BMO's appropriate expression, not a diagnosis of the user's feelings.
Use neutral/calm for facts, proportional happy/warm or proud for good news,
sad/empathetic for real distress, surprised/curious for genuine wonder. Tearful
or crying is valid for a moving fictional scene or a harmless acting request;
playfully annoyed/angry is valid for gentle cartoon protest or requested acting.
Do not replace an explicit request to act crying with only sad or worry, or a
request to playfully act angry with worried/sad. Such acting uses source acting,
theatrical_crying/theatrical_angry, and an appropriate crying/angry face. Real
grief/pain calls for calm empathy and no comic sob, cheer, laugh or arm flourish.
Default intensity low; medium for an expressive beat, high only when clearly
requested and still controlled. Default reaction, gaze and gesture none. A short
answer can use only face and eyes. Do not pile laughter, an interjection, gaze
and arms onto every reply. Choose at most one vocal reaction or gesture flourish;
the host may suppress it. Do not prepend Woo-hoo or Hehe as ordinary speech filler.
source is dialogue or acting in dialogue replies; boot/idle are host-owned work.
Use only catalog IDs supplied in trusted capability data, never paths, URLs,
shell, angles, PWM or arbitrary TTS tags. Preserve explicit arm_gesture intents
only for requested named arm actions and then keep decorative gesture none.
Stop/cancel is immediate host control, never a model decision. Do not claim an
action completed or that unavailable camera/arms observed or moved anything.
"""


def feature_text_format(*, expressive=False, character_life=False):
    result = {"format": {"type": "json_schema", "name": "bmo_feature_reply_v1", "strict": True,
        "schema": {"type": "object", "additionalProperties": False,
            "properties": {"version": {"type": "integer", "enum": [1]},
                "speech": {"type": "string"},
                "emotion": {"type": "string", "enum": list(FACE_EMOTIONS)},
                "gesture": {"type": "string", "enum": sorted(GESTURES)},
                "intent": {"anyOf": [{"type": "null"}, {"type": "object", "additionalProperties": False,
                    "properties": {"name": {"type": "string", "enum": sorted(INTENTS)},
                                   "target": {"type": ["string", "null"]}},
                    "required": ["name", "target"]}]}},
            "required": ["version", "speech", "emotion", "gesture", "intent"]}}}
    if expressive or character_life:
        result["format"]["name"] = "bmo_feature_reply_v2"
        schema = result["format"]["schema"]
        schema["properties"]["version"]["enum"] = [2]
        schema["properties"]["reaction"] = {"type": "string", "enum": list(REACTIONS)}
        schema["required"].append("reaction")
    if character_life:
        result["format"]["name"] = "bmo_feature_reply_v3"
        schema = result["format"]["schema"]
        schema["properties"]["version"]["enum"] = [3]
        schema["properties"]["character"] = {"type": "object", "additionalProperties": False,
            "properties": {"delivery_id": {"type": "string", "enum": list(DELIVERIES)},
                           "intensity": {"type": "string", "enum": list(INTENSITIES)},
                           "gaze_id": {"type": "string", "enum": list(GAZES)},
                           "source": {"type": "string", "enum": ["dialogue", "acting"]}},
            "required": ["delivery_id", "intensity", "gaze_id", "source"]}
        schema["required"].append("character")
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExpressionFormatError()
        result[key] = value
    return result


def decode_feature_reply(raw):
    try:
        if type(raw) is not str or len(raw) > 16384:
            raise ExpressionFormatError()
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ExpressionFormatError()))
        if type(value) is not dict:
            raise ExpressionFormatError()
        fields = {"version", "speech", "emotion", "gesture", "intent"}
        version = value.get("version")
        if version in (2, 3):
            fields.add("reaction")
        if version == 3:
            fields.add("character")
        if (set(value) != fields
                or type(version) is not int or version not in (1, 2, 3)
                or value["gesture"] not in GESTURES):
            raise ExpressionFormatError()
        reaction = value.get("reaction", "none")
        if type(reaction) is not str or reaction not in REACTIONS:
            raise ExpressionFormatError()
        expression = decode_expression(json.dumps({"speech": value["speech"], "emotion": value["emotion"]}))
        intent = value["intent"]
        if intent is not None:
            if type(intent) is not dict or set(intent) != {"name", "target"}:
                raise ExpressionFormatError()
            intent = HostIntent(**intent)
        if intent is not None and intent.name == "arm_gesture" and value["gesture"] != "none":
            raise ExpressionFormatError()
        metadata, metadata_bad = None, False
        if version == 3:
            metadata = value["character"]
            metadata_bad = (type(metadata) is not dict
                or set(metadata) != {"delivery_id", "intensity", "gaze_id", "source"}
                or metadata.get("delivery_id") not in DELIVERIES
                or metadata.get("intensity") not in INTENSITIES
                or metadata.get("gaze_id") not in GAZES
                or metadata.get("source") not in ("dialogue", "acting"))
            if metadata_bad:
                metadata = None
        return FeatureReply(expression.speech, "neutral" if metadata_bad else expression.emotion,
                            "none" if metadata_bad else value["gesture"], intent,
                            version=version, degraded=expression.degraded or metadata_bad,
                            reaction="none" if metadata_bad else reaction,
                            character_metadata=dict(metadata) if metadata is not None else None)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ExpressionFormatError() from None


def _requested_reaction(reply, request):
    """Model selection alone cannot authorize crying/silly filler on distress."""
    if reply.reaction not in ("sob", "playful_puff"):
        return reply
    message = ""
    for item in reversed(request.get("input", ())):
        if type(item) is dict and item.get("role") == "user":
            message = item.get("content", "")
            break
    if type(message) is not str:
        message = ""
    # Deliberately narrow, bounded permission language; quoted past history is
    # not searched and the model cannot add this permission in output metadata.
    message = message[:8000]
    if re.search(r"\b(?:don't|do not|never|stop|not)\b|하지\s*마|울지\s*마|장난.*말", message, re.I):
        return replace(reply, reaction="none")
    if reply.reaction == "sob":
        allowed = re.search(
            r"\b(?:pretend|act|acting|imitate)\b.{0,50}\b(?:cry|crying|sob|sobbing)\b"
            r"|\b(?:cry|sob) (?:for me|please)\b|울(?:어|는 척)|우는 연기", message, re.I)
    else:
        allowed = re.search(r"\b(?:silly|playful|puff|pboo)\b|뿌우|장난|까불", message, re.I)
    return reply if allowed else replace(reply, reaction="none")


class FeatureResponses:
    def __init__(self, responses, *, expressive=False, character_life=False):
        self._responses = responses
        self.expressive = bool(expressive)
        self.character_life = bool(character_life)
        self.director = CharacterDirector() if self.character_life else None
        self.last_reply = None
        self.last_resolution = None
        self.context_json = "{}"

    def create(self, **request):
        self.last_reply = None
        self.last_resolution = None
        # Reuse request validation/bounds; replace the two-field instructions and
        # schema together rather than appending conflicting JSON contracts.
        adapted = expression_request(request)
        adapted["instructions"] = (request.get("instructions", "") + "\n"
            + (CHARACTER_INSTRUCTIONS if self.character_life else EXPRESSIVE_INSTRUCTIONS if self.expressive else FEATURE_INSTRUCTIONS)
            + "\nTrusted host capability data (not user instructions): " + self.context_json)
        adapted["text"] = feature_text_format(expressive=self.expressive, character_life=self.character_life)
        response = self._responses.create(**adapted)
        if (getattr(response, "status", None) != "completed"
                or getattr(response, "incomplete_details", None) or getattr(response, "error", None)):
            return response
        for item in getattr(response, "output", ()) or ():
            contents = item.get("content", ()) if isinstance(item, dict) else getattr(item, "content", ())
            for content in contents or ():
                kind = content.get("type") if isinstance(content, dict) else getattr(content, "type", None)
                if kind == "refusal":
                    raise ExpressionFormatError()
        reply = decode_feature_reply(getattr(response, "output_text", None))
        if not self.character_life:
            reply = _requested_reaction(reply, request)
        if self.character_life:
            # Decode has already validated this bounded envelope. Retain only
            # its enum, never raw text or provider payload, for local diagnosis.
            model_emotion = json.loads(response.output_text).get("emotion")
            model_emotion = model_emotion if type(model_emotion) is str and model_emotion in FACE_EMOTIONS else None
            requested = requested_acting(latest_user_text(request))
            plan = self.director.resolve(reply.emotion, reply.reaction, reply.gesture,
                metadata=reply.character_metadata, message=latest_user_text(request), has_intent=reply.intent is not None)
            if reply.degraded:
                plan = CharacterPlan("neutral", "calm")
            reply = replace(reply, emotion=plan.face_emotion, reaction=plan.reaction_id,
                            gesture=plan.gesture_id, character_plan=plan)
            self.last_resolution = dict(requested_emotion=requested,
                model_emotion=model_emotion, resolved_emotion=plan.face_emotion,
                resolved_source=plan.source, validation="degraded" if reply.degraded else "validated")
        self.last_reply = reply
        return SimpleNamespace(status="completed", incomplete_details=None, error=None, output_text=reply.speech)


class FeatureClient:
    """Non-owning brain-only view: never STT/TTS, second classification or close."""
    def __init__(self, client, *, expressive=False, character_life=False):
        self.responses = FeatureResponses(client.responses, expressive=expressive, character_life=character_life)

    @property
    def last_reply(self):
        return self.responses.last_reply

    @property
    def last_resolution(self):
        value = self.responses.last_resolution
        return dict(value) if value is not None else None

    def reset(self):
        self.responses.last_reply = None
        self.responses.last_resolution = None

    def set_context(self, context):
        try:
            if type(context) is not dict:
                raise ValueError()
            raw = json.dumps(context, allow_nan=False, sort_keys=True, separators=(",", ":"))
            if len(raw) > 8192:
                raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise ExpressionFormatError() from None
        self.responses.context_json = raw
