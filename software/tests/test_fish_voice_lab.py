"""Voice lab stays on free Fish model, even across multiple public BMO voices."""
import io
import json
import os
import tempfile
import unittest
import wave
from datetime import date
from pathlib import Path
from unittest.mock import patch

from bmo_ai import fish_voice_lab


class FishVoiceLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = []
        self.calls = []
        self.clock = patch("bmo_ai.fish_voice_lab.today_kst", return_value=date(2026, 10, 21))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def synthesis(self, text, *, model, reference_id):
        self.calls.append((text, model, reference_id))
        out = self.root / ("request-" + str(len(self.calls)) + ".wav")
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 1200)
        return out

    def run_lab(self, *flags):
        return fish_voice_lab.run([*flags, "--output-dir", str(self.root / "results")],
                                  synth=self.synthesis, output=self.logs.append)

    def test_plan_no_network_or_file_or_key(self):
        with patch.dict(os.environ, {"FISH_API_KEY": ""}):
            self.assertEqual(self.run_lab(), 0)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "results").exists())

    def test_default_one_free_aaron(self):
        with patch.dict(os.environ, {"FISH_API_KEY": "offline-test-key"}):
            self.assertEqual(self.run_lab("--mode", "free"), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], "s2.1-pro-free")
        self.assertEqual(self.calls[0][2], fish_voice_lab.VOICES["aaron"][1])
        folder = next((self.root / "results").iterdir())
        self.assertTrue((folder / "aaron_plain.wav").is_file())
        self.assertIn("audio controls", (folder / "listen.html").read_text())
        self.assertEqual(json.loads((folder / "results.json").read_text())[0]["model"], "s2.1-pro-free")

    def test_all_profiles_one_voice_per_line_and_never_paid(self):
        with patch.dict(os.environ, {"FISH_API_KEY": "offline-test-key"}):
            self.assertEqual(self.run_lab("--mode", "free", "--profile", "all"), 0)
        self.assertEqual(len(self.calls), len(fish_voice_lab.PROFILES))
        self.assertEqual({c[1] for c in self.calls}, {"s2.1-pro-free"})
        names = [fish_voice_lab.PROFILES[n][0] for n in fish_voice_lab.PROFILES]
        self.assertEqual([x[2] for x in self.calls],
                         [fish_voice_lab.VOICES[n][1] for n in names])
        self.assertEqual(self.calls[0][0], fish_voice_lab.SCRIPTS["normal"])
        self.assertEqual(self.calls[1][0].split("] ")[1], fish_voice_lab.SCRIPTS["normal"])
        folder = next((self.root / "results").iterdir())
        self.assertEqual(len(list(folder.glob("*.wav"))), len(fish_voice_lab.PROFILES))

    def test_missing_key_and_post_free_window_both_block(self):
        with patch.dict(os.environ, {"FISH_API_KEY": ""}):
            self.assertEqual(self.run_lab("--mode", "free"), 2)
        with patch("bmo_ai.fish_voice_lab.today_kst", return_value=date(2026, 12, 1)):
            with patch.dict(os.environ, {"FISH_API_KEY": "offline-test-key"}):
                self.assertEqual(self.run_lab("--mode", "free"), 2)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
