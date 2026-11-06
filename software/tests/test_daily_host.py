"""Daily preferences and the real semantic/Host/B1 path with injected IO.

The historical arm record is a synthetic fixture, never a physical approval.
No provider, serial device, audio process or user diagnostic store is accessed.
"""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai.completed_turn_diagnostics import CompletedTurnStore
from bmo_ai.daily_preferences import DailyPreferences
from bmo_ai.features.host import FeatureHost, HostControl
from bmo_ai.features.motion.daily_usage import ArmUsageStore, CHECKS, DailyArmController, DEVICE, _ROOT
from bmo_ai.features.motion.legacy_host import NAMED_GESTURES
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from test_daily_arm_usage import Port
from test_semantic_requests import Provider, envelope


class DailyHostTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix='daily-host-')))
        self.hosts = []
        self.arm_count = 0
        self.preferences_path = self.root / 'preferences'
        for name in ('socket.socket.connect', 'subprocess.Popen', 'bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials', 'bmo_ai.fish_tts.synthesize_wav',
                     'bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open',
                     'bmo_ai.completed_turn_diagnostics.default_directory'):
            self.enterContext(patch(name, side_effect=AssertionError('Real IO or user diagnostic store forbidden')))

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def controller(self, *, commissioned=True, enabled=True, conversation_gestures=False):
        root = self.root / ('arms-' + str(self.arm_count))
        self.arm_count += 1
        root.mkdir(mode=0o700)
        mount = root / 'board'; mount.mkdir(mode=0o700)
        code = (_ROOT / 'code.py').read_bytes().replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
        (mount / 'code.py').write_bytes(code)
        (mount / 'bmo_arm_bridge.py').write_bytes((_ROOT / 'bmo_arm_bridge.py').read_bytes())
        (mount / 'boot.py').write_bytes(b'import usb_cdc\nusb_cdc.enable(console=True, data=True)\n')
        identity = dict(device=str(DEVICE), interface='02', label='CircuitPython CDC2 fixture',
            product='RP2040 fixture', vendor_id='239a', product_id='80f4', serial='synthetic_daily_host')
        store = ArmUsageStore(root / 'settings')
        if commissioned:
            store.commission(reviewed_sha=hashlib.sha256(code).hexdigest(),
                test_session_id='synthetic_completed_test', checks={key: True for key in CHECKS},
                mount=mount, identity_reader=lambda: dict(identity))
        store.preferences(dict(enabled=enabled, conversation_gestures=conversation_gestures))
        port = Port()
        controller = DailyArmController(store=store, runtime_ready=lambda: True,
            port_factory=lambda: port, identity_reader=lambda: dict(identity), mount=mount)
        return controller, port

    def make_host(self, replies=(), *, preferences=None, controller=None, player=None):
        config, provider = integration_config(), Provider(replies)
        def immediate(_command, *, on_start, cancel_check, **_kwargs):
            cancel_check(); on_start(); cancel_check()
        voice = IntegrationVoice(config, provider, simulate=True, character_life=True,
            player=player or immediate, emit=lambda *_a, **_kw: None)
        store = CompletedTurnStore(self.root / ('turns-' + str(len(self.hosts))))
        self.addCleanup(store.close)
        self.assertTrue(store.snapshot()['enabled'])
        motion = controller.motion if controller is not None else None
        host = FeatureHost(config=config, client=provider, voice=voice,
            runtime=RuntimePolicy(simulate=False, arms_commissioned=motion is not None),
            motion=motion, arm_usage=controller,
            emotion_arms=bool(motion is not None and controller.enabled and controller.conversation_gestures),
            character_life=True, completed_turn_store=store, preferences=preferences)
        self.hosts.append(host)
        return host, provider

    def pump(self, host, predicate=None):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            host.heartbeat(); host.poll()
            if predicate() if predicate is not None else not host.session.busy and host.audio.owner is None:
                return
            time.sleep(.002)
        self.fail('Synthetic daily Host did not reach its bounded boundary')

    def ask(self, host, question):
        self.assertTrue(host.start_question(question))
        self.pump(host)

    @staticmethod
    def meaning(kind, name, target=None):
        return envelope(kind=kind, disposition='execute', intent={'name': name, 'target': target})

    @staticmethod
    def verbs(port, verb):
        return [frame for frame in port.frames if frame.split()[1] == verb.encode()]

    def test_initial_defaults_and_poll_do_not_create_a_settings_file(self):
        preferences = DailyPreferences(self.preferences_path)
        host, _ = self.make_host(preferences=preferences)
        for _ in range(3): host.poll()
        self.assertEqual(host.snapshot()['volume']['level'], .25)
        self.assertFalse(host.snapshot()['volume']['muted'])
        self.assertEqual(host.snapshot()['subtitles']['mode'], 'off')
        self.assertFalse(self.preferences_path.exists())
        self.assertEqual(preferences.load(), {})

    def test_menu_volume_mute_and_subtitles_survive_a_new_host_without_overwriting_gain(self):
        preferences = DailyPreferences(self.preferences_path)
        host, _ = self.make_host(preferences=preferences)
        result = host.control(HostControl('volume', {'level': .6, 'muted': True}), 'menu_volume')
        self.assertEqual(result.code, 'volume_applied')
        host.control(HostControl('subtitles', 'ko'), 'menu_subtitles')
        host.poll(); host.close()
        restarted, _ = self.make_host(preferences=DailyPreferences(self.preferences_path))
        state = restarted.snapshot()
        self.assertEqual((state['volume']['level'], state['volume']['muted']), (.6, True))
        self.assertEqual(state['subtitles']['mode'], 'ko')
        self.assertEqual(restarted.config.fish_gain, .25)
        self.assertEqual(DailyPreferences(self.preferences_path).load(),
            {'volume': {'level': .6, 'muted': True}, 'subtitle_mode': 'ko'})

    def test_spoken_volume_and_subtitle_changes_use_the_same_persistent_settings(self):
        preferences = DailyPreferences(self.preferences_path)
        host, provider = self.make_host([
            self.meaning('volume', 'set_volume', 'level_65'),
            envelope(kind='subtitles', disposition='execute', subtitle_mode='ko')], preferences=preferences)
        self.ask(host, 'Set the volume to sixty five percent.')
        self.ask(host, '한국어 자막을 켜 줘.')
        self.assertEqual(len(provider.requests), 2)
        host.close()
        restarted, _ = self.make_host(preferences=DailyPreferences(self.preferences_path))
        self.assertEqual(restarted.snapshot()['volume']['level'], .65)
        self.assertEqual(restarted.snapshot()['subtitles']['mode'], 'ko')
        self.assertEqual(restarted.config.fish_gain, .25)

    def test_off_and_uncommissioned_hosts_always_publish_all_seven_named_actions(self):
        for controller in (None, self.controller(commissioned=False)[0]):
            with self.subTest(commissioning=controller is not None):
                host, provider = self.make_host([self.meaning('arm', 'arm_gesture', 'wave'), envelope()],
                                                controller=controller)
                self.assertEqual(set(host.snapshot()['system_diagnostics']['arms']['supported_gestures']), NAMED_GESTURES)
                self.ask(host, 'Wave your hand.')
                context = host.session.expression_client.responses.context
                self.assertEqual(set(context['arm_actions']), NAMED_GESTURES)
                self.assertEqual(len(context['arm_actions']), 7)
                self.assertFalse(context['arm_status']['request_allowed'])
                self.assertEqual(host.session.expression_client.last_reply.speech,
                    "My arms aren't ready yet. Check Move arms in the menu.")
                self.assertEqual(host.snapshot()['last_result']['status'], 'rejected')
                self.assertFalse(host.snapshot()['last_result']['executed'])
                self.ask(host, 'What is two plus two?')
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(host.snapshot()['mode'], 'conversation')

    def test_menu_preference_disable_then_enable_preserves_current_stop_latch(self):
        controller, port = self.controller(conversation_gestures=True)
        host, _ = self.make_host(controller=controller)
        self.assertFalse(port.opened)
        host.execute(HostIntent('arm_gesture', 'wave'), request_id='menu_first_wave')
        self.pump(host, lambda: len(self.verbs(port, 'DO')) == 1)
        off = host.control(HostControl('arm_usage', {'enabled': False, 'conversation_gestures': False}), 'arm_off')
        self.assertEqual(off.code, 'arm_preferences_saved')
        self.assertTrue(port.stopped)
        self.assertEqual(len(self.verbs(port, 'STOP')), 1)
        on = host.control(HostControl('arm_usage', {'enabled': True, 'conversation_gestures': True}), 'arm_on')
        self.assertEqual(on.code, 'arm_preferences_saved')
        self.assertTrue(port.stopped)
        result = host.execute(HostIntent('arm_gesture', 'both_up'), request_id='menu_after_stop')
        host.poll()
        self.assertIn(result.status, ('blocked', 'rejected'))
        self.assertEqual(len(self.verbs(port, 'DO')), 1)
        self.assertEqual(len(self.verbs(port, 'HELLO')), 1)
        self.assertEqual(controller.store.load()['enabled'], True)
        self.assertEqual(controller.store.load()['conversation_gestures'], True)
        self.assertFalse(host.snapshot()['system_diagnostics']['arms']['action_status']['request_allowed'])
        raw = (controller.store.directory / 'daily.json').read_text()
        self.assertNotIn('supervisor_present', raw)
        self.assertNotIn('power_cutoff_ready', raw)

    def test_english_and_korean_named_actions_reach_the_same_b1_path_and_repeat_moves_none(self):
        requests = [('Wave your hand.', 'wave'), ('손 흔들어 줘.', 'wave'),
            ('Raise your left arm.', 'raise_left_arm'), ('왼팔 들어 줘.', 'raise_left_arm'),
            ('Put both arms down.', 'both_down'), ('양팔 내려 줘.', 'both_down')]
        for text, target in requests:
            with self.subTest(text=text):
                controller, port = self.controller()
                host, provider = self.make_host([self.meaning('arm', 'arm_gesture', target)], controller=controller)
                self.ask(host, text)
                self.assertEqual(len(self.verbs(port, 'DO')), 1)
                self.assertEqual(self.verbs(port, 'DO')[0].split()[-1], target.encode())
                self.assertEqual(host.session.expression_client.last_reply.speech,
                    "Okay, I'll ask my arms to do that.")
                self.assertEqual(len(provider.requests), 1)
                self.assertFalse(host.snapshot()['system_diagnostics']['arms']['physical_verified'])
                host.control(HostControl('repeat'), 'repeat_' + str(len(self.hosts)))
                self.pump(host)
                self.assertEqual(len(self.verbs(port, 'DO')), 1)
                self.assertEqual(len(provider.requests), 1)

    def test_spoken_stop_is_dispatched_once_before_acknowledgement_playback_finishes(self):
        for text in ('Stop moving your arms.', '팔 움직이지 마. 멈춰 줘.'):
            with self.subTest(text=text):
                controller, port = self.controller()
                began, release = Event(), Event()
                def player(_command, *, on_start, cancel_check, **_kwargs):
                    on_start(); began.set()
                    deadline = time.monotonic() + 4
                    while not release.wait(.002):
                        cancel_check()
                        if time.monotonic() >= deadline:
                            raise AssertionError('Synthetic stop speech was not released')
                host, provider = self.make_host([self.meaning('arm', 'stop_arms')], controller=controller, player=player)
                host.execute(HostIntent('arm_gesture', 'wave'), request_id='menu_wave_before_stop')
                self.pump(host, lambda: len(self.verbs(port, 'DO')) == 1)
                try:
                    self.assertTrue(host.start_question(text))
                    self.assertTrue(began.wait(2))
                    self.pump(host, lambda: len(self.verbs(port, 'STOP')) == 1)
                    self.assertTrue(host.session.busy)
                    self.assertFalse(release.is_set())
                    self.assertTrue(port.stopped)
                    receipts = [value for value in host._result_history.values()
                                if value.code == 'arm_stop_acknowledged']
                    self.assertEqual(len(receipts), 1)
                    self.assertEqual(receipts[0].completion, 'ack_position_unverified')
                    self.assertTrue(receipts[0].executed)
                    self.assertFalse(host.snapshot()['system_diagnostics']['arms']['physical_verified'])
                    for _ in range(3): host.poll()
                    self.assertEqual(len(self.verbs(port, 'STOP')), 1)
                finally:
                    release.set()
                    self.pump(host)
                host.control(HostControl('repeat'), 'repeat_stop')
                self.pump(host)
                self.assertEqual(len(self.verbs(port, 'STOP')), 1)
                self.assertEqual(len(self.verbs(port, 'DO')), 1)
                self.assertEqual(len(provider.requests), 1)


if __name__ == '__main__':
    unittest.main()
