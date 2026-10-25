"""Desktop owner fault boundaries; no real Host, server, browser or audio IO."""
import json
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import HostControl
from bmo_ai.features.resources import AudioFocus
from bmo_ai.greeting_cache import GreetingError


class Clock:
    now = 0.0
    def __call__(self):return self.now


class Host:
    def __init__(self):
        self.audio = AudioFocus()
        self.session = SimpleNamespace(busy=False)
        self.closed = False
        self.commands = []
        self.stop_calls = self.poll_calls = 0
    def poll(self):self.poll_calls += 1
    def heartbeat(self):pass
    def stop(self):self.stop_calls += 1;return True
    def close(self):self.closed = True
    def snapshot(self):return {'mode':'conversation','mode_generation':0}
    def face_snapshot(self):
        return dict(session_id='synthetic-session',turn_id='',utterance_id='',phase='idle',
                    emotion='neutral',emotion_mode='auto',playback='idle',position=0.,cues=[])
    def queue_command(self, nonce, command):
        self.commands.append((nonce,command))
        return {'request_id':'synthetic_'+nonce,'status':'accepted','code':'queued'}


class Resource:
    def __init__(self):
        self.closed = False
        self._bridge = SimpleNamespace(sha256='a'*64)
        self.ready_calls = 0
    def poll(self):return {'state':'running','code':'running'}
    def mark_ready(self):self.ready_calls += 1
    def close(self):self.closed = True


class Greeting:
    def __init__(self, *, error=None, blocked=False):
        self.error, self.blocked = error, blocked
        self.entered, self.release = Event(), Event()
        self.calls = 0
        self.closed = False
    def play(self, *, emit, cancel_check):
        self.calls += 1
        if self.error is not None:raise self.error
        emit('prepared',cues=[{'time':0.,'mouth':'closed'},{'time':.1,'mouth':'wide'}])
        emit('started')
        self.entered.set()
        if self.blocked and not self.release.wait(2):
            raise AssertionError('Synthetic worker was not released')
        # Deliberately ignore cancellation after release to model late native
        # completion. The runtime must preserve its deadline/STOP decision.
        emit('ended')
        return {'completed':True}
    def close(self):self.closed = True;self.release.set();return True


class DeferredStartedGreeting(Greeting):
    def __init__(self):
        super().__init__(blocked=True)
        self.allow_started, self.started = Event(), Event()
    def play(self, *, emit, cancel_check):
        self.calls += 1
        emit('prepared',cues=[{'time':0.,'mouth':'closed'}])
        self.entered.set()
        if not self.allow_started.wait(2):raise AssertionError('Synthetic start gate timeout')
        emit('started');self.started.set()
        if not self.release.wait(2):raise AssertionError('Synthetic completion gate timeout')
        emit('ended')
        return {'completed':True}
    def close(self):
        self.allow_started.set()
        return super().close()


class DesktopRuntimeFailureTests(unittest.TestCase):
    def setUp(self):
        self.apps, self.guards = [], []
        for target in ('socket.socket.connect','socket.socket.connect_ex','subprocess.Popen',
                       'bmo_ai.private_credentials.resolve_credentials','bmo_ai.cloud.create_client',
                       'bmo_ai.audio.record_wav','bmo_ai.audio.transcribe',
                       'bmo_ai.fish_tts.synthesize_wav','bmo_ai.local_piper.synthesize_wav'):
            guard = Mock(side_effect=AssertionError('Production IO forbidden'))
            self.enterContext(patch(target,guard));self.guards.append(guard)

    def tearDown(self):
        for app in self.apps:
            if app.greeting is not None:app.greeting.release.set()
            try:app.close()
            except FeatureError:pass
        for guard in self.guards:guard.assert_not_called()

    def app(self, greeting=None):
        clock=Clock()
        app=DesktopRuntime(clock=clock,simulate=True,startup_timeout=2.,greeting_timeout=.2)
        app.set_server(Resource());app.set_browser(Resource())
        app.set_host(Host(),greeting or Greeting())
        self.apps.append(app)
        return app,clock

    def ready(self, app):
        return app.queue_desktop_control('ready',{'action':'face_ready','value':{
            'run_id':app.run_id,'screen_sha256':'a'*64,'frame_version':'synthetic-contract-v1'}})

    def drain(self, app):
        deadline=time.monotonic()+1
        while app._worker is not None and time.monotonic()<deadline:
            app.poll();time.sleep(.001)
        self.assertIsNone(app._worker,'Synthetic worker cleanup did not finish')
        app.poll()

    def test_thread_start_failure_releases_focus_and_still_closes_all_owned_resources(self):
        app,_=self.app();self.ready(app)
        with patch('bmo_ai.desktop_runtime.Thread.start',side_effect=RuntimeError('private-secret-thread')):
            app.poll()
        self.assertIsNone(app._worker)
        self.assertIsNone(app.host.audio.owner)
        self.assertIn(app.startup_snapshot()['phase'],('ready','error'))
        self.assertNotIn('private-secret',json.dumps(app.startup_snapshot()))
        app.close()
        self.assertTrue(app.browser.closed and app.server.closed and app.host.closed and app.greeting.closed)

    def test_join_failure_does_not_skip_browser_server_or_host_cleanup(self):
        app,_=self.app()
        app._lease=app.host.audio.acquire('greeting',stop=app.cancel_greeting)
        worker=Mock()
        worker.join.side_effect=RuntimeError('private-secret-join')
        worker.is_alive.return_value=False
        app._worker=worker
        try:app.close()
        except FeatureError as error:self.assertEqual(error.code,'desktop_cleanup_failed')
        self.assertTrue(app.browser.closed and app.server.closed and app.host.closed and app.greeting.closed)
        self.assertIsNone(app.host.audio.owner)

    def test_deadline_keeps_focus_until_worker_exits_and_late_success_cannot_erase_timeout(self):
        greeting=Greeting(blocked=True);app,clock=self.app(greeting);self.ready(app)
        app.poll();self.assertTrue(greeting.entered.wait(1));app.poll()
        self.assertEqual(app.host.audio.owner,'greeting')
        self.assertTrue(app.startup_snapshot()['greeting_played'])
        clock.now=1.;app.poll()
        self.assertEqual(app.startup_snapshot()['code'],'greeting_timeout')
        self.assertEqual(app.host.audio.owner,'greeting')
        self.assertTrue(app._worker.is_alive())
        with self.assertRaises(FeatureError):
            app.queue_command('premature_talk',HostControl('question','Synthetic question.'))
        greeting.release.set();self.drain(app)
        self.assertIsNone(app.host.audio.owner)
        self.assertEqual(app.startup_snapshot()['phase'],'ready')
        self.assertEqual(app.startup_snapshot()['code'],'greeting_timeout')
        self.ready(app);app.poll()
        self.assertEqual(greeting.calls,1)

    def test_untrusted_exception_code_and_message_are_not_public(self):
        error=RuntimeError('private-secret-message-and-path')
        error.code='private-secret-code-and-token'
        app,_=self.app(Greeting(error=error));self.ready(app);app.poll();self.drain(app)
        public=json.dumps(app.startup_snapshot())
        self.assertNotIn('private-secret',public)
        self.assertEqual(app.startup_snapshot()['code'],'greeting_failed')
        self.assertIsNone(app.host.audio.owner)

    def test_initial_missing_cache_shows_ready_face_and_reconnect_never_auto_retries(self):
        greeting=Greeting(error=GreetingError('greeting_missing'))
        app,_=self.app(greeting);self.ready(app);app.poll();self.drain(app)
        state=app.startup_snapshot()
        self.assertEqual((state['phase'],state['code'],state['greeting_attempts']),('ready','greeting_missing',1))
        self.assertTrue(state['face_ready']);self.assertFalse(state['greeting_played'])
        self.assertEqual(app.face_snapshot()['phase'],'idle')
        self.assertIsNone(app.host.audio.owner)
        for _ in range(3):self.ready(app);app.heartbeat();app.poll()
        self.assertEqual(greeting.calls,1)

    def test_pending_user_stop_or_cancel_wins_over_initial_face_ready(self):
        for action in ('stop','cancel'):
            with self.subTest(action=action):
                app,_=self.app()
                app.queue_command('safety_'+action,HostControl(action))
                self.ready(app);app.poll();app.poll()
                self.assertIsNone(app._worker)
                self.assertEqual(app.greeting.calls,0)
                self.assertIsNone(app.host.audio.owner)
                self.assertEqual(app.startup_snapshot()['phase'],'ready')
                self.assertEqual(app.startup_snapshot()['code'],'greeting_cancelled')

    def test_pending_stop_or_cancel_retires_an_already_queued_explicit_retry(self):
        for action in ('stop','cancel'):
            with self.subTest(action=action):
                greeting=Greeting(error=GreetingError('greeting_missing'))
                app,_=self.app(greeting);self.ready(app);app.poll();self.drain(app)
                self.assertEqual(greeting.calls,1)
                greeting.error=None
                app.queue_desktop_control('retry',{'action':'retry_greeting','value':None})
                app.queue_command('safety_'+action,HostControl(action))
                app.poll();app.poll()
                self.assertIsNone(app._worker)
                self.assertEqual(greeting.calls,1)
                self.assertIsNone(app.host.audio.owner)

    def test_user_stop_or_cancel_between_poll_admission_and_ready_still_suppresses_first_player(self):
        for action in ('stop','cancel'):
            with self.subTest(action=action):
                app,_=self.app()
                # Deterministic stand-in for HTTP admission after poll sampled
                # flags, immediately before it admits the first greeting.
                app.browser.mark_ready=lambda:app.queue_command('late_'+action,HostControl(action))
                self.ready(app);app.poll();app.poll()
                self.assertIsNone(app._worker)
                self.assertEqual(app.greeting.calls,0)
                self.assertIsNone(app.host.audio.owner)

    def test_started_fact_survives_cancel_before_owner_can_consume_started_event(self):
        greeting=DeferredStartedGreeting();app,_=self.app(greeting);self.ready(app)
        app.poll();self.assertTrue(greeting.entered.wait(1))
        self.assertFalse(app.startup_snapshot()['greeting_played'])
        greeting.allow_started.set();self.assertTrue(greeting.started.wait(1))
        # No owner poll occurred after the player's started boundary.
        self.assertTrue(app.startup_snapshot()['greeting_played'])
        app.queue_command('cancel_before_drain',HostControl('cancel'))
        app.poll()
        self.assertTrue(app.startup_snapshot()['greeting_played'])
        greeting.release.set();self.drain(app)
        self.assertTrue(app.startup_snapshot()['greeting_played'])
        self.assertEqual(greeting.calls,1)

    def test_ready_disconnect_recovers_only_on_fresh_heartbeat_without_replaying_greeting(self):
        app,clock=self.app();self.ready(app);app.heartbeat()
        history=[{'role':'assistant','content':'Synthetic retained dialogue answer.'}]
        app.host.session.brain=SimpleNamespace(history=history)
        app.poll();self.drain(app)
        before=app.startup_snapshot()
        self.assertEqual((before['phase'],before['code']),('ready','greeting_completed'))
        self.assertEqual((before['greeting_attempts'],app.greeting.calls),(1,1))
        clock.now=6.;app.poll()
        self.assertEqual((app.startup_snapshot()['phase'],app.startup_snapshot()['code']),
                         ('error','browser_disconnected'))
        app.poll()  # Owner polling alone is not browser recovery evidence.
        self.assertEqual(app.startup_snapshot()['code'],'browser_disconnected')
        app.heartbeat();app.poll()
        state=app.startup_snapshot()
        self.assertEqual((state['phase'],state['code']),('ready','greeting_completed'))
        self.assertEqual((state['run_id'],state['greeting_attempts'],app.greeting.calls),
                         (before['run_id'],1,1))
        self.assertIs(app.host.session.brain.history,history)
        self.assertEqual(history,[{'role':'assistant','content':'Synthetic retained dialogue answer.'}])
        self.assertEqual(app.queue_command('after_reconnect',HostControl('question','Synthetic question.'))['status'],
                         'accepted')

    def test_fatal_face_timeout_is_first_cause_and_late_greeting_cannot_clear_disconnect(self):
        with self.subTest(boundary='fatal startup cause'):
            app,clock=self.app();app.heartbeat()
            clock.now=3.;app.poll()
            self.assertEqual((app.startup_snapshot()['phase'],app.startup_snapshot()['code']),
                             ('error','face_ready_timeout'))
            clock.now=6.;app.poll()
            self.assertEqual(app.startup_snapshot()['code'],'face_ready_timeout')
            self.ready(app);app.heartbeat();app.poll()
            self.assertEqual((app.startup_snapshot()['phase'],app.startup_snapshot()['code']),
                             ('error','face_ready_timeout'))
            self.assertEqual(app.greeting.calls,0)
        with self.subTest(boundary='late greeting during transient disconnect'):
            greeting=Greeting(blocked=True);app,clock=self.app(greeting)
            app.greeting_timeout=30.  # Isolate browser loss from greeting timeout.
            self.ready(app);app.heartbeat();app.poll()
            self.assertTrue(greeting.entered.wait(1));app.poll()
            clock.now=6.;app.poll()
            self.assertEqual((app.startup_snapshot()['phase'],app.startup_snapshot()['code']),
                             ('error','browser_disconnected'))
            greeting.release.set();self.drain(app)
            self.assertEqual((app.startup_snapshot()['phase'],app.startup_snapshot()['code']),
                             ('error','browser_disconnected'))
            self.assertIsNone(app.host.audio.owner)
            app.heartbeat();app.poll()
            self.assertEqual(app.startup_snapshot()['phase'],'ready')
            self.assertEqual((app.startup_snapshot()['greeting_attempts'],greeting.calls),(1,1))


if __name__=='__main__':unittest.main()
