"""B1 readiness describes injected software/ACK evidence, never physical safety."""
import unittest
from unittest.mock import patch

from bmo_ai.features.motion.admission import arm_readiness
from bmo_ai.features.motion.legacy_host import LegacyArmLink, LegacyMotionFeature
from test_legacy_arm_host import Clock, FakePort, core


class B1ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('os.open', side_effect=AssertionError('device forbidden')))
        self.clock, self.moves = Clock(), []
        self.controller = core.LegacyBridge(None, 'readinessBoot',
            lambda *args: self.moves.append(args), lambda *args: self.moves.append(args),
            lambda: 0, enabled=True, clock=self.clock)
        self.controller.enabled = True  # Pure controller fixture; no device/PWM.
        self.port = FakePort(self.controller)
        self.link = LegacyArmLink(self.port, motor_permission=lambda: True,
                                  sequence_clock=lambda: 1000)
        self.motion = LegacyMotionFeature(self.link, clock=self.clock, cooldown=0)
        self.motion.set_context('session', 'turn', 1)

    def queue(self, request='gesture'):
        return self.motion.submit('wave', request_id=request, session_id='session',
            turn_id='turn', mode_generation=1, started_at=self.clock())

    def test_snapshot_before_hello_cannot_claim_ready_or_run_permission(self):
        self.link.motor_permission = lambda: self.fail('snapshot evaluated physical permission')
        state = self.motion.snapshot()
        self.assertEqual(state['readiness'], 'UNVERIFIED')
        self.assertEqual(state['reason_code'], 'hello_required')
        self.assertEqual(state['preflight']['status'], 'CONFIGURED')
        self.assertEqual(state['preflight']['scope'], 'software_adapter')
        self.assertEqual(state['transport']['status'], 'NOT_CONNECTED')
        self.assertFalse(state['transport']['usable'])
        self.assertIsNone(state['transport']['last_response_code'])
        self.assertFalse(state['physical_verified'])
        self.assertFalse(self.port.sent)
        self.assertFalse(self.moves)

    def test_exact_ready_ack_is_software_transport_evidence_only(self):
        self.link.hello()
        state = self.motion.snapshot()
        self.assertEqual(state['readiness'], 'READY')
        self.assertEqual(state['transport']['status'], 'ACKNOWLEDGED')
        self.assertEqual(state['transport']['last_response_code'], 'READY')
        self.assertTrue(state['transport']['usable'])
        self.assertFalse(state['physical_verified'])
        self.assertFalse(state['position_verified'])
        self.assertEqual(arm_readiness()['readiness'], 'BLOCKED')
        self.assertIn('arm_live_protocol_unimplemented', arm_readiness()['reasons'])
        self.assertEqual(len(self.port.sent), 1)
        self.assertFalse(self.moves)

    def test_disabled_ack_remains_fail_closed_without_any_do_or_auto_retry(self):
        self.controller.enabled = False
        self.assertEqual(self.queue().status, 'accepted')
        event, = self.motion.poll()
        state = self.motion.snapshot()
        self.assertEqual(event.code, 'arm_link_unknown')
        self.assertEqual(state['readiness'], 'UNVERIFIED')
        self.assertEqual(state['last_error_code'], 'firmware_not_ready_disabled')
        self.assertEqual(state['preflight']['status'], 'BLOCKED')
        self.assertEqual(state['transport']['status'], 'UNKNOWN')
        self.assertEqual(state['transport']['last_response_code'], 'DISABLED')
        self.assertFalse(state['transport']['usable'])
        self.assertFalse(state['physical_verified'])
        self.assertEqual(len(self.port.sent), 1)
        self.assertFalse(self.controller.enabled)
        self.assertEqual(self.motion.poll(), ())
        self.assertEqual(len(self.port.sent), 1)
        self.assertFalse(self.moves)

    def test_stop_close_and_duplicate_cannot_display_ready_or_restart(self):
        self.assertEqual(self.queue().status, 'accepted')
        self.motion.poll()
        self.motion.cancel()
        self.assertEqual(self.queue().status, 'rejected')
        self.assertFalse(self.controller.steps)
        self.assertEqual(sum(frame.startswith(b'B1 DO ') for frame in self.port.sent), 1)
        self.motion.stop()
        state = self.motion.snapshot()
        self.assertEqual(state['readiness'], 'BLOCKED')
        self.assertEqual(state['reason_code'], 'arm_stopped')
        self.assertEqual(state['preflight']['status'], 'BLOCKED')
        self.assertEqual(state['transport']['status'], 'STOPPED')
        self.assertEqual(state['transport']['last_response_code'], 'STOPPED')
        self.assertFalse(state['transport']['usable'])
        self.assertFalse(state['physical_verified'])
        writes = len(self.port.sent)
        self.motion.stop()
        self.motion.poll()
        self.assertEqual(len(self.port.sent), writes)
        self.motion.close()
        state = self.motion.snapshot()
        self.assertEqual(state['readiness'], 'BLOCKED')
        self.assertEqual(state['reason_code'], 'arm_closed')
        self.assertEqual(state['transport']['status'], 'CLOSED')
        self.assertFalse(state['transport']['usable'])
        self.assertFalse(state['physical_verified'])
        self.assertFalse(self.moves)

    def test_bad_reply_clears_previous_ack_evidence_without_retry(self):
        self.link.hello()
        self.port.read_until = lambda *_args: b'bad reply\n'
        self.assertEqual(self.queue().status, 'accepted')
        self.motion.poll()
        state = self.motion.snapshot()
        self.assertEqual(state['transport']['status'], 'UNKNOWN')
        self.assertIsNone(state['transport']['last_response_code'])
        self.assertFalse(state['transport']['usable'])
        count = len(self.port.sent)
        self.motion.poll()
        self.assertEqual(len(self.port.sent), count)
        self.assertFalse(self.moves)


if __name__ == '__main__':
    unittest.main()
