"""Original offline chime and cached English greeting; no paid/device playback."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from bmo_ai.conversation import TurnCancelled
from bmo_ai.greeting import GreetingPlayer
from bmo_ai.greeting_cache import make_synthetic_greeting_cache, GREETING_TEXT
from bmo_ai.integration_profile import integration_config
from bmo_ai.startup_sound import (StartupSoundError, startup_chime_asset,
                                  startup_chime_file, synthesized_wav_bytes)


class StartupSoundTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())); self.forbidden = []
        for target in ('subprocess.Popen', 'bmo_ai.fish_tts.synthesize_wav',
                       'bmo_ai.cloud.create_client', 'bmo_ai.private_credentials.resolve_credentials'):
            self.forbidden.append(self.enterContext(patch(target, side_effect=AssertionError('No production IO'))))

    def tearDown(self):
        for guard in self.forbidden: guard.assert_not_called()

    def test_original_pcm_is_byte_deterministic_and_private_cache_reused(self):
        raw = synthesized_wav_bytes(); self.assertEqual(raw, synthesized_wav_bytes())
        with wave.open(io.BytesIO(raw), 'rb') as source:
            self.assertEqual((source.getnchannels(), source.getsampwidth(), source.getframerate()), (1, 2, 24000))
            self.assertEqual(source.getnframes(), 28800)
        target = startup_chime_file(home=self.root); self.assertEqual(target.read_bytes(), raw)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600); self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        with patch('bmo_ai.startup_sound.synthesized_wav_bytes', side_effect=AssertionError('No rebuild')):
            self.assertEqual(startup_chime_file(home=self.root), target)
        self.assertFalse(list(target.parent.glob('.boot-*.tmp')))

    def test_user_custom_private_pcm_is_used_without_overwriting_it(self):
        custom = self.root / '.config' / 'bmo' / 'startup_chime.wav'; custom.parent.mkdir(parents=True, mode=0o700)
        custom.write_bytes(synthesized_wav_bytes()); custom.chmod(0o600)
        self.assertEqual(startup_chime_file(home=self.root), custom); self.assertFalse((self.root / '.cache').exists())
        with startup_chime_asset(home=self.root) as (snapshot, info):
            self.assertNotEqual(snapshot, custom); self.assertEqual(snapshot.read_bytes(), custom.read_bytes())
            self.assertAlmostEqual(info['duration_seconds'], 1.2)
        self.assertFalse(snapshot.exists()); self.assertTrue(custom.exists())

    def test_symlink_directory_or_leaf_and_world_readable_custom_fail_closed(self):
        other = self.root / 'other'; other.mkdir(mode=0o700)
        (self.root / '.config').symlink_to(other, target_is_directory=True)
        with self.assertRaises(StartupSoundError): startup_chime_file(home=self.root)
        self.assertEqual(list(other.iterdir()), []); (self.root / '.config').unlink()
        custom = self.root / '.config' / 'bmo' / 'startup_chime.wav'; custom.parent.mkdir(parents=True, mode=0o700)
        real = self.root / 'owned.wav'; real.write_bytes(synthesized_wav_bytes()); real.chmod(0o600); custom.symlink_to(real)
        with self.assertRaises(StartupSoundError): startup_chime_file(home=self.root)
        custom.unlink(); custom.write_bytes(real.read_bytes()); custom.chmod(0o644)
        with self.assertRaises(StartupSoundError): startup_chime_file(home=self.root)
        self.assertEqual(real.read_bytes(), synthesized_wav_bytes())

    def test_private_snapshot_is_removed_on_cancel_and_does_not_mutate_cache(self):
        cached = startup_chime_file(home=self.root); original = cached.read_bytes()
        with self.assertRaises(TurnCancelled):
            with startup_chime_asset(home=self.root) as (snapshot, _): raise TurnCancelled()
        self.assertFalse(snapshot.exists()); self.assertEqual(cached.read_bytes(), original)

    def test_invalid_or_truncated_cache_is_not_replaced_or_played(self):
        cached = startup_chime_file(home=self.root); cached.write_bytes(b'RIFFbroken')
        with self.assertRaises(StartupSoundError): startup_chime_file(home=self.root)
        self.assertEqual(cached.read_bytes(), b'RIFFbroken')

    def test_one_player_chime_then_approved_english_greeting_once_even_on_retry(self):
        config = integration_config(); cache = make_synthetic_greeting_cache(self.root / 'greeting', config)
        records, events = [], []
        def player(command, **settings): records.append(settings['timeout']); settings['on_start']()
        owner = GreetingPlayer(config, cache, simulate=True, player=player, startup_chime=True); self.addCleanup(owner.close)
        real_loader = startup_chime_asset
        with patch('bmo_ai.startup_sound.startup_chime_asset', side_effect=lambda **_settings: real_loader(home=self.root)):
            for _ in range(2): owner.play(emit=lambda kind, **fields: events.append((kind, fields['source'])))
        self.assertEqual(GREETING_TEXT, "Hey! You're back! BMO missed you!"); self.assertEqual(len(records), 3)
        self.assertAlmostEqual(records[0], 11.2)
        self.assertEqual([kind for kind, source in events], ['prepared', 'started', 'ended'] * 2)
        self.assertTrue(all(source == 'cached_greeting' for _, source in events)); self.assertIsNone(owner.voice._cache)

    def test_cancel_during_chime_never_starts_greeting_or_replays_boot_sound(self):
        cache = make_synthetic_greeting_cache(self.root / 'greeting', integration_config()); played = []; owner = None
        def cancel_player(command, **settings):
            played.append(command); settings['on_start'](); owner.cancel(); settings['cancel_check']()
        owner = GreetingPlayer(integration_config(), cache, simulate=True, player=cancel_player, startup_chime=True)
        self.addCleanup(owner.close); real_loader = startup_chime_asset; events = []
        with patch('bmo_ai.startup_sound.startup_chime_asset', side_effect=lambda **_settings: real_loader(home=self.root)) as loader:
            with self.assertRaises(TurnCancelled): owner.play(emit=lambda kind, **fields: events.append(kind))
            owner.voice._player = lambda _cmd, **settings: settings['on_start']()
            owner.play(emit=lambda *_a, **_k: None)
        self.assertEqual(loader.call_count, 1); self.assertEqual(len(played), 1); self.assertEqual(events, ['cancelled'])


if __name__ == '__main__': unittest.main()
