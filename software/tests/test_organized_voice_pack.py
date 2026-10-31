import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import wave
import zipfile

from bmo_ai import audio, fish_tts
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.integration_session import IntegrationSession
from bmo_ai.master_volume import MasterVolume
from bmo_ai.voice_bank import VoiceBankError
from bmo_ai.voice_pack import OrganizedVoicePack, V8_ARCHIVE_SHA256


def pcm(level=10000, frames=1600):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
        wav.writeframes(struct.pack('<h', level)*frames)
    return out.getvalue()


def first_sample(path):
    with wave.open(str(path), 'rb') as wav:
        return struct.unpack('<h', wav.readframes(1))[0]


class OrganizedVoicePackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-pack-')))
        self.pack = OrganizedVoicePack(self.root/'candidate')
        self.raw, self.preview = pcm(), pcm(2500)

    def archive(self, *, rows=None, extra=(), name='pack.zip'):
        raw_hash = hashlib.sha256(self.raw).hexdigest()
        preview_hash = hashlib.sha256(self.preview).hexdigest()
        if rows is None:
            rows = [dict(candidate_id='PH_REVIEW_02_A', clip_id='PH_REVIEW_02',
                text='Ta-da! A tiny moment, all saved.', source_model='Drama3',
                voice_id=fish_tts.AARON_BMO_VOICE_ID,
                files=[dict(path='RAW/review.wav', sha256=raw_hash, baked_gain=1.),
                       dict(path='LEGACY_PREVIEW_GAIN025/review.wav', sha256=preview_hash, baked_gain=.25)])]
        path = self.root/name
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('catalogue.json', json.dumps(dict(candidates=rows)))
            archive.writestr('RAW/review.wav', self.raw)
            archive.writestr('LEGACY_PREVIEW_GAIN025/review.wav', self.preview)
            for key, value in extra:
                archive.writestr(key, value)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def imported(self):
        path, digest = self.archive()
        self.pack.import_zip(path, expected_sha256=digest)
        return digest

    def approved(self, variant='preview_gain025'):
        self.imported()
        row = self.pack.inventory()[0]
        digest = row['variants'][variant]['sha256']
        self.pack.approve(row['id'], digest, listening_approved=True)
        return row['id'], digest

    def test_absent_pack_remains_independent_asset_pending_without_io_generation(self):
        self.assertEqual(self.pack.inventory(), [])
        state = self.pack.snapshot()
        self.assertEqual(state['status'], 'ASSET_PENDING')
        self.assertFalse(state['automatic_approval'])
        self.assertFalse(state['wake_ack_available'])
        self.assertFalse((self.root/'candidate').exists())

    def test_hash_pin_pcm_provenance_private_staging_and_idempotence(self):
        digest = self.imported()
        state = self.pack.snapshot()
        self.assertEqual(state['archive_sha256'], digest)
        self.assertEqual(state['expected_v8_sha256'], V8_ARCHIVE_SHA256)
        self.assertEqual(state['approved'], 0)
        row = self.pack.inventory()[0]
        self.assertEqual(row['source_model'], 'drama-3')
        self.assertEqual(row['variants']['raw']['baked_gain'], 1.)
        self.assertEqual(row['variants']['preview_gain025']['baked_gain'], .25)
        self.assertEqual((self.root/'candidate').stat().st_mode&0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode&0o777 == 0o600 for p in (self.root/'candidate').iterdir()))
        before = {p.name:p.read_bytes() for p in (self.root/'candidate').iterdir()}
        self.pack.import_zip(self.root/'pack.zip', expected_sha256=digest)
        self.assertEqual(before, {p.name:p.read_bytes() for p in (self.root/'candidate').iterdir()})

    def test_approval_requires_exact_individual_sha_and_event_cannot_misroute(self):
        self.imported()
        row = self.pack.inventory()[0]
        digest = row['variants']['preview_gain025']['sha256']
        self.assertIsNone(self.pack.select_event('photo_saved'))
        self.assertIsNone(self.pack.select_event('photo_saved', row['id']))
        self.assertIsNone(self.pack.load_event('photo_saved'))
        with self.assertRaises(VoiceBankError):
            self.pack.approve(row['id'], digest)
        with self.assertRaises(VoiceBankError):
            self.pack.approve(row['id'], '0'*64, listening_approved=True)
        self.pack.approve(row['id'], digest, listening_approved=True)
        self.assertEqual(self.pack.select_event('photo_saved'), row['id'])
        self.assertIsNone(self.pack.select_event('photo_ready'))
        self.assertIsNone(self.pack.select_event('video_saved'))
        with self.assertRaises(VoiceBankError):
            self.pack.select_event('video_saved', row['id'])
        with self.assertRaises(VoiceBankError):
            self.pack.select_event('boot')
        with self.pack.load_event('photo_saved') as asset:
            path = asset.path
            self.assertEqual(path.read_bytes(), self.preview)
            self.assertEqual(asset.source_model, 'drama-3')
            self.assertEqual(asset.baked_gain, .25)
        self.assertFalse(path.exists())

    def test_archive_path_escape_and_zip_symlink_are_rejected_before_staging(self):
        badlink = zipfile.ZipInfo('RAW/link.wav')
        badlink.create_system = 3
        badlink.external_attr = (stat.S_IFLNK|0o777)<<16
        for index, entry in enumerate((('../escape.wav', b'bad'), ('/absolute', b'bad'),
                                        ('RAW\\escape.wav', b'bad'), (badlink, b'review.wav'))):
            path, digest = self.archive(extra=(entry,), name=f'bad{index}.zip')
            with self.subTest(index=index), self.assertRaises(VoiceBankError):
                self.pack.import_zip(path, expected_sha256=digest)
        self.assertFalse((self.root/'candidate').exists())

    def test_source_symlink_wrong_hash_missing_provenance_and_bad_pcm_do_not_publish(self):
        path, digest = self.archive()
        link = self.root/'linked.zip';link.symlink_to(path)
        with self.assertRaises(VoiceBankError):
            self.pack.import_zip(link, expected_sha256=digest)
        with self.assertRaises(VoiceBankError):
            self.pack.import_zip(path, expected_sha256='0'*64)
        rows = [dict(clip_id='PH_REVIEW_02', text='A saved picture.',
                     files=[dict(path='RAW/review.wav', sha256=hashlib.sha256(self.raw).hexdigest(), baked_gain=1.)])]
        path, digest = self.archive(rows=rows, name='missing.zip')
        with self.assertRaises(VoiceBankError):
            self.pack.import_zip(path, expected_sha256=digest)
        self.assertFalse((self.root/'candidate/pack.json').exists())

    def test_tampered_pcm_and_manifest_never_reuse_previous_approval(self):
        _, digest = self.approved()
        target = self.root/'candidate'/(digest+'.wav')
        target.write_bytes(pcm(1000))
        with self.assertRaises(VoiceBankError):
            self.pack.load_event('photo_saved')
        manifest = self.root/'candidate/pack.json'
        data = json.loads(manifest.read_text())
        data['candidates'][0]['source_model'] = 'sneaky-live-model'
        manifest.write_text(json.dumps(data))
        with self.assertRaises(VoiceBankError):
            self.pack.inventory()

    def test_second_import_preserves_existing_pack(self):
        self.imported()
        before = (self.root/'candidate/pack.json').read_bytes()
        path, digest = self.archive(extra=(('notes.txt', b'new candidate'),), name='second.zip')
        with self.assertRaises(VoiceBankError):
            self.pack.import_zip(path, expected_sha256=digest)
        self.assertEqual((self.root/'candidate/pack.json').read_bytes(), before)

    def test_approved_event_uses_actual_pcm_text_single_player_and_repeat_without_tts(self):
        self.approved()
        levels = [];events = [];calls = []
        def player(command, *, on_start, cancel_check, **_):
            levels.append(first_sample(command[-1]));calls.append(command)
            cancel_check();on_start();cancel_check()
        synth = Mock(side_effect=AssertionError('A cache event must not synthesize'))
        voice = IntegrationVoice(integration_config(), None, emit=lambda kind, **fields:events.append((kind, fields)),
                                 fish_synthesizer=synth, player=player, character_life=True)
        self.addCleanup(voice.close)
        voice.event_pack = self.pack
        voice.queue_event('photo_saved', 'request_current')
        self.assertTrue(voice.speak('A generic success sentence.'))
        self.assertEqual(levels, [2500])
        started = next(fields for kind, fields in events if kind == 'started')
        self.assertEqual(started['caption_payload'], {'en':'Ta-da! A tiny moment, all saved.', 'ko':''})
        self.assertEqual(started['cache_source_model'], 'drama-3')
        self.assertEqual(voice.config.fish_model, 's2.1-pro-free')
        voice.volume.set(level=.5)
        self.assertTrue(voice.repeat())
        self.assertEqual(levels, [2500, 5000])
        self.assertEqual(len(calls), 2);synth.assert_not_called()

    def test_cache_only_event_never_falls_back_and_cancel_discards_old_queue(self):
        synth = Mock(side_effect=lambda *a, **kw:synthetic_wav())
        player = Mock(side_effect=lambda command, on_start, **kwargs:on_start())
        voice = IntegrationVoice(integration_config(), None, emit=lambda *_a, **_k:None,
                                 fish_synthesizer=synth, player=player)
        self.addCleanup(voice.close)
        voice.event_pack = self.pack
        self.assertFalse(voice.play_event('video_saved'))
        synth.assert_not_called();player.assert_not_called()
        self.approved()
        voice.queue_event('photo_saved')
        voice.invalidate_pending()
        voice.speak('A new unrelated turn.')
        synth.assert_called_once()

    def test_raw_variant_uses_start_level_once_and_muted_repeat_is_silent(self):
        self.approved('raw')
        values = []
        def player(command, *, on_start, **_):
            values.append(first_sample(command[-1]));on_start()
        voice = IntegrationVoice(integration_config(), None, emit=lambda *_a, **_k:None, player=player)
        self.addCleanup(voice.close);voice.event_pack = self.pack
        voice.queue_event('photo_saved')
        self.assertTrue(voice.speak('A saved photo.'))
        self.assertEqual(values, [2500])
        voice.volume.set(muted=True)
        voice.repeat()
        self.assertEqual(values, [2500, 0])

    def test_source_model_provenance_change_invalidates_old_sha_approval(self):
        self.approved()
        manifest = self.root/'candidate/pack.json'
        data = json.loads(manifest.read_text())
        data['candidates'][0]['source_model'] = 's2.1-pro-free'
        manifest.write_text(json.dumps(data))
        self.assertFalse(self.pack.inventory()[0]['approved'])
        self.assertIsNone(self.pack.select_event('photo_saved'))
        self.assertIsNone(self.pack.load_event('photo_saved'))

    def test_session_cached_event_uses_same_worker_and_preserves_dialogue_repeat(self):
        self.approved()
        samples = []
        synth = Mock(side_effect=lambda *_a, **_k:synthetic_wav())
        def player(command, *, on_start, **_):
            samples.append(first_sample(command[-1]));on_start()
        config = integration_config()
        voice = IntegrationVoice(config, None, emit=lambda *_a, **_k:None,
                                 fish_synthesizer=synth, player=player)
        voice.speak('The last successful answer.')
        saved = voice._cache['path'].read_bytes()
        voice.event_pack = self.pack
        brain = Mock();brain.history=[]
        session = IntegrationSession(config, Mock(), voice=voice, brain=brain)
        self.addCleanup(session.close)
        def finish():
            until = time.monotonic()+2
            while session.busy and time.monotonic() < until:
                session.poll();time.sleep(.002)
            session.poll()
            self.assertFalse(session.busy)
        self.assertTrue(session.start_cached_event('photo_saved'))
        finish()
        self.assertEqual(session.snapshot()['playback'], 'ended')
        self.assertEqual(session.snapshot()['source'], 'voice_pack')
        self.assertEqual(voice._cache['path'].read_bytes(), saved)
        self.assertTrue(session.repeat());finish()
        self.assertEqual(synth.call_count, 1)
        brain.ask.assert_not_called()
        self.assertEqual(brain.history, [])
        self.assertTrue(session.start_cached_event('video_saved'));finish()
        self.assertEqual(session.snapshot()['playback'], 'error')
        self.assertEqual(synth.call_count, 1)


class MasterVolumeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-volume-')))

    def test_raw_preview_default_zero_mute_and_increase_do_not_change_original(self):
        source = self.root/'preview.wav';source.write_bytes(pcm(2500))
        original = source.read_bytes();volume = MasterVolume()
        with volume.prepare(source, baked_gain=.25) as path:
            self.assertEqual(path, source);self.assertEqual(first_sample(path), 2500)
        for level, muted, expected in ((.5,False,5000), (1.,False,10000), (0.,False,0), (.8,True,0)):
            volume.set(level=level, muted=muted)
            with volume.prepare(source, baked_gain=.25) as path:
                self.assertEqual(first_sample(path), expected)
            self.assertFalse(path.exists())
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(volume.snapshot()['effective_level'], 0.)

    def test_invalid_controls_rejected_without_state_change(self):
        volume = MasterVolume();before = volume.snapshot()
        for value in (-.1, 1.1, True, float('nan'), '0.5'):
            with self.subTest(value=value), self.assertRaises(audio.AudioValidationError):
                volume.set(level=value)
        with self.assertRaises(audio.AudioValidationError):
            volume.set(muted=1)
        self.assertEqual(volume.snapshot(), before)

    def test_pinned_music_fd_scales_from_current_position_and_keeps_source(self):
        source = self.root/'music.wav';source.write_bytes(pcm(12000, frames=16000))
        volume = MasterVolume(.5)
        descriptor = os.open(source, os.O_RDONLY)
        try:
            with volume.prepare_music_fd(descriptor, start_seconds=.5) as played:
                self.assertEqual(first_sample(played), 6000)
                with wave.open(str(played), 'rb') as wav:
                    self.assertEqual(wav.getnframes(), 8000)
            self.assertFalse(played.exists())
            self.assertEqual(first_sample(source), 12000)
        finally:
            os.close(descriptor)


class ProviderAdmissionTests(unittest.TestCase):
    def test_auth_error_latches_without_auto_request_and_explicit_reset_recovers(self):
        synth = Mock(side_effect=[audio.AudioSynthesisError('Safe synthetic failure.', 'fish_authentication'), synthetic_wav()])
        player = Mock(side_effect=lambda command, on_start, **kwargs:on_start())
        voice = IntegrationVoice(integration_config(), None, emit=lambda *_a, **_k:None,
                                 fish_synthesizer=synth, player=player)
        self.addCleanup(voice.close)
        with self.assertRaises(audio.AudioSynthesisError):
            voice.speak('First.')
        self.assertTrue(voice.provider_snapshot()['blocked'])
        with self.assertRaises(audio.AudioSynthesisError):
            voice.speak('Second.')
        self.assertEqual(synth.call_count, 1)
        voice.reset_provider_error();voice.speak('After explicit recheck.')
        self.assertEqual(synth.call_count, 2)
        self.assertFalse(voice.provider_snapshot()['blocked'])

    def test_provider_status_codes_stay_distinct_without_payloads(self):
        voice = IntegrationVoice(integration_config(), None, emit=lambda *_a, **_k:None)
        self.addCleanup(voice.close)
        for code in ('fish_authentication', 'fish_balance', 'fish_permission', 'fish_rate_limit', 'fish_connection', 'fish_timeout', 'device_unavailable'):
            voice._provider_failure(code)
            state = voice.provider_snapshot()
            self.assertEqual(state['code'], code)
            self.assertNotIn('key', state)
            self.assertEqual(state['blocked'], code in ('fish_authentication', 'fish_balance', 'fish_permission', 'fish_rate_limit'))
