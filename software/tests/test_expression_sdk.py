"""Expression/brain integration through the real SDK and offline HTTP only.

Fixtures exercise transport and routing, not live Luna schema support, expression
selection quality, audio synthesis, or device playback.
"""
import importlib.util
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.app import ConversationApp
from bmo_ai.audio import AudioSynthesisError
from bmo_ai.cloud import CloudError, call_cloud, cloud_error
from bmo_ai.core import BMOBrain, BrainResponseError, LANGUAGE_INSTRUCTIONS, SYSTEM_PROMPT
from bmo_ai.expression_contract import (
    EXPRESSION_INSTRUCTIONS, FACE_EMOTIONS, ExpressionClient,
    ExpressionFormatError, ExpressionReply,
)
from bmo_ai.integration_profile import integration_config, load_voice_policy, policy_brain


HAS_SDK = (importlib.util.find_spec("openai") is not None
           and importlib.util.find_spec("httpx") is not None)


def envelope(speech="That is wonderful news!", emotion="happy"):
    return json.dumps({"speech": speech, "emotion": emotion}, ensure_ascii=False)


def sdk_response(text, *, status="completed", content=None):
    return {
        "id": "resp_expression_fixture", "object": "response", "created_at": 0,
        "model": "gpt-6-luna", "status": status, "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [{"id": "msg_expression_fixture", "type": "message", "role": "assistant",
                    "status": status, "content": content if content is not None else [
                        {"type": "output_text", "text": text, "annotations": []},
                    ]}],
        "parallel_tool_calls": False, "tools": [], "tool_choice": "auto",
        "max_output_tokens": 320, "store": False,
    }


class ExpressionCloudTests(unittest.TestCase):
    def test_expression_error_is_fixed_nonretryable_and_called_once(self):
        error = ExpressionFormatError()
        error.args = ("private response body must stay hidden",)
        operation = Mock(side_effect=error)
        with self.assertRaises(CloudError) as caught:
            call_cloud(operation)
        self.assertEqual(caught.exception.code, "expression_format")
        self.assertEqual(str(caught.exception), "The reply did not match the expression contract.")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(operation.call_count, 1)
        self.assertNotIn("private", str(caught.exception))

    def test_arbitrary_valueerror_or_spoofed_code_remains_generic_cloud_error(self):
        for error in (ValueError("private response"), RuntimeError("private response")):
            error.code = "expression_format"
            with self.subTest(error_type=type(error).__name__):
                result = cloud_error(error)
                self.assertEqual(result.code, "cloud")
                self.assertFalse(result.retryable)
                self.assertNotIn("private", str(result))

    def test_optional_brain_view_is_explicit_and_nonowning(self):
        raw = SimpleNamespace(responses=SimpleNamespace(create=Mock()))
        view = SimpleNamespace(responses=SimpleNamespace(create=Mock()))
        config = integration_config()
        self.assertIs(policy_brain(raw, config).client, raw)
        self.assertIs(policy_brain(raw, config, expression_client=view).client, view)
        self.assertFalse(raw.responses.create.called)
        self.assertFalse(view.responses.create.called)


@unittest.skipUnless(HAS_SDK, "Expression SDK tests require the isolated requirements environment")
class ExpressionSDKTests(unittest.TestCase):
    def wired(self, payloads, *, baseline=False, **config_overrides):
        import httpx
        from openai import OpenAI
        queue = list(payloads)
        requests = []

        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            self.assertEqual(request.method, "POST")
            requests.append(json.loads(request.read()))
            self.assertTrue(queue, "Unexpected extra request; no classification/repair call is allowed")
            result = queue.pop(0)
            status, body = result if isinstance(result, tuple) else (200, result)
            return httpx.Response(status, json=body)

        client = OpenAI(
            api_key="offline-expression-fixture", base_url="https://api.openai.com/v1",
            timeout=3, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.addCleanup(client.close)
        delegated_patch = patch.object(client.responses, "create", wraps=client.responses.create)
        delegated = delegated_patch.start()
        self.addCleanup(delegated_patch.stop)
        view = ExpressionClient(client)
        config = integration_config(baseline=baseline, **config_overrides)
        brain = policy_brain(client, config, expression_client=view)
        return SimpleNamespace(client=client, view=view, config=config, brain=brain,
                               delegated=delegated, requests=requests)

    def test_strict_text_format_serializes_with_approved_policy_and_one_delegate(self):
        fixture = self.wired([sdk_response(envelope())])
        self.assertEqual(fixture.brain.ask("I passed my exam."), "That is wonderful news!")
        self.assertEqual(fixture.delegated.call_count, 1)
        self.assertEqual(len(fixture.requests), 1)
        request = fixture.requests[0]
        self.assertEqual((request["model"], request["reasoning"], request["max_output_tokens"]),
                         ("gpt-6-luna", {"effort": "low"}, 320))
        self.assertFalse(request["store"])
        self.assertTrue(request["instructions"].startswith(load_voice_policy()))
        self.assertIn("Trusted host language policy: english_voice", request["instructions"])
        self.assertNotIn(LANGUAGE_INSTRUCTIONS["en"], request["instructions"])
        self.assertIn(EXPRESSION_INSTRUCTIONS, request["instructions"])
        text_format = request["text"]["format"]
        self.assertEqual(text_format["type"], "json_schema")
        self.assertTrue(text_format["strict"])
        schema = text_format["schema"]
        self.assertEqual(set(schema["properties"]), {"speech", "emotion"})
        self.assertEqual(schema["properties"]["emotion"]["enum"], list(FACE_EMOTIONS))
        self.assertEqual(set(schema["required"]), {"speech", "emotion"})
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("response_format", request)
        self.assertNotIn("tools", request)
        self.assertEqual(fixture.view.last_reply, ExpressionReply("That is wonderful news!", "happy"))

    def test_two_turns_keep_only_clean_speech_in_history_and_next_request(self):
        fixture = self.wired([
            sdk_response(envelope("I am sorry that was hard.", "sad")),
            sdk_response(envelope("Your friend arriving is good news!", "happy")),
        ])
        first = fixture.brain.ask("It was a hard morning.")
        self.assertEqual(fixture.view.last_reply.emotion, "sad")
        second = fixture.brain.ask("My friend is visiting now.")
        self.assertEqual(fixture.view.last_reply.emotion, "happy")
        self.assertEqual(fixture.requests[1]["input"], [
            {"role": "user", "content": "It was a hard morning."},
            {"role": "assistant", "content": first},
            {"role": "user", "content": "My friend is visiting now."},
        ])
        self.assertEqual([m["content"] for m in fixture.brain.history if m["role"] == "assistant"],
                         [first, second])
        for item in fixture.brain.history:
            self.assertNotIn('"speech"', item["content"])
            self.assertNotIn('"emotion"', item["content"])
        self.assertEqual(fixture.delegated.call_count, 2)

    def test_fish_and_same_answer_piper_fallback_receive_clean_speech_only(self):
        speech = "That is wonderful news!"
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                fixture = self.wired([sdk_response(envelope(speech))], offline_voice=fallback)
                app = ConversationApp(fixture.brain, fixture.client, fixture.config)
                self.assertIs(app.client, fixture.client)
                self.assertFalse(hasattr(fixture.view, "audio"))
                self.assertFalse(hasattr(fixture.view, "close"))
                answer = app.answer("I passed my exam.")
                with patch("bmo_ai.fish_tts.speak") as fish, \
                     patch("bmo_ai.local_piper.speak") as piper:
                    if fallback:
                        fish.side_effect = AudioSynthesisError("Fixture failure", "fixture")
                    app.playback(answer)
                self.assertEqual(fish.call_args.args[0], speech)
                self.assertEqual(fish.call_args.kwargs["delivery"], "soft")
                self.assertEqual(fish.call_args.kwargs["name_mode"], "beemo")
                self.assertEqual(fish.call_args.kwargs["gain"], 0.25)
                if fallback:
                    self.assertEqual(piper.call_args.args[0], speech)
                    self.assertEqual(piper.call_args.kwargs["gain"], 0.25)
                else:
                    piper.assert_not_called()
                self.assertEqual(fixture.delegated.call_count, 1)
                self.assertEqual(len(fixture.requests), 1)

    def test_missing_invalid_and_unknown_emotion_degrade_to_neutral_without_retry(self):
        values = [{"speech": "A calm answer."}]
        values.extend({"speech": "A calm answer.", "emotion": value}
                      for value in (None, False, [], "excited", "HAPPY"))
        fixture = self.wired([sdk_response(json.dumps(value)) for value in values])
        for index in range(len(values)):
            self.assertEqual(fixture.brain.ask("Question " + str(index)), "A calm answer.")
            self.assertEqual(fixture.view.last_reply, ExpressionReply("A calm answer.", "neutral", True))
        self.assertEqual(fixture.delegated.call_count, len(values))
        self.assertEqual(len(fixture.requests), len(values))

    def test_malformed_unsafe_and_extra_fields_reject_with_clean_history_then_recover(self):
        payloads = (
            '{"speech":"private partial',
            envelope("[laugh] private instruction"),
            envelope("<speak>private content</speak>"),
            '{"speech":"private payload","emotion":"happy","action":"move"}',
            '{"speech":"one","speech":"private duplicate","emotion":"happy"}',
        )
        for payload in payloads:
            with self.subTest(kind=payload[:14]):
                fixture = self.wired([
                    sdk_response(envelope("First answer.", "happy")),
                    sdk_response(payload),
                    sdk_response(envelope("The answer is four.", "neutral")),
                ])
                fixture.brain.ask("First question.")
                saved = list(fixture.brain.history)
                app = ConversationApp(fixture.brain, fixture.client, fixture.config)
                with patch("bmo_ai.fish_tts.speak") as fish, \
                     patch("bmo_ai.local_piper.speak") as piper, \
                     self.assertRaises(CloudError) as caught:
                    app.playback(app.answer("Rejected question."))
                self.assertEqual(caught.exception.code, "expression_format")
                self.assertFalse(caught.exception.retryable)
                self.assertNotIn("private", str(caught.exception))
                self.assertEqual(fixture.brain.history, saved)
                self.assertIsNone(fixture.view.last_reply)
                fish.assert_not_called()
                piper.assert_not_called()
                self.assertEqual(fixture.brain.ask("Next question."), "The answer is four.")
                self.assertEqual(fixture.view.last_reply.emotion, "neutral")
                self.assertNotIn({"role": "user", "content": "Rejected question."},
                                 fixture.requests[2]["input"])
                self.assertEqual(fixture.delegated.call_count, 3)
                self.assertEqual(len(fixture.requests), 3)

    def test_refusal_even_alongside_valid_json_has_no_speech_or_metadata(self):
        content = [
            {"type": "refusal", "refusal": "private refusal body"},
            {"type": "output_text", "text": envelope(), "annotations": []},
        ]
        fixture = self.wired([sdk_response("", content=content)])
        with self.assertRaises(CloudError) as caught:
            fixture.brain.ask("Question.")
        self.assertEqual(caught.exception.code, "expression_format")
        self.assertNotIn("private", str(caught.exception))
        self.assertIsNone(fixture.view.last_reply)
        self.assertEqual(fixture.brain.history, [])
        self.assertEqual(fixture.delegated.call_count, 1)

    def test_incomplete_partial_response_preserves_history_and_next_turn_recovers(self):
        fixture = self.wired([
            sdk_response(envelope("First answer.", "happy")),
            sdk_response('{"speech":"private partial', status="incomplete"),
            sdk_response(envelope("We can start again.", "neutral")),
        ])
        fixture.brain.ask("First question.")
        saved = list(fixture.brain.history)
        with self.assertRaises(BrainResponseError) as caught:
            fixture.brain.ask("Incomplete question.")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(fixture.brain.history, saved)
        self.assertIsNone(fixture.view.last_reply)
        self.assertEqual(fixture.brain.ask("Next question."), "We can start again.")
        self.assertEqual(fixture.view.last_reply.emotion, "neutral")
        self.assertEqual(fixture.delegated.call_count, 3)

    def test_schema_rejection_is_visible_and_does_not_switch_models_or_repair(self):
        fixture = self.wired([(400, {"error": {
            "message": "private schema rejection", "type": "invalid_request_error",
            "param": "text.format", "code": "unsupported_parameter",
        }})])
        with self.assertRaises(CloudError) as caught:
            fixture.brain.ask("Question.")
        self.assertEqual(caught.exception.code, "request")
        self.assertNotIn("private", str(caught.exception))
        self.assertIsNone(fixture.view.last_reply)
        self.assertEqual(fixture.brain.history, [])
        self.assertEqual(fixture.delegated.call_count, 1)
        self.assertEqual([r["model"] for r in fixture.requests], ["gpt-6-luna"])

    def test_baseline_keeps_model_and_omits_luna_reasoning_with_expression_view(self):
        fixture = self.wired([sdk_response(envelope("A simple answer.", "neutral"))], baseline=True)
        self.assertEqual(fixture.brain.ask("Question."), "A simple answer.")
        self.assertEqual(fixture.requests[0]["model"], "gpt-4.1-mini")
        self.assertNotIn("reasoning", fixture.requests[0])
        self.assertEqual(fixture.requests[0]["text"]["format"]["type"], "json_schema")
        self.assertEqual(fixture.delegated.call_count, 1)

    def test_manual_policy_and_general_brain_do_not_add_expression_format(self):
        fixture = self.wired([sdk_response("A plain answer."), sdk_response("Hello.")])
        manual = policy_brain(fixture.client, fixture.config)
        self.assertEqual(manual.ask("Question."), "A plain answer.")
        self.assertIsNone(fixture.view.last_reply)
        self.assertNotIn("text", fixture.requests[0])
        self.assertNotIn(EXPRESSION_INSTRUCTIONS, fixture.requests[0]["instructions"])
        ordinary = BMOBrain(fixture.client, model="gpt-4.1-mini")
        self.assertEqual(ordinary.ask("Hi."), "Hello.")
        self.assertEqual(fixture.requests[1]["instructions"],
                         SYSTEM_PROMPT + "\n" + LANGUAGE_INSTRUCTIONS["en"])
        self.assertEqual(fixture.requests[1]["max_output_tokens"], 180)
        self.assertNotIn("text", fixture.requests[1])
        self.assertNotIn("reasoning", fixture.requests[1])


if __name__ == "__main__":
    unittest.main()
