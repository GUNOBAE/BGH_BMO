"""Read-only current-board evidence and non-actuating launcher preparation."""
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmo_ai.arm_prepare import main, prepare_legacy_session
from bmo_ai.features.motion.legacy_host import LegacyArmLink, LegacyMotionFeature
from test_legacy_arm_host import Clock, FakePort, core


ROOT = Path(__file__).resolve().parents[2] / 'firmware/staging/bmo_arm_legacy_bridge_v1'


class ArmPreparationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.mount = self.root / 'board'
        self.mount.mkdir()
        for name in ('code.py', 'bmo_arm_bridge.py'):
            (self.mount / name).write_bytes((ROOT / name).read_bytes())
        (self.mount / 'boot.py').write_text('import usb_cdc\nusb_cdc.enable(console=True, data=True)\n')
        self.enterContext(patch('bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open',
                                side_effect=AssertionError('device access forbidden')))

    def test_disabled_static_source_stays_blocked_without_firmware_write_or_sha_approval(self):
        before = {p.name: p.read_bytes() for p in self.mount.iterdir()}
        result = prepare_legacy_session(firmware_root=ROOT, mount=self.mount)
        self.assertEqual(result['static_code'], 'arm_board_disabled')
        self.assertFalse(result['remote_enabled_static'])
        self.assertFalse(result['reviewed_sha_supplied'])
        self.assertEqual(result['transport_status'], 'NOT_OPENED')
        self.assertIn('arm_operator_condition_missing', result['blocked_reasons'])
        self.assertIsNone(result['launcher_argv'])
        self.assertFalse(result['physical_verified'])
        self.assertFalse(result['executed'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.mount.iterdir()})

    def test_only_supplied_exact_hash_and_operator_declarations_prepare_existing_launcher(self):
        # Pure local fixture; no real board write or changed staged firmware.
        enabled = (ROOT / 'code.py').read_bytes().replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
        (self.mount / 'code.py').write_bytes(enabled)
        digest = hashlib.sha256(enabled).hexdigest()
        self.assertEqual(prepare_legacy_session(firmware_root=ROOT, mount=self.mount)['static_status'], 'NEEDS_REVIEW')
        result = prepare_legacy_session(firmware_root=ROOT, mount=self.mount, reviewed_sha=digest)
        self.assertEqual(result['static_status'], 'STATIC_MATCH')
        self.assertIsNone(result['launcher_argv'])
        result = prepare_legacy_session(firmware_root=ROOT, mount=self.mount, reviewed_sha=digest,
                                       user_present=True, power_off_ready=True)
        self.assertEqual(result['readiness'], 'PREPARED_NOT_ACTIVATED')
        self.assertEqual(result['launcher_argv'], ['./launch-character-life', '--arms-supervised',
            '--arm-legacy-bridge', '--arm-reviewed-code-sha', digest, '--arm-power-off-ready'])
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['executed'])
        self.assertFalse(result['physical_verified'])

    def test_mismatch_missing_and_symlink_cannot_prepare_activation(self):
        enabled = (ROOT / 'code.py').read_bytes().replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
        (self.mount / 'code.py').write_bytes(enabled)
        result = prepare_legacy_session(firmware_root=ROOT, mount=self.mount, reviewed_sha='a' * 64,
                                       user_present=True, power_off_ready=True)
        self.assertEqual(result['static_code'], 'arm_bridge_sha_or_dependency_mismatch')
        self.assertIsNone(result['launcher_argv'])
        (self.mount / 'code.py').unlink()
        (self.mount / 'code.py').symlink_to(ROOT / 'code.py')
        result = prepare_legacy_session(firmware_root=ROOT, mount=self.mount)
        self.assertEqual(result['static_code'], 'arm_firmware_source_unavailable')
        self.assertIsNone(result['board_source_sha256'])

    def test_cli_default_is_dry_run_and_declarations_are_literal(self):
        output = io.StringIO()
        with patch('sys.stdout', output):
            self.assertEqual(main(['--mount', str(self.mount)]), 2)
        self.assertTrue(json.loads(output.getvalue())['dry_run'])
        self.assertFalse(json.loads(output.getvalue())['executed'])
        with self.assertRaises(ValueError):
            prepare_legacy_session(mount=self.mount, user_present=1)


class ArmRuntimeReasonTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.controller = core.LegacyBridge(None, 'reasonsBoot', lambda *_: None,
            lambda *_: None, lambda: 0, enabled=True, clock=self.clock)
        self.port = FakePort(self.controller)
        self.link = LegacyArmLink(self.port, motor_permission=lambda: True, sequence_clock=lambda: 1000)
        self.motion = LegacyMotionFeature(self.link, clock=self.clock, cooldown=0)
        self.motion.set_context('session', 'turn', 1)

    def submit(self):
        return self.motion.submit('raise_left_arm', request_id='current', session_id='session',
            turn_id='turn', mode_generation=1, started_at=self.clock(), explicit=True)

    def test_disabled_and_missing_operator_conditions_have_different_reasons_no_do(self):
        self.controller.enabled = False
        self.submit()
        self.motion.poll()
        result = self.motion.snapshot()
        self.assertIn('arm_board_disabled', result['blocked_reasons'])
        self.assertEqual(result['operation'], 'fault')
        self.assertFalse(any(x.startswith(b'B1 DO ') for x in self.port.sent))
        # Fresh injected session; the previous disabled session is not reset.
        operator_controller = core.LegacyBridge(None, 'operatorBoot', lambda *_: None,
            lambda *_: None, lambda: 0, enabled=True, clock=self.clock)
        operator_controller.enabled = True  # Memory fixture only, no remote board.
        other = LegacyArmLink(FakePort(operator_controller), motor_permission=lambda: False,
            sequence_clock=lambda: 2000)
        motion = LegacyMotionFeature(other, clock=self.clock)
        motion.set_context('session', 'turn', 1)
        motion.submit('wave', request_id='operator', session_id='session', turn_id='turn',
                      mode_generation=1, started_at=self.clock(), explicit=True)
        motion.poll()
        self.assertIn('arm_operator_condition_missing', motion.snapshot()['blocked_reasons'])
        self.assertFalse(any(x.startswith(b'B1 DO ') for x in other.port.sent))

    def test_menu_names_and_stop_semantics_are_actual_b1_allowlist(self):
        state = self.motion.snapshot()
        self.assertEqual(state['supported_gestures'],
            ['both_down', 'both_up', 'celebrate', 'raise_left_arm', 'raise_right_arm', 'shrug', 'wave'])
        self.assertEqual(state['side_reference'], 'bmo_own')
        self.assertEqual(state['operation'], 'idle')
        self.assertEqual(state['cancel_semantics'], 'hold_current_commanded_position')
        self.assertEqual(state['stop_semantics'], 'latch_until_session_restart_not_power_cut')
        self.assertFalse(self.port.sent)


if __name__ == '__main__':
    unittest.main()
