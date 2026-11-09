"""Explicit native game launcher/installer wiring, with no real game or pad."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import desktop_install
from bmo_ai.desktop_app import main, parser
from bmo_ai.features.host import FeatureHost
from bmo_ai.private_credentials import CredentialError


class NativeGameLaunchTests(unittest.TestCase):
    def setUp(self):
        self.forbidden = Mock(side_effect=AssertionError("No game/device/provider IO"))
        for name in ('subprocess.Popen', 'socket.socket.connect',
                     'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.audio.record_wav'):
            self.enterContext(patch(name, self.forbidden))

    def test_default_has_no_native_permission_and_existing_profile_defaults_remain(self):
        args = parser().parse_args(['--live', '--character-life'])
        self.assertFalse(args.native_supertux)
        self.assertIsNone(args.native_gamepad_profile)
        self.assertTrue(args.playable_games)
        self.assertFalse(args.usb_gamepad)

    def test_native_requires_explicit_live_and_pad_path_requires_native_before_any_io(self):
        cases = (['--native-supertux', '--status'],
                 ['--simulate', '--native-supertux', '--dry-run'],
                 ['--live', '--native-gamepad-profile', '/private/pad.json', '--status'],
                 ['--install-desktop', '--native-supertux', '--dry-run'])
        for args in cases:
            with self.subTest(args=args), patch('os.open', self.forbidden), \
                    patch('bmo_ai.private_credentials.resolve_credentials', self.forbidden), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as end:
                main(args)
            self.assertEqual(end.exception.code, 2)
        self.forbidden.assert_not_called()

    def test_status_and_dry_run_allow_missing_profile_without_claiming_readiness_or_opening_it(self):
        for action in ('--status', '--dry-run'):
            with self.subTest(action=action), patch('os.open', self.forbidden), \
                    patch('bmo_ai.private_credentials.resolve_credentials', self.forbidden), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--live', '--native-supertux', '--native-gamepad-profile',
                    '/not-installed/observed-pad.json', action],
                    host_factory=self.forbidden, browser_factory=self.forbidden), 0)
            value = json.loads(output.getvalue())
            self.assertEqual(value['native_supertux'], dict(requested=True,
                gamepad_profile_selected=True, installation='NOT_CHECKED', screen_input_readiness='NOT_RUN',
                audio='silent', master_volume_scope='conversation_and_music',
                automatic_download=False, automatic_start=False))
            self.assertFalse(value['processes_started'] or value['credentials_accessed'])
        self.forbidden.assert_not_called()

    def test_desktop_install_branch_preserves_explicit_native_options(self):
        with patch('bmo_ai.desktop_install.install_desktop', return_value={'status': 'DRY_RUN'}) as install, \
                patch('os.open', self.forbidden), \
                patch('bmo_ai.private_credentials.resolve_credentials', self.forbidden), \
                redirect_stdout(io.StringIO()):
            result = main(['--live', '--install-desktop', '--dry-run', '--native-supertux',
                '--native-gamepad-profile', '/private/observed-pad.json'])
        self.assertEqual(result, 0)
        self.assertEqual(install.call_args.kwargs, dict(install=False, simulate=False,
            frontend='auto', native_supertux=True, native_gamepad_profile=Path('/private/observed-pad.json')))
        self.forbidden.assert_not_called()

    def test_live_launcher_passes_optional_game_settings_without_starting_a_game(self):
        # This checks the launcher boundary; native process/pad readiness is
        # exercised separately by the actual Host/runner's injected IO tests.
        with TemporaryDirectory() as directory:
            root = Path(directory)
            server = Mock()
            server.start.return_value = server
            server.url = 'http://127.0.0.1:1234/'
            server._bridge = SimpleNamespace(sha256='a' * 64)
            browser = Mock()
            browser.poll.return_value = dict(state='running')
            greeting = Mock(); greeting.voice = SimpleNamespace()
            selected = root / 'not-yet-observed-pad.json'
            observed = {}

            def make_host(**settings):
                observed['native_supertux'] = settings.pop('native_supertux')
                observed['native_gamepad_profile'] = settings.pop('native_gamepad_profile')
                observed['runtime'] = settings['runtime']
                return FeatureHost(**settings)

            def started(app):
                self.assertIsNotNone(app.host)
                self.assertFalse(app.failed)
                self.assertEqual(app.host.modes.mode, 'conversation')
                app._exit.set()

            with patch('bmo_ai.desktop_app.FeatureServer', return_value=server), \
                    redirect_stderr(io.StringIO()) as output:
                result = main(['--live', '--frontend', 'chromium', '--silent-start',
                    '--native-supertux', '--native-gamepad-profile', str(selected)],
                    lock_path=root / 'app.lock', host_factory=make_host,
                    browser_factory=Mock(return_value=browser), greeting_factory=Mock(return_value=greeting),
                    credential_resolver=Mock(side_effect=CredentialError('credentials_missing')),
                    client_factory=self.forbidden, on_started=started)
            self.assertEqual(result, 0, output.getvalue())
            self.assertTrue(observed['native_supertux'])
            self.assertEqual(observed['native_gamepad_profile'], selected)
            self.assertFalse(observed['runtime'].simulate)
            self.assertFalse(selected.exists())
            greeting.play.assert_not_called()
            self.forbidden.assert_not_called()


class NativeGameInstallerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.home = self.root / 'home'; self.home.mkdir(mode=0o700)
        self.project = self.home / 'BMO Project'; self.project.mkdir(mode=0o700)
        source = Path(__file__).resolve().parents[2] / 'launch-desktop'
        launcher = self.project / 'launch-desktop'
        launcher.write_bytes(source.read_bytes()); launcher.chmod(0o700)
        self.profile = self.home / 'BMO_private' / 'pad profile.json'
        self.forbidden = Mock(side_effect=AssertionError('No game/installer process or credentials'))
        for target in ('subprocess.Popen', 'socket.socket.connect', 'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(target, self.forbidden))

    def options(self, **changes):
        return dict(project_root=self.project, home=self.home,
            environ={'HOME': str(self.home), 'OPENAI_API_KEY': 'DO_NOT_COPY', 'FISH_API_KEY': 'DO_NOT_COPY'},
            native_supertux=True, native_gamepad_profile=self.profile, **changes)

    def test_private_candidate_wrapper_preserves_opt_in_and_missing_profile_without_modifying_existing_desktop(self):
        desktop = self.home / 'Desktop'; desktop.mkdir(mode=0o700)
        original = desktop / 'BMO-Candidate.desktop'
        original.write_text('existing private runner entry\n'); original.chmod(0o700)
        before = original.read_bytes(), original.stat().st_ino
        options = self.options(character_options=dict(character_life=True),
            install_target='applications-only', frontend='chromium', ptt_microphone=True)
        plan = desktop_install.install_desktop(**options)
        self.assertEqual(plan['status'], 'DRY_RUN')
        self.assertEqual(plan['native_game_readiness'], 'NOT_RUN')
        self.assertEqual(plan['native_game_audio'], 'silent')
        self.assertIn('--native-supertux', plan['command'])
        position = plan['command'].index('--native-gamepad-profile')
        self.assertEqual(plan['command'][position + 1], str(self.profile))
        self.assertFalse(self.profile.exists())
        self.assertNotIn('DO_NOT_COPY', json.dumps(plan))
        installed = desktop_install.install_desktop(install=True, **options)
        self.assertEqual(installed['status'], 'INSTALLED')
        entry = self.home / '.local/share/applications/BMO-Candidate.desktop'
        self.assertIn('"--native-supertux"', entry.read_text())
        self.assertIn('"--native-gamepad-profile" "' + str(self.profile) + '"', entry.read_text())
        wrapper = Path(installed['command'][0])
        self.assertIn('desktop_app "$@"', wrapper.read_text())
        self.assertEqual((original.read_bytes(), original.stat().st_ino), before)
        self.assertEqual(installed['activation'], 'NOT_RUN')
        self.forbidden.assert_not_called()

    def test_installer_rejects_simulation_unrequested_profile_and_command_like_paths(self):
        options = self.options()
        for changes in (dict(simulate=True), dict(native_supertux=False), dict(native_supertux=1),
                        dict(native_gamepad_profile='relative.json'),
                        dict(native_gamepad_profile='/private/pad;command.json')):
            with self.subTest(changes=changes), self.assertRaises(desktop_install.DesktopInstallError):
                desktop_install.install_desktop(**(options | changes))
        self.assertFalse((self.home / 'Desktop').exists())

    def test_standalone_installer_command_keeps_native_settings(self):
        with patch('bmo_ai.desktop_install.install_desktop', return_value={'status': 'DRY_RUN'}) as install, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(desktop_install.main(['--native-supertux', '--native-gamepad-profile',
                str(self.profile), '--project-root', str(self.project)]), 0)
        self.assertTrue(install.call_args.kwargs['native_supertux'])
        self.assertEqual(install.call_args.kwargs['native_gamepad_profile'], self.profile)
        self.assertFalse(install.call_args.kwargs['install'])
        self.forbidden.assert_not_called()


if __name__ == '__main__':
    unittest.main()
