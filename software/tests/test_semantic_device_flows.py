"""Same-reply action meanings; fixtures do not approve live model understanding."""
import unittest
from unittest.mock import patch

from bmo_ai.expression_contract import ExpressionFormatError
from bmo_ai.features.reply import (FeatureClient, HostIntent, VIDEO_PREPARATION,
    feature_text_format, video_request_duration, volume_percent)
from bmo_ai.integration_profile import integration_config, policy_brain
from test_semantic_requests import Provider, envelope


class SemanticDeviceFlowsTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket.connect', 'subprocess.Popen',
                     'bmo_ai.cloud.create_client', 'bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(name, side_effect=AssertionError('real IO forbidden')))

    def ask(self, text, kind, intent, *, disposition='execute', **changes):
        value = envelope(kind=kind, disposition=disposition, intent=intent, **changes)
        provider = Provider([value])
        view = FeatureClient(provider, character_life=True)
        view.set_context({'camera': 'explicit_current_user_photo_only'})
        brain = policy_brain(provider, integration_config(), expression_client=view)
        brain.ask(text)
        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual((request['model'], request['reasoning'], request['max_output_tokens']),
                         ('gpt-6-luna', {'effort': 'low'}, 320))
        return view.last_reply

    def test_english_korean_current_meanings_share_existing_one_reply(self):
        cases = [
            ('Could you make a five second clip for me?', 'video', 'start_video', 'seconds_5'),
            ('비모야 짧은 동영상을 남겨 줄래?', 'video', 'start_video', None),
            ('Okay, raise your left arm a little please.', 'arm', 'arm_gesture', 'raise_left_arm'),
            ('Okay, lift the left one.', 'arm', 'arm_gesture', 'raise_left_arm'),
            ('비모 네 오른팔을 올려 줘.', 'arm', 'arm_gesture', 'raise_right_arm'),
            ('Give me a wave please.', 'arm', 'arm_gesture', 'wave'),
            ('양팔을 내려 줘.', 'arm', 'arm_gesture', 'both_down'),
            ('Stop moving your arms.', 'arm', 'stop_arms', None),
            ('Can your eyes follow me please?', 'tracking', 'start_face_tracking', None),
            ('나를 바라봐 줄래?', 'tracking', 'start_face_tracking', None),
            ('Stop tracking me.', 'tracking', 'stop_face_tracking', None),
            ('이제 추적을 멈춰 줘.', 'tracking', 'stop_face_tracking', None),
            ('Put the volume at fifty percent.', 'volume', 'set_volume', 'level_50'),
            ('음량을 0으로 해 줘.', 'volume', 'set_volume', 'level_0'),
            ('Please mute.', 'volume', 'mute_audio', None),
            ('음소거 풀어 줘.', 'volume', 'unmute_audio', None),
            ('Okay, make it quieter.', 'volume', 'volume_down', None),
            ('좀 더 크게 들려 줘.', 'volume', 'volume_up', None),
            ('Stop this recording.', 'video', 'stop_video', None),
        ]
        for text, kind, name, target in cases:
            with self.subTest(text=text):
                reply = self.ask(text, kind, {'name': name, 'target': target})
                self.assertTrue(reply.human_device_request)
                self.assertEqual(reply.request_validation, 'validated')
                self.assertEqual(reply.intent, HostIntent(name, target))
                self.assertEqual(reply.gesture, 'none')
                self.assertIsNone(reply.photo_success)

    def test_video_preparation_cannot_claim_storage_and_clamp_is_observable(self):
        for text, speech, language in [('Record a two minute clip.', 'I saved it.', 'en'),
                                      ('2분 영상을 찍어 줘.', '이미 저장했어.', 'ko')]:
            reply = self.ask(text, 'video', {'name': 'start_video', 'target': 'seconds_120'}, speech=speech)
            self.assertEqual(reply.speech, VIDEO_PREPARATION[language])
            self.assertEqual(reply.captions, VIDEO_PREPARATION)
            self.assertEqual(video_request_duration(reply.intent.target),
                {'requested_seconds': 120, 'duration_seconds': 60, 'duration_clamped': True})
        self.assertEqual(video_request_duration()['duration_seconds'], 30)
        self.assertEqual(video_request_duration('seconds_5')['duration_seconds'], 5)
        self.assertEqual(video_request_duration('seconds_0'),
            {'requested_seconds': 0, 'duration_seconds': 1, 'duration_clamped': True})

    def test_naked_or_wrong_kind_or_clarifying_intent_does_not_execute(self):
        intents = [('start_video', 'seconds_5'), ('stop_video', None),
            ('start_face_tracking', None), ('stop_face_tracking', None),
            ('arm_gesture', 'wave'), ('stop_arms', None), ('set_volume', 'level_10'),
            ('mute_audio', None), ('unmute_audio', None), ('volume_up', None), ('volume_down', None)]
        for name, target in intents:
            with self.subTest(name=name):
                reply = self.ask('A normal conversation.', 'none', {'name': name, 'target': target}, disposition='none')
                self.assertIsNone(reply.intent)
                self.assertFalse(reply.human_device_request)
        for kind, name in [('video', 'start_face_tracking'), ('arm', 'volume_up'),
                           ('tracking', 'stop_video'), ('volume', 'launch_game')]:
            reply = self.ask('Please do that now.', kind,
                {'name': name, 'target': 'fixture_game' if name == 'launch_game' else None})
            self.assertIsNone(reply.intent)
            self.assertFalse(reply.human_device_request)
        reply = self.ask('Can we make something?', 'video', {'name': 'start_video', 'target': None}, disposition='clarify')
        self.assertIsNone(reply.intent)
        self.assertFalse(reply.human_device_request)

    def test_clear_nonrequests_and_stale_tokens_cannot_authorize_devices(self):
        cases = [
            ("Don't record a video.", 'video', 'start_video', None),
            ('동영상은 나중에 찍어 줘.', 'video', 'start_video', None),
            ('Record a clip tomorrow.', 'video', 'start_video', None),
            ('I asked for a video yesterday.', 'video', 'start_video', None),
            ('Do you support video recording?', 'video', 'start_video', None),
            ('"Follow my face"', 'tracking', 'start_face_tracking', None),
            ('Translate "Follow my face"', 'tracking', 'start_face_tracking', None),
            ("Don't follow my face.", 'tracking', 'start_face_tracking', None),
            ("Don't stop tracking.", 'tracking', 'stop_face_tracking', None),
            ('팔을 올리지 마.', 'arm', 'arm_gesture', 'raise_left_arm'),
            ('Raise your arm later.', 'arm', 'arm_gesture', 'raise_right_arm'),
            ("Don't stop your arms.", 'arm', 'stop_arms', None),
            ("Don't mute.", 'volume', 'mute_audio', None),
            ('음소거 풀지 마.', 'volume', 'unmute_audio', None),
            ('What is the volume?', 'volume', 'set_volume', 'level_50'),
        ]
        for text, kind, name, target in cases:
            with self.subTest(text=text):
                reply = self.ask(text, kind, {'name': name, 'target': target})
                self.assertIsNone(reply.intent)
                self.assertFalse(reply.human_device_request)
                self.assertNotEqual(reply.request_validation, 'validated')
        request = envelope(kind='arm', disposition='execute')['request']
        request['turn_token'] = 'old_turn'
        reply = self.ask('Wave to me.', 'arm', {'name': 'arm_gesture', 'target': 'wave'}, request=request)
        self.assertEqual(reply.request_validation, 'stale_request')
        self.assertIsNone(reply.intent)

    def test_named_values_are_bounded_no_angle_shell_or_arbitrary_duration(self):
        for name, target in [('arm_gesture', 'pwm_1500'), ('arm_gesture', 'left_down'),
            ('start_video', '/tmp/movie'), ('start_video', 'seconds_1000000'),
            ('start_video', 'seconds_1.5'), ('start_video', 30), ('set_volume', 'level_101'),
            ('set_volume', 'level_-1'), ('set_volume', 'level_050'), ('volume_up', 'level_50')]:
            with self.subTest(name=name, target=target), self.assertRaises(ExpressionFormatError):
                HostIntent(name, target)
        for level in (0, 1, 25, 100):
            self.assertEqual(volume_percent('level_' + str(level)), level)

    def test_schema_keeps_six_request_fields_and_existing_response_budget_shape(self):
        schema = feature_text_format(character_life=True, turn_token='current')['format']['schema']
        request = schema['properties']['request']
        self.assertEqual(set(request['properties']),
            {'turn_token', 'kind', 'disposition', 'emotion', 'subtitle_mode', 'confirmation_token'})
        self.assertEqual(schema['properties']['version']['enum'], [4])
        self.assertIn('video', request['properties']['kind']['enum'])
        self.assertNotIn('approved', request['properties'])


if __name__ == '__main__':
    unittest.main()
