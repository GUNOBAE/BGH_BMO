"""Review-first B1 flag transition. Default dry-run never changes the board.

Writing code.py can trigger CircuitPython auto-reload and set_arms(0). Applying
or rolling back requires a present supervisor, isolated servo power, a clear
workspace, an immediate physical cutoff, and explicit acknowledgement of that
reload risk. These declarations are not sensor evidence. No serial/reset,
calibration, helper, or boot change is performed here.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .features.motion.arm_serial_posix_v2 import MOUNT
from .features.motion.legacy_host import validate_legacy_install

_ROOT = Path(__file__).resolve().parents[2] / 'firmware/staging/bmo_arm_legacy_bridge_v1'
_OFF_SHA = '4e30cb8dd29f3af03867828da2fee7e1ea1c1e5099be725e7daa11b98f67ebae'
_ORIGINAL_SHA = 'fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e'
_HELPER_SHA = '14fc42886a39f5b74fb7bc28e7eecf0914801b05e1fe5bd74cfdb3df0f0fcbfe'
_FILES = ('code.py', 'bmo_arm_bridge.py', 'boot.py')
_FLAGS = ('user_present', 'servo_power_isolated', 'clearance_verified',
          'physical_cutoff_ready', 'reload_risk_acknowledged')


class ArmCandidateError(ValueError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _path(value):
    path = Path(value).absolute()
    if '..' in path.parts or path.resolve() != path:
        raise ArmCandidateError('symlink_or_relative_parent')
    return path


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 128 * 1024:
            raise ArmCandidateError('unsafe_file')
        data = os.read(fd, 128 * 1024 + 1)
        if len(data) != info.st_size:
            raise ArmCandidateError('changed_or_oversize_file')
        return data
    finally:
        os.close(fd)


def _private(directory, *, create=False):
    directory = _path(directory)
    if create:
        # Only the explicit private work path is created, never board parents.
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ArmCandidateError('private_directory_required')
    return directory


def _new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # Keep any partial artifact for review; never overwrite on retry.
        raise


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _templates():
    original, off, helper = (_read(_ROOT / name)
                            for name in ('original_code.py', 'code.py', 'bmo_arm_bridge.py'))
    if (_sha(original) != _ORIGINAL_SHA or _sha(off) != _OFF_SHA
            or _sha(helper) != _HELPER_SHA
            or off.count(b'\nREMOTE_ENABLED = False\n') != 1):
        raise ArmCandidateError('reviewed_template_mismatch')
    return off, off.replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1), helper


def _board(mount):
    data = {name: _read(mount / name) for name in _FILES}
    if b'usb_cdc.enable(console=True, data=True)' not in data['boot.py']:
        raise ArmCandidateError('dedicated_data_not_reviewed')
    return data


def _replace_code(mount, expected, replacement):
    """One existing file only, with a final descriptor-bound byte recheck."""
    fd = os.open(mount / 'code.py', os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r+b') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stream.read(128 * 1024 + 1) != expected:
            raise ArmCandidateError('board_changed_before_write')
        stream.seek(0)
        stream.write(replacement)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    if _read(mount / 'code.py') != replacement:
        raise ArmCandidateError('board_write_unverified_use_retained_backup')


def candidate_transition(*, action='dry_run', mount=MOUNT, work_directory=None,
                         reviewed_sha=None, user_present=False, servo_power_isolated=False,
                         clearance_verified=False, physical_cutoff_ready=False,
                         reload_risk_acknowledged=False):
    """Prepare/apply/restore exact reviewed bytes; never imply physical success."""
    if action not in ('dry_run', 'prepare', 'apply', 'rollback'):
        raise ArmCandidateError('invalid_action')
    declarations = dict(user_present=user_present, servo_power_isolated=servo_power_isolated,
        clearance_verified=clearance_verified, physical_cutoff_ready=physical_cutoff_ready,
        reload_risk_acknowledged=reload_risk_acknowledged)
    if any(type(value) is not bool for value in declarations.values()):
        raise ArmCandidateError('literal_operator_declarations_required')
    off, enabled, helper = _templates()
    digest = _sha(enabled)
    mount = _path(mount)
    work = _path(work_directory) if work_directory is not None else None
    if work is not None and (work == mount or work.is_relative_to(mount)
                             or mount.is_relative_to(work)):
        raise ArmCandidateError('private_work_must_be_separate_from_board')
    board = _board(mount)
    if board['bmo_arm_bridge.py'] != helper:
        raise ArmCandidateError('board_helper_mismatch')
    expected = enabled if action == 'rollback' else off
    if board['code.py'] != expected:
        raise ArmCandidateError('board_source_mismatch')
    hashes = {name: _sha(data) for name, data in board.items()}
    report = dict(schema='bmo.arm_candidate.v1', action=action, dry_run=action == 'dry_run',
        board_write_performed=False, transport_status='NOT_OPENED', physical_verified=False,
        off_code_sha256=_sha(off), enabled_code_sha256=digest, board_files_sha256=hashes,
        operator_declarations=declarations, reload_may_move_servos=True,
        candidate_reviewed=False, backup_directory=None,
        changed_files=[], status='REVIEW_REQUIRED')
    if action == 'dry_run':
        return report
    if work is None:
        raise ArmCandidateError('explicit_private_work_directory_required')
    if action == 'prepare':
        work = _private(work, create=True)
        audit = dict(schema=report['schema'], off_code_sha256=_sha(off),
            enabled_code_sha256=digest, board_files_sha256=hashes,
            mount=str(mount), candidate_reviewed=False)
        _new(work / 'enabled_code.py', enabled)
        _new(work / 'review.json', (json.dumps(audit, sort_keys=True, indent=2)+'\n').encode())
        _sync_directory(work)
        _sync_directory(work.parent)
        report.update(status='CANDIDATE_PREPARED_NOT_APPROVED')
        return report
    if (type(reviewed_sha) is not str or re.fullmatch(r'[a-f0-9]{64}', reviewed_sha) is None
            or reviewed_sha != digest):
        raise ArmCandidateError('exact_reviewed_enabled_sha_required')
    if not all(declarations.values()):
        raise ArmCandidateError('current_supervised_conditions_required')
    work = _private(work)
    if _read(work / 'enabled_code.py') != enabled:
        raise ArmCandidateError('candidate_changed')
    audit = json.loads(_read(work / 'review.json'))
    if (type(audit) is not dict or audit.get('schema') != report['schema'] or audit.get('mount') != str(mount)
            or audit.get('enabled_code_sha256') != digest
            or audit.get('off_code_sha256') != _sha(off)):
        raise ArmCandidateError('candidate_audit_mismatch')
    if type(audit.get('board_files_sha256')) is not dict:
        raise ArmCandidateError('candidate_audit_mismatch')
    expected_hashes = dict(audit['board_files_sha256'])
    expected_hashes['code.py'] = _sha(expected)
    if hashes != expected_hashes:
        raise ArmCandidateError('board_changed_since_prepare')
    backup = work / 'backup-off'
    if action == 'apply':
        if backup.exists():
            raise ArmCandidateError('backup_exists_new_review_directory_required')
        backup.mkdir(mode=0o700, exist_ok=False)
        for name in _FILES:
            _new(backup / name, board[name])
        _new(backup / 'SHA256.json', (json.dumps(hashes, sort_keys=True)+'\n').encode())
        _sync_directory(backup)
        _sync_directory(work)
        # Full backup is durable and rechecked before the only board write.
        if any(_read(backup / name) != board[name] for name in _FILES):
            raise ArmCandidateError('backup_unverified')
    else:
        _private(backup)
        saved_hashes = json.loads(_read(backup / 'SHA256.json'))
        saved = {name: _read(backup / name) for name in _FILES}
        if ({name: _sha(data) for name, data in saved.items()} != saved_hashes
                or saved != dict(board, **{'code.py': off})):
            raise ArmCandidateError('backup_or_dependencies_changed')
    if _board(mount) != board:
        raise ArmCandidateError('board_changed_before_write')
    _replace_code(mount, expected, off if action == 'rollback' else enabled)
    if action == 'apply':
        validate_legacy_install(_ROOT, digest, mount=mount)
    report.update(status='STATIC_ROLLED_BACK' if action == 'rollback' else 'STATIC_ENABLED_NOT_MOTION_TESTED',
        board_write_performed=True, candidate_reviewed=True, changed_files=['code.py'],
        backup_directory=str(backup))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    for name in ('prepare', 'apply', 'rollback'):
        actions.add_argument('--'+name, action='store_const', const=name, dest='action')
    parser.set_defaults(action='dry_run')
    parser.add_argument('--mount', type=Path, default=MOUNT)
    parser.add_argument('--work-directory', type=Path)
    parser.add_argument('--reviewed-code-sha', dest='reviewed_sha')
    for name in _FLAGS:
        parser.add_argument('--'+name.replace('_', '-'), action='store_true')
    args = parser.parse_args(argv)
    try:
        report = candidate_transition(**vars(args))
    except (OSError, ValueError) as error:
        # Safe code only, never arbitrary filesystem/provider exception details.
        code = str(error) if isinstance(error, ArmCandidateError) else 'arm_candidate_io_or_validation_failed'
        print(json.dumps(dict(status='BLOCKED', code=code, transport_status='NOT_OPENED', physical_verified=False)))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
