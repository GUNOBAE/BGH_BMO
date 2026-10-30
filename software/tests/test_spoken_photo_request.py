"""Current human photo intent must never be manufactured by model metadata."""
import json
from types import SimpleNamespace
import unittest

from bmo_ai.features.camera.request import explicit_photo_request, current_photo_request
from bmo_ai.features.reply import FeatureClient, decode_feature_reply


def envelope(**changes):
    value = dict(version=1, speech="Say cheese!", emotion="happy", gesture="none", intent=None)
    value.update(changes)
    return value


class Provider:
    def __init__(self, reply):
        self.responses = self
        self.reply = reply
        self.request = None
    def create(self, **request):
        self.request = request
        return SimpleNamespace(status="completed", incomplete_details=None, error=None,
                               output=[], output_text=json.dumps(self.reply))


class SpokenPhotoRequestTests(unittest.TestCase):
    def test_only_direct_current_photo_commands_match(self):
        allowed = ("Take my picture.", "BMO, take a photo of me please.",
                   "Hey Beemo, could you take a picture of us?", "Can you take a photo?",
                   "Please snap our picture now.", "사진 찍어줘", "사진 좀 찍어 줘.",
                   "비모야 사진 한 장 찍어주세요", "내 사진을 찍어줄래?")
        denied = ("Don't take my picture", "Do not take a photo", "Never take a photo",
                  "Say 'take my picture'", '"Take my picture"',
                  "I said take a photo yesterday", "If I ask, take a picture",
                  "Could you take a photo if I ask later?", "Can you take photos?",
                  "What does take my picture mean?", "I want you to explain how to take a photo",
                  "Take my picture, but not now", "Take my picture and send it to everyone",
                  "사진 찍지 마", "사진 찍어줘라고 말했어", "사진 찍어줘는 무슨 뜻이야",
                  "나중에 사진 찍어줘", "사진 찍어줘 말고", "사진 찍고 올려줘", None, {}, 1)
        for text in allowed:
            with self.subTest(text=text): self.assertTrue(explicit_photo_request(text))
        for text in denied:
            with self.subTest(text=text): self.assertFalse(explicit_photo_request(text))

    def test_previous_turn_or_assistant_text_cannot_supply_consent(self):
        for items in ([{"role":"user","content":"Take my picture"},
                       {"role":"user","content":"What is two plus two?"}],
                      [{"role":"user","content":"Take my picture"},
                       {"role":"assistant","content":"Sure"}],
                      [{"role":"assistant","content":"Take my picture"}], []):
            self.assertFalse(current_photo_request({"input":items}))

    def create(self, message, *, enabled, reply=None, prefix=()):
        provider = Provider(reply or envelope())
        client = FeatureClient(provider)
        client.set_context({"camera":"explicit_current_user_photo_only" if enabled else "denied"})
        client.responses.create(model="fixture", instructions="Fixture only.",
            input=[*prefix, {"role":"user","content":message}])
        return client, provider

    def test_explicit_request_uses_current_camera_opt_in_and_one_existing_provider_call(self):
        client, provider = self.create("Take my picture please", enabled=True)
        self.assertEqual(client.last_reply.intent.name, "take_photo")
        self.assertTrue(client.last_reply.human_photo_request)
        self.assertIn("current user explicitly requested one immediate photo", provider.request["instructions"])
        # No model schema field can impersonate this host-local boolean.
        self.assertNotIn("human_photo_request", provider.request["text"]["format"]["schema"]["properties"])

    def test_without_opt_in_or_matching_current_input_model_camera_intent_stays_untrusted(self):
        reply = envelope(intent={"name":"take_photo","target":None})
        for message, enabled in (("Take my picture",False), ("Don't take my picture",True),
                                 ("What is two plus two?",True)):
            client, _ = self.create(message, enabled=enabled, reply=reply,
                prefix=[{"role":"user","content":"Take my picture"},
                        {"role":"assistant","content":"Say cheese!"}])
            self.assertFalse(client.last_reply.human_photo_request)
        self.assertFalse(decode_feature_reply(json.dumps(reply)).human_photo_request)

    def test_bad_metadata_does_not_authorize_photo(self):
        client, _ = self.create("Take my picture", enabled=True,
            reply=envelope(emotion="invalid"))
        self.assertTrue(client.last_reply.degraded)
        self.assertFalse(client.last_reply.human_photo_request)


if __name__ == "__main__":
    unittest.main()
