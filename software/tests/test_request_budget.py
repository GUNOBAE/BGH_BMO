"""Admission boundaries, decimal estimates and cancellation-safe session limits."""
from concurrent.futures import ThreadPoolExecutor
import math
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.request_budget import RequestBudget


class Clock:
    def __init__(self):
        self.now = 10.
    def __call__(self):
        return self.now


class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        for name in ('socket.socket.connect', 'subprocess.Popen', 'bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(name, side_effect=AssertionError('external IO forbidden')))

    def denied(self, budget, code):
        before = budget.snapshot()['admitted_count']
        with self.assertRaises(FeatureError) as failure:
            budget.admit()
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(budget.snapshot()['admitted_count'], before)

    def test_personal_defaults_have_no_limits_or_invented_provider_price(self):
        budget = RequestBudget(clock=self.clock)
        for index in range(20):
            receipt = budget.admit()
            self.assertEqual(receipt['sequence'], index+1)
            self.assertIsNone(receipt['estimated_cost'])
            self.assertTrue(receipt['billing_unknown'])
        snapshot = budget.snapshot()
        self.assertFalse(snapshot['enabled'])
        self.assertFalse(snapshot['enforced'])
        self.assertTrue(snapshot['available'])
        self.assertIsNone(snapshot['remaining_requests'])
        self.assertIsNone(snapshot['cost_per_request'])
        self.assertIsNone(snapshot['estimated_cost'])

    def test_cooldown_rejects_before_consuming_then_reopens_at_exact_boundary(self):
        budget = RequestBudget(clock=self.clock, cooldown=2)
        budget.admit()
        self.clock.now += 1.9
        self.denied(budget, 'request_cooldown')
        self.assertAlmostEqual(budget.snapshot()['retry_after_seconds'], .1)
        self.clock.now = 12.
        self.assertEqual(budget.admit()['admitted_count'], 2)
        self.assertEqual(budget.snapshot()['admitted_count'], 2)

    def test_session_cap_counts_admitted_cancellation_or_failure_without_refund(self):
        budget = RequestBudget(clock=self.clock, max_requests=2)
        self.assertEqual(budget.admit()['remaining_requests'], 1)
        # Simulated cancellation/failure happens outside admission. Neither an
        # observer nor another click resets, refunds or retries the reservation.
        for _ in range(5):
            self.assertEqual(budget.snapshot()['admitted_count'], 1)
        self.assertEqual(budget.admit()['remaining_requests'], 0)
        self.denied(budget, 'session_request_limit')
        snapshot = budget.snapshot()
        self.assertFalse(snapshot['cancellation_refunds'])
        self.assertFalse(snapshot['automatic_retry'])
        self.assertEqual(snapshot['reset_policy'], 'new_session_only')

    def test_configured_decimal_estimate_exactly_reaches_cap_and_never_claims_billing(self):
        budget = RequestBudget(clock=self.clock, cost_per_request=.1, max_estimated_cost=.3)
        self.assertEqual([budget.admit()['estimated_cost'] for _ in range(3)], [.1, .2, .3])
        self.denied(budget, 'estimated_cost_limit')
        snapshot = budget.snapshot()
        self.assertEqual(snapshot['estimated_cost'], .3)
        self.assertEqual(snapshot['remaining_estimated_cost'], 0.)
        self.assertTrue(snapshot['billing_unknown'])
        self.assertEqual(snapshot['estimate_basis'], 'configured_per_admission_not_provider_billing')

    def test_no_turn_admitted_when_explicit_cap_cannot_cover_first_estimate(self):
        budget = RequestBudget(clock=self.clock, cost_per_request=.2, max_estimated_cost=.1)
        self.denied(budget, 'estimated_cost_limit')
        self.assertEqual(budget.snapshot()['admitted_count'], 0)
        self.assertEqual(budget.snapshot()['estimated_cost'], 0.)

    def test_zero_coefficient_is_explicit_estimate_not_an_assumed_free_provider(self):
        budget = RequestBudget(clock=self.clock, max_requests=1,
                               cost_per_request=0, max_estimated_cost=0)
        self.assertEqual(budget.admit()['estimated_cost'], 0.)
        self.denied(budget, 'session_request_limit')
        self.assertTrue(budget.snapshot()['billing_unknown'])

    def test_all_supplied_limits_apply_and_snapshot_is_non_consuming(self):
        budget = RequestBudget(clock=self.clock, cooldown=1, max_requests=3,
                               cost_per_request=.2, max_estimated_cost=.4)
        for _ in range(3):
            self.assertEqual(budget.snapshot()['admitted_count'], 0)
        budget.admit()
        self.denied(budget, 'request_cooldown')
        self.clock.now += 1
        budget.admit()
        self.denied(budget, 'estimated_cost_limit')
        self.assertEqual(budget.snapshot()['remaining_requests'], 1)

    def test_invalid_configuration_cannot_supply_a_cap_without_a_user_coefficient(self):
        cases = [dict(cooldown=-1), dict(cooldown=True), dict(cooldown=math.nan),
                 dict(cooldown=math.inf), dict(max_requests=0), dict(max_requests=1.5),
                 dict(max_requests=True), dict(cost_per_request=-.1), dict(cost_per_request=True),
                 dict(cost_per_request=math.inf), dict(max_estimated_cost=.5),
                 dict(max_estimated_cost=0), dict(max_estimated_cost=-1, cost_per_request=.1),
                 dict(max_estimated_cost=math.nan, cost_per_request=.1), dict(clock=None)]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(FeatureError) as failure:
                RequestBudget(**options)
            self.assertEqual(failure.exception.code, 'invalid_request_budget')

    def test_invalid_or_backwards_clock_fails_closed_without_damaging_counts(self):
        budget = RequestBudget(clock=self.clock, cooldown=1)
        budget.admit()
        for value in (9., -1., math.nan, math.inf, True, '11'):
            with self.subTest(clock=value):
                self.clock.now = value
                self.denied(budget, 'request_budget_clock_invalid')
                self.assertEqual(budget.snapshot()['code'], 'request_budget_clock_invalid')
                self.assertIsNone(budget.snapshot()['retry_after_seconds'])
        self.clock.now = 11.
        self.assertEqual(budget.admit()['admitted_count'], 2)

    def test_clock_exception_is_sanitized_and_does_not_call_any_provider(self):
        def broken():
            raise RuntimeError('private details must not escape')
        budget = RequestBudget(clock=broken, max_requests=1)
        with self.assertRaises(FeatureError) as failure:
            budget.admit()
        self.assertEqual(failure.exception.code, 'request_budget_clock_invalid')
        self.assertNotIn('private', str(failure.exception))
        self.assertEqual(budget.snapshot()['admitted_count'], 0)

    def test_unrepresentable_estimate_cannot_publish_invalid_json_or_consume_a_turn(self):
        import json
        budget = RequestBudget(clock=self.clock, cost_per_request=1e308)
        budget.admit()
        self.denied(budget, 'request_budget_estimate_invalid')
        self.assertEqual(budget.snapshot()['admitted_count'], 1)
        json.dumps(budget.snapshot(), allow_nan=False)

    def test_parallel_attempts_cannot_overspend_the_same_last_session_slot(self):
        budget = RequestBudget(clock=self.clock, max_requests=3,
                               cost_per_request=.1, max_estimated_cost=.3)
        def attempt(_):
            try:
                return budget.admit()['sequence']
            except FeatureError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(16)))
        self.assertEqual(sorted(value for value in results if type(value) is int), [1, 2, 3])
        self.assertEqual(results.count('session_request_limit'), 13)
        self.assertEqual(budget.snapshot()['admitted_count'], 3)
        self.assertEqual(budget.snapshot()['estimated_cost'], .3)


if __name__ == '__main__':
    unittest.main()
