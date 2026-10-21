"""Read-only BMO Action Lab contract and command routing regression tests."""
import json
import unittest
from unittest.mock import patch

from bmo_ai.action_lab import (
    Action, ActionValidationError, direct_intent, parse_tool_call,
    simulate_action, tool_definitions,
)


class ActionLabTests(unittest.TestCase):
    def test_supported_functions_are_strict_and_no_motors_are_exposed(self):
        tools = tool_definitions()
        names = {entry["name"] for entry in tools}
        self.assertEqual(len(names), len(tools))
        self.assertIn("take_photo", names)
        self.assertIn("open_game_menu", names)
        self.assertIn("play_song", names)
        self.assertNotIn("arm_gesture", names)
        self.assertNotIn("run_shell", names)
        for tool in tools:
            self.assertTrue(tool["strict"])
            self.assertEqual(tool["type"], "function")
            schema = tool["parameters"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))

    def test_exact_arguments_allowlist_and_safe_catalog_ids(self):
        self.assertEqual(parse_tool_call("take_photo", "{}"), Action("take_photo"))
        self.assertEqual(
            parse_tool_call("play_song", '{"track_id":"theme_01"}'),
            Action("play_song", "theme_01"),
        )
        self.assertEqual(
            parse_tool_call("show_expression", '{"emotion":"happy"}'),
            Action("show_expression", "happy"),
        )
        for name, args in (
            ("arm_gesture", '{"gesture":"raise_both"}'),
            ("run_shell", '{"command":"rm -rf /"}'),
            ("play_song", '{"track_id":"../secrets"}'),
            ("launch_game", '{"game_id":"/bin/sh"}'),
            ("launch_game", '{"game_id":"Game With Spaces"}'),
            ("launch_game", '{"game_id":""}'),
            ("show_expression", '{"emotion":"evil"}'),
            ("play_song", '{"track_id":true}'),
            ("take_photo", '{"filename":"/tmp/secret"}'),
            ("take_photo", '{"x":1,"x":2}'),
            ("take_photo", '{"x":1}'),
            ("take_photo", "[]"),
            ("take_photo", "{not json}"),
            ("take_photo", " "),
            ("UNKNOWN", "{}"),
        ):
            with self.subTest(name=name, args=args), self.assertRaises(ActionValidationError):
                parse_tool_call(name, args)

    def test_no_hardware_ever_runs_in_preview(self):
        for cmd in (
            Action("take_photo"),
            Action("open_game_menu"),
            Action("open_music_menu"),
            Action("stop_song"),
            Action("close_game"),
            Action("stop_speaking"),
            Action("show_expression", "happy"),
        ):
            result = simulate_action(cmd)
            self.assertEqual(result.status, "preview")
            self.assertIn("Would", result.description)
        self.assertEqual(simulate_action(Action("take_photo")).speech_cue, "Cheese!")
        self.assertEqual(simulate_action(Action("arm_gesture")).status, "blocked")
        self.assertEqual(simulate_action(Action("launch_game", "snake")).status, "unavailable")
        self.assertEqual(simulate_action(Action("play_song", "beemo_song")).status, "unavailable")
        self.assertEqual(
            simulate_action(Action("launch_game", "snake"), installed_games={"snake"}).status,
            "preview",
        )
        self.assertEqual(
            simulate_action(Action("play_song", "theme"), installed_tracks={"theme"}).status,
            "preview",
        )
        with self.assertRaises(ActionValidationError):
            simulate_action(Action("show_expression", "__import__"))

    def test_fast_path_is_whole_utterance_and_not_an_untrusted_lexical_match(self):
        examples = {
            "비모야 사진 찍어줘!": "take_photo",
            "사진 찍어줘": "take_photo",
            "Hey BMO! Take a picture.": "take_photo",
            "게임하자!": "open_game_menu",
            "비모 게임 켜줘": "open_game_menu",
            "노래 틀어줘": "open_music_menu",
            "Stop the music!": "stop_song",
            "비모 게임 종료해줘": "close_game",
            "BMO stop talking": "stop_speaking",
        }
        for phrase, name in examples.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(direct_intent(phrase), Action(name))
        for unsafe in (
            "내가 사진 찍어줘라고 말하면 어떻게 돼?",
            "If I say take a photo, don't actually do it.",
            "비모 너는 사진 찍어줘라는 명령을 알아들어?",
            "Please move the arm by 900 degrees.",
            "안녕, 오늘 기분 어때?",
            "rm -rf /",
            "사진 찍어줘" * 150,
            None,
        ):
            with self.subTest(unsafe=unsafe):
                self.assertIsNone(direct_intent(unsafe))


if __name__ == "__main__":
    unittest.main()
