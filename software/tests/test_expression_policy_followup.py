"""Semantic decoration policy, not a real provider or listening evaluation."""
import json
from types import SimpleNamespace
import unittest

from bmo_ai.character_director import CharacterDirector, CharacterPlan, performance_resolution
from bmo_ai.expressive_voice import ExpressivePolicy
from bmo_ai.features.reply import FeatureClient, feature_text_format, COMPOUND_INSTRUCTIONS
from test_compound_requests import compound, step
from test_semantic_requests import Provider, envelope


class ExpressionFollowupTests(unittest.TestCase):
    def plan(self, emotion='happy', reaction='none', gesture='none', context='none', **kwargs):
        return CharacterDirector().resolve(emotion, reaction, gesture,
            metadata=dict(gesture_context=context, **kwargs))

    @staticmethod
    def voice_metadata(plan):
        return dict(character_plan=plan.public(), emotion=plan.face_emotion,
                    emotion_validation='validated')

    def test_same_call_context_allows_only_matching_named_decoration(self):
        for gesture, context, allowed in [('wave', 'greeting', True),
                ('celebrate', 'achievement', True), ('shrug', 'playful', True),
                ('shrug', 'hesitation', True), ('wave', 'achievement', False),
                ('celebrate', 'greeting', False), ('shrug', 'none', False)]:
            with self.subTest(gesture=gesture, context=context):
                plan = self.plan(gesture=gesture, context=context)
                self.assertEqual(plan.gesture_id, gesture if allowed else 'none')
                trace = performance_resolution(plan, reaction='none', gesture=gesture)
                self.assertEqual(trace['gesture_reason'], 'eligible' if allowed else 'context_mismatch')
        self.assertEqual(self.plan(emotion='neutral', gesture='celebrate', context='achievement').gesture_id, 'none')

    def test_legacy_plan_remains_readable_but_no_context_grants_no_motion(self):
        plan = self.plan(gesture='wave')
        self.assertEqual(plan.gesture_id, 'none')
        legacy = plan.public()
        legacy.pop('gesture_context')
        self.assertEqual(CharacterPlan.from_public(legacy).gesture_context, 'none')

    def test_one_small_aha_or_hmm_can_share_gesture_but_laugh_and_long_ooh_do_not(self):
        for reaction, gesture, context, expected in [
                ('aha', 'celebrate', 'achievement', 'celebrate'),
                ('hmm', 'shrug', 'hesitation', 'shrug'),
                ('chuckle', 'celebrate', 'achievement', 'none'),
                ('ooh', 'celebrate', 'achievement', 'none')]:
            plan = self.plan(emotion='neutral' if reaction == 'hmm' else 'happy',
                reaction=reaction, gesture=gesture, context=context)
            self.assertEqual(plan.gesture_id, expected)
            self.assertEqual(plan.reaction_id, reaction)

    def test_controls_and_real_distress_do_not_get_decoration(self):
        for message, has_intent, reason in [('My dad died.', False, 'sensitive_context'),
                                          ('Turn the volume up.', True, 'action_request')]:
            plan = CharacterDirector().resolve('happy', 'chuckle', 'celebrate',
                metadata={'gesture_context': 'achievement'}, message=message, has_intent=has_intent)
            self.assertEqual((plan.gesture_id, plan.reaction_id), ('none', 'none'))
            trace = performance_resolution(plan, reaction='chuckle', gesture='celebrate', has_intent=has_intent)
            self.assertEqual((trace['gesture_reason'], trace['reaction_reason']), (reason, reason))

    def test_rejected_control_plan_cannot_turn_into_an_automatic_flourish(self):
        rows = [compound(step('stop_face_tracking'), step('start_video', 'seconds_5'),
                    gesture='celebrate', reaction='aha', emotion='happy'),
                envelope(kind='photo', disposition='execute', gesture='celebrate',
                    reaction='aha', model_emotion='happy')]
        for row in rows:
            row['character'] = dict(delivery_id='proud', intensity='low', gaze_id='none',
                                    source='dialogue', gesture_context='achievement')
            view = FeatureClient(Provider([row]), character_life=True)
            view.responses.create(instructions='Bounded synthetic fixture.',
                input=[{'role':'user', 'content':'Stop tracking and record a video.'}])
            self.assertIsNone(view.last_reply.intent)
            self.assertEqual(view.last_reply.action_plan, ())
            self.assertEqual((view.last_reply.gesture, view.last_reply.reaction), ('none', 'none'))
            self.assertEqual(view.last_resolution['gesture_reason'], 'action_request')

    def test_semantic_correction_does_not_require_redundant_apology_before_cached_oops(self):
        policy = ExpressivePolicy()
        plan = self.plan(emotion='neutral', reaction='oops', delivery_id='gentle_correction')
        result = policy.plan('The number is fourteen.', self.voice_metadata(plan), model='s2.1-pro-free')
        self.assertEqual((result.reaction, result.proposed_reaction, result.reaction_reason),
                         ('oops', 'oops', 'eligible'))
        self.assertIn('never scolding', result.text)
        ordinary = self.plan(emotion='neutral', reaction='oops')
        suppressed = policy.plan('The number is fourteen.', self.voice_metadata(ordinary), model='s2.1-pro-free')
        self.assertEqual((suppressed.reaction, suppressed.reaction_reason), ('none', 'correction_required'))

    def test_reaction_decision_distinguishes_budget_repeat_and_existing_spoken_expression(self):
        policy = ExpressivePolicy()
        plan = self.plan(reaction='chuckle', delivery_id='playful')
        def deliver(text='A tiny funny thing.', current=plan):
            return policy.plan(text, self.voice_metadata(current), model='s2.1-pro-free')
        first = deliver()
        self.assertEqual(first.reaction_reason, 'eligible')
        # Merely querying eligibility does not charge the actual-play budget.
        self.assertEqual(deliver().reaction_reason, 'eligible')
        policy.completed(first)
        blocked = deliver()
        self.assertEqual((blocked.reaction, blocked.reaction_reason), ('none', 'reaction_cooldown'))
        for _ in range(2):
            policy.completed(deliver())
        self.assertEqual(deliver().reaction_reason, 'reaction_repeat')
        hmm = self.plan(emotion='neutral', reaction='hmm', delivery_id='curious')
        different = deliver('There might be a clue.', hmm)
        self.assertEqual(different.reaction_reason, 'eligible')
        self.assertEqual(deliver('Hehe. A tiny funny thing.').reaction_reason, 'already_in_speech')
        plain = self.plan(emotion='neutral', delivery_id='curious')
        voice = deliver('How does that little machine work?', plain)
        self.assertEqual(voice.reaction, 'none')
        self.assertIn('gentle wonder', voice.text)

    def test_active_schema_and_same_provider_response_carry_context_and_reaction_diagnostics(self):
        schema = feature_text_format(character_life=True)['format']['schema']['properties']['character']
        self.assertIn('gesture_context', schema['required'])
        self.assertEqual(schema['properties']['gesture_context']['enum'],
                         ['none', 'greeting', 'achievement', 'playful', 'hesitation'])
        requests = []
        class Provider:
            def create(self, **request):
                requests.append(request)
                return SimpleNamespace(status='completed', output=[], error=None, incomplete_details=None,
                    output_text=json.dumps(dict(version=3, speech='You did it!', emotion='happy',
                        reaction='aha', gesture='celebrate', intent=None, character=dict(
                            delivery_id='proud', intensity='low', gaze_id='none', source='dialogue',
                            gesture_context='achievement'))))
        view = FeatureClient(SimpleNamespace(responses=Provider()), character_life=True)
        view.responses.create(instructions='Bounded synthetic dialogue fixture.',
            input=[{'role': 'user', 'content': 'I finally finished my project.'}])
        self.assertEqual(len(requests), 1)
        self.assertEqual(view.last_reply.gesture, 'celebrate')
        self.assertEqual(view.last_resolution['model_reaction'], 'aha')
        self.assertEqual(view.last_resolution['resolved_reaction'], 'aha')
        self.assertEqual(view.last_resolution['gesture_context'], 'achievement')
        self.assertIn('at least45 seconds', COMPOUND_INSTRUCTIONS)
        self.assertNotIn('Choose at most one vocal reaction or gesture flourish', COMPOUND_INSTRUCTIONS)


if __name__ == '__main__':
    unittest.main()
