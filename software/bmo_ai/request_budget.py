"""Opt-in, in-memory session admission limits before microphone/provider work.

This is a configured estimate per admitted turn, never provider billing. Defaults
preserve the personal candidate: no cooldown, count limit or assumed price.
Cancellation/failure after admission does not refund or retry that admission.
The owning Host's existing concurrency gate remains separate.
"""
from decimal import Decimal
import math
from threading import RLock
import time

from .features.contracts import FeatureError


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


class RequestBudget:
    def __init__(self, *, clock=time.monotonic, cooldown=0, max_requests=None,
                 cost_per_request=None, max_estimated_cost=None):
        if (not callable(clock) or not _finite(cooldown) or cooldown < 0
                or max_requests is not None and (type(max_requests) is not int or max_requests < 1)
                or any(value is not None and (not _finite(value) or value < 0)
                       for value in (cost_per_request, max_estimated_cost))
                or max_estimated_cost is not None and cost_per_request is None):
            raise FeatureError('invalid_request_budget')
        self.clock = clock
        self.cooldown = float(cooldown)
        self.max_requests = max_requests
        # Decimal(str(...)) honors configured decimal coefficients/caps and
        # avoids denying the third .1 estimate under an explicitly chosen .3.
        self._coefficient = None if cost_per_request is None else Decimal(str(cost_per_request))
        self._cap = None if max_estimated_cost is None else Decimal(str(max_estimated_cost))
        self._guard = RLock()
        self._admitted = 0
        self._last_admitted = self._last_observed = None

    def _now(self):
        try:
            now = self.clock()
        except Exception:
            raise FeatureError('request_budget_clock_invalid') from None
        if (not _finite(now) or now < 0
                or self._last_observed is not None and now < self._last_observed):
            raise FeatureError('request_budget_clock_invalid')
        return now

    def _estimated(self, admitted=None):
        return None if self._coefficient is None else self._coefficient * (
            self._admitted if admitted is None else admitted)

    def _denial(self, now):
        if self.max_requests is not None and self._admitted >= self.max_requests:
            return 'session_request_limit'
        if self._coefficient is not None and not math.isfinite(float(self._estimated(self._admitted+1))):
            return 'request_budget_estimate_invalid'
        if self._cap is not None and self._estimated(self._admitted+1) > self._cap:
            return 'estimated_cost_limit'
        if (self._last_admitted is not None
                and now-self._last_admitted < self.cooldown):
            return 'request_cooldown'
        return None

    def admit(self):
        """Atomically consume one admission or reject before any external IO."""
        with self._guard:
            now = self._now()
            self._last_observed = now
            reason = self._denial(now)
            if reason is not None:
                raise FeatureError(reason)
            self._admitted += 1
            self._last_admitted = now
            estimate = self._estimated()
            return dict(sequence=self._admitted, admitted_count=self._admitted,
                admitted_at=now,
                remaining_requests=None if self.max_requests is None else self.max_requests-self._admitted,
                estimated_cost=None if estimate is None else float(estimate),
                estimate_basis='configured_per_admission_not_provider_billing',
                billing_unknown=True, automatic_retry=False)

    def snapshot(self):
        """Safe non-consuming status; it is not a reservation or permission."""
        with self._guard:
            try:
                now = self._now()
                denial = self._denial(now)
                retry_after = (max(0., self.cooldown-(now-self._last_admitted))
                               if self._last_admitted is not None else 0.)
            except FeatureError:
                denial, retry_after = 'request_budget_clock_invalid', None
            estimate = self._estimated()
            configured = (self.cooldown > 0 or self.max_requests is not None
                          or self._coefficient is not None or self._cap is not None)
            return dict(enabled=configured,
                enforced=self.cooldown > 0 or self.max_requests is not None or self._cap is not None,
                admitted_count=self._admitted,
                max_requests=self.max_requests,
                remaining_requests=None if self.max_requests is None else max(0, self.max_requests-self._admitted),
                cooldown_seconds=self.cooldown, retry_after_seconds=retry_after,
                available=denial is None, code=denial or 'request_budget_ready',
                cost_per_request=None if self._coefficient is None else float(self._coefficient),
                max_estimated_cost=None if self._cap is None else float(self._cap),
                estimated_cost=None if estimate is None else float(estimate),
                remaining_estimated_cost=None if self._cap is None else float(max(Decimal(0), self._cap-estimate)),
                estimate_basis='configured_per_admission_not_provider_billing',
                billing_unknown=True, automatic_retry=False,
                cancellation_refunds=False, reset_policy='new_session_only')
