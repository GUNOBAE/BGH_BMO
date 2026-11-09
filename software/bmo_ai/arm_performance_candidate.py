"""Explicit reviewed v1/v2 → v3 migration, never called by the runtime.

The default is read-only. Apply/rollback reuse the existing current supervisor,
isolated servo power, cutoff and reload declarations; no USB reset or serial IO.
"""
import json

from . import arm_candidate as base
from .arm_recovery_candidate import _replace

ROOT = base._ROOT.with_name('bmo_arm_legacy_bridge_v3')


def _templates():
    off, v1, helper1 = base._templates()
    versions = [(v1, helper1)]
    for version in (2, 3):
        root = base._ROOT.with_name('bmo_arm_legacy_bridge_v'+str(version))
        code, helper = base._read(root/'code.py'), base._read(root/'bmo_arm_bridge.py')
        if (code.split(b'\n', 1)[-1] != off
                or base._read(root/'original_code.py') != base._read(base._ROOT/'original_code.py')
                or base._read(root/'boot.py') != base._read(base._ROOT/'boot.py')
                or code.count(b'\nREMOTE_ENABLED = False\n') != 1):
            raise base.ArmCandidateError('performance_template_mismatch')
        versions.append((code.replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1), helper))
    return versions[:2], versions[2]


def transition(*, action, mount, work_directory, reviewed_sha, reviewed_helper_sha, declarations):
    previous, (enabled, helper) = _templates()
    mount = base._path(mount)
    work = base._path(work_directory) if work_directory is not None else None
    if work is not None and (work == mount or work.is_relative_to(mount) or mount.is_relative_to(work)):
        raise base.ArmCandidateError('private_work_must_be_separate_from_board')
    board = base._board(mount)
    schema = 'bmo.arm_performance_candidate.v3'
    audit = None
    if action == 'rollback':
        if work is None:
            raise base.ArmCandidateError('explicit_private_work_directory_required')
        work = base._private(work)
        audit = json.loads(base._read(work/'review.json'))
        if type(audit) is not dict or audit.get('schema') != schema:
            raise base.ArmCandidateError('performance_audit_mismatch')
        versions = [(index+1, code, dep) for index,(code,dep) in enumerate(previous)
            if audit.get('board_files_sha256', {}).get('code.py') == base._sha(code)
            and audit.get('board_files_sha256', {}).get('bmo_arm_bridge.py') == base._sha(dep)]
    else:
        versions = [(index+1, code, dep) for index,(code,dep) in enumerate(previous)
                    if board['code.py'] == code and board['bmo_arm_bridge.py'] == dep]
    if len(versions) != 1:
        raise base.ArmCandidateError('performance_board_source_mismatch')
    version, old, old_helper = versions[0]
    if action == 'rollback' and (board['code.py'] not in (old, enabled)
            or board['bmo_arm_bridge.py'] not in (old_helper, helper)):
        raise base.ArmCandidateError('performance_board_source_mismatch')
    old_files = dict(board, **{'code.py':old, 'bmo_arm_bridge.py':old_helper})
    desired = dict(board, **{'code.py':enabled, 'bmo_arm_bridge.py':helper})
    hashes = {key:base._sha(value) for key,value in old_files.items()}
    candidate_hashes = {key:base._sha(value) for key,value in desired.items()}
    report = dict(schema=schema, action=action, previous_protocol_version=version,
        dry_run=action == 'dry_run', board_write_performed=False, transport_status='NOT_OPENED',
        physical_verified=False, board_files_sha256={key:base._sha(value) for key,value in board.items()},
        candidate_files_sha256=candidate_hashes, enabled_code_sha256=base._sha(enabled),
        enabled_helper_sha256=base._sha(helper), operator_declarations=declarations,
        reload_may_move_servos=True, candidate_reviewed=False, backup_directory=None,
        changed_files=[], status='REVIEW_REQUIRED')
    if action == 'dry_run':
        return report
    if work is None:
        raise base.ArmCandidateError('explicit_private_work_directory_required')
    if action == 'prepare':
        work = base._private(work, create=True)
        for key,value in desired.items(): base._new(work/key, value)
        audit = dict(schema=schema, previous_protocol_version=version,
            board_files_sha256=hashes, candidate_files_sha256=candidate_hashes,
            mount=str(mount), candidate_reviewed=False)
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
    if (type(audit) is not dict or audit.get('schema') != schema
            or audit.get('mount') != str(mount) or audit.get('candidate_files_sha256') != candidate_hashes
            or audit.get('board_files_sha256') != hashes or audit.get('previous_protocol_version') != version):
        raise base.ArmCandidateError('performance_audit_mismatch')
    backup = work/'backup-previous'
    if action == 'apply':
        if backup.exists():
            raise base.ArmCandidateError('backup_exists_new_review_directory_required')
        backup.mkdir(mode=0o700)
        for key,value in old_files.items(): base._new(backup/key,value)
        base._new(backup/'SHA256.json',(json.dumps(hashes,sort_keys=True)+'\n').encode())
        base._sync_directory(backup); base._sync_directory(work)
    else:
        base._private(backup)
    if (any(base._read(backup/key) != value for key,value in old_files.items())
            or json.loads(base._read(backup/'SHA256.json')) != hashes):
        raise base.ArmCandidateError('backup_unverified')
    if base._board(mount) != board:
        raise base.ArmCandidateError('board_changed_before_write')
    replacement = old_files if action == 'rollback' else desired
    for key in ('bmo_arm_bridge.py','code.py'):
        _replace(mount,key,board[key],replacement[key])
    if base._board(mount) != replacement:
        raise base.ArmCandidateError('board_write_unverified_use_retained_backup')
    report.update(status='STATIC_ROLLED_BACK' if action == 'rollback' else 'STATIC_UPDATED_NOT_MOTION_TESTED',
        board_write_performed=True, candidate_reviewed=True, changed_files=['bmo_arm_bridge.py','code.py'],
        backup_directory=str(backup))
    return report
