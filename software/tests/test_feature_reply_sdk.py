"""Real SDK serialization through offline MockTransport; no paid/network IO."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import OpenAI

from bmo_ai.cloud import CloudError
from bmo_ai.core import BrainResponseError
from bmo_ai.expression_contract import EXPRESSION_INSTRUCTIONS, ExpressionClient
from bmo_ai.features.reply import (FEATURE_INSTRUCTIONS, FeatureClient, HostIntent,
                                   decode_feature_reply)
from bmo_ai.integration_profile import integration_config, load_voice_policy, policy_brain
from test_expression_sdk import sdk_response


def feature_payload(**changes):
    return {"version": 1, "speech": "A clean synthetic answer.", "emotion": "crying",
            "gesture": "none", "intent": None} | changes


def feature_envelope(**changes):
    return json.dumps(feature_payload(**changes))


class FeatureReplySDKTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))

    def wired(self, payloads):
        queue, requests = list(payloads), []

        def handler(request):
            self.assertEqual((request.method, request.url.path), ("POST", "/v1/responses"))
            requests.append(json.loads(request.read()))
            self.assertTrue(queue, "No repair/classification request is allowed")
            value = queue.pop(0)
            status, value = value if isinstance(value, tuple) else (200, value)
            return httpx.Response(status, json=value)

        client = OpenAI(api_key="offline-feature-fixture", base_url="https://api.openai.com/v1",
                        max_retries=0, timeout=2,
                        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        self.addCleanup(client.close)
        view = FeatureClient(client)
        config = integration_config()
        brain = policy_brain(client, config, expression_client=view)
        return SimpleNamespace(client=client, view=view, brain=brain, requests=requests)

    def test_strict_version_one_schema_is_one_request_without_conflicting_two_field_instructions(self):
        fixture = self.wired([sdk_response(feature_envelope(intent={"name": "open_game_menu", "target": None}))])
        self.assertEqual(fixture.brain.ask("Show the game menu."), "A clean synthetic answer.")
        self.assertEqual(len(fixture.requests), 1)
        request = fixture.requests[0]
        self.assertEqual((request["model"], request["reasoning"], request["max_output_tokens"]),
                         ("gpt-6-luna", {"effort": "low"}, 320))
        self.assertFalse(request["store"])
        self.assertTrue(request["instructions"].startswith(load_voice_policy()))
        self.assertIn(FEATURE_INSTRUCTIONS, request["instructions"])
        self.assertNotIn(EXPRESSION_INSTRUCTIONS, request["instructions"])
        schema = request["text"]["format"]
        self.assertTrue(schema["strict"])
        self.assertEqual(schema["name"], "bmo_feature_reply_v1")
        self.assertEqual(set(schema["schema"]["required"]), {"version", "speech", "emotion", "gesture", "intent"})
        self.assertFalse(schema["schema"]["additionalProperties"])
        self.assertNotIn("tools", request)
        self.assertEqual(fixture.view.last_reply.intent, HostIntent("open_game_menu"))
        self.assertFalse(hasattr(fixture.view, "audio"))
        self.assertFalse(hasattr(fixture.view, "close"))

    def test_two_turns_retain_only_clean_speech_and_never_intent_metadata_in_history(self):
        fixture = self.wired([sdk_response(feature_envelope(speech="First clean answer.", emotion="sad")),
                             sdk_response(feature_envelope(speech="Second clean answer.", emotion="happy",
                                                          intent={"name": "play_song", "target": "fixture_song_a"}))])
        fixture.brain.ask("First question."); fixture.brain.ask("Second question.")
        self.assertEqual(len(fixture.requests), 2)
        self.assertEqual(fixture.requests[1]["input"], [
            {"role": "user", "content": "First question."},
            {"role": "assistant", "content": "First clean answer."},
            {"role": "user", "content": "Second question."}])
        self.assertEqual([m["content"] for m in fixture.brain.history if m["role"] == "assistant"],
                         ["First clean answer.", "Second clean answer."])
        self.assertEqual(fixture.view.last_reply.intent, HostIntent("play_song", "fixture_song_a"))

    def test_bad_version_gesture_intent_fields_targets_and_multiple_intents_fail_without_repair(self):
        values = [feature_payload(**changes) for changes in (
            {"version": True}, {"version": 2}, {"gesture": "dance"}, {"gesture": []},
            {"intent": [{"name": "open_game_menu", "target": None}]},
            {"intent": {"name": "take_photo", "target": "unexpected"}},
            {"intent": {"name": "launch_game", "target": "../private"}},
            {"intent": {"name": "play_song", "target": "https://private.invalid"}},
            {"intent": {"name": "not_supported", "target": None}},
            {"intent": {"name": "launch_game", "target": "fixture_game_a", "command": "private"}},
            {"intent": {"name": "arm_gesture", "target": "wave"}, "gesture": "celebrate"},
        )]
        values.append(feature_payload() | {"extra": "private"})
        for value in values:
            with self.subTest(fields=tuple(value)):
                fixture = self.wired([sdk_response(json.dumps(value))])
                with self.assertRaises(CloudError) as error:
                    fixture.brain.ask("Fixture question.")
                self.assertEqual(error.exception.code, "expression_format")
                self.assertNotIn("private", str(error.exception))
                self.assertEqual(fixture.brain.history, [])
                self.assertIsNone(fixture.view.last_reply)
                self.assertEqual(len(fixture.requests), 1)

    def test_tags_markup_duplicates_and_partial_json_never_become_spoken_fallback(self):
        values = [feature_envelope(speech=value) for value in ("[laugh] private", "<speak>private</speak>", "private {json}")]
        values.extend(('{"version":1,"speech":"private partial',
                       feature_envelope().replace('"version": 1', '"version":1,"version":1')))
        for raw in values:
            with self.subTest(raw_kind=raw[:12]):
                fixture = self.wired([sdk_response(raw), sdk_response(feature_envelope(speech="Recovered."))])
                with self.assertRaises(CloudError) as error:
                    fixture.brain.ask("Bad fixture.")
                self.assertEqual(error.exception.code, "expression_format")
                self.assertNotIn("private", str(error.exception))
                self.assertEqual(fixture.brain.ask("Good fixture."), "Recovered.")
                self.assertEqual(len(fixture.requests), 2)
                self.assertEqual(len(fixture.brain.history), 2)

    def test_refusal_and_incomplete_clear_provisional_metadata_and_preserve_prior_history(self):
        refusal = sdk_response("", content=[{"type": "refusal", "refusal": "private refusal"},
            {"type": "output_text", "text": feature_envelope(), "annotations": []}])
        incomplete = sdk_response('{"version":1,"speech":"private partial', status="incomplete")
        for response, error_type in ((refusal, CloudError), (incomplete, BrainResponseError)):
            with self.subTest(status=response["status"]):
                fixture = self.wired([sdk_response(feature_envelope(speech="First.")), response])
                fixture.brain.ask("First question.")
                previous = list(fixture.brain.history)
                with self.assertRaises(error_type) as error:
                    fixture.brain.ask("Rejected question.")
                self.assertNotIn("private", str(error.exception))
                self.assertIsNone(fixture.view.last_reply)
                self.assertEqual(fixture.brain.history, previous)
                self.assertEqual(len(fixture.requests), 2)

    def test_schema_rejection_does_not_switch_model_or_retry_semantic_request(self):
        fixture = self.wired([(400, {"error": {"message": "private schema failure", "type": "invalid_request_error",
                                              "param": "text.format", "code": "unsupported_parameter"}})])
        with self.assertRaises(CloudError) as error:
            fixture.brain.ask("Fixture question.")
        self.assertEqual(error.exception.code, "request")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual([r["model"] for r in fixture.requests], ["gpt-6-luna"])

    def test_emotion_only_degradation_and_gesture_enum_remain_bounded(self):
        reply = decode_feature_reply(feature_envelope(emotion="unknown"))
        self.assertEqual((reply.emotion, reply.degraded), ("neutral", True))
        for emotion in ("neutral", "happy", "sad", "crying", "angry", "surprised"):
            for gesture in ("none", "wave", "celebrate", "shrug"):
                reply = decode_feature_reply(feature_envelope(emotion=emotion, gesture=gesture))
                self.assertEqual((reply.emotion, reply.gesture), (emotion, gesture))

    def test_original_two_field_and_plain_manual_clients_keep_their_original_formats(self):
        fixture = self.wired([sdk_response('{"speech":"Original expression.","emotion":"sad"}'),
                             sdk_response("Plain manual answer.")])
        original = ExpressionClient(fixture.client)
        brain = policy_brain(fixture.client, integration_config(), expression_client=original)
        self.assertEqual(brain.ask("Original question."), "Original expression.")
        self.assertEqual(set(fixture.requests[0]["text"]["format"]["schema"]["properties"]), {"speech", "emotion"})
        manual = policy_brain(fixture.client, integration_config())
        self.assertEqual(manual.ask("Manual question."), "Plain manual answer.")
        self.assertNotIn("text", fixture.requests[1])
        self.assertNotIn(FEATURE_INSTRUCTIONS, fixture.requests[1]["instructions"])


if __name__ == "__main__":
    unittest.main()
