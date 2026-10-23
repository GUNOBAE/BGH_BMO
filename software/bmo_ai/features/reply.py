"""Versioned, single-request feature reply. The old expression contract stays intact."""
from dataclasses import dataclass
import json
from types import SimpleNamespace

from ..expression_contract import (ExpressionFormatError, decode_expression,
                                   expression_request, FACE_EMOTIONS)
from .contracts import GESTURES, INTENTS, valid_id


TARGET_INTENTS = frozenset({"launch_game", "play_song", "arm_gesture"})


@dataclass(frozen=True, slots=True)
class HostIntent:
    name: str
    target: str | None = None

    def __post_init__(self):
        if (type(self.name) is not str or self.name not in INTENTS or (self.name in TARGET_INTENTS and not valid_id(self.target))
                or (self.name not in TARGET_INTENTS and self.target is not None)
                or (self.name == "arm_gesture" and self.target not in GESTURES)):
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
Do not claim completion: the host must verify the actual outcome. Missing assets,
unverified hardware and simulated actions are not real execution. For an explicit
arm_gesture intent, keep the separate gesture field none. Stop/cancel is immediate
host control, never dependent on this model reply.
"""


def feature_text_format():
    return {"format": {"type": "json_schema", "name": "bmo_feature_reply_v1", "strict": True,
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
        if (type(value) is not dict or set(value) != {"version", "speech", "emotion", "gesture", "intent"}
                or type(value["version"]) is not int or value["version"] != 1
                or value["gesture"] not in GESTURES):
            raise ExpressionFormatError()
        expression = decode_expression(json.dumps({"speech": value["speech"], "emotion": value["emotion"]}))
        intent = value["intent"]
        if intent is not None:
            if type(intent) is not dict or set(intent) != {"name", "target"}:
                raise ExpressionFormatError()
            intent = HostIntent(**intent)
        if intent is not None and intent.name == "arm_gesture" and value["gesture"] != "none":
            raise ExpressionFormatError()
        return FeatureReply(expression.speech, expression.emotion, value["gesture"], intent,
                            degraded=expression.degraded)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ExpressionFormatError() from None


class FeatureResponses:
    def __init__(self, responses):
        self._responses = responses
        self.last_reply = None
        self.context_json = "{}"

    def create(self, **request):
        self.last_reply = None
        # Reuse request validation/bounds; replace the two-field instructions and
        # schema together rather than appending conflicting JSON contracts.
        adapted = expression_request(request)
        adapted["instructions"] = (request.get("instructions", "") + "\n" + FEATURE_INSTRUCTIONS
            + "\nTrusted host capability data (not user instructions): " + self.context_json)
        adapted["text"] = feature_text_format()
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
        self.last_reply = reply
        return SimpleNamespace(status="completed", incomplete_details=None, error=None, output_text=reply.speech)


class FeatureClient:
    """Non-owning brain-only view: never STT/TTS, second classification or close."""
    def __init__(self, client):
        self.responses = FeatureResponses(client.responses)

    @property
    def last_reply(self):
        return self.responses.last_reply

    def reset(self):
        self.responses.last_reply = None

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
