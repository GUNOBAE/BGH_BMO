"""Actual v4 sensor state machine, protocol and retained v3 ownership; fake IO."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2] / 'firmware/staging/bmo_arm_legacy_bridge_v4'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


imu = module('imu_v4_sensor_fixture', 'bmo_imu.py')
bridge = module('imu_v4_bridge_fixture', 'bmo_arm_bridge.py')


class Clock:
    def __init__(self):
        self.now = 0
    def __call__(self):
        self.now += 2000
        return self.now
    def advance(self, ns):
        self.now += ns


class Line:
    def __init__(self, *, stuck=False):
        self.released = True
        self.stuck = stuck
    @property
    def value(self):
        return self.released and not self.stuck
    @value.setter
    def value(self, released):
        self.released = bool(released)


class RegisterBus:
    """Only the bus boundary is injected; real init/read/decode is exercised."""
    def __init__(self, raw=None):
        self.calls = []
        self.fail = None
        self.raw = raw or bytes.fromhex('80007fff00001388fffe0064fe70')
        self.releases = 0
    def release(self):
        self.releases += 1
    def recover(self):
        yield
    def transfer(self, address, register, *, count=0, value=None):
        self.calls.append((address, register, count, value))
        yield
        if self.fail:
            raise self.fail('injected_sensor_failure')
        if register == 0x75:
            return b'\x68'
        if register == 0x1b and count == 2:
            return b'\0\0'
        if register == 0x3b:
            return self.raw
        return b''


def progress(sensor, clock, count=100):
    for _ in range(count):
        sensor.poll()
        clock.advance(10000000)


class SensorTests(unittest.TestCase):
    def test_cooperative_bit_engine_valid_register_write_and_burst_read(self):
        class ScriptedSDA(Line):
            def __init__(self, values):
                super().__init__(); self.values=iter(values)
            @Line.value.getter
            def value(self):
                return next(self.values)
        def run(task, clock):
            for _ in range(20000):
                try:next(task)
                except StopIteration as done:return done.value
                clock.advance(1000)
            self.fail('I2C test transfer did not terminate')
        clock=Clock()
        # START free, write-address ACK, register ACK, repeated START free,
        # read-address ACK, then actual byte bits (MSB first). The last byte
        # is NACKed by the master; no temperature/normalization path involved.
        bits=[bool(value & (1 << shift)) for value in (0x80,0x7f) for shift in range(7,-1,-1)]
        sda=ScriptedSDA([True,False,False,True,False,*bits])
        bus=imu.CooperativeI2C(sda,Line(),clock)
        self.assertEqual(run(bus.transfer(0x68,0x3b,count=2),clock),b'\x80\x7f')
        sda=ScriptedSDA([True,False,False,False])
        bus=imu.CooperativeI2C(sda,Line(),clock)
        self.assertEqual(run(bus.transfer(0x68,0x6b,value=1),clock),b'')
        self.assertTrue(sda.released)
        self.assertTrue(bus.scl.value)

    def test_real_raw_six_axes_and_identity_temperature_excluded_no_health_claim(self):
        clock = Clock(); bus = RegisterBus(); sensor = imu.MPU6050(bus, clock)
        progress(sensor, clock, 30)
        self.assertEqual(sensor.status, 'READY')
        self.assertEqual(sensor.axes, (-32768, 32767, 0, -2, 100, -400))
        self.assertEqual(sensor.sensor_id, 'I68A68')
        self.assertGreater(sensor.sequence, 0)
        self.assertGreater(sensor.timestamp_ms, 0)
        self.assertIn((0x68, 0x6b, 0, 1), bus.calls)
        self.assertIn((0x68, 0x1b, 0, 0), bus.calls)
        self.assertIn((0x68, 0x1c, 0, 0), bus.calls)
        frame = sensor.frame('query_1', 'boot_1')
        self.assertEqual(len(frame.split()), 16)
        self.assertIn(b'READY -32768 32767 0 -2 100 -400 A2 G250 I68A68\n', frame)
        self.assertLessEqual(len(frame), 256)
        self.assertNotIn(b'healthy', frame.lower())

    def test_disconnection_nulls_every_axis_preserves_original_error_then_reinitializes(self):
        clock = Clock(); bus = RegisterBus(); sensor = imu.MPU6050(bus, clock)
        progress(sensor, clock, 30)
        old_seq = sensor.sequence
        bus.fail = imu.NoAck
        progress(sensor, clock, 10)
        self.assertEqual(sensor.status, 'ABSENT')
        self.assertIsNone(sensor.axes)
        clock.advance(2000000000)
        self.assertIn(b'ABSENT n n n n n n', sensor.frame('read', 'boot'))
        bus.fail = None
        progress(sensor, clock, 30)
        self.assertEqual(sensor.status, 'READY')
        self.assertGreater(sensor.sequence, old_seq)
        self.assertGreaterEqual(sum(call[1] == 0x75 for call in bus.calls), 2)

    def test_stale_snapshot_is_null_not_fake_three_axis_zero(self):
        clock = Clock(); sensor = imu.MPU6050(RegisterBus(), clock)
        progress(sensor, clock, 30)
        seq = sensor.sequence
        clock.advance(imu.STALE_NS + 1)
        self.assertIn(b'STALE n n n n n n', sensor.frame('query', 'boot'))
        self.assertEqual(sensor.sequence, seq)

    def test_stalled_task_and_even_frozen_clock_have_finite_poll_step_budget(self):
        clock = Clock(); sensor = imu.MPU6050(RegisterBus(), clock)
        def stuck():
            while True:
                yield
        sensor._task = stuck(); sensor._task_started = clock()
        before = clock.now
        sensor.poll()
        self.assertLessEqual(sensor.last_steps, imu.POLL_STEPS)
        self.assertLess(clock.now - before, imu.POLL_BUDGET_NS + 10000)
        clock.advance(imu.TRANSFER_NS + 1)
        sensor.poll()
        self.assertEqual(sensor.status, 'BUS_ERROR')
        sensor.clock_ns = lambda: 0
        sensor._task = stuck(); sensor._task_started = 0
        sensor.poll()
        self.assertEqual(sensor.last_steps, imu.POLL_STEPS)

    def test_stuck_i2c_clock_times_out_without_blocking_other_main_loop_work(self):
        clock = Clock(); sda = Line(); scl = Line(stuck=True)
        sensor = imu.MPU6050(imu.CooperativeI2C(sda, scl, clock), clock)
        hid_edges = arm_heartbeats = 0
        for _ in range(200):
            # Exact product order: HID and arm owner before bounded IMU work.
            hid_edges += 1; arm_heartbeats += 1
            before = clock.now
            sensor.poll()
            self.assertLessEqual(sensor.last_steps, imu.POLL_STEPS)
            self.assertLess(clock.now - before, imu.POLL_BUDGET_NS + 20000)
            clock.advance(10000000)
        self.assertEqual((hid_edges, arm_heartbeats), (200, 200))
        self.assertEqual(sensor.status, 'BUS_ERROR')
        self.assertGreater(sensor.errors, 0)
        self.assertIsNone(sensor.axes)

    def test_missing_sensor_nack_is_absent_and_bus_error_is_separate(self):
        clock = Clock(); sensor = imu.MPU6050(imu.CooperativeI2C(Line(), Line(), clock), clock)
        progress(sensor, clock, 100)
        self.assertEqual(sensor.status, 'ABSENT')
        self.assertIn(b'ABSENT n n n n n n', sensor.frame('test', 'boot'))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.moves = []
        self.bridge = bridge.LegacyBridge(None, 'boot', lambda *a:self.moves.append(a),
            lambda *a:self.moves.append(a), lambda:0)
        self.sensor = imu.MPU6050(RegisterBus(), Clock())
        self.bridge.imu = self.sensor

    def test_read_only_snapshot_works_disabled_stopped_and_motor_fault_without_rearming(self):
        for state in ('disabled', 'stopped', 'fault'):
            with self.subTest(state=state):
                self.setUp()
                if state == 'stopped':
                    self.bridge.stopped = True
                if state == 'fault':
                    self.bridge.fault_remote('servo_fault')
                before = (self.bridge.enabled, self.bridge.stopped, self.bridge.fault,
                          self.bridge.last_ping, self.bridge.highest_motion_sequence)
                frame = self.bridge.handle(b'B1 IMU query\n')
                self.assertTrue(frame.startswith(b'B1 IMU query boot '))
                self.assertIn(b'WARMUP n n n n n n', frame)
                self.assertEqual(before, (self.bridge.enabled, self.bridge.stopped, self.bridge.fault,
                    self.bridge.last_ping, self.bridge.highest_motion_sequence))
                self.assertEqual(self.moves, [])
                self.assertEqual(self.bridge.last_request_order, [])

    def test_invalid_query_does_not_call_sensor_and_missing_sensor_does_not_break_old_protocol(self):
        self.bridge.imu = None
        for raw in (b'B1 IMU bad/id\n', b'B1 IMU query\r\n', b'B1  IMU query\n', b'B1 IMU query\nextra'):
            self.assertIsNone(self.bridge.handle(raw))
        self.assertIn(b'BUS_ERROR n n n n n n', self.bridge.handle(b'B1 IMU valid\n'))
        self.assertIn(b'DISABLED', self.bridge.handle(b'B1 HELLO old\n'))

    def test_sensor_fault_and_queries_never_extend_arm_watchdog(self):
        self.bridge.enabled = True
        self.bridge.last_ping = 10
        self.bridge.last_requests['existing'] = (b'old', b'ack')
        self.bridge.last_request_order.append('existing')
        self.sensor.bus.fail = imu.BusError
        progress(self.sensor, self.sensor.clock_ns, 10)
        for n in range(40):
            self.bridge.handle(('B1 IMU read%d\n' % n).encode())
        self.assertEqual(self.bridge.last_ping, 10)
        self.assertEqual(self.bridge.last_request_order, ['existing'])
        self.assertIsNone(self.bridge.fault)
        self.assertEqual(self.moves, [])

    def test_read_only_queries_cannot_keep_an_unowned_performance_alive(self):
        now=[0.]
        self.bridge.clock=lambda:now[0]
        self.bridge.enabled=True
        self.assertIn(b'PERFORM_ENTER', self.bridge.handle(b'B1 ACT bmo_1 boot greeting\n'))
        for i in range(100):
            now[0]+=.01
            self.bridge.handle(('B1 IMU watch%d\n' % i).encode())
            self.bridge._tick_steps()
        self.assertTrue(self.bridge.stopped)
        self.assertEqual(self.bridge.fault,'watchdog_timeout')
        self.assertIn(b'B1 IMU status boot',self.bridge.handle(b'B1 IMU status\n'))

    def test_original_buttons_pins_pulses_speed_and_v3_protocol_are_preserved(self):
        code = (ROOT/'code.py').read_text()
        old = (ROOT.with_name('bmo_arm_legacy_bridge_v3')/'code.py').read_text()
        # The proven original HID/pins/calibration/manual path is verbatim;
        # only independent post-bridge sensor setup/tick is appended.
        start = old.index('import time')
        end = old.index('# =========================\n# Main Loop')
        original = old[start:end]
        inserted = code[code.index('import time'):code.index('# Sensor initialization')]
        self.assertEqual(inserted, original.rstrip()+'\n\n')
        for constant in ('MAX_SLEW_DPS = 90.0', 'MAX_UPDATE_DEG = 2.0', 'WATCHDOG_S = 0.9'):
            self.assertIn(constant, (ROOT/'bmo_arm_bridge.py').read_text())
        self.assertIn('REMOTE_ENABLED = False', code)
        self.bridge.enabled = True
        self.assertIn(b'PERFORMANCE_READY', self.bridge.handle(b'B1 CAPS capsule boot\n'))
        self.assertIn(b'PERFORM_ENTER', self.bridge.handle(b'B1 ACT bmo_1 boot greeting\n'))
        self.assertEqual(self.bridge.performance['profile'], 'greeting')
        self.assertIn(b'READY', self.bridge.handle(b'B1 HELLO legacy\n'))


# The existing tests execute candidate code.py with fake HID/PWM/CDC, so new
# sensor initialization failure cannot accidentally suppress proven buttons.
import test_arm_recovery as recovery


class V4Buttons(recovery.RecoveryButtonTests):
    def setUp(self):
        self.enterContext(patch.object(recovery, 'ROOT', ROOT))
        super().setUp()


class V4ExistingProtocol(recovery.RecoveryExistingProtocolTests):
    def setUp(self):
        self.enterContext(patch.object(recovery, 'core', bridge))
        super().setUp()


class V4ExplicitPrepare(recovery.RecoveryTests):
    def setUp(self):
        self.enterContext(patch.object(recovery, 'core', bridge))
        self.enterContext(patch.object(recovery, 'ROOT', ROOT))
        super().setUp()

    def test_candidate_preserves_original_code_calibration_and_buttons(self):
        # v4 intentionally adds sensor initialization/ticks; exact approved
        # hardware/manual function bodies remain identical, not the whole file.
        import ast
        old=ROOT.with_name('bmo_arm_legacy_bridge_v3')
        actual=(ROOT/'code.py').read_text(); previous=(old/'code.py').read_text()
        def functions(source):
            return {node.name:ast.get_source_segment(source,node) for node in ast.parse(source).body
                    if isinstance(node,ast.FunctionDef)}
        self.assertEqual(functions(actual),functions(previous))
        self.assertEqual((ROOT/'original_code.py').read_bytes(),(old/'original_code.py').read_bytes())
        self.assertIn('SERVO_MIN_US = 600\nSERVO_MAX_US = 2400',actual)


if __name__ == '__main__':
    unittest.main()
