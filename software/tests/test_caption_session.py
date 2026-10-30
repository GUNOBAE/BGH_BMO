"""Actual one-call session/voice caption timing; no devices or paid providers."""
import json
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.reply import FeatureClient, PHOTO_PREPARATION
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_session import IntegrationSession
from bmo_ai.integration_voice import IntegrationVoice
from test_semantic_requests import Provider, envelope


class CaptionSessionTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect','subprocess.Popen','bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials','bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(name,side_effect=AssertionError('Actual IO forbidden')))
        self.sessions=[]

    def tearDown(self):
        for fixture in self.sessions:
            fixture['allow'].set();fixture['release'].set();fixture['session'].close()

    def fixture(self, replies, *, mode='auto', on_reply=None):
        provider=Provider(replies);config=integration_config()
        prepared,allow,started,release=Event(),Event(),Event(),Event()
        def player(_command, *, on_start,cancel_check,**kwargs):
            cancel_check();prepared.set()
            while not allow.wait(.002):cancel_check()
            cancel_check();on_start();started.set()
            while not release.wait(.002):cancel_check()
            cancel_check()
        voice=IntegrationVoice(config,provider,simulate=True,player=player,
            emit=lambda *_a,**_kw:None,expressive=True,character_life=True)
        voice._synthesize=Mock(wraps=voice._synthesize)
        view=FeatureClient(provider,character_life=True)
        view.set_context({'camera':'explicit_current_user_photo_only','subtitle_mode':'off',
                          'subtitle_last_language':'en'})
        session=IntegrationSession(config,provider,voice=voice,emotion_mode=mode,
            character_life=True,reply_client=view,on_reply=on_reply)
        result=dict(session=session,provider=provider,voice=voice,view=view,config=config,
            prepared=prepared,allow=allow,started=started,release=release)
        self.sessions.append(result)
        return result

    def settle(self, fixture):
        session=fixture['session'];deadline=time.monotonic()+3
        while session.busy and time.monotonic()<deadline:
            session.poll();time.sleep(.002)
        session.poll();self.assertFalse(session.busy)

    def arm(self, fixture):
        for name in ('prepared','allow','started','release'):fixture[name].clear()

    def prepared(self, fixture, message):
        self.assertTrue(fixture['session'].start(message))
        self.assertTrue(fixture['prepared'].wait(2))
        self.assertEqual(fixture['session'].snapshot()['caption']['text'],'')

    def start_player(self, fixture):
        fixture['allow'].set();self.assertTrue(fixture['started'].wait(2))
        snapshot=fixture['session'].snapshot()
        self.assertEqual((snapshot['phase'],snapshot['playback']),('speaking','started'))
        return snapshot

    def finish(self, fixture):
        fixture['release'].set();self.settle(fixture)
        self.assertEqual(fixture['session'].snapshot()['caption']['text'],'')

    def test_korean_translation_appears_only_at_actual_start_with_english_voice_preserved(self):
        value=envelope(speech='A tiny adventure starts here.',
            captions={'en':'An unrelated fixture must not replace native speech.',
                      'ko':'작은 모험이 여기서 시작돼.'})
        f=self.fixture([value]);session=f['session'];session.set_subtitles('ko')
        voice_settings=(f['config'].language,f['config'].fish_voice_id,
                        f['config'].fish_name_mode,f['config'].fish_gain,f['config'].fish_delivery)
        self.prepared(f,'Tell me about today.')
        active=self.start_player(f)
        self.assertEqual(active['caption'],{'mode':'ko','language':'ko','text':'작은 모험이 여기서 시작돼.'})
        self.assertEqual(len(f['provider'].requests),1)
        self.assertEqual((f['config'].language,f['config'].fish_voice_id,
            f['config'].fish_name_mode,f['config'].fish_gain,f['config'].fish_delivery),voice_settings)
        self.assertEqual(f['provider'].requests[0]['max_output_tokens'],320)
        session.set_subtitles('en')
        self.assertEqual(session.snapshot()['caption']['text'],value['speech'])
        session.set_subtitles('off');self.assertEqual(session.snapshot()['caption']['text'],'')
        session.set_subtitles('on');self.assertEqual(session.subtitle_snapshot()['mode'],'en')
        encoded=json.dumps(list(session.events),ensure_ascii=False)
        self.assertNotIn(value['speech'],encoded)
        self.assertNotIn(value['captions']['ko'],encoded)
        self.finish(f)

    def test_missing_or_invalid_translation_does_not_block_basic_voice(self):
        for captions in ({'en':'','ko':''},{'en':'','ko':'<unsafe>'},None):
            with self.subTest(captions=captions):
                f=self.fixture([envelope(captions=captions)]);f['session'].set_subtitles('ko')
                self.prepared(f,'A normal question.')
                self.assertEqual(self.start_player(f)['caption']['text'],'')
                self.finish(f)
                self.assertTrue(f['voice'].has_cache)
                self.assertEqual(f['voice']._synthesize.call_count,1)

    def test_mixed_script_names_do_not_overwrite_the_requested_translation(self):
        cases=[('That word is 사과.','ko',{'en':'That word is 사과.','ko':'그 단어는 사과야.'}),
               ('나는 BMO야.','en',{'en':'I am BMO.','ko':'나는 BMO야.'})]
        for speech,mode,captions in cases:
            with self.subTest(mode=mode):
                f=self.fixture([envelope(speech=speech,captions=captions)])
                f['session'].set_subtitles(mode)
                self.prepared(f,'Translate the answer captions.')
                self.assertEqual(self.start_player(f)['caption']['text'],captions[mode])
                self.finish(f)

    def test_mode_command_commits_at_playback_and_cancelled_preparation_does_not_commit(self):
        value=envelope(kind='subtitles',disposition='execute',subtitle_mode='ko')
        f=self.fixture([value,value]);session=f['session']
        self.prepared(f,'Turn on Korean subtitles.')
        self.assertEqual(session.subtitle_snapshot()['mode'],'off')
        session.cancel();self.settle(f)
        self.assertEqual(session.subtitle_snapshot()['mode'],'off')
        self.arm(f);self.prepared(f,'한국어 자막 켜줘')
        active=self.start_player(f)
        self.assertEqual((session.subtitle_snapshot()['mode'],active['caption']['text']),
                         ('ko','깨끗한 시험 답변이에요.'))
        self.finish(f)

    def test_manual_face_override_preserves_semantic_caption_and_photo_request(self):
        seen=[]
        f=self.fixture([envelope(kind='photo',disposition='execute',
            captions={'en':'','ko':'준비됐어.'})],mode='manual',on_reply=lambda reply,*_a:seen.append(reply))
        session=f['session'];session.set_emotion('sad');session.set_subtitles('ko')
        self.prepared(f,'Can you take a picture for me?')
        active=self.start_player(f)
        self.assertEqual(active['emotion'],'sad')
        self.assertEqual(active['emotion_mode'],'manual')
        self.assertTrue(seen[0].human_photo_request)
        self.assertEqual(seen[0].intent.name,'take_photo')
        self.assertEqual(active['caption']['text'],PHOTO_PREPARATION['ko'])
        self.finish(f)

    def test_repeat_uses_only_retained_successful_audio_caption_without_new_provider_or_synthesis(self):
        first=envelope(speech='First clean answer.',captions={'en':'','ko':'첫 번째 답이야.'})
        second=envelope(speech='An unplayed replacement.',captions={'en':'','ko':'재생되지 않은 답이야.'})
        f=self.fixture([first,second]);session=f['session'];session.set_subtitles('ko')
        self.prepared(f,'First question.');self.start_player(f);self.finish(f)
        self.arm(f);self.prepared(f,'Second question.')
        session.cancel();self.settle(f)
        self.assertEqual(session.snapshot()['caption']['text'],'')
        self.arm(f);self.assertTrue(session.repeat());self.assertTrue(f['prepared'].wait(2))
        self.assertEqual(session.snapshot()['caption']['text'],'')
        self.assertEqual(self.start_player(f)['caption']['text'],'첫 번째 답이야.')
        self.assertEqual(len(f['provider'].requests),2)
        self.assertEqual(f['voice']._synthesize.call_count,2)
        self.finish(f)

    def test_explicit_menu_mode_change_retires_a_prepared_voice_mode_command(self):
        for menu_mode in ('off','en'):
            with self.subTest(menu_mode=menu_mode):
                f=self.fixture([envelope(kind='subtitles',disposition='execute',subtitle_mode='ko')])
                session=f['session']
                self.prepared(f,'Turn Korean subtitles on.')
                session.set_subtitles(menu_mode)
                active=self.start_player(f)
                self.assertEqual(session.subtitle_snapshot()['mode'],menu_mode)
                self.assertEqual(active['caption']['text'],'' if menu_mode=='off' else 'A clean fixture answer.')
                self.finish(f)

    def test_next_question_reset_and_cancel_clear_old_caption(self):
        f=self.fixture([envelope(captions={'en':'','ko':'이전 답이야.'}),
                        envelope(captions={'en':'','ko':'새 답이야.'})])
        session=f['session'];session.set_subtitles('ko')
        self.prepared(f,'First question.');self.start_player(f);self.finish(f)
        self.arm(f);self.prepared(f,'New question.')
        self.assertEqual(self.start_player(f)['caption']['text'],'새 답이야.')
        session.reset();self.settle(f)
        self.assertEqual(session.snapshot()['caption']['text'],'')
        self.assertFalse(f['voice'].has_cache)

    def test_photo_success_feedback_has_same_caption_timing_without_a_gpt_call(self):
        f=self.fixture([]);session=f['session'];session.set_subtitles('ko')
        self.assertTrue(session.start_feedback('Ta-da! Pretty good, huh?',{'ko':'짜잔! 꽤 괜찮지?'}))
        self.assertTrue(f['prepared'].wait(2));self.assertEqual(session.snapshot()['caption']['text'],'')
        self.assertEqual(self.start_player(f)['caption']['text'],'짜잔! 꽤 괜찮지?')
        self.finish(f)
        self.assertEqual(f['provider'].requests,[])
        self.assertEqual(session.brain.history,[])
        self.arm(f);self.assertTrue(session.repeat());self.assertTrue(f['prepared'].wait(2))
        self.assertEqual(self.start_player(f)['caption']['text'],'짜잔! 꽤 괜찮지?')
        self.finish(f)
        self.assertEqual(f['voice']._synthesize.call_count,1)


if __name__=='__main__':unittest.main()
