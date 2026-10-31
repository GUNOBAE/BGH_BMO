"""Explicit integration choices and a pinned spoken-dialogue policy.

No environment, SDK client, device, or model is loaded here. The launcher owns
the persistent brain returned by policy_brain; do not recreate it every turn.
"""

import hashlib
from pathlib import Path

from .config import BMOConfig
from .core import BMOBrain, VOICE_LANGUAGE_INSTRUCTIONS


# The user requested a character-style revision after the 2026-10-30 field test.
# The exact source is the pinned blob below; do not attribute new bytes to the
# preserved baseline commit or invent a self-referential containing commit SHA.
POLICY_SOURCE_COMMIT = None
POLICY_BASE_COMMIT = "707c66b8876130be75ad216fa10aaabf2bdcdb56"
POLICY_REVISION = "character-life-20261030-v2"
POLICY_SOURCE_PATH = "docs/character-life-v1/system-prompt.en.txt"
POLICY_BLOB_SHA = "2b2b5263253ffbce8c3f6c37ec312c06a221ff64"
DEFAULT_LANGUAGE_POLICY = "english_voice"
_POLICY_PATH = Path(__file__).with_name("policies") / "system-prompt.en.txt"


def integration_config(baseline=False, offline_voice=False, **overrides):
    """Build actual BMOConfig values without inheriting shell settings.

    The 320-token limit is the integration starting profile, not a claim about
    model quality. Piper remains an explicit opt-in. Selecting GPT-4.1 mini
    always removes Luna reasoning, including when it appears in overrides.
    """
    if type(baseline) is not bool:
        raise ValueError("baseline must be a boolean")
    values = {
        "chat_model": "gpt-4.1-mini" if baseline else "gpt-6-luna",
        "reasoning_effort": None if baseline else "low",
        "stt_model": "gpt-4o-mini-transcribe",
        "tts_provider": "fish",
        "fish_model": "s2.1-pro-free",
        "fish_voice_id": "106f41ea392c4cc48c6eee76f69ff17e",
        "fish_gain": 0.25,
        "fish_name_mode": "beemo",
        "fish_delivery": "soft",
        "language": "en",  # legacy STT setting; spoken policy is explicit below
        "max_output_tokens": 320,
        "offline_voice": offline_voice,
    }
    values.update(overrides)
    if values["chat_model"] == "gpt-4.1-mini":
        values["reasoning_effort"] = None
    return BMOConfig(**values)


def load_voice_policy():
    """Verify Git's blob identity before using the separately shipped prompt.

    Comparing the runtime file with another mutable copy would allow both to
    drift together. The fixed blob ID identifies the versioned source bytes.
    Failures contain neither file contents nor underlying filesystem errors.
    """
    try:
        raw = _POLICY_PATH.read_bytes()
    except OSError:
        raise ValueError("Integration voice policy is unavailable") from None
    blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    if hashlib.sha1(blob, usedforsecurity=False).hexdigest() != POLICY_BLOB_SHA:
        raise ValueError("Integration voice policy does not match the pinned source")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Integration voice policy is not valid UTF-8") from None


def policy_brain(client, config, *, language_policy=DEFAULT_LANGUAGE_POLICY,
                 expression_client=None):
    """Create a persistent brain, optionally using a non-owning expression view.

    The session supplies and owns the optional view. The ordinary client remains
    available to its STT/TTS paths; neither client is closed by this factory.
    """
    if not isinstance(language_policy, str) or language_policy not in VOICE_LANGUAGE_INSTRUCTIONS:
        raise ValueError("Unsupported integration language policy")
    return BMOBrain(
        client if expression_client is None else expression_client,
        model=config.chat_model,
        reasoning_effort=config.reasoning_effort,
        language=config.language,
        max_turns=config.max_turns,
        max_input_chars=config.max_input_chars,
        max_history_chars=config.max_history_chars,
        max_output_tokens=config.max_output_tokens,
        system_prompt=load_voice_policy(),
        language_policy=language_policy,
    )


def effective_config(config, *, language_policy=DEFAULT_LANGUAGE_POLICY, emotion_mode="manual"):
    """Return an explicit noncredential allowlist; never serialize config/env."""
    if not isinstance(language_policy, str) or language_policy not in VOICE_LANGUAGE_INSTRUCTIONS:
        raise ValueError("Unsupported integration language policy")
    if emotion_mode not in ("auto", "manual"):
        raise ValueError("Unsupported emotion mode")
    names = (
        "chat_model", "reasoning_effort", "stt_model", "tts_provider",
        "fish_model", "fish_voice_id", "fish_gain", "fish_name_mode",
        "fish_delivery", "offline_voice", "language", "max_output_tokens",
        "max_turns", "max_input_chars", "max_history_chars", "seconds",
        "timeout", "max_retries",
    )
    return dict(
        {name: getattr(config, name) for name in names},
        language_policy=language_policy,
        emotion_mode=emotion_mode,
        policy_source_commit=POLICY_SOURCE_COMMIT,
        policy_source_path=POLICY_SOURCE_PATH,
        policy_blob_sha=POLICY_BLOB_SHA,
        policy_revision=POLICY_REVISION,
        policy_base_commit=POLICY_BASE_COMMIT,
        audio_owner="python_alsa",
    )
