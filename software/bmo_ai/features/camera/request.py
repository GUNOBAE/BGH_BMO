"""Bounded current-user photo commands, not permission from a model reply.

Full-message matching intentionally excludes quoted, negated, hypothetical,
reported and compound requests. Ambiguous language keeps the existing UI route.
No image, device, transcript logging, provider or second player lives here.
"""
import re


_NAME = r"(?:(?:hey\s+)?(?:bmo|beemo)[,\s]+)?"
_ENGLISH = re.compile(
    _NAME + r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:take|snap|capture)\s+(?:(?:a|one)\s+(?:photo|picture)(?:\s+of\s+(?:me|us))?"
    r"|(?:my|our)\s+(?:photo|picture))"
    r"(?:\s+(?:now|please))?(?:\s+please)?[.!?]*", re.I)
_KOREAN = re.compile(
    r"(?:비모(?:야)?[,\s]*)?(?:지금\s*)?(?:(?:내|제|우리|저희)\s*)?"
    r"사진(?:을)?\s*(?:한\s*장\s*)?(?:좀\s*)?"
    r"(?:찍어\s*(?:줘|주세요|줄래|주겠니)|찍어)[.!?]*")


def explicit_photo_request(message):
    if type(message) is not str or not 0 < len(message) <= 256:
        return False
    text = " ".join(message.strip().split())
    return bool(_ENGLISH.fullmatch(text) or _KOREAN.fullmatch(text))


def current_photo_request(request):
    """Only the final current user item grants permission, never prior history."""
    items = request.get("input") if type(request) is dict else None
    if type(items) not in (tuple, list) or not items:
        return False
    current = items[-1]
    return (type(current) is dict and current.get("role") == "user"
            and explicit_photo_request(current.get("content")))
