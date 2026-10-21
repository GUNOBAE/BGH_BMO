"""Regression for Fish WAV placeholder headers (no real API use)."""
import io
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from bmo_ai import fish_tts
from bmo_ai.audio import AudioValidationError


def sample(placeholder=True, invalid=False):
    output = io.BytesIO()
    with wave.open(output, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 1600)
    raw = bytearray(output.getvalue())
    if placeholder:
        struct.pack_into("<I", raw, 4, 4294967076)
        struct.pack_into("<I", raw, 40, 12345678 if invalid else 4294967040)
    return bytes(raw)


class FishWavHeaderTests(unittest.TestCase):
    def test_fix_known_placeholder_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "f.wav"
            path.write_bytes(sample())
            with self.assertRaises(AudioValidationError):
                fish_tts.audio._wav_info(path, allow_streaming_header=True)
            fish_tts._normalize_fish_wav(path)
            self.assertEqual(path.read_bytes(), sample(False))
            self.assertAlmostEqual(fish_tts.audio._wav_info(path)["duration_seconds"], .1)

    def test_reject_inconsistent_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "broken.wav"
            path.write_bytes(sample(invalid=True))
            with self.assertRaises(AudioValidationError):
                fish_tts._normalize_fish_wav(path)

    def test_synthesize_works_with_fish_placeholder(self):
        class Response:
            def __init__(self):
                self.done = False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, _):
                if self.done:
                    return b""
                self.done = True
                return sample()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tts.wav"
            with patch.dict(os.environ, {"FISH_API_KEY": "offline-test-key"}), patch(
                "bmo_ai.fish_tts.urlopen", return_value=Response()
            ), patch("bmo_ai.fish_tts.audio._new_wav", return_value=path):
                result = fish_tts.synthesize_wav("Hello!")
            self.assertEqual(result, path)
            self.assertEqual(path.read_bytes(), sample(False))
            path.unlink()


if __name__ == "__main__":
    unittest.main()
