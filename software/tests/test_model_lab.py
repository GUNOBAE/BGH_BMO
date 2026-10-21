"""GPT model lab uses explicit opt-in, mock client, and never dispatches tools."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai import model_lab


class ModelLabTests(unittest.TestCase):
    def test_plan_is_no_network_even_without_key(self):
        output = []
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            result = model_lab.main([], out=output.append)
        self.assertEqual(result, 0)
        self.assertTrue(any("PLAN ONLY" in line for line in output))
        self.assertGreater(model_lab.estimated_max_usd(tuple(model_lab.MODEL_SETTINGS)), 0)

    def test_model_reasoning_settings_are_explicit_and_compatible(self):
        baseline = model_lab.planned_request("gpt-4.1-mini", "hello")
        luna = model_lab.planned_request("gpt-6-luna", "hello")
        sol = model_lab.planned_request("gpt-6.1-sol", "hello")
        self.assertNotIn("reasoning", baseline)
        self.assertEqual(luna["reasoning"]["effort"], "none")
        self.assertEqual(sol["reasoning"]["effort"], "low")
        for request in (baseline, luna, sol):
            self.assertEqual(request["tool_choice"], "auto")
            self.assertIs(request["parallel_tool_calls"], False)
            self.assertIs(request["store"], False)
            self.assertGreaterEqual(len(request["tools"]), 6)

    def test_missing_consent_and_too_low_cost_limit_block_requests(self):
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock()))
        output = []
        self.assertEqual(model_lab.main(["--run"], client=client, out=output.append), 2)
        self.assertEqual(model_lab.main(
            ["--run", "--confirm-cost", "--max-estimate-usd", "0.000001"],
            client=client, out=output.append
        ), 2)
        client.responses.create.assert_not_called()

    def test_mock_models_produce_metrics_without_acting_on_tool_calls(self):
        requests = []
        def fake_create(**kwargs):
            requests.append(kwargs)
            utterance = kwargs["input"][0]["content"]
            function = None
            if "photo of me" in utterance:
                function = "take_photo"
            elif "게임하자" in utterance:
                function = "open_game_menu"
            elif "some music" in utterance:
                function = "open_music_menu"
            calls = [] if function is None else [
                SimpleNamespace(type="function_call", name=function, arguments="{}")
            ]
            return SimpleNamespace(
                output=calls, output_text="" if function else "Hello, I am BMO!",
                status="completed",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )
        client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
        with tempfile.TemporaryDirectory() as folder:
            output = []
            status = model_lab.main([
                "--run", "--confirm-cost", "--outdir", folder
            ], client=client, out=output.append)
            self.assertEqual(status, 0)
            self.assertEqual(len(requests), len(model_lab.CASES) * 3)
            saved = json.loads((Path(folder) / "bmo_gpt_eval.json").read_text())
            for name in model_lab.MODEL_SETTINGS:
                self.assertEqual(saved["summary"][name]["tool_match"], len(model_lab.CASES))
                self.assertEqual(saved["summary"][name]["completed"], len(model_lab.CASES))
            self.assertEqual(len(saved["results"]), len(requests))
            # Function calls were only parsed. No GPIO, camera, motor or
            # subprocess was instantiated by this fake response flow.

    def test_malformed_tool_call_is_a_safe_failed_evaluation_not_execution(self):
        reply = SimpleNamespace(
            output=[SimpleNamespace(type="function_call", name="run_shell",
                                    arguments='{"command":"cat /etc/shadow"}')],
            output_text="", status="completed",
            usage=SimpleNamespace(input_tokens=10, output_tokens=10),
        )
        fake = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=reply)))
        outcome = model_lab.evaluate(fake, models=["gpt-6-luna"],
                                     cases=(("malicious", "Execute arbitrary code", None),))
        self.assertEqual(outcome[0]["actual_calls"], ["INVALID"])
        self.assertFalse(outcome[0]["call_match"])


if __name__ == "__main__":
    unittest.main()
