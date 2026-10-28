"""Character policy/pipeline fixtures, not real LLM decisions or audio approval."""
import json
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.character_director import CharacterDirector, CharacterPlan
from bmo_ai import audio
from bmo_ai.expressive_voice import ExpressivePolicy
from bmo_ai.features.reply import FeatureClient, feature_text_format
from bmo_ai.fish_tts import SOFT_AARON_DIRECTION
from bmo_ai.integration_profile import integration_config, policy_brain
from bmo_ai.integration_session import IntegrationSession
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


def payload(*, emotion="neutral", delivery="calm", intensity="low", reaction="none",
            gesture="none", gaze="none", source="dialogue", speech="A clean synthetic answer."):
    return dict(version=3, speech=speech, emotion=emotion, gesture=gesture, intent=None,
        reaction=reaction, character=dict(delivery_id=delivery, intensity=intensity,
                                          gaze_id=gaze, source=source))


class Provider:
    def __init__(self, replies):
        self.responses = self
        self.replies, self.requests = list(replies), []

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(status="completed", incomplete_details=None, error=None,
                               output=[], output_text=json.dumps(self.replies.pop(0)))

    def close(self):
        pass


class CharacterExpressionTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))

    def request(self, message, reply):
        provider = Provider([reply])
        view = FeatureClient(provider, expressive=True, character_life=True)
        brain = policy_brain(provider, integration_config(), expression_client=view)
        self.assertEqual(brain.ask(message), reply["speech"])
        self.assertEqual(len(provider.requests), 1)
        actual = provider.requests[0]
        self.assertEqual((actual["model"], actual["reasoning"]), ("gpt-6-luna", {"effort": "low"}))
        self.assertEqual(actual["text"]["format"]["name"], "bmo_feature_reply_v3")
        self.assertFalse(actual["store"])
        self.assertEqual(brain.history[-1]["content"], reply["speech"])
        return view.last_reply

    def test_twenty_four_fixed_contexts_use_one_reply_and_bounded_performance(self):
        # The provider outputs are explicit fixtures. This validates host policy,
        # not a paid model's ability to choose these outputs naturally.
        cases = [
            ("Tell me a fact.", payload(), "neutral", "calm", "none"),
            ("We finished our tiny project!", payload(emotion="happy", delivery="proud", intensity="medium", gesture="celebrate"), "happy", "proud", "none"),
            ("That was funny.", payload(emotion="happy", delivery="warm", intensity="medium", reaction="chuckle"), "happy", "warm", "chuckle"),
            ("What a surprise!", payload(emotion="surprised", delivery="curious", intensity="medium", reaction="gasp", gesture="shrug"), "surprised", "curious", "gasp"),
            ("장난스럽게 화내 봐", payload(emotion="sad", delivery="soft_sad", source="acting"), "angry", "theatrical_angry", "none"),
            ("우는 연기를 해봐", payload(emotion="sad", delivery="soft_sad", source="acting", reaction="sob"), "crying", "theatrical_crying", "sob"),
            ("Please pretend to cry for this scene.", payload(emotion="neutral", source="acting"), "crying", "theatrical_crying", "none"),
            ("Please act angry for a silly scene.", payload(emotion="neutral", source="acting"), "angry", "theatrical_angry", "none"),
            ("Cry for me please.", payload(emotion="crying", delivery="tearful", source="acting"), "crying", "theatrical_crying", "none"),
            ("Look playfully angry.", payload(emotion="angry", delivery="playfully_annoyed", source="acting"), "angry", "theatrical_angry", "none"),
            ("My mom died.", payload(emotion="happy", delivery="excited", reaction="chuckle", gesture="celebrate", intensity="high"), "sad", "empathetic", "none"),
            ("I'm grieving.", payload(emotion="crying", delivery="tearful", reaction="sob"), "sad", "empathetic", "none"),
            ("너무 힘들어서 이야기하고 싶어", payload(emotion="angry", delivery="playfully_annoyed", reaction="playful_puff"), "sad", "empathetic", "none"),
            ("I feel hopeless.", payload(emotion="neutral"), "neutral", "empathetic", "none"),
            ("Please do not pretend to cry.", payload(emotion="crying", delivery="theatrical_crying", source="acting", reaction="sob"), "neutral", "calm", "none"),
            ("화내지 마", payload(emotion="angry", delivery="theatrical_angry", source="acting"), "neutral", "calm", "none"),
            ("Please don't act angry.", payload(emotion="angry", delivery="theatrical_angry", source="acting"), "neutral", "calm", "none"),
            ("A moving fictional ending.", payload(emotion="crying", delivery="tearful"), "crying", "tearful", "none"),
            ("A little playful complaint.", payload(emotion="angry", delivery="playfully_annoyed"), "angry", "playfully_annoyed", "none"),
            ("What shall we make?", payload(delivery="curious", gaze="curious"), "neutral", "curious", "none"),
            ("Good morning.", payload(emotion="happy", delivery="warm", gesture="wave"), "happy", "warm", "none"),
            ("Explain that calmly.", payload(emotion="neutral", reaction="sob"), "neutral", "calm", "none"),
            ("A small smile is enough.", payload(emotion="happy", delivery="warm", reaction="chuckle", intensity="low"), "happy", "warm", "none"),
            ("Please act crying, my father died.", payload(emotion="crying", delivery="theatrical_crying", reaction="sob", source="acting"), "sad", "empathetic", "none"),
        ]
        self.assertEqual(len(cases), 24)
        for message, reply, emotion, delivery, reaction in cases:
            with self.subTest(message=message):
                result = self.request(message, reply)
                plan = result.character_plan
                self.assertEqual((result.emotion, plan.delivery_id, result.reaction), (emotion, delivery, reaction))
                self.assertEqual(plan.face_emotion, result.emotion)
                self.assertFalse(plan.reaction_id != "none" and plan.gesture_id != "none")
                if plan.sensitive_context:
                    self.assertEqual((plan.gesture_id, plan.reaction_id, plan.intensity), ("none", "none", "low"))

    def test_v3_schema_keeps_strict_clean_speech_and_allowlist_metadata(self):
        schema = feature_text_format(character_life=True)["format"]
        self.assertTrue(schema["strict"])
        self.assertFalse(schema["schema"]["properties"]["character"]["additionalProperties"])
        self.assertEqual(schema["schema"]["properties"]["version"]["enum"], [3])
        self.assertEqual(feature_text_format()["format"]["name"], "bmo_feature_reply_v1")
        self.assertEqual(feature_text_format(expressive=True)["format"]["name"], "bmo_feature_reply_v2")
        bad = payload(emotion="happy", delivery="warm")
        bad["character"]["delivery_id"] = "[private instruction]"
        reply = self.request("A fact.", bad)
        self.assertTrue(reply.degraded)
        self.assertEqual((reply.emotion, reply.character_plan.delivery_id), ("neutral", "calm"))

    def test_prior_acting_request_cannot_authorize_a_later_fact(self):
        provider = Provider([payload(source="acting"), payload(source="acting", emotion="crying", delivery="theatrical_crying")])
        view = FeatureClient(provider, character_life=True)
        brain = policy_brain(provider, integration_config(), expression_client=view)
        brain.ask("Please pretend to cry.")
        self.assertEqual(view.last_reply.emotion, "crying")
        brain.ask("What is two plus two?")
        self.assertEqual(view.last_reply.emotion, "neutral")
        self.assertEqual(len(provider.requests), 2)

    def metadata(self, plan):
        return dict(emotion=plan.face_emotion, emotion_validation="validated", reaction=plan.reaction_id,
                    character_plan=plan.public())

    def test_explicit_acting_is_not_suppressed_by_automatic_reaction_quota(self):
        director, policy = CharacterDirector(), ExpressivePolicy()
        happy = director.resolve("happy", "chuckle", metadata=dict(delivery_id="warm", intensity="medium"))
        first = policy.plan("A funny little thing.", self.metadata(happy), model="s2.1-pro-free")
        policy.completed(first)
        crying = director.resolve("crying", "sob", metadata=dict(delivery_id="tearful"), message="Please pretend to cry.")
        for _ in range(2):
            spoken = policy.plan("Oh, dear.", self.metadata(crying), model="s2.1-pro-free")
            self.assertEqual(spoken.reaction, "sob")
            self.assertIn("controlled theatrical tears", spoken.text)
            self.assertNotIn("[sad]", spoken.text)
            policy.completed(spoken)
        repeat = policy.plan("Another funny thing.", self.metadata(happy), model="s2.1-pro-free")
        self.assertEqual(repeat.reaction, "none")  # Not another automatic Hehe.

    def test_character_drama_support_does_not_change_default_model_or_legacy_policy(self):
        plan = CharacterDirector().resolve("angry", message="Playfully act angry.")
        policy = ExpressivePolicy()
        candidate = policy.plan("Tiny protest.", self.metadata(plan), model="drama-3")
        self.assertEqual(candidate.mechanism, "fish_character_drama3_v1")
        self.assertIn("theatrical protest", candidate.text)
        legacy = policy.plan("Original.", dict(emotion="angry", emotion_validation="validated"), model="drama-3")
        self.assertEqual(legacy.mechanism, "plain_fallback")
        self.assertEqual(integration_config().fish_model, "s2.1-pro-free")

    def test_voice_uses_congruent_direction_with_unchanged_identity_and_one_synthesis(self):
        seen, events = [], []
        config = integration_config()
        def synth(text, **kwargs):
            seen.append((text, kwargs)); return synthetic_wav()
        def player(_command, *, on_start, cancel_check, **_):
            cancel_check(); on_start(); cancel_check()
        voice = IntegrationVoice(config, None, emit=lambda kind, **fields: events.append((kind, fields)),
                                 fish_synthesizer=synth, player=player, expressive=True, character_life=True)
        self.addCleanup(voice.close)
        plan = CharacterDirector().resolve("sad", message="우는 연기를 해봐")
        voice.expression_metadata = self.metadata(plan)
        voice.speak("BMO can act a little scene.")
        self.assertEqual(len(seen), 1)
        text, kwargs = seen[0]
        self.assertNotIn(SOFT_AARON_DIRECTION, text)
        self.assertIn("sobbing", text)
        self.assertIn("Beemo", text)
        self.assertEqual((kwargs["model"], kwargs["reference_id"]), (config.fish_model, config.fish_voice_id))
        self.assertEqual((config.fish_delivery, config.fish_gain), ("soft", .25))
        started = next(fields for kind, fields in events if kind == "started")
        self.assertEqual((started["emotion"], started["character_plan"]["face_emotion"]), ("crying", "crying"))
        voice.repeat()
        self.assertEqual(len(seen), 1)

    def test_synthetic_and_piper_fallback_never_report_fish_audio_ready(self):
        def player(_command, *, on_start, cancel_check, **_):
            cancel_check(); on_start(); cancel_check()
        for simulate in (True, False):
            with self.subTest(simulate=simulate):
                markers = []
                def fish(*_args, **_kwargs):
                    raise audio.AudioSynthesisError("Synthetic provider failure.")
                voice = IntegrationVoice(integration_config(offline_voice=True), None,
                    simulate=simulate, emit=lambda *_a, **_kw: None, player=player,
                    fish_synthesizer=fish, piper_synthesizer=lambda *_a, **_kw: synthetic_wav(),
                    on_timing=lambda stage, code, **_kw: markers.append((stage, code)))
                self.addCleanup(voice.close)
                voice.speak("Synthetic fallback.")
                self.assertNotIn(("fish", "audio_ready"), markers)
                self.assertIn(("playback", "playback_started"), markers)


class CharacterSessionContinuityTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))
        self.allow_start, self.started, self.prepared, self.release = Event(), Event(), Event(), Event()
        def player(_command, *, on_start, cancel_check, **_):
            self.prepared.set()
            while not self.allow_start.wait(.002): cancel_check()
            cancel_check(); on_start(); self.started.set()
            while not self.release.wait(.002): cancel_check()
            cancel_check()
        self.provider = Provider([payload(emotion="sad", delivery="soft_sad", source="acting")])
        config = integration_config()
        self.voice = IntegrationVoice(config, self.provider, simulate=True, player=player,
            emit=lambda *_a, **_kw: None, expressive=True, character_life=True)
        self.session = IntegrationSession(config, self.provider, voice=self.voice,
            emotion_mode="auto", character_life=True,
            reply_client=FeatureClient(self.provider, expressive=True, character_life=True))
        self.session.set_emotion("crying"); self.session.set_emotion("auto")

    def tearDown(self):
        self.allow_start.set(); self.release.set(); self.session.close()

    def settle(self):
        deadline = time.monotonic()+3
        while self.session.busy and time.monotonic()<deadline:
            self.session.poll();time.sleep(.002)
        self.assertFalse(self.session.busy)

    def test_actual_player_start_commits_plan_and_repeat_preserves_it_without_new_request(self):
        self.assertTrue(self.session.start("장난스럽게 화내 봐"))
        self.assertTrue(self.prepared.wait(2))
        self.assertEqual(self.session.snapshot()["emotion"], "crying")
        self.assertIsNone(self.session.snapshot()["character"])
        self.allow_start.set();self.assertTrue(self.started.wait(2))
        self.assertEqual(self.session.snapshot()["emotion"], "angry")
        self.assertEqual(self.session.snapshot()["character"]["delivery_id"], "theatrical_angry")
        self.release.set();self.settle()
        before = self.session.snapshot()["character"]
        self.assertEqual(self.session.snapshot()["emotion"], "angry")
        self.assertTrue(self.session.repeat());self.settle()
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.session.snapshot()["character"], before)
        self.assertEqual(self.session.snapshot()["emotion"], "angry")

    def test_cancelled_prepared_plan_does_not_change_previous_face(self):
        self.session.start("Playfully act angry.")
        self.assertTrue(self.prepared.wait(2));self.session.cancel();self.settle()
        self.assertEqual(self.session.snapshot()["emotion"], "crying")
        self.assertIsNone(self.session.snapshot()["character"])

    def test_manual_change_retires_automatic_plan_and_reset_clears_it(self):
        self.session.start("Playfully act angry.")
        self.assertTrue(self.prepared.wait(2));self.session.set_emotion("sad")
        self.allow_start.set();self.assertTrue(self.started.wait(2))
        self.assertEqual(self.session.snapshot()["emotion"], "sad")
        self.assertIsNone(self.session.snapshot()["character"])
        self.release.set();self.settle();self.session.reset()
        self.assertEqual(self.session.snapshot()["emotion"], "neutral")
        self.assertIsNone(self.session.snapshot()["character"])

    def test_notice_restores_prior_character_on_end_repeat_cancel_but_not_reset_or_override(self):
        for action in ("ended", "cancel", "reset", "override"):
            with self.subTest(action=action):
                entered, release = Event(), Event()
                def player(_command, *, on_start, cancel_check, **_):
                    cancel_check(); on_start(); entered.set()
                    while not release.wait(.002): cancel_check()
                    cancel_check()
                provider = Provider([payload()])
                config = integration_config()
                voice = IntegrationVoice(config, provider, simulate=True, player=player,
                    emit=lambda *_a, **_kw: None, character_life=True)
                # A request-local fixed notice adapter, with no device/provider.
                voice._synthesize = lambda *_a, **_kw: (synthetic_wav(), "piper", True)
                session = IntegrationSession(config, provider, voice=voice,
                    emotion_mode="auto", character_life=True,
                    reply_client=FeatureClient(provider, character_life=True))
                prior = CharacterDirector().resolve("crying", message="Please pretend to cry.").public()
                with session._lock:
                    session._data.update(emotion="crying", emotion_validation="validated", character=prior)
                try:
                    self.assertTrue(session.start("A fact."));self.assertTrue(entered.wait(2))
                    self.assertEqual(session.snapshot()["emotion"], "neutral")
                    self.assertIsNone(session.snapshot()["character"])
                    if action == "cancel": session.cancel()
                    elif action == "reset": session.reset()
                    elif action == "override": session.set_emotion("happy");session.set_emotion("auto")
                    release.set()
                    deadline = time.monotonic()+3
                    while session.busy and time.monotonic()<deadline:
                        session.poll();time.sleep(.002)
                    self.assertFalse(session.busy)
                    expected = "neutral" if action == "reset" else "happy" if action == "override" else "crying"
                    self.assertEqual(session.snapshot()["emotion"], expected)
                    self.assertEqual(session.snapshot()["character"], prior if action in ("ended", "cancel") else None)
                    if action == "ended":
                        self.assertTrue(session.repeat())
                        deadline = time.monotonic()+3
                        while session.busy and time.monotonic()<deadline:
                            session.poll();time.sleep(.002)
                        self.assertFalse(session.busy)
                        self.assertEqual(session.snapshot()["character"], prior)
                        self.assertEqual(len(provider.requests), 1)
                finally:
                    release.set();session.close()


if __name__ == "__main__":
    unittest.main()
