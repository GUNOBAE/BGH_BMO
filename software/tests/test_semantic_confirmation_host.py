"""Real Host confirmation ownership, with only provider/player boundaries injected."""
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.host import FeatureHost
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from test_semantic_requests import Provider, envelope


class Clock:
    def __init__(self):self.offset=0.
    def __call__(self):return time.monotonic()+self.offset


class SemanticConfirmationHostTests(unittest.TestCase):
    def setUp(self):
        self.hosts=[]
        for name in ('socket.socket.connect','subprocess.Popen','bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials','bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(name,side_effect=AssertionError('Actual IO forbidden')))

    def tearDown(self):
        for host in self.hosts:host.close()

    def host(self, replies):
        provider=Provider(replies);config=integration_config();clock=Clock()
        def player(_command,*,on_start,cancel_check,**_kwargs):
            cancel_check();on_start();cancel_check()
        voice=IntegrationVoice(config,provider,simulate=True,player=player,
            emit=lambda *_a,**_kw:None,expressive=True,character_life=True)
        host=FeatureHost(config=config,client=provider,voice=voice,clock=clock,
            runtime=RuntimePolicy(simulate=True),character_life=True)
        self.hosts.append(host)
        return host,provider,clock

    def finish(self, host):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            host.poll()
            if not host.session.busy and host.audio.owner is None:return
            time.sleep(.002)
        self.fail('Host did not complete the current conversation')

    def ask(self,host,message):
        self.assertTrue(host.start_question(message))
        self.finish(host)

    def clarify(self, more):
        host,provider,clock=self.host([envelope(kind='photo',disposition='clarify',
            speech='Would you like me to take one now?'),*more])
        self.ask(host,'Could you capture it?')
        pending=dict(host._pending_confirmation)
        self.assertEqual(pending['kind'],'photo')
        self.assertEqual(pending['generation'],host.modes.generation)
        self.assertFalse(host.camera.snapshot()['on'])
        return host,provider,clock,pending

    def test_current_confirmation_executes_one_preparation_only_with_matching_host_token(self):
        host,provider,clock,pending=self.clarify([envelope(kind='photo',disposition='execute')])
        provider.replies[0]['request']['confirmation_token']=pending['token']
        self.ask(host,"Yes, I'd like one please.")
        self.assertEqual(host.modes.mode,'photo')
        self.assertEqual(host.snapshot()['photo_phase'],'preparing')
        self.assertIsNone(host._pending_confirmation)
        self.assertIsNotNone(host._pending_photo)
        self.assertIn(pending['token'],provider.requests[-1]['instructions'])
        self.assertEqual(len(provider.requests),2)
        self.assertIsNone(host.snapshot()['photo'])  # No preview ACK, no capture/success.

    def test_expired_or_cancelled_confirmation_does_not_authorize_yes(self):
        for cancel in (False,True):
            with self.subTest(cancel=cancel):
                host,provider,clock,pending=self.clarify([envelope(kind='photo',disposition='execute')])
                provider.replies[0]['request']['confirmation_token']=pending['token']
                if cancel:host.cancel_conversation()
                else:clock.offset+=45.1
                self.ask(host,'Yes.')
                self.assertEqual(host.modes.mode,'conversation')
                self.assertFalse(host.camera.snapshot()['on'])
                self.assertIsNone(host._pending_photo)
                self.assertEqual(host.session.expression_client.last_reply.request_validation,'confirmation_missing')

    def test_pending_scope_is_consumed_by_exactly_the_next_user_turn(self):
        host,provider,clock,pending=self.clarify([envelope(),envelope(kind='photo',disposition='execute')])
        self.ask(host,'What is two plus two?')
        self.assertIsNone(host._pending_confirmation)
        provider.replies[0]['request']['confirmation_token']=pending['token']
        self.ask(host,'Yes.')
        self.assertFalse(host.camera.snapshot()['on'])
        self.assertEqual(host.modes.mode,'conversation')
        self.assertEqual(len(provider.requests),3)

    def test_mode_generation_change_invalidates_pending_permission(self):
        host,provider,clock,pending=self.clarify([envelope(kind='photo',disposition='execute')])
        provider.replies[0]['request']['confirmation_token']=pending['token']
        host._transition('conversation',force=True)
        self.ask(host,'Yes.')
        self.assertFalse(host.camera.snapshot()['on'])
        self.assertEqual(host.modes.mode,'conversation')

    def test_late_finished_clarification_cannot_rearm_confirmation_in_a_new_generation(self):
        host,provider,clock,pending=self.clarify([])
        turn=host.session.worker.turn
        turn_id=f'{host.session.session_id}:{turn}'
        host._pending_confirmation=None
        host._proposals[turn_id]=dict(gesture='none',intent=None,ended=True,
            request_interpretation={'kind':'photo','disposition':'clarify'})
        host._turn_generations[turn_id]=host.modes.generation
        host._transition('conversation',force=True)
        # The worker completion boundary is delayed; the actual Host must reject
        # its old-generation proposal rather than re-stamping current authority.
        with patch.object(host.session,'poll',return_value=[SimpleNamespace(kind='finished',code='ok',turn=turn)]):
            host.poll()
        self.assertIsNone(host._pending_confirmation)
        self.assertFalse(host.camera.snapshot()['on'])


if __name__=='__main__':unittest.main()
