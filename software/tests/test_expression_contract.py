"""Offline contract tests: no SDK, network, device, key, face file or model."""
from dataclasses import FrozenInstanceError
import copy
import json
from types import SimpleNamespace
import unittest

from bmo_ai.expression_contract import (
    EXPRESSION_INSTRUCTIONS, FACE_EMOTIONS, MAX_ENVELOPE_CHARS, MAX_SPEECH_CHARS,
    ExpressionClient, ExpressionFormatError, ExpressionReply, ExpressionResponses,
    decode_expression, expression_request, response_text_format,
)


def envelope(speech="That is wonderful news!", emotion="happy"):
    return json.dumps({"speech": speech, "emotion": emotion}, ensure_ascii=False)


def response(text=None, **overrides):
    fields = dict(status="completed", incomplete_details=None, error=None,
                  output_text=envelope() if text is None else text, output=[])
    fields.update(overrides)
    return SimpleNamespace(**fields)


def request(**overrides):
    value = dict(model="gpt-6-luna", reasoning={"effort": "low"},
                 instructions="Keep the approved BMO voice and language policy.",
                 input=[{"role": "user", "content": "I passed my exam."}],
                 max_output_tokens=320, store=False)
    value.update(overrides)
    return value


class FakeResponses:
    def __init__(self, result=None):
        self.result = response() if result is None else result
        self.calls = []
        self.failure = None

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.result


class DecodeTests(unittest.TestCase):
    def assertInvalid(self, raw):
        with self.assertRaises(ExpressionFormatError) as caught:
            decode_expression(raw)
        self.assertEqual(str(caught.exception), "The reply did not match the expression contract.")

    def test_all_six_approved_face_emotions(self):
        for emotion in FACE_EMOTIONS:
            with self.subTest(emotion=emotion):
                reply = decode_expression(envelope(emotion=emotion))
                self.assertEqual(reply.emotion, emotion)
                self.assertFalse(reply.degraded)

    def test_unknown_emotion_preserves_speech_and_uses_neutral(self):
        reply = decode_expression(envelope(emotion="excited"))
        self.assertEqual((reply.speech, reply.emotion, reply.degraded),
                         ("That is wonderful news!", "neutral", True))

    def test_missing_emotion_degrades_without_retry(self):
        self.assertEqual(decode_expression('{"speech":"Hello."}'),
                         ExpressionReply("Hello.", "neutral", True))

    def test_invalid_emotion_types_and_case_degrade(self):
        for item in (None, True, 1, [], {}, "HAPPY", " happy ", "[laugh]"):
            with self.subTest(item=item):
                reply = decode_expression(envelope(emotion=item))
                self.assertEqual(reply.emotion, "neutral")
                self.assertTrue(reply.degraded)

    def test_unicode_korean_numbers_dates_and_negation_are_unchanged(self):
        speech = "아니, -5도야. 2026-10-22에 0.25배였어. Do not double it."
        self.assertEqual(decode_expression(envelope(speech)).speech, speech)

    def test_edge_whitespace_trim_only(self):
        self.assertEqual(decode_expression(envelope("  A\nB\tC.  ")).speech, "A\nB\tC.")

    def test_empty_or_nonstring_speech_rejected(self):
        for item in ("", "  ", None, True, 17, [], {}):
            self.assertInvalid(envelope(item))

    def test_extra_action_and_transport_fields_rejected(self):
        for key in ("action", "arguments", "voice_id", "gain", "path", "executed"):
            raw = json.loads(envelope())
            raw[key] = "not trusted"
            self.assertInvalid(json.dumps(raw))

    def test_duplicate_keys_rejected(self):
        self.assertInvalid('{"speech":"one","speech":"two","emotion":"happy"}')
        self.assertInvalid('{"speech":"one","emotion":"happy","emotion":"sad"}')

    def test_wrong_root_and_missing_speech_rejected(self):
        for raw in ('[]', 'null', '42', '"Hello"', '{}', '{"emotion":"happy"}'):
            self.assertInvalid(raw)

    def test_partial_json_fences_prose_and_trailing_content_rejected(self):
        for raw in ('{"speech":"Hello', '```json\n'+envelope()+'\n```',
                    'Hello.', envelope()+' extra', envelope()+envelope()):
            self.assertInvalid(raw)

    def test_nonfinite_json_rejected(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            self.assertInvalid('{"speech":"Hello","emotion":'+constant+'}')

    def test_tts_tags_and_nested_markup_never_reach_speech(self):
        for speech in ("[laugh] Hi.", "[shout loudly] Hi.", '<speak>Hello</speak>',
                       '{"speech":"Hi"}', '```Hello```', 'unclosed [tag'):
            self.assertInvalid(envelope(speech))

    def test_control_characters_and_surrogates_rejected(self):
        for char in ('\x00', '\x1b', '\x7f', '\ud800'):
            self.assertInvalid(envelope('Hello '+char))

    def test_speech_limit_is_bounded_not_truncated(self):
        self.assertEqual(len(decode_expression(envelope('a'*MAX_SPEECH_CHARS)).speech), MAX_SPEECH_CHARS)
        self.assertInvalid(envelope('a'*(MAX_SPEECH_CHARS+1)))

    def test_envelope_limit_and_nonstring_rejected(self):
        for raw in (' '*MAX_ENVELOPE_CHARS+'x', b'{}', {}, None):
            self.assertInvalid(raw)

    def test_deep_json_failure_is_sanitized(self):
        self.assertInvalid('['*1100+'0'+']'*1100)

    def test_reply_is_immutable_and_direct_construction_validates(self):
        reply = ExpressionReply("Hello", "neutral")
        with self.assertRaises(FrozenInstanceError):
            reply.emotion = "sad"
        for args in (("",), ("Hello", "evil"), ("[laugh]",), ("Hello", "happy", 1)):
            with self.assertRaises(ExpressionFormatError):
                ExpressionReply(*args)


class RequestTests(unittest.TestCase):
    def test_schema_uses_responses_text_format(self):
        value = response_text_format()["format"]
        self.assertEqual(value["type"], "json_schema")
        self.assertTrue(value["strict"])
        self.assertEqual(set(value["schema"]["required"]), {"speech", "emotion"})
        self.assertFalse(value["schema"]["additionalProperties"])

    def test_schema_is_fresh(self):
        value = response_text_format()
        value["format"]["schema"]["properties"]["emotion"]["enum"].clear()
        self.assertEqual(response_text_format()["format"]["schema"]["properties"]["emotion"]["enum"], list(FACE_EMOTIONS))

    def test_preserves_model_reasoning_tokens_history_and_original(self):
        original = request()
        saved = copy.deepcopy(original)
        new = expression_request(original)
        self.assertEqual(original, saved)
        for key in ("model", "reasoning", "max_output_tokens", "input"):
            self.assertEqual(new[key], original[key])
        self.assertTrue(new["instructions"].startswith(original["instructions"]))
        self.assertIn(EXPRESSION_INSTRUCTIONS, new["instructions"])
        self.assertFalse(new["store"])

    def test_baseline_is_not_given_luna_reasoning(self):
        value = request(model="gpt-4.1-mini")
        del value["reasoning"]
        self.assertNotIn("reasoning", expression_request(value))

    def test_prior_plaintext_verbosity_is_preserved(self):
        value = expression_request(request(text={"format":{"type":"text"}, "verbosity":"low"}))
        self.assertEqual(value["text"]["verbosity"], "low")

    def test_other_formats_and_unknown_options_rejected(self):
        for value in ({"format":{"type":"json_object"}}, {"foo":1}, "text"):
            with self.assertRaises(ExpressionFormatError):
                expression_request(request(text=value))
        with self.assertRaises(ExpressionFormatError):
            expression_request(request(response_format={"type":"json_object"}))

    def test_streaming_and_actions_are_not_silently_combined(self):
        for options in ({"stream":True}, {"tools":[{"type":"function"}]}, {"tool_choice":"auto"}):
            with self.assertRaises(ExpressionFormatError):
                expression_request(request(**options))

    def test_invalid_instructions_fail_before_delegate(self):
        for value in (None, "", 1, []):
            with self.assertRaises(ExpressionFormatError):
                expression_request(request(instructions=value))


class BoundaryTests(unittest.TestCase):
    def test_one_call_returns_only_speech_and_keeps_provisional_metadata(self):
        fake = FakeResponses()
        boundary = ExpressionResponses(fake)
        result = boundary.create(**request())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result.output_text, "That is wonderful news!")
        self.assertEqual(boundary.last_reply.emotion, "happy")
        self.assertNotIn('"speech"', result.output_text)

    def test_incomplete_failed_and_error_responses_never_supply_metadata(self):
        for fields in ({"status":"incomplete"}, {"status":"failed"},
                       {"incomplete_details":{"reason":"max_output_tokens"}}, {"error":{"code":"failure"}}):
            fake = FakeResponses(response(**fields))
            boundary = ExpressionResponses(fake)
            self.assertIs(boundary.create(**request()), fake.result)
            self.assertIsNone(boundary.last_reply)
            self.assertEqual(len(fake.calls), 1)

    def test_invalid_payload_never_retries_or_passes_raw(self):
        fake = FakeResponses(response("[laugh] raw text"))
        boundary = ExpressionResponses(fake)
        with self.assertRaises(ExpressionFormatError):
            boundary.create(**request())
        self.assertEqual(len(fake.calls), 1)
        self.assertIsNone(boundary.last_reply)

    def test_unknown_emotion_is_neutral_without_second_call(self):
        fake = FakeResponses(response(envelope(emotion="bad")))
        boundary = ExpressionResponses(fake)
        self.assertEqual(boundary.create(**request()).output_text, "That is wonderful news!")
        self.assertTrue(boundary.last_reply.degraded)
        self.assertEqual(len(fake.calls), 1)

    def test_refusal_objects_or_dicts_never_supply_metadata(self):
        outputs = ([{"type":"message","content":[{"type":"refusal","refusal":"No."}]}],
                   [SimpleNamespace(type="message",content=[SimpleNamespace(type="refusal",refusal="No.")])])
        for output in outputs:
            fake = FakeResponses(response(output=output))
            boundary = ExpressionResponses(fake)
            with self.assertRaises(ExpressionFormatError):
                boundary.create(**request())
            self.assertIsNone(boundary.last_reply)
            self.assertEqual(len(fake.calls), 1)

    def test_network_exception_is_not_retried_and_clears_old_metadata(self):
        fake = FakeResponses()
        boundary = ExpressionResponses(fake)
        boundary.create(**request())
        fake.failure = ConnectionError("not to be logged by the host")
        with self.assertRaises(ConnectionError):
            boundary.create(**request())
        self.assertIsNone(boundary.last_reply)
        self.assertEqual(len(fake.calls), 2)

    def test_second_turn_is_not_stuck_in_previous_expression(self):
        fake = FakeResponses()
        boundary = ExpressionResponses(fake)
        boundary.create(**request())
        fake.result = response(envelope("The answer is four.", "neutral"))
        boundary.create(**request())
        self.assertEqual(boundary.last_reply.emotion, "neutral")

    def test_reset_is_local_and_does_not_close_real_client(self):
        fake = FakeResponses()
        client = SimpleNamespace(responses=fake)
        view = ExpressionClient(client)
        view.responses.create(**request())
        view.reset()
        self.assertIsNone(view.last_reply)
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(hasattr(view, "close"))

    def test_invalid_request_clears_previous_metadata_without_call(self):
        fake = FakeResponses()
        boundary = ExpressionResponses(fake)
        boundary.create(**request())
        with self.assertRaises(ExpressionFormatError):
            boundary.create(**request(stream=True))
        self.assertIsNone(boundary.last_reply)
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
