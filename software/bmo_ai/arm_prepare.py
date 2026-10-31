"""Read-only B1 supervised-session preparation; never opens or writes a board.

Run ``python -m bmo_ai.arm_prepare`` for a safe default dry-run. This tool does
not create enabled firmware, choose a reviewed SHA, reset USB, acquire a serial
port, or send HELLO/DO/STOP. A launcher command is shown only after exact static
verification and the operator's explicit current-session declarations.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import stat

from .features.motion.arm_serial_posix_v2 import MOUNT
from .features.motion.legacy_host import (LegacyArmError, NAMED_GESTURES,
                                         validate_legacy_install)


def _source(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 128 * 1024:
        raise ValueError('unsafe_source')
    data = path.read_bytes()
    if not data or len(data) > 128 * 1024:
        raise ValueError('unsafe_source')
    return data


def _remote_setting(data):
    values = []
    for node in ast.parse(data.decode('utf-8')).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'REMOTE_ENABLED'
                for target in node.targets):
            values.append(node.value.value if isinstance(node.value, ast.Constant)
                          and type(node.value.value) is bool else None)
    return values[0] if len(values) == 1 else None


def prepare_legacy_session(*, firmware_root=None, mount=MOUNT, reviewed_sha=None,
                           user_present=False, power_off_ready=False):
    """Inspect existing bytes only. Static agreement is not a physical PASS."""
    if any(type(value) is not bool for value in (user_present, power_off_ready)):
        raise ValueError('literal_operator_declarations_required')
    if reviewed_sha is not None and (type(reviewed_sha) is not str
            or re.fullmatch(r'[a-f0-9]{64}', reviewed_sha) is None):
        raise ValueError('reviewed_sha_required')
    root = Path(firmware_root) if firmware_root is not None else (
        Path(__file__).resolve().parents[2] / 'firmware/staging/bmo_arm_legacy_bridge_v1')
    mount = Path(mount)
    report = dict(dry_run=True, protocol='bmo_legacy_b1',
        static_status='NOT_CHECKED', static_code='arm_firmware_source_unavailable',
        board_source_sha256=None, remote_enabled_static=None,
        reviewed_sha_supplied=reviewed_sha is not None,
        transport_status='NOT_OPENED', physical_verified=False, executed=False,
        supported_gestures=sorted(NAMED_GESTURES), side_reference='bmo_own',
        operator_declarations={'user_present': user_present, 'power_off_ready': power_off_ready},
        blocked_reasons=[], launcher_argv=None,
        cancel_semantics='hold_current_commanded_position',
        stop_semantics='latch_until_session_restart_not_power_cut',
        rollback='close_candidate_normally_then_use_preserved_BMO_REAL_ROBOT')
    try:
        code = _source(mount / 'code.py')
        report['board_source_sha256'] = hashlib.sha256(code).hexdigest()
        report['remote_enabled_static'] = _remote_setting(code)
        # Bound every input before the existing full-byte verifier reads it.
        for directory, names in ((root, ('original_code.py', 'code.py', 'bmo_arm_bridge.py')),
                                 (mount, ('bmo_arm_bridge.py', 'boot.py'))):
            for name in names:
                _source(directory / name)
        if report['remote_enabled_static'] is False:
            report.update(static_status='BLOCKED', static_code='arm_board_disabled')
        elif report['remote_enabled_static'] is not True:
            report.update(static_status='BLOCKED', static_code='arm_remote_setting_unknown')
        elif reviewed_sha is None:
            report.update(static_status='NEEDS_REVIEW', static_code='arm_reviewed_sha_required')
        else:
            validate_legacy_install(root, reviewed_sha, mount=mount)
            report.update(static_status='STATIC_MATCH', static_code='arm_exact_source_match')
    except LegacyArmError as error:
        report.update(static_status='BLOCKED', static_code='arm_' + error.code)
    except (OSError, ValueError, SyntaxError, UnicodeError, RecursionError):
        report.update(static_status='BLOCKED', static_code='arm_firmware_source_unavailable')
    if report['static_status'] != 'STATIC_MATCH':
        report['blocked_reasons'].append(report['static_code'])
    if not user_present or not power_off_ready:
        report['blocked_reasons'].append('arm_operator_condition_missing')
    if report['blocked_reasons']:
        report['readiness'] = 'NEEDS_OPERATOR'
    else:
        report['readiness'] = 'PREPARED_NOT_ACTIVATED'
        report['launcher_argv'] = ['./launch-character-life', '--arms-supervised',
            '--arm-legacy-bridge', '--arm-reviewed-code-sha', reviewed_sha,
            '--arm-power-off-ready']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mount', type=Path, default=MOUNT)
    parser.add_argument('--reviewed-code-sha')
    parser.add_argument('--user-present', action='store_true')
    parser.add_argument('--power-off-ready', action='store_true')
    options = parser.parse_args(argv)
    try:
        report = prepare_legacy_session(mount=options.mount,
            reviewed_sha=options.reviewed_code_sha, user_present=options.user_present,
            power_off_ready=options.power_off_ready)
    except ValueError:
        parser.error('a previously reviewed full SHA256 and literal operator declarations are required')
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['readiness'] == 'PREPARED_NOT_ACTIVATED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
