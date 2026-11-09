"""Explicit supervised v1→v2 candidate migration; default caller is read-only.

This is a deployment helper, never a daily rearm path. Both firmware writes may
reload CircuitPython and move boot-time PWM: current isolated servo power and
all existing operator declarations are mandatory for apply/rollback. No serial,
reset, calibration, preference, or commissioning record is changed here.
"""
import json
import os
import stat

from . import arm_candidate as base


ROOT = base._ROOT.with_name('bmo_arm_legacy_bridge_v2')


def _templates():
    off, old, old_helper = base._templates()
    code = base._read(ROOT/'code.py')
    helper = base._read(ROOT/'bmo_arm_bridge.py')
    if (code.split(b'\n', 1)[-1] != off
            or base._read(ROOT/'original_code.py') != base._read(base._ROOT/'original_code.py')
            or base._read(ROOT/'boot.py') != base._read(base._ROOT/'boot.py')
            or code.count(b'\nREMOTE_ENABLED = False\n') != 1):
        raise base.ArmCandidateError('recovery_template_mismatch')
    return old, old_helper, code.replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1), helper


def _replace(mount, name, expected, replacement):
    fd = os.open(mount/name, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stream.read(128*1024+1) != expected:
            raise base.ArmCandidateError('board_changed_before_write')
        stream.seek(0); stream.write(replacement); stream.truncate()
        stream.flush(); os.fsync(stream.fileno())
    if base._read(mount/name) != replacement:
        raise base.ArmCandidateError('board_write_unverified_use_retained_backup')


def transition(*, action, mount, work_directory, reviewed_sha, reviewed_helper_sha, declarations):
    old, old_helper, enabled, helper = _templates()
    mount = base._path(mount)
    work = base._path(work_directory) if work_directory is not None else None
    if work is not None and (work == mount or work.is_relative_to(mount) or mount.is_relative_to(work)):
        raise base.ArmCandidateError('private_work_must_be_separate_from_board')
    board = base._board(mount)
    expected = dict(board, **{'code.py':enabled if action == 'rollback' else old,
        'bmo_arm_bridge.py':helper if action == 'rollback' else old_helper})
    if action == 'rollback':
        # Explicit rollback also covers a verified partial two-file apply. No
        # third-party/unknown bytes are overwritten by this recovery path.
        if board['code.py'] not in (old, enabled) or board['bmo_arm_bridge.py'] not in (old_helper, helper):
            raise base.ArmCandidateError('recovery_board_source_mismatch')
    elif board != expected:
        raise base.ArmCandidateError('recovery_board_source_mismatch')
    desired = dict(board, **{'code.py':enabled, 'bmo_arm_bridge.py':helper})
    hashes = {key:base._sha(value) for key,value in board.items()}
    candidate_hashes = {key:base._sha(value) for key,value in desired.items()}
    report = dict(schema='bmo.arm_recovery_candidate.v2', action=action,
        dry_run=action == 'dry_run', board_write_performed=False,
        transport_status='NOT_OPENED', physical_verified=False,
        board_files_sha256=hashes, candidate_files_sha256=candidate_hashes,
        enabled_code_sha256=base._sha(enabled), enabled_helper_sha256=base._sha(helper),
        operator_declarations=declarations, reload_may_move_servos=True,
        candidate_reviewed=False, backup_directory=None, changed_files=[], status='REVIEW_REQUIRED')
    if action == 'dry_run':
        return report
    if work is None:
        raise base.ArmCandidateError('explicit_private_work_directory_required')
    if action == 'prepare':
        work = base._private(work, create=True)
        for key,value in desired.items(): base._new(work/key, value)
        audit = dict(schema=report['schema'], board_files_sha256=hashes,
            candidate_files_sha256=candidate_hashes, mount=str(mount), candidate_reviewed=False)
        base._new(work/'review.json', (json.dumps(audit,sort_keys=True)+'\n').encode())
        base._sync_directory(work); base._sync_directory(work.parent)
        report['status'] = 'CANDIDATE_PREPARED_NOT_APPROVED'
        return report
    if reviewed_sha != base._sha(enabled) or reviewed_helper_sha != base._sha(helper):
        raise base.ArmCandidateError('exact_reviewed_code_and_helper_sha_required')
    if not all(declarations.values()):
        raise base.ArmCandidateError('current_supervised_conditions_required')
    work = base._private(work)
    if any(base._read(work/key) != value for key,value in desired.items()):
        raise base.ArmCandidateError('candidate_changed')
    audit = json.loads(base._read(work/'review.json'))
    old_files = dict(board, **{'code.py':old, 'bmo_arm_bridge.py':old_helper})
    old_hashes = {key:base._sha(value) for key,value in old_files.items()}
    if (type(audit) is not dict or audit.get('schema') != report['schema']
            or audit.get('mount') != str(mount) or audit.get('candidate_files_sha256') != candidate_hashes
            or audit.get('board_files_sha256') != old_hashes):
        raise base.ArmCandidateError('recovery_audit_mismatch')
    backup = work/'backup-v1'
    if action == 'apply':
        if backup.exists():
            raise base.ArmCandidateError('backup_exists_new_review_directory_required')
        backup.mkdir(mode=0o700)
        for key,value in board.items(): base._new(backup/key,value)
        base._new(backup/'SHA256.json',(json.dumps(hashes,sort_keys=True)+'\n').encode())
        base._sync_directory(backup); base._sync_directory(work)
    else:
        base._private(backup)
    if (any(base._read(backup/key) != value for key,value in old_files.items())
            or json.loads(base._read(backup/'SHA256.json')) != old_hashes):
        raise base.ArmCandidateError('backup_unverified')
    if base._board(mount) != board:
        raise base.ArmCandidateError('board_changed_before_write')
    replacement = old_files if action == 'rollback' else desired
    # These two files cannot be replaced atomically on CIRCUITPY. Isolated servo
    # power is mandatory and the complete retained backup covers a partial write.
    for key in ('bmo_arm_bridge.py','code.py'):
        _replace(mount,key,board[key],replacement[key])
    if base._board(mount) != replacement:
        raise base.ArmCandidateError('board_write_unverified_use_retained_backup')
    report.update(status='STATIC_ROLLED_BACK' if action == 'rollback' else 'STATIC_UPDATED_NOT_MOTION_TESTED',
        board_write_performed=True, candidate_reviewed=True,
        changed_files=['bmo_arm_bridge.py','code.py'], backup_directory=str(backup))
    return report
