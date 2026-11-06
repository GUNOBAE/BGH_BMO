"""One existing GPT reply carries a bounded plan; no live understanding claim."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from bmo_ai.expression_contract import ExpressionFormatError
from bmo_ai.features.reply import (FeatureClient, HostIntent, decode_feature_reply,
    feature_text_format, VIDEO_PREPARATION)
from bmo_ai.integration_profile import integration_config, policy_brain
from test_semantic_requests import Provider, envelope


def step(name, target=None, media_mode=None):
    return dict(name=name, target=target, media_mode=media_mode)


def compound(*steps, **changes):
    return envelope(kind='compound', disposition='execute', version=5,
        plan=dict(version=1, steps=list(steps)), **changes)


class CompoundRequestTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'subprocess.Popen',
                     'bmo_ai.cloud.create_client', 'bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(name, side_effect=AssertionError('real IO forbidden')))

    def ask(self, text, value):
        provider = Provider([value])
        view = FeatureClient(provider, character_life=True)
        view.set_context({'camera': 'explicit_current_user_photo_only'})
        brain = policy_brain(provider, integration_config(), expression_client=view)
        brain.ask(text)
        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual((request['model'], request['reasoning'], request['max_output_tokens']),
                         ('gpt-6-luna', {'effort': 'low'}, 320))
        self.assertEqual([row['content'] for row in brain.history if row['role']=='assistant'],
                         [view.last_reply.speech])
        return view.last_reply, provider

    def test_stop_then_video_order_preserves_one_reply_and_no_legacy_double_dispatch(self):
        for text in ('Stop following my face and record a five-second video.',
                     '얼굴 추적을 멈추고 5초 영상을 녹화해 줘.'):
            with self.subTest(text=text):
                reply, provider = self.ask(text, compound(step('stop_face_tracking'), step('start_video', 'seconds_5')))
                self.assertEqual(reply.action_plan,
                    (HostIntent('stop_face_tracking'), HostIntent('start_video', 'seconds_5')))
                self.assertIsNone(reply.intent)
                self.assertEqual(reply.request_validation, 'validated')
                self.assertTrue(reply.human_device_request)
                self.assertEqual(reply.speech, VIDEO_PREPARATION['en'])
                self.assertNotIn('id', provider.requests[0]['text']['format']['schema']['properties']['plan']['properties'])

    def test_independent_negative_clause_does_not_suppress_requested_action(self):
        cases = [
            ("Stop tracking but don't record.", 'tracking', 'stop_face_tracking'),
            ('추적은 멈추되 녹화는 하지 마.', 'tracking', 'stop_face_tracking'),
            ("Don't stop tracking; take a photo.", 'photo', 'take_photo'),
            ('추적은 멈추지 말고 사진 찍어줘.', 'photo', 'take_photo'),
            ('I said stop tracking yesterday; record a video now.', 'video', 'start_video'),
            ('추적은 내일 중지하고 지금 사진 찍어줘.', 'photo', 'take_photo'),
        ]
        for text, kind, name in cases:
            with self.subTest(text=text):
                value = envelope(kind=kind, disposition='execute', version=5,
                    intent=step(name), plan={'version':1,'steps':[]})
                reply, _ = self.ask(text, value)
                self.assertEqual(reply.request_validation, 'validated')
                self.assertEqual(reply.intent, HostIntent(name))
                self.assertEqual(reply.action_plan, ())

    def test_negated_quoted_past_future_or_bare_confirmation_in_any_step_rejects_entire_plan(self):
        cases = [
            "Don't stop tracking; record a video.",
            "Stop tracking but don't record.",
            '추적은 멈추되 녹화는 하지 마.',
            '추적은 멈추지 말고 영상을 녹화해 줘.',
            '"Stop tracking"; record a video.',
            'Translate "Stop tracking and record a video".',
            'Yesterday I asked to stop tracking; record a video now.',
            'Stop tracking tomorrow; record a video now.',
            'Yes.',
            'Cancel.',
            '취소',
        ]
        for text in cases:
            with self.subTest(text=text):
                reply, _ = self.ask(text, compound(step('stop_face_tracking'), step('start_video')))
                self.assertEqual(reply.action_plan, ())
                self.assertIsNone(reply.intent)
                self.assertFalse(reply.human_device_request)
                self.assertNotEqual(reply.request_validation, 'validated')

    def test_temporal_and_reporting_prefix_before_comma_never_becomes_current_execution(self):
        cases = [
            ('Tomorrow, take a photo.', 'photo', 'take_photo', 'future_request'),
            ('Tomorrow, BMO, take a photo.', 'photo', 'take_photo', 'future_request'),
            ('Later, record a video.', 'video', 'start_video', 'future_request'),
            ('When I return, follow my face.', 'tracking', 'start_face_tracking', 'future_request'),
            ('If we have time, wave your arms.', 'arm', 'arm_gesture', 'future_request'),
            ('Yesterday, I asked to take a photo.', 'photo', 'take_photo', 'past_request'),
            ('As I asked, take a photo.', 'photo', 'take_photo', 'past_request'),
            ('내일, 사진 찍어줘.', 'photo', 'take_photo', 'future_request'),
            ('어제, 얼굴 추적을 멈춰 달라고 했어.', 'tracking', 'stop_face_tracking', 'informational_request'),
            ('추적을 멈춰 달라고 했어.', 'tracking', 'stop_face_tracking', 'informational_request'),
            ('사진 찍어 달라고 했어.', 'photo', 'take_photo', 'informational_request'),
        ]
        for version in (4,5):
            for text,kind,name,reason in cases:
                with self.subTest(version=version,text=text):
                    extra={'plan':{'version':1,'steps':[]}} if version==5 else {}
                    value=envelope(version=version,kind=kind,disposition='execute',
                        intent=step(name,'wave' if name=='arm_gesture' else None),**extra)
                    reply,_=self.ask(text,value)
                    self.assertIsNone(reply.intent)
                    self.assertEqual(reply.action_plan,())
                    self.assertEqual(reply.request_validation,reason)
                    self.assertFalse(reply.human_device_request)
                    self.assertFalse(reply.human_photo_request)
        for text in ('Tomorrow, stop tracking and record a video.',
                     '내일, 추적을 멈추고 영상을 녹화해 줘.'):
            reply,_=self.ask(text,compound(step('stop_face_tracking'),step('start_video')))
            self.assertEqual(reply.action_plan,())
            self.assertEqual(reply.request_validation,'future_request')

    def test_temporal_prefix_ends_at_independent_sentence_or_semicolon(self):
        for text,kind,name in [
            ('Tomorrow, stop tracking; record a video now.','video','start_video'),
            ('Yesterday, stop tracking. Take a photo now.','photo','take_photo'),
            ('내일, 추적을 멈춰줘; 지금 사진 찍어줘.','photo','take_photo'),
        ]:
            reply,_=self.ask(text,envelope(version=5,kind=kind,disposition='execute',
                intent=step(name),plan={'version':1,'steps':[]}))
            self.assertEqual(reply.intent,HostIntent(name))
            self.assertEqual(reply.request_validation,'validated')

    def test_bare_cancel_cannot_become_two_stop_actions_from_a_forged_plan(self):
        for text in ('Cancel.', 'Never mind.', '취소', '잠깐'):
            reply,_=self.ask(text,compound(step('stop_face_tracking'),step('stop_arms')))
            self.assertEqual(reply.action_plan,())
            self.assertIsNone(reply.intent)
            self.assertEqual(reply.request_validation,'negated_request')

    def test_invalid_plan_structure_is_atomic_and_no_partial_json_recovery(self):
        valid = compound(step('stop_face_tracking'), step('start_video'))
        values=[]
        for bad in [None, [], {'version':True,'steps':[]}, {'version':2,'steps':[]},
                    {'version':1,'steps':[step('stop_face_tracking')]*2},
                    {'version':1,'steps':[step('stop_face_tracking'),step('start_video'),step('volume_up'),step('volume_down')]},
                    {'version':1,'steps':[step('stop_face_tracking'),step('shell','danger')]},
                    {'version':1,'steps':[step('stop_face_tracking'),step('start_video','seconds_1.5')]},
                    {'version':1,'steps':[step('stop_face_tracking'),step('start_video')],'deadline':3}]:
            item=deepcopy(valid);item['plan']=bad;values.append(item)
        for value in values:
            with self.subTest(plan=value['plan']), self.assertRaises(ExpressionFormatError):
                decode_feature_reply(json.dumps(value))
        raw=json.dumps(valid)
        for malformed in (raw[:-2], raw.replace('"version": 5','"version":5,"version":5')):
            with self.assertRaises(ExpressionFormatError): decode_feature_reply(malformed)

    def test_parent_mismatch_clarify_duplicate_legacy_intent_or_stale_token_never_executes(self):
        valid=compound(step('stop_face_tracking'), step('start_video'))
        values=[]
        for change in ({'kind':'none','disposition':'none'}, {'kind':'video'},
                       {'disposition':'clarify'}, {'turn_token':'old_turn'},
                       {'confirmation_token':'host_confirm_1'}):
            item=deepcopy(valid);item['request'].update(change);values.append(item)
        item=deepcopy(valid);item['intent']=step('volume_up');values.append(item)
        item=deepcopy(valid);item['plan']['steps']=[];values.append(item)
        item=deepcopy(valid);item['plan']['steps']=[step('stop_face_tracking')];values.append(item)
        for value in values:
            with self.subTest(request=value['request']):
                reply,_=self.ask('Stop tracking and record a video.',value)
                self.assertEqual(reply.action_plan, ())
                self.assertIsNone(reply.intent)
                self.assertNotEqual(reply.request_validation,'validated')

    def test_single_media_mode_is_same_stable_catalog_id_and_never_camera_intent(self):
        for mode in ('face','video'):
            value=envelope(version=5, kind='media', disposition='execute',
                intent=step('play_song','space_potatoes',mode),plan={'version':1,'steps':[]})
            reply,_=self.ask('Play Space Potatoes as a '+mode+'.',value)
            self.assertEqual(reply.intent,HostIntent('play_song','space_potatoes',mode))
            self.assertTrue(reply.human_device_request)
            self.assertEqual(reply.intent.public()['target'],'space_potatoes')
        for name in ('start_video','take_photo','launch_game'):
            with self.assertRaises(ExpressionFormatError):
                HostIntent(name,'fixture' if name=='launch_game' else None,'video')
        for mode in ('song','../../private',False):
            with self.assertRaises(ExpressionFormatError): HostIntent('play_song','space_potatoes',mode)
        self.assertEqual(HostIntent('play_song','space_potatoes').public(),
                         {'name':'play_song','target':'space_potatoes'})

    def test_v5_shape_bounds_while_v1_to_v4_inputs_remain_compatible(self):
        schema=feature_text_format(character_life=True,turn_token='current')['format']['schema']
        self.assertEqual(schema['properties']['version']['enum'],[5])
        plan=schema['properties']['plan']
        self.assertFalse(plan['additionalProperties'])
        self.assertEqual(plan['properties']['steps']['maxItems'],3)
        self.assertEqual(set(plan['properties']['steps']['items']['properties']),{'name','target','media_mode'})
        self.assertIn('compound',schema['properties']['request']['properties']['kind']['enum'])
        reply,_=self.ask('Stop tracking.',envelope(kind='tracking',disposition='execute',
            intent={'name':'stop_face_tracking','target':None}))
        self.assertEqual(reply.intent,HostIntent('stop_face_tracking'))
        self.assertEqual(reply.action_plan,())


if __name__=='__main__': unittest.main()
