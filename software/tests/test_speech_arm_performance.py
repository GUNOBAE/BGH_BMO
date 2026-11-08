"""Production B1 speech controller + reviewed v3 scheduler with injected IO only."""
import hashlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmo_ai.arm_candidate import candidate_transition, ArmCandidateError
from bmo_ai.features.motion.legacy_host import LegacyArmLink, LegacyMotionFeature, validate_legacy_install
from test_legacy_arm_host import Clock, FakePort, core as v1
from test_arm_recovery import core as v2
import test_arm_recovery as recovery

ROOT = Path(__file__).resolve().parents[2]/'firmware/staging/bmo_arm_legacy_bridge_v3'
spec = importlib.util.spec_from_file_location('speech_b1_fixture', ROOT/'bmo_arm_bridge.py')
core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)


class SpeechArmTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock(); self.moves=[]
        self.board=core.LegacyBridge(None,'bootA',
            lambda side,value:self.moves.append((self.clock(),side,value)),
            lambda value:self.moves.append((self.clock(),'both',value)),lambda:0,clock=self.clock)
        self.board.enabled=True
        self.port=FakePort(self.board)
        self.link=LegacyArmLink(self.port,motor_permission=lambda:True)
        self.motion=LegacyMotionFeature(self.link,clock=self.clock,cooldown=0)
        self.motion.set_context('session','turn1',1)

    def start(self,gesture='wave',context='greeting',turn='turn1',request='speech1'):
        return self.motion.start_performance(gesture,performance_context=context,request_id=request,
            session_id='session',turn_id=turn,mode_generation=1,started_at=self.clock())

    def step(self,seconds):
        for _ in range(round(seconds/.02)):
            self.clock.advance(.02);self.board._tick_steps();self.motion.poll()

    def verbs(self):return [raw.split()[1] for raw in self.port.sent]

    def test_greeting_one_small_raise_hold_and_utterance_end_returns_without_other_do(self):
        self.assertEqual(self.start().code,'performance_queued')
        self.assertEqual(self.moves,[]);self.motion.poll();self.step(.6)
        self.assertEqual(self.board.positions,{'left':0,'right':45})
        self.assertEqual(self.motion.snapshot()['performance']['phase'],'holding')
        before=len(self.port.sent)
        self.assertTrue(self.motion.finish_performance('turn1','playback_ended'))
        self.assertEqual(len(self.port.sent),before)  # enqueue only, no arrival wait
        self.motion.poll();self.step(.65)
        self.assertEqual(self.board.positions,{'left':0,'right':0})
        self.assertFalse(self.motion.snapshot()['active'])
        self.assertEqual(self.motion.snapshot()['performance']['code'],'performance_return_commanded')
        self.assertFalse(self.motion.snapshot()['position_verified'])
        self.assertEqual(self.verbs().count(b'ACT'),1);self.assertNotIn(b'DO',self.verbs())
        self.assertLessEqual(max(value for _,_,value in self.moves),45)

    def test_surprise_has_bounded_hold_and_returns_even_if_reply_never_ends(self):
        self.start('shrug','surprise');self.motion.poll();self.step(3.)
        self.assertEqual(self.board.positions,{'left':0,'right':0})
        self.assertFalse(self.board.stopped)
        self.assertFalse(self.motion.snapshot()['active'])
        self.assertLessEqual(max(value for _,_,value in self.moves),30)
        self.assertEqual(self.verbs().count(b'ACT'),1)
        self.assertNotIn(b'END',self.verbs())  # firmware normal finite hold, not watchdog

    def test_cancel_during_entry_atomically_replaces_entry_and_new_turn_does_not_drop_return(self):
        self.start();self.motion.poll();self.step(.12)
        height=self.board.positions['right']
        self.motion.set_context('session','turn2',1)
        self.motion.poll();self.step(.4)
        self.assertLessEqual(max(value for _,_,value in self.moves),height)
        self.assertEqual(self.board.positions['right'],0)
        self.assertEqual(self.motion.snapshot()['performance']['finish_reason'],'cancel')
        self.assertFalse(self.motion.snapshot()['active'])
        self.assertEqual(self.start('shrug','surprise','turn2','speech2').status,'accepted')
        self.motion.poll();before=len(self.port.sent)
        self.assertFalse(self.motion.finish_performance('turn1','playback_ended'))
        self.assertEqual(len(self.port.sent),before)
        self.step(.4)
        self.assertEqual(self.board.positions,{'left':30,'right':30})

    def test_stop_overrides_queued_return_and_no_poststop_return_is_emitted(self):
        self.start();self.motion.poll();self.step(.2)
        self.motion.finish_performance(reason='cancel')
        self.motion.stop();position=dict(self.board.positions);count=len(self.moves)
        self.step(1.)
        self.assertTrue(self.board.stopped)
        self.assertEqual(self.board.positions,position);self.assertEqual(len(self.moves),count)
        self.assertNotIn(b'END',self.verbs());self.assertIn(b'STOP',self.verbs())

    def test_gp7_fault_and_unknown_connection_never_return_automatically(self):
        for cause in ('manual','servo','unknown'):
            with self.subTest(cause=cause):
                self.setUp();self.start();self.motion.poll();self.step(.2)
                if cause=='manual':self.board.manual_cancel();self.board.manual_pose(180)
                elif cause=='servo':self.board.fault_remote('servo_fault')
                else:self.link.unknown=True
                before=len(self.moves)
                self.motion.finish_performance(reason='cancel');self.motion.poll();self.step(.3)
                if cause=='unknown':
                    # An unplugged/unknown link cannot send HOLD. The board may
                    # finish bounded entry until its unchanged watchdog holds;
                    # the host must never enqueue a return or reconnect it.
                    self.step(.7)
                    self.assertTrue(self.board.stopped)
                    self.assertNotIn(b'END',self.verbs())
                    self.assertGreaterEqual(self.board.positions['right'],18)
                else:
                    self.assertEqual(len(self.moves),before)
                self.assertFalse(self.motion.snapshot()['active'])
                self.assertNotIn(b'PREPARE',self.verbs())

    def test_watchdog_holds_and_normal_finish_cannot_restart_motion(self):
        self.start();self.motion.poll();self.step(.2)
        self.clock.advance(1.);self.board._tick_steps();before=list(self.moves)
        self.motion.finish_performance(reason='playback_ended');self.motion.poll()
        self.assertTrue(self.board.stopped);self.assertEqual(self.moves,before)
        self.assertNotIn(b'PREPARE',self.verbs())

    def test_old_firmware_is_reported_without_poisoning_explicit_named_commands(self):
        for module in (v1,v2):
            with self.subTest(module=module.__name__):
                board=module.LegacyBridge(None,'bootA',lambda *_:None,lambda *_:None,lambda:0)
                board.enabled=True;port=FakePort(board)
                motion=LegacyMotionFeature(LegacyArmLink(port,motor_permission=lambda:True),clock=self.clock,cooldown=0)
                motion.set_context('s','t',1)
                motion.start_performance('wave',performance_context='greeting',request_id='auto',
                    session_id='s',turn_id='t',mode_generation=1,started_at=self.clock())
                events=motion.poll()
                self.assertEqual(events[-1].code,'arm_performance_firmware_required')
                self.assertFalse(motion.snapshot()['performance_supported'])
                self.assertIsNone(motion.fault)
                result=motion.submit('raise_left_arm',request_id='manual',session_id='s',turn_id='t',
                    mode_generation=1,started_at=None,explicit=True)
                self.assertEqual(result.status,'accepted');motion.poll()
                self.assertEqual(sum(b' DO ' in raw for raw in port.sent),1)

    def test_cancel_during_start_ack_returns_old_token_without_late_raise(self):
        original=self.port.read_until
        def read(*args):
            reply=original(*args)
            if b'PERFORM_ENTER' in reply and self.motion.performance['phase']=='starting':
                self.motion.set_context('session','turn2',1)
            return reply
        self.port.read_until=read
        self.start();self.motion.poll();self.step(.2)
        self.assertEqual(self.moves,[])
        self.assertEqual(self.verbs().count(b'ACT'),1);self.assertEqual(self.verbs().count(b'END'),1)
        self.assertFalse(self.motion.snapshot()['active'])

    def test_stop_during_start_ack_never_resurrects_token_or_sends_return(self):
        original=self.port.read_until
        def read(*args):
            reply=original(*args)
            if b'PERFORM_ENTER' in reply and self.motion.performance is not None:
                self.motion.stop()
            return reply
        self.port.read_until=read
        self.start();self.motion.poll();self.step(.4)
        self.assertTrue(self.board.stopped)
        self.assertEqual(self.moves,[])
        self.assertFalse(self.motion.snapshot()['active'])
        self.assertNotIn(b'END',self.verbs())

    def test_high_user_pose_is_not_moved_down_to_make_room_for_decoration(self):
        self.board.positions={'left':180,'right':0}
        self.start();events=self.motion.poll()
        self.assertEqual(events[-1].code,'performance_pose_unsuitable')
        self.assertEqual(self.moves,[]);self.assertEqual(self.board.positions,{'left':180,'right':0})
        self.assertIsNone(self.motion.fault)

    def test_late_release_token_and_evicted_start_cannot_control_new_performance(self):
        def send(text):return self.board.handle((text+'\n').encode()).decode()
        self.assertIn('PERFORM_ENTER',send('B1 ACT bmo_10 bootA greeting'))
        self.assertIn('PERFORM_RETURN',send('B1 END bmo_20 bootA bmo_10'))
        self.clock.advance(.02);self.board._tick_steps()
        self.assertIn('PERFORM_ENTER',send('B1 ACT bmo_30 bootA surprise'))
        self.assertIn('STALE_PERFORMANCE',send('B1 END bmo_31 bootA bmo_10'))
        for i in range(40,70):send('B1 PING bmo_'+str(i)+' bootA')
        self.assertIn('STALE_REQUEST',send('B1 ACT bmo_10 bootA greeting'))
        self.assertEqual(self.board.performance['profile'],'surprise')
        self.board.manual_cancel()
        self.assertIn('STOPPED',send('B1 ACT bmo_30 bootA surprise'))

    def test_performance_candidate_preserves_all_pin_calibration_and_manual_code(self):
        old=ROOT.with_name('bmo_arm_legacy_bridge_v1')
        self.assertEqual((ROOT/'code.py').read_bytes().split(b'\n',1)[1],(old/'code.py').read_bytes())
        self.assertEqual((ROOT/'original_code.py').read_bytes(),(old/'original_code.py').read_bytes())
        self.assertEqual(core.MAX_SLEW_DPS,v2.MAX_SLEW_DPS)
        self.assertEqual(core.MAX_UPDATE_DEG,v2.MAX_UPDATE_DEG)


class CandidateTests(unittest.TestCase):
    def test_reviewed_migration_from_v1_or_v2_retains_exact_backup_and_rollback(self):
        for version in (1,2):
            with self.subTest(version=version),TemporaryDirectory() as directory:
                root=Path(directory);mount=root/'board';mount.mkdir(mode=0o700)
                old=ROOT.with_name('bmo_arm_legacy_bridge_v'+str(version))
                for name in ('code.py','bmo_arm_bridge.py','boot.py'):
                    data=(old/name).read_bytes()
                    if name=='code.py':data=data.replace(b'REMOTE_ENABLED = False',b'REMOTE_ENABLED = True',1)
                    (mount/name).write_bytes(data)
                before={p.name:p.read_bytes() for p in mount.iterdir()}
                work=root/'review'
                report=candidate_transition(speech_performance=True,mount=mount)
                self.assertFalse(report['board_write_performed']);self.assertEqual(report['previous_protocol_version'],version)
                settings=dict(speech_performance=True,mount=mount,work_directory=work,
                    reviewed_sha=report['enabled_code_sha256'],reviewed_helper_sha=report['enabled_helper_sha256'])
                candidate_transition(action='prepare',**settings)
                with self.assertRaises(ArmCandidateError):candidate_transition(action='apply',**settings)
                flags=dict(user_present=True,servo_power_isolated=True,clearance_verified=True,
                    physical_cutoff_ready=True,reload_risk_acknowledged=True)
                result=candidate_transition(action='apply',**settings,**flags)
                self.assertFalse(result['physical_verified'])
                validate_legacy_install(ROOT.with_name('bmo_arm_legacy_bridge_v1'),settings['reviewed_sha'],mount)
                candidate_transition(action='rollback',**settings,**flags)
                self.assertEqual({p.name:p.read_bytes() for p in mount.iterdir()},before)

    def test_partial_apply_retains_reviewed_backup_for_explicit_rollback(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);mount=root/'board';mount.mkdir(mode=0o700)
            old=ROOT.with_name('bmo_arm_legacy_bridge_v1')
            for name in ('code.py','bmo_arm_bridge.py','boot.py'):
                data=(old/name).read_bytes()
                if name=='code.py':data=data.replace(b'REMOTE_ENABLED = False',b'REMOTE_ENABLED = True',1)
                (mount/name).write_bytes(data)
            before={p.name:p.read_bytes() for p in mount.iterdir()}
            report=candidate_transition(speech_performance=True,mount=mount)
            settings=dict(speech_performance=True,mount=mount,work_directory=root/'review',
                reviewed_sha=report['enabled_code_sha256'],reviewed_helper_sha=report['enabled_helper_sha256'])
            candidate_transition(action='prepare',**settings)
            flags=dict(user_present=True,servo_power_isolated=True,clearance_verified=True,
                physical_cutoff_ready=True,reload_risk_acknowledged=True)
            import bmo_ai.arm_performance_candidate as candidate
            replace=candidate._replace
            def fail_second(mount,name,current,new):
                if name=='code.py':raise ArmCandidateError('injected_write_failure')
                replace(mount,name,current,new)
            with patch.object(candidate,'_replace',side_effect=fail_second):
                with self.assertRaisesRegex(ArmCandidateError,'injected_write_failure'):
                    candidate_transition(action='apply',**settings,**flags)
            self.assertNotEqual((mount/'bmo_arm_bridge.py').read_bytes(),before['bmo_arm_bridge.py'])
            self.assertEqual((mount/'code.py').read_bytes(),before['code.py'])
            candidate_transition(action='rollback',**settings,**flags)
            self.assertEqual({p.name:p.read_bytes() for p in mount.iterdir()},before)


class V3RecoveryTests(recovery.RecoveryTests):
    def setUp(self):
        self.enterContext(patch.object(recovery,'core',core))
        self.enterContext(patch.object(recovery,'ROOT',ROOT))
        super().setUp()

class V3ButtonTests(recovery.RecoveryButtonTests):
    def setUp(self):
        self.enterContext(patch.object(recovery,'ROOT',ROOT))
        super().setUp()
