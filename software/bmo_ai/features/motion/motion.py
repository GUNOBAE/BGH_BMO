"""Turn-scoped gesture scheduler with a fake-only ACK boundary."""
from collections import deque
from dataclasses import dataclass
import math
import time

from ..contracts import FeatureError, FeatureEvent, GESTURES, HostResult, request_id_ok
from .calibration import CalibrationProfile, load_profile
from .protocol import FakeMotionTransport, MotionCommand


@dataclass(frozen=True, slots=True)
class _Pending:
    request_id: str
    gesture_id: str
    context: tuple
    started_at: float | None
    explicit: bool
    admitted_at: float


class MotionFeature:
    """No real backend exists: calibration or environment cannot unlock motors.

    The host sets the current context before submitting; automatic decoration
    also requires a real player-start timestamp from that same admitted turn.
    Explicit commands are separate intents, never inferred from happy emotion.
    """
    def __init__(self, *, transport=None, profile=None, clock=time.monotonic,
                 simulated=True, ttl=1.0, cooldown=2.0, ack_timeout=0.5,
                 queue_limit=2, ledger_limit=256):
        if (type(simulated) is not bool or any(type(v) not in (int, float)
                or not math.isfinite(v) or v <= 0 for v in (ttl, ack_timeout))
                or type(cooldown) not in (int, float) or not math.isfinite(cooldown) or cooldown < 0
                or type(queue_limit) is not int or not 1 <= queue_limit <= 16
                or type(ledger_limit) is not int or not 1 <= ledger_limit <= 4096):
            raise FeatureError("invalid_motion_config")
        self.clock, self.simulated = clock, simulated
        self.transport = FakeMotionTransport(clock=clock) if transport is None else transport
        self.profile = profile if isinstance(profile, CalibrationProfile) or profile is None else load_profile(profile)
        self.ttl, self.cooldown, self.ack_timeout = ttl, cooldown, ack_timeout
        self.queue_limit, self.ledger_limit = queue_limit, ledger_limit
        self._context = None
        self._queue = deque()
        self._active = None
        self._sent_at = self._boot = self._last_dispatch = None
        self._ledger = {}
        self._auto_turns = set()
        self._events = deque(maxlen=64)
        self._closed = self._stopped = False
        self._rejected_acks = 0

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise FeatureError("invalid_clock")
        return value

    @staticmethod
    def _context_value(session_id, turn_id, mode_generation):
        if type(turn_id) is int and turn_id >= 0:
            turn_id = str(turn_id)
        if (not request_id_ok(session_id) or not request_id_ok(turn_id)
                or type(mode_generation) is not int or mode_generation < 0):
            raise FeatureError("invalid_motion_context")
        return session_id, turn_id, mode_generation

    def set_context(self, session_id, turn_id, mode_generation):
        value = self._context_value(session_id, turn_id, mode_generation)
        if value != self._context:
            self.cancel()
            self._context = value
            self._stopped = False

    def _result(self, request_id, status, code, *, completion="not_started"):
        return HostResult(request_id, status, code, simulated=True, executed=False,
                          completed=False, completion=completion)

    def _publish(self, pending, status, code, *, completion="not_started"):
        result = self._result(pending.request_id, status, code, completion=completion)
        fingerprint = (pending.gesture_id, pending.context, pending.started_at, pending.explicit)
        self._ledger[pending.request_id] = (fingerprint, result)
        self._events.append(FeatureEvent("motion_result", pending.context[2], pending.request_id,
                                        code, {**result.public(), "gesture_id": pending.gesture_id,
                                               "position_verified": False}))
        return result

    def submit(self, gesture_id, *, request_id, session_id, turn_id,
               mode_generation, started_at, explicit=False):
        if not request_id_ok(request_id):
            raise FeatureError("invalid_request_id")
        context = self._context_value(session_id, turn_id, mode_generation)
        if type(explicit) is not bool:
            raise FeatureError("invalid_motion_request")
        if type(gesture_id) is not str or gesture_id not in GESTURES:
            return self._result(request_id, "rejected", "invalid_gesture")
        if started_at is not None and (type(started_at) not in (int, float) or not math.isfinite(started_at)):
            return self._result(request_id, "rejected", "invalid_playback_start")
        pending = _Pending(request_id, gesture_id, context, started_at, explicit, self._now())
        fingerprint = (gesture_id, context, started_at, explicit)
        previous = self._ledger.get(request_id)
        if previous is not None:
            return previous[1] if previous[0] == fingerprint else self._result(request_id, "rejected", "request_conflict")
        if len(self._ledger) >= self.ledger_limit:
            return self._result(request_id, "rejected", "motion_ledger_full")
        if self._closed or self._stopped:
            return self._publish(pending, "rejected", "motion_stopped")
        if context != self._context:
            return self._publish(pending, "rejected", "stale_context")
        if gesture_id == "none":
            return self._publish(pending, "simulated", "no_gesture")
        if not self.simulated or not isinstance(self.transport, FakeMotionTransport):
            return self._publish(pending, "rejected", "real_disabled")
        now = self._now()
        if (not explicit and started_at is None) or (started_at is not None and not 0 <= now - started_at <= self.ttl):
            return self._publish(pending, "rejected", "stale_playback_start")
        if not explicit and context in self._auto_turns:
            return self._publish(pending, "rejected", "turn_gesture_limit")
        if self._active is None and self._last_dispatch is not None and now - self._last_dispatch < self.cooldown:
            return self._publish(pending, "rejected", "gesture_cooldown")
        if len(self._queue) + int(self._active is not None) >= self.queue_limit:
            return self._publish(pending, "rejected", "gesture_queue_full")
        if not explicit:
            self._auto_turns.add(context)
        self._queue.append(pending)
        return self._publish(pending, "accepted", "gesture_queued")

    def poll(self):
        if not self._closed and not self._stopped and (self._active is not None or self._queue):
            now = self._now()
            try:
                hello = self.transport.handshake()
                if (type(hello.version) is not int or hello.version != 1
                        or hello.channel != "dedicated_data" or hello.controller_dedup is not True
                        or not request_id_ok(hello.controller_boot)):
                    raise FeatureError("protocol_unverified")
                if self._boot is not None and hello.controller_boot != self._boot:
                    if self._active is not None:
                        self._publish(self._active, "unknown", "controller_reboot_unknown", completion="position_unverified")
                        self._active = None
                    while self._queue:
                        self._publish(self._queue.popleft(), "cancelled", "controller_reboot_no_replay")
                self._boot = hello.controller_boot
                if self._active is not None and now - self._sent_at >= self.ack_timeout:
                    self._publish(self._active, "unknown", "ack_timeout_unknown", completion="position_unverified")
                    self._active = None
                for ack in self.transport.poll():
                    if (self._active is None or ack.request_id != self._active.request_id
                            or ack.controller_boot != self._boot or type(ack.accepted) is not bool):
                        self._rejected_acks += 1
                        continue
                    self._publish(self._active, "simulated" if ack.accepted else "rejected",
                                  "ack_position_unverified" if ack.accepted else "controller_rejected",
                                  completion="position_unverified")
                    self._active = None
                if self._active is None and self._queue:
                    pending = self._queue[0]
                    reference = pending.admitted_at if pending.started_at is None else pending.started_at
                    if pending.context != self._context or not 0 <= now - reference <= self.ttl:
                        self._queue.popleft()
                        self._publish(pending, "cancelled", "stale_gesture")
                    elif self._last_dispatch is None or now - self._last_dispatch >= self.cooldown:
                        self._queue.popleft()
                        self._boot = hello.controller_boot
                        self._active, self._sent_at, self._last_dispatch = pending, now, now
                        self.transport.send(MotionCommand(pending.request_id, pending.gesture_id,
                                                         *pending.context, self._boot))
                        self._publish(pending, "running", "command_sent", completion="position_unverified")
            except FeatureError as error:
                if self._active is not None:
                    self._publish(self._active, "unknown", "connection_lost_unknown", completion="position_unverified")
                    self._active = None
                while self._queue:
                    self._publish(self._queue.popleft(), "rejected", error.code)
            except Exception:
                if self._active is not None:
                    self._publish(self._active, "unknown", "controller_error_unknown", completion="position_unverified")
                    self._active = None
                while self._queue:
                    self._publish(self._queue.popleft(), "failed", "controller_unavailable")
        result = tuple(self._events)
        self._events.clear()
        return result

    def cancel(self, request_id=None):
        if request_id is not None and not request_id_ok(request_id):
            raise FeatureError("invalid_request_id")
        if self._active is not None and request_id in (None, self._active.request_id):
            self.transport.stop()
            self._publish(self._active, "unknown", "cancelled_position_unknown", completion="position_unverified")
            self._active = None
        retained = deque()
        while self._queue:
            pending = self._queue.popleft()
            if request_id in (None, pending.request_id):
                self._publish(pending, "cancelled", "gesture_cancelled")
            else:
                retained.append(pending)
        self._queue = retained

    def stop(self):
        self.cancel()
        self._stopped = True

    def close(self):
        if not self._closed:
            self.stop()
            if isinstance(self.transport, FakeMotionTransport):
                self.transport.close()
            self._closed = True

    def snapshot(self):
        return {"simulated": True, "real_enabled": False,
                "calibration_ready": bool(self.profile and self.profile.reviewed),
                "readiness": "UNVERIFIED", "reason_code": "real_disabled",
                "queued": len(self._queue), "active": self._active is not None,
                "stopped": self._stopped, "closed": self._closed,
                "position_verified": False, "rejected_acks": self._rejected_acks}
