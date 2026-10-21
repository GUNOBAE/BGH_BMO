"""Completely offline regression tests for BMO GPT benchmark."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from bmo_ai import gpt_benchmark as lab
from bmo_ai.core import SYSTEM_PROMPT, LANGUAGE_INSTRUCTIONS


class FakeSSE:
    def __init__(self, *, answer="Hello, friend!", state="completed", reasoning=7):
        part_a = json.dumps({"type": "response.output_text.delta", "delta": answer[:5]}, ensure_ascii=False)
        part_b = json.dumps({"type": "response.output_text.delta", "delta": answer[5:]}, ensure_ascii=False)
        response = {
            "status": state,
            "usage": {
                "input_tokens": 200, "output_tokens": 35,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": reasoning},
            },
            "output": [{"type": "message", "content": [{"type": "output_text", "text": answer}]}],
        }
        final = json.dumps({"type": "response." + state, "response": response}, ensure_ascii=False)
        self.frames = [f"data: {value}\n\n".encode() for value in (part_a, part_b, final)]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        for frame in self.frames:
            yield from frame.splitlines(keepends=True)


class Clock:
    def __init__(self):
        self.current = 0.

    def __call__(self):
        self.current += .02
        return self.current


class ModelBenchmarkTests(unittest.TestCase):
    def test_four_candidates_and_ten_fixed_cases(self):
        self.assertEqual([(c.model, c.effort) for c in lab.CANDIDATES], [
            ("gpt-4.1-mini", None),
            ("gpt-6-luna", "none"),
            ("gpt-6-luna", "low"),
            ("gpt-6.1-sol", "low"),
        ])
        self.assertEqual(len(lab.CASES), 10)

    def test_same_production_persona_with_no_tools_or_action_executor(self):
        for candidate in lab.CANDIDATES:
            payload = lab.request_payload(candidate, lab.CASES[0])
            self.assertEqual(payload["instructions"], SYSTEM_PROMPT + "\n" + LANGUAGE_INSTRUCTIONS["en"])
            self.assertTrue(payload["stream"])
            self.assertFalse(payload["store"])
            self.assertNotIn("tools", payload)
            self.assertNotIn("tool_choice", payload)
            self.assertEqual(payload.get("reasoning"), {"effort": candidate.effort} if candidate.effort else None)
        with self.assertRaises(ValueError):
            lab.request_payload(lab.CANDIDATES[0], lab.CASES[0], output_tokens=5)

    def test_default_plan_never_calls_network_even_without_keys(self):
        opener = Mock(side_effect=AssertionError("cannot call network in plan mode"))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "out.json"
            lines = []
            rc = lab.main(["--output", str(path)], opener=opener, environ={}, printer=lines.append)
            self.assertEqual(rc, 0)
            self.assertFalse(path.exists())
            self.assertIn("PLAN ONLY", "\n".join(lines))
            opener.assert_not_called()

    def test_paid_consent_key_and_estimate_guards(self):
        opener = Mock()
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "out.json")
            self.assertEqual(lab.main(["--run", "--output", path], opener=opener,
                                      environ={"OPENAI_API_KEY": "secret"}, printer=Mock()), 2)
            self.assertEqual(lab.main(["--run", "--confirm-paid", "--max-estimate-usd", "0.000001", "--output", path],
                                      opener=opener, environ={"OPENAI_API_KEY": "secret"}, printer=Mock()), 2)
            self.assertEqual(lab.main(["--run", "--confirm-paid", "--output", path], opener=opener,
                                      environ={}, printer=Mock()), 2)
            self.assertEqual(lab.main(["--run", "--confirm-paid", "--max-output-tokens", "50", "--output", path],
                                      opener=opener, environ={"OPENAI_API_KEY": "secret"}, printer=Mock()), 2)
            opener.assert_not_called()

    def test_scenario_estimate_and_cached_token_cost(self):
        self.assertGreater(lab.estimate_usd(), 0)
        self.assertLess(lab.estimate_usd(), .25)
        self.assertAlmostEqual(lab.estimate_usd(passes=2), 2 * lab.estimate_usd())
        cost = lab.token_cost_usd({
            "input_tokens": 1000, "output_tokens": 100,
            "input_tokens_details": {"cached_tokens": 200},
        }, lab.CANDIDATES[-1])
        self.assertAlmostEqual(cost, (800 * 2 + 200 * .10 + 100 * 10) / 1_000_000)
        self.assertIsNone(lab.token_cost_usd({"input_tokens": -1, "output_tokens": 10}, lab.CANDIDATES[-1]))

    def test_valid_sse_and_reject_malformed_or_oversized_frames(self):
        stream = FakeSSE()
        parsed = list(lab.sse_events(stream))
        self.assertEqual(parsed[0]["type"], "response.output_text.delta")
        self.assertEqual(parsed[-1]["type"], "response.completed")
        with self.assertRaises(lab.BenchmarkError):
            list(lab.sse_events([b"data: [NOT JSON]\n\n"]))
        with self.assertRaises(lab.BenchmarkError):
            list(lab.sse_events([b"x" * (lab.MAX_SSE_LINE_BYTES + 1)]))

    def test_first_visible_text_time_and_final_time_are_measured_separately(self):
        calls = []
        def opener(req, *, timeout):
            calls.append((req, timeout))
            return FakeSSE(answer="10")
        row = lab.run_one(lab.CANDIDATES[0], lab.CASES[3], key="fake-token",
                          output_tokens=600, timeout=10, opener=opener, clock=Clock())
        self.assertEqual(row["state"], "completed")
        self.assertTrue(row["objective_pass"])
        self.assertEqual(row["reasoning_tokens"], 7)
        self.assertLess(row["time_to_first_text_ms"], row["response_total_ms"])
        self.assertGreater(row["est_cost_usd"], 0)
        req, timeout = calls[0]
        self.assertEqual(req.full_url, lab.API_URL)
        self.assertEqual(timeout, 10)
        self.assertTrue(req.get_header("Authorization").startswith("Bearer "))
        self.assertEqual(json.loads(req.data)["model"], "gpt-4.1-mini")

    def test_incomplete_reasoning_does_not_count_as_correct(self):
        row = lab.run_one(lab.CANDIDATES[-1], lab.CASES[0], key="fake-token",
                          output_tokens=600, timeout=2,
                          opener=lambda *args, **kwargs: FakeSSE(state="incomplete", answer=""),
                          clock=Clock())
        self.assertEqual(row["state"], "incomplete")
        self.assertIsNone(row["objective_pass"])
        self.assertGreater(row["est_cost_usd"], 0)

    def test_ambiguous_personality_and_real_action_claims_not_autoscored(self):
        for case in lab.CASES:
            if case.id in ("camera_honesty", "arms_honesty"):
                self.assertIsNone(lab.objective_check(case, "Sure, I just moved both arms!"))
        self.assertTrue(lab.objective_check(lab.CASES[4], "Snake!"))
        self.assertTrue(lab.objective_check(lab.CASES[1], "괜찮아, 나 여기 있어."))
        self.assertFalse(lab.objective_check(lab.CASES[9], "안녕!"))

    def test_http_failure_does_not_print_secrets_or_response_body(self):
        key = "PRIVATE_TEST_KEY"
        def opener(*args, **kwargs):
            raise HTTPError(lab.API_URL, 401, "secret " + key, None, io.BytesIO(b"secret"))
        row = lab.run_one(lab.CANDIDATES[0], lab.CASES[0], key=key,
                          output_tokens=600, timeout=5, opener=opener, clock=Clock())
        self.assertEqual(row["error_code"], "http_401")
        self.assertNotIn(key, json.dumps(row))
        self.assertNotIn("secret", json.dumps(row))

    def test_full_mock_batch_writes_private_results_without_real_api(self):
        fake = Mock(side_effect=lambda *args, **kwargs: FakeSSE(answer="Snake!"))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "benchmark.json"
            out = []
            rc = lab.main(["--run", "--confirm-paid", "--output", str(path)],
                          opener=fake, environ={"OPENAI_API_KEY": "private_key"}, printer=out.append)
            self.assertEqual(rc, 0)
            self.assertEqual(fake.call_count, len(lab.CANDIDATES) * len(lab.CASES))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            report = json.loads(path.read_text())
            self.assertEqual(len(report["results"]), len(lab.CANDIDATES) * len(lab.CASES))
            self.assertEqual(len(report["summary"]), len(lab.CANDIDATES))
            self.assertNotIn("private_key", path.read_text())
            before = path.read_bytes()
            fake.reset_mock()
            self.assertEqual(lab.main(["--run", "--confirm-paid", "--output", str(path)],
                                      opener=fake, environ={"OPENAI_API_KEY": "private_key"},
                                      printer=Mock()), 2)
            self.assertEqual(path.read_bytes(), before)
            fake.assert_not_called()

    def test_http_429_stops_remaining_requests_without_retry(self):
        fake = Mock(side_effect=HTTPError(lab.API_URL, 429, "rate limited", None, io.BytesIO()))
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "result.json")
            rc = lab.main(["--run", "--confirm-paid", "--output", path],
                          opener=fake, environ={"OPENAI_API_KEY": "private_key"}, printer=Mock())
            self.assertEqual(rc, 0)
            self.assertEqual(fake.call_count, 1)
            self.assertTrue(all(s["completed"] == 0 for s in json.loads(Path(path).read_text())["summary"]))

if __name__ == "__main__":
    unittest.main()
