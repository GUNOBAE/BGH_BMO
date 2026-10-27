"""Production frontend ownership/security without constructing Qt or a GUI."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.desktop_app import main, parser
from bmo_ai.desktop_browser import BrowserError, DesktopBrowser
from bmo_ai.desktop_install import desktop_install_plan, DesktopInstallError
from bmo_ai.desktop_native_window import available, command, origin_allowed, validated_loopback, main as native_main
from tests.test_desktop_browser import FakeProcess


class DesktopReleaseFrontendTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.executable = self.root / 'chromium'
        self.executable.write_text('fake, never executed'); self.executable.chmod(0o700)
        self.env = {'XDG_RUNTIME_DIR': str(self.root), 'WAYLAND_DISPLAY': 'wayland-0',
                    'FISH_API_KEY': 'synthetic-secret', 'OPENAI_API_KEY': 'synthetic-secret',
                    'QTWEBENGINE_CHROMIUM_FLAGS': '--no-sandbox', 'PYTHONPATH': '/private'}
        self.forbidden = self.enterContext(patch('subprocess.Popen', side_effect=AssertionError('No GUI')))

    def browser(self, frontend, probe):
        process = FakeProcess(); factory = Mock(return_value=process)
        def signaler(*_): process.returncode = 0
        value = DesktopBrowser('http://127.0.0.1:8123/', frontend=frontend,
            executable=self.executable, environ=self.env, native_probe=probe,
            process_factory=factory, group_signaler=signaler,
            observer=lambda p: p.returncode is not None,
            group_probe=lambda *_: process.returncode is None)
        self.addCleanup(value.close)
        return value, factory

    def test_optional_native_uses_same_owned_cleanup_and_scrubbed_environment(self):
        value, factory = self.browser('auto', Mock(return_value=True))
        with patch('bmo_ai.desktop_native_window.native_python', return_value='/usr/bin/python3.13'):
            value.start()
        self.assertEqual(value.snapshot()['selected_frontend'], 'native')
        args, options = factory.call_args.args[0], factory.call_args.kwargs
        self.assertEqual(args[:3], ['/usr/bin/python3.13', '-I', '-B'])
        self.assertIn('desktop_native_window.py', args[3]); self.assertNotIn('--windowed', args)
        for name in ('PYTHONPATH', 'QTWEBENGINE_CHROMIUM_FLAGS', 'FISH_API_KEY', 'OPENAI_API_KEY'):
            self.assertNotIn(name, options['env'])
        self.assertTrue(options['close_fds'] and options['start_new_session'])
        self.assertTrue(value.mark_ready()); report = value.close()['cleanup']
        self.assertEqual(report['scope'], 'owned_bmo_native_window'); self.assertTrue(report['resources_cleaned'])
        factory.assert_called_once()

    def test_auto_missing_native_uses_existing_chromium_without_extra_spawn(self):
        value, factory = self.browser('auto', Mock(return_value=False)); value.start()
        self.assertEqual(value.snapshot()['selected_frontend'], 'chromium')
        self.assertEqual(factory.call_args.args[0][0], str(self.executable))
        self.assertIn('--mute-audio', factory.call_args.args[0]); factory.assert_called_once(); value.close()

    def test_explicit_native_missing_fails_without_implicit_browser_launch(self):
        value, factory = self.browser('native', Mock(return_value=False))
        with self.assertRaises(BrowserError) as error: value.start()
        self.assertEqual(error.exception.code, 'native_window_unavailable')
        self.assertTrue(value.snapshot()['cleanup_complete']); factory.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [self.executable])

    def test_explicit_chromium_never_checks_optional_qt(self):
        probe = Mock(side_effect=AssertionError('No Qt check'))
        value, _ = self.browser('chromium', probe); value.start(); probe.assert_not_called(); value.close()

    def test_native_probe_is_isolated_bounded_and_never_forwards_output(self):
        for result in (SimpleNamespace(returncode=0), SimpleNamespace(returncode=1),
                       subprocess.TimeoutExpired('private-library', 2)):
            runner = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
            with patch('bmo_ai.desktop_native_window.native_python', return_value='/usr/bin/python3.13'), \
                    patch('bmo_ai.desktop_native_window.subprocess.run', runner):
                self.assertEqual(available({'HOME': '/synthetic'}), not isinstance(result, Exception) and result.returncode == 0)
            self.assertEqual(runner.call_args.args[0][:3], ['/usr/bin/python3.13', '-I', '-B'])
            self.assertEqual(runner.call_args.kwargs['timeout'], 2.)
            for key in ('stdin', 'stdout', 'stderr'): self.assertEqual(runner.call_args.kwargs[key], subprocess.DEVNULL)
            self.assertNotIn('shell', runner.call_args.kwargs)
        self.forbidden.assert_not_called()

    def test_native_origin_blocks_remote_other_port_files_and_top_frame_data(self):
        self.assertTrue(origin_allowed('http', '127.0.0.1', 8123, 8123))
        for scheme, host, port in (('https', '127.0.0.1', 8123), ('http', 'localhost', 8123),
                                   ('http', '127.0.0.1', 9999), ('file', '', -1),
                                   ('ws', 'evil.example', 8123), ('data', '', -1)):
            self.assertFalse(origin_allowed(scheme, host, port, 8123))
        for scheme in ('data', 'about', 'blob'): self.assertTrue(origin_allowed(scheme, '', -1, 8123, local_frame=True))
        for url in ('http://localhost:8123/', 'file:///private/index.html',
                    'http://127.0.0.1:8123/?key=private', 'http://user@127.0.0.1:8123/'):
            with self.assertRaises(ValueError): validated_loopback(url)
        with patch('bmo_ai.desktop_native_window.native_python', return_value='/usr/bin/python3.13'):
            self.assertEqual(command('http://127.0.0.1:8123/', windowed=True)[-1], '--windowed')

    def test_native_window_setup_enforces_face_only_and_single_python_audio_owner(self):
        # Execute the actual Qt setup function with a substitute Qt boundary;
        # module wiring, signal handlers and page overrides are production code.
        from urllib.parse import urlsplit
        class Signal:
            def connect(self, callback): self.callback = callback
        class Url:
            def __init__(self, value): self.value = urlsplit(value)
            def scheme(self): return self.value.scheme
            def host(self): return self.value.hostname or ''
            def port(self): return self.value.port or -1
        class App:
            def __init__(self, args): pass
            def setApplicationName(self, name): self.name = name
            def exec(self): return 0
        class Profile:
            HttpCacheType = SimpleNamespace(NoCache='no-cache')
            PersistentCookiesPolicy = SimpleNamespace(NoPersistentCookies='no-cookies')
            def __init__(self, parent): self.downloadRequested = Signal()
            def setUrlRequestInterceptor(self, value): self.interceptor = value
            def setHttpCacheType(self, value): self.cache = value
            def setPersistentCookiesPolicy(self, value): self.cookies = value
        class Page:
            def __init__(self, profile, parent): self.profile = profile; self.permissionRequested = Signal(); self.attributes = {}
            def setAudioMuted(self, value): self.muted = value
            def settings(self): return self
            def setAttribute(self, key, value): self.attributes[key] = value
        class Interceptor:
            def __init__(self, parent): pass
        class View:
            instance = None
            def __init__(self): View.instance = self; self.fullscreen = self.windowed = False
            def setPage(self, page): self.page = page
            def setContextMenuPolicy(self, policy): self.context_menu = policy
            def setWindowTitle(self, title): self.title = title
            def resize(self, *size): self.size = size
            def show(self): self.windowed = True
            def setWindowFlag(self, *args): self.flag = args
            def showFullScreen(self): self.fullscreen = True
            def setUrl(self, url): self.url = url
        qt = SimpleNamespace(WindowType=SimpleNamespace(FramelessWindowHint='frameless'),
                             ContextMenuPolicy=SimpleNamespace(NoContextMenu='none'))
        attrs = SimpleNamespace(JavascriptCanOpenWindows='popups', LocalContentCanAccessRemoteUrls='remote')
        modules = {'PySide6.QtCore': SimpleNamespace(QUrl=Url, Qt=qt),
            'PySide6.QtWidgets': SimpleNamespace(QApplication=App),
            'PySide6.QtWebEngineWidgets': SimpleNamespace(QWebEngineView=View),
            'PySide6.QtWebEngineCore': SimpleNamespace(QWebEnginePage=Page,
                QWebEngineProfile=Profile, QWebEngineUrlRequestInterceptor=Interceptor,
                QWebEngineSettings=SimpleNamespace(WebAttribute=attrs))}
        with patch.dict(sys.modules, modules):
            self.assertEqual(native_main(['--url', 'http://127.0.0.1:8123/']), 0)
        view, page = View.instance, View.instance.page
        self.assertEqual((view.title, view.size, view.context_menu), ('BMO', (800, 480), 'none'))
        self.assertTrue(view.fullscreen); self.assertFalse(view.windowed); self.assertTrue(page.muted)
        self.assertEqual(page.attributes, {'popups': False, 'remote': False})
        self.assertEqual(page.profile.cache, 'no-cache'); self.assertEqual(page.profile.cookies, 'no-cookies')
        self.assertIsNone(page.createWindow(None))
        self.assertFalse(page.acceptNavigationRequest(Url('https://example.invalid/'), None, True))
        self.assertTrue(page.acceptNavigationRequest(Url('http://127.0.0.1:8123/'), None, True))
        self.assertFalse(page.acceptNavigationRequest(Url('data:text/html,evil'), None, True))
        for url, blocked in [('http://127.0.0.1:8123/api/features', False), ('https://example.invalid/', True)]:
            request = SimpleNamespace(requestUrl=lambda value=url: Url(value), block=Mock())
            page.profile.interceptor.interceptRequest(request)
            self.assertEqual(request.block.called, blocked)
        permission, download = Mock(), Mock()
        page.permissionRequested.callback(permission); page.profile.downloadRequested.callback(download)
        permission.deny.assert_called_once(); download.cancel.assert_called_once()

    def test_app_status_frontend_media_flags_have_zero_device_probe_or_keys(self):
        forbidden = Mock(side_effect=AssertionError('Read-only public plan'))
        with patch('os.open', forbidden), patch('subprocess.run', forbidden), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['--status', '--live', '--frontend', 'auto', '--ptt-microphone',
                '--usb-gamepad', '--music-supervised', '--music-directory', '/synthetic/wav']), 0)
        state = json.loads(output.getvalue()); self.assertEqual(state['frontend'], 'auto')
        self.assertTrue(state['usb_gamepad']['enabled']); self.assertTrue(state['music_library']['live_playback_enabled'])
        self.assertFalse(state['credentials_accessed']); forbidden.assert_not_called()
        for args in (['--music-supervised', '--live'], ['--simulate', '--music-supervised', '--music-directory', '/synthetic']):
            with patch('os.open', forbidden), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): main(args)

    def test_installer_persists_only_explicit_flags_without_installing(self):
        plan = desktop_install_plan(home=self.root, frontend='auto', ptt_microphone=True,
            usb_gamepad=True, music_directory='/synthetic/wav', music_supervised=True, startup_chime=False)
        args = plan['command']; self.assertEqual(parser().parse_args(args[1:]).frontend, 'auto')
        for flag in ('--usb-gamepad', '--ptt-microphone', '--music-supervised', '--no-startup-chime'): self.assertIn(flag, args)
        self.assertNotIn('--arms-supervised', args); self.assertFalse((self.root / 'Desktop').exists())
        self.assertEqual(plan['files_written'], 0)
        with self.assertRaises(DesktopInstallError):
            desktop_install_plan(home=self.root, simulate=True, music_supervised=True, music_directory='/synthetic')

    def test_installer_round_trip_preserves_explicit_chromium_and_supervised_camera(self):
        for frontend in ('auto', 'native', 'chromium'):
            plan = desktop_install_plan(home=self.root, frontend=frontend,
                camera_supervised=True, photo_directory='/synthetic/photos',
                camera_video_supervised=True, video_directory='/synthetic/video')
            args = parser().parse_args(plan['command'][1:])
            self.assertEqual(args.frontend, frontend)
            self.assertTrue(args.camera_supervised and args.camera_video_supervised)
            self.assertEqual(args.photo_directory, Path('/synthetic/photos'))
            self.assertEqual(args.video_directory, Path('/synthetic/video'))
            self.assertFalse(args.arms_supervised)
            self.assertFalse((self.root / 'Desktop').exists())
        for options in (dict(camera_supervised=True), dict(photo_directory='/synthetic'),
                        dict(camera_video_supervised=True, video_directory='/synthetic')):
            with self.assertRaises(DesktopInstallError):
                desktop_install_plan(home=self.root, **options)


if __name__ == '__main__': unittest.main()
