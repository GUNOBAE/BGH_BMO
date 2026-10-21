import unittest
from types import SimpleNamespace
from bmo_ai.core import BMOBrain, SYSTEM_PROMPT

class FakeResponses:
    def __init__(self):
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text="Hello, friend!")

class BrainTests(unittest.TestCase):
    def test_conversation_and_context(self):
        fake = FakeResponses()
        brain = BMOBrain(SimpleNamespace(responses=fake), max_turns=2)
        self.assertEqual(brain.ask("Hi"), "Hello, friend!")
        self.assertEqual(brain.ask(""), "")
        for i in range(6):
            brain.ask(str(i))
        self.assertLessEqual(len(brain.history), 4)
        self.assertLessEqual(len(fake.calls[-1]["input"]), 4)
        self.assertEqual(fake.calls[-1]["model"], "gpt-4.1-mini")
        self.assertIn("not the", SYSTEM_PROMPT)
    def test_failed_request_keeps_history(self):
        class Broken:
            def create(self, **kwargs):
                raise RuntimeError("offline")
        brain = BMOBrain(SimpleNamespace(responses=Broken()))
        with self.assertRaises(RuntimeError):
            brain.ask("hello")
        self.assertEqual(brain.history, [])

if __name__ == "__main__":
    unittest.main()
