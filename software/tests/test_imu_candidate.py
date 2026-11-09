"""Reviewed B1+IMU migration in synthetic files; no real mount, port or reset."""
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmo_ai import imu_candidate as candidate
from bmo_ai.arm_candidate import ArmCandidateError


class IMUCandidateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.mount = self.root/'synthetic-board'
        self.mount.mkdir(mode=0o700)
        self.work = self.root/'review'
        self.flags = dict(user_present=True, servo_power_isolated=True,
            clearance_verified=True, physical_cutoff_ready=True, reload_risk_acknowledged=True)
        self.enterContext(patch('bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open',
                               side_effect=AssertionError('physical serial access forbidden')))
        self.enterContext(patch('subprocess.Popen',side_effect=AssertionError('reset/process forbidden')))
        self.install_old()

    def install_old(self, version=3, enabled=True):
        old = candidate.base._ROOT.with_name('bmo_arm_legacy_bridge_v'+str(version))
        for path in self.mount.iterdir():path.unlink()
        for name in candidate.OLD_FILES:
            raw = (old/name).read_bytes()
            if name == 'code.py' and enabled:
                raw = raw.replace(b'REMOTE_ENABLED = False',b'REMOTE_ENABLED = True',1)
            (self.mount/name).write_bytes(raw)
        self.before = self.board()

    def board(self):
        return {path.name:path.read_bytes() for path in self.mount.iterdir()}

    def invoke(self, action='dry_run', **options):
        settings = dict(action=action,mount=self.mount,work_directory=self.work)
        settings.update(options)
        return candidate.candidate_transition(**settings)

    def prepare(self, **options):
        report = self.invoke('prepare',**options)
        hashes = report['candidate_files_sha256']
        return dict(options,reviewed_code_sha=hashes['code.py'],
            reviewed_helper_sha=hashes['bmo_arm_bridge.py'],reviewed_sensor_sha=hashes['bmo_imu.py'])

    def test_default_dry_run_has_no_writes_or_health_or_motor_approval(self):
        report = self.invoke()
        self.assertEqual(self.board(),self.before)
        self.assertFalse(self.work.exists())
        self.assertTrue(report['dry_run'])
        self.assertFalse(report['board_write_performed'])
        self.assertFalse(report['candidate_remote_enabled'])
        self.assertTrue(report['previous_remote_enabled'])
        self.assertFalse(report['motors_newly_enabled'])
        self.assertFalse(report['sensor_health_verified'])
        self.assertEqual(report['transport_status'],'NOT_OPENED')
        self.assertEqual(set(report['candidate_files_sha256']),set(candidate.FILES))

    def test_private_prepare_pins_all_three_sources_and_does_not_touch_board(self):
        settings = self.prepare()
        self.assertEqual(self.board(),self.before)
        self.assertEqual(self.work.stat().st_mode & 0o777,0o700)
        for name in (*candidate.FILES,'review.json'):
            self.assertEqual((self.work/name).stat().st_mode & 0o777,0o600)
        self.assertIn(b'REMOTE_ENABLED = False',(self.work/'code.py').read_bytes())
        audit = json.loads((self.work/'review.json').read_bytes())
        self.assertFalse(audit['candidate_reviewed'])
        self.assertEqual(audit['candidate_files_sha256']['bmo_imu.py'],settings['reviewed_sensor_sha'])
        with self.assertRaises(FileExistsError):self.prepare()

    def test_known_v1_v2_v3_off_and_on_round_trip_back_to_exact_original(self):
        for version in (1,2,3):
            for keep in (False,True):
                with self.subTest(version=version,keep=keep):
                    self.install_old(version,enabled=keep)
                    self.work = self.root/('review-'+str(version)+'-'+str(keep))
                    settings = self.prepare(keep_reviewed_motors=keep)
                    result = self.invoke('apply',**settings,**self.flags)
                    self.assertEqual(result['status'],'STATIC_UPDATED_NOT_SENSOR_TESTED')
                    self.assertFalse(result['physical_verified'])
                    self.assertEqual((self.mount/'bmo_imu.py').read_bytes(),(candidate.ROOT/'bmo_imu.py').read_bytes())
                    self.assertEqual(b'REMOTE_ENABLED = True' in (self.mount/'code.py').read_bytes(),keep)
                    self.assertEqual({p.name:p.read_bytes() for p in (self.work/'backup-previous').iterdir()
                        if p.name!='SHA256.json'},self.before)
                    self.invoke('rollback',**settings,**self.flags)
                    self.assertEqual(self.board(),self.before)

    def test_motor_preservation_is_not_permission_to_enable_an_off_board(self):
        self.install_old(enabled=False)
        with self.assertRaisesRegex(ArmCandidateError,'cannot_grant_motor'):
            self.invoke(keep_reviewed_motors=True)
        self.assertEqual(self.board(),self.before)
        self.assertFalse(self.work.exists())

    def test_existing_on_board_default_candidate_is_sensor_only_and_rollback_restores_on(self):
        settings = self.prepare()
        self.invoke('apply',**settings,**self.flags)
        self.assertIn(b'REMOTE_ENABLED = False',(self.mount/'code.py').read_bytes())
        self.invoke('rollback',**settings,**self.flags)
        self.assertEqual(self.board(),self.before)

    def test_each_current_physical_declaration_is_required_before_any_board_write(self):
        settings = self.prepare()
        for key in self.flags:
            with self.subTest(key=key),self.assertRaisesRegex(ArmCandidateError,'current_supervised'):
                self.invoke('apply',**settings,**dict(self.flags,**{key:False}))
            self.assertEqual(self.board(),self.before)
        self.assertFalse((self.work/'backup-previous').exists())

    def test_exact_code_arm_and_sensor_hashes_are_all_required(self):
        settings = self.prepare()
        for key in ('reviewed_code_sha','reviewed_helper_sha','reviewed_sensor_sha'):
            with self.subTest(key=key),self.assertRaisesRegex(ArmCandidateError,'exact_reviewed'):
                self.invoke('apply',**dict(settings,**{key:'0'*64}),**self.flags)
            self.assertEqual(self.board(),self.before)

    def test_changed_staged_sensor_is_not_deployed(self):
        settings = self.prepare()
        (self.work/'bmo_imu.py').write_bytes(b'changed sensor dependency')
        with self.assertRaisesRegex(ArmCandidateError,'candidate_changed'):
            self.invoke('apply',**settings,**self.flags)
        self.assertEqual(self.board(),self.before)

    def test_work_on_board_or_board_inside_work_is_rejected(self):
        for directory in (self.mount,self.mount/'review',self.root):
            with self.subTest(directory=directory),self.assertRaisesRegex(ArmCandidateError,'separate'):
                self.invoke(work_directory=directory)

    def test_unknown_board_or_existing_sensor_module_is_never_overwritten(self):
        (self.mount/'bmo_imu.py').write_bytes(b'unknown user sensor module')
        before = self.board()
        with self.assertRaisesRegex(ArmCandidateError,'board_source_mismatch'):self.invoke()
        self.assertEqual(self.board(),before)
        (self.mount/'bmo_imu.py').unlink()
        (self.mount/'boot.py').write_bytes(b'unknown boot code')
        before = self.board()
        with self.assertRaisesRegex(ArmCandidateError,'board_source_mismatch'):self.invoke()
        self.assertEqual(self.board(),before)

    def test_interrupted_file_migration_recovers_from_verified_backup(self):
        for fail_name in ('bmo_arm_bridge.py','code.py'):
            with self.subTest(fail_name=fail_name):
                self.install_old(1,True)
                self.work = self.root/('failure-'+fail_name)
                settings = self.prepare(keep_reviewed_motors=True)
                replace = candidate._replace
                def fail(mount,name,expected,replacement):
                    if name == fail_name:raise ArmCandidateError('injected_write_failure')
                    return replace(mount,name,expected,replacement)
                with patch.object(candidate,'_replace',side_effect=fail):
                    with self.assertRaisesRegex(ArmCandidateError,'injected_write_failure'):
                        self.invoke('apply',**settings,**self.flags)
                self.assertTrue((self.work/'backup-previous'/'SHA256.json').exists())
                self.assertTrue((self.mount/'bmo_imu.py').exists())
                self.invoke('rollback',**settings,**self.flags)
                self.assertEqual(self.board(),self.before)

    def test_corrupted_backup_cannot_be_used_for_rollback(self):
        settings = self.prepare()
        self.invoke('apply',**settings,**self.flags)
        current = self.board()
        (self.work/'backup-previous'/'bmo_arm_bridge.py').write_bytes(b'corrupted backup')
        with self.assertRaisesRegex(ArmCandidateError,'backup_unverified'):
            self.invoke('rollback',**settings,**self.flags)
        self.assertEqual(self.board(),current)

    def test_symlink_staged_file_is_not_followed(self):
        settings = self.prepare()
        target = self.work/'bmo_imu.py'
        raw = target.read_bytes();target.unlink()
        alternate = self.root/'other';alternate.write_bytes(raw)
        target.symlink_to(alternate)
        with self.assertRaises(OSError):self.invoke('apply',**settings,**self.flags)
        self.assertEqual(self.board(),self.before)

    def test_cli_blocks_safely_without_exposing_private_path(self):
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            result = candidate.main(['--mount',str(self.root/'missing')])
        self.assertEqual(result,2)
        self.assertNotIn(str(self.root),output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['transport_status'],'NOT_OPENED')


if __name__ == '__main__':unittest.main()
