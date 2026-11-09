"""Final one-call payload and failed-turn evidence; no provider/audio network IO."""
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.cloud import CloudError
from bmo_ai.core import BrainResponseError
from bmo_ai.features.reply import FeatureClient, COMPOUND_INSTRUCTIONS, COMPACT_COMPOUND_INSTRUCTIONS
from bmo_ai.integration_profile import integration_config, policy_brain
from bmo_ai.integration_session import IntegrationSession
from bmo_ai.provider_evidence import response_evidence
from bmo_ai.turn_diagnostics import TurnDiagnostics
from test_semantic_requests import envelope, Provider


class RawProvider:
    def __init__(self, responses):
        self.responses = self
        self.values, self.requests = list(responses), []
    def create(self, **request):
        self.requests.append(request)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    def close(self):
        pass


def response(status='completed', text='', reason=None, error=None):
    return SimpleNamespace(status=status, output_text=text,
        incomplete_details=None if reason is None else SimpleNamespace(reason=reason),
        error=error, output=[], usage=SimpleNamespace(input_tokens=2201, output_tokens=320,
            output_tokens_details=SimpleNamespace(reasoning_tokens=73)))


class ProviderEvidenceTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'bmo_ai.cloud.create_client', 'bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(target, side_effect=AssertionError('Paid/real IO forbidden')))

    def brain(self, provider):
        view = FeatureClient(provider, character_life=True)
        view.set_context(dict(camera='explicit_current_user_photo_only', subtitle_mode='off'))
        brain = policy_brain(provider, integration_config(), expression_client=view)
        return brain, view

    def test_final_payload_compacts_only_active_contract_preserves_one_call_model_budget_and_semantic_guards(self):
        provider = Provider([envelope()])
        brain, view = self.brain(provider)
        answer = brain.ask('What is fourteen minus four?')
        self.assertEqual(len(provider.requests), 1)
        sent = provider.requests[0]
        self.assertEqual((sent['model'], sent['reasoning'], sent['max_output_tokens']),
            ('gpt-6-luna', {'effort': 'low'}, 320))
        self.assertLess(len(COMPACT_COMPOUND_INSTRUCTIONS), len(COMPOUND_INSTRUCTIONS) * .6)
        self.assertIn(COMPACT_COMPOUND_INSTRUCTIONS, sent['instructions'])
        self.assertNotIn(COMPOUND_INSTRUCTIONS, sent['instructions'])
        self.assertEqual(sent['text']['format']['name'], 'bmo_feature_reply_v5')
        self.assertTrue(sent['text']['format']['strict'])
        self.assertEqual(brain.last_call_metrics['request_instruction_chars'], len(sent['instructions']))
        self.assertGreater(brain.last_call_metrics['request_schema_chars'], 1000)
        self.assertGreater(brain.last_call_metrics['response_output_chars'], len(answer))
        self.assertEqual(brain.history[-1]['content'], answer)
        self.assertFalse(sent['store'])
        self.assertEqual(brain.max_turns, integration_config().max_turns)

    def test_incomplete_output_filter_and_failed_response_keep_safe_cause_counts_and_no_partial_intent(self):
        for status, reason, error, code in (
                ('incomplete', 'max_output_tokens', None, 'llm_incomplete_output'),
                ('incomplete', 'content_filter', None, 'llm_content_filter'),
                ('incomplete', 'PRIVATE reason', None, 'llm_incomplete'),
                ('failed', None, {'message': 'PRIVATE provider body'}, 'llm_response_failed'),
                ('completed', None, {'message': 'PRIVATE provider body'}, 'llm_response_failed'),
                ('completed', None, None, 'llm_empty_reply')):
            with self.subTest(status=status, code=code):
                raw = response(status, '' if code == 'llm_empty_reply' else '{"intent":{"name":"take_photo"}', reason, error)
                provider = RawProvider([raw])
                brain, view = self.brain(provider)
                with self.assertRaises(BrainResponseError) as caught:
                    brain.ask('PRIVATE current question')
                self.assertEqual(caught.exception.code, code)
                self.assertIsNone(view.last_reply)
                self.assertEqual(brain.history, [])
                metrics = brain.last_call_metrics
                self.assertEqual((metrics['max_output_tokens'], metrics['input_tokens'],
                    metrics['output_tokens'], metrics['reasoning_tokens']), (320, 2201, 320, 73))
                self.assertEqual(metrics['response_status'], status)
                self.assertNotIn('PRIVATE', json.dumps(metrics))
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertEqual(len(provider.requests), 1)

    def test_malformed_schema_and_timeout_retain_final_request_evidence_without_repair_call(self):
        for raw, code in ((response(text='PRIVATE not JSON'), 'expression_format'),
                          (TimeoutError('PRIVATE request body'), 'timeout')):
            with self.subTest(code=code):
                provider = RawProvider([raw])
                brain, view = self.brain(provider)
                with self.assertRaises(CloudError) as caught:
                    brain.ask('PRIVATE question')
                self.assertEqual(caught.exception.code, code)
                self.assertGreater(brain.last_call_metrics['request_instruction_chars'], 6000)
                self.assertEqual(brain.last_call_metrics['max_output_tokens'], 320)
                self.assertEqual(brain.last_call_metrics.get('response_status'),
                    'completed' if code == 'expression_format' else None)
                self.assertNotIn('PRIVATE', json.dumps(brain.last_call_metrics))
                self.assertIsNone(view.last_reply)
                self.assertEqual(len(provider.requests), 1)

    def test_worker_failure_receipt_has_actual_llm_cause_then_next_turn_recovers(self):
        provider = Provider([{'status': 'incomplete'}, envelope()])
        receipts = []
        diagnostics = TurnDiagnostics(enabled=True, completed_sink=receipts.append)
        view = FeatureClient(provider, character_life=True)
        session = IntegrationSession(integration_config(), provider, simulate=True,
            emotion_mode='auto', character_life=True, reply_client=view, diagnostics=diagnostics)
        self.addCleanup(session.close)
        for number in (1, 2):
            self.assertTrue(session.start('A safe fixture question.'))
            deadline = time.monotonic() + 3
            while session.busy and time.monotonic() < deadline:
                session.poll()
                time.sleep(.002)
            self.assertFalse(session.busy)
            trace = diagnostics.snapshot()
            if number == 1:
                self.assertEqual(trace['failure']['code'], 'llm_incomplete_output')
                self.assertEqual(trace['terminal'], 'llm_incomplete_output')
                self.assertEqual(trace['stage_metadata']['ai']['incomplete_reason'], 'max_output_tokens')
                self.assertEqual(trace['stage_metadata']['ai']['max_output_tokens'], 320)
                self.assertIsNone(view.last_reply)
            else:
                self.assertIsNone(trace['failure'])
                self.assertEqual(trace['stage_metadata']['ai']['response_status'], 'completed')
                self.assertEqual(session.snapshot()['playback'], 'ended')
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(len(session.brain.history), 2)

    def test_malformed_usage_cannot_smuggle_strings_bools_or_unbounded_counts(self):
        raw = response('PRIVATE', 'short', 'PRIVATE')
        raw.usage = {'input_tokens': 'PRIVATE', 'output_tokens': True,
            'output_tokens_details': {'reasoning_tokens': 10**99}}
        metrics = response_evidence(raw)
        self.assertEqual(metrics, dict(response_status='unknown', incomplete_reason='unknown', response_output_chars=5))


if __name__ == '__main__':
    unittest.main()
