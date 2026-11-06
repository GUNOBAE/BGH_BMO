"""Real desktop main/Host remain usable after optional supervised-arm setup fails.

Only the HTTP server is real (loopback). Browser, credentials, dialogue client,
voice output and the serial admission boundary are explicitly injected.
"""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.completed_turn_diagnostics import CompletedTurnStore
from bmo_ai.desktop_app import main
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.motion.arm_serial_posix_v2 import ArmPortUnavailable
from bmo_ai.features.motion.legacy_host import LegacyArmError
from bmo_ai.integration_voice import IntegrationVoice
from bmo_ai.screen_profiles import SCREEN_PROFILES


class Browser:
    def __init__(self, *_args, **_kwargs):
        self.closed = False
    def start(self):
        return self
    def poll(self):
        return {'state': 'running'}
    def mark_ready(self):
        return True
    def close(self):
        self.closed = True
        return True


class DesktopArmFallbackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-arm-fallback-')))
        fixture = Path(__file__).resolve().parents[1]/'checks/fixtures/bmo_face_contract.html'
        profiles = dict(SCREEN_PROFILES)
        profiles['ci-synthetic'] = dict(default_path=str(fixture),
            sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
            label='Public synthetic text API fixture; no artwork',
            approval_status='synthetic_test_only', pi_live_verified=False)
        self.enterContext(patch('bmo_ai.desktop_app.SCREEN_PROFILES', profiles))
        self.enterContext(patch('bmo_ai.integration_face.SCREEN_PROFILES', profiles))
        self.forbidden = []
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'subprocess.Popen',
                       'bmo_ai.private_credentials.resolve_credentials', 'bmo_ai.cloud.create_client',
                       'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                       'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.local_piper.synthesize_wav'):
            guard = Mock(side_effect=AssertionError('Physical/provider IO forbidden'))
            self.enterContext(patch(target, guard))
            self.forbidden.append(guard)

    def tearDown(self):
        for guard in self.forbidden:
            guard.assert_not_called()

    def run_fallback(self, *, validation_error=None, open_error=None, expected_code):
        client = FeatureSimulatedClient()
        browser = Browser()
        hosts, apps, playback = [], [], []
        checked = []

        def player(_command, *, cancel_check, on_start, **_settings):
            cancel_check(); on_start(); cancel_check()
            playback.append('started')

        def factory(**settings):
            # Keep the actual live RuntimePolicy and constructor validation.
            # Only IO adapters/private test storage are injected.
            checked.append(dict(settings))
            settings['voice'] = IntegrationVoice(settings['config'], client,
                simulate=True, player=player, character_life=True, emit=lambda *_a, **_k: None)
            settings['completed_turn_store'] = CompletedTurnStore(self.root/'turns')
            host = FeatureHost(**settings)
            hosts.append(host)
            return host

        def started(app):
            apps.append(app)
            try:
                self.assertFalse(app.failed, app.startup_snapshot())
                self.assertIsInstance(app.host, FeatureHost)
                self.assertFalse(app.host.runtime.simulate)
                app.queue_desktop_control('face_ready', {'action':'face_ready', 'value':{
                    'run_id':app.run_id, 'screen_sha256':app.server._bridge.sha256,
                    'frame_version':'synthetic-arm-fallback'}})
                app.poll()
                app.queue_desktop_control('face_presented', {'action':'face_presented',
                    'value':{'run_id':app.run_id}})
                app.poll()
                self.assertEqual(app.startup_snapshot()['phase'], 'ready')
                arms = app.host.snapshot()['system_diagnostics']['arms']
                launcher = arms['launcher']
                self.assertEqual(launcher, dict(supervised_requested=True,
                    supervised_effective=False, emotion_arms_requested=True,
                    emotion_arms_effective=False, setup_code=expected_code))
                self.assertIn(expected_code, arms['conversation']['blocked_reasons'])
                self.assertIsNone(arms['board']['remote_enabled_static'])
                self.assertFalse(arms['physical_verified'])
                self.assertNotIn('private-secret', json.dumps(arms))

                app.queue_command('question_after_arm_failure',
                                  HostControl('question', 'A synthetic next conversation.'))
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    app.heartbeat(); app.poll()
                    if client.requests and not app.host.session.busy and app.host.audio.owner is None:
                        break
                    time.sleep(.002)
                self.assertEqual(len(client.requests), 1)
                self.assertEqual(playback, ['started'])
                self.assertEqual(app.host.snapshot()['mode'], 'conversation')
                self.assertFalse(app.host.session.busy)
                self.assertIsNone(app.host.audio.owner)
                self.assertFalse(app.failed)
            finally:
                app.queue_desktop_control('exit_after_check', {'action':'exit', 'value':None})

        greeting = SimpleNamespace(voice=SimpleNamespace(volume=None),
                                  cancel=lambda: True, close=lambda: True,
                                  play=Mock(side_effect=AssertionError('Silent startup')))
        with patch('bmo_ai.features.motion.legacy_host.validate_legacy_install',
                   side_effect=validation_error) as validate, \
             patch('bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort.open',
                   side_effect=open_error or AssertionError('Port must remain unopened')) as port, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            code = main(['--live', '--character-life', '--silent-start', '--no-playable-games',
                         '--screen-profile', 'ci-synthetic',
                         '--arms-supervised', '--emotion-arms', '--arm-legacy-bridge',
                         '--arm-reviewed-code-sha', 'a'*64, '--arm-power-off-ready',
                         '--voice-bank-directory', str(self.root/'bank'),
                         '--voice-pack-directory', str(self.root/'pack')],
                        lock_path=self.root/'app.lock', host_factory=factory,
                        browser_factory=lambda *_a, **_k: browser,
                        greeting_factory=lambda **_k: greeting,
                        credential_resolver=lambda: SimpleNamespace(openai_api_key='synthetic-fixture', fish_api_key=None),
                        client_factory=lambda *_a, **_k: client, on_started=started)
        self.assertEqual(code, 0, errors.getvalue())
        validate.assert_called_once()
        self.assertEqual(port.call_count, 0 if validation_error is not None else 1)
        self.assertEqual(len(hosts), 1)
        self.assertEqual(len(apps), 1)
        self.assertTrue(checked[0]['arm_supervision_requested'])
        self.assertTrue(checked[0]['emotion_arms_requested'])
        self.assertFalse(checked[0]['emotion_arms'])
        self.assertFalse(checked[0]['runtime'].arms_supervised)
        self.assertTrue(apps[0].cleanup_snapshot()['resources_cleaned'])
        self.assertTrue(browser.closed)
        greeting.play.assert_not_called()

    def test_reviewed_source_mismatch_keeps_live_conversation_available(self):
        self.run_fallback(validation_error=LegacyArmError('original_source_mismatch'),
                          expected_code='arm_original_source_mismatch')

    def test_unavailable_data_port_keeps_live_conversation_available(self):
        self.run_fallback(open_error=ArmPortUnavailable('arm_data_port_busy_or_unavailable'),
                          expected_code='arm_data_port_busy_or_unavailable')

    def test_unknown_setup_exception_is_redacted_and_does_not_disable_conversation(self):
        self.run_fallback(validation_error=RuntimeError('private-secret-board-location'),
                          expected_code='arm_setup_failed')


if __name__ == '__main__':
    unittest.main()
