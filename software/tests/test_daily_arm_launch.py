"""The ordinary desktop entry loads saved arm intent without session flags."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.completed_turn_diagnostics import CompletedTurnStore
from bmo_ai.daily_preferences import DailyPreferences
from bmo_ai.desktop_app import main
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.motion.daily_usage import DailyArmController, ArmUsageStore, CHECKS, DEVICE, _ROOT
from bmo_ai.integration_voice import IntegrationVoice
from bmo_ai.screen_profiles import SCREEN_PROFILES
from test_daily_arm_usage import Port
from test_desktop_arm_fallback import Browser


class DailyArmLaunchTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='daily-arm-launch-')))
        self.mount = self.root/'board'; self.mount.mkdir(mode=0o700)
        enabled = (_ROOT/'code.py').read_bytes().replace(b'REMOTE_ENABLED = False', b'REMOTE_ENABLED = True', 1)
        for name, data in {'code.py':enabled, 'bmo_arm_bridge.py':(_ROOT/'bmo_arm_bridge.py').read_bytes(),
                           'boot.py':b'import usb_cdc\nusb_cdc.enable(console=True, data=True)\n'}.items():
            (self.mount/name).write_bytes(data)
        self.identity = dict(device=str(DEVICE), interface='02', label='CircuitPython CDC2 fixture',
            product='RP2040 fixture', vendor_id='239a', product_id='80f4', serial='synthetic_launch_01')
        self.store = ArmUsageStore(self.root/'arm-settings')
        self.store.commission(reviewed_sha=hashlib.sha256(enabled).hexdigest(), test_session_id='fixture_prior_test',
            checks={key:True for key in CHECKS}, mount=self.mount, identity_reader=lambda:self.identity)
        self.store.preferences(dict(enabled=True, conversation_gestures=True))
        self.preferences = DailyPreferences(self.root/'preferences')
        self.preferences.update(volume=dict(level=.4, muted=False), subtitle_mode='ko', presence_mode='normal')
        fixture = Path(__file__).resolve().parents[1]/'checks/fixtures/bmo_face_contract.html'
        profiles = dict(SCREEN_PROFILES)
        profiles['ci-synthetic'] = dict(default_path=str(fixture), sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
            label='Public synthetic text API fixture; no artwork', approval_status='synthetic_test_only', pi_live_verified=False)
        self.enterContext(patch('bmo_ai.desktop_app.SCREEN_PROFILES', profiles))
        self.enterContext(patch('bmo_ai.integration_face.SCREEN_PROFILES', profiles))
        self.forbidden = []
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'subprocess.Popen',
                       'bmo_ai.private_credentials.resolve_credentials', 'bmo_ai.cloud.create_client',
                       'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                       'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.local_piper.synthesize_wav',
                       'bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open'):
            guard = Mock(side_effect=AssertionError('Physical/provider IO forbidden'))
            self.enterContext(patch(target, guard)); self.forbidden.append(guard)

    def tearDown(self):
        for guard in self.forbidden: guard.assert_not_called()

    def test_ordinary_icon_arguments_load_preferences_and_lazy_reviewed_adapter(self):
        ports, settings_seen = [], []
        original = DailyArmController.__init__
        def injected_init(owner, *args, **kwargs):
            port = Port(stopped=bool(ports and ports[-1].stopped)); ports.append(port)
            kwargs.update(port_factory=lambda:port, mount=self.mount, identity_reader=lambda:self.identity)
            original(owner, *args, **kwargs)
        client = FeatureSimulatedClient()
        def factory(**settings):
            settings_seen.append(dict(settings))
            settings['voice'] = IntegrationVoice(settings['config'], client, simulate=True,
                character_life=True, emit=lambda *_a, **_k: None)
            settings['completed_turn_store'] = CompletedTurnStore(self.root/'turns')
            return FeatureHost(**settings)
        app_seen, callback_errors = [], []
        def started(app):
            app_seen.append(app)
            try:
                self.assertFalse(app.failed, app.startup_snapshot())
                self.assertFalse(ports[-1].opened)
                app.queue_desktop_control('fixture_face', {'action':'face_ready','value':dict(run_id=app.run_id,
                    screen_sha256=app.server._bridge.sha256, frame_version='synthetic-daily-arm')})
                app.poll()
                app.queue_desktop_control('fixture_present', {'action':'face_presented','value':dict(run_id=app.run_id)})
                app.poll()
                self.assertEqual(app.startup_snapshot()['phase'], 'ready')
                self.assertTrue(app.host.runtime.arms_commissioned)
                self.assertFalse(app.host.runtime.arms_supervised)
                self.assertEqual(app.host.volume.snapshot()['level'], .4)
                self.assertEqual(app.host.session.subtitle_snapshot()['mode'], 'ko')
                self.assertEqual(app.startup_snapshot()['character_life']['presence']['mode'], 'normal')
                if len(app_seen) == 2:
                    self.assertFalse(app.host.arm_usage.enabled)
                    self.assertFalse(app.host._emotion_arms)
                    self.assertFalse(ports[-1].opened)
                    return
                result = app.host.execute(HostIntent('arm_gesture','raise_left_arm'), request_id='fixture_menu_arm')
                self.assertEqual(result.status, 'accepted')
                app.poll()
                self.assertTrue(ports[-1].opened)
                self.assertEqual(sum(b' DO ' in raw for raw in ports[-1].frames), 1)
                app.host.control(HostControl('arm_usage', dict(enabled=False, conversation_gestures=False)),
                                 request='fixture_disable')
                self.assertTrue(ports[-1].stopped)
            except Exception as error:
                callback_errors.append(repr(error))
                raise
            finally:
                app.queue_desktop_control('fixture_exit', {'action':'exit', 'value':None})
        greeting = SimpleNamespace(voice=SimpleNamespace(volume=None), cancel=lambda:True, close=lambda:True,
            play=Mock(side_effect=AssertionError('silent startup')))
        with patch.object(DailyArmController, '__init__', injected_init), redirect_stdout(io.StringIO()), \
             redirect_stderr(io.StringIO()) as errors:
            for _ in range(2):
                code = main(['--live','--character-life','--silent-start','--no-playable-games',
                    '--screen-profile','ci-synthetic','--arm-usage-directory',str(self.store.directory),
                    '--preferences-directory',str(self.preferences.directory),
                    '--voice-bank-directory',str(self.root/'bank'),'--voice-pack-directory',str(self.root/'pack')],
                    lock_path=self.root/'app.lock', host_factory=factory, browser_factory=lambda *_a,**_k:Browser(),
                    greeting_factory=lambda **_k:greeting, credential_resolver=lambda:SimpleNamespace(openai_api_key='synthetic-fixture',fish_api_key=None),
                    client_factory=lambda *_a,**_k:client, on_started=started)
        self.assertEqual(code, 0, errors.getvalue()+repr(callback_errors))
        self.assertEqual(len(app_seen),2)
        self.assertTrue(app_seen[0].cleanup_snapshot()['resources_cleaned'])
        self.assertTrue(ports[0].closed)
        self.assertFalse(self.store.load()['enabled'])
        self.assertFalse(settings_seen[0]['runtime'].arms_supervised)
        self.assertTrue(settings_seen[0]['runtime'].arms_commissioned)
        self.assertTrue(settings_seen[0]['emotion_arms'])
        self.assertFalse(settings_seen[1]['emotion_arms'])
        self.assertFalse(ports[1].opened)
        self.assertTrue(app_seen[1].cleanup_snapshot()['resources_cleaned'])


if __name__ == '__main__': unittest.main()
