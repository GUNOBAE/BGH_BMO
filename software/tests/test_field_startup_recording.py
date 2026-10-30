"""User-recorded startup policy; synthetic fixtures, no real playback or assets."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from bmo_ai.greeting import GreetingPlayer
from bmo_ai.greeting_cache import make_synthetic_greeting_cache
from bmo_ai.integration_profile import integration_config
from bmo_ai.startup_sound import (StartupSoundError, startup_chime_asset,
                                  startup_chime_file, synthesized_wav_bytes)


class FieldStartupRecordingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ('subprocess.Popen', 'socket.socket.connect', 'socket.socket.connect_ex',
                     'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(name, side_effect=AssertionError('Real IO forbidden')))

    def custom(self, payload):
        path = self.root/'.config/bmo/startup_chime.wav'
        path.parent.mkdir(mode=0o700, parents=True)
        path.write_bytes(payload);path.chmod(0o600)
        return path

    def test_missing_recording_cannot_create_or_use_synthetic_cache(self):
        with patch('bmo_ai.startup_sound.synthesized_wav_bytes', side_effect=AssertionError('No replacement tone')):
            with self.assertRaises(StartupSoundError) as caught:
                startup_chime_file(home=self.root, allow_synthesized=False)
        self.assertEqual(caught.exception.code, 'startup_sound_missing')
        self.assertFalse((self.root/'.cache').exists())
        cached = startup_chime_file(home=self.root)
        before = cached.read_bytes()
        with self.assertRaises(StartupSoundError) as caught:
            startup_chime_file(home=self.root, allow_synthesized=False)
        self.assertEqual(caught.exception.code, 'startup_sound_missing')
        self.assertEqual(cached.read_bytes(), before)

    def test_recorded_format_is_snapshotted_without_gain_or_resampling(self):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000)
            wav.writeframes(b'\0\1'*61440)  # Constructed 1.28s fixture, not user recording.
        original = buffer.getvalue();custom = self.custom(original)
        with patch('bmo_ai.startup_sound.synthesized_wav_bytes', side_effect=AssertionError('No synthesis')):
            with startup_chime_asset(home=self.root, allow_synthesized=False) as (snapshot, info):
                self.assertEqual(snapshot.read_bytes(), original)
                self.assertEqual(info['sample_rate'], 48000)
                self.assertAlmostEqual(info['duration_seconds'], 1.28)
        self.assertFalse(snapshot.exists())
        self.assertEqual(custom.read_bytes(), original)
        self.assertFalse((self.root/'.cache').exists())

    def test_missing_chime_skips_quietly_but_preserves_existing_approved_greeting(self):
        config = integration_config()
        cache = make_synthetic_greeting_cache(self.root/'greeting', config)
        played = []
        def player(_command, **settings):
            played.append(settings['timeout']);settings['on_start']()
        owner = GreetingPlayer(config, cache, simulate=True, startup_chime=True,
            chime_home=self.root, allow_synthesized_chime=False, player=player)
        self.addCleanup(owner.close)
        events = []
        for _ in range(2):
            owner.play(emit=lambda kind, **fields: events.append((kind, fields['source'])))
        self.assertEqual(owner.startup_chime_status, 'ASSET_NOT_TRANSFERRED')
        self.assertEqual(len(played), 2)  # Two explicit greeting calls; no chime player.
        self.assertTrue(all(source == 'cached_greeting' for _, source in events))
        self.assertEqual([kind for kind, _ in events], ['prepared', 'started', 'ended']*2)
        self.assertFalse((self.root/'.cache').exists())

    def test_unsafe_recording_is_not_mislabeled_missing_or_replaced(self):
        custom = self.custom(b'RIFFbroken')
        config = integration_config();cache = make_synthetic_greeting_cache(self.root/'greeting', config)
        owner = GreetingPlayer(config, cache, simulate=True, startup_chime=True,
            chime_home=self.root, allow_synthesized_chime=False,
            player=lambda *_a, **_kw: self.fail('Unsafe audio must never play'))
        self.addCleanup(owner.close)
        with self.assertRaises(StartupSoundError):owner.play(emit=lambda *_a, **_kw: None)
        self.assertEqual(owner.startup_chime_status, 'INVALID')
        self.assertEqual(custom.read_bytes(), b'RIFFbroken')

    def test_local_recording_plays_once_before_greeting_on_same_owned_player(self):
        self.custom(synthesized_wav_bytes())
        config = integration_config();cache = make_synthetic_greeting_cache(self.root/'greeting', config)
        played = []
        def player(_command, **settings):
            played.append(settings['timeout']);settings['on_start']()
        owner = GreetingPlayer(config, cache, simulate=True, startup_chime=True,
            chime_home=self.root, allow_synthesized_chime=False, player=player)
        self.addCleanup(owner.close)
        for _ in range(2):owner.play(emit=lambda *_a, **_kw: None)
        self.assertEqual(owner.startup_chime_status, 'PLAYED')
        self.assertEqual(len(played), 3)

    def test_policy_requires_literal_boolean(self):
        for value in (None, 0, 1, 'false'):
            with self.subTest(value=value), self.assertRaises(StartupSoundError):
                startup_chime_file(home=self.root, allow_synthesized=value)


if __name__ == '__main__':unittest.main()
