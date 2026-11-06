"""Execute the real PTT/voice/Host/B1 bridge path with injected device boundaries."""
import importlib.util
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostIntent, HostControl
from bmo_ai.features.input.microphone import SyntheticPcmSource
from bmo_ai.features.motion.legacy_host import LegacyArmLink, LegacyMotionFeature
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("b1_desktop_bridge", ROOT /
    "firmware/staging/bmo_arm_legacy_bridge_v1/bmo_arm_bridge.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class Clock:
    def __init__(self):
        self.now = 0.
        self.sequence = 1000
    def __call__(self):
        return self.now
    def sequence_clock(self):
        self.sequence += 1
        return self.sequence


class Wire:
    """Actual B1 framing/ACK goes through the staged controller, without a port."""
    in_waiting = 0
    def __init__(self):
        self.sent, self.reply, self.controller = [], None, None
    def write(self, frame):
        self.sent.append(frame)
        self.reply = self.controller.handle(frame)
        return len(frame)
    def read_until(self, *_):
        return self.reply
    def close(self):
        pass


class B1DesktopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("device forbidden")))
        self.enterContext(patch("bmo_ai.cloud.create_client", side_effect=AssertionError("API forbidden")))
        self.enterContext(patch("bmo_ai.private_credentials.resolve_credentials", side_effect=AssertionError("keys forbidden")))
        self.clock, self.wire, self.moves = Clock(), Wire(), []
        self.controller = core.LegacyBridge(self.wire, "bootIntegration",
            lambda side, angle: self.moves.append((side, angle)),
            lambda angle: self.moves.append(("both", angle)), lambda: 0,
            enabled=True, clock=self.clock)
        self.wire.controller = self.controller
        self.link = LegacyArmLink(self.wire, motor_permission=lambda: True,
                                  sequence_clock=self.clock.sequence_clock)
        self.motion = LegacyMotionFeature(self.link, clock=self.clock)
        self.started, self.release = Event(), Event()
        self.stt_calls = 0

        def player(_command, *, on_start, cancel_check, **_):
            on_start()
            self.started.set()
            deadline = time.monotonic() + 3
            while not self.release.wait(.002):
                cancel_check()
                if time.monotonic() > deadline:
                    raise AssertionError("synthetic player gate timed out")
            cancel_check()

        config = integration_config()
        self.client = FeatureSimulatedClient([dict(version=1,
            speech="A synthetic happy reply.", emotion="happy", gesture="none", intent=None)])
        voice = IntegrationVoice(config, self.client, simulate=True,
            emit=lambda *_a, **_kw: None, player=player)
        def stt(*_a, **_kw):
            self.stt_calls += 1
            return "Synthetic GP8 question."
        self.host = FeatureHost(config=config, client=self.client, voice=voice,
            runtime=RuntimePolicy(simulate=False, conversation_ptt_microphone=True, arms_supervised=True),
            ptt_source_factory=lambda: SyntheticPcmSource(clock=self.clock), ptt_transcriber=stt,
            motion=self.motion, emotion_arms=True, clock=self.clock)

    def tearDown(self):
        self.release.set()
        if self.host.session.busy:
            self.host.cancel_conversation()
            self.pump(lambda: not self.host.session.busy)
        self.host.close()

    def pump(self, done):
        deadline = time.monotonic() + 4
        while not done() and time.monotonic() < deadline:
            self.host.heartbeat()
            self.host.poll()
            time.sleep(.002)
        self.assertTrue(done(), "injected B1 desktop condition timed out")

    def do_frames(self):
        return [f for f in self.wire.sent if f.startswith(b"B1 DO ")]

    def test_host_keeps_static_source_and_actual_adapter_evidence_separate(self):
        arms = self.host.snapshot()['system_diagnostics']['arms']
        self.assertEqual(arms['source']['readiness'], 'BLOCKED')
        self.assertEqual(arms['source']['scope'], 'legacy_static_admission')
        self.assertEqual(arms['adapter_readiness'], 'UNVERIFIED')
        self.assertEqual(arms['transport']['status'], 'NOT_CONNECTED')
        self.link.hello()
        self.host.poll()
        arms = self.host.snapshot()['system_diagnostics']['arms']
        self.assertEqual(arms['source']['readiness'], 'BLOCKED')
        self.assertEqual(arms['adapter_readiness'], 'READY')
        self.assertEqual(arms['preflight']['scope'], 'software_adapter')
        self.assertEqual(arms['transport']['status'], 'ACKNOWLEDGED')
        self.assertFalse(arms['physical_verified'])
        self.assertFalse(self.moves)

    def test_gp8_to_actual_speech_start_sends_one_emotion_b1_and_repeat_sends_none(self):
        self.assertTrue(self.host.ptt_button())
        self.assertTrue(self.started.wait(2))
        # Speaking callback alone only queues metadata, never does CDC I/O.
        self.assertEqual(self.do_frames(), [])
        self.pump(lambda: len(self.do_frames()) == 1)
        self.assertTrue(self.do_frames()[0].endswith(b" celebrate\n"))
        self.assertEqual((self.stt_calls, len(self.client.requests)), (1, 1))
        self.assertEqual(self.host.session.snapshot()["emotion"], "happy")
        for _ in range(10):
            self.clock.now += .02
            self.host.poll()
            self.controller.poll()
        self.assertTrue(self.moves)
        self.assertLessEqual(max(angle for _, angle in self.moves), 20)
        self.assertEqual(len(self.do_frames()), 1)
        self.assertFalse(self.host.camera.snapshot()["on"])
        self.release.set()
        self.pump(lambda: not self.host.session.busy)
        repeat = self.host.control(HostControl("repeat"), request="repeatIntegration")
        self.assertEqual(repeat.status, 'running')
        self.pump(lambda: not self.host.session.busy)
        self.assertEqual(len(self.do_frames()), 1)
        self.assertEqual(self.host.session.snapshot()["emotion"], "happy")

    def test_delayed_playback_event_never_reaches_b1(self):
        self.assertTrue(self.host.ptt_button())
        self.assertTrue(self.started.wait(2))
        self.clock.now = 2.
        self.host.poll()
        self.controller.poll()
        self.assertEqual(self.do_frames(), [])
        self.assertEqual(self.moves, [])

    def test_old_wire_command_after_real_host_heartbeats_cannot_restart_arms(self):
        self.assertTrue(self.host.ptt_button())
        self.assertTrue(self.started.wait(2))
        self.pump(lambda: len(self.do_frames()) == 1)
        original = self.do_frames()[0]
        self.motion.cancel()
        self.assertFalse(self.controller.steps)
        for _ in range(30):
            self.link.ping()
        before = list(self.moves)
        self.wire.write(original)
        self.assertNotIn(b" ACCEPTED ", self.wire.reply)
        self.clock.now += .02
        self.controller.poll()
        self.assertFalse(self.controller.steps)
        self.assertEqual(self.moves, before)

    def test_explicit_named_gesture_duplicate_cancel_stop_uses_one_host_without_voice(self):
        self.assertEqual(self.host.snapshot()['motion']['readiness'], 'UNVERIFIED')
        request = 'supervised_wave'
        result = self.host.execute(HostIntent('arm_gesture', 'wave'), request_id=request)
        self.assertEqual(result.status, 'accepted')
        self.assertEqual(self.wire.sent, [])
        self.pump(lambda: len(self.do_frames()) == 1)
        self.assertEqual(self.host.snapshot()['motion']['transport']['status'], 'ACKNOWLEDGED')
        self.assertFalse(self.host.snapshot()['motion']['physical_verified'])
        self.host.execute(HostIntent('arm_gesture', 'wave'), request_id=request)
        self.host.poll()
        self.assertEqual(len(self.do_frames()), 1)
        self.host.control(HostControl('cancel'), request='cancelSupervisedWave')
        self.host.poll()
        self.assertFalse(self.controller.steps)
        self.assertEqual(sum(frame.startswith(b'B1 HOLD ') for frame in self.wire.sent), 1)
        self.host.control(HostControl('stop'), request='stopSupervisedWave')
        self.host.poll()
        self.assertEqual(sum(frame.startswith(b'B1 STOP ') for frame in self.wire.sent), 1)
        self.assertEqual(self.host.snapshot()['motion']['readiness'], 'BLOCKED')
        self.assertEqual(self.host.snapshot()['motion']['transport']['status'], 'STOPPED')
        self.host.execute(HostIntent('arm_gesture', 'wave'), request_id='lateSupervisedWave')
        self.host.control(HostControl('repeat'), request='repeatSupervisedWave')
        self.host.poll()
        self.assertEqual(len(self.do_frames()), 1)
        self.assertEqual((self.stt_calls, len(self.client.requests)), (0, 0))
        self.assertFalse(self.started.is_set())


if __name__ == "__main__":
    unittest.main()
