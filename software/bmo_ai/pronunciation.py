"""Speech-only name normalization. Never modify GPT chat/display transcripts."""
from __future__ import annotations

import re

# Only a standalone Latin-script acronym. Avoid rewriting code identifiers,
# URLs containing BMO within a word, or Korean suffixes until a bilingual test.
_BMO_NAME = re.compile(r"(?<!\w)BMO(?!\w)", flags=re.IGNORECASE)
MODES = frozenset(("beemo", "bee-mo", "literal"))


def spoken_bmo_name(text: str, mode: str = "beemo") -> str:
    """Convert standalone BMO to the requested on-air name pronunciation.

    Original chat text stays untouched. Live Fish TTS calls this once at
    the output boundary, while sample generators can test spellings verbatim.
    """
    if not isinstance(text, str):
        raise ValueError("Speech input must be text")
    if mode not in MODES:
        raise ValueError("BMO name mode must be beemo, bee-mo or literal")
    if mode == "literal":
        return text
    return _BMO_NAME.sub("Beemo" if mode == "beemo" else "Bee-mo", text)
