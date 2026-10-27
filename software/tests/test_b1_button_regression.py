"""Run the real staged B1 code with injected pins/PWM/CDC/HID, never devices.

The bounded interpreter loop is a regression of the deployable staged source,
not proof that a physical servo or electrical cutoff works.
"""
import ast
from contextlib import redirect_stdout
import hashlib
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


FIRMWARE = Path(__file__).resolve().parents[2] / "firmware/staging/bmo_arm_legacy_bridge_v1"
ORIGINAL_SHA = "fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e"


class _LoopComplete(BaseException):
    pass


class _FirmwareRun:
    """Tiny CircuitPython module doubles; writes are in-memory records only."""
    def __init__(self, *, schedule=None, remote=False, pwm_fault=None, cdc_fault=False,
                 remote_gesture="wave"):
        self.iteration = 0
        self.now = 0.
        self.schedule = schedule or {}
        self.remote = remote
        self.pwm_fault = pwm_fault
        self.fault_seen = False
        self.hid, self.pwm_attempts, self.pwm_writes = [], [], []
        self.pin_writes, self.servos, self.inputs = {}, {}, []
        self.namespace = {"__name__": "bmo_staged_firmware_regression"}
        self.output = io.StringIO()
        owner = self

        class Pin:
            def __init__(self, name):
                self.name = name

        class DigitalInput:
            def __init__(self, pin):
                self.pin = pin
                self.direction = self.pull = None
                owner.inputs.append(self)

            @property
            def value(self):
                return self.pin.name not in owner.schedule.get(owner.iteration, ())

        class PWM:
            def __init__(self, pin, *, frequency):
                if owner.pwm_fault == "init_" + pin.name:
                    owner.fault_seen = True
                    raise RuntimeError("synthetic_pwm_initialization_failure")
                self.pin, self.frequency = pin, frequency
                self._duty_cycle = 0
                owner.servos[pin.name] = self

            @property
            def duty_cycle(self):
                return self._duty_cycle

            @duty_cycle.setter
            def duty_cycle(self, value):
                name = self.pin.name
                count = owner.pin_writes.get(name, 0) + 1
                owner.pin_writes[name] = count
                owner.pwm_attempts.append((owner.iteration, name, value))
                fail = (
                    owner.pwm_fault == "boot" and name == "GP10" and count == 1
                    or owner.pwm_fault == "manual" and name == "GP11" and count == 2
                    or owner.pwm_fault == "remote" and name == "GP10" and count == 2
                )
                if fail and not owner.fault_seen:
                    owner.fault_seen = True
                    raise RuntimeError("synthetic_pwm_failure")
                self._duty_cycle = value
                owner.pwm_writes.append((owner.iteration, name, value))

        class Keyboard:
            def __init__(self, devices):
                if devices != ("synthetic_hid",):
                    raise AssertionError("only injected HID devices allowed")

            def press(self, key):
                owner.hid.append((owner.iteration, "press", key))

            def release(self, key):
                owner.hid.append((owner.iteration, "release", key))

        class Port:
            timeout = write_timeout = None

            def __init__(self):
                self.pending = ("B1 DO bmo_00000001 b111111111111 " + remote_gesture + "\n").encode() if remote else b""
                self.writes = []

            def __setattr__(self, name, value):
                if cdc_fault == "init" and name in ("timeout", "write_timeout"):
                    raise RuntimeError("synthetic_cdc_initialization_failure")
                object.__setattr__(self, name, value)

            @property
            def in_waiting(self):
                if cdc_fault is True:
                    raise RuntimeError("synthetic_cdc_failure")
                return len(self.pending)

            def read(self, size):
                result, self.pending = self.pending[:size], self.pending[size:]
                return result

            def write(self, frame):
                self.writes.append(frame)
                return len(frame)

        self.port = Port()
        self.modules = {}

        def module(name, **attributes):
            value = ModuleType(name)
            value.__dict__.update(attributes)
            self.modules[name] = value
            return value

        module("time", monotonic=lambda: self.now, sleep=self._sleep)
        module("os", urandom=lambda count: b"\x11" * count)
        module("board", **{f"GP{i}": Pin(f"GP{i}") for i in (2, 3, 4, 5, 6, 7, 8, 10, 11)})
        module("digitalio", DigitalInOut=DigitalInput,
               Direction=SimpleNamespace(INPUT="input"), Pull=SimpleNamespace(UP="up"))
        module("pwmio", PWMOut=PWM)
        module("usb_hid", devices=("synthetic_hid",))
        module("usb_cdc", data=self.port)
        package = module("adafruit_hid")
        package.__path__ = []
        module("adafruit_hid.keyboard", Keyboard=Keyboard)
        module("adafruit_hid.keycode", Keycode=SimpleNamespace(
            UP_ARROW="UP", LEFT_ARROW="LEFT", RIGHT_ARROW="RIGHT",
            DOWN_ARROW="DOWN", Z="Z", ENTER="ENTER"))
        module("bmo_arm_bridge")

    def _sleep(self, seconds):
        if seconds != .01:
            raise AssertionError("the original nonblocking button poll period changed")
        self.now += seconds
        self.iteration += 1
        if self.iteration == 75:
            raise _LoopComplete()

    def run(self):
        source = (FIRMWARE / "code.py").read_text()
        if self.remote:
            needle = "\nREMOTE_ENABLED = False\n"
            if source.count(needle) != 1:
                raise AssertionError("exact staged enable setting must remain unambiguous")
            source = source.replace(needle, "\nREMOTE_ENABLED = True\n", 1)
        with patch.dict(sys.modules, self.modules), redirect_stdout(self.output):
            # The source imports only the injected CircuitPython doubles above.
            helper = self.modules["bmo_arm_bridge"]
            exec(compile((FIRMWARE / "bmo_arm_bridge.py").read_text(),
                         str(FIRMWARE / "bmo_arm_bridge.py"), "exec"), helper.__dict__)
            try:
                exec(compile(source, str(FIRMWARE / "code.py"), "exec"), self.namespace)
            except _LoopComplete:
                return self
        raise AssertionError("firmware loop stopped before the injected iteration bound")


class B1ButtonRegressionTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen"):
            self.enterContext(patch(target, side_effect=AssertionError("real I/O forbidden")))

    @staticmethod
    def hid_schedule():
        # All six HID buttons press/release after any remote fault and again later.
        all_hid = ("GP2", "GP3", "GP4", "GP5", "GP6", "GP8")
        return {25: all_hid, 35: all_hid, 40: ("GP7",), 45: ("GP7",)}

    def assert_all_hid_survived(self, run):
        expected = {"UP", "LEFT", "RIGHT", "DOWN", "Z", "ENTER"}
        for iteration in (25, 35):
            self.assertEqual({key for at, edge, key in run.hid
                              if at == iteration and edge == "press"}, expected)
            self.assertEqual({key for at, edge, key in run.hid
                              if at == iteration + 1 and edge == "release"}, expected)

    def test_original_source_hash_and_mapping_functions_remain_exact(self):
        original = (FIRMWARE / "original_code.py").read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), ORIGINAL_SHA)
        original_text = original.decode()
        staged = (FIRMWARE / "code.py").read_text()
        for name in ("servo_angle", "set_arms"):
            original_node = next(node for node in ast.parse(original_text).body
                                 if isinstance(node, ast.FunctionDef) and node.name == name)
            staged_node = next(node for node in ast.parse(staged).body
                               if isinstance(node, ast.FunctionDef) and node.name == name)
            self.assertEqual(ast.get_source_segment(staged, staged_node),
                             ast.get_source_segment(original_text, original_node))

    def test_default_staged_remote_is_off_and_all_hid_still_work(self):
        run = _FirmwareRun(schedule=self.hid_schedule()).run()
        self.assertFalse(run.namespace["REMOTE_ENABLED"])
        self.assertFalse(run.namespace["bridge"].enabled)
        self.assert_all_hid_survived(run)
        self.assertEqual([entry.pin.name for entry in run.inputs],
                         ["GP2", "GP3", "GP4", "GP5", "GP6", "GP7", "GP8"])
        self.assertTrue(all(entry.direction == "input" and entry.pull == "up" for entry in run.inputs))

    def test_normal_gp7_retains_instant_endpoint_toggle_and_original_pulses(self):
        run = _FirmwareRun(schedule={5: ("GP7",), 10: ("GP7",)}).run()
        self.assertEqual((run.namespace["SERVO_MIN_US"], run.namespace["SERVO_MAX_US"]), (600, 2400))
        self.assertEqual({name: servo.frequency for name, servo in run.servos.items()},
                         {"GP10": 50, "GP11": 50})
        low, high = int(600 / 20000 * 65535), int(2400 / 20000 * 65535)
        self.assertEqual(run.pwm_writes, [
            (0, "GP11", high), (0, "GP10", low),
            (5, "GP11", low), (5, "GP10", high),
            (10, "GP11", high), (10, "GP10", low),
        ])
        self.assertEqual(run.namespace["arm_angle"], 0)
        self.assertEqual(sum(line.startswith("ARM =") for line in run.output.getvalue().splitlines()), 3)

    def test_remote_pwm_fault_latches_only_automation_and_preserves_all_hid(self):
        run = _FirmwareRun(schedule=self.hid_schedule(), remote=True, pwm_fault="remote").run()
        self.assertTrue(run.fault_seen)
        bridge = run.namespace["bridge"]
        self.assertTrue(bridge.stopped)
        self.assertEqual(bridge.fault, "servo_fault")
        self.assertFalse(bridge.steps)
        self.assert_all_hid_survived(run)
        # Manual GP7 retries retain the original PWM path after the remote fault.
        self.assertTrue(any(at == 40 and pin == "GP10" for at, pin, _ in run.pwm_writes))

    def test_manual_gp7_pwm_fault_cannot_disable_gp8_or_other_hid(self):
        schedule = self.hid_schedule() | {5: ("GP7",), 10: ("GP7",)}
        run = _FirmwareRun(schedule=schedule, pwm_fault="manual").run()
        self.assertTrue(run.fault_seen)
        bridge = run.namespace["bridge"]
        self.assertTrue(bridge.stopped)
        self.assertEqual(bridge.fault, "servo_fault")
        self.assertFalse(bridge.steps)
        self.assert_all_hid_survived(run)
        self.assertTrue(any(at == 10 and pin == "GP10" for at, pin, _ in run.pwm_writes))

    def test_boot_pwm_fault_keeps_buttons_alive_without_automatic_retry(self):
        run = _FirmwareRun(schedule={25: ("GP8",), 35: ("GP2",)}, pwm_fault="boot").run()
        self.assertTrue(run.fault_seen)
        self.assertTrue(run.namespace["bridge"].stopped)
        self.assertEqual(run.namespace["bridge"].fault, "servo_fault")
        self.assertIn((25, "press", "ENTER"), run.hid)
        self.assertIn((26, "release", "ENTER"), run.hid)
        self.assertIn((35, "press", "UP"), run.hid)
        self.assertEqual(len(run.pwm_attempts), 2)

    def test_cdc_exception_does_not_terminate_manual_or_hid_loop(self):
        run = _FirmwareRun(schedule=self.hid_schedule(), remote=True, cdc_fault=True).run()
        bridge = run.namespace["bridge"]
        self.assertTrue(bridge.stopped)
        self.assertEqual(bridge.fault, "cdc_fault")
        self.assertFalse(bridge.steps)
        self.assert_all_hid_survived(run)

    def test_each_pwm_constructor_failure_still_services_every_hid_button(self):
        for pin in ("GP10", "GP11"):
            with self.subTest(pin=pin):
                run = _FirmwareRun(schedule=self.hid_schedule(), pwm_fault="init_" + pin).run()
                self.assertTrue(run.fault_seen)
                self.assertTrue(run.namespace["bridge"].stopped)
                self.assertEqual(run.namespace["bridge"].fault, "servo_fault")
                self.assert_all_hid_survived(run)

    def test_cdc_setup_failure_keeps_manual_and_hid_loop_running(self):
        run = _FirmwareRun(schedule=self.hid_schedule(), remote=True, cdc_fault="init").run()
        bridge = run.namespace["bridge"]
        self.assertFalse(bridge.enabled)
        self.assertTrue(bridge.stopped)
        self.assertEqual(bridge.fault, "cdc_fault")
        self.assert_all_hid_survived(run)

    def test_automatic_both_arm_slew_does_not_flood_original_console_prints(self):
        run = _FirmwareRun(remote=True, remote_gesture="celebrate").run()
        self.assertGreater(len(run.pwm_writes), 10)
        self.assertEqual(sum(line.startswith("ARM =") for line in run.output.getvalue().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
