"""Pure unit tests for staged RP2040 firmware (never runs/imports code.py)."""
import ast
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / 'firmware/staging/bmo_arm_supervised_v1/safe_arm_core.py'
APP = CORE.with_name('code.py')
BOOT = CORE.with_name('boot.py')
spec = importlib.util.spec_from_file_location('safe_arm_core_staging', CORE)
arm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arm)


class FakeClock:
    t = 0.0

    def __call__(self):
        return self.t

    def plus(self, seconds):
        self.t += seconds


class FakeIO:
    def __init__(self):
        self.armed = []
        self.writes = []
        self.stops = 0
        self.fail_arm = False

    def arm(self, homes):
        if self.fail_arm:
            raise RuntimeError()
        self.armed.append(dict(homes))

    def set_pulse(self, side, pulse):
        self.writes.append((side, pulse))

    def stop(self):
        self.stops += 1


# Synthetic range is ONLY for tests: never use it as a real servo calibration.
TEST_LIMITS = {
    'reviewed': True,
    'left': {'min_us': 1450, 'home_us': 1500, 'max_us': 1550},
    'right': {'min_us': 1460, 'home_us': 1510, 'max_us': 1560},
}


def cmd(op, *args):
    return ('V2 ' + op + ' ' + ' '.join(map(str, args)) + '\n').encode()


class SafeArmFirmwareStagingTests(unittest.TestCase):
    def setUp(self):
        self.io = FakeIO()
        self.clock = FakeClock()
        self.hold = False
        self.controller = arm.SafeArmController(
            self.io, 'boot-test', settings=TEST_LIMITS,
            physical_enabled=lambda: self.hold, clock=self.clock)

    def request(self, op, *args):
        return self.controller.handle(cmd(op, *args)).decode('ascii')

    def test_no_pwm_or_io_on_boot(self):
        self.assertEqual(self.io.armed, [])
        self.assertEqual(self.io.writes, [])
        self.assertEqual(self.io.stops, 0)
        self.assertFalse(self.controller.armed)

    def test_deployment_code_is_physically_disabled_and_zero_boot_motion(self):
        tree = ast.parse(APP.read_text(encoding='utf-8'))
        assignments = [node for node in tree.body
                       if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name)
                           and t.id == 'ARM_OUTPUT_BUILD_ENABLED' for t in node.targets)]
        self.assertEqual(len(assignments), 1)
        self.assertIs(assignments[0].value.value, False)
        self.assertIn('REVIEWED_CALIBRATION = None', APP.read_text())
        for file in (CORE, APP, BOOT):
            compile(file.read_text(), str(file), 'exec')
        source = CORE.read_text()
        self.assertNotIn('import pwmio', source)
        self.assertNotIn('import usb_cdc', source)

    def test_requires_review_and_manual_physical_enable(self):
        self.assertIn('PHYSICAL_ENABLE_REQUIRED', self.request('ARM', 'req1', 'boot-test'))
        self.assertEqual(self.io.armed, [])
        self.hold = True
        self.assertIn('ARMED_POSITION_UNVERIFIED', self.request('ARM', 'req1', 'boot-test'))
        self.assertEqual(self.io.armed, [{'left': 1500, 'right': 1510}])
        self.assertEqual(self.io.writes, [])

    def test_boot_mismatch_and_out_of_bounds_deny(self):
        self.hold = True
        self.assertIn('BOOT_MISMATCH', self.request('ARM', 'start', 'oldboot'))
        self.assertIn('ARMED_', self.request('ARM', 'start', 'boot-test'))
        self.assertIn('BOOT_MISMATCH', self.request('MOVE', 'cmd1', 'oldboot', 'left', 1520))
        self.assertIn('OUT_OF_BOUNDS', self.request('MOVE', 'cmd2', 'boot-test', 'left', 1600))
        self.assertEqual(self.io.writes, [])

    def test_independent_slew_and_duplicate_idempotency(self):
        self.hold = True
        self.request('ARM', 'start', 'boot-test')
        msg = cmd('MOVE', 'move001', 'boot-test', 'left', 1540)
        self.assertIn('ACCEPTED_POSITION_UNVERIFIED', self.controller.handle(msg).decode())
        self.assertIn('DUPLICATE', self.controller.handle(msg).decode())
        self.assertIn('REQUEST_CONFLICT', self.request('MOVE', 'move001', 'boot-test', 'left', 1530))
        self.clock.plus(.021)
        self.controller.tick()
        self.assertEqual(self.io.writes, [('left', 1510)])
        self.clock.plus(.021)
        self.controller.tick()
        self.assertEqual(self.io.writes[-1], ('left', 1520))
        self.assertTrue(all(side == 'left' for side, _ in self.io.writes))
        self.assertLessEqual(self.io.writes[-1][1] - 1500, 20)

    def test_ping_counter_and_watchdog_stop_latches(self):
        self.hold = True
        self.request('ARM', 'start', 'boot-test')
        self.clock.plus(.20)
        self.assertIn('ALIVE', self.request('PING', 'ping1', 'boot-test', 1))
        self.clock.plus(.20)
        self.controller.tick()
        self.assertTrue(self.controller.armed)
        self.assertIn('PING_REPLAY', self.request('PING', 'ping2', 'boot-test', 1))
        self.clock.plus(.16)
        self.controller.tick()
        self.assertFalse(self.controller.armed)
        self.assertTrue(self.controller.stop_latched)
        self.assertGreaterEqual(self.io.stops, 1)
        self.assertIn('STOP_LATCHED', self.request('ARM', 'start2', 'boot-test'))

    def test_hardware_gate_drop_software_stop(self):
        self.hold = True
        self.request('ARM', 'start', 'boot-test')
        self.hold = False
        self.clock.plus(.03)
        self.controller.tick()
        self.assertTrue(self.controller.stop_latched)
        self.assertIn('physical_gate_open', self.controller.stop_reason)

    def test_stop_latches_when_id_is_duplicate(self):
        self.hold = True
        self.request('ARM', 'start', 'boot-test')
        self.assertIn('STOP_LATCHED', self.request('STOP', 'stop1'))
        self.assertIn('STOP_LATCHED', self.request('STOP', 'stop1'))
        self.assertFalse(self.controller.armed)
        self.assertIn('STOP_LATCHED', self.request('MOVE', 'new', 'boot-test', 'left', 1510))

    def test_bad_frames_cannot_modify_io(self):
        for raw in (b'', b'V2 ARM foo boot-test', b'V2  ARM a boot-test\n',
                    b'V2 MOVE x boot-test both 1500\n', b'V2 MOVE x boot-test left 1e4\n',
                    b'V2 MOVE x boot-test left 1530\nV2 STOP x\n',
                    b'V2 STOP stop1\r\n', b'Z' * 200 + b'\n',
                    b'V2 PING id boot-test nan\n'):
            with self.subTest(raw=raw):
                self.assertIn('FRAME_REJECTED', self.controller.handle(raw).decode())
        self.assertEqual(self.io.armed, [])
        self.assertEqual(self.io.writes, [])

    def test_config_unreviewed_or_initial_output_failure(self):
        other = arm.SafeArmController(self.io, 'boot-unreviewed',
                                     settings=None, physical_enabled=lambda: True,
                                     clock=self.clock)
        self.assertIn('UNCALIBRATED', other.handle(cmd('ARM', 'x', 'boot-unreviewed')).decode())
        self.assertEqual(self.io.armed, [])
        self.hold = True
        self.io.fail_arm = True
        self.assertIn('OUTPUT_FAILED', self.request('ARM', 'start', 'boot-test'))
        self.assertTrue(self.controller.stop_latched)

    def test_emergency_stop_is_not_a_hard_power_cut(self):
        self.hold = True
        self.request('ARM', 'start', 'boot-test')
        self.controller.emergency_stop('red_software_stop')
        self.assertTrue(self.controller.stop_latched)
        self.assertFalse(self.controller.armed)
        self.assertEqual(self.controller.stop_reason, 'red_software_stop')

    def test_command_ledger_capacity_is_fail_closed(self):
        self.hold = True
        self.request('ARM', 'start', 'boot-test')
        for i in range(arm.MAX_COMMANDS):
            self.assertIn('ACCEPTED_', self.request('MOVE', 'req%03d' % i, 'boot-test', 'left', 1500))
        self.assertIn('LEDGER_FULL_STOP', self.request('MOVE', 'overflow', 'boot-test', 'right', 1510))
        self.assertFalse(self.controller.armed)
        self.assertTrue(self.controller.stop_latched)


if __name__ == '__main__':
    unittest.main()
