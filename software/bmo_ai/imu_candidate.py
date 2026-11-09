"""Review-first B1+MPU6050 candidate installation; never a runtime updater.

Default dry-run is read-only. Even telemetry-only installation can trigger the
existing boot pose through CircuitPython auto-reload, so apply and rollback
require current physical declarations. No serial, reset or sensor IO is used.
"""
import argparse
import json
import os
from pathlib import Path

from . import arm_candidate as base
from .arm_recovery_candidate import _replace

ROOT = base._ROOT.with_name('bmo_arm_legacy_bridge_v4')
SCHEMA = 'bmo.imu_candidate.v4'
OLD_FILES = ('code.py', 'bmo_arm_bridge.py', 'boot.py')
FILES = (*OLD_FILES, 'bmo_imu.py')


def _templates():
    off, _, _ = base._templates()
    previous = []
    for version in (1, 2, 3):
        root = base._ROOT.with_name('bmo_arm_legacy_bridge_v'+str(version))
        files = {name:base._read(root/name) for name in OLD_FILES}
        code = files['code.py']
        if (code.count(b'\nREMOTE_ENABLED = False\n') != 1
                or base._read(root/'original_code.py') != base._read(base._ROOT/'original_code.py')
                or files['boot.py'] != base._read(base._ROOT/'boot.py')
                or (code != off if version == 1 else code.split(b'\n', 1)[-1] != off)):
            raise base.ArmCandidateError('imu_previous_template_mismatch')
        for enabled in (False, True):
            data = dict(files)
            if enabled:
                data['code.py'] = code.replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
            previous.append((version, enabled, data))
    candidate = {name:base._read(ROOT/name) for name in FILES}
    if (candidate['code.py'].count(b'\nREMOTE_ENABLED = False\n') != 1
            or candidate['boot.py'] != base._read(base._ROOT/'boot.py')
            or base._read(ROOT/'original_code.py') != base._read(base._ROOT/'original_code.py')):
        raise base.ArmCandidateError('imu_candidate_template_mismatch')
    return previous, candidate


def _board(mount):
    files = {name:base._read(mount/name) for name in OLD_FILES}
    try:
        files['bmo_imu.py'] = base._read(mount/'bmo_imu.py')
    except FileNotFoundError:
        files['bmo_imu.py'] = None
    return files


def _hashes(files):
    return {name:base._sha(data) for name,data in files.items() if data is not None}


def candidate_transition(*, action='dry_run', mount=base.MOUNT, work_directory=None,
                         reviewed_code_sha=None, reviewed_helper_sha=None,
                         reviewed_sensor_sha=None, keep_reviewed_motors=False,
                         user_present=False, servo_power_isolated=False,
                         clearance_verified=False, physical_cutoff_ready=False,
                         reload_risk_acknowledged=False):
    if action not in ('dry_run', 'prepare', 'apply', 'rollback'):
        raise base.ArmCandidateError('invalid_action')
    declarations = dict(user_present=user_present, servo_power_isolated=servo_power_isolated,
        clearance_verified=clearance_verified, physical_cutoff_ready=physical_cutoff_ready,
        reload_risk_acknowledged=reload_risk_acknowledged)
    if any(type(value) is not bool for value in (*declarations.values(), keep_reviewed_motors)):
        raise base.ArmCandidateError('literal_operator_declarations_required')
    previous, template = _templates()
    mount = base._path(mount)
    work = base._path(work_directory) if work_directory is not None else None
    if work is not None and (work == mount or work.is_relative_to(mount) or mount.is_relative_to(work)):
        raise base.ArmCandidateError('private_work_must_be_separate_from_board')
    board = _board(mount)
    audit = None
    if action == 'rollback':
        if work is None:
            raise base.ArmCandidateError('explicit_private_work_directory_required')
        work = base._private(work)
        audit = json.loads(base._read(work/'review.json'))
        if type(audit) is not dict or audit.get('schema') != SCHEMA:
            raise base.ArmCandidateError('imu_audit_mismatch')
        matches = [(v,on,files) for v,on,files in previous
                   if _hashes(files) == audit.get('board_files_sha256')]
    else:
        matches = [(v,on,files) for v,on,files in previous
                   if board == dict(files, **{'bmo_imu.py':None})]
    if len(matches) != 1:
        raise base.ArmCandidateError('imu_board_source_mismatch')
    version, old_enabled, old_files = matches[0]
    if keep_reviewed_motors and not old_enabled:
        raise base.ArmCandidateError('imu_cannot_grant_motor_permission')
    desired = dict(template)
    if keep_reviewed_motors:
        desired['code.py'] = desired['code.py'].replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
    old_hashes, candidate_hashes = _hashes(old_files), _hashes(desired)
    # Recovery accepts only the old/new exact bytes of each known file. This
    # permits recovery from an interrupted multi-file write without accepting
    # an unrelated board change or overwriting an unknown sensor module.
    if action == 'rollback' and any(board[name] not in (old_files.get(name), desired[name]) for name in FILES):
        raise base.ArmCandidateError('imu_board_source_mismatch')
    report = dict(schema=SCHEMA, action=action, dry_run=action == 'dry_run',
        previous_protocol_version=version, previous_remote_enabled=old_enabled,
        candidate_remote_enabled=keep_reviewed_motors, motors_newly_enabled=False,
        board_write_performed=False, transport_status='NOT_OPENED',
        physical_verified=False, sensor_health_verified=False,
        board_files_sha256=_hashes(board), candidate_files_sha256=candidate_hashes,
        operator_declarations=declarations, reload_may_move_servos=True,
        candidate_reviewed=False, backup_directory=None, changed_files=[], status='REVIEW_REQUIRED')
    if action == 'dry_run':
        return report
    if work is None:
        raise base.ArmCandidateError('explicit_private_work_directory_required')
    expected_audit = dict(schema=SCHEMA, previous_protocol_version=version,
        previous_remote_enabled=old_enabled, keep_reviewed_motors=keep_reviewed_motors,
        board_files_sha256=old_hashes, candidate_files_sha256=candidate_hashes,
        mount=str(mount), candidate_reviewed=False)
    if action == 'prepare':
        work = base._private(work, create=True)
        for name,data in desired.items():base._new(work/name,data)
        base._new(work/'review.json',(json.dumps(expected_audit,sort_keys=True)+'\n').encode())
        base._sync_directory(work);base._sync_directory(work.parent)
        report['status'] = 'CANDIDATE_PREPARED_NOT_APPROVED'
        return report
    if (reviewed_code_sha != candidate_hashes['code.py']
            or reviewed_helper_sha != candidate_hashes['bmo_arm_bridge.py']
            or reviewed_sensor_sha != candidate_hashes['bmo_imu.py']):
        raise base.ArmCandidateError('exact_reviewed_code_arm_sensor_sha_required')
    if not all(declarations.values()):
        raise base.ArmCandidateError('current_supervised_conditions_required')
    work = base._private(work)
    if any(base._read(work/name) != data for name,data in desired.items()):
        raise base.ArmCandidateError('candidate_changed')
    if json.loads(base._read(work/'review.json')) != expected_audit:
        raise base.ArmCandidateError('imu_audit_mismatch')
    backup = work/'backup-previous'
    if action == 'apply':
        if backup.exists():
            raise base.ArmCandidateError('backup_exists_new_review_directory_required')
        backup.mkdir(mode=0o700)
        for name,data in old_files.items():base._new(backup/name,data)
        base._new(backup/'SHA256.json',(json.dumps(old_hashes,sort_keys=True)+'\n').encode())
        base._sync_directory(backup);base._sync_directory(work)
    else:
        base._private(backup)
    if (any(base._read(backup/name) != data for name,data in old_files.items())
            or json.loads(base._read(backup/'SHA256.json')) != old_hashes):
        raise base.ArmCandidateError('backup_unverified')
    if _board(mount) != board:
        raise base.ArmCandidateError('board_changed_before_write')
    changed = []
    if action == 'rollback':
        for name in ('code.py','bmo_arm_bridge.py'):
            _replace(mount,name,board[name],old_files[name]);changed.append(name)
        if board['bmo_imu.py'] is not None:
            if base._read(mount/'bmo_imu.py') != desired['bmo_imu.py']:
                raise base.ArmCandidateError('board_changed_before_write')
            os.unlink(mount/'bmo_imu.py');changed.append('bmo_imu.py')
        replacement = dict(old_files, **{'bmo_imu.py':None})
    else:
        # New dependency first, old-compatible bridge next, importing code last.
        # Individual file writes are not atomic as a group. Servo power must
        # remain isolated through all writes, auto-reload and verification.
        base._new(mount/'bmo_imu.py',desired['bmo_imu.py']);changed.append('bmo_imu.py')
        for name in ('bmo_arm_bridge.py','code.py'):
            _replace(mount,name,board[name],desired[name]);changed.append(name)
        replacement = desired
    base._sync_directory(mount)
    if _board(mount) != replacement:
        raise base.ArmCandidateError('board_write_unverified_use_retained_backup')
    report.update(status='STATIC_ROLLED_BACK' if action == 'rollback' else 'STATIC_UPDATED_NOT_SENSOR_TESTED',
        board_write_performed=True, candidate_reviewed=True,
        changed_files=changed, backup_directory=str(backup))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    for name in ('prepare','apply','rollback'):
        actions.add_argument('--'+name,action='store_const',const=name,dest='action')
    parser.set_defaults(action='dry_run')
    parser.add_argument('--mount',type=Path,default=base.MOUNT)
    parser.add_argument('--work-directory',type=Path)
    parser.add_argument('--keep-reviewed-motors',action='store_true')
    for name in ('code','helper','sensor'):
        parser.add_argument('--reviewed-'+name+'-sha')
    for name in base._FLAGS:
        parser.add_argument('--'+name.replace('_','-'),action='store_true')
    args = vars(parser.parse_args(argv))
    try:
        report = candidate_transition(**args)
    except (OSError,ValueError) as error:
        code = str(error) if isinstance(error,base.ArmCandidateError) else 'imu_candidate_io_or_validation_failed'
        print(json.dumps(dict(status='BLOCKED',code=code,transport_status='NOT_OPENED',physical_verified=False)))
        return 2
    print(json.dumps(report,sort_keys=True,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
