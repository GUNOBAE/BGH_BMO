"""Wake acknowledgment contract with private synthetic bundles only."""
from pathlib import Path
import io
import tempfile
import unittest
from unittest.mock import patch
import wave

from bmo_ai import audio
from bmo_ai.conversation import TurnCancelled
from bmo_ai.greeting_cache import GreetingCache, GreetingSpec
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import synthetic_wav
from bmo_ai.wake_ack import (WakeAckCache, WakeAckSpec, WAKE_ACK_ID,
                            WAKE_ACK_TEXT, default_wake_ack_directory)


class FieldWakeAckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = integration_config()
        self.folder = self.root/'ack'
        for target in ('subprocess.Popen', 'socket.socket.connect', 'socket.socket.connect_ex',
                       'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(target, side_effect=AssertionError('Actual IO forbidden')))

    def producer(self, *, approved=True, spec=None):
        cache = GreetingCache(self.folder, spec or WakeAckSpec.from_config(self.config))
        source = synthetic_wav()
        try:cache.publish(source, approved=approved, simulated=True)
        finally:audio._discard(source, suppress=True)
        return cache

    def test_contract_uses_exact_text_and_separate_cache_fingerprint_without_io(self):
        with patch('bmo_ai.greeting_cache._open_directory', side_effect=AssertionError('Status cannot read/create')):
            ack = WakeAckCache(self.config, self.folder)
            status = ack.snapshot()
        self.assertEqual(WAKE_ACK_TEXT, "Yeah? What's up?")
        self.assertEqual(status['clip_id'], WAKE_ACK_ID)
        self.assertEqual(status['status'], 'NOT_CHECKED')
        self.assertFalse(status['automatic_generation'] or status['automatic_approval'] or status['player_owned'])
        self.assertEqual(ack.spec.fish_voice_id, self.config.fish_voice_id)
        self.assertEqual(ack.spec.gain, .25)
        self.assertNotEqual(ack.spec.fingerprint, GreetingSpec.from_config(self.config).fingerprint)
        self.assertEqual(default_wake_ack_directory(home=self.root), self.root/'.cache/bmo/wake-ack-v1')
        self.assertFalse(self.folder.exists())

    def test_missing_cache_is_visual_only_and_creates_no_files(self):
        ack = WakeAckCache(self.config, self.folder)
        with ack.load() as asset:self.assertIsNone(asset)
        self.assertEqual(ack.snapshot()['status'], 'ASSET_MISSING')
        self.assertFalse(self.folder.exists())

    def test_unapproved_corrupt_and_wrong_spec_bundles_are_visual_only_without_modification(self):
        for condition in ('unapproved', 'corrupt', 'wrong_spec'):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory(dir=self.root) as directory:
                self.folder = Path(directory)/'ack'
                self.producer(approved=condition != 'unapproved',
                    spec=GreetingSpec.from_config(self.config) if condition == 'wrong_spec' else None)
                path = self.folder/'greeting.cache'
                if condition == 'corrupt':path.write_bytes(b'not a bundle')
                original = path.read_bytes()
                ack = WakeAckCache(self.config, self.folder)
                with ack.load(simulate=True) as asset:self.assertIsNone(asset)
                self.assertNotEqual(ack.snapshot()['status'], 'APPROVED_READY')
                self.assertEqual(path.read_bytes(), original)

    def test_verified_asset_lives_only_in_existing_temporary_snapshot(self):
        self.producer();ack = WakeAckCache(self.config, self.folder)
        source = self.folder/'greeting.cache';original = source.read_bytes()
        with ack.load(simulate=True) as asset:
            self.assertTrue(asset.approved)
            self.assertNotEqual(asset.path, source);snapshot = asset.path
            self.assertTrue(snapshot.exists())
            self.assertEqual(ack.snapshot()['sha256'], asset.sha256)
        self.assertFalse(snapshot.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_live_never_uses_simulated_bundle_and_cancellation_never_becomes_fallback(self):
        self.producer();ack = WakeAckCache(self.config, self.folder)
        with ack.load(simulate=False) as asset:self.assertIsNone(asset)
        self.assertEqual(ack.snapshot()['status'], 'SIMULATED_ASSET_REJECTED')
        with self.assertRaises(TurnCancelled):
            with ack.load(simulate=True, cancel_check=lambda: True):self.fail('Cancelled load')

    def test_playback_failure_propagates_after_snapshot_cleanup_without_second_yield(self):
        self.producer();ack = WakeAckCache(self.config, self.folder)
        with self.assertRaisesRegex(RuntimeError, 'owned player failed'):
            with ack.load(simulate=True) as asset:
                snapshot = asset.path
                raise RuntimeError('owned player failed')
        self.assertFalse(snapshot.exists())

    def test_long_ack_is_rejected_without_playing_or_rewriting(self):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
            wav.writeframes(b'\0\1'*64000)
        source = self.root/'long-fixture.wav';source.write_bytes(buffer.getvalue());source.chmod(0o600)
        GreetingCache(self.folder, WakeAckSpec.from_config(self.config)).publish(
            source, approved=True, simulated=True)
        bundle = self.folder/'greeting.cache';original = bundle.read_bytes()
        ack = WakeAckCache(self.config, self.folder)
        with ack.load(simulate=True) as asset:self.assertIsNone(asset)
        self.assertEqual(ack.snapshot()['reason_code'], 'wake_ack_duration')
        self.assertFalse(ack.snapshot()['approved'])
        self.assertEqual(bundle.read_bytes(), original)


if __name__ == '__main__':unittest.main()
