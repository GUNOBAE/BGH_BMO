"""Automatic decoration quotas never grant or consume explicit arm commands."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from bmo_ai.character_director import CharacterPlan
from bmo_ai.features.host import FeatureHost
from bmo_ai.features.motion.conversation_policy import ConversationGesturePolicy
from bmo_ai.features.reply import HostIntent


class ConversationGesturePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = ConversationGesturePolicy()
        self.plan = CharacterPlan('happy', 'warm', gesture_id='celebrate',
                                  gesture_context='achievement')

    def reason(self, now=100., turn='turn1', plan=None, **options):
        return self.policy.reason(self.plan if plan is None else plan, now=now,
            turn=turn, greeting_eligible=True, **options)

    def test_query_does_not_charge_and_only_started_charges_once_per_turn(self):
        for _ in range(4):
            self.assertEqual(self.reason(), 'eligible')
        self.assertEqual(self.policy.snapshot(100.)['started_in_window'], 0)
        self.policy.started('turn1', 100.)
        self.policy.started('turn1', 200.)
        self.assertEqual(list(self.policy.starts), [100.])
        self.assertEqual(self.reason(500.), 'automatic_gesture_duplicate')

    def test_45_seconds_and_rolling_5_minutes_have_exact_boundaries(self):
        self.policy.started('turn1', 100.)
        self.assertEqual(self.reason(144.999, 'turn2'), 'automatic_gesture_cooldown')
        self.assertEqual(self.reason(145., 'turn2'), 'eligible')
        self.policy.started('turn2', 145.)
        self.assertEqual(self.reason(399.999, 'turn3'), 'automatic_gesture_window_limit')
        self.assertEqual(self.reason(400., 'turn3'), 'eligible')
        self.assertEqual(self.policy.snapshot(400.)['started_in_window'], 1)
        self.policy.started('turn3', 400.)
        self.assertEqual(self.reason(444.999, 'turn4'), 'automatic_gesture_cooldown')
        self.assertEqual(self.reason(445., 'turn4'), 'eligible')

    def test_only_matching_contexts_are_eligible(self):
        for gesture, context in (('wave', 'greeting'), ('celebrate', 'achievement'),
                                 ('shrug', 'playful'), ('shrug', 'hesitation')):
            with self.subTest(gesture=gesture, context=context):
                self.assertEqual(self.reason(plan=replace(self.plan, gesture_id=gesture,
                    gesture_context=context)), 'eligible')
        for context in ('none', 'greeting', 'playful', 'hesitation'):
            with self.subTest(mismatched_context=context):
                self.assertEqual(self.reason(plan=replace(self.plan, gesture_context=context)),
                                 'automatic_gesture_context_required')
        self.assertEqual(self.reason(plan=replace(self.plan, gesture_id='none')),
                         'current_turn_no_gesture')

    def test_actions_distress_and_acting_never_gain_decorative_motion(self):
        self.assertEqual(self.reason(has_action=True), 'automatic_gesture_action_turn')
        for options in ({'sensitive_context': True}, {'explicit_acting': True},
                        {'source': 'acting'}):
            self.assertEqual(self.reason(plan=replace(self.plan, **options)),
                             'automatic_gesture_context_suppressed')
        self.assertEqual(self.policy.snapshot(100.)['started_in_window'], 0)

    def test_surprise_requires_surprised_face_and_keeps_existing_suppression_and_quota(self):
        surprise = replace(self.plan, face_emotion='surprised', delivery_id='curious',
                           gesture_id='shrug', gesture_context='surprise')
        self.assertEqual(self.reason(plan=surprise), 'eligible')
        self.assertEqual(self.reason(plan=replace(surprise, face_emotion='neutral')),
                         'automatic_gesture_context_required')
        self.assertEqual(self.reason(plan=surprise, has_action=True), 'automatic_gesture_action_turn')
        self.assertEqual(self.reason(plan=replace(surprise, sensitive_context=True)),
                         'automatic_gesture_context_suppressed')
        self.policy.started('other', 90.)
        self.assertEqual(self.reason(plan=surprise), 'automatic_gesture_cooldown')

    def test_wave_needs_first_conversation_or_ten_minute_gap(self):
        self.assertTrue(self.policy.greeting_eligible(100.))
        self.policy.last_conversation = 100.
        self.assertFalse(self.policy.greeting_eligible(699.999))
        self.assertTrue(self.policy.greeting_eligible(700.))
        wave = replace(self.plan, gesture_id='wave', gesture_context='greeting')
        self.assertEqual(self.policy.reason(wave, now=150., turn='hello2',
            greeting_eligible=False), 'automatic_greeting_recent_conversation')

    def test_explicit_host_command_bypasses_auto_quota_but_not_device_stop_or_dedup(self):
        with (patch('subprocess.Popen', side_effect=AssertionError('Physical IO forbidden')),
              patch('socket.socket.connect', side_effect=AssertionError('Network forbidden'))):
            host = FeatureHost(clock=lambda: 200.)
            self.addCleanup(host.close)
            host._automatic_gestures.started('auto1', 150.)
            host._automatic_gestures.started('auto2', 195.)
            quota = host._automatic_gestures.snapshot(200.)
            with patch.object(host.motion, 'submit', wraps=host.motion.submit) as submit:
                result = host.execute(HostIntent('arm_gesture', 'wave'), request_id='manual_wave')
                self.assertEqual((result.status, result.code), ('accepted', 'gesture_queued'))
                self.assertTrue(submit.call_args.kwargs['explicit'])
                host.execute(HostIntent('arm_gesture', 'wave'), request_id='manual_wave')
                self.assertEqual(submit.call_count, 1)
                self.assertEqual(host._automatic_gestures.snapshot(200.), quota)
                host.execute(HostIntent('stop_arms'), request_id='manual_stop')
                stopped = host.execute(HostIntent('arm_gesture', 'wave'), request_id='after_stop')
                self.assertEqual((stopped.status, stopped.code), ('rejected', 'motion_stopped'))


if __name__ == '__main__':
    unittest.main()
