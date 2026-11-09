"""Existing one-response meaning -> bounded settings/music actions, no live IO."""
import unittest

from bmo_ai.expression_contract import ExpressionFormatError
from bmo_ai.features.host import HostControl
from bmo_ai.features.reply import HostIntent
from test_compound_requests import CompoundRequestTests, compound, step
from test_semantic_requests import envelope
import test_full_batch_semantic_host as fixture


class PresenceMenuMeaningTests(unittest.TestCase):
    setUp = CompoundRequestTests.setUp
    ask = CompoundRequestTests.ask

    def test_current_english_korean_settings_use_existing_single_response(self):
        cases = [('Open the menu please.', 'menu', 'open_menu', None),
                 ('메뉴 열어', 'menu', 'open_menu', None),
                 ('메뉴 닫아', 'menu', 'close_menu', None),
                 ('Hide those settings.', 'menu', 'close_menu', None),
                 ('혼잣말 꺼', 'presence', 'set_presence', 'off'),
                 ('조용히 해', 'presence', 'set_presence', 'quiet'),
                 ("Don't talk to yourself for now.", 'presence', 'set_presence', 'off'),
                 ('다시 가끔 말해', 'presence', 'set_presence', 'normal'),
                 ('Say something occasionally.', 'presence', 'set_presence', 'normal'),
                 ('Please say something occasionally.', 'presence', 'set_presence', 'normal'),
                 ('BMO, say something occasionally.', 'presence', 'set_presence', 'normal'),
                 ('Choose some music for me.', 'media', 'play_random', None)]
        for text, kind, action, target in cases:
            with self.subTest(text=text):
                value = envelope(kind=kind, disposition='execute', version=5,
                    intent=step(action, target), plan={'version':1,'steps':[]})
                reply, _ = self.ask(text, value)
                self.assertEqual(reply.intent, HostIntent(action, target))
                self.assertTrue(reply.human_device_request)
                self.assertEqual(reply.request_validation, 'validated')

    def test_negative_quoted_past_future_settings_do_not_execute(self):
        for text, kind, action, target in [
                ("Don't open the menu.", 'menu', 'open_menu', None),
                ('메뉴 열지 마', 'menu', 'open_menu', None),
                ('메뉴 닫지 마', 'menu', 'close_menu', None),
                ('혼잣말 끄지 마', 'presence', 'set_presence', 'off'),
                ('혼잣말 켜지 마', 'presence', 'set_presence', 'normal'),
                ("Don't turn self-talk off.", 'presence', 'set_presence', 'off'),
                ("Don't disable occasional speech.", 'presence', 'set_presence', 'quiet'),
                ("Don't stop talking to yourself.", 'presence', 'set_presence', 'off'),
                ('Translate "Open the menu".', 'menu', 'open_menu', None),
                ('Say "Stop talking to yourself".', 'presence', 'set_presence', 'off'),
                ('Yesterday I asked for occasional self-talk.', 'presence', 'set_presence', 'normal'),
                ('Open the menu tomorrow.', 'menu', 'open_menu', None)]:
            with self.subTest(text=text):
                value = envelope(kind=kind, disposition='execute', version=5,
                    intent=step(action, target), plan={'version':1,'steps':[]})
                reply, _ = self.ask(text, value)
                self.assertIsNone(reply.intent)
                self.assertFalse(reply.human_device_request)
        with self.assertRaises(ExpressionFormatError):HostIntent('set_presence', 'always')

    def test_compound_settings_preserve_current_clause_scope(self):
        reply, _ = self.ask('Stop talking to yourself and open the menu.',
            compound(step('set_presence', 'off'), step('open_menu')))
        self.assertEqual(reply.action_plan, (HostIntent('set_presence','off'), HostIntent('open_menu')))


class PresenceMenuHostTests(unittest.TestCase):
    setUp = fixture.FullBatchSemanticHostTests.setUp
    tearDown = fixture.FullBatchSemanticHostTests.tearDown
    make_host = fixture.FullBatchSemanticHostTests.make_host
    finish = fixture.FullBatchSemanticHostTests.finish

    def test_meaning_reaches_menu_and_presence_once_without_replaying_actions(self):
        values = [envelope(kind='menu', disposition='execute', intent=step('open_menu')),
                  envelope(kind='presence', disposition='execute', intent=step('set_presence','normal'))]
        host, provider, _, played = self.make_host(values)
        self.assertTrue(host.start_question('메뉴 열어'));self.finish(host)
        self.assertEqual(host.snapshot()['ui_menu'], dict(sequence=1, open=True))
        self.assertEqual(host.modes.mode, 'conversation')
        host.control(HostControl('repeat'), 'repeat_menu_answer');self.finish(host)
        self.assertEqual(host.snapshot()['ui_menu']['sequence'],1)
        self.assertTrue(host.start_question('다시 가끔 말해'));self.finish(host)
        self.assertEqual(host.consume_presence_request(), 'normal')
        self.assertIsNone(host.consume_presence_request())
        self.assertEqual(len(provider.requests),2)
        host.execute(HostIntent('close_menu'),request_id='manual_close')
        host.execute(HostIntent('close_menu'),request_id='manual_close')
        host.poll()
        self.assertEqual(host.snapshot()['ui_menu'],dict(sequence=2,open=False))
        self.assertIsNone(host.audio.owner)

    def test_unvalidated_model_settings_are_rejected(self):
        host, _, _, _ = self.make_host([])
        for index,intent in enumerate((HostIntent('open_menu'),HostIntent('set_presence','normal'),HostIntent('play_random'))):
            self.assertEqual(host.execute(intent,request_id='model_'+str(index)).code,'current_user_action_required')
        self.assertIsNone(host.consume_presence_request())
        self.assertEqual(host.snapshot()['ui_menu']['sequence'],0)


if __name__ == '__main__':unittest.main()
