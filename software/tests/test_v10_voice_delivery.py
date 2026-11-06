"""Owned v10 PCM/captions and reaction boundaries with synthetic local audio.

Approvals below apply only to temporary generated fixtures. No private voice,
provider, device, playback or user listening approval is used by this check.
"""
from pathlib import Path
import struct
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai import audio
from bmo_ai.character_director import CharacterDirector
from bmo_ai.conversation import TurnCancelled
from bmo_ai.expressive_voice import ExpressivePolicy
from bmo_ai.features.reply import FeatureClient
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_face import _public_snapshot
from bmo_ai.integration_session import IntegrationSession
from bmo_ai.integration_voice import IntegrationVoice
from bmo_ai.voice_pack_context import context_for
import test_voice_pack_v10 as pack_fixtures
from test_semantic_requests import Provider, envelope


class V10VoiceDeliveryTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'subprocess.Popen', 'bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials', 'bmo_ai.fish_tts.synthesize_wav',
                     'bmo_ai.local_piper.synthesize_wav'):
            self.enterContext(patch(name, side_effect=AssertionError('Real IO forbidden')))

    def pack(self, ids, *, approved=True):
        fixture = pack_fixtures.VoicePackV10Tests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        def transcript(rows, _payloads):
            for row in rows:
                row['spoken_text'] = context_for(row['clip_id'])['en']
        fixture.imported(ids, mutate=transcript)
        if approved:
            for clip in ids:
                fixture.approve(clip)
        return fixture.pack

    def voice(self, pack):
        events, deliveries = [], []
        def player(_command, *, on_start, cancel_check, **_):
            cancel_check(); on_start(); cancel_check()
        voice = IntegrationVoice(integration_config(), None, simulate=True, expressive=True,
            character_life=True, player=player,
            emit=lambda kind, **fields: events.append((kind, fields)))
        voice.event_pack = pack
        self.addCleanup(voice.close)
        def synth(text, check, **settings):
            check(); deliveries.append((text, settings.get('delivery')))
            path = audio._new_wav(audio.AudioSynthesisError)
            with wave.open(str(path), 'wb') as output:
                output.setparams((1, 2, 24000, 4800, 'NONE', 'not compressed'))
                output.writeframes(struct.pack('<h', 8000)*4800)
            return path, 'fish', False
        voice._synthesize = synth
        return voice, events, deliveries

    @staticmethod
    def metadata(reaction, *, emotion='neutral', message=''):
        plan = CharacterDirector().resolve(emotion, reaction, message=message)
        return dict(emotion=plan.face_emotion, emotion_validation='validated',
            reaction=plan.reaction_id, character_plan=plan.public(),
            caption_payload={'en':'A tiny discovery.', 'ko':'작은 발견이야.'})

    def test_raw44k_reaction_joins24k_answer_once_with_exact_caption_identity_and_gain(self):
        pack = self.pack(('RX06',))
        voice, events, deliveries = self.voice(pack)
        voice.expression_metadata = self.metadata('hmm')
        with patch.object(pack, 'load_event', wraps=pack.load_event) as select:
            voice.speak('A tiny discovery.')
            self.assertEqual(select.call_count, 1)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual([kind for kind, _ in events], ['prepared', 'started', 'ended'])
        expression = voice._cache['expression']
        self.assertEqual((expression['reaction'], expression['cached_reaction_id']), ('hmm', 'RX06'))
        self.assertEqual(expression['caption_payload'], {'en':'Hmm? A tiny discovery.', 'ko':'음? 작은 발견이야.'})
        row = pack.inventory()[0]
        self.assertEqual(expression['caption_asset'], dict(clip_id='RX06', candidate_id=row['id'],
            sha256=row['variants']['raw']['sha256']))
        with wave.open(str(voice._cache['path']), 'rb') as pcm:
            self.assertEqual(pcm.getframerate(), 24000)
            values = struct.unpack('<'+'h'*pcm.getnframes(), pcm.readframes(pcm.getnframes()))
        self.assertEqual(set(values[:2400]), {600})  # RAW 2400 * .25, exactly once.
        self.assertEqual(set(values[2400:]), {2000})  # Live 8000 * .25, exactly once.
        retained = voice._cache['path'].read_bytes()
        voice.repeat()
        self.assertEqual(voice._cache['path'].read_bytes(), retained)
        self.assertEqual(len(deliveries), 1)

    def test_v10_context_mappings_remove_fish_duplicate_and_use_single_output(self):
        mappings = (('chuckle','happy','RX01',''), ('gasp','surprised','RX02',''),
            ('aha','happy','RX07',''), ('ooh','surprised','REACTION_LINE_OOH_INTERESTING',''),
            ('oops','neutral','REACTION_LINE_OOPS_MY_MISTAKE',''))
        for reaction, emotion, clip, message in mappings:
            with self.subTest(reaction=reaction):
                pack = self.pack((clip,)); voice, events, deliveries = self.voice(pack)
                voice.expression_metadata = self.metadata(reaction, emotion=emotion, message=message)
                voice.speak('I was wrong about that.' if reaction == 'oops' else 'A tiny discovery.')
                self.assertEqual(voice._cache['expression']['cached_reaction_id'], clip)
                self.assertEqual(len(deliveries), 1)
                self.assertEqual(sum(kind == 'started' for kind, _ in events), 1)
                self.assertNotIn('[chuckling]', deliveries[0][1].text)
                self.assertNotIn('[gasping]', deliveries[0][1].text)

    def test_missing_new_cache_has_no_false_reaction_or_quota_charge(self):
        voice, _events, _deliveries = self.voice(self.pack(('RX01',)))
        voice.expression_metadata = self.metadata('hmm')
        voice.speak('A tiny discovery.')
        self.assertEqual(voice._cache['expression']['reaction'], 'none')
        self.assertIsNone(voice._expressive_policy._last_character_reaction)

    def test_cancel_after_reaction_load_discards_private_snapshot_before_synthesis(self):
        pack = self.pack(('RX06',));voice, events, deliveries = self.voice(pack)
        voice.expression_metadata = self.metadata('hmm');cancelled = Event();paths = []
        original = pack.load_event
        def load(*args, **kwargs):
            asset = original(*args, **kwargs);paths.append(asset.path);cancelled.set();return asset
        def check():
            if cancelled.is_set():raise TurnCancelled()
        voice.cancel_check = check
        with patch.object(pack,'load_event',side_effect=load), self.assertRaises(TurnCancelled):
            voice.speak('A tiny discovery.')
        self.assertFalse(paths[0].exists())
        self.assertEqual(deliveries,[])
        self.assertEqual([kind for kind,_ in events],['cancelled'])

    def test_disabled_or_revoked_pack_reaction_cannot_fallback_to_fish_or_old_bank(self):
        for action in ('disabled', 'revoked'):
            with self.subTest(action=action):
                if action == 'disabled':
                    pack = self.pack(('RX01',))
                    pack.enable_event('reaction_chuckle',enabled=False)
                else:
                    pack = self.pack(pack_fixtures.V10_IDS,approved=False)
                    pack.accept_full_use(user_accepted=True);pack.disable_batch()
                voice, _events, deliveries = self.voice(pack)
                voice.character_bank = Mock()
                voice.expression_metadata = self.metadata('chuckle',emotion='happy')
                voice.speak('A tiny discovery.')
                self.assertEqual(voice._cache['expression']['reaction'],'none')
                self.assertNotIn('[chuckling]', deliveries[0][1].text)
                voice.character_bank.select.assert_not_called()
                self.assertIsNone(voice._expressive_policy._last_character_reaction)

    def test_nonmute_volume_change_applies_next_owned_playback_without_mutating_current_or_cache(self):
        voice, _events, _deliveries = self.voice(None)
        voice.simulate = False  # Injected player inspects actual prepared path; never starts ALSA.
        observed = []
        def sample(path):
            with wave.open(str(path),'rb') as pcm:
                return struct.unpack('<h',pcm.readframes(1))[0]
        def player(command, *, on_start, cancel_check, **_):
            cancel_check();on_start();observed.append(sample(command[-1]))
            if len(observed) == 1:
                voice.volume.set(level=.5)
                self.assertEqual(sample(command[-1]),2000)
            cancel_check()
        voice._player = player
        voice.speak('First answer.');retained = voice._cache['path'].read_bytes()
        voice.repeat()
        self.assertEqual(observed,[2000,4000])
        self.assertEqual(voice._cache['path'].read_bytes(),retained)

    def test_context_quota_repeat_suppression_and_serious_guard_at_low_intensity(self):
        policy = ExpressivePolicy(); got = []
        for reaction in ('chuckle','hmm','aha','chuckle','aha','hmm','aha'):
            meta = self.metadata(reaction, emotion='happy' if reaction != 'hmm' else 'neutral')
            value = policy.plan('A tiny discovery.', meta, model='s2.1-pro-free')
            got.append(value.reaction); policy.completed(value)
        self.assertEqual(got, ['chuckle','none','none','none','aha','none','none'])
        for reaction, speech in (('chuckle','Haha, that is funny.'), ('chuckle','Ha ha! That is funny.'),
                ('chuckle','Ha-ha! Good one.'), ('chuckle','Heh, a tiny victory.'), ('aha','Aha! A discovery.'),
                ('ooh','Ooh, an idea.'), ('hmm','Hmm, a mystery.'), ('oops','Oops, my mistake.'),
                ('tada','We can try it.'), ('oops','You made a mistake.')):
            with self.subTest(reaction=reaction, speech=speech):
                meta = self.metadata(reaction, emotion='happy' if reaction in ('chuckle','aha','tada') else 'neutral')
                self.assertEqual(ExpressivePolicy().plan(speech,meta,model='s2.1-pro-free').reaction,'none')
        serious = self.metadata('chuckle', emotion='happy', message='My mom died.')
        self.assertEqual(ExpressivePolicy().plan('I am here.',serious,model='s2.1-pro-free').reaction,'none')

    def test_written_korean_laughter_is_not_read_by_live_synthesizer(self):
        voice, _events, deliveries = self.voice(None)
        voice.speak('ㅋㅋㅋ That little hat. ㅎㅎ')
        self.assertEqual(deliveries[0][0], 'That little hat.')

    def test_browser_caption_identity_is_allowlisted_and_only_visible_while_speaking(self):
        identity = dict(clip_id='M04',candidate_id='M04',sha256='a'*64)
        state = dict(phase='speaking',playback='started',caption=dict(mode='ko',text='이 곡이야!'),
            caption_asset=identity)
        self.assertEqual(_public_snapshot(state)['caption_asset'],identity)
        for changes in (dict(playback='prepared'),dict(playback='ended'),dict(playback='cancelled'),
                dict(playback='error'),dict(phase='listening'),dict(caption=dict(mode='off',text='이 곡이야!')),
                dict(caption_asset=dict(identity,path='/private/audio.wav')),
                dict(caption_asset=dict(identity,clip_id='../../audio.wav')),
                dict(caption_asset=dict(identity,sha256='invalid'))):
            with self.subTest(changes=changes):
                self.assertNotIn('caption_asset',_public_snapshot(dict(state,**changes)))

    def session(self, pack):
        provider = Provider([envelope(speech='An ordinary answer.', captions={'en':'','ko':'평범한 답이야.'})])
        ready, allow, started, release = Event(), Event(), Event(), Event()
        def player(_command, *, on_start, cancel_check, **_):
            ready.set()
            while not allow.wait(.002): cancel_check()
            cancel_check(); on_start(); started.set()
            while not release.wait(.002): cancel_check()
            cancel_check()
        voice = IntegrationVoice(integration_config(),provider,simulate=True,player=player,
            emit=lambda *_a,**_kw:None,expressive=True,character_life=True)
        voice.event_pack = pack; voice._synthesize = Mock(wraps=voice._synthesize)
        session = IntegrationSession(integration_config(),provider,voice=voice,character_life=True,
            reply_client=FeatureClient(provider,character_life=True))
        session.set_subtitles('ko')
        def close(): allow.set(); release.set(); session.close()
        self.addCleanup(close)
        return dict(session=session,voice=voice,provider=provider,ready=ready,allow=allow,started=started,release=release)

    def settle(self, f):
        deadline = time.monotonic()+3
        while f['session'].busy and time.monotonic()<deadline:
            f['session'].poll(); time.sleep(.002)
        f['session'].poll(); self.assertFalse(f['session'].busy)

    def test_random_event_caption_and_asset_match_only_actual_start_and_clear_on_end_off_cancel(self):
        pack = self.pack(('M04','M05','M06')); f = self.session(pack); session=f['session']
        selected = []
        for action in ('end','cancel','end'):
            for key in ('ready','allow','started','release'): f[key].clear()
            self.assertTrue(session.start_cached_event('music_selected')); self.assertTrue(f['ready'].wait(2))
            self.assertEqual(session.snapshot()['caption']['text'],'')
            self.assertNotIn('caption_asset',session.snapshot())
            f['allow'].set(); self.assertTrue(f['started'].wait(2))
            active = session.snapshot(); identity = active['caption_asset']; selected.append(identity['clip_id'])
            self.assertEqual(active['caption']['text'],context_for(identity['clip_id'])['ko'])
            self.assertEqual(_public_snapshot(active)['caption_asset'],identity)
            row = next(row for row in pack.inventory() if row['id'] == identity['candidate_id'])
            self.assertEqual(identity['sha256'],row['variants']['raw']['sha256'])
            session.set_subtitles('en')
            self.assertEqual(session.snapshot()['caption']['text'],context_for(identity['clip_id'])['en'])
            session.set_subtitles('off'); self.assertEqual(session.snapshot()['caption']['text'],'')
            self.assertNotIn('caption_asset',session.snapshot()); session.set_subtitles('ko')
            if action == 'cancel': session.cancel()
            else: f['release'].set()
            self.settle(f)
            self.assertEqual(session.snapshot()['caption']['text'],'')
            self.assertNotIn('caption_asset',session.snapshot())
        self.assertEqual(len(set(selected)),3)
        self.assertEqual(f['voice']._synthesize.call_count,0)
        self.assertEqual(f['provider'].requests,[])
        self.assertFalse(f['voice'].has_cache)
        for key in ('ready','allow','started','release'): f[key].clear()
        self.assertTrue(session.start('A new question.')); self.assertTrue(f['ready'].wait(2))
        self.assertNotIn('caption_asset',session.snapshot()); f['allow'].set(); self.assertTrue(f['started'].wait(2))
        self.assertEqual(session.snapshot()['caption']['text'],'평범한 답이야.')
        self.assertNotIn('caption_asset',session.snapshot()); f['release'].set();self.settle(f)


if __name__ == '__main__': unittest.main()
