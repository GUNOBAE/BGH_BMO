"""Real v10 pack/worker/player paths with synthetic PCM and forbidden IO."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import wave
import zipfile

from bmo_ai import fish_tts
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession, SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.voice_bank import VoiceBankError
from bmo_ai.voice_pack import OrganizedVoicePack, V10_SCHEMA
from bmo_ai.wake_ack import WakeAckCache
from test_voice_pack_v10 import pcm


class V10OwnedPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-v10-owned-')))
        self.config = integration_config()
        self.pack = OrganizedVoicePack(self.root/'pack')
        rows = []
        file = self.root/'source.zip'
        with zipfile.ZipFile(file, 'w') as archive:
            for clip, text, seconds in [('RX01', '', .1), ('E07', 'The network is away.', .1),
                                       ('wake_ack_01', "Yeah? What's up?", 1.76)]:
                raw = pcm(seconds)
                sha = hashlib.sha256(raw).hexdigest()
                path = 'audio/'+clip+'.wav'
                rows.append(dict(clip_id=clip, spoken_text=text, file=path, sha256=sha,
                    baked_gain=1., requested_model='drama-3', voice_id=fish_tts.AARON_BMO_VOICE_ID,
                    provenance=dict(requested_model='drama-3', server_model_attestation='NOT_AVAILABLE'),
                    source_raw_sha256=sha, approvals=dict(listening_approved=False)))
                archive.writestr(path, raw)
            archive.writestr('catalogue.json', json.dumps(dict(schema=V10_SCHEMA,
                not_runtime_compatible_manifest=True, entries=rows)))
        self.pack.import_zip(file, expected_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        self.rows = {row['clip_id']:row for row in self.pack.inventory()}
        for name in ('socket.socket.connect', 'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.audio.record_wav',
                     'bmo_ai.audio.transcribe', 'subprocess.Popen'):
            self.enterContext(patch(name, side_effect=AssertionError('Real IO forbidden')))

    def sha(self, clip):
        return self.rows[clip]['variants']['raw']['sha256']

    def approve(self, clip):
        self.pack.approve(clip, self.sha(clip), listening_approved=True)

    @staticmethod
    def player(command, *, on_start, cancel_check, **_):
        cancel_check()
        on_start()
        cancel_check()

    def voice(self, **settings):
        events = []
        voice = IntegrationVoice(self.config, SimulatedClient(), player=self.player,
            emit=lambda kind, **data:events.append((kind, data)), **settings)
        voice.event_pack = self.pack
        self.addCleanup(voice.close)
        return voice, events

    def test_pack_ack_uses_real_cache_model_and_unapproved_never_uses_legacy_bypass(self):
        ack = WakeAckCache(self.config, self.root/'legacy', organized_pack=self.pack)
        with patch.object(ack._cache, 'load', side_effect=AssertionError('Legacy bypass forbidden')):
            with ack.load() as asset:
                self.assertIsNone(asset)
            self.assertEqual(ack.snapshot()['status'], 'ASSET_UNAPPROVED')
            self.approve('wake_ack_01')
            with ack.load() as asset:
                self.assertEqual(asset.sha256, self.sha('wake_ack_01'))
                self.assertEqual(asset.baked_gain, 1.)
                path = asset.path
                state = ack.snapshot()
                self.assertEqual(state['source_model'], 'drama-3')
                self.assertEqual(state['asset_source'], 'organized_voice_pack')
                self.assertEqual(ack.spec.fish_model, 's2.1-pro-free')
            self.assertFalse(path.exists())
        self.assertEqual(self.pack.snapshot()['played'], 0)

    def test_pack_ack_cancellation_and_caller_failure_propagate_once(self):
        self.approve('wake_ack_01')
        ack = WakeAckCache(self.config, organized_pack=self.pack)
        def cancelled(): raise TurnCancelled()
        with self.assertRaises(TurnCancelled):
            with ack.load(cancel_check=cancelled): self.fail('cancelled before asset')
        failure = VoiceBankError('caller_failed')
        with self.assertRaises(VoiceBankError) as caught:
            with ack.load() as asset:
                path = asset.path
                raise failure
        self.assertIs(caught.exception, failure)
        self.assertFalse(path.exists())

    def test_owned_event_and_manual_rx01_play_no_tts_gain_once_and_keep_repeat(self):
        synth = Mock(side_effect=lambda *_a, **_kw:synthetic_wav())
        voice, events = self.voice(fish_synthesizer=synth)
        voice.speak('Original dialogue.')
        self.assertEqual(synth.call_count, 1)
        previous = voice._cache['path']
        previous_bytes = previous.read_bytes()
        self.approve('E07')
        events.clear()
        peaks = []
        def player(command, *, on_start, cancel_check, **_):
            with wave.open(command[-1], 'rb') as wav:
                raw = wav.readframes(wav.getnframes())
                peaks.append(max(abs(int.from_bytes(raw[i:i+2], 'little', signed=True))
                                 for i in range(0, len(raw), 2)))
            cancel_check();on_start();cancel_check()
        voice._player = player
        with patch.object(self.pack, 'mark_played', wraps=self.pack.mark_played) as marked:
            self.assertTrue(voice.play_event('error_network'))
            self.assertTrue(voice.play_candidate('RX01', self.sha('RX01')))
            self.assertEqual(marked.call_count, 2)
            self.assertTrue(voice.repeat())
            self.assertEqual(marked.call_count, 2)
        self.assertEqual(peaks[:2], [600, 600])  # raw2400 * output.25, once.
        self.assertEqual(synth.call_count, 1)
        self.assertEqual(previous.read_bytes(), previous_bytes)
        prepared = [data for kind,data in events if kind == 'prepared']
        self.assertEqual(prepared[0]['cache_source_model'], 'drama-3')
        self.assertEqual(prepared[1]['caption_payload'], {'en':'', 'ko':''})
        self.assertTrue(prepared[1]['voice_pack_review_only'])
        state = self.pack.snapshot()
        self.assertEqual((state['played'], state['approved'], state['listened']), (2,1,1))

    def test_session_manual_review_is_real_worker_without_gpt_history_or_repeat(self):
        voice, _ = self.voice()
        client = SimulatedClient()
        session = IntegrationSession(self.config, client, voice=voice, simulate=True)
        self.addCleanup(session.close)
        self.assertFalse(session.start_cached_candidate('RX01', 'wrong'))
        self.assertTrue(session.start_cached_candidate('RX01', self.sha('RX01')))
        deadline = time.monotonic()+3
        while session.busy and time.monotonic() < deadline:
            session.poll();time.sleep(.002)
        session.poll()
        self.assertFalse(session.busy)
        self.assertEqual(session.snapshot()['playback'], 'ended')
        self.assertEqual(client.requests, [])
        self.assertEqual(session.brain.history, [])
        self.assertFalse(voice.has_cache)
        self.assertEqual(self.pack.snapshot()['played'], 1)
        self.assertEqual(self.pack.snapshot()['approved'], 0)

    def test_deferred_preparation_has_no_player_start_or_cache_mutation_and_cancel_clears(self):
        synth = Mock(side_effect=lambda *_a, **_kw:synthetic_wav())
        voice, events = self.voice(fish_synthesizer=synth)
        voice.speak('Previous reply.')
        cached = voice._cache
        events.clear()
        started = Mock()
        voice.defer_feature_preparation()
        voice.speak('Preparation will be cached after preview.', on_playback_start=started)
        self.assertEqual(events, [('ended', {'source':'deferred_feature_preparation'})])
        self.assertEqual(synth.call_count, 1)
        self.assertIs(voice._cache, cached)
        started.assert_not_called()
        voice.defer_feature_preparation()
        voice.invalidate_pending()
        voice.speak('Next independent question.')
        self.assertEqual(synth.call_count, 2)


if __name__ == '__main__':
    unittest.main()
