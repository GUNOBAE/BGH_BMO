"""Private historical admission, real B1 adapter, and injected byte transport."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bmo_ai.arm_usage import main as usage_main
from bmo_ai.desktop_app import parser, public_plan
from bmo_ai.desktop_install import _character_arguments, DesktopInstallError
from bmo_ai.features.motion.daily_usage import (
    ArmUsageStore, ArmUsageError, DailyArmController, CHECKS, _ROOT, DEVICE)


class Port:
    def __init__(self, stopped=False):
        self.frames = []
        self.opened = self.closed = False
        self.stopped = stopped
    def open(self):
        self.opened = True
        return self
    def write(self, raw):
        self.frames.append(raw)
        return len(raw)
    def read_until(self, delimiter, bound):
        fields = self.frames[-1].decode().split()
        verb, rid = fields[1:3]
        if verb == 'HELLO': code = 'STOPPED' if self.stopped else 'READY'
        elif verb == 'STOP': self.stopped = True; code = 'STOPPED'
        elif verb == 'DO': code = 'STOPPED' if self.stopped else 'ACCEPTED'
        elif verb == 'PING': code = 'ALIVE'
        else: code = 'HELD'
        return ('B1 ACK '+rid+' boot_fixture '+code+' POSITION_UNVERIFIED\n').encode()
    def close(self): self.closed = True


class DailyArmUsageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='daily-arm-')))
        self.mount = self.root/'board'
        self.mount.mkdir(mode=0o700)
        enabled = (_ROOT/'code.py').read_bytes().replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
        (self.mount/'code.py').write_bytes(enabled)
        (self.mount/'bmo_arm_bridge.py').write_bytes((_ROOT/'bmo_arm_bridge.py').read_bytes())
        (self.mount/'boot.py').write_bytes(b'import usb_cdc\nusb_cdc.enable(console=True, data=True)\n')
        self.sha = hashlib.sha256(enabled).hexdigest()
        self.identity = dict(device=str(DEVICE), interface='02', label='CircuitPython CDC2 fixture',
            product='RP2040 fixture', vendor_id='239a', product_id='80f4', serial='synthetic_board_01')
        self.store = ArmUsageStore(self.root/'private')
        self.now = 100.
        self.physical = self.enterContext(patch('bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open',
                                               side_effect=AssertionError('real USB forbidden')))
        self.addCleanup(self.physical.assert_not_called)

    def record(self, **kwargs):
        options = dict(reviewed_sha=self.sha, test_session_id='synthetic_first_test',
            checks={name:True for name in CHECKS}, mount=self.mount, identity_reader=lambda:dict(self.identity))
        options.update(kwargs)
        return self.store.commission(**options)

    def controller(self, port=None, **kwargs):
        port = port or Port()
        options = dict(store=self.store, runtime_ready=lambda:True, clock=lambda:self.now,
            port_factory=lambda:port, identity_reader=lambda:dict(self.identity), mount=self.mount)
        options.update(kwargs)
        controller = DailyArmController(**options)
        return controller, port

    def submit(self, controller, rid='test_wave', explicit=True):
        motion = controller.motion
        motion.set_context('test_session', 'turn_1', 0)
        result = motion.submit('wave', request_id=rid, session_id='test_session', turn_id='turn_1',
            mode_generation=0, started_at=self.now, explicit=explicit)
        events = motion.poll()
        return result, events

    def test_missing_receipt_preferences_are_saved_but_never_admit_adapter(self):
        controller, port = self.controller()
        self.assertFalse(self.store.directory.exists())
        self.assertIsNone(controller.motion)
        controller.configure(dict(enabled=True, conversation_gestures=True))
        after, _ = self.controller()
        self.assertTrue(after.enabled)
        self.assertTrue(after.conversation_gestures)
        self.assertIsNone(after.motion)
        self.assertEqual(after.snapshot()['code'], 'arm_first_test_required')
        self.assertFalse(port.opened)
        self.assertEqual((self.store.directory/'daily.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)

    def test_record_is_historical_bound_to_sources_and_device_not_live_conditions(self):
        record = self.record()
        self.assertEqual(record['reviewed_code_sha256'], self.sha)
        self.assertEqual(record['device_identity'], self.identity)
        raw = (self.store.directory/'daily.json').read_text()
        for forbidden in ('user_present', 'supervisor_present', 'power_off_ready', 'power_cutoff_ready'):
            self.assertNotIn(forbidden, raw)
        self.assertFalse(self.store.load()['enabled'])
        controller, port = self.controller()
        self.assertTrue(controller.snapshot()['commissioned'])
        self.assertFalse(controller.snapshot()['physical_verified'])
        self.assertFalse(port.opened)
        controller.motion.close()
        self.assertFalse(port.opened)

    def test_historical_check_or_wrong_source_cannot_create_receipt(self):
        incomplete = {name:True for name in CHECKS}; incomplete['stop_verified'] = False
        with self.assertRaises(ArmUsageError): self.record(checks=incomplete)
        self.assertFalse(self.store.directory.exists())
        with self.assertRaises(Exception): self.record(reviewed_sha='0'*64)
        self.assertFalse(self.store.directory.exists())

    def test_current_identity_and_dependencies_rechecked_before_first_hello(self):
        self.record(); self.store.preferences(dict(enabled=True, conversation_gestures=True))
        for change in ('device', 'helper'):
            with self.subTest(change=change):
                if change == 'helper': (self.mount/'bmo_arm_bridge.py').write_bytes(b'changed')
                reader = lambda:dict(self.identity, serial='other_board') if change == 'device' else dict(self.identity)
                controller, port = self.controller(identity_reader=reader)
                queued, events = self.submit(controller)
                self.assertEqual(queued.status, 'accepted')
                self.assertFalse(port.opened)
                self.assertEqual(port.frames, [])
                self.assertEqual(events[-1].data['status'], 'unknown')
                self.assertFalse(controller.snapshot()['current_connection_verified'])
                self.assertNotEqual(controller.snapshot()['code'], 'arm_connection_not_requested')
                controller.motion.close()

    def test_enabled_current_request_uses_existing_b1_ack_and_never_claims_motion(self):
        self.record(); self.store.preferences(dict(enabled=True, conversation_gestures=False))
        controller, port = self.controller()
        queued, events = self.submit(controller)
        self.assertEqual(queued.status, 'accepted')
        self.assertTrue(port.opened)
        self.assertEqual([v.split()[1] for v in port.frames], [b'HELLO', b'DO', b'PING'])
        self.assertTrue(events[-1].data['executed'])
        self.assertFalse(events[-1].data['position_verified'])
        self.assertTrue(controller.snapshot()['current_connection_verified'])
        self.assertFalse(controller.snapshot()['physical_verified'])
        controller.motion.close()
        self.assertTrue(port.closed)

    def test_preference_disable_and_reenable_preserve_stop_latch(self):
        self.record(); self.store.preferences(dict(enabled=True, conversation_gestures=True))
        controller, port = self.controller()
        self.submit(controller)
        controller.configure(dict(enabled=False, conversation_gestures=False))
        self.assertTrue(port.stopped)
        controller.configure(dict(enabled=True, conversation_gestures=True))
        result, _ = self.submit(controller, rid='second_request')
        self.assertEqual(result.status, 'rejected')
        self.assertEqual(sum(b' DO ' in v for v in port.frames), 1)
        self.assertEqual(controller.snapshot()['code'], 'arm_stopped')
        controller.motion.close()

    def test_disable_stops_even_when_preference_write_fails(self):
        self.record(); self.store.preferences(dict(enabled=True, conversation_gestures=True))
        controller, port = self.controller()
        self.submit(controller)
        with patch.object(self.store, 'preferences', side_effect=OSError('fixture full disk')):
            with self.assertRaises(ArmUsageError):
                controller.configure(dict(enabled=False, conversation_gestures=False))
        self.assertFalse(controller.enabled)
        self.assertTrue(controller.motion.stopped)
        self.assertTrue(port.stopped)
        controller.motion.close()

    def test_commissioned_status_does_not_claim_current_supervision(self):
        from bmo_ai.features.motion.runtime_status import conversation_arm_status
        self.record(); self.store.preferences(dict(enabled=True, conversation_gestures=True))
        controller, port = self.controller()
        view = conversation_arm_status(simulate=False, supervised_requested=False,
            supervised_effective=False, commissioned_requested=True, commissioned_effective=True,
            usage_enabled=True, emotion_arms_requested=True, emotion_arms_effective=True,
            motion=controller.motion.snapshot())
        self.assertFalse(view['launcher']['supervised_effective'])
        self.assertTrue(view['launcher']['commissioned_effective'])
        self.assertFalse(view['launcher']['current_supervision_stored'])
        self.assertNotIn('arm_runtime_off', view['conversation']['blocked_reasons'])
        self.assertIsNone(view['board']['remote_enabled_at_last_ack'])
        controller.motion.close()

    def test_prior_board_stop_is_not_cleared_by_new_app_or_pref_toggle(self):
        self.record(); self.store.preferences(dict(enabled=True, conversation_gestures=True))
        controller, port = self.controller(Port(stopped=True))
        _, events = self.submit(controller)
        self.assertEqual([v.split()[1] for v in port.frames], [b'HELLO'])
        self.assertTrue(controller.motion.link.stopped)
        self.assertEqual(events[-1].data['status'], 'unknown')
        controller.configure(dict(enabled=False, conversation_gestures=True))
        controller.configure(dict(enabled=True, conversation_gestures=True))
        self.submit(controller, rid='try_again')
        self.assertEqual([v.split()[1] for v in port.frames], [b'HELLO'])
        controller.motion.close()

    def test_current_app_permission_still_required_after_recorded_test(self):
        self.record(); self.store.preferences(dict(enabled=True, conversation_gestures=True))
        controller, port = self.controller(runtime_ready=lambda:False)
        self.submit(controller)
        self.assertEqual([v.split()[1] for v in port.frames], [b'HELLO'])
        self.assertEqual(controller.motion.snapshot()['last_error_code'], 'daily_arm_permission_required')
        self.assertIn('arm_runtime_not_ready', controller.motion.snapshot()['blocked_reasons'])
        controller.motion.close()

    def test_invalid_or_public_preferences_do_not_overwrite_or_admit(self):
        self.store.directory.mkdir(mode=0o700)
        path = self.store.directory/'daily.json'; path.write_text('private unrelated file')
        path.chmod(0o600)
        controller, _ = self.controller()
        self.assertIsNone(controller.motion)
        with self.assertRaises(ArmUsageError): controller.configure(dict(enabled=True, conversation_gestures=False))
        self.assertEqual(path.read_text(), 'private unrelated file')

    def test_symlink_store_and_unknown_attestation_keys_rejected(self):
        self.record()
        data = self.store.load()
        data['commissioning']['user_present'] = True
        path = self.store.directory/'daily.json'
        path.write_text(json.dumps(data))
        controller, _ = self.controller()
        self.assertIsNone(controller.motion)
        target = self.root/'other'; target.mkdir(mode=0o700)
        link = self.root/'linked'; link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ArmUsageError): ArmUsageStore(link).load()

    def test_invalid_default_config_root_blocks_only_optional_arms(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME':'relative_config'}):
            controller = DailyArmController()
        self.assertIsNone(controller.motion)
        self.assertEqual(controller.snapshot()['code'], 'arm_settings_unavailable')
        self.assertFalse(controller.enabled)

    def test_status_cli_is_read_only_and_does_not_open_a_port(self):
        self.record()
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(usage_main(['--directory', str(self.store.directory)]), 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report['status'], 'READ_ONLY')
        self.assertFalse(report['transport_opened'] or report['motion_requested'] or report['firmware_changed'])

    def test_installer_preserves_private_settings_paths_not_session_confirmations(self):
        options = dict(character_life=True, arm_usage_directory=self.root/'arms', preferences_directory=self.root/'prefs')
        argv = _character_arguments(options, simulate=False, ptt_microphone=False)
        self.assertIn('--arm-usage-directory', argv)
        self.assertIn('--preferences-directory', argv)
        self.assertNotIn('--arms-supervised', argv)
        self.assertNotIn('--arm-power-off-ready', argv)
        self.assertIn(str(self.root/'arms'), argv)
        with self.assertRaises(DesktopInstallError):
            _character_arguments(options, simulate=True, ptt_microphone=False)
        self.assertFalse((self.root/'arms').exists())
        plan = public_plan(parser().parse_args(['--live', '--character-life', '--arm-usage-directory', str(self.root/'arms')]))
        self.assertFalse(plan['daily_arm_settings']['loaded'])
        self.assertFalse(plan['daily_arm_settings']['stores_current_supervision'])


if __name__ == '__main__': unittest.main()
