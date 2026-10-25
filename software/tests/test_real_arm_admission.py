"""현 펌웨어의 실제 이동 경로가 없고 체크리스트만으로 해제되지 않음을 검증한다."""
import ast
from dataclasses import fields
from pathlib import Path
import unittest

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.motion.admission import (
    ArmSafetyChecklist, arm_readiness, inspect_firmware_source, prepare_one_arm_test,
)
from bmo_ai.features.motion.calibration import ArmLimits, CalibrationProfile


class ArmAdmissionTests(unittest.TestCase):
    def test_readiness_is_blocked_and_imu_never_used_for_control(self):
        state=arm_readiness()
        self.assertEqual(state['readiness'],'BLOCKED')
        self.assertFalse(state['host_command_supported'])
        self.assertFalse(state['executed'])
        self.assertFalse(state['imu_used_for_control'])
        self.assertIn('arm_physical_kill_verified_required',state['reasons'])

    def test_all_checked_profile_does_not_invent_firmware_protocol(self):
        checked=ArmSafetyChecklist(**{field.name:True for field in fields(ArmSafetyChecklist)})
        profile=CalibrationProfile(ArmLimits(40,80,100,1),ArmLimits(30,70,110,-1),True,True)
        with self.assertRaises(FeatureError) as caught:
            prepare_one_arm_test(request_id='human-test',side='left',profile=profile,checklist=checked)
        self.assertEqual(caught.exception.code,'arm_live_protocol_unimplemented')

    def test_model_or_unknown_side_cannot_prepare_physical_test(self):
        for options in ({'side':'left','source':'model'},{'side':'both'},{'side':'arbitrary-pwm'}):
            with self.subTest(options=options), self.assertRaises(FeatureError) as caught:
                prepare_one_arm_test(request_id='test',**options)
            self.assertEqual(caught.exception.code,'arm_operator_intent_required')

    def test_firmware_static_evidence_does_not_execute_import_or_boot_motion(self):
        source=b'import usb_hid\nimport board\nimport pwmio\nservo=pwmio.PWMOut(board.GP10)\nother=board.GP11\nbutton=board.GP7\ngreen=board.GP8\nset_arms(0)\n'
        evidence=inspect_firmware_source(source)
        self.assertTrue(evidence['hid_present'])
        self.assertTrue(evidence['servo_pins_present'])
        self.assertTrue(evidence['boot_arm_call_present'])
        self.assertFalse(evidence['dedicated_data_import_present'])
        self.assertFalse(evidence['protocol_verified'])
        self.assertIn('arm_boot_motion_present',arm_readiness(firmware_evidence=evidence)['reasons'])

    def test_malformed_or_overlarge_firmware_evidence_rejected(self):
        for value in (b'', b'bad(', b'x'*131073, 'source'):
            with self.subTest(size=len(value)), self.assertRaises(FeatureError):
                inspect_firmware_source(value)

    def test_checklist_requires_literal_booleans(self):
        with self.assertRaises(FeatureError):
            ArmSafetyChecklist(user_present=1)

    def test_admission_module_has_no_transport_import_or_io_calls(self):
        path=Path(__file__).parents[1]/'bmo_ai/features/motion/admission.py'
        tree=ast.parse(path.read_text())
        forbidden={'serial','usb_cdc','pwmio','subprocess','os','socket','importlib'}
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                self.assertFalse({name.name.split('.')[0] for name in node.names}&forbidden)
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                self.assertNotIn(node.func.id,{'open','exec','eval','compile','__import__'})


if __name__ == '__main__':
    unittest.main()
