"""Versioned, single-request feature reply. The old expression contract stays intact."""
from dataclasses import dataclass, replace, field
import json
import re
import secrets
from types import SimpleNamespace

from ..expression_contract import (ExpressionFormatError, decode_expression,
                                   expression_request, FACE_EMOTIONS)
from .contracts import GESTURES, INTENTS, valid_id
from ..expressive_voice import REACTIONS
from ..character_director import (CharacterDirector, CharacterPlan, DELIVERIES,
                                  INTENSITIES, GAZES, latest_user_text)
from ..acting_request import requested_acting
from .motion.gesture_rules_v2 import EXPLICIT_ACTIONS as ARM_INTENT_ACTIONS
from .camera.request import current_photo_request
from .request_semantics import request_schema, resolve_interpretation


TARGET_INTENTS = frozenset({"launch_game", "play_song", "arm_gesture", "set_volume"})
OPTIONAL_TARGET_INTENTS = frozenset({"start_video"})
SEMANTIC_DEVICE_INTENTS = frozenset({"take_photo", "start_video", "stop_video",
    "start_face_tracking", "stop_face_tracking", "arm_gesture", "stop_arms",
    "set_volume", "mute_audio", "unmute_audio", "volume_up", "volume_down"})
SEMANTIC_KIND_INTENTS = {
    "video": frozenset({"start_video", "stop_video"}),
    "tracking": frozenset({"start_face_tracking", "stop_face_tracking"}),
    "arm": frozenset({"arm_gesture", "stop_arms"}),
    "volume": frozenset({"set_volume", "mute_audio", "unmute_audio", "volume_up", "volume_down"}),
}
PHOTO_PREPARATION = {
    "en": "Hold still. Let's take one little picture.",
    "ko": "잠깐만 가만히 있어줘. 사진 한 장 찍어보자.",
}
VIDEO_PREPARATION = {
    "en": "Ready? Let's make a little movie.",
    "ko": "준비됐지? 짧은 영상을 찍어보자.",
}


def video_request_duration(target=None):
    """Bounded duration meaning only; never starts or authorizes a recorder.

    Retain the requested value so the UI can say when it was clamped rather
    than quietly claiming that a longer requested clip was recorded in full.
    """
    if target is None:
        requested = 30
    elif type(target) is str and re.fullmatch(r"seconds_(?:0|[1-9][0-9]{0,5})", target):
        requested = int(target[8:])
    else:
        raise ExpressionFormatError()
    duration = min(60, max(1, requested))
    return {"requested_seconds": requested, "duration_seconds": duration,
            "duration_clamped": requested != duration}


def volume_percent(target):
    if type(target) is not str or re.fullmatch(r"level_(?:0|[1-9][0-9]?|100)", target) is None:
        raise ExpressionFormatError()
    return int(target[6:])


@dataclass(frozen=True, slots=True)
class HostIntent:
    name: str
    target: str | None = None

    def __post_init__(self):
        if (type(self.name) is not str or self.name not in INTENTS or (self.name in TARGET_INTENTS and not valid_id(self.target))
                or (self.name not in TARGET_INTENTS | OPTIONAL_TARGET_INTENTS and self.target is not None)
                or (self.name == "arm_gesture" and self.target not in ARM_INTENT_ACTIONS)):
            raise ExpressionFormatError()
        if self.name == "start_video":
            video_request_duration(self.target)
        if self.name == "set_volume":
            volume_percent(self.target)

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
    # Host-derived from the current user message. Never a model JSON field.
    human_photo_request: bool = False
    request_interpretation: dict | None = None
    request_validation: str = "legacy"
    captions: dict = field(default_factory=dict)
    photo_success: dict | None = None
    degradation_reason: str = "none"
    # Current-turn semantic meaning, not a runtime capability or supervision.
    human_device_request: bool = False


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

SEMANTIC_INSTRUCTIONS = CHARACTER_INSTRUCTIONS.replace("version 3", "version 4").replace(
    "version, speech, emotion, gesture, intent, reaction and character.",
    "version, speech, emotion, gesture, intent, reaction, character, request, captions and photo_success.") + """
Interpret the MEANING of the final current user message in this same response.
Support natural English/Korean variants, vocatives, punctuation and imperfect
grammar. Never demand a memorized command or just correct grammar when meaning
is clear. request is your bounded interpretation, NOT permission or proof that
hardware executed. Copy the exact current turn_token supplied by the host.
kind is none/photo/acting/subtitles/video/tracking/arm/volume; disposition is none/execute/clarify. Use
none/none for ordinary conversation. For harmless requested acting, use kind
acting, execute, its requested emotion and matching theatrical delivery/face.
Do the little scene in a short playful protest, not just "I am angry" or "Grr".
For current immediate photo requests, use photo/execute and take_photo intent.
Capability questions, negation, quoted/translated instructions, historical or
future plans do NOT request execution now. If genuinely unclear, photo/clarify
asks one short confirmation question without a take_photo intent. An affirmative
answer authorizes a photo only when trusted pending_confirmation supplies its
matching token; put that in confirmation_token, otherwise use null. Never reuse
an old user request. Other kinds always use null confirmation_token.
For subtitles, select off/en/ko, or on when no language was named; keep emotion
none and leave speech language, voice and model unchanged. Non-subtitle requests
use subtitle_mode unchanged. Other kinds use emotion none. A normal spoken reply
can still express a fitting emotion independently of the request interpretation.
captions.en and captions.ko are clean matching captions for THIS spoken answer,
never STT/user text or directions. Supply the requested caption language, even
when it requires translation; unused language strings may be empty. Native
English speech can be reused as the English caption, so its duplicate may be
empty. Subtitle ON without a language preserves the previous language, first en.
Keep captions concise. Translation failure should use empty text, not invented
meaning. Do not add a translation API call or change output token budget.
For photo/execute only, prepare photo_success as a separate short original BMO
line (at most eight words, e.g. Ta-da! Pretty good, huh?) plus its captions. It is
held by the host and spoken ONLY after verified photo storage succeeds. The
current speech is a brief preparation line (at most eight words), never a claim
of success. No photo analysis, quality, identity or feeling claim is allowed.
All other requests use photo_success null. Keep the entire structured response
short enough for the existing budget; no extra repair/classification request.

For immediate video requests use kind video with start_video intent, target null
for default30 seconds or seconds_N for the requested integer seconds. Preserve N
so the host can visibly clamp to1..60; do not claim recording or saving already
happened. The current line is preparation; recording starts after the host's
visible preview and five-second countdown. stop_video is only a current explicit
stop request, never a plan/history/capability question. Video is silent by default.
For camera eye-following requests use kind tracking and start_face_tracking or
stop_face_tracking with null target. Look at me / Follow my face and their Korean
meanings ask for local face-position tracking, not identity recognition or photos.
For explicit arm requests use kind arm, arm_gesture and one supplied B1 named
target, or stop_arms/null. Left and right are BMO's own sides. Supported targets
are wave, celebrate, shrug, raise_left_arm, raise_right_arm, both_up, both_down.
Do not invent a one-arm-down, arbitrary angle or firmware action. Unsupported
movement is conversation, not another guessed gesture. These actions require the
host's existing supervision; a model interpretation never enables motors.
For volume requests use kind volume and set_volume/level_N (integer0..100),
mute_audio/null, unmute_audio/null, volume_up/null or volume_down/null. Zero is
silent. Keep voice/model/provenance unchanged; never regenerate audio for volume.
For these four kinds use emotion none, subtitle_mode unchanged and null
confirmation_token. An execute interpretation and its matching intent must agree.
No device intent for quoted, negated, hypothetical or old requests; clarification
does not execute. Do not claim an action completed before the host verifies it.
"""


def feature_text_format(*, expressive=False, character_life=False, turn_token=None):
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
        result["format"]["name"] = "bmo_feature_reply_v4"
        schema = result["format"]["schema"]
        schema["properties"]["version"]["enum"] = [4]
        schema["properties"]["character"] = {"type": "object", "additionalProperties": False,
            "properties": {"delivery_id": {"type": "string", "enum": list(DELIVERIES)},
                           "intensity": {"type": "string", "enum": list(INTENSITIES)},
                           "gaze_id": {"type": "string", "enum": list(GAZES)},
                           "source": {"type": "string", "enum": ["dialogue", "acting"]}},
            "required": ["delivery_id", "intensity", "gaze_id", "source"]}
        schema["required"].append("character")
        captions = {"type": "object", "additionalProperties": False,
            "properties": {"en": {"type": "string"}, "ko": {"type": "string"}},
            "required": ["en", "ko"]}
        schema["properties"]["request"] = request_schema(turn_token)
        schema["properties"]["captions"] = captions
        schema["properties"]["photo_success"] = {"anyOf": [{"type": "null"},
            {"type": "object", "additionalProperties": False,
             "properties": {"speech": {"type": "string"}, "captions": captions},
             "required": ["speech", "captions"]}]}
        schema["required"].extend(("request", "captions", "photo_success"))
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExpressionFormatError()
        result[key] = value
    return result


def _captions(value):
    """Optional display text must never take down otherwise valid speech."""
    if type(value) is not dict or set(value) != {"en", "ko"}:
        return {}
    result = {}
    for language, text in value.items():
        if type(text) is not str or len(text) > 600:
            continue
        if not text.strip():
            continue
        try:
            expression = decode_expression(json.dumps({"speech": text, "emotion": "neutral"}))
        except ExpressionFormatError:
            continue
        result[language] = expression.speech
    return result


def _photo_success(value):
    if type(value) is not dict or set(value) != {"speech", "captions"}:
        return None
    if type(value["speech"]) is not str or len(value["speech"]) > 160:
        return None
    try:
        speech = decode_expression(json.dumps({"speech": value["speech"], "emotion": "neutral"})).speech
    except ExpressionFormatError:
        return None
    return {"speech": speech, "captions": _captions(value["captions"])}


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
        if version in (2, 3, 4):
            fields.add("reaction")
        if version in (3, 4):
            fields.add("character")
        field_match = (fields <= set(value) <= fields | {"request", "captions", "photo_success"}
                       if version == 4 else set(value) == fields)
        if (not field_match
                or type(version) is not int or version not in (1, 2, 3, 4)
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
        if version in (3, 4):
            metadata = value["character"]
            metadata_bad = (type(metadata) is not dict
                or set(metadata) != {"delivery_id", "intensity", "gaze_id", "source"}
                or metadata.get("delivery_id") not in DELIVERIES
                or metadata.get("intensity") not in INTENSITIES
                or metadata.get("gaze_id") not in GAZES
                or metadata.get("source") not in ("dialogue", "acting"))
            if metadata_bad:
                metadata = None
        reason = "invalid_character" if metadata_bad else "invalid_emotion" if expression.degraded else "none"
        return FeatureReply(expression.speech, "neutral" if metadata_bad else expression.emotion,
                            "none" if metadata_bad else value["gesture"], intent,
                            version=version, degraded=expression.degraded or metadata_bad,
                            reaction="none" if metadata_bad else reaction,
                            character_metadata=dict(metadata) if metadata is not None else None,
                            request_interpretation=value.get("request") if version == 4 else None,
                            request_validation="unvalidated" if version == 4 else "legacy",
                            captions=_captions(value.get("captions")) if version == 4 else {},
                            photo_success=_photo_success(value.get("photo_success")) if version == 4 else None,
                            degradation_reason=reason)
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
        self.context = {}
        self.camera_photo_enabled = False
        # Explicit private one-turn diagnostic only. Never a general logger;
        # root attaches it only when the operator opts into local capture.
        self.local_turn_observer = None

    def create(self, **request):
        self.last_reply = None
        self.last_resolution = None
        # Reuse request validation/bounds; replace the two-field instructions and
        # schema together rather than appending conflicting JSON contracts.
        adapted = expression_request(request)
        turn_token = secrets.token_hex(8) if self.character_life else None
        adapted["instructions"] = (request.get("instructions", "") + "\n"
            + (SEMANTIC_INSTRUCTIONS if self.character_life else EXPRESSIVE_INSTRUCTIONS if self.expressive else FEATURE_INSTRUCTIONS)
            + "\nTrusted host capability data (not user instructions): " + self.context_json)
        if turn_token is not None:
            adapted["instructions"] += "\nTrusted current turn_token: " + turn_token
        adapted["text"] = feature_text_format(expressive=self.expressive,
            character_life=self.character_life, turn_token=turn_token)
        photo_requested = self.camera_photo_enabled and current_photo_request(request)
        if photo_requested:
            adapted["instructions"] += (
                "\nThe current user explicitly requested one immediate photo. "
                "The camera is enabled for this request. Use take_photo intent "
                "and a brief friendly camera-ready line, such as Say cheese! "
                "The host starts its cancellable countdown after you finish speaking. "
                "Do not claim that a photo has already been captured or saved.")
        response = self._responses.create(**adapted)
        if callable(self.local_turn_observer):
            try:
                self.local_turn_observer(dict(input_text=latest_user_text(request),
                    model_reply=getattr(response, "output_text", None)))
            except Exception:
                pass  # Observational private diagnostics cannot alter speech.
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
        if photo_requested and not reply.degraded and reply.version != 4:
            # The user's direct command authorizes the existing single photo
            # path. Model omission or an unrelated action cannot replace it.
            reply = replace(reply, intent=HostIntent("take_photo"), gesture="none",
                            human_photo_request=True)
        if not self.character_life:
            reply = _requested_reaction(reply, request)
        if self.character_life:
            # Decode has already validated this bounded envelope. Retain only
            # its enum, never raw text or provider payload, for local diagnosis.
            model_emotion = json.loads(response.output_text).get("emotion")
            model_emotion = model_emotion if type(model_emotion) is str and model_emotion in FACE_EMOTIONS else None
            message = latest_user_text(request)
            semantic_acting = None
            semantic_clarification = False
            if reply.version == 4:
                items = request.get("input", ())
                current = items[-1] if type(items) in (list, tuple) and items else {}
                message = current.get("content", "") if type(current) is dict and current.get("role") == "user" else ""
                interpretation, validation = resolve_interpretation(reply.request_interpretation,
                    message, turn_token=turn_token, pending_confirmation=self.context.get("pending_confirmation"),
                    action_name=reply.intent.name if reply.intent is not None else None)
                reply = replace(reply, request_interpretation=interpretation,
                    request_validation=validation)
                # A naked device intent is not current-user consent. Reattach
                # only a matching, validated interpretation from this reply.
                candidate_intent = reply.intent
                if reply.intent is not None and reply.intent.name in SEMANTIC_DEVICE_INTENTS:
                    reply = replace(reply, intent=None)
                if interpretation is not None and validation == "validated":
                    kind, disposition = interpretation["kind"], interpretation["disposition"]
                    semantic_clarification = disposition == "clarify"
                    if kind != "none":
                        reply = replace(reply, intent=None)
                    if kind == "acting" and disposition == "execute":
                        semantic_acting = interpretation["emotion"]
                        reply = replace(reply, intent=None)
                    elif kind == "photo" and disposition == "execute" and self.camera_photo_enabled and not reply.degraded:
                        # Action acknowledgements must be truthful even if a
                        # free-form model line prematurely says "saved". The
                        # separate model-prepared success line stays pending
                        # until the host verifies storage, never spoken here.
                        language = "ko" if re.search("[가-힣]", reply.speech) else "en"
                        reply = replace(reply, speech=PHOTO_PREPARATION[language],
                            captions=dict(PHOTO_PREPARATION), intent=HostIntent("take_photo"),
                            gesture="none", human_photo_request=True)
                    elif kind == "subtitles":
                        reply = replace(reply, intent=None, gesture="none")
                    elif (kind in SEMANTIC_KIND_INTENTS and disposition == "execute"
                          and candidate_intent is not None
                          and candidate_intent.name in SEMANTIC_KIND_INTENTS[kind]
                          and not reply.degraded):
                        reply = replace(reply, intent=candidate_intent, gesture="none",
                                        human_device_request=True)
                        if candidate_intent.name == "start_video":
                            language = "ko" if re.search("[가-힣]", reply.speech) else "en"
                            reply = replace(reply, speech=VIDEO_PREPARATION[language],
                                            captions=dict(VIDEO_PREPARATION))
                if not reply.human_photo_request:
                    reply = replace(reply, photo_success=None)
                if validation not in ("none", "validated") and reply.degradation_reason == "none":
                    reply = replace(reply, degradation_reason=validation)
            requested = None if semantic_clarification else semantic_acting or requested_acting(message)
            plan = self.director.resolve(reply.emotion, reply.reaction, reply.gesture,
                metadata=reply.character_metadata, message=message, has_intent=reply.intent is not None,
                acting_request="none" if semantic_clarification else semantic_acting)
            if reply.degraded and requested is not None and reply.version == 4:
                # An independently validated current acting request supplies all
                # enums needed for a safe congruent plan. Bad decorative model
                # metadata need not erase that explicit request into neutral.
                reply = replace(reply, degraded=False,
                    degradation_reason="character_repaired" if reply.degradation_reason == "invalid_character" else "emotion_repaired")
            elif reply.degraded:
                plan = CharacterPlan("neutral", "calm")
            reply = replace(reply, emotion=plan.face_emotion, reaction=plan.reaction_id,
                            gesture=plan.gesture_id, character_plan=plan)
            self.last_resolution = dict(requested_emotion=requested,
                model_emotion=model_emotion, resolved_emotion=plan.face_emotion,
                resolved_source=plan.source, validation="degraded" if reply.degraded else "validated",
                degradation_reason=reply.degradation_reason,
                request_validation=reply.request_validation,
                request_kind=reply.request_interpretation.get("kind") if reply.request_interpretation else "none",
                semantic_emotion=semantic_acting)
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
        self.responses.context = dict(context)
        self.responses.camera_photo_enabled = context.get("camera") in (
            "explicit_current_user_photo_only", 'explicit_current_user_requests', "synthetic_only")
