"""Fake child/process-group only; never launch an interactive browser."""
import json
from contextlib import nullcontext
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.desktop_browser import BrowserError, DesktopBrowser, _active_owned_group, _observe_exit


class FakeProcess:
    pid = 43127

    def __init__(self, returncode=None, waits=()):
        self.returncode, self.waits, self.wait_calls = returncode, list(waits), []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.wait_calls.append(timeout)
        result = self.waits.pop(0) if self.waits else 0
        if isinstance(result, BaseException):
            raise result
        self.returncode = result
        return result


class DesktopBrowserTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.executable = self.root / "chromium"
        self.executable.write_text("synthetic executable; must never run\n")
        self.executable.chmod(0o700)
        self.now = 0.0
        self.env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(self.root),
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(self.root / "bus"), "DISPLAY": ":1",
                    "OPENAI_API_KEY": "synthetic_private_openai", "FISH_API_KEY": "synthetic_private_fish",
                    "CHROMIUM_FLAGS": "--no-sandbox", "LD_PRELOAD": "synthetic_private_library"}
        for target in ("subprocess.Popen", "os.killpg", "socket.socket.connect",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("Real process/signal/network/key forbidden")))

    def browser(self, process=None, **changes):
        process = process or FakeProcess()
        factory = Mock(return_value=process)
        def terminate_fake_group(pgid, value):
            process.returncode = 0
        signaler = Mock(side_effect=terminate_fake_group)
        settings = {"executable": self.executable, "environ": self.env, "process_factory": factory,
                    "group_signaler": signaler, "clock": lambda: self.now,
                    "observer": lambda child: child.returncode is not None,
                    "group_probe": lambda pgid, sid: process.returncode is None}
        settings.update(changes)
        browser = DesktopBrowser("http://127.0.0.1:32145/", **settings)
        def cleanup():
            try:
                browser.stop()
            except BrowserError:
                pass
        self.addCleanup(cleanup)
        return browser, process, factory, signaler

    def test_start_is_owned_kiosk_wayland_private_profile_and_allowlisted_environment(self):
        browser, process, factory, signaler = self.browser()
        self.assertIs(browser.start(), browser)
        self.assertIs(browser.start(), browser)
        factory.assert_called_once()
        arguments, options = factory.call_args.args[0], factory.call_args.kwargs
        for flag in ("--kiosk", "--start-fullscreen", "--ozone-platform=wayland", "--mute-audio"):
            self.assertIn(flag, arguments)
        self.assertTrue(options["start_new_session"] and options["close_fds"])
        self.assertNotIn("shell", options)
        self.assertFalse(any("sandbox" in value or "remote-debugging" in value for value in arguments))
        profile = Path(next(value.split("=", 1)[1] for value in arguments if value.startswith("--user-data-dir=")))
        self.assertEqual(profile.stat().st_mode & 0o777, 0o700)
        self.assertEqual(options["env"]["HOME"], str(profile))
        self.assertEqual(options["env"]["XDG_CONFIG_HOME"], str(profile / "config"))
        self.assertNotIn("synthetic_private", json.dumps(options["env"]))
        self.assertNotIn("CHROMIUM_FLAGS", options["env"])
        public = json.dumps(browser.snapshot())
        for private in ("32145", str(self.root), "argv", "environment", "http://"):
            self.assertNotIn(private, public)
        self.assertEqual(browser.snapshot()["state"], "starting")
        signaler.assert_not_called()

    def test_windowed_is_explicit_app_window_without_kiosk(self):
        browser, _, factory, _ = self.browser(windowed=True)
        browser.start()
        arguments = factory.call_args.args[0]
        self.assertNotIn("--kiosk", arguments)
        self.assertNotIn("--start-fullscreen", arguments)
        self.assertIn("--app=http://127.0.0.1:32145/", arguments)

    def test_face_handshake_controls_ready_and_prevents_startup_timeout(self):
        browser, process, _, signaler = self.browser()
        self.assertFalse(browser.mark_ready())
        browser.start()
        self.assertTrue(browser.mark_ready())
        self.assertTrue(browser.mark_ready())
        self.now = 1000
        self.assertEqual(browser.poll()["state"], "running")
        process.returncode = 0
        state = browser.poll()
        self.assertEqual((state["state"], state["code"], state["cleanup_complete"]),
                         ("exited", "browser_closed", True))
        self.assertFalse(browser.mark_ready())
        signaler.assert_not_called()

    def test_startup_timeout_terminates_only_owned_group_and_never_relaunches(self):
        browser, process, factory, signaler = self.browser(startup_timeout=2)
        browser.start()
        self.now = 2
        state = browser.poll()
        self.assertEqual((state["state"], state["code"]), ("failed", "browser_start_timeout"))
        signaler.assert_called_once_with(process.pid, signal.SIGTERM)
        self.assertTrue(state["cleanup_complete"])
        self.assertFalse(state["private_profile"])
        browser.poll()
        factory.assert_called_once()
        self.assertFalse(browser.mark_ready())

    def test_immediate_child_exit_is_start_failure_and_profile_is_reaped(self):
        browser, process, _, signaler = self.browser(FakeProcess(returncode=17))
        with self.assertRaises(BrowserError) as caught:
            browser.start()
        self.assertEqual(caught.exception.code, "browser_start_failed")
        self.assertTrue(browser.snapshot()["cleanup_complete"])
        self.assertTrue(process.wait_calls)
        signaler.assert_not_called()

    def test_owned_stop_escalates_after_bounded_wait_and_is_idempotent(self):
        active = [True]
        process = FakeProcess()
        def signal_group(pgid, value):
            if value == signal.SIGKILL:
                active[0], process.returncode = False, 0
        signaler = Mock(side_effect=signal_group)
        browser, process, _, _ = self.browser(process, stop_timeout=.05,
            group_signaler=signaler, group_probe=lambda pgid, sid: active[0])
        browser.start()
        state = browser.stop()
        self.assertEqual(signaler.call_args_list[0].args, (process.pid, signal.SIGTERM))
        self.assertEqual(signaler.call_args_list[1].args, (process.pid, signal.SIGKILL))
        self.assertEqual(process.wait_calls, [.05])
        self.assertTrue(state["cleanup_complete"])
        self.assertFalse(state["owned_child"])
        browser.close()
        self.assertEqual(signaler.call_count, 2)

    def test_unreaped_child_preserves_profile_and_reports_cleanup_failure(self):
        timeouts = (subprocess.TimeoutExpired("private_data", .1),)
        browser, _, _, _ = self.browser(FakeProcess(waits=timeouts), stop_timeout=.1)
        browser.start()
        with self.assertRaises(BrowserError) as caught:
            browser.stop()
        self.assertEqual(caught.exception.code, "browser_cleanup_failed")
        self.assertNotIn("private_data", str(caught.exception))
        self.assertFalse(browser.snapshot()["cleanup_complete"])
        self.assertTrue(browser.snapshot()["private_profile"])
        self.assertTrue(browser.close()["cleanup_complete"])

    def test_spawn_failure_has_fixed_error_and_removes_only_own_profile(self):
        factory = Mock(side_effect=OSError("synthetic_private_os_path"))
        browser, _, _, _ = self.browser(process_factory=factory)
        before = set(self.root.iterdir())
        with self.assertRaises(BrowserError) as caught:
            browser.start()
        self.assertEqual(caught.exception.code, "browser_spawn_failed")
        self.assertNotIn("synthetic_private", str(caught.exception))
        self.assertEqual(set(self.root.iterdir()), before)

    def test_bad_executable_links_permissions_and_names_never_spawn(self):
        alias = self.root / "chromium-browser"
        alias.symlink_to(self.executable)
        for path in (alias, self.root / "other", self.root / "missing" / "chromium"):
            browser, _, factory, _ = self.browser(executable=path)
            with self.subTest(path=path.name), self.assertRaises(BrowserError):
                browser.start()
            factory.assert_not_called()
        for mode in (0o777, 0o600, 0o4700):
            self.executable.chmod(mode)
            browser, _, factory, _ = self.browser()
            with self.subTest(mode=mode), self.assertRaises(BrowserError):
                browser.start()
            factory.assert_not_called()
        self.executable.chmod(0o700)

    def test_missing_wayland_and_unsafe_runtime_fail_before_spawn(self):
        for env in ({}, {"XDG_RUNTIME_DIR": str(self.root), "WAYLAND_DISPLAY": "/tmp/wayland"},
                    {"XDG_RUNTIME_DIR": "relative", "WAYLAND_DISPLAY": "wayland-0"}):
            browser, _, factory, _ = self.browser(environ=env)
            with self.subTest(names=tuple(env)), self.assertRaises(BrowserError):
                browser.start()
            factory.assert_not_called()
        self.root.chmod(0o755)
        browser, _, factory, _ = self.browser()
        with self.assertRaises(BrowserError):
            browser.start()
        factory.assert_not_called()
        self.root.chmod(0o700)

    def test_profile_collisions_and_replaced_symlinks_preserve_other_data(self):
        existing = self.root / "bmo-chromium-fixed"
        existing.mkdir(mode=0o700)
        (existing / "keep").write_text("existing private browser")
        browser, _, factory, _ = self.browser()
        with patch("bmo_ai.desktop_browser.secrets.token_hex", return_value="fixed"), self.assertRaises(BrowserError):
            browser.start()
        factory.assert_not_called()
        self.assertEqual((existing / "keep").read_text(), "existing private browser")
        other, _, calls, _ = self.browser()
        other.start()
        profile = Path(calls.call_args.kwargs["env"]["HOME"])
        profile.rmdir()
        profile.symlink_to(existing, target_is_directory=True)
        with self.assertRaises(BrowserError):
            other.close()
        self.assertEqual((existing / "keep").read_text(), "existing private browser")

    def test_invalid_urls_and_timeouts_have_no_side_effects(self):
        for url in ("https://127.0.0.1:123/", "http://localhost:123/", "http://127.0.0.1:0/",
                    "http://127.0.0.1:123/?token=synthetic_private", "http://user@127.0.0.1:123/",
                    "http://127.0.0.1:123/anything", "file:///tmp/index.html"):
            with self.subTest(index=len(url)), self.assertRaises(BrowserError):
                DesktopBrowser(url)
        for changes in ({"startup_timeout": float("nan")}, {"stop_timeout": 0}, {"windowed": 1}):
            with self.assertRaises(BrowserError):
                self.browser(**changes)
        self.assertEqual(list(self.root.iterdir()), [self.executable])

    def test_constructor_reads_only_allowed_environment_names(self):
        class Guarded(dict):
            def get(mapping, name, default=None):
                if name not in ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "XAUTHORITY"):
                    raise AssertionError("Environment value access forbidden")
                return super().get(name, default)
            def keys(mapping):
                raise AssertionError("Whole environment copy forbidden")
        browser, _, _, _ = self.browser(environ=Guarded(self.env))
        browser.start()

    def test_session_metadata_cannot_forward_remote_bus_or_arbitrary_key_file(self):
        private = self.root / "credentials.json"
        private.write_text("synthetic_private_credentials")
        private.chmod(0o600)
        environment = {**self.env, "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus;tcp:host=invalid,port=9999",
                       "XAUTHORITY": str(private)}
        browser, _, factory, _ = self.browser(environ=environment)
        browser.start()
        child = factory.call_args.kwargs["env"]
        self.assertNotIn("XAUTHORITY", child)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", child)
        self.assertNotIn("synthetic_private", json.dumps(child))

    def test_exited_leader_with_active_descendants_is_cleaned_before_reap(self):
        process, active, order = FakeProcess(returncode=0), [True], []
        def signal_group(pgid, value):
            self.assertEqual(process.wait_calls, [])
            order.append((pgid, value))
            active[0] = False
        signaler = Mock(side_effect=signal_group)
        browser, _, _, _ = self.browser(process, group_probe=lambda pgid, sid: active[0], group_signaler=signaler)
        with patch.object(process, "poll", side_effect=AssertionError("Do not reap via poll")), \
             self.assertRaises(BrowserError) as caught:
            browser.start()
        self.assertEqual(caught.exception.code, "browser_start_failed")
        self.assertEqual(order, [(process.pid, signal.SIGTERM)])
        self.assertEqual(process.wait_calls, [browser.stop_timeout])
        self.assertTrue(browser.snapshot()["cleanup_complete"])
        browser.stop()
        self.assertEqual(len(order), 1)  # No signals after the leader was reaped.

    def test_uncertain_or_persistent_group_preserves_profile_and_unreaped_leader(self):
        active = [True]
        browser, process, _, _ = self.browser(stop_timeout=.05,
            group_probe=lambda pgid, sid: active[0], group_signaler=Mock())
        browser.start()
        with self.assertRaises(BrowserError) as caught:
            browser.stop()
        self.assertEqual(caught.exception.code, "browser_cleanup_failed")
        self.assertEqual(process.wait_calls, [])
        self.assertTrue(browser.snapshot()["private_profile"])
        active[0], process.returncode = False, 0
        self.assertTrue(browser.stop()["cleanup_complete"])

    def test_unavailable_nonreaping_observer_fails_closed_without_signals(self):
        browser, process, _, signaler = self.browser(observer=Mock(side_effect=BrowserError("browser_cleanup_failed")))
        with self.assertRaises(BrowserError) as caught:
            browser.start()
        self.assertEqual(caught.exception.code, "browser_cleanup_failed")
        signaler.assert_not_called()
        self.assertEqual(process.wait_calls, [])
        self.assertFalse(browser.snapshot()["cleanup_complete"])

    def test_real_observer_uses_wnowait_and_has_no_reaping_fallback(self):
        process = SimpleNamespace(pid=43127)
        with patch("bmo_ai.desktop_browser.os.waitid", return_value=None) as observe:
            self.assertFalse(_observe_exit(process))
        self.assertEqual(observe.call_args.args, (os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG))
        with patch("bmo_ai.desktop_browser.os.waitid", side_effect=NotImplementedError()), \
             self.assertRaises(BrowserError):
            _observe_exit(process)

    def test_group_probe_reads_only_bounded_stat_metadata_and_checks_session(self):
        entries = [SimpleNamespace(name="43127", path="/proc/43127")]
        for session, state, active in ((43127, "S", True), (43127, "Z", False)):
            raw = f"43127 (synthetic child) {state} 1 43127 {session} 0 0\n".encode()
            with patch("bmo_ai.desktop_browser.os.scandir", return_value=nullcontext(iter(entries))), \
                 patch("bmo_ai.desktop_browser.os.open", side_effect=(80, 81)) as opened, \
                 patch("bmo_ai.desktop_browser.os.read", return_value=raw), \
                 patch("bmo_ai.desktop_browser.os.close"):
                self.assertEqual(_active_owned_group(43127, 43127), active)
                self.assertEqual(opened.call_args_list[1].args[0], "stat")
        with patch("bmo_ai.desktop_browser.os.scandir", return_value=nullcontext(iter(entries))), \
             patch("bmo_ai.desktop_browser.os.open", side_effect=(80, 81)), \
             patch("bmo_ai.desktop_browser.os.read", return_value=b"43127 (synthetic) S 1 43127 999 0\n"), \
             patch("bmo_ai.desktop_browser.os.close"), self.assertRaises(BrowserError):
            _active_owned_group(43127, 43127)
        many = (SimpleNamespace(name="not_numeric") for _ in range(8193))
        with patch("bmo_ai.desktop_browser.os.scandir", return_value=nullcontext(many)), \
             patch("bmo_ai.desktop_browser.time.monotonic", return_value=0), self.assertRaises(BrowserError):
            _active_owned_group(43127, 43127)


class OwnedGroupSubprocessTests(unittest.TestCase):
    def test_fresh_harmless_orphan_group_is_killed_and_reaped_in_isolated_subreaper(self):
        # The subreaper applies ONLY inside this short-lived test Python child,
        # never to the application or the user's desktop session.
        script = r'''
import ctypes,json,os,signal,subprocess,sys,tempfile,time
from unittest.mock import patch
from bmo_ai.desktop_browser import BrowserError,DesktopBrowser
if ctypes.CDLL(None).prctl(36,1,0,0,0) != 0:
    raise RuntimeError("Synthetic subreaper unavailable")
leader=child=None
events=[]
child_code="import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('ready',flush=True);time.sleep(30)"
leader_code="import subprocess,sys;p=subprocess.Popen([sys.executable,'-B','-c',"+repr(child_code)+"],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True);p.stdout.readline();print(p.pid,flush=True)"
def factory(*args,**kwargs):
    global leader,child
    leader=subprocess.Popen([sys.executable,'-B','-c',leader_code],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True,env={'PATH':os.defpath})
    child=int(leader.stdout.readline())
    deadline=time.monotonic()+3
    while os.waitid(os.P_PID,leader.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG) is None:
        if time.monotonic()>deadline: raise RuntimeError('Fresh leader exit timeout')
        time.sleep(.01)
    leader.poll=lambda: (_ for _ in ()).throw(AssertionError('Popen.poll must not reap'))
    return leader
def signal_group(pgid,value):
    assert pgid==leader.pid and leader.returncode is None
    assert os.waitid(os.P_PID,leader.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG) is not None
    events.append(value)
    os.killpg(pgid,value)
with tempfile.TemporaryDirectory(prefix='bmo-owned-group-test-') as runtime:
    browser=DesktopBrowser('http://127.0.0.1:12345/',process_factory=factory,group_signaler=signal_group,stop_timeout=.1,environ={'XDG_RUNTIME_DIR':runtime,'WAYLAND_DISPLAY':'wayland-0'})
    try:
        with patch('bmo_ai.desktop_browser._executable',return_value=sys.executable):
            try: browser.start()
            except BrowserError as error: assert error.code=='browser_start_failed'
            else: raise AssertionError('Exited leader must fail startup')
        assert events==[signal.SIGTERM,signal.SIGKILL]
        assert browser.snapshot()['cleanup_complete']
        deadline=time.monotonic()+3
        while True:
            pid,status=os.waitpid(child,os.WNOHANG)
            if pid==child: break
            if time.monotonic()>deadline: raise RuntimeError('Fresh child reap timeout')
            time.sleep(.01)
        child=None
        assert leader.returncode==0
        browser.stop()
        assert len(events)==2
        print(json.dumps({'owned_group_cleaned':True,'all_toy_children_reaped':True,'interactive_browser':False}))
    finally:
        if child is not None:
            try:
                if os.getpgid(child)==leader.pid: os.killpg(leader.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            os.waitpid(child,0)
        if leader is not None:
            if leader.returncode is None: leader.wait(timeout=3)
            leader.stdout.close()
'''
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=Path(__file__).resolve().parents[1],
                                env={"PATH": os.defpath, "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, "Isolated owned-group regression failed")
        self.assertEqual(json.loads(result.stdout), {"owned_group_cleaned": True,
            "all_toy_children_reaped": True, "interactive_browser": False})


if __name__ == "__main__":
    unittest.main()
