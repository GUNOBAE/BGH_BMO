"""Passive, bounded IMU quality checks and synthetic-only reaction candidates.

No sensor/serial polling exists. Real observations remain physically unverified,
even when numeric checks pass. No output authorizes a motor or safety decision.
"""
from collections import deque
import json
import math
import time

from ...hardware import Heartbeat, IMUSample, TelemetryError, parse_telemetry
from ..contracts import FeatureError, FeatureEvent, request_id_ok


class IMUHealthMonitor:
    def __init__(self, *, clock=time.monotonic, freshness=1.0, min_samples=8,
                 health_duration=0.3, frozen_duration=1.0, event_duration=0.25,
                 event_cooldown=1.0, accel_full_scale_g=16.0, gyro_full_scale_dps=2000.0):
        durations = (freshness, health_duration, frozen_duration, event_duration, event_cooldown)
        if (any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in durations) or type(min_samples) is not int or not 3 <= min_samples <= 64
                or accel_full_scale_g not in (2, 4, 8, 16)
                or gyro_full_scale_dps not in (250, 500, 1000, 2000)):
            raise FeatureError("invalid_imu_config")
        self.clock, self.freshness, self.min_samples = clock, freshness, min_samples
        self.health_duration, self.frozen_duration = health_duration, frozen_duration
        self.event_duration, self.event_cooldown = event_duration, event_cooldown
        self.accel_full_scale_g, self.gyro_full_scale_dps = accel_full_scale_g, gyro_full_scale_dps
        self._samples = deque(maxlen=64)
        self._events = deque(maxlen=32)
        self._boot = self._sequence = self._timestamp = None
        self._controller_origin = None
        self._last_sample = self._last_heartbeat = self._last_now = None
        self._last_vector = self._same_since = None
        self._synthetic = False
        self._health, self._reason = "unknown", "no_samples"
        self._candidate = self._candidate_since = self._reaction = None
        self._last_event = None
        self._generation = 0
        self._closed = False
        self._accepted = self._rejected = self._missing = self._restarts = 0

    def _now(self):
        now = self.clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or (self._last_now is not None and now < self._last_now)):
            raise FeatureError("invalid_clock")
        self._last_now = now
        return now

    def _health_state(self, health, reason):
        if (health, reason) != (self._health, self._reason):
            self._health, self._reason = health, reason
            self._events.append(FeatureEvent("imu_health", self._generation, code=reason,
                data={"health": health, "synthetic": self._synthetic,
                      "physical_verified": False, "usable_for_control": False}))
        if health != "healthy":
            self._candidate = self._candidate_since = self._reaction = None

    def _reset_window(self):
        self._samples.clear()
        self._last_vector = self._same_since = None
        self._candidate = self._candidate_since = self._reaction = None

    @staticmethod
    def _validated(sample):
        try:
            if isinstance(sample, bytes):
                result = parse_telemetry(sample)
            elif isinstance(sample, (IMUSample, Heartbeat)):
                value = {"v": 1, "type": "imu" if isinstance(sample, IMUSample) else "heartbeat",
                         "seq": sample.sequence, "timestamp_ms": sample.timestamp_ms}
                if isinstance(sample, IMUSample):
                    value.update(accel_g=list(sample.acceleration_g), gyro_dps=list(sample.gyroscope_dps),
                                 temperature_c=sample.temperature_c)
                result = parse_telemetry(json.dumps(value, separators=(",", ":")).encode())
            else:
                raise FeatureError("invalid_imu_sample")
        except (TelemetryError, ValueError, TypeError, OverflowError, AttributeError):
            raise FeatureError("invalid_imu_sample") from None
        if not isinstance(result, (IMUSample, Heartbeat)):
            raise FeatureError("invalid_imu_sample")
        return result

    def ingest(self, sample, *, controller_boot, synthetic=False, mode_generation=0,
               stationary_reference=False):
        if (self._closed or not request_id_ok(controller_boot) or type(synthetic) is not bool
                or type(stationary_reference) is not bool or type(mode_generation) is not int
                or mode_generation < 0):
            raise FeatureError("invalid_imu_context")
        self._generation = mode_generation
        now = self._now()
        try:
            sample = self._validated(sample)
        except FeatureError:
            self._rejected += 1
            self._reset_window()
            self._health_state("degraded", "invalid_schema")
            raise
        restart = self._boot is not None and controller_boot != self._boot
        source_change = self._boot is not None and synthetic != self._synthetic
        if restart or source_change:
            self._reset_window()
            self._sequence = self._timestamp = self._last_sample = None
            self._controller_origin = self._last_heartbeat = None
            if restart:
                self._restarts += 1
            self._synthetic = synthetic
            self._health_state("degraded", "controller_restart" if restart else "source_changed")
        self._boot, self._synthetic = controller_boot, synthetic
        gap_reason = None
        if self._sequence is not None:
            advance = (sample.sequence - self._sequence) & 0xffffffff
            ticks = (sample.timestamp_ms - self._timestamp) & 0xffffffff
            if not 0 < advance < 0x80000000 or not 0 < ticks < 0x80000000:
                self._rejected += 1
                self._reset_window()
                self._health_state("degraded", "reordered_sample")
                return self._drain()
            if advance > 1:
                self._missing += advance - 1
                gap_reason = "missing_samples"
            if ticks > self.freshness * 1000:
                gap_reason = "timestamp_gap"
        self._sequence, self._timestamp = sample.sequence, sample.timestamp_ms
        if self._controller_origin is None:
            self._controller_origin = sample.timestamp_ms, now
        controller_elapsed = ((sample.timestamp_ms - self._controller_origin[0]) & 0xffffffff) / 1000
        if abs(controller_elapsed - (now - self._controller_origin[1])) > self.freshness:
            gap_reason = "timestamp_mismatch"
        if isinstance(sample, Heartbeat):
            self._last_heartbeat = now
            if self._last_sample is None:
                self._health_state("unknown", "heartbeat_only")
            return self._drain()
        if self._last_sample is not None and now - self._last_sample > self.freshness:
            gap_reason = gap_reason or "stale_recovery"
        if gap_reason:
            self._reset_window()
        self._last_sample = now
        self._accepted += 1
        accel, gyro = sample.acceleration_g, sample.gyroscope_dps
        # Temperature jitter does not establish that acceleration/gyro axes move.
        vector = (*accel, *gyro)
        if vector == self._last_vector:
            if self._same_since is None:
                self._same_since = now
        else:
            self._last_vector, self._same_since = vector, now
        self._samples.append((now, sample))
        if (any(abs(value) >= self.accel_full_scale_g * .99 for value in accel)
                or any(abs(value) >= self.gyro_full_scale_dps * .99 for value in gyro)):
            self._reset_window()
            self._health_state("fault", "sensor_saturation")
        elif self._same_since is not None and now - self._same_since >= self.frozen_duration:
            self._samples.clear()
            self._health_state("fault", "frozen_sample")
        elif stationary_reference and self._stationary_bias():
            self._samples.clear()
            self._health_state("fault", "stationary_bias")
        elif gap_reason:
            self._health_state("degraded", gap_reason)
        elif len(self._samples) >= self.min_samples and now - self._samples[0][0] >= self.health_duration:
            self._health_state("healthy" if synthetic else "unknown",
                               "synthetic_checks_pass" if synthetic else "physical_unverified")
        elif self._health not in ("fault", "degraded"):
            self._health_state("unknown", "warming_up")
        if self._health == "healthy" and synthetic:
            self._reaction_candidate(sample, now)
        return self._drain()

    def _stationary_bias(self):
        if len(self._samples) < self.min_samples or self._samples[-1][0] - self._samples[0][0] < self.health_duration:
            return False
        count = len(self._samples)
        accel_mean = [sum(item.acceleration_g[axis] for _, item in self._samples) / count for axis in range(3)]
        gyro_mean = [sum(item.gyroscope_dps[axis] for _, item in self._samples) / count for axis in range(3)]
        return (abs(math.sqrt(sum(v * v for v in accel_mean)) - 1) > .20
                or math.sqrt(sum(v * v for v in gyro_mean)) > 5)

    def _reaction_candidate(self, sample, now):
        norm_delta = abs(math.sqrt(sum(value * value for value in sample.acceleration_g)) - 1)
        gyro = math.sqrt(sum(value * value for value in sample.gyroscope_dps))
        tilt = max(abs(sample.acceleration_g[0]), abs(sample.acceleration_g[1]))
        if gyro > 20 or norm_delta > .25 or (self._reaction == "motion" and (gyro > 8 or norm_delta > .10)):
            candidate = "motion"
        elif tilt > .40 or (self._reaction == "tilt" and tilt > .25):
            candidate = "tilt"
        elif norm_delta < .10 and gyro < 3:
            candidate = "still"
        else:
            candidate = None
        if candidate != self._candidate:
            self._candidate, self._candidate_since = candidate, now
        if (candidate is not None and candidate != self._reaction
                and now - self._candidate_since >= self.event_duration
                and (self._last_event is None or now - self._last_event >= self.event_cooldown)):
            self._reaction, self._last_event = candidate, now
            self._events.append(FeatureEvent("imu_candidate", self._generation, code="synthetic_candidate",
                data={"candidate": candidate, "synthetic": True,
                      "physical_verified": False, "usable_for_control": False}))

    def _drain(self):
        result = tuple(self._events)
        self._events.clear()
        return result

    def poll(self):
        if not self._closed and self._last_sample is not None and self._now() - self._last_sample > self.freshness:
            self._reset_window()
            if self._health != "fault":
                self._health_state("degraded", "stale_sample")
        return self._drain()

    def snapshot(self):
        now = self._now()
        if not self._closed and self._last_sample is not None and now - self._last_sample > self.freshness:
            self._reset_window()
            if self._health != "fault":
                self._health_state("degraded", "stale_sample")
        return {"health": self._health, "reason_code": self._reason, "synthetic": self._synthetic,
                "physical_verified": False, "usable_for_control": False, "readiness": "UNVERIFIED",
                "sample_age": None if self._last_sample is None else max(0.0, now - self._last_sample),
                "accepted": self._accepted, "rejected": self._rejected, "missing": self._missing,
                "controller_restarts": self._restarts, "retained_samples": len(self._samples),
                "candidate": self._reaction, "closed": self._closed}

    def close(self):
        self._closed = True
        self._reset_window()
        self._events.clear()
