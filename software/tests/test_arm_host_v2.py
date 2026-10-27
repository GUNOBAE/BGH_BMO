"""V2 host/client command contract against the actual staged controller core.

No actual USB/ALSA/GPIO/CIRCUITPY import or device is opened in these tests.
"""
import importlib.util
from pathlib import Path
import unittest

from bmo_ai.features.motion.arm_host_v2 import (
    ArmHostV2, ArmV2Error, BoundedCdcExchange, decode_ack,
    encode_arm, encode_hello, encode_move, encode_ping, encode_stop,
    reviewed_limits,
)


ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "firmware/staging/bmo_arm_supervised_v1/safe_arm_core.py"
spec = importlib.util.spec_from_file_location("rp2040_bmo_arm_v2_core_test", CORE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, delay):
        self.t += delay


class FakeOutputs:
    def __init__(self):
        self.initial = []
        self.writes = []
        self.stops = 0

    def arm(self, homes):
        self.initial.append(dict(homes))

    def set_pulse(self, side, value):
        self.writes.append((side, value))

    def stop(self):
        self.stops += 1


FAKE_PROFILE = {"reviewed": True,
                "left": {"min_us": 1400, "home_us": 1500, "max_us": 1600},
                "right": {"min_us": 1410, "home_us": 1510, "max_us": 1610}}


class ControllerLink:
    def __init__(self, clock, *, simulated=True, profile=None):
        self.simulated = simulated
        self.clock = clock
        self.io = FakeOutputs()
        self.controller = core.SafeArmController(self.io, "boot_fixture",
                     settings=profile or FAKE_PROFILE,
                     physical_enabled=lambda: True, clock=clock)
        self.sent = []
        self.inject = None

    def __call__(self, request):
        self.sent.append(request)
        if self.inject is not None:
            injected, self.inject = self.inject, None
            if isinstance(injected, Exception):
                raise injected
            return injected
        return self.controller.handle(request)


def error_code(code, fn):
    with unittest.TestCase().assertRaises(ArmV2Error) as raised:
        fn()
    assert raised.exception.code == code, (raised.exception.code, code)


class ArmHostV2Tests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.link = ControllerLink(self.clock)
        self.host = ArmHostV2(self.link, profile=FAKE_PROFILE, clock=self.clock)

    def test_constructor_no_io_and_full_hello_handshake(self):
        self.assertEqual(self.link.sent, [])
        self.assertFalse(self.host.snapshot()["armed"])
        ack = self.host.hello()
        self.assertEqual(ack.request_id, "hello1")
        self.assertEqual(ack.boot_id, "boot_fixture")
        self.assertEqual(ack.code, "READY")
        self.assertFalse(ack.position_verified)
        self.assertEqual(self.link.sent, [b"V2 HELLO hello1\n"])

    def test_codec_matches_controller_version_two_exactly(self):
        self.assertEqual(encode_hello("q1"), b"V2 HELLO q1\n")
        self.assertEqual(encode_arm("a1", "boot_fixture"), b"V2 ARM a1 boot_fixture\n")
        self.assertEqual(encode_ping("p1", "boot_fixture", 3), b"V2 PING p1 boot_fixture 3\n")
        self.assertEqual(encode_move("m1", "boot_fixture", "left", 1505),
                         b"V2 MOVE m1 boot_fixture left 1505\n")
        self.assertEqual(encode_stop("stop1"), b"V2 STOP stop1\n")
        for packet in [encode_hello("q1"), encode_arm("a1", "boot_fixture"),
                       encode_ping("p1", "boot_fixture", 3),
                       encode_move("m1", "boot_fixture", "right", 1530),
                       encode_stop("stop1")]:
            self.assertEqual(core.decode_frame(packet)[0], packet.split(b" ")[1].decode())

    def test_simulated_arm_move_both_independent(self):
        self.host.hello()
        ack = self.host.arm("arm1")
        self.assertEqual(ack.code, "ARMED_POSITION_UNVERIFIED")
        self.assertEqual(self.link.io.initial, [{"left": 1500, "right": 1510}])
        self.assertEqual(self.host.move("mleft", "left", 1550).code, "ACCEPTED_POSITION_UNVERIFIED")
        self.assertEqual(self.host.move("mright", "right", 1590).code, "ACCEPTED_POSITION_UNVERIFIED")
        self.clock.advance(.021)
        self.link.controller.tick()
        self.assertEqual(self.link.io.writes, [("left", 1510), ("right", 1520)])
        self.assertFalse(self.host.snapshot()["position_verified"])
        self.assertEqual(self.host.ping("p1").code, "ALIVE")

    def test_default_real_hardware_locks_arm_even_with_hello_and_reviewed_profile(self):
        port = ControllerLink(self.clock, simulated=False)
        session = ArmHostV2(port, profile=FAKE_PROFILE, clock=self.clock)
        session.hello()
        error_code("arm_physical_cutoff_required", lambda: session.arm("arm1"))
        self.assertEqual(len(port.sent), 1)
        self.assertEqual(port.io.initial, [])
        self.assertFalse(session.snapshot()["armed"])

    def test_missing_calibration_rejected_without_arm_io(self):
        port = ControllerLink(self.clock)
        session = ArmHostV2(port, profile=None, clock=self.clock)
        session.hello()
        error_code("arm_profile_unreviewed", lambda: session.arm("arm1"))
        self.assertEqual(len(port.sent), 1)

    def test_host_profile_cannot_override_controller_unverified_calibration(self):
        link = ControllerLink(self.clock)
        link.controller.settings = None
        session = ArmHostV2(link, profile=FAKE_PROFILE, clock=self.clock)
        ack = session.hello()
        self.assertEqual(ack.code, "UNCALIBRATED")
        error_code("arm_controller_unverified", lambda: session.arm("arm1"))

    def test_recommended_first_position_is_endpoint_not_center(self):
        # This is an offline synthetic verification, NOT user approval to
        # reconnect or drive servos. The physical arm position has no feedback.
        up = {'reviewed': True,
              'left': {'min_us': 600, 'home_us': 600, 'max_us': 650},
              'right': {'min_us': 2350, 'home_us': 2400, 'max_us': 2400}}
        self.assertIs(reviewed_limits(up), up)
        link = ControllerLink(self.clock, profile=up)
        session = ArmHostV2(link, profile=up, clock=self.clock)
        session.hello()
        self.assertIn('ARMED_', session.arm('home-up').code)
        self.assertEqual(link.io.initial, [{'left': 600, 'right': 2400}])
        self.assertIn('ACCEPTED_', session.move('step001', 'left', 610).code)
        self.assertIn('ACCEPTED_', session.move('step002', 'right', 2390).code)
        self.clock.advance(.021)
        link.controller.tick()
        self.assertEqual(link.io.writes, [('left', 610), ('right', 2390)])

    def test_move_outside_reviewed_limit_does_not_touch_device(self):
        self.host.hello()
        self.host.arm("arm1")
        sent = len(self.link.sent)
        error_code("arm_out_of_bounds", lambda: self.host.move("m1", "left", 1800))
        error_code("invalid_arm_side", lambda: self.host.move("m2", "both", 1500))
        error_code("arm_out_of_bounds", lambda: self.host.move("m3", "left", 1500.5))
        self.assertEqual(len(self.link.sent), sent)

    def test_clock_expiry_latches_and_prevents_retry(self):
        self.host.hello()
        self.host.arm("arm1")
        self.clock.advance(.67)
        error_code("arm_host_heartbeat_expired", lambda: self.host.move("m1", "left", 1530))
        self.assertTrue(self.host.snapshot()["latched"])
        error_code("arm_not_armed", lambda: self.host.move("m2", "left", 1530))
        self.assertEqual(len(self.link.sent), 2)

    def test_ping_renews_liveness_but_controller_watchdog_remains_authoritative(self):
        self.host.hello()
        self.host.arm("arm1")
        self.clock.advance(.2)
        self.assertEqual(self.host.ping("p1").code, "ALIVE")
        self.clock.advance(.2)
        self.link.controller.tick()
        self.assertTrue(self.link.controller.armed)
        self.assertEqual(self.host.ping("p2").code, "ALIVE")
        self.assertEqual(self.host.ping_counter, 2)

    def test_stop_latches_and_no_second_motion(self):
        self.host.hello()
        self.host.arm("arm1")
        ack = self.host.stop("stop1")
        self.assertEqual(ack.code, "STOP_LATCHED")
        self.assertFalse(self.link.controller.armed)
        error_code("arm_session_latched", lambda: self.host.arm("arm2"))
        self.assertEqual(self.link.io.stops, 1)

    def test_wrong_id_bad_frame_and_boot_replay_reject_without_retry(self):
        for bad in (b"V2 ACK other boot_fixture READY POSITION_UNVERIFIED\n",
                    b"V2 ACK hello1 boot_fixture READY POSITION_VERIFIED\n",
                    b'{"v":1,"seq":1}\n'):
            with self.subTest(bad=bad):
                link = ControllerLink(self.clock)
                link.inject = bad
                session = ArmHostV2(link, profile=FAKE_PROFILE, clock=self.clock)
                with self.assertRaises(ArmV2Error):
                    session.hello()
                self.assertTrue(session.snapshot()["link_unknown"])
                self.assertEqual(len(link.sent), 1)
                error_code("arm_transport_unknown", lambda: session.hello())
                self.assertEqual(len(link.sent), 1)

    def test_boot_change_rejected_after_initial_hello_without_retry(self):
        self.host.hello()
        self.link.inject = b"V2 ACK arm1 another_boot ARMED_POSITION_UNVERIFIED POSITION_UNVERIFIED\n"
        error_code("controller_boot_changed", lambda: self.host.arm("arm1"))
        self.assertTrue(self.host.snapshot()["link_unknown"])
        self.assertEqual(len(self.link.sent), 2)
        error_code("arm_transport_unknown", lambda: self.host.arm("arm2"))
        self.assertEqual(len(self.link.sent), 2)

    def test_transport_failure_after_arm_is_unknown_never_resend(self):
        self.host.hello()
        self.host.arm("arm1")
        self.link.inject = TimeoutError("never expose private errors")
        error_code("arm_transport_unknown", lambda: self.host.move("move1", "left", 1505))
        self.assertEqual(sum(x.startswith(b"V2 MOVE") for x in self.link.sent), 1)
        self.assertTrue(self.host.snapshot()["link_unknown"])
        self.assertFalse(self.host.snapshot()["armed"])
        error_code("arm_not_armed", lambda: self.host.move("move1", "left", 1505))

    def test_host_payload_strict_validation(self):
        for invalid in (None, 1, "x y", "", "a" * 33, "한글", "x\ny"):
            with self.subTest(name=invalid):
                with self.assertRaises(ArmV2Error):
                    encode_hello(invalid)
        for invalid in (True, -1, 2 ** 32, 2.1):
            with self.subTest(counter=invalid):
                with self.assertRaises(ArmV2Error):
                    encode_ping("p", "boot_fixture", invalid)
        for invalid in (True, 500.0, 2501, 400):
            with self.subTest(pulse=invalid):
                with self.assertRaises(ArmV2Error):
                    encode_move("m", "boot_fixture", "left", invalid)
        self.assertEqual(decode_ack(b"V2 ACK req1 boot_fixture READY POSITION_UNVERIFIED\n").code, "READY")
        for bad in (b"", b"V2 ACK r boot READY POSITION_UNVERIFIED",
                    b"V2 ACK r boot READY POSITION_UNVERIFIED\r\n",
                    b"V2 ACK r boot READY POSITION_UNVERIFIED\nV2 ACK r boot READY POSITION_UNVERIFIED\n",
                    b"Z" * 129 + b"\n"):
            with self.subTest(ack=bad):
                with self.assertRaises(ArmV2Error):
                    decode_ack(bad)

    def test_missing_vs_incomplete_ack_explains_previous_field_failure(self):
        error_code("arm_ack_missing", lambda: decode_ack(b""))
        error_code("arm_ack_incomplete",
                   lambda: decode_ack(b"V2 ACK a boot READY POSITION_UNVERIFIED"))
        error_code("arm_ack_ambiguous",
                   lambda: decode_ack(b"V2 ACK a boot READY POSITION_UNVERIFIED\nextra\n"))
        error_code("arm_ack_oversized",
                   lambda: decode_ack(b"V2" + b"x" * 125 + b"\n"))

    def test_not_a_serial_opener(self):
        import ast
        tree = ast.parse((ROOT / "software/bmo_ai/features/motion/arm_host_v2.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(x.name not in ("serial", "usb_cdc", "pwmio") for x in node.names))

    def test_bounded_cdc_requires_preopened_bounded_port_and_exact_write(self):
        class FakePort:
            is_open = True
            timeout = .1
            write_timeout = .1
            def __init__(self):
                self.last = None
                self.partial = False
            def write(self, raw):
                self.last = raw
                return len(raw) - 1 if self.partial else len(raw)
            def read_until(self, mark, size):
                assert mark == b"\n" and size == 128
                return b"V2 ACK hello1 boot_fixture READY POSITION_UNVERIFIED\n"
        port = FakePort()
        exchange = BoundedCdcExchange(port)
        self.assertEqual(exchange(b"V2 HELLO hello1\n"),
                         b"V2 ACK hello1 boot_fixture READY POSITION_UNVERIFIED\n")
        port.partial = True
        error_code("arm_partial_write_unknown", lambda: exchange(b"V2 HELLO hello1\n"))
        port.partial = False
        port.timeout = 1.0
        error_code("dedicated_data_timeout_unverified", lambda: exchange(b"V2 HELLO hello1\n"))

    def test_real_session_with_reported_cutoff_requires_operator_currently_present(self):
        link = ControllerLink(self.clock, simulated=False)
        human = False
        host = ArmHostV2(link, profile=FAKE_PROFILE, clock=self.clock,
                         power_cutoff_confirmed=lambda: True,
                         operator_present=lambda: human)
        host.hello()
        error_code("arm_physical_cutoff_required", lambda: host.arm("arm1"))
        self.assertEqual(len(link.sent), 1)


if __name__ == "__main__":
    unittest.main()
