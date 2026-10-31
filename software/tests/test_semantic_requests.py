"""One-call meaning transport/host guards; no natural-language model approval."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.acting_request import requested_acting
from bmo_ai.core import BrainResponseError
from bmo_ai.features.camera.request import explicit_photo_request
from bmo_ai.features.reply import FeatureClient, feature_text_format, PHOTO_PREPARATION
from bmo_ai.integration_profile import integration_config, policy_brain


def envelope(*, kind="none", disposition="none", requested_emotion="none",
             subtitle_mode="unchanged", model_emotion="neutral", source="dialogue",
             delivery="calm", confirmation_token=None, **changes):
    result = dict(version=4, speech="A clean fixture answer.", emotion=model_emotion,
        gesture="none", intent=None, reaction="none",
        character=dict(delivery_id=delivery,intensity="medium",gaze_id="none",source=source),
        request=dict(turn_token="CURRENT",kind=kind,disposition=disposition,
            emotion=requested_emotion,subtitle_mode=subtitle_mode,confirmation_token=confirmation_token),
        captions={"en":"A clean fixture answer.","ko":"깨끗한 시험 답변이에요."},
        photo_success=None)
    result.update(changes)
    return result


class Provider:
    def __init__(self, replies):
        self.responses=self;self.replies=list(replies);self.requests=[]
    def create(self, **request):
        self.requests.append(request)
        value=deepcopy(self.replies.pop(0))
        if value.get("status")=="incomplete":
            return SimpleNamespace(status="incomplete",incomplete_details={"reason":"max_output_tokens"},
                error=None,output=[],output_text="")
        if type(value.get("request")) is dict and value['request'].get('turn_token')=='CURRENT':
            value['request']['turn_token']=request['text']['format']['schema']['properties']['request']['properties']['turn_token']['enum'][0]
        return SimpleNamespace(status="completed",incomplete_details=None,error=None,
            output=[],output_text=json.dumps(value))


class SemanticRequestsTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect','subprocess.Popen','bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials','bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(name,side_effect=AssertionError('Actual IO forbidden')))

    def wired(self, values, **context):
        provider=Provider(values)
        view=FeatureClient(provider,character_life=True)
        view.set_context(dict(camera='explicit_current_user_photo_only',subtitle_mode='off',**context))
        brain=policy_brain(provider,integration_config(),expression_client=view)
        return provider,view,brain

    def ask(self, message, value, **context):
        provider,view,brain=self.wired([value],**context)
        spoken=brain.ask(message)
        self.assertEqual(spoken,view.last_reply.speech)
        self.assertEqual(len(provider.requests),1)
        request=provider.requests[0]
        self.assertEqual((request['model'],request['reasoning'],request['max_output_tokens']),
                         ('gpt-6-luna',{'effort':'low'},320))
        self.assertEqual(request['text']['format']['name'],'bmo_feature_reply_v5')
        self.assertFalse(request['store'])
        self.assertEqual(brain.history[-1]['content'],spoken)
        return view.last_reply,view.last_resolution,provider

    def test_unmatched_english_korean_meanings_use_the_same_single_reply(self):
        # These are fixture interpretations, NOT a claim a live model understood
        # the sentences. Several deliberately miss the old literal matchers.
        photos=('Can you take a picture for me?','I want you to take my picture.',
                'Please take a picture of my robot.','Take photo.',
                'Okay, take a photo of my robot.',
                '비모야, 내 모습을 사진으로 남겨 줄 수 있을까?')
        self.assertFalse(explicit_photo_request(photos[0]))
        for message in photos:
            with self.subTest(message=message):
                reply,trace,provider=self.ask(message,envelope(kind='photo',disposition='execute',
                    photo_success={'speech':'Ta-da! Pretty good, huh?','captions':{'en':'Ta-da! Pretty good, huh?','ko':'짜잔! 꽤 괜찮지?'}}))
                self.assertTrue(reply.human_photo_request)
                self.assertEqual(reply.intent.name,'take_photo')
                self.assertEqual(reply.photo_success['speech'],'Ta-da! Pretty good, huh?')
                self.assertEqual(trace['request_validation'],'validated')
        for message in ('Please, get angry.','Hey BMO, get angry.','Get angry BMO.',
                        'I want an angry face.','살짝 성난 비모를 연기해 줄 수 있을까?'):
            with self.subTest(message=message):
                self.assertIsNone(requested_acting(message))
                reply,trace,_=self.ask(message,envelope(kind='acting',disposition='execute',
                    requested_emotion='angry',source='acting'))
                self.assertEqual((reply.emotion,reply.character_plan.delivery_id),('angry','theatrical_angry'))
                self.assertTrue(reply.character_plan.explicit_acting)
                self.assertEqual(trace['semantic_emotion'],'angry')

    def test_clear_non_requests_cannot_gain_photo_permission_from_forged_interpretation(self):
        denied=("Don't take a photo",'사진 찍지 마','Can you take photos?',
                'Do you support photos?', '"Take my picture"',"Say 'take my picture'",
                'Translate "take a picture"','I asked for a photo yesterday.',
                'Take a picture tomorrow.','나중에 사진을 찍어줘','What is two plus two?')
        for message in denied:
            with self.subTest(message=message):
                value=envelope(kind='photo',disposition='execute')
                value['intent']={'name':'take_photo','target':None}
                reply,trace,_=self.ask(message,value)
                self.assertFalse(reply.human_photo_request)
                self.assertIsNone(reply.intent)
                self.assertIsNone(reply.photo_success)
                self.assertNotEqual(trace['request_validation'],'validated')

    def test_negative_acting_and_fact_after_acting_remain_neutral(self):
        for message in ("Don't get angry.",'화내지 마','Explain an angry face.'):
            with self.subTest(message=message):
                reply,_,_=self.ask(message,envelope(kind='acting',disposition='execute',
                    requested_emotion='angry',model_emotion='angry',source='acting',delivery='theatrical_angry'))
                self.assertEqual(reply.emotion,'neutral')
        provider,view,brain=self.wired([
            envelope(kind='acting',disposition='execute',requested_emotion='angry',source='acting'),
            envelope(model_emotion='angry',source='acting',delivery='theatrical_angry')])
        brain.ask('Please, get angry.');self.assertEqual(view.last_reply.emotion,'angry')
        brain.ask('What is two plus two?');self.assertEqual(view.last_reply.emotion,'neutral')
        self.assertNotEqual(provider.requests[0]['text']['format']['schema']['properties']['request']['properties']['turn_token'],
                            provider.requests[1]['text']['format']['schema']['properties']['request']['properties']['turn_token'])
        self.assertEqual(len(provider.requests),2)

    def test_invalid_stale_or_unbound_metadata_never_authorizes_a_camera(self):
        values=[]
        for change in ({'turn_token':'old_turn'},{'kind':'take_photo'},{'approved':True},
                       {'disposition':'execute_every_time'},{'kind':[]}):
            value=envelope(kind='photo',disposition='execute');value['request'].update(change);values.append(value)
        value=envelope(kind='photo',disposition='execute');value.pop('request');values.append(value)
        for value in values:
            with self.subTest(request=value.get('request')):
                reply,_,_=self.ask('Can you take a picture for me?',value)
                self.assertFalse(reply.human_photo_request);self.assertIsNone(reply.intent)
        provider,view,brain=self.wired([envelope(kind='photo',disposition='execute')])
        view.set_context({'camera':'denied'})
        brain.ask('Take a picture of my robot.')
        self.assertFalse(view.last_reply.human_photo_request)
        self.assertIsNone(view.last_reply.intent)

    def test_confirmation_requires_the_current_host_owned_pending_token(self):
        value=envelope(kind='photo',disposition='execute',confirmation_token='host_confirm_1')
        for pending in (None,{'kind':'photo','token':'expired_token'}, {'kind':'acting','token':'host_confirm_1'}):
            with self.subTest(pending=pending):
                reply,trace,_=self.ask('Yes.',value,pending_confirmation=pending)
                self.assertFalse(reply.human_photo_request)
                self.assertEqual(trace['request_validation'],'confirmation_missing')
        reply,_,_=self.ask('Yes.',value,pending_confirmation={'kind':'photo','token':'host_confirm_1'})
        self.assertTrue(reply.human_photo_request)
        for message in ('Yes, please!',"Yes, I'd like one please",'That would be lovely.','응 지금 찍어줘'):
            reply,_,_=self.ask(message,value,pending_confirmation={'kind':'photo','token':'host_confirm_1'})
            self.assertTrue(reply.human_photo_request)
        for message in ('No thanks.','Maybe later.','No','아니','하지마','cancel',"Don't."):
            reply,_,_=self.ask(message,value,pending_confirmation={'kind':'photo','token':'host_confirm_1'})
            self.assertFalse(reply.human_photo_request)
        reply,_,_=self.ask('Yes.',envelope(kind='photo',disposition='execute'))
        self.assertFalse(reply.human_photo_request)
        reply,_,_=self.ask('Yes, please!',envelope(kind='photo',disposition='execute'))
        self.assertFalse(reply.human_photo_request)
        reply,_,_=self.ask('Can you take it?',envelope(kind='photo',disposition='clarify'))
        self.assertFalse(reply.human_photo_request)
        self.assertEqual(reply.request_interpretation['disposition'],'clarify')
        reply,_,_=self.ask('What is two plus two?',envelope(),
            pending_confirmation={'kind':'photo','token':'host_confirm_1'})
        self.assertFalse(reply.human_photo_request)
        for kind,mode,emotion in [('acting','unchanged','angry'),('subtitles','ko','none')]:
            reply,_,_=self.ask('Yes.',envelope(kind=kind,disposition='execute',
                subtitle_mode=mode,requested_emotion=emotion,source='acting'))
            self.assertIsNone(reply.request_interpretation)
        reply,_,_=self.ask('Please act angry or sad.',envelope(kind='acting',disposition='clarify',
            requested_emotion='angry',source='acting'))
        self.assertEqual(reply.emotion,'neutral')

    def test_subtitles_meaning_and_optional_translation_do_not_change_voice_budget(self):
        for message,mode in [('Show English captions','en'),('한국어 자막 켜줘','ko'),
                             ('Turn subtitles off','off'),('Could I have captions please?','on'),
                             ('Okay, show Korean captions.','ko')]:
            with self.subTest(message=message):
                reply,_,provider=self.ask(message,envelope(kind='subtitles',disposition='execute',subtitle_mode=mode))
                self.assertEqual(reply.request_interpretation['subtitle_mode'],mode)
                self.assertEqual(reply.captions['ko'],'깨끗한 시험 답변이에요.')
                self.assertNotIn('speech_language',provider.requests[0])
        value=envelope();value['captions']={'en':'<bad>','ko':123}
        reply,_,_=self.ask('A normal question.',value)
        self.assertEqual(reply.captions,{})
        self.assertFalse(reply.degraded)
        value=envelope();value.pop('captions')
        reply,_,_=self.ask('A normal question.',value)
        self.assertEqual(reply.captions,{})
        self.assertFalse(reply.degraded)

    def test_unrelated_device_intent_cannot_piggyback_on_caption_or_acting_interpretation(self):
        for kind,mode,emotion in [('subtitles','ko','none'),('acting','unchanged','angry')]:
            with self.subTest(kind=kind):
                value=envelope(kind=kind,disposition='execute',subtitle_mode=mode,
                    requested_emotion=emotion)
                value['intent']={'name':'arm_gesture','target':'raise_left_arm'}
                reply,_,_=self.ask('Okay, show Korean captions.' if kind=='subtitles' else 'Yeah, get mad.',value)
                self.assertIsNone(reply.intent)
                if kind=='acting':self.assertEqual(reply.emotion,'angry')

    def test_valid_current_acting_can_repair_bad_decorative_metadata_with_reason(self):
        for field,bad,reason in [('emotion','invalid','emotion_repaired'),
                                ('character',{'delivery_id':'bad'},'character_repaired')]:
            with self.subTest(field=field):
                value=envelope(kind='acting',disposition='execute',requested_emotion='angry',source='acting')
                value[field]=bad
                reply,trace,_=self.ask('Please, get angry.',value)
                self.assertFalse(reply.degraded)
                self.assertEqual((reply.emotion,reply.character_plan.delivery_id),('angry','theatrical_angry'))
                self.assertEqual(trace['degradation_reason'],reason)

    def test_literal_get_angry_trace_separates_model_and_resolved_emotion_without_claiming_field_stt(self):
        reply,trace,provider=self.ask('Get angry.',envelope(kind='acting',disposition='execute',
            requested_emotion='angry',model_emotion='neutral',source='acting'))
        self.assertEqual(trace['requested_emotion'],'angry')
        self.assertEqual(trace['model_emotion'],'neutral')
        self.assertEqual(trace['resolved_emotion'],'angry')
        self.assertEqual(reply.character_plan.delivery_id,'theatrical_angry')
        self.assertEqual(len(provider.requests),1)

    def test_private_observer_is_opt_in_current_turn_only_and_cannot_break_reply(self):
        provider,view,brain=self.wired([envelope(),envelope()])
        seen=[]
        def observer(value):
            seen.append(value)
            raise RuntimeError('Private diagnostic boundary failure')
        self.assertIsNone(view.responses.local_turn_observer)
        brain.ask('First fixture question.')
        view.responses.local_turn_observer=observer
        brain.ask('Second fixture question.')
        self.assertEqual(len(seen),1)
        self.assertEqual(set(seen[0]),{'input_text','model_reply'})
        self.assertEqual(seen[0]['input_text'],'Second fixture question.')
        self.assertNotIn('First fixture question.',json.dumps(seen[0]))
        self.assertEqual(len(provider.requests),2)

    def test_photo_preparation_cannot_claim_unverified_success_from_model_text(self):
        for speech,language in [('I already saved your beautiful photo.','en'),
                                ('사진을 이미 저장했고 아주 멋지게 나왔어.','ko')]:
            value=envelope(kind='photo',disposition='execute',speech=speech,
                captions={'en':'Already saved.','ko':'이미 저장했어.'},
                photo_success={'speech':'Ta-da!','captions':{'en':'Ta-da!','ko':'짜잔!'}})
            reply,_,provider=self.ask('Take a picture of my robot please.',value)
            self.assertEqual(reply.speech,PHOTO_PREPARATION[language])
            self.assertEqual(reply.captions,PHOTO_PREPARATION)
            self.assertEqual(reply.photo_success['speech'],'Ta-da!')
            self.assertEqual(len(provider.requests),1)
        for disposition in ('clarify','none'):
            value=envelope(kind='photo' if disposition=='clarify' else 'none',
                disposition=disposition,speech='A plain untouched reply.')
            reply,_,_=self.ask('Could you take it?',value)
            self.assertEqual(reply.speech,value['speech'])

    def test_incomplete_reply_uses_existing_error_without_budget_raise_or_repair_call(self):
        provider,view,brain=self.wired([{'status':'incomplete'}])
        with self.assertRaises(BrainResponseError):brain.ask('Turn Korean captions on.')
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(provider.requests[0]['max_output_tokens'],320)
        self.assertIsNone(view.last_reply)
        self.assertEqual(brain.history,[])


if __name__=='__main__':unittest.main()
