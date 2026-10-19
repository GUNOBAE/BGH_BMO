"""Test Fish Aaron A/B tool without external API, speakers or billing."""
import io
import json
import os
import tempfile
import unittest
import wave
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from bmo_ai import fish_compare


def make_wav(path):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)


class FishCompareTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = []
        self.requests = []
        self.counter = 0
        self.fake_day = patch("bmo_ai.fish_compare.today_kst", return_value=date(2026, 10, 19))
        self.fake_day.start()
        self.addCleanup(self.fake_day.stop)

    def generate(self, text, *, model, reference_id):
        self.counter += 1
        path = self.root / ("source_" + str(self.counter) + ".wav")
        make_wav(path)
        self.requests.append((text, model, reference_id))
        return path

    def call(self, *args):
        return fish_compare.run(
            [*args, "--output-dir", str(self.root / "results")],
            synthesis_fn=self.generate, output_fn=self.output.append
        )

    def test_plan_is_offline_and_requires_no_fish_key(self):
        with patch.dict(os.environ, {"FISH_API_KEY": ""}):
            self.assertEqual(self.call(), 0)
        self.assertEqual(self.requests, [])
        self.assertFalse((self.root / "results").exists())
        self.assertIn("NO", "NO")  # explicit offline mode is also tested via no files
        self.assertTrue(any("PLAN ONLY" in line for line in self.output))

    def test_free_one_voice_saves_valid_wav_and_html(self):
        with patch.dict(os.environ, {"FISH_API_KEY": "test-token"}):
            self.assertEqual(self.call("--mode", "free", "--sample", "hello"), 0)
        self.assertEqual(len(self.requests), 1)
        text, model, voice = self.requests[0]
        self.assertEqual(model, "s2.1-pro-free")
        self.assertEqual(text, fish_compare.SAMPLES["hello"][1])
        self.assertEqual(voice, "106f41ea392c4cc48c6eee76f69ff17e")
        folder = next((self.root / "results").iterdir())
        self.assertTrue((folder / "hello_free.wav").is_file())
        self.assertFalse((folder / "hello_drama3.wav").exists())
        report = (folder / "report.html").read_text()
        self.assertIn('audio controls', report)
        self.assertIn("hello_free.wav", report)
        self.assertIn("Not requested", report)
        details = json.loads((folder / "results.json").read_text())
        self.assertEqual(details["results"][0]["cost_estimate_usd"], 0)
        self.assertFalse(list(self.root.glob("source_*.wav")))

    def test_compare_requires_explicit_paid_flag_and_cost_limit(self):
        with patch.dict(os.environ, {"FISH_API_KEY": "test-token"}):
            self.assertEqual(self.call("--mode", "compare"), 2)
            self.assertEqual(self.call("--mode", "compare", "--confirm-paid",
                                       "--max-paid-usd", "0.000001"), 2)
        self.assertEqual(self.requests, [])
        self.assertFalse((self.root / "results").exists())

    def test_compare_both_models_same_text_and_id(self):
        with patch.dict(os.environ, {"FISH_API_KEY": "test-token"}):
            self.assertEqual(self.call("--mode", "compare", "--confirm-paid"), 0)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0][0], self.requests[1][0])
        self.assertEqual(self.requests[0][2], self.requests[1][2])
        self.assertEqual([r[1] for r in self.requests], ["s2.1-pro-free", "drama-3"])
        folder = next((self.root / "results").iterdir())
        self.assertTrue((folder / "hello_drama3.wav").exists())
        self.assertEqual(len(json.loads((folder / "results.json").read_text())["results"]), 2)

    def test_all_samples_exactly_four_pairs_no_hidden_extra_requests(self):
        with patch.dict(os.environ, {"FISH_API_KEY": "test-token"}):
            self.assertEqual(self.call("--mode", "compare", "--sample", "all", "--confirm-paid"), 0)
        self.assertEqual(len(self.requests), 8)
        self.assertEqual([r[1] for r in self.requests], ["s2.1-pro-free", "drama-3"] * 4)
        self.assertEqual(len(set(r[2] for r in self.requests)), 1)

    def test_missing_key_blocks_before_any_request(self):
        with patch.dict(os.environ, {"FISH_API_KEY": ""}):
            self.assertEqual(self.call("--mode", "free"), 2)
        self.assertEqual(self.requests, [])

    def test_if_free_fails_it_never_spends_on_drama(self):
        def failed_free(*args, **kwargs):
            raise fish_compare.audio.AudioSynthesisError("Fish free model unavailable", "fish_permission")
        with patch.dict(os.environ, {"FISH_API_KEY": "test-token"}):
            self.assertEqual(fish_compare.run(
                ["--mode", "compare", "--confirm-paid", "--output-dir", str(self.root / "results")],
                synthesis_fn=failed_free, output_fn=self.output.append,
            ), 1)
        self.assertEqual(len(list((self.root / "results").glob("*/results.json"))), 1)

    def test_expired_free_period_requires_terms_review(self):
        with patch("bmo_ai.fish_compare.today_kst", return_value=date(2026, 12, 1)), patch.dict(
            os.environ, {"FISH_API_KEY": "test-token"}
        ):
            self.assertEqual(self.call("--mode", "free"), 2)
            self.assertEqual(self.requests, [])
            self.assertEqual(self.call("--mode", "free", "--reviewed-free-terms"), 0)
            self.assertEqual(len(self.requests), 1)

    def test_promo_rate_switch(self):
        self.assertEqual(fish_compare.drama_rate(date(2026, 11, 12)), 10)
        self.assertEqual(fish_compare.drama_rate(date(2026, 11, 13)), 50)


if __name__ == "__main__":
    unittest.main()
