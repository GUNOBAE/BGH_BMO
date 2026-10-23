"""Run the actual launcher with synthetic IO and credential-free child envs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]

# Guard the real launcher boundary, rather than mocking the session being
# tested. Only its harmless owned Python sleep child is permitted to spawn.
CHILD = r'''
from contextlib import ExitStack
from pathlib import Path
import os
import subprocess
import sys
from unittest.mock import patch

allowed_environment = {"PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH",
                       "PYTHONDONTWRITEBYTECODE", "LC_CTYPE"}
if set(os.environ) - allowed_environment:
    raise AssertionError("Unexpected launcher environment field")

from bmo_ai.integration import main

original_popen = subprocess.Popen
def harmless_child(command, *args, **kwargs):
    if command != [sys.executable, "-c", "import time; time.sleep(0.8)"]:
        raise AssertionError("Non-synthetic child forbidden")
    if set(kwargs.get("env", {})) - allowed_environment:
        raise AssertionError("Unexpected player environment field")
    return original_popen(command, *args, **kwargs)

with ExitStack() as guards:
    for target in ("socket.socket.connect", "bmo_ai.cloud.create_client",
                   "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                   "bmo_ai.fish_tts.synthesize_wav",
                   "bmo_ai.local_piper.synthesize_wav"):
        guards.enter_context(patch(target, side_effect=AssertionError("Live IO forbidden")))
    guards.enter_context(patch("subprocess.Popen", side_effect=harmless_child))
    raise SystemExit(main(sys.argv[1:], lock_path=Path(os.environ["HOME"]) / "launcher.lock"))
'''


class IntegrationLauncherTests(unittest.TestCase):
    def launch(self, *arguments, stdin=None):
        # Do not copy or filter a real credential-bearing environment. Construct
        # the complete child environment from public, fixed fields instead.
        with tempfile.TemporaryDirectory() as home:
            environment = {
                "PATH": os.defpath,
                "HOME": home,
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(ROOT / "software"),
            }
            return subprocess.run(
                [sys.executable, "-c", CHILD, *arguments], cwd=ROOT,
                env=environment, input=stdin, capture_output=True, text=True, timeout=12,
            )

    def test_effective_config_uses_selected_profile_without_parent_environment(self):
        # These are synthetic markers only; none are passed to the test child.
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "synthetic-private-marker",
            "FISH_API_KEY": "synthetic-private-marker",
            "GH_TOKEN": "synthetic-private-marker",
            "BMO_CHAT_MODEL": "unselected-parent-model",
            "BMO_FISH_GAIN": "0.99",
        }):
            result = self.launch("--effective-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["chat_model"], "gpt-6-luna")
        self.assertEqual(config["reasoning_effort"], "low")
        self.assertEqual(config["fish_gain"], .25)
        self.assertEqual(config["language_policy"], "english_voice")
        self.assertEqual(config["audio_owner"], "python_alsa")
        self.assertEqual(config["emotion_mode"], "manual")
        self.assertEqual(config["screen_profile"], "v13.2")
        self.assertNotIn("private", result.stdout + result.stderr)
        self.assertNotIn("unselected-parent-model", result.stdout)

    def test_auto_mode_is_explicit_in_effective_config(self):
        result = self.launch("--emotion-mode", "auto", "--effective-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["emotion_mode"], "auto")

    def test_r10_selection_is_explicit_without_loading_an_asset_or_credentials(self):
        result = self.launch("--screen-profile", "r10", "--effective-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["screen_profile"], "r10")

    def test_unknown_screen_profile_is_rejected(self):
        result = self.launch("--screen-profile", "unregistered-screen", "--effective-config")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_auto_launcher_uses_same_two_turn_path(self):
        result = self.launch("--simulate", "--emotion-mode", "auto", "--no-web", "--turns", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        turns = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        self.assertEqual([turn["history_messages"] for turn in turns], [2, 4])
        self.assertTrue(all(turn["playback"] == "ended" for turn in turns))

    def test_runtime_emotion_modes_are_accepted_without_cloud_io(self):
        result = self.launch("--simulate", "--no-web", stdin=(
            "/emotion happy\n/emotion auto\n/emotion manual\n/reset\n/quit\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Emotion:", result.stdout)
        self.assertNotIn("Unknown command", result.stdout)

    def test_baseline_effective_config_clears_reasoning(self):
        result = self.launch("--baseline", "--effective-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertEqual(config["chat_model"], "gpt-4.1-mini")
        self.assertIsNone(config["reasoning_effort"])
        self.assertEqual(config["max_output_tokens"], 320)

    def test_two_synthetic_turns_use_one_brain_and_harmless_player(self):
        result = self.launch("--simulate", "--no-web", "--turns", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        turns = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(turns, [
            {"turn": 1, "history_messages": 2, "playback": "ended"},
            {"turn": 2, "history_messages": 4, "playback": "ended"},
        ])

    def test_explicit_mode_is_required_and_modes_are_exclusive(self):
        for arguments in ((), ("--no-web",), ("--simulate", "--live")):
            with self.subTest(arguments=arguments):
                result = self.launch(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_repeat_without_a_successful_voice_is_explicit_and_does_not_synthesize(self):
        result = self.launch("--simulate", "--no-web", stdin="/repeat\n/quit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No successful voice is cached", result.stdout)
        self.assertNotIn("Unknown command", result.stdout)

    def test_mock_action_demo_keeps_logical_ids_and_ledger_across_dialogue_reset(self):
        result = self.launch("--simulate", "--no-web",
                             stdin="/actions-demo\n/actions-demo\n/reset\n/actions-demo\n/quit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(len(actions), 15)
        self.assertTrue(all(item["executed"] is False for item in actions))
        self.assertTrue(all(item["status"] == "simulated" and item["code"] == "mock_only"
                            for item in actions[:5]))
        self.assertTrue(all(item["status"] == "duplicate" for item in actions[5:]))
        self.assertEqual([item["request_id"] for item in actions[:5]],
                         [item["request_id"] for item in actions[10:]])

    def test_explicit_action_json_rejects_invalid_input_and_preserves_request_identity(self):
        request = {"action": "play_song", "arguments": {"song_id": "demo_song"},
                   "request_id": "cli-song-001"}
        result = self.launch("--simulate", "--no-web", stdin=(
            "/action not-json\n/action []\n/action " + json.dumps(request) + "\n/quit\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("structured JSON request", result.stdout)
        actions = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["status"], "rejected")
        self.assertEqual(actions[1]["request_id"], request["request_id"])
        self.assertEqual(actions[1]["code"], "mock_only")
        self.assertTrue(all(item["executed"] is False for item in actions))


if __name__ == "__main__":
    unittest.main()
