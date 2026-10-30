"""Actual FeatureClient/session/voice-start field regressions; device boundaries injected."""
import json
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.features.host import FeatureHost, HostControl
from bmo_ai.features.reply import FeatureClient
from bmo_ai.integration_profile import integration_config, policy_brain
from bmo_ai.integration_voice import IntegrationVoice


def envelope(emotion="neutral", *, source="acting", delivery="calm"):
    return dict(version=3, speech="A safe field fixture answer.", emotion=emotion,
                gesture="none", intent=None, reaction="none",
                character=dict(delivery_id=delivery, intensity="medium", gaze_id="none", source=source))


class Provider:
    def __init__(self, rows):
        self.responses=self
        self.rows, self.requests=list(rows), []
    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(status="completed", incomplete_details=None, error=None,
                               output=[], output_text=json.dumps(self.rows.pop(0)))
    def close(self):pass


class FieldExpressionFlowTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.private_credentials.resolve_credentials", "bmo_ai.fish_tts.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("No devices, paid API or credentials")))
        self.host=None
        self.prepared, self.begin, self.played, self.release=Event(), Event(), Event(), Event()

    def tearDown(self):
        self.begin.set();self.release.set()
        if self.host is not None:self.host.close()

    def make_host(self, rows):
        provider=Provider(rows);config=integration_config()
        def player(_command, *, on_start, cancel_check, **_kw):
            self.prepared.set();deadline=time.monotonic()+4
            while not self.begin.wait(.002):
                cancel_check()
                if time.monotonic()>deadline:raise AssertionError("Start gate exceeded")
            on_start();self.played.set()
            while not self.release.wait(.002):
                cancel_check()
                if time.monotonic()>deadline:raise AssertionError("Player gate exceeded")
            cancel_check()
        voice=IntegrationVoice(config, provider, simulate=True, character_life=True, player=player,
                               emit=lambda *_a, **_kw:None)
        self.host=FeatureHost(config=config, client=provider, voice=voice, character_life=True)
        return provider

    def pump(self, predicate):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            self.host.heartbeat();self.host.poll()
            if predicate():return
            time.sleep(.002)
        self.fail("Production expression path did not reach expected boundary")

    def test_field_request_traces_requested_model_resolved_then_actual_emitted_at_player_start(self):
        self.make_host([envelope()])
        self.host.session.set_emotion("crying");self.host.session.set_emotion("auto")
        self.assertTrue(self.host.start_question("express a mad or angry?"))
        self.assertTrue(self.prepared.wait(2))
        state=self.host.session.snapshot();trace=state["expression_trace"]
        self.assertEqual(state["emotion"],"crying")
        self.assertEqual((trace["requested_emotion"],trace["model_emotion"],trace["resolved_emotion"]),
                         ("angry","neutral","angry"))
        self.assertIsNone(trace["emitted_emotion"])
        self.assertEqual(trace["emission_status"],"staged")
        self.begin.set();self.assertTrue(self.played.wait(2))
        self.pump(lambda:self.host.session.snapshot()["emotion"]=="angry")
        state=self.host.face_snapshot()
        self.assertEqual(state["expression_trace"]["emitted_emotion"],"angry")
        self.assertEqual(state["character"]["delivery_id"],"theatrical_angry")
        self.assertNotIn("express a mad",json.dumps(state["expression_trace"]))
        self.assertFalse(state["expression_trace"]["contains_text"])
        # Returned nested metadata cannot mutate the retained session trace.
        state["expression_trace"]["resolved_emotion"]="happy"
        self.assertEqual(self.host.session.snapshot()["expression_trace"]["resolved_emotion"],"angry")

    def test_korean_sad_acting_traverses_same_voice_and_session_without_becoming_crying(self):
        provider=self.make_host([envelope()])
        self.begin.set();self.release.set()
        self.assertTrue(self.host.start_question("슬픈척 해봐"))
        self.pump(lambda:not self.host.session.busy)
        state=self.host.session.snapshot()
        self.assertEqual(state["emotion"],"sad")
        self.assertEqual(state["character"]["delivery_id"],"soft_sad")
        self.assertEqual(state["expression_trace"]["emitted_emotion"],"sad")
        self.assertEqual(len(provider.requests),1)

    def test_get_angry_reaches_player_started_face_and_next_fact_resets(self):
        provider=self.make_host([envelope('angry',delivery='theatrical_angry'),envelope()])
        self.begin.set();self.release.set()
        self.assertTrue(self.host.start_question('Get angry.'))
        self.pump(lambda:not self.host.session.busy)
        state=self.host.face_snapshot();trace=state['expression_trace']
        self.assertEqual((trace['requested_emotion'],trace['model_emotion'],
                          trace['resolved_emotion'],trace['emitted_emotion']),
                         ('angry','angry','angry','angry'))
        self.assertEqual(state['character']['delivery_id'],'theatrical_angry')
        self.assertTrue(state['character']['explicit_acting'])
        self.assertTrue(self.host.start_question('What is two plus two?'))
        self.pump(lambda:not self.host.session.busy)
        state=self.host.session.snapshot()
        self.assertEqual(state['emotion'],'neutral')
        self.assertIsNone(state['expression_trace']['requested_emotion'])
        self.assertEqual(len(provider.requests),2)

    def test_repeat_retains_expression_and_next_fact_does_not_reuse_acting_permission(self):
        provider=self.make_host([envelope(),envelope("angry",delivery="theatrical_angry")])
        self.begin.set();self.release.set()
        self.assertTrue(self.host.start_question("express a mad or angry?"))
        self.pump(lambda:not self.host.session.busy)
        receipt=self.host.control(HostControl("repeat"),request="repeatFieldExpression")
        self.assertEqual(receipt.code,"repeat_started")
        self.pump(lambda:not self.host.session.busy)
        state=self.host.session.snapshot()
        self.assertEqual(state["emotion"],"angry")
        self.assertTrue(state["expression_trace"]["replayed"])
        self.assertEqual(len(provider.requests),1)
        self.assertTrue(self.host.start_question("What is two plus two?"))
        self.pump(lambda:not self.host.session.busy)
        state=self.host.session.snapshot()
        self.assertEqual(state["emotion"],"neutral")
        self.assertIsNone(state["expression_trace"]["requested_emotion"])
        self.assertEqual((state["expression_trace"]["model_emotion"],state["expression_trace"]["resolved_emotion"]),
                         ("angry","neutral"))
        self.assertEqual(len(provider.requests),2)

    def test_cancel_before_player_start_preserves_display_and_does_not_claim_emission(self):
        self.make_host([envelope()])
        self.host.session.set_emotion("sad");self.host.session.set_emotion("auto")
        self.assertTrue(self.host.start_question("express a mad or angry?"))
        self.assertTrue(self.prepared.wait(2))
        self.host.session.cancel()
        self.begin.set();self.release.set()
        self.pump(lambda:not self.host.session.busy)
        state=self.host.session.snapshot()
        self.assertEqual(state["emotion"],"sad")
        self.assertIsNone(state["expression_trace"]["emitted_emotion"])
        self.assertEqual(state["expression_trace"]["emission_status"],"cancelled")

    def test_source_label_alone_neither_erases_natural_emotion_nor_grants_theatrical_output(self):
        rows=[envelope("happy",delivery="excited"),envelope("sad",delivery="soft_sad"),
              envelope("surprised",delivery="curious"),envelope("crying",delivery="theatrical_crying")]
        provider=Provider(rows);view=FeatureClient(provider,character_life=True)
        brain=policy_brain(provider,integration_config(),expression_client=view)
        for message,emotion,delivery in (("I passed the exam!","happy","warm"),
            ("My day was difficult.","sad","soft_sad"),("Something unexpected happened.","surprised","curious"),
            ("What is two plus two?","neutral","calm")):
            brain.ask(message)
            plan=view.last_reply.character_plan
            self.assertEqual((plan.face_emotion,plan.delivery_id),(emotion,delivery))
            self.assertEqual((plan.intensity,plan.reaction_id,plan.gesture_id),("low","none","none"))
            self.assertFalse(plan.explicit_acting)
        self.assertEqual(len(provider.requests),4)

    def test_cancelled_replacement_cannot_overwrite_trace_for_retained_repeat_audio(self):
        provider=self.make_host([envelope(),envelope()])
        self.begin.set();self.release.set()
        self.assertTrue(self.host.start_question("express a mad or angry?"))
        self.pump(lambda:not self.host.session.busy)
        self.begin.clear();self.release.clear();self.prepared.clear()
        self.assertTrue(self.host.start_question("슬픈척 해봐"))
        self.assertTrue(self.prepared.wait(2))
        self.assertEqual(self.host.session.snapshot()["expression_trace"]["resolved_emotion"],"sad")
        self.host.session.cancel();self.begin.set();self.release.set()
        self.pump(lambda:not self.host.session.busy)
        self.assertEqual(self.host.control(HostControl("repeat"),request="repeatAfterFieldCancel").code,"repeat_started")
        self.pump(lambda:not self.host.session.busy)
        trace=self.host.session.snapshot()["expression_trace"]
        self.assertEqual((trace["requested_emotion"],trace["resolved_emotion"],trace["emitted_emotion"]),
                         ("angry","angry","angry"))
        self.assertTrue(trace["replayed"])
        self.assertEqual(len(provider.requests),2)

    def test_user_observed_korean_comfort_stays_sad_without_acting_permission(self):
        provider=self.make_host([envelope("sad",source="dialogue",delivery="soft_sad")])
        self.begin.set();self.release.set()
        self.assertTrue(self.host.start_question("나 오늘 힘들었어"))
        self.pump(lambda:not self.host.session.busy)
        state=self.host.session.snapshot()
        self.assertEqual((state["emotion"],state["character"]["delivery_id"]),("sad","soft_sad"))
        self.assertFalse(state["character"]["explicit_acting"])
        self.assertIsNone(state["expression_trace"]["requested_emotion"])
        self.assertEqual(len(provider.requests),1)

    def test_degraded_metadata_keeps_actual_model_enum_visible_without_authorizing_output(self):
        row=envelope("happy");row["character"]["delivery_id"]="untrusted delivery"
        provider=Provider([row]);view=FeatureClient(provider,character_life=True)
        brain=policy_brain(provider,integration_config(),expression_client=view)
        brain.ask("A normal question.")
        trace=view.last_resolution
        self.assertEqual((trace["model_emotion"],trace["resolved_emotion"],trace["validation"]),
                         ("happy","neutral","degraded"))
        trace["resolved_emotion"]="crying"
        self.assertEqual(view.last_resolution["resolved_emotion"],"neutral")
        view.reset();self.assertIsNone(view.last_resolution)


if __name__=="__main__":unittest.main()
