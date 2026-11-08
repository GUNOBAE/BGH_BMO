"""Explicit B1 preparation: actual protocol, fake byte transport, no board IO."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.motion.legacy_host import LegacyArmError, LegacyArmLink, LegacyMotionFeature
from test_legacy_arm_host import Clock, FakePort, core as v1
import test_daily_arm_usage as daily_fixture
import test_b1_button_regression as button_fixture
import test_legacy_arm_bridge as bridge_fixture

ROOT = Path(__file__).resolve().parents[2] / 'firmware/staging/bmo_arm_legacy_bridge_v2'
spec = importlib.util.spec_from_file_location('b1_recovery_fixture', ROOT/'bmo_arm_bridge.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.moves = []
        self.board = core.LegacyBridge(None, 'bootA',
            lambda side, value:self.moves.append((side, value)),
            lambda value:self.moves.append(('both', value)), lambda:0,
            clock=self.clock)
        self.board.enabled = True
        self.port = FakePort(self.board)
        self.link = LegacyArmLink(self.port, motor_permission=lambda:True)
        self.motion = LegacyMotionFeature(self.link, clock=self.clock, cooldown=0)

    def send(self, line): return self.board.handle(line.encode()).decode()

    def gesture(self, request='wave1'):
        self.motion.set_context('session', 'turn', 1)
        result = self.motion.submit('wave', request_id=request, session_id='session', turn_id='turn',
            mode_generation=1, started_at=self.clock(), explicit=True)
        self.motion.poll()
        return result

    def test_prepare_after_stop_has_no_motion_no_pending_replay_and_new_request_works(self):
        self.gesture()
        self.motion.stop()
        before = len(self.port.sent)
        self.motion.prepare()
        self.assertEqual([raw.split()[1] for raw in self.port.sent[before:]], [b'HELLO', b'STATUS', b'PREPARE'])
        self.assertFalse(self.board.stopped)
        self.assertEqual(self.moves, [])
        self.assertEqual(self.board.steps, ())
        self.assertIsNone(self.motion.pending)
        self.assertEqual(self.gesture('wave1').status, 'rejected')
        self.assertEqual(self.gesture('wave2').status, 'accepted')
        self.assertEqual(sum(b' DO ' in raw for raw in self.port.sent), 2)

    def test_relaunch_observes_stop_then_explicit_prepare_recovers(self):
        self.gesture(); self.motion.close()
        other = FakePort(self.board)
        link = LegacyArmLink(other, motor_permission=lambda:True)
        with self.assertRaisesRegex(LegacyArmError, 'firmware_not_ready_stopped'):
            link.hello()
        self.assertTrue(self.board.stopped)
        self.assertEqual(link.prepare(), 'PREPARED')
        self.assertEqual(self.moves, [])
        self.assertEqual(self.board.steps, ())

    def test_gp7_between_status_and_prepare_invalidates_permission(self):
        self.board.manual_cancel()
        self.assertIn('RECOVERABLE', self.send('B1 STATUS bmo_100 bootA\n'))
        self.board.manual_cancel(); self.board.manual_pose(180)
        self.assertIn('DENIED', self.send('B1 PREPARE bmo_101 bootA bmo_100\n'))
        self.assertTrue(self.board.stopped)
        self.assertEqual(self.moves, [])

    def test_expired_token_wrong_boot_and_old_motion_are_rejected(self):
        self.board.manual_cancel()
        self.send('B1 STATUS bmo_100 bootA\n'); self.clock.advance(2.01)
        self.assertIn('DENIED', self.send('B1 PREPARE bmo_101 bootA bmo_100\n'))
        self.send('B1 STATUS bmo_102 bootA\n')
        self.assertIn('STALE_BOOT', self.send('B1 PREPARE bmo_103 wrongboot bmo_102\n'))
        self.assertIn('PREPARED', self.send('B1 PREPARE bmo_104 bootA bmo_102\n'))
        self.assertIn('STALE_REQUEST', self.send('B1 DO bmo_099 bootA wave\n'))
        self.assertEqual(self.moves, [])
        self.assertEqual(self.board.steps, ())

    def test_delayed_prepare_retry_after_stop_cannot_claim_or_release_ready(self):
        self.board.manual_cancel()
        self.send('B1 STATUS bmo_100 bootA\n')
        packet = 'B1 PREPARE bmo_101 bootA bmo_100\n'
        self.assertIn('PREPARED', self.send(packet))
        self.board.manual_cancel()
        self.assertIn('STOPPED', self.send(packet))
        self.assertTrue(self.board.stopped)
        self.assertEqual(self.moves, [])

    def test_watchdog_requires_explicit_prepare_and_keeps_last_commanded_pose(self):
        self.gesture(); self.clock.advance(.02); self.board._tick_steps()
        previous = dict(self.board.positions)
        self.clock.advance(1); self.board._tick_steps()
        self.assertTrue(self.board.stopped)
        self.assertEqual(self.board.positions, previous)
        self.motion.poll()  # observes stopped ACK, no retry or automatic prepare
        before = list(self.moves)
        self.motion.prepare()
        self.assertEqual(self.moves, before)
        self.assertEqual(self.board.positions, previous)
        self.assertEqual(self.board.steps, ())

    def test_hardware_fault_does_not_become_recoverable(self):
        self.board.fault_remote('servo_fault')
        with self.assertRaises(FeatureError) as caught:
            self.motion.prepare()
        self.assertEqual(caught.exception.code, 'arm_prepare_fault_requires_review')
        self.assertTrue(self.board.stopped)
        self.assertEqual(self.moves, [])
        self.assertFalse(any(b' PREPARE ' in raw for raw in self.port.sent))

    def test_unknown_commanded_pose_cannot_prepare_even_without_fault_code(self):
        self.board.manual_cancel(); self.board.positions = None
        with self.assertRaises(FeatureError) as caught:
            self.motion.prepare()
        self.assertEqual(caught.exception.code, 'arm_prepare_fault_requires_review')
        self.assertTrue(self.board.stopped)
        self.assertEqual(self.moves, [])
        self.assertFalse(any(b' PREPARE ' in raw for raw in self.port.sent))

    def test_busy_permission_or_unknown_transport_never_prepares(self):
        self.gesture()
        with self.assertRaises(FeatureError) as caught:
            self.motion.prepare()
        self.assertEqual(caught.exception.code, 'arm_prepare_busy_or_closed')
        self.motion.stop(); self.link.motor_permission = lambda:False
        before = len(self.port.sent)
        with self.assertRaises(FeatureError) as caught:
            self.motion.prepare()
        self.assertEqual(caught.exception.code, 'physical_supervision_required')
        self.assertEqual(len(self.port.sent), before)
        self.link.motor_permission = lambda:True
        self.link.unknown = True; self.link.last_response_code = None
        with self.assertRaises(FeatureError) as caught:
            self.motion.prepare()
        self.assertEqual(caught.exception.code, 'arm_prepare_connection_unverified')
        self.assertEqual(len(self.port.sent), before)

    def test_v1_reports_firmware_required_without_clearing_latch(self):
        board = v1.LegacyBridge(None, 'bootA', lambda *_:None, lambda *_:None, lambda:0)
        board.enabled = True; board.manual_cancel()
        port = FakePort(board); link = LegacyArmLink(port, motor_permission=lambda:True)
        with self.assertRaisesRegex(LegacyArmError, 'arm_prepare_firmware_required'):
            link.prepare()
        self.assertTrue(board.stopped)
        self.assertFalse(any(b' PREPARE ' in raw for raw in port.sent))

    def test_supervised_host_prepare_is_deduplicated_and_only_new_request_moves(self):
        from tempfile import TemporaryDirectory
        from bmo_ai.completed_turn_diagnostics import CompletedTurnStore
        from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
        from bmo_ai.features.runtime import RuntimePolicy
        from bmo_ai.features.reply import HostIntent
        from bmo_ai.integration_profile import integration_config
        from bmo_ai.integration_voice import IntegrationVoice
        with TemporaryDirectory() as directory:
            config = integration_config(); client = FeatureSimulatedClient()
            store = CompletedTurnStore(Path(directory)/'turns')
            voice = IntegrationVoice(config, client, simulate=True, character_life=True,
                                     emit=lambda *_a, **_k:None)
            host = FeatureHost(config=config, client=client, voice=voice, motion=self.motion,
                runtime=RuntimePolicy(simulate=False, arms_supervised=True), clock=self.clock,
                character_life=True, completed_turn_store=store)
            try:
                first = host.execute(HostIntent('arm_gesture', 'wave'), request_id='menu_wave')
                self.assertEqual(first.status, 'accepted'); host.poll()
                host.execute(HostIntent('stop_arms', None), request_id='menu_stop'); host.poll()
                self.assertTrue(self.board.stopped)
                result = host.control(HostControl('arm_prepare'), request='menu_prepare')
                self.assertEqual(result.status, 'succeeded')
                self.assertEqual(result.completion, 'protocol_ready_position_unverified')
                after = len(self.port.sent)
                host.control(HostControl('arm_prepare'), request='menu_prepare')
                self.assertEqual(len(self.port.sent), after)
                self.assertEqual(sum(b' DO ' in raw for raw in self.port.sent), 1)
                self.assertEqual(self.board.steps, ())
                second = host.execute(HostIntent('arm_gesture', 'both_down'), request_id='menu_after_prepare')
                self.assertEqual(second.status, 'accepted'); host.poll()
                self.assertEqual(sum(b' DO ' in raw for raw in self.port.sent), 2)
            finally:
                host.close(); store.close()

    def test_candidate_preserves_original_code_calibration_and_buttons(self):
        old = ROOT.with_name('bmo_arm_legacy_bridge_v1')
        self.assertEqual((ROOT/'code.py').read_bytes().split(b'\n',1)[1], (old/'code.py').read_bytes())
        self.assertEqual((ROOT/'original_code.py').read_bytes(), (old/'original_code.py').read_bytes())
        self.assertEqual((ROOT/'boot.py').read_bytes(), (old/'boot.py').read_bytes())


class DailyRecoveryTests(unittest.TestCase):
    setUp = daily_fixture.DailyArmUsageTests.setUp
    record = daily_fixture.DailyArmUsageTests.record
    controller = daily_fixture.DailyArmUsageTests.controller
    submit = daily_fixture.DailyArmUsageTests.submit

    def test_v2_receipt_stop_prepare_and_off_are_explicit(self):
        enabled = (ROOT/'code.py').read_bytes().replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
        (self.mount/'code.py').write_bytes(enabled)
        (self.mount/'bmo_arm_bridge.py').write_bytes((ROOT/'bmo_arm_bridge.py').read_bytes())
        self.sha = hashlib.sha256(enabled).hexdigest()
        self.record()  # Exact v2 SHA resolves only its matching helper, v1 unchanged.
        self.store.preferences(dict(enabled=True, conversation_gestures=True))
        board = core.LegacyBridge(None, 'bootA', lambda *_:None, lambda *_:None, lambda:0)
        board.enabled = True
        port = FakePort(board); port.open = lambda:port
        controller, _ = self.controller(port=port)
        self.submit(controller)
        controller.configure(dict(enabled=False, conversation_gestures=True))
        with self.assertRaises(FeatureError) as caught: controller.prepare()
        self.assertEqual(caught.exception.code, 'arm_usage_off')
        controller.configure(dict(enabled=True, conversation_gestures=True))
        self.assertTrue(board.stopped)
        state = controller.prepare()
        self.assertEqual(state['state'], 'ready')
        self.assertFalse(state['physical_verified'])
        self.assertTrue(controller.conversation_gestures)
        self.assertEqual(board.steps, ())


class RecoveryButtonTests(button_fixture.B1ButtonRegressionTests):
    def setUp(self):
        from unittest.mock import patch
        self.enterContext(patch.object(button_fixture, 'FIRMWARE', ROOT))


class RecoveryExistingProtocolTests(bridge_fixture.TestLegacyBridge):
    def setUp(self):
        from unittest.mock import patch
        self.enterContext(patch.object(bridge_fixture, 'module', core))
        self.enterContext(patch.object(bridge_fixture, 'LegacyBridge', core.LegacyBridge))
        super().setUp()


class RecoveryCandidateTests(unittest.TestCase):
    def setUp(self):
        import test_arm_candidate as fixture
        fixture.ArmCandidateTests.setUp(self)
        (self.mount/'code.py').write_bytes(self.enabled)
        self.before['code.py'] = self.enabled

    def invoke(self, action='dry_run', **changes):
        from bmo_ai.arm_candidate import candidate_transition
        options = dict(action=action, mount=self.mount, work_directory=self.work,
                       recovery_protocol=True)
        options.update(changes)
        return candidate_transition(**options)

    def test_read_only_prepare_reviewed_apply_and_exact_rollback(self):
        report = self.invoke()
        self.assertFalse(self.work.exists())
        self.assertFalse(report['board_write_performed'])
        prepared = self.invoke('prepare')
        self.assertFalse(prepared['candidate_reviewed'])
        kwargs = dict(reviewed_sha=report['enabled_code_sha256'],
                      reviewed_helper_sha=report['enabled_helper_sha256'])
        from bmo_ai.arm_candidate import ArmCandidateError
        with self.assertRaisesRegex(ArmCandidateError, 'current_supervised_conditions_required'):
            self.invoke('apply', **kwargs)
        for missing in self.conditions:
            with self.subTest(missing=missing), self.assertRaises(ArmCandidateError):
                self.invoke('apply', **kwargs, **dict(self.conditions, **{missing:False}))
        with self.assertRaisesRegex(ArmCandidateError, 'exact_reviewed_code_and_helper_sha_required'):
            self.invoke('apply', reviewed_sha=kwargs['reviewed_sha'], **self.conditions)
        applied = self.invoke('apply', **kwargs, **self.conditions)
        self.assertEqual(applied['changed_files'], ['bmo_arm_bridge.py', 'code.py'])
        self.assertFalse(applied['physical_verified'])
        self.assertEqual((self.mount/'boot.py').read_bytes(), self.before['boot.py'])
        from bmo_ai.features.motion.legacy_host import validate_legacy_install
        self.assertEqual(validate_legacy_install(ROOT.with_name('bmo_arm_legacy_bridge_v1'),
            kwargs['reviewed_sha'], mount=self.mount), kwargs['reviewed_sha'])
        rolled = self.invoke('rollback', **kwargs, **self.conditions)
        self.assertEqual(rolled['status'], 'STATIC_ROLLED_BACK')
        self.assertEqual({key:(self.mount/key).read_bytes() for key in self.before}, self.before)
        self.assertTrue((self.work/'backup-v1/SHA256.json').exists())

    def test_partial_two_file_apply_has_explicit_verified_rollback(self):
        from unittest.mock import patch
        from bmo_ai import arm_recovery_candidate as recovery
        report = self.invoke('prepare')
        kwargs = dict(reviewed_sha=report['enabled_code_sha256'],
                      reviewed_helper_sha=report['enabled_helper_sha256'], **self.conditions)
        real_replace = recovery._replace
        def fail_code(mount, name, expected, replacement):
            if name == 'code.py': raise OSError('injected write interruption')
            return real_replace(mount, name, expected, replacement)
        with patch.object(recovery, '_replace', side_effect=fail_code), self.assertRaises(OSError):
            self.invoke('apply', **kwargs)
        self.assertNotEqual((self.mount/'bmo_arm_bridge.py').read_bytes(), self.before['bmo_arm_bridge.py'])
        self.assertEqual((self.mount/'code.py').read_bytes(), self.before['code.py'])
        self.assertEqual(self.invoke('rollback', **kwargs)['status'], 'STATIC_ROLLED_BACK')
        self.assertEqual({key:(self.mount/key).read_bytes() for key in self.before}, self.before)

    def test_prepare_hash_changes_cannot_bypass_review(self):
        from bmo_ai.arm_candidate import ArmCandidateError
        report = self.invoke('prepare')
        (self.work/'bmo_arm_bridge.py').write_bytes(b'unreviewed')
        with self.assertRaisesRegex(ArmCandidateError, 'candidate_changed'):
            self.invoke('apply', reviewed_sha=report['enabled_code_sha256'],
                reviewed_helper_sha=report['enabled_helper_sha256'], **self.conditions)
        self.assertFalse((self.work/'backup-v1').exists())
        self.assertEqual({key:(self.mount/key).read_bytes() for key in self.before}, self.before)


if __name__ == '__main__': unittest.main()
