"""Actual Desktop launcher degradation with provider/device IO replaced."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.desktop_app import main
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.motion.legacy_host import LegacyArmError
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_voice import IntegrationVoice
from bmo_ai.private_credentials import CredentialError


class FullBatchLauncherTests(unittest.TestCase):
    def test_missing_startup_credentials_keep_local_menus_available_but_gate_conversation_before_mic(self):
        with tempfile.TemporaryDirectory(prefix='bmo-launch-no-credentials-') as directory:
            root = Path(directory)
            server = Mock()
            server.start.return_value = server
            server.url = 'http://127.0.0.1:1234/'
            server._bridge = SimpleNamespace(sha256='a'*64)
            browser = Mock()
            browser.poll.return_value = {'state': 'running'}
            greeting = Mock()
            greeting.voice = SimpleNamespace()
            resolver = Mock(side_effect=CredentialError('credentials_missing'))
            forbidden = Mock(side_effect=AssertionError('Provider/microphone/player forbidden'))
            observed = {}
            now = [time.monotonic()]

            def started(app):
                try:
                    host = app.host
                    observed['host'] = host
                    self.assertIsNotNone(host)
                    self.assertFalse(app.failed)
                    host.microphone.source_factory = forbidden
                    app.queue_desktop_control('no_credentials_ready', dict(action='face_ready', value=dict(
                        run_id=app.run_id, screen_sha256='a'*64, frame_version='13.2')))
                    app.poll()
                    app.queue_desktop_control('no_credentials_presented', dict(action='face_presented', value=dict(run_id=app.run_id)))
                    app.poll()
                    self.assertEqual(app.startup_snapshot()['phase'], 'ready')
                    provider = host.snapshot()['providers']['cloud']
                    self.assertTrue(provider['blocked'])
                    self.assertEqual((provider['code'], provider['recovery']), ('credentials_missing', 'restart_required'))
                    self.assertFalse(host.ptt_button())
                    self.assertFalse(host.start_question('Do not send this question.'))
                    host.control(HostControl('reset'), 'reset_missing_provider')
                    deadline = time.monotonic() + 3
                    while host.session.busy and time.monotonic() < deadline:
                        host.heartbeat()
                        host.poll()
                        time.sleep(.002)
                    host.poll()
                    self.assertFalse(host.session.busy)
                    self.assertTrue(host.snapshot()['providers']['cloud']['blocked'])
                    self.assertEqual(host.snapshot()['providers']['cloud']['recovery'], 'restart_required')
                    now[0] += 1.  # A new button press after the existing debounce window.
                    self.assertFalse(host.ptt_button())
                    self.assertFalse(host.start_question('Still blocked until a configured restart.'))
                    self.assertEqual(len(host.games.catalog()), 3)
                    host.execute(HostIntent('open_game_menu'), request_id='local_menu_without_keys')
                    self.assertEqual(host.modes.mode, 'game_menu')
                    self.assertIn('/games/engines.js', host.game_resources())
                    host.execute(HostIntent('return_to_face'), request_id='face_without_keys')
                    self.assertEqual(host.modes.mode, 'conversation')
                    self.assertFalse(host.camera.snapshot()['on'])
                    self.assertFalse(host.session.busy)
                finally:
                    app._exit.set()

            with patch('bmo_ai.desktop_app.FeatureServer', return_value=server), \
                 patch('subprocess.Popen', forbidden), patch('socket.socket.connect', forbidden), \
                 patch('bmo_ai.unavailable_provider.UnavailableCloudClient.create', forbidden), \
                 patch('bmo_ai.fish_tts.synthesize_wav', forbidden), \
                 patch('bmo_ai.audio.record_wav', forbidden), redirect_stderr(io.StringIO()) as output:
                status = main(['--live', '--frontend', 'chromium', '--silent-start',
                    '--no-feedback-voice', '--ptt-microphone', '--playable-games'],
                    lock_path=root/'app.lock', browser_factory=Mock(return_value=browser),
                    greeting_factory=Mock(return_value=greeting), credential_resolver=resolver,
                    client_factory=forbidden, on_started=started, clock=lambda: now[0])
            self.assertEqual(status, 0, output.getvalue())
            self.assertTrue(observed['host'].cleanup_snapshot()['resources_cleaned'])
            resolver.assert_called_once_with()
            forbidden.assert_not_called()
            greeting.play.assert_not_called()
            server.close.assert_called_once()
            browser.close.assert_called_once()

    def test_reviewed_b1_sha_mismatch_degrades_only_arms_without_opening_a_port_or_losing_conversation(self):
        with tempfile.TemporaryDirectory(prefix='bmo-launch-degrade-') as directory:
            root = Path(directory)
            server = Mock()
            server.start.return_value = server
            server.url = 'http://127.0.0.1:1234/'
            server._bridge = SimpleNamespace(sha256='a'*64)
            browser = Mock()
            browser.poll.return_value = {'state': 'running'}
            client = FeatureSimulatedClient([dict(version=1, speech='A test reply.',
                emotion='neutral', gesture='none', intent=None)])
            greeted = Mock()
            greeted.voice = SimpleNamespace()
            observed = {}

            def make_host(**settings):
                def player(_command, *, on_start, cancel_check, **_):
                    cancel_check()
                    on_start()
                    cancel_check()
                voice = IntegrationVoice(settings['config'], client, simulate=True,
                    emit=lambda *_a, **_kw: None, player=player)
                host = FeatureHost(**settings, voice=voice)
                observed['host'] = host
                return host

            def started(app):
                try:
                    host = app.host
                    self.assertIsNotNone(host)
                    self.assertFalse(app.failed)
                    self.assertFalse(host.runtime.arms_supervised)
                    observed['arms'] = host.snapshot()['system_diagnostics']['arms']
                    app.queue_desktop_control('face_ready_test', dict(action='face_ready', value=dict(
                        run_id=app.run_id, screen_sha256='a'*64, frame_version='13.2')))
                    app.poll()
                    app.queue_desktop_control('presented_test', dict(action='face_presented', value=dict(run_id=app.run_id)))
                    app.poll()
                    self.assertEqual(app.startup_snapshot()['code'], 'greeting_cancelled')
                    self.assertFalse(app.startup_snapshot()['greeting_played'])
                    self.assertTrue(host.start_question('A current test question.'))
                    deadline = time.monotonic() + 3
                    while host.session.busy and time.monotonic() < deadline:
                        host.heartbeat()
                        host.poll()
                        time.sleep(.002)
                    self.assertFalse(host.session.busy)
                    self.assertEqual(len(client.requests), 1)
                    self.assertEqual(host.session.snapshot()['phase'], 'idle')
                    self.assertEqual(host.modes.mode, 'conversation')
                finally:
                    app._exit.set()

            forbidden = Mock(side_effect=AssertionError('Physical/provider IO forbidden'))
            with patch('bmo_ai.desktop_app.FeatureServer', return_value=server), \
                 patch('bmo_ai.features.motion.legacy_host.validate_legacy_install',
                       side_effect=LegacyArmError('bridge_sha_or_dependency_mismatch')), \
                 patch('bmo_ai.features.motion.arm_serial_posix_v2.OwnedCdcDataPort', forbidden), \
                 patch('subprocess.Popen', forbidden), patch('socket.socket.connect', forbidden), \
                 patch('bmo_ai.fish_tts.synthesize_wav', forbidden), \
                 patch('bmo_ai.audio.record_wav', forbidden), redirect_stderr(io.StringIO()) as output:
                status = main(['--live', '--frontend', 'chromium', '--silent-start',
                    '--no-feedback-voice', '--arms-supervised', '--arm-legacy-bridge',
                    '--arm-reviewed-code-sha', 'a'*64, '--arm-power-off-ready'],
                    lock_path=root/'app.lock', host_factory=make_host,
                    browser_factory=Mock(return_value=browser),
                    greeting_factory=Mock(return_value=greeted),
                    credential_resolver=lambda: SimpleNamespace(openai_api_key='synthetic', fish_api_key='synthetic'),
                    client_factory=lambda *_a, **_kw: client, on_started=started)
            self.assertEqual(status, 0, output.getvalue())
            self.assertIn('arm_bridge_sha_or_dependency_mismatch', observed['arms']['blocked_reasons'])
            self.assertFalse(observed['arms']['runtime_enabled'])
            self.assertFalse(observed['arms']['physical_verified'])
            self.assertTrue(observed['host'].cleanup_snapshot()['resources_cleaned'])
            forbidden.assert_not_called()
            greeted.play.assert_not_called()
            server.close.assert_called_once()
            browser.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
