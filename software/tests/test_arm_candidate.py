"""Exact flag candidate/backup/rollback in synthetic directories, no board IO."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai import arm_candidate as candidate


class ArmCandidateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.mount = self.root / 'synthetic-board'
        self.mount.mkdir()
        self.work = self.root / 'private-candidate'
        for name in ('code.py', 'bmo_arm_bridge.py'):
            (self.mount / name).write_bytes((candidate._ROOT / name).read_bytes())
        (self.mount / 'boot.py').write_bytes(b'import usb_cdc\nusb_cdc.enable(console=True, data=True)\n')
        self.before = {name: (self.mount / name).read_bytes() for name in candidate._FILES}
        self.enabled = self.before['code.py'].replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
        self.digest = hashlib.sha256(self.enabled).hexdigest()
        self.conditions = dict(user_present=True, servo_power_isolated=True,
            clearance_verified=True, physical_cutoff_ready=True, reload_risk_acknowledged=True)
        self.enterContext(patch('bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open',
                                side_effect=AssertionError('serial/device access forbidden')))
        self.enterContext(patch('subprocess.Popen', side_effect=AssertionError('reset/process forbidden')))

    def invoke(self, action='dry_run', **changes):
        settings = dict(action=action, mount=self.mount, work_directory=self.work)
        settings.update(changes)
        return candidate.candidate_transition(**settings)

    def assertBoard(self, expected=None):
        self.assertEqual({name: (self.mount / name).read_bytes() for name in candidate._FILES},
                         self.before if expected is None else expected)

    def test_default_dry_run_reports_exact_candidate_without_creating_or_writing(self):
        result = self.invoke()
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['board_write_performed'])
        self.assertFalse(result['physical_verified'])
        self.assertFalse(result['candidate_reviewed'])
        self.assertEqual(result['transport_status'], 'NOT_OPENED')
        self.assertEqual(result['enabled_code_sha256'], self.digest)
        self.assertEqual(result['off_code_sha256'], candidate._OFF_SHA)
        self.assertTrue(result['reload_may_move_servos'])
        self.assertFalse(self.work.exists())
        self.assertBoard()

    def test_prepare_is_single_flag_only_and_not_approval(self):
        result = self.invoke('prepare')
        self.assertEqual((self.work / 'enabled_code.py').read_bytes(), self.enabled)
        self.assertEqual(self.enabled.replace(b'REMOTE_ENABLED = True', b'REMOTE_ENABLED = False', 1),
                         self.before['code.py'])
        self.assertEqual(self.work.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.work / 'enabled_code.py').stat().st_mode & 0o777, 0o600)
        self.assertFalse(result['candidate_reviewed'] or result['board_write_performed'])
        self.assertFalse(json.loads((self.work / 'review.json').read_text())['candidate_reviewed'])
        self.assertBoard()
        with self.assertRaises(FileExistsError):
            self.invoke('prepare')
        self.assertBoard()

    def test_apply_and_rollback_require_current_conditions_exact_sha_and_retained_backup(self):
        self.invoke('prepare')
        for missing in self.conditions:
            conditions = dict(self.conditions, **{missing: False})
            with self.subTest(missing=missing), self.assertRaisesRegex(candidate.ArmCandidateError,
                    'current_supervised_conditions_required'):
                self.invoke('apply', reviewed_sha=self.digest, **conditions)
            self.assertBoard()
            self.assertFalse((self.work / 'backup-off').exists())
        with self.assertRaisesRegex(candidate.ArmCandidateError, 'exact_reviewed_enabled_sha_required'):
            self.invoke('apply', reviewed_sha='f'*64, **self.conditions)
        result = self.invoke('apply', reviewed_sha=self.digest, **self.conditions)
        self.assertEqual(result['status'], 'STATIC_ENABLED_NOT_MOTION_TESTED')
        self.assertEqual(result['changed_files'], ['code.py'])
        self.assertFalse(result['physical_verified'])
        self.assertEqual(result['transport_status'], 'NOT_OPENED')
        self.assertBoard(dict(self.before, **{'code.py': self.enabled}))
        backup = self.work / 'backup-off'
        self.assertEqual({name: (backup / name).read_bytes() for name in candidate._FILES}, self.before)
        self.assertEqual(json.loads((backup / 'SHA256.json').read_text()),
            {name: hashlib.sha256(data).hexdigest() for name, data in self.before.items()})
        for missing in self.conditions:
            with self.subTest(rollback_missing=missing), self.assertRaises(candidate.ArmCandidateError):
                self.invoke('rollback', reviewed_sha=self.digest, **dict(self.conditions, **{missing: False}))
        rolled = self.invoke('rollback', reviewed_sha=self.digest, **self.conditions)
        self.assertEqual(rolled['status'], 'STATIC_ROLLED_BACK')
        self.assertTrue(rolled['board_write_performed'])
        self.assertBoard()
        self.assertTrue(backup.is_dir())
        with self.assertRaises(candidate.ArmCandidateError):
            self.invoke('apply', reviewed_sha=self.digest, **self.conditions)
        self.assertBoard()

    def test_source_dependency_and_boot_changes_after_prepare_are_not_overwritten(self):
        for name in candidate._FILES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / 'private'
                self.invoke('prepare', work_directory=work)
                changed = self.before[name] + b'\n# changed after review\n'
                (self.mount / name).write_bytes(changed)
                with self.assertRaises(candidate.ArmCandidateError):
                    self.invoke('apply', work_directory=work, reviewed_sha=self.digest, **self.conditions)
                self.assertEqual((self.mount / name).read_bytes(), changed)
                self.assertFalse((work / 'backup-off').exists())
                (self.mount / name).write_bytes(self.before[name])

    def test_candidate_or_backup_tampering_blocks_and_does_not_repair_silently(self):
        self.invoke('prepare')
        (self.work / 'enabled_code.py').write_bytes(self.enabled + b'\n# modified\n')
        with self.assertRaisesRegex(candidate.ArmCandidateError, 'candidate_changed'):
            self.invoke('apply', reviewed_sha=self.digest, **self.conditions)
        self.assertBoard()
        (self.work / 'enabled_code.py').write_bytes(self.enabled)
        self.invoke('apply', reviewed_sha=self.digest, **self.conditions)
        (self.work / 'backup-off' / 'code.py').write_bytes(b'unsafe backup')
        with self.assertRaisesRegex(candidate.ArmCandidateError, 'backup_or_dependencies_changed'):
            self.invoke('rollback', reviewed_sha=self.digest, **self.conditions)
        self.assertBoard(dict(self.before, **{'code.py': self.enabled}))

    def test_symlinks_world_readable_work_and_work_inside_board_are_rejected(self):
        self.work.mkdir(mode=0o755)
        self.work.chmod(0o755)
        with self.assertRaisesRegex(candidate.ArmCandidateError, 'private_directory_required'):
            self.invoke('prepare')
        with self.assertRaises(candidate.ArmCandidateError):
            self.invoke('prepare', work_directory=self.mount / 'work')
        alias = self.root / 'board-alias'
        alias.symlink_to(self.mount, target_is_directory=True)
        with self.assertRaises(candidate.ArmCandidateError):
            self.invoke(mount=alias)
        source = self.mount / 'code.py'
        source.unlink()
        source.symlink_to(candidate._ROOT / 'code.py')
        with self.assertRaises(OSError):
            self.invoke()

    def test_cli_default_and_missing_confirmations_are_safe_codes(self):
        output = io.StringIO()
        with patch('sys.stdout', output):
            self.assertEqual(candidate.main(['--mount', str(self.mount)]), 0)
        self.assertTrue(json.loads(output.getvalue())['dry_run'])
        self.assertBoard()
        output = io.StringIO()
        with patch('sys.stdout', output):
            self.assertEqual(candidate.main(['--apply', '--mount', str(self.mount),
                '--work-directory', str(self.work), '--reviewed-code-sha', self.digest]), 2)
        self.assertEqual(json.loads(output.getvalue())['code'], 'current_supervised_conditions_required')
        self.assertFalse(self.work.exists())
        with self.assertRaises(candidate.ArmCandidateError):
            self.invoke(user_present=1)


if __name__ == '__main__':
    unittest.main()
