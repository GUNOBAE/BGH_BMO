"""Live FeatureHost arm scheduling tested against a fake PWM RP2040 V2 core.

No USB device, GPIO, motor, physical power, credential or provider touched.
"""
import importlib.util
from pathlib import Path
import unittest

from bmo_ai.features.motion.arm_host_v2 import ArmHostV2
from bmo_ai.features.motion.live_feature_v2 import LiveArmMotionFeatureV2
from bmo_ai.features.motion.gesture_rules_v2 import LEGACY_REVIEWED_LIMITS, plan_gesture


SOURCE = Path(__file__).resolve().parents[2] / "firmware/staging/bmo_arm_supervised_v1/safe_arm_core.py"
spec = importlib.util.spec_from_file_location("rp2040_v2_fake_live_fixture", SOURCE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Clock:
    def __init__(self):
        self.t = 0.
    def __call__(self):
        return self.t
    def advance(self, d):
        self.t += d


class Outputs:
    def __init__(self):
        self.starts, self.moves, self.stops = [], [], 0
    def arm(self, homes):
        self.starts.append(dict(homes))
    def set_pulse(self, side, pulse):
        self.moves.append((side, pulse))
    def stop(self):
        self.stops += 1


class RealProtocolFixture:
    simulated = False
    def __init__(self, clock):
        self.out = Outputs()
        self.clock = clock
        self.controller = core.SafeArmController(self.out, "boot_real_fake",
            settings=LEGACY_REVIEWED_LIMITS, physical_enabled=lambda: True, clock=clock)
        self.requests = []
    def __call__(self, message):
        self.requests.append(message)
        return self.controller.handle(message)


class ArmMotionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hw = RealProtocolFixture(self.clock)
        self.host = ArmHostV2(self.hw, profile=LEGACY_REVIEWED_LIMITS,
            clock=self.clock, power_cutoff_confirmed=lambda: True,
            operator_present=lambda: True)
        self.feature = LiveArmMotionFeatureV2(self.host, clock=self.clock, cooldown=0)
        self.feature.set_context("session1", "turn1", 1)

    def pump(self, amount=3.5):
        events = []
        for i in range(int(amount/.02)):
            self.clock.advance(.02)
            self.hw.controller.tick()
            events.extend(self.feature.poll())
        return events

    def test_model_wave_moves_only_right_and_heartbeats_before_stop(self):
        state = self.feature.submit("wave", request_id="wave1",
                session_id="session1", turn_id="turn1", mode_generation=1,
                started_at=0, explicit=False)
        self.assertEqual(state.status, "accepted")
        self.assertEqual(self.hw.requests, [])
        events = self.pump()
        moves = [m for m in self.hw.requests if m.startswith(b"V2 MOVE")]
        self.assertEqual(len(moves), 4)
        self.assertTrue(all(m.split()[4] == b"right" for m in moves))
        self.assertTrue(any(m.startswith(b"V2 PING") for m in self.hw.requests))
        self.assertFalse(self.hw.controller.stop_latched)
        self.assertTrue(any(e.code == "ack_position_unverified" for e in events))
        self.assertFalse(self.feature.snapshot()["position_verified"])

    def test_celebrate_and_shrug_use_both_arms_without_model_pwm_values(self):
        for gesture in ("celebrate", "shrug"):
            with self.subTest(gesture=gesture):
                state = self.feature.submit(gesture, request_id=gesture,
                    session_id="session1", turn_id="turn1", mode_generation=1,
                    started_at=self.clock(), explicit=True)
                self.assertEqual(state.status, "accepted")
                self.pump(4)
                new = [m for m in self.hw.requests if m.startswith(b"V2 MOVE")]
                self.assertTrue(any(m.split()[4] == b"left" for m in new))
                self.assertTrue(any(m.split()[4] == b"right" for m in new))
                self.assertFalse(self.hw.controller.stop_latched)
        with self.assertRaises(ValueError):
            plan_gesture("MOVE right 45000", from_model=True)
        with self.assertRaises(ValueError):
            plan_gesture("both_down", from_model=True)

    def test_cancel_holds_and_next_explicit_request_can_continue(self):
        self.feature.submit("wave", request_id="first",
            session_id="session1", turn_id="turn1", mode_generation=1,
            started_at=0, explicit=True)
        self.pump(.14)
        self.feature.cancel()
        self.assertTrue(any(m.startswith(b"V2 HOLD") for m in self.hw.requests))
        self.assertFalse(self.hw.controller.stop_latched)
        next_action = self.feature.submit("raise_left_arm", request_id="second",
            session_id="session1", turn_id="turn1", mode_generation=1,
            started_at=None, explicit=True)
        self.assertEqual(next_action.status, "accepted")
        self.pump(1)
        self.assertTrue(any(m.startswith(b"V2 MOVE") and m.split()[4] == b"left"
                            for m in self.hw.requests))

    def test_mode_change_cancels_pending_and_invalidates_old_context(self):
        self.feature.submit("wave", request_id="old",
            session_id="session1", turn_id="turn1", mode_generation=1,
            started_at=0, explicit=False)
        self.feature.set_context("session1", "turn2", 2)
        events = self.feature.poll()
        self.assertTrue(any(e.code == "gesture_cancelled" for e in events))
        old = self.feature.submit("wave", request_id="stale",
            session_id="session1", turn_id="turn1", mode_generation=1,
            started_at=0, explicit=False)
        self.assertEqual(old.code, "stale_context")

    def test_invalid_model_intent_never_reaches_motor(self):
        bad = self.feature.submit("both_down", request_id="bad",
            session_id="session1", turn_id="turn1", mode_generation=1,
            started_at=0, explicit=False)
        self.assertEqual(bad.code, "invalid_gesture")
        self.assertEqual(self.hw.requests, [])

    def test_stop_is_emergency_latch_not_gp7_toggle(self):
        self.feature.submit("wave", request_id="wave",
            session_id="session1", turn_id="turn1", mode_generation=1,
            started_at=0, explicit=True)
        self.pump(.1)
        self.feature.stop()
        self.assertTrue(self.hw.controller.stop_latched)
        self.assertFalse(self.hw.controller.manual_toggle())
        self.assertTrue(any(m.startswith(b"V2 STOP") for m in self.hw.requests))


if __name__ == "__main__":
    unittest.main()
