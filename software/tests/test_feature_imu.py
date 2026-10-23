"""Synthetic IMU traces only; no sensor polling, serial, offsets or GPT calls."""
import json
import math
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.motion import IMUHealthMonitor, MotionFeature
from bmo_ai.hardware import Heartbeat, IMUSample


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FeatureIMUTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "os.open"):
            self.enterContext(patch(target, side_effect=AssertionError("Hardware/file IO forbidden")))
        self.clock = Clock()
        self.monitor = IMUHealthMonitor(clock=self.clock)
        self.sequence = 0

    def sample(self, *, accel=(0, 0, 1), gyro=(0, 0, 0), jitter=True):
        sign = 1 if self.sequence % 2 else -1
        accel = (accel[0] + sign * .001, accel[1], accel[2]) if jitter else accel
        gyro = (gyro[0] + sign * .01, gyro[1], gyro[2]) if jitter else gyro
        return IMUSample(self.sequence & 0xffffffff, round(self.clock.now * 1000) & 0xffffffff,
                         accel, gyro, 25 + sign * .001 if jitter else 25)

    def feed(self, count=16, *, synthetic=True, boot="fixture_boot", step=.05, **kwargs):
        events = []
        stationary = kwargs.pop("stationary_reference", False)
        for _ in range(count):
            events.extend(self.monitor.ingest(self.sample(**kwargs), controller_boot=boot,
                                              synthetic=synthetic, mode_generation=7,
                                              stationary_reference=stationary))
            self.sequence += 1
            self.clock.now += step
        return events

    def test_initial_unknown_and_heartbeat_only_never_certify_sensor_health(self):
        self.assertEqual(self.monitor.snapshot()["health"], "unknown")
        for index in range(20):
            self.clock.now = index * .05
            self.monitor.ingest(Heartbeat(index, index * 50), controller_boot="fixture_boot", synthetic=True)
        state = self.monitor.snapshot()
        self.assertEqual((state["health"], state["reason_code"]), ("unknown", "heartbeat_only"))
        self.assertEqual(state["accepted"], 0)
        self.assertIsNone(state["candidate"])

    def test_clean_synthetic_trace_passes_numeric_checks_but_is_never_a_control_input(self):
        events = self.feed()
        state = self.monitor.snapshot()
        self.assertEqual(state["health"], "healthy")
        self.assertEqual(state["reason_code"], "synthetic_checks_pass")
        self.assertFalse(state["physical_verified"])
        self.assertFalse(state["usable_for_control"])
        candidates = [event for event in events if event.kind == "imu_candidate"]
        self.assertEqual([event.data["candidate"] for event in candidates], ["still"])
        self.assertTrue(all(event.mode_generation == 7 for event in events))
        self.assertTrue(all(event.data["usable_for_control"] is False for event in events))

    def test_real_observation_trace_remains_unverified_and_emits_zero_product_candidates(self):
        events = self.feed(40, synthetic=False, gyro=(30, 0, 0))
        state = self.monitor.snapshot()
        self.assertEqual((state["health"], state["reason_code"]), ("unknown", "physical_unverified"))
        self.assertFalse(state["usable_for_control"])
        self.assertFalse(any(event.kind == "imu_candidate" for event in events))

    def test_tilt_motion_still_candidates_require_duration_and_respect_cooldown(self):
        events = self.feed(25)
        events += self.feed(28, accel=(.6, 0, .8))
        events += self.feed(28, gyro=(35, 0, 0))
        events += self.feed(28)
        candidates = [event.data["candidate"] for event in events if event.kind == "imu_candidate"]
        self.assertEqual(candidates, ["still", "tilt", "motion", "still"])
        self.assertTrue(all(event.code == "synthetic_candidate" for event in events if event.kind == "imu_candidate"))

    def test_hysteresis_prevents_tilt_chatter_inside_exit_deadband(self):
        self.feed(25, accel=(.6, 0, .8))
        self.assertEqual(self.monitor.snapshot()["candidate"], "tilt")
        events = self.feed(30, accel=(.30, 0, math.sqrt(1 - .30 ** 2)))
        self.assertFalse(any(event.kind == "imu_candidate" for event in events))
        self.assertEqual(self.monitor.snapshot()["candidate"], "tilt")

    def test_short_alternating_motion_does_not_publish_an_unstable_reaction(self):
        self.feed(25)
        events = []
        for index in range(30):
            events += self.feed(1, gyro=(35, 0, 0) if index % 2 else (0, 0, 0))
        self.assertFalse(any(event.kind == "imu_candidate" and event.data["candidate"] == "motion"
                             for event in events))

    def test_accel_and_gyro_saturation_are_faults_at_configured_full_scale(self):
        for settings, values in (({"accel_full_scale_g": 2}, {"accel": (1.99, 0, 0)}),
                                 ({"gyro_full_scale_dps": 250}, {"gyro": (249, 0, 0)})):
            with self.subTest(settings=settings):
                self.monitor = IMUHealthMonitor(clock=self.clock, **settings)
                self.feed(1, **values)
                self.assertEqual((self.monitor.snapshot()["health"], self.monitor.snapshot()["reason_code"]),
                                 ("fault", "sensor_saturation"))
                self.assertIsNone(self.monitor.snapshot()["candidate"])

    def test_frozen_samples_fault_and_cannot_recover_on_one_changed_sample(self):
        self.feed(24, jitter=False)
        self.assertEqual(self.monitor.snapshot()["health"], "fault")
        self.assertEqual(self.monitor.snapshot()["reason_code"], "frozen_sample")
        self.feed(1, jitter=True)
        self.assertEqual(self.monitor.snapshot()["health"], "fault")
        self.feed(12)
        self.assertEqual(self.monitor.snapshot()["health"], "healthy")

    def test_temperature_jitter_does_not_hide_frozen_motion_axes(self):
        for index in range(24):
            sample = IMUSample(index, index * 50, (0, 0, 1), (0, 0, 0), 25 + index * .001)
            self.monitor.ingest(sample, controller_boot="fixture_boot", synthetic=True)
            self.clock.now += .05
        self.assertEqual(self.monitor.snapshot()["reason_code"], "frozen_sample")

    def test_stationary_reference_detects_bias_without_applying_offsets(self):
        events = self.feed(12, gyro=(50, 0, 0), stationary_reference=True)
        self.assertEqual((self.monitor.snapshot()["health"], self.monitor.snapshot()["reason_code"]),
                         ("fault", "stationary_bias"))
        self.assertFalse(any(event.kind == "imu_candidate" for event in events))
        self.assertFalse(hasattr(self.monitor, "offsets"))

    def test_missing_reordered_and_duplicate_sequences_degrade_without_reaction(self):
        self.feed()
        self.sequence += 3
        events = self.feed(1)
        self.assertEqual(self.monitor.snapshot()["health"], "degraded")
        self.assertEqual(self.monitor.snapshot()["missing"], 3)
        previous = IMUSample((self.sequence - 1) & 0xffffffff,
                             round((self.clock.now - .05) * 1000), (0, 0, 1), (0, 0, 0), 25)
        events += self.monitor.ingest(previous, controller_boot="fixture_boot", synthetic=True)
        self.assertEqual(self.monitor.snapshot()["reason_code"], "reordered_sample")
        self.assertEqual(self.monitor.snapshot()["rejected"], 1)
        self.assertFalse(any(event.kind == "imu_candidate" for event in events))

    def test_sequence_and_timestamp_wrap_are_accepted_without_false_reboot(self):
        self.monitor.ingest(IMUSample(0xffffffff, 0xfffffff0, (0, 0, 1), (0, 0, 0), 25),
                            controller_boot="fixture_boot", synthetic=True)
        self.clock.now = .05
        self.monitor.ingest(IMUSample(0, 34, (.001, 0, 1), (.01, 0, 0), 25),
                            controller_boot="fixture_boot", synthetic=True)
        self.assertEqual(self.monitor.snapshot()["rejected"], 0)
        self.assertEqual(self.monitor.snapshot()["controller_restarts"], 0)

    def test_timestamp_reversal_gap_and_clock_drift_are_rejected_or_degraded(self):
        self.feed()
        bad = self.sample()
        bad = IMUSample(bad.sequence, 1, bad.acceleration_g, bad.gyroscope_dps, bad.temperature_c)
        self.monitor.ingest(bad, controller_boot="fixture_boot", synthetic=True)
        self.assertEqual(self.monitor.snapshot()["reason_code"], "reordered_sample")
        self.sequence += 1
        self.clock.now += 2
        self.feed(1)
        self.assertIn(self.monitor.snapshot()["reason_code"], ("timestamp_gap", "stale_recovery"))
        fresh = IMUHealthMonitor(clock=self.clock)
        fresh.ingest(IMUSample(0, 0, (0, 0, 1), (0, 0, 0), 25), controller_boot="boot", synthetic=True)
        self.clock.now += 1.1
        fresh.ingest(IMUSample(1, 1, (.001, 0, 1), (0, 0, 0), 25), controller_boot="boot", synthetic=True)
        self.assertEqual(fresh.snapshot()["reason_code"], "timestamp_mismatch")

    def test_freshness_expiry_clears_candidates_and_requires_new_window_for_recovery(self):
        self.feed()
        self.clock.now += 1.1
        events = self.monitor.poll()
        self.assertEqual(self.monitor.snapshot()["health"], "degraded")
        self.assertEqual(self.monitor.snapshot()["reason_code"], "stale_sample")
        self.assertIsNone(self.monitor.snapshot()["candidate"])
        self.assertFalse(any(event.kind == "imu_candidate" for event in events))
        self.feed(1)
        self.assertEqual(self.monitor.snapshot()["health"], "degraded")
        self.feed(12)
        self.assertEqual(self.monitor.snapshot()["health"], "healthy")

    def test_snapshot_alone_cannot_present_an_expired_sample_as_healthy(self):
        self.feed()
        self.clock.now += 1.1
        self.assertEqual(self.monitor.snapshot()["health"], "degraded")
        self.assertIsNone(self.monitor.snapshot()["candidate"])

    def test_controller_reboot_and_source_change_reset_history_and_do_not_replay_candidates(self):
        self.feed()
        self.sequence = 0
        restart = self.monitor.ingest(IMUSample(0, 0, (0, 0, 1), (0, 0, 0), 25),
                                      controller_boot="new_boot", synthetic=True)
        self.assertEqual(self.monitor.snapshot()["controller_restarts"], 1)
        self.assertEqual(self.monitor.snapshot()["health"], "degraded")
        self.assertFalse(any(event.kind == "imu_candidate" for event in restart))
        self.clock.now += .05
        real = self.monitor.ingest(IMUSample(1, 50, (.001, 0, 1), (0, 0, 0), 25),
                                   controller_boot="new_boot", synthetic=False)
        self.assertFalse(self.monitor.snapshot()["synthetic"])
        self.assertFalse(any(event.kind == "imu_candidate" for event in real))

    def test_untrusted_schema_nonfinite_bad_axes_and_extra_commands_never_leak(self):
        bad = (b'{"speech":"private payload"}', b'{"v":1,"type":"imu","seq":0}',
               IMUSample(0, 0, (float("nan"), 0, 1), (0, 0, 0), 25),
               IMUSample(0, 0, (1, 2), (0, 0, 0), 25),
               IMUSample(True, 0, (0, 0, 1), (0, 0, 0), 25),
               IMUSample(0, 0, (0, 0, 1), (0, 0, 0), 200), {"accel_g": [0, 0, 1]})
        for index, sample in enumerate(bad):
            with self.subTest(index=index), self.assertRaises(FeatureError) as caught:
                self.monitor.ingest(sample, controller_boot="fixture_boot", synthetic=True)
            self.assertNotIn("private", str(caught.exception))
        self.assertEqual(self.monitor.snapshot()["rejected"], len(bad))

    def test_existing_versioned_json_schema_is_reused(self):
        raw = json.dumps({"v": 1, "type": "imu", "seq": 1, "timestamp_ms": 0,
                          "accel_g": [0, 0, 1], "gyro_dps": [0, 0, 0], "temperature_c": 25}).encode()
        self.monitor.ingest(raw, controller_boot="fixture_boot", synthetic=True)
        self.assertEqual(self.monitor.snapshot()["accepted"], 1)

    def test_window_is_bounded_and_reactions_do_not_repeat_every_sample(self):
        events = self.feed(500)
        self.assertLessEqual(self.monitor.snapshot()["retained_samples"], 64)
        self.assertEqual(len([event for event in events if event.kind == "imu_candidate"]), 1)

    def test_motion_preparation_is_independent_of_absent_or_faulty_imu(self):
        self.feed(1, accel=(16, 0, 0), jitter=False)
        self.assertEqual(self.monitor.snapshot()["health"], "fault")
        motion = MotionFeature(clock=self.clock)
        motion.set_context("session", "turn1", 1)
        result = motion.submit("wave", request_id="motion", session_id="session", turn_id="turn1",
                               mode_generation=1, started_at=self.clock.now)
        self.assertEqual(result.status, "accepted")
        self.assertFalse(motion.snapshot()["real_enabled"])
        motion.close()

    def test_close_and_invalid_configuration_or_clock_are_safe(self):
        self.monitor.close()
        with self.assertRaises(FeatureError):
            self.feed(1)
        for kwargs in ({"freshness": 0}, {"min_samples": True}, {"accel_full_scale_g": 3}):
            with self.subTest(kwargs=tuple(kwargs)), self.assertRaises(FeatureError):
                IMUHealthMonitor(**kwargs)
        monitor = IMUHealthMonitor(clock=self.clock)
        self.clock.now = 2
        monitor.snapshot()
        self.clock.now = 1
        with self.assertRaises(FeatureError):
            monitor.snapshot()


if __name__ == "__main__":
    unittest.main()
