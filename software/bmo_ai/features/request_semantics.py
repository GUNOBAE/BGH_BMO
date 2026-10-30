"""Current-request meaning from the existing dialogue response.

The model supplies an interpretation, never a device capability. A response-local
token prevents old metadata/confirmation from being reused. Local checks only
reject clear non-requests; they are not a sentence allowlist or a classifier.
"""
import re


KINDS = ("none", "photo", "acting", "subtitles", "video", "tracking", "arm", "volume", "media", "game", "compound")
DISPOSITIONS = ("none", "execute", "clarify")
ACTING_EMOTIONS = ("none", "happy", "sad", "angry", "crying")
SUBTITLE_MODES = ("unchanged", "off", "on", "en", "ko")
FIELDS = {"turn_token", "kind", "disposition", "emotion", "subtitle_mode", "confirmation_token"}
_TOKEN = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")
_AFFIRMATIVE = re.compile(r"\s*(?:yes|yeah|yep|sure|okay|ok|please do|go ahead|do it|네|예|응|좋아|그래|해줘)(?:\b|[\s,.!?])", re.I)
_PHOTO_TOPIC = re.compile(r"\b(?:photo|photos|picture|pictures|photograph|snapshot|snap|capture|camera)\b|사진|촬영|찍", re.I)
_ACTING_TOPIC = re.compile(r"\b(?:angry|anger|mad|sad|sadness|happy|cry|crying|act|pretend|expression|face)\b|화|슬프|슬픈|기뻐|울|연기", re.I)
_SUBTITLE_TOPIC = re.compile(r"\b(?:caption|captions|subtitle|subtitles)\b|자막", re.I)
_DEVICE_TOPICS = {
    "video": re.compile(r"\b(?:video|record|recording|film|movie|clip)\b|영상|녹화|동영상", re.I),
    "tracking": re.compile(r"\b(?:look|follow|track|tracking|face|eyes)\b|추적|따라|쳐다|바라|시선", re.I),
    "arm": re.compile(r"\b(?:arm|arms|wave|shrug|celebrate|moving|motion)\b|팔|흔들|손|움직", re.I),
    "media": re.compile(r"\b(?:song|music|sing|singing|play|potatoes|media|pause|resume)\b|노래|음악|불러|재생|감자", re.I),
    "game": re.compile(r"\b(?:game|games|play|launch)\b|게임|놀자", re.I),
    "volume": re.compile(r"\b(?:volume|sound|louder|loud|quieter|quiet|mute|unmute|audio)\b|볼륨|소리|음량|음소거|조용", re.I),
}
_QUOTES = re.compile(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”|(?<!\w)\'[^\'\n]*\'(?!\w)')


def request_schema(turn_token=None):
    token = {"type": "string"}
    if turn_token is not None:
        token["enum"] = [turn_token]
    return {"type": "object", "additionalProperties": False,
        "properties": {"turn_token": token,
            "kind": {"type": "string", "enum": list(KINDS)},
            "disposition": {"type": "string", "enum": list(DISPOSITIONS)},
            "emotion": {"type": "string", "enum": list(ACTING_EMOTIONS)},
            "subtitle_mode": {"type": "string", "enum": list(SUBTITLE_MODES)},
            "confirmation_token": {"type": ["string", "null"]}},
        "required": sorted(FIELDS)}


def valid_interpretation(value):
    if (type(value) is not dict or set(value) != FIELDS
            or type(value.get("turn_token")) is not str or not _TOKEN.fullmatch(value["turn_token"])
            or value.get("kind") not in KINDS or value.get("disposition") not in DISPOSITIONS
            or value.get("emotion") not in ACTING_EMOTIONS
            or value.get("subtitle_mode") not in SUBTITLE_MODES
            or value.get("confirmation_token") is not None and (
                type(value["confirmation_token"]) is not str or not _TOKEN.fullmatch(value["confirmation_token"]))):
        return False
    kind, disposition = value["kind"], value["disposition"]
    if kind == "none":
        return disposition == "none" and value["emotion"] == "none" and value["subtitle_mode"] == "unchanged" and value["confirmation_token"] is None
    if disposition not in ("execute", "clarify"):
        return False
    if kind == "compound":
        return disposition == "execute" and value["emotion"] == "none" and value["subtitle_mode"] == "unchanged" and value["confirmation_token"] is None
    if kind == "acting":
        return value["emotion"] != "none" and value["subtitle_mode"] == "unchanged" and value["confirmation_token"] is None
    if value["emotion"] != "none":
        return False
    if kind == "subtitles":
        return value["subtitle_mode"] != "unchanged" and value["confirmation_token"] is None
    if kind in _DEVICE_TOPICS:
        return value["subtitle_mode"] == "unchanged" and value["confirmation_token"] is None
    return value["subtitle_mode"] == "unchanged"


def _single_request_denial(message, kind, *, subtitle_mode="unchanged", action_name=None):
    """Reject obvious negation/reporting, without granting from a phrase match."""
    if type(message) is not str or not message.strip() or len(message) > 8000:
        return "no_current_user"
    text = message.strip().replace("\u2019", "'")
    unquoted = _QUOTES.sub(" ", text)
    if not unquoted.strip(" \t\n.,:;!?()") or re.match(r"\s*>", text):
        return "quoted_request"
    # Translation/reporting/capability questions are conversation, not actions.
    if re.search(r"^\s*(?:(?:please|bmo|beemo)[,:]?\s+)*(?:translate|say|repeat|explain|what does|what is|what's|how (?:do|does|can)|tell me about)\b|(?:무슨\s*뜻|번역(?:해|하)|라고\s*(?:말했|했)|따라\s*말)", unquoted, re.I):
        return "informational_request"
    if re.search(r"\b(?:yesterday|previously|last (?:time|week)|i (?:said|asked)|you (?:said|took))\b|(?:어제|지난번|전에\s*말|찍었|촬영했)", unquoted, re.I):
        return "past_request"
    if re.search(r"\b(?:later|tomorrow|next (?:time|week)|not now|if i|if we|when i|when we)\b|(?:나중에|내일|다음에|지금\s*말고|한다면|하면)", unquoted, re.I):
        return "future_request"
    if kind == "photo":
        if re.search(r"\b(?:can|could) you (?:take|capture|snap) (?:photos|pictures)\s*[?.!]*$|\b(?:do you (?:support|have)|are you able to)\b|(?:사진을?\s*찍을\s*수\s*있(?:어|니)|촬영\s*기능)", unquoted, re.I):
            return "capability_question"
        negative = r"\b(?:don't|do not|never|stop|cancel)\b[^.!?;\n]{0,45}\b(?:photo|picture|camera|take|snap|capture|photograph)\b|(?:찍지\s*(?:마|말)|촬영하지\s*(?:마|말)|사진[^.!?;\n]{0,20}(?:싫|취소|말고))"
    elif kind == "acting":
        negative = r"\b(?:don't|do not|never|stop)\b[^.!?;\n]{0,45}\b(?:angry|mad|cry|crying|sad|happy|act|pretend)\b|(?:화내지\s*마|울지\s*마|슬퍼하지\s*마|(?:연기|화난|우는|슬픈)[^.!?;\n]{0,25}(?:하지|보지)\s*마)"
    elif kind == "subtitles":
        # "Don't show subtitles" can itself mean OFF; do not reject that.
        desired = "off" if subtitle_mode == "off" else "on"
        negative = (r"\b(?:don't|do not|never)\b[^.!?;\n]{0,35}\b(?:turn|switch)\b[^.!?;\n]{0,30}\b" + desired + r"\b"
                    + (r"|(?:자막[^.!?;\n]{0,20}끄지\s*마)" if desired == "off" else r"|(?:자막[^.!?;\n]{0,20}켜지\s*마)"))
    elif kind in _DEVICE_TOPICS:
        if re.search(r"\b(?:do you (?:support|have)|are you able to)\b|(?:기능(?:이|은|을)|할\s*수\s*있(?:어|니))\s*[?.!]*$", unquoted, re.I):
            return "capability_question"
        # Negative stop/mute is not the same action as a stop/mute request.
        # These checks only deny clear conflicts; the current model meaning is
        # still required and grants no runtime device permission.
        if action_name in ("stop_face_tracking", "stop_video", "stop_arms"):
            negative = r"\b(?:don't|do not|never)\s+(?:stop|cancel|end)\b|(?:멈추지|중지하지|끄지)\s*(?:마|말)"
        elif action_name == "mute_audio":
            negative = r"\b(?:don't|do not|never)\s+mute\b|음소거(?:하지|하진)\s*마"
        elif action_name == "unmute_audio":
            negative = r"\b(?:don't|do not|never)\s+unmute\b|음소거[^.!?;\n]{0,15}풀지\s*마"
        else:
            negative = r"\b(?:don't|do not|never|cancel)\b[^.!?;\n]{0,60}|(?:하지\s*(?:마|말)|하지마|찍지\s*(?:마|말)|따라오지\s*(?:마|말)|올리지\s*(?:마|말)|내리지\s*(?:마|말)|바꾸지\s*(?:마|말))"
    else:
        return "invalid_request"
    return "negated_request" if re.search(negative, unquoted, re.I) else None



# Clause boundaries only narrow rejection scope. They never authorize a request
# from words alone: the same one-response semantic interpretation is required.
_CLAUSE_SPLIT = re.compile(
    r"[;\n.!?]+|\s+(?:and\s+then|and|then|but|however|instead)\s+|[,，]+\s*(?:and|but|then)?\s*"
    r"|(?:그리고|그런데|하지만|대신|한\s*뒤에?|한\s*후에?)\s*|(?<=고)\s+|(?<=되)\s+|(?<=지만)\s+", re.I)
_TOPIC_BY_KIND = {"photo": _PHOTO_TOPIC, "acting": _ACTING_TOPIC,
                  "subtitles": _SUBTITLE_TOPIC, **_DEVICE_TOPICS}
_STOP_WORDS = re.compile(r"\b(?:stop|end|cancel|quit|disable)\b|멈|중지|그만|끄|꺼|따라오지\s*마", re.I)
_STOP_ACTIONS = frozenset({"stop_face_tracking", "stop_video", "stop_arms", "stop_media", "exit_game"})
_START_ACTIONS = frozenset({"start_face_tracking", "start_video", "arm_gesture", "play_song", "launch_game"})


def request_denial(message, kind, *, subtitle_mode="unchanged", action_name=None):
    """Reject locally contradictory clauses without denying another request.

    Broad topic checks select rejection context, not positive permission. If no
    relevant clause can be isolated, retain the conservative whole-message
    checks. Quoted/history/future clauses cannot supply a current action.
    """
    if type(message) is not str or not message.strip() or len(message) > 8000:
        return "no_current_user"
    text = message.strip().replace("\u2019", "'")
    unquoted = _QUOTES.sub(" ", text)
    if re.fullmatch(r"\s*(?:cancel|never\s*mind|forget\s+it|stop|wait|취소|그만|잠깐)(?:\s+please)?\s*[.!?]*", unquoted, re.I):
        return "negated_request"
    # A top-level request to translate/explain/repeat applies to all its clauses.
    if re.search(r"^\s*(?:(?:please|bmo|beemo)[,:]?\s+)*(?:translate|say|repeat|explain|what does|what is|what's|how (?:do|does|can)|tell me about)\b|^\s*(?:번역|따라\s*말)", unquoted, re.I):
        return "informational_request"
    if re.match(r"\s*>", text):
        return "quoted_request"
    clauses = [part.strip() for part in _CLAUSE_SPLIT.split(unquoted) if part.strip()]
    topic = _TOPIC_BY_KIND.get(kind)
    if topic is not None and topic.search(text) and not topic.search(unquoted):
        return "quoted_request"
    if len(clauses) < 2 or topic is None:
        return _single_request_denial(message, kind, subtitle_mode=subtitle_mode, action_name=action_name)
    relevant = [part for part in clauses if topic.search(part)]
    if action_name in _STOP_ACTIONS:
        specific = [part for part in relevant if _STOP_WORDS.search(part)]
        if specific:
            relevant = specific
    elif action_name in _START_ACTIONS:
        specific = [part for part in relevant if not _STOP_WORDS.search(part)]
        if specific:
            relevant = specific
    if not relevant:
        if topic.search(text) and not topic.search(unquoted):
            return "quoted_request"
        return _single_request_denial(message, kind, subtitle_mode=subtitle_mode, action_name=action_name)
    # Conflicting clauses for the same action stay denied; a model cannot use
    # one positive span to silently override another explicit veto.
    for part in relevant:
        denied = _single_request_denial(part, kind, subtitle_mode=subtitle_mode, action_name=action_name)
        if denied:
            return denied
    return None

def resolve_interpretation(value, message, *, turn_token, pending_confirmation=None, action_name=None):
    if not valid_interpretation(value):
        return None, "invalid_request"
    if value["turn_token"] != turn_token:
        return None, "stale_request"
    if value["kind"] == "none":
        return dict(value), "none"
    if value["kind"] == "compound":
        return dict(value), "validated"
    denied = request_denial(message, value["kind"], subtitle_mode=value["subtitle_mode"], action_name=action_name)
    if denied:
        return None, denied
    pending = pending_confirmation if type(pending_confirmation) is dict else {}
    confirmation = value["confirmation_token"]
    # The model may understand "That would be lovely" as an affirmative; a
    # correct live host token, not a phrase allowlist, is what permits it.
    # A new explicit request such as "Okay, take a picture" is not a bare
    # confirmation. These topic words do not grant an action; the validated
    # semantic response still has to request it and host permissions apply.
    topic = {"photo": _PHOTO_TOPIC, "acting": _ACTING_TOPIC, "subtitles": _SUBTITLE_TOPIC,
             **_DEVICE_TOPICS}[value["kind"]]
    affirmative = bool(_AFFIRMATIVE.match(message)) and not topic.search(message)
    if value["kind"] in _DEVICE_TOPICS:
        # Topic words are not a positive command allowlist. Natural commands
        # such as "Okay, make it quieter" or "Yes, lift the left one" can be
        # understood by the same response even without the literal noun.
        affirmative = bool(re.fullmatch(r"\s*(?:yes|yeah|yep|sure|okay|ok|네|예|응|좋아|그래)(?:\s*,?\s*please)?\s*[,.!?]*", message, re.I))
    if confirmation is not None or affirmative:
        if (value["kind"] != "photo" or value["disposition"] != "execute"
                or pending.get("kind") != "photo" or pending.get("token") != confirmation
                or confirmation is None):
            return None, "confirmation_missing"
        if re.match(r"\s*(?:no\b|nope\b|nah\b|don't\b|do not\b|never\b|maybe\b|not sure\b|wait\b|cancel\b|stop\b|never ?mind\b|아니|싫|취소|하지\s*마|잠깐|글쎄)", message, re.I):
            return None, "negated_request"
    return dict(value), "validated"
