"""Focused Aaron refinement uses only free Fish S2.1 and never the rejected voices."""
import json
import os
import tempfile
import unittest
import wave
from datetime import date
from pathlib import Path
from unittest.mock import patch

from bmo_ai import fish_aaron_refine


class AaronRefinementTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.entries = []
        self.printed = []
        self.current_date = patch("bmo_ai.fish_aaron_refine.today_kst", return_value=date(2026, 10, 21))
        self.current_date.start()
        self.addCleanup(self.current_date.stop)

    def generate(self, text, *, model, reference_id):
        self.entries.append((text, model, reference_id))
        p = self.root / ("source" + str(len(self.entries)) + ".wav")
        with wave.open(str(p), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 1600)
        return p

    def run_lab(self, *args):
        return fish_aaron_refine.run(
            [*args, "--output-dir", str(self.root / "output")],
            synth=self.generate, output=self.printed.append
        )

    def test_default_plan_never_calls_provider(self):
        with patch.dict(os.environ, {"FISH_API_KEY": ""}):
            self.assertEqual(self.run_lab(), 0)
        self.assertEqual(self.entries, [])
        self.assertFalse((self.root / "output").exists())

    def test_free_all_uses_one_aaron_voice_and_only_spelling_style_changes(self):
        with patch.dict(os.environ, {"FISH_API_KEY": "offline-fixture"}):
            self.assertEqual(self.run_lab("--mode", "free", "--variant", "all"), 0)
        self.assertEqual(len(self.entries), len(fish_aaron_refine.CASES))
        self.assertEqual({entry[1] for entry in self.entries}, {"s2.1-pro-free"})
        self.assertEqual({entry[2] for entry in self.entries}, {fish_aaron_refine.AARON})
        self.assertIn("I'm Beemo!", self.entries[0][0])
        self.assertIn("I'm Bee-mo!", self.entries[1][0])
        self.assertIn("I'm Beemo!", self.entries[2][0])
        self.assertTrue(self.entries[0][0].endswith(fish_aaron_refine.BASE.format(name="Beemo")))
        self.assertTrue(self.entries[1][0].endswith(fish_aaron_refine.BASE.format(name="Bee-mo")))
        output = next((self.root / "output").iterdir())
        self.assertEqual(len(list(output.glob("*.wav"))), 3)
        self.assertIn("<audio controls", (output / "listen.html").read_text())
        self.assertEqual(len(json.loads((output / "results.json").read_text())), 3)
        self.assertEqual(list(self.root.glob("source*.wav")), [])

    def test_missing_key_or_expired_free_period_blocks(self):
        with patch.dict(os.environ, {"FISH_API_KEY": ""}):
            self.assertEqual(self.run_lab("--mode", "free"), 2)
        with patch("bmo_ai.fish_aaron_refine.today_kst", return_value=date(2026, 12, 1)):
            with patch.dict(os.environ, {"FISH_API_KEY": "offline-fixture"}):
                self.assertEqual(self.run_lab("--mode", "free"), 2)
        self.assertEqual(self.entries, [])


if __name__ == "__main__":
    unittest.main()
