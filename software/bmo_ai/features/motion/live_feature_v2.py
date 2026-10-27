"""Optional live V2 motion scheduler for the existing BMO FeatureHost.

Only an explicitly injected, already reviewed ArmHostV2 can enable this class.
The normal desktop continues to construct mock-only MotionFeature, untouched.
No serial, USB, GPIO, PWM, IMU, audio, or network is opened here. All model
gestures resolve through a small immutable allowlist, never raw motor angles.
ACK means the RP2040 accepted a command; position is *always* unverified.
"""
from collections import deque
import math
import time

from ..contracts import FeatureError, FeatureEvent, HostResult, request_id_ok
from .arm_host_v2 import ArmHostV2, ArmV2Error
from .gesture_rules_v2 import LEGACY_REVIEWED_LIMITS, plan_gesture


class LiveArmMotionFeatureV2:
    def __init__(self, host, *, clock=time.monotonic, heartbeat_s=.12,
                 ttl=1.0, cooldown=2.0):
        if not isinstance(host, ArmHostV2) or host.simulated:
            raise FeatureError("live_arm_host_required")
        if (type(heartbeat_s) not in (int, float) or not 0 < heartbeat_s <= .20
                or type(ttl) not in (int, float) or not 0 < ttl <= 2
                or type(cooldown) not in (int, float) or cooldown < 0):
            raise FeatureError("invalid_motion_config")
        if host.profile != LEGACY_REVIEWED_LIMITS:
            raise FeatureError("original_legacy_profile_required")
        self.host, self.clock = host, clock
        self.heartbeat_s, self.ttl, self.cooldown = heartbeat_s, ttl, cooldown
        self.context = None
        self.pending = None
        self.events = deque(maxlen=32)
        self._seen = set()
        self._generation = 0
        self._stage = 0
        self._side = 0
        self._not_before = 0.
        self._last_gesture = float("-inf")
        self._next_ping = 0.
        self._counter = 0
        self._position_guess = {"left": 600, "right": 2400}
        self.stopped = False
        self.closed = False
        self.fault = None

    def _now(self):
        value = self.clock()
        if type(value) not in (float, int) or not math.isfinite(value):
            raise FeatureError("invalid_clock")
        return value

    def _next_id(self):
        self._counter += 1
        if self._counter >= 999999999:
            raise FeatureError("motion_id_exhausted")
        return "v2_%08d" % self._counter

    def _event(self, request_id, code, status="unknown", *, executed=False):
        self.events.append(FeatureEvent(
            "motion_result", self._generation, request_id, code,
            {"status": status, "simulated": False, "executed": executed,
             "completed": False, "completion": "position_unverified",
             "position_verified": False}))

    def set_context(self, session_id, turn_id, mode_generation):
        if type(turn_id) is int and turn_id >= 0:
            turn_id = str(turn_id)
        if (not request_id_ok(session_id) or not request_id_ok(turn_id)
                or type(mode_generation) is not int or mode_generation < 0):
            raise FeatureError("invalid_motion_context")
        candidate = (session_id, turn_id, mode_generation)
        if candidate != self.context:
            self.cancel()
            self.context = candidate
            self._generation = mode_generation

    def submit(self, gesture_id, *, request_id, session_id, turn_id,
               mode_generation, started_at, explicit=False):
        if not request_id_ok(request_id):
            raise FeatureError("invalid_request_id")
        if (self.closed or self.stopped or self.fault or self.pending is not None
                or request_id in self._seen):
            return HostResult(request_id, "rejected", "motion_unavailable",
                              simulated=False)
        if self.context != (session_id, str(turn_id), mode_generation):
            return HostResult(request_id, "rejected", "stale_context",
                              simulated=False)
        try:
            steps = plan_gesture(gesture_id, from_model=not explicit)
        except ValueError:
            return HostResult(request_id, "rejected", "invalid_gesture",
                              simulated=False)
        now = self._now()
        if (not explicit and
                (type(started_at) not in (int, float)
                 or not 0 <= now - started_at <= self.ttl)):
            return HostResult(request_id, "rejected", "stale_playback_start",
                              simulated=False)
        if now - self._last_gesture < self.cooldown:
            return HostResult(request_id, "rejected", "gesture_cooldown",
                              simulated=False)
        self._seen.add(request_id)
        self.pending = {"request": request_id, "steps": steps}
        self._stage, self._side, self._not_before = 0, 0, now
        return HostResult(request_id, "accepted", "gesture_queued",
                          simulated=False, executed=False, completed=False)

    def _heartbeat(self, now):
        if not self.host.armed or now < self._next_ping:
            return
        self.host.ping(self._next_id())
        self._next_ping = self._now() + self.heartbeat_s

    def poll(self):
        if self.closed or self.stopped or self.fault is not None:
            events = tuple(self.events)
            self.events.clear()
            return events
        now = self._now()
        try:
            # Keep one actual live USB owner heartbeating even between gestures.
            self._heartbeat(now)
            if self.pending is not None:
                if self.host.boot_id is None:
                    reply = self.host.hello(self._next_id())
                    if reply.code != "READY":
                        raise ArmV2Error("arm_controller_unverified")
                if not self.host.armed:
                    self.host.arm(self._next_id())
                    self._next_ping = self._now() + self.heartbeat_s
                if now >= self._not_before:
                    steps = self.pending["steps"]
                    if self._stage >= len(steps):
                        self._last_gesture = now
                        self._event(self.pending["request"], "ack_position_unverified",
                                    "running", executed=True)
                        self.pending = None
                    else:
                        step = steps[self._stage]
                        side_targets = tuple((side, pulse) for side, pulse in
                                             (("left", step.left), ("right", step.right))
                                             if pulse is not None)
                        if self._side < len(side_targets):
                            side, pulse = side_targets[self._side]
                            self.host.move(self._next_id(), side, pulse)
                            self._side += 1
                            previous = self._position_guess[side]
                            self._position_guess[side] = pulse
                            # Never claim actual arrival; estimate the time
                            # allowed by the firmware's bounded pulse slew.
                            travel = abs(pulse - previous) / 30 * .02
                            self._not_before = self._now() + max(step.dwell_s, travel + .15)
                        else:
                            self._stage += 1
                            self._side = 0
                            self._not_before = self._now()
        except (ArmV2Error, FeatureError):
            self.fault = "arm_link_unknown"
            if self.pending is not None:
                self._event(self.pending["request"], "arm_link_unknown")
                self.pending = None
            # Firmware watchdog will latch if link stays unknown. Do not retry
            # any movement or manufacture STOP acknowledgement.
        result = tuple(self.events)
        self.events.clear()
        return result

    def cancel(self, request_id=None):
        pending = self.pending
        if pending is None or request_id not in (None, pending["request"]):
            return
        self.pending = None
        if self.host.armed and not self.host.unknown:
            try:
                self.host.hold(self._next_id())
            except ArmV2Error:
                self.fault = "arm_link_unknown"
        self._event(pending["request"],
                    "gesture_cancelled" if self.fault is None else "arm_link_unknown",
                    "cancelled" if self.fault is None else "unknown")

    def stop(self):
        self.cancel()
        self.stopped = True
        if self.host.armed and not self.host.unknown:
            try:
                self.host.stop(self._next_id())
            except ArmV2Error:
                self.fault = "arm_stop_unverified"

    def close(self):
        if not self.closed:
            self.stop()
            self.closed = True

    def snapshot(self):
        return {"simulated": False, "real_enabled": True,
                "calibration_ready": self.host.calibrated,
                "readiness": "UNVERIFIED" if not self.host.calibrated else "READY",
                "reason_code": "arm_link_unknown" if self.fault else "position_unverified",
                "queued": int(self.pending is not None),
                "active": self.pending is not None,
                "stopped": self.stopped, "closed": self.closed,
                "position_verified": False, "rejected_acks": int(self.host.unknown),
                "controller": self.host.snapshot()}
