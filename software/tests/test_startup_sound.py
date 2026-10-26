"""Offline BMO startup chime and English-only fixed greeting regression."""
import hashlib
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai import audio
from bmo_ai.greeting import GreetingPlayer
from bmo_ai.greeting_cache import GreetingCache, GreetingSpec, GREETING_TEXT, default_greeting_directory
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import synthetic_wav
from bmo_ai.startup_sound import (StartupSoundError, startup_chime_file, synthesized_wav_bytes)


class StartupSoundTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = synthetic_wav()
        self.addCleanup(audio._discard, self.source, suppress=True)

    def test_original_sound_is_short_valid_pcm_and_byte_deterministic(self):
        first = synthesized_wav_bytes()
        self.assertEqual(first, synthesized_wav_bytes())
        self.assertTrue(first.startswith(b"RIFF"))
        self.assertLess(len(first), 100_000)
        target = self.root / "preview.wav"
        target.write_bytes(first)
        info = audio._wav_info(target)
        self.assertEqual((info["channels"], info["sample_width"], info["sample_rate"]), (1, 2, 24000))
        self.assertAlmostEqual(info["duration_seconds"], 1.20, delta=.01)

    def test_fixed_chime_cached_locally_and_not_rebuilt(self):
        path = startup_chime_file(home=self.root)
        raw = path.read_bytes()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        with patch("bmo_ai.startup_sound.synthesized_wav_bytes", side_effect=AssertionError("rebuild forbidden")):
            self.assertEqual(startup_chime_file(home=self.root), path)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), hashlib.sha256(raw).digest())

    def test_valid_private_user_sound_overrides_generated_sound(self):
        target = self.root / ".config" / "bmo" / "startup_chime.wav"
        target.parent.mkdir(parents=True)
        target.write_bytes(synthesized_wav_bytes())
        target.chmod(0o600)
        self.assertEqual(startup_chime_file(home=self.root), target)
        self.assertFalse((self.root / ".cache" / "bmo" / "startup-chime-v1").exists())

    def test_symlink_and_world_visible_user_sound_fail_closed(self):
        target = self.root / ".config" / "bmo" / "startup_chime.wav"
        target.parent.mkdir(parents=True)
        real = self.root / "real.wav"
        real.write_bytes(synthesized_wav_bytes())
        target.symlink_to(real)
        with self.assertRaises(StartupSoundError):
            startup_chime_file(home=self.root)
        target.unlink()
        target.write_bytes(synthesized_wav_bytes())
        target.chmod(0o644)
        with self.assertRaises(StartupSoundError):
            startup_chime_file(home=self.root)

    def test_english_greeting_chime_then_one_cached_voice(self):
        config = integration_config()
        self.assertEqual(GREETING_TEXT, "Oh! You're back! BMO missed you!")
        self.assertIn("Beemo missed you!", GreetingSpec.from_config(config).spoken)
        self.assertTrue(str(default_greeting_directory(home=self.root, environ={})).endswith("/greeting-welcome-en-v1"))
        cache = GreetingCache(self.root / "private-welcome", GreetingSpec.from_config(config))
        cache.publish(self.source, approved=True, simulated=True)
        voice = GreetingPlayer(config, cache, simulate=True, startup_chime=True)
        self.addCleanup(voice.close)
        sources = []
        events = []
        def observe(path, source, info, cues, check, emit, on_playback_start, expression):
            sources.append((source, len(cues)))
            check()
            emit("prepared", cues=list(cues))
            emit("started")
            emit("ended")
        with patch.object(voice.voice, "_play", side_effect=observe):
            with patch("bmo_ai.startup_sound.startup_chime_file", return_value=self.root / "sound.wav"):
                (self.root / "sound.wav").write_bytes(synthesized_wav_bytes())
                result = voice.play(emit=lambda *a, **k: events.append(a[0]))
        self.assertTrue(result["completed"])
        self.assertEqual([entry[0] for entry in sources], ["local_startup_chime", "cached_greeting"])
        self.assertEqual(sources[0][1], 0)
        self.assertEqual(events, ["prepared", "started", "ended"])


if __name__ == "__main__":
    unittest.main()
