"""현재 펌웨어의 실물 팔 차단 사유. 포트/REPL/PWM/펌웨어 쓰기 경로는 없다."""
import ast
from dataclasses import asdict, dataclass
import hashlib

from ..contracts import FeatureError, request_id_ok
from .calibration import CalibrationProfile


def inspect_firmware_source(source):
    """이미 읽은 code.py 바이트만 정적 검사한다. import/실행/보드 접속을 하지 않는다."""
    if type(source) is not bytes or not 0 < len(source) <= 128 * 1024:
        raise FeatureError('firmware_evidence_invalid')
    try:
        tree = ast.parse(source.decode('utf-8'))
    except (SyntaxError, UnicodeError, RecursionError):
        raise FeatureError('firmware_evidence_invalid') from None
    imports, pins = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split('.')[0])
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'board':
            pins.add(node.attr)
    boot_motion = any(isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                      and isinstance(node.value.func, ast.Name) and node.value.func.id == 'set_arms'
                      for node in tree.body)
    return {'sha256': hashlib.sha256(source).hexdigest(), 'bytes': len(source),
            'hid_present': 'usb_hid' in imports,
            'dedicated_data_import_present': 'usb_cdc' in imports,
            'servo_pins_present': {'GP10', 'GP11'} <= pins,
            'gp7_present': 'GP7' in pins, 'gp8_present': 'GP8' in pins,
            'boot_arm_call_present': boot_motion,
            'protocol_verified': False, 'physical_tested': False,
            'code': 'firmware_static_evidence_only'}


@dataclass(frozen=True, slots=True)
class ArmSafetyChecklist:
    user_present: bool = False
    isolated_servo_power: bool = False
    common_ground: bool = False
    current_limit_verified: bool = False
    power_integrity_verified: bool = False
    physical_kill_verified: bool = False
    both_limits_verified: bool = False
    watchdog_verified: bool = False
    stop_protocol_verified: bool = False
    no_boot_motion_verified: bool = False

    def __post_init__(self):
        if any(type(value) is not bool for value in asdict(self).values()):
            raise FeatureError('arm_checklist_invalid')


def arm_readiness(*, profile=None, checklist=None, firmware_evidence=None):
    """사람이 체크했다고 해도 없는 host protocol/STOP을 생성하지 않는다."""
    checklist = checklist or ArmSafetyChecklist()
    if not isinstance(checklist, ArmSafetyChecklist):
        raise FeatureError('arm_checklist_invalid')
    if profile is not None and not isinstance(profile, CalibrationProfile):
        raise FeatureError('invalid_calibration')
    if firmware_evidence is not None and type(firmware_evidence) is not dict:
        raise FeatureError('firmware_evidence_invalid')
    reasons = ['arm_live_protocol_unimplemented']
    for name, checked in asdict(checklist).items():
        if not checked:
            reasons.append('arm_' + name + '_required')
    if profile is None or not profile.reviewed:
        reasons.append('arm_calibration_unverified')
    if firmware_evidence is None:
        reasons.append('arm_firmware_source_unverified')
    elif firmware_evidence.get('boot_arm_call_present') is True:
        reasons.append('arm_boot_motion_present')
    return {'readiness': 'BLOCKED', 'code': 'arm_live_protocol_unimplemented',
            'reasons': reasons, 'simulated': False, 'executed': False,
            'position_verified': False, 'imu_used_for_control': False,
            'host_command_supported': False, 'physical_tested': False,
            'gp7_policy': 'preserve_existing_toggle_do_not_press_for_host_test'}


def prepare_one_arm_test(*, request_id, side, source='operator', profile=None,
                         checklist=None, firmware_evidence=None):
    """새 프로토콜·기계 검수 전까지 모든 실제 경로를 거부한다. 자동 replay가 없다."""
    if not request_id_ok(request_id) or side not in ('left', 'right') or source != 'operator':
        raise FeatureError('arm_operator_intent_required')
    readiness = arm_readiness(profile=profile, checklist=checklist, firmware_evidence=firmware_evidence)
    raise FeatureError(readiness['code'])
