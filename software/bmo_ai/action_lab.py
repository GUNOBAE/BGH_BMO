"""BMO Action Lab: validation-only tool contracts and zero-side-effect previews.

This module intentionally does NOT import GPIO, RP2040 serial, Pi camera, game
launchers, music players, or face renderers. In this branch no physical action
or shell command can run, even when a GPT function call is supplied.

The front-end, local Piper lab, Fish Aaron, and Pi runtime are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import re
from typing import Any

MAX_ARGUMENT_BYTES = 512
_SAFE_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")
_NO_TARGET = frozenset({
    "take_photo", "open_game_menu", "open_music_menu",
    "stop_song", "close_game", "stop_speaking",
})
_WITH_TARGET = {
    "play_song": "track_id",
    "launch_game": "game_id",
    "show_expression": "emotion",
}
_EMOTIONS = frozenset({"idle", "happy", "sad", "sleepy", "surprised", "thinking"})
# Hardware driving is a separate supervised future project. Do not expose a
# motor tool until RP2040 firmware, wiring, motion range and stop are validated.
_BLOCKED = frozenset({"arm_gesture", "move_servo", "run_shell", "execute_code"})


class ActionValidationError(ValueError):
    """Fixed, safe messages. Never include untrusted model arguments."""


@dataclass(frozen=True)
class Action:
    name: str
    target: str | None = None


@dataclass(frozen=True)
class ActionPreview:
    name: str
    status: str
    description: str
    # For photos, "Cheese!" is a future pre-capture cue, NOT proof of capture.
    speech_cue: str | None = None


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in pairs:
        if key in fields:
            raise ActionValidationError("Duplicate tool fields are forbidden.")
        fields[key] = value
    return fields


def parse_tool_call(name: str, arguments: str) -> Action:
    """Parse an OpenAI Responses function_call with an exact allowlist schema.

    The returned object is an intent ONLY; parse_tool_call does not execute.
    """
    if not isinstance(name, str) or name in _BLOCKED:
        raise ActionValidationError("Physical motion and arbitrary code are disabled.")
    if name not in _NO_TARGET and name not in _WITH_TARGET:
        raise ActionValidationError("This BMO action is not available.")
    if not isinstance(arguments, str) or len(arguments.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ActionValidationError("Tool arguments are too large or not JSON.")
    try:
        obj = json.loads(arguments, object_pairs_hook=_unique_fields)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as err:
        raise ActionValidationError("Tool arguments are not valid JSON.") from None
    if not isinstance(obj, dict):
        raise ActionValidationError("Tool arguments must be a JSON object.")
    required = { _WITH_TARGET[name] } if name in _WITH_TARGET else set()
    if set(obj) != required:
        raise ActionValidationError("Unexpected or missing action fields.")
    if not required:
        return Action(name=name)
    value = obj[next(iter(required))]
    if not isinstance(value, str) or not _SAFE_KEY.fullmatch(value):
        raise ActionValidationError("Action target must be a short catalog key.")
    if name == "show_expression" and value not in _EMOTIONS:
        raise ActionValidationError("Unknown BMO expression.")
    return Action(name=name, target=value)


def tool_definitions() -> list[dict]:
    """Strict Responses API function schemas; motors intentionally absent."""
    explanations = {
        "take_photo": "User explicitly asks to take a photograph. Never claim a photo was captured until Pi confirms it.",
        "open_game_menu": "User wants to play a game without naming a verified game.",
        "open_music_menu": "User wants music but did not name a verified track.",
        "stop_song": "User asks to stop the current music.",
        "close_game": "User asks to exit an active game and return to BMO face.",
        "stop_speaking": "User asks BMO to stop its voice playback.",
        "play_song": "User selects a confirmed preinstalled song by its whitelisted track_id.",
        "launch_game": "User selects a confirmed preinstalled game by its whitelisted game_id.",
        "show_expression": "User explicitly requests an expression; use the enumerated emotion.",
    }
    result = []
    for name in (*sorted(_NO_TARGET), *sorted(_WITH_TARGET)):
        properties = {}
        required = []
        if name in _WITH_TARGET:
            field = _WITH_TARGET[name]
            spec = {"type": "string"}
            if name == "show_expression":
                spec["enum"] = sorted(_EMOTIONS)
            properties[field] = spec
            required = [field]
        result.append({
            "type": "function",
            "name": name,
            "description": explanations[name],
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        })
    return result


def simulate_action(action: Action, *, installed_games=(), installed_tracks=()) -> ActionPreview:
    """Dry-run only. No backend exists in this lab, regardless of user input."""
    if not isinstance(action, Action):
        raise ActionValidationError("Expected a validated action.")
    if action.name in _BLOCKED:
        return ActionPreview(action.name, "blocked", "Arm motors are not yet approved.")
    # Revalidate any directly constructed Action; callers cannot bypass schema.
    arg = {} if action.name in _NO_TARGET else {
        _WITH_TARGET[action.name]: action.target
    } if action.name in _WITH_TARGET else None
    if arg is None:
        raise ActionValidationError("Unknown action.")
    parse_tool_call(action.name, json.dumps(arg))

    if action.name == "launch_game" and action.target not in frozenset(installed_games):
        return ActionPreview(action.name, "unavailable", "Requested game is not verified as installed.")
    if action.name == "play_song" and action.target not in frozenset(installed_tracks):
        return ActionPreview(action.name, "unavailable", "Requested song is not verified as installed.")
    messages = {
        "take_photo": "Would say Cheese and request a camera frame, if a verified camera adapter is attached.",
        "open_game_menu": "Would show the BMO game selector after display integration.",
        "open_music_menu": "Would show preinstalled songs after an audio-library adapter is verified.",
        "stop_song": "Would stop BMO music (not other operating system audio).",
        "close_game": "Would close the active BMO game and restore the face.",
        "stop_speaking": "Would interrupt only the BMO voice output.",
        "play_song": "Would play a verified local audio track.",
        "launch_game": "Would launch a verified local game by ID.",
        "show_expression": "Would request a BMO face emotion; v9 face renderer not linked yet.",
    }
    return ActionPreview(
        name=action.name, status="preview", description=messages[action.name],
        speech_cue="Cheese!" if action.name == "take_photo" else None,
    )


# Local fast path is intentionally conservative. These must match whole
# utterances, not hypotheticals ('if you ask BMO to take a photo...').
# Unmatched sentences go to GPT when the live action router is built.
_COMMANDS = {
    "사진 찍어줘": "take_photo",
    "사진 찍어": "take_photo",
    "사진 찍자": "take_photo",
    "take a photo": "take_photo",
    "take a picture": "take_photo",
    "게임하자": "open_game_menu",
    "게임 하고 싶어": "open_game_menu",
    "게임 켜줘": "open_game_menu",
    "let's play a game": "open_game_menu",
    "play a game": "open_game_menu",
    "노래 불러줘": "open_music_menu",
    "노래 틀어줘": "open_music_menu",
    "노래 재생해줘": "open_music_menu",
    "play a song": "open_music_menu",
    "play some music": "open_music_menu",
    "노래 꺼줘": "stop_song",
    "음악 꺼줘": "stop_song",
    "stop the music": "stop_song",
    "게임 종료해줘": "close_game",
    "게임 꺼줘": "close_game",
    "quit the game": "close_game",
    "조용히 해줘": "stop_speaking",
    "stop talking": "stop_speaking",
}
_PREFIX = re.compile(r"^(?:(?:hey[ ,]+)?bmo[!,\s]+|비모[야,!\s]+)", re.IGNORECASE)


def direct_intent(utterance: str) -> Action | None:
    """Recognize a tiny set of clear commands without a GPT network request.

    Never tries to infer the game/song title from arbitrary words.
    """
    if not isinstance(utterance, str) or len(utterance) > 160:
        return None
    clean = utterance.strip().lower().rstrip("!?.。！？ ").strip()
    clean = _PREFIX.sub("", clean).strip()
    action = _COMMANDS.get(clean)
    return Action(action) if action else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Offline BMO Action Lab — no hardware control")
    p.add_argument("--say", default="비모 사진 찍어줘", help="Only parse one example command; no cloud/device calls")
    args = p.parse_args(argv)
    intent = direct_intent(args.say)
    if intent is None:
        print("No direct safe action recognized. Future GPT intent path would handle this.")
    else:
        preview = simulate_action(intent)
        print(json.dumps(preview.__dict__, indent=2, ensure_ascii=False))
    print("DRY RUN: no camera, GPIO, motors, music, games, or API calls occurred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
