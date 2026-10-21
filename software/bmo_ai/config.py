"""Validated, nonsecret runtime settings for the Raspberry Pi application.

The API key belongs to the SDK environment, never this dataclass. Loading settings
does not read dotenv files, probe hardware, or modify the operating system.
"""

from dataclasses import dataclass, fields
import math
import os
import re
from typing import Mapping


class ConfigError(ValueError):
    """A BMO setting is invalid; messages contain setting names, never values."""


@dataclass(frozen=True)
class BMOConfig:
    chat_model: str = "gpt-4.1-mini"
    stt_model: str = "gpt-4o-mini-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    voice: str = "coral"
    tts_provider: str = "openai"
    fish_model: str = "s2.1-pro-free"
    fish_voice_id: str = "106f41ea392c4cc48c6eee76f69ff17e"
    fish_gain: float = 0.30
    fish_name_mode: str = "beemo"
    fish_delivery: str = "soft"
    language: str = "en"
    seconds: int = 5
    mic_device: str | None = None
    speaker_device: str | None = None
    timeout: float = 30.0
    max_retries: int = 1
    max_turns: int = 8
    max_input_chars: int = 4000
    max_history_chars: int = 16000
    max_output_tokens: int = 180
    log_file: str | None = None
    log_level: str = "INFO"

    def __post_init__(self):
        for name in ("chat_model", "stt_model", "tts_model", "voice"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or any(char.isspace() for char in value):
                raise ConfigError(f"BMO_{name.upper()} must be a nonempty name without whitespace")
        # Use the same built-in/legacy voice rules as playback, before a paid
        # chat request can succeed with an unusable synthesis configuration.
        from .audio import AudioValidationError, validate_speech_settings
        try:
            validate_speech_settings(self.tts_model, self.voice)
        except AudioValidationError as error:
            if error.code == "invalid_model":
                raise ConfigError("BMO_TTS_MODEL must be a valid speech model identifier") from None
            raise ConfigError("BMO_VOICE must be a supported built-in voice for BMO_TTS_MODEL") from None
        if not isinstance(self.tts_provider, str) or self.tts_provider not in ("openai", "fish"):
            raise ConfigError("BMO_TTS_PROVIDER must be openai or fish")
        if not isinstance(self.fish_model, str) or self.fish_model not in ("s2.1-pro-free", "s2.1-pro", "drama-3"):
            raise ConfigError("BMO_FISH_MODEL must be s2.1-pro-free, s2.1-pro, or drama-3")
        if not isinstance(self.fish_voice_id, str) or not re.fullmatch(r"[a-f0-9]{32}", self.fish_voice_id):
            raise ConfigError("BMO_FISH_VOICE_ID must be a 32-character lowercase hex voice ID")
        if isinstance(self.fish_gain, bool) or not isinstance(self.fish_gain, (float, int)) or not math.isfinite(self.fish_gain) or not 0 < self.fish_gain <= 1:
            raise ConfigError("BMO_FISH_GAIN must be a number above 0 and at most 1")
        if self.fish_name_mode not in ("beemo", "bee-mo", "literal"):
            raise ConfigError("BMO_FISH_NAME_MODE must be beemo, bee-mo or literal")
        if not isinstance(self.fish_delivery, str) or self.fish_delivery not in ("soft", "plain"):
            raise ConfigError("BMO_FISH_DELIVERY must be soft or plain")
        if self.language not in ("en", "ko", "auto"):
            raise ConfigError("BMO_LANGUAGE must be en, ko, or auto")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ConfigError("BMO_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        integer_bounds = {
            "seconds": (1, 20),
            "max_retries": (0, 5),
            "max_turns": (1, 100),
            "max_input_chars": (1, 100000),
            "max_history_chars": (1, 1000000),
            "max_output_tokens": (1, 8192),
        }
        for name, (minimum, maximum) in integer_bounds.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ConfigError(f"BMO_{name.upper()} must be an integer between {minimum} and {maximum}")
        if self.max_history_chars < self.max_input_chars:
            raise ConfigError("BMO_MAX_HISTORY_CHARS must be at least BMO_MAX_INPUT_CHARS")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or not 0 < self.timeout <= 300
        ):
            raise ConfigError("BMO_TIMEOUT must be a finite number greater than 0 and at most 300")
        for name in ("mic_device", "speaker_device", "log_file"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value)
            ):
                raise ConfigError(f"BMO_{name.upper()} must be a nonempty string without control characters")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        """Load only documented BMO settings; an explicit mapping is isolated.

        Malformed settings fail at startup. Error text never reflects raw values,
        so even an accidentally pasted credential is kept out of logs.
        """
        source = os.environ if env is None else env
        integer_names = {
            "seconds", "max_retries", "max_turns", "max_input_chars",
            "max_history_chars", "max_output_tokens",
        }
        optional_names = {"mic_device", "speaker_device", "log_file"}
        values = {}
        for field in fields(cls):
            name = field.name
            variable = f"BMO_{name.upper()}"
            if variable not in source:
                continue
            raw = source[variable]
            if not isinstance(raw, str):
                raise ConfigError(f"{variable} must be a string")
            raw = raw.strip()
            if name in integer_names:
                try:
                    # Disallow formats such as underscores accepted by int().
                    if not raw.isascii() or not raw.isdecimal():
                        raise ValueError
                    values[name] = int(raw)
                except ValueError:
                    raise ConfigError(f"{variable} must be an integer") from None
            elif name in ("timeout", "fish_gain"):
                try:
                    values[name] = float(raw)
                except ValueError:
                    raise ConfigError(f"{variable} must be a number") from None
            elif name in optional_names:
                values[name] = raw or None
            elif name == "log_level":
                values[name] = raw.upper()
            elif name in ("language", "tts_provider", "fish_delivery"):
                values[name] = raw.lower()
            else:
                values[name] = raw
        return cls(**values)
