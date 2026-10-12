import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bmo_ai.core import BMOBrain, BrainResponseError, SYSTEM_PROMPT


class FakeResponses:
    def __init__(self, reply="Hello, friend!", status="completed"):
        self.calls = []
        self.reply = reply
        self.status = status
        self.error = None
        self.incomplete_details = None
        self.exception = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exception:
            raise self.exception
        return SimpleNamespace(
            output_text=self.reply, status=self.status, error=self.error,
            incomplete_details=self.incomplete_details,
        )


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeResponses()
        self.client = SimpleNamespace(responses=self.fake)

    def test_conversation_uses_stateless_responses_and_local_complete_turns(self):
        brain = BMOBrain(self.client, max_turns=2, max_output_tokens=90)
        self.assertEqual(brain.ask(" Hi "), "Hello, friend!")
        self.assertEqual(brain.ask(""), "")
        for i in range(6):
            brain.ask(str(i))
        self.assertEqual(len(brain.history), 4)
        self.assertEqual(len(self.fake.calls[-1]["input"]), 3)
        for call in self.fake.calls:
            self.assertFalse(call["store"])
            self.assertEqual(call["max_output_tokens"], 90)
            self.assertNotIn("previous_response_id", call)
            self.assertEqual(call["input"][-1]["role"], "user")
            self.assertEqual([item["role"] for item in call["input"]],
                             ["user", "assistant"] * (len(call["input"]) // 2) + ["user"])
        self.assertEqual([item["role"] for item in brain.history],
                         ["user", "assistant", "user", "assistant"])
        self.assertIn("fan-built", SYSTEM_PROMPT)

    def test_default_model_and_explicit_model_override_environment(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(BMOBrain(self.client).model, "gpt-4.1-mini")
        with patch.dict("os.environ", {"BMO_CHAT_MODEL": "environment-model"}):
            self.assertEqual(BMOBrain(self.client).model, "environment-model")
            self.assertEqual(BMOBrain(self.client, model="explicit-model").model, "explicit-model")

    def test_language_instructions_keep_english_default_with_korean_support(self):
        brain = BMOBrain(self.client)
        brain.ask("안녕")
        prompt = self.fake.calls[-1]["instructions"]
        self.assertIn("English by default", prompt)
        self.assertIn("Korean", prompt)
        for language, expected in (("ko", "Korean by default"), ("auto", "latest message")):
            BMOBrain(self.client, language=language).ask("hello")
            self.assertIn(expected, self.fake.calls[-1]["instructions"])

    def test_failed_request_preserves_history_even_when_candidate_trimmed(self):
        brain = BMOBrain(self.client, max_turns=1)
        brain.ask("first")
        before = [dict(item) for item in brain.history]
        self.fake.exception = RuntimeError("offline secret request details")
        with self.assertRaises(RuntimeError) as context:
            brain.ask("second")
        self.assertNotIn("secret", str(context.exception))
        self.assertEqual(brain.history, before)
        self.assertEqual(len(self.fake.calls), 2)

    def test_empty_and_incomplete_responses_preserve_history(self):
        brain = BMOBrain(self.client)
        brain.ask("first")
        before = [dict(item) for item in brain.history]
        for status in (None, "incomplete", "failed", "cancelled", "queued", "in_progress"):
            self.fake.status = status
            with self.subTest(status=status), self.assertRaises(BrainResponseError):
                brain.ask("second")
            self.assertEqual(brain.history, before)
        self.fake.status = "completed"
        for reply in (None, "", " \n ", 42):
            self.fake.reply = reply
            with self.subTest(reply=reply), self.assertRaises(BrainResponseError):
                brain.ask("second")
            self.assertEqual(brain.history, before)
        self.fake.reply = "partial"
        self.fake.incomplete_details = {"reason": "max_output_tokens"}
        with self.assertRaises(BrainResponseError):
            brain.ask("second")
        self.assertEqual(brain.history, before)
        self.fake.incomplete_details = None
        self.fake.error = {"message": "secret request details"}
        with self.assertRaisesRegex(BrainResponseError, "Cloud response failed"):
            brain.ask("second")
        self.assertEqual(brain.history, before)

    def test_blank_and_oversized_input_do_not_call_api(self):
        brain = BMOBrain(self.client, max_input_chars=4, max_history_chars=10)
        self.assertEqual(brain.ask("  "), "")
        for message in ("12345", "     ", "가나다라마"):
            with self.assertRaises(ValueError):
                brain.ask(message)
        for message in (None, 12, ["hello"]):
            with self.assertRaises(TypeError):
                brain.ask(message)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(brain.history, [])

    def test_history_and_outbound_character_budgets_drop_whole_turns(self):
        self.fake.reply = "AAAA"
        brain = BMOBrain(self.client, max_turns=20, max_input_chars=6, max_history_chars=18)
        brain.ask("1111")
        brain.ask("2222")
        brain.ask("333333")
        self.assertEqual(self.fake.calls[-1]["input"], [
            {"role": "user", "content": "2222"},
            {"role": "assistant", "content": "AAAA"},
            {"role": "user", "content": "333333"},
        ])
        for messages in [brain.history] + [call["input"] for call in self.fake.calls]:
            self.assertLessEqual(sum(len(item["content"]) for item in messages), 18)
        self.assertEqual(brain.history[0]["content"], "2222")

    def test_turn_larger_than_history_budget_is_answered_without_partial_retention(self):
        self.fake.reply = "too long for history"
        brain = BMOBrain(self.client, max_input_chars=4, max_history_chars=4)
        self.assertEqual(brain.ask("hi"), self.fake.reply)
        self.assertEqual(brain.history, [])

    def test_reset_makes_next_request_start_without_previous_context(self):
        brain = BMOBrain(self.client)
        brain.ask("remember")
        brain.reset()
        self.assertEqual(brain.history, [])
        brain.ask("hello")
        self.assertEqual(self.fake.calls[-1]["input"], [{"role": "user", "content": "hello"}])

    def test_invalid_constructor_budgets_and_languages(self):
        for name in ("max_turns", "max_input_chars", "max_history_chars", "max_output_tokens"):
            for value in (0, -1, True, 1.5):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    BMOBrain(self.client, **{name: value})
        for kwargs in ({"language": "es"},
                       {"max_input_chars": 11, "max_history_chars": 10}, {"model": " "}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BMOBrain(self.client, **kwargs)


if __name__ == "__main__":
    unittest.main()
