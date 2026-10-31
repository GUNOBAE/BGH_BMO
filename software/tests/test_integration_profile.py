"""Offline wiring checks, not execution of the policy's model review cases."""

import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.config import BMOConfig
from bmo_ai.core import BMOBrain, LANGUAGE_INSTRUCTIONS, SYSTEM_PROMPT
from bmo_ai.integration_profile import (
    POLICY_BLOB_SHA, effective_config, integration_config, load_voice_policy, policy_brain,
)


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text="We can take a quiet little break.", status="completed",
            error=None, incomplete_details=None,
        )


class IntegrationProfileTests(unittest.TestCase):
    def client(self):
        return SimpleNamespace(responses=FakeResponses())

    def test_selected_profile_reaches_real_request_without_inheriting_env(self):
        with patch.dict("os.environ", {"BMO_CHAT_MODEL": "unselected", "BMO_FISH_GAIN": "0.99"}, clear=True):
            config = integration_config()
        self.assertIsInstance(config, BMOConfig)
        self.assertEqual((config.tts_provider, config.fish_model, config.fish_delivery,
                          config.fish_name_mode, config.fish_gain),
                         ("fish", "s2.1-pro-free", "soft", "beemo", 0.25))
        self.assertEqual(config.fish_voice_id, "106f41ea392c4cc48c6eee76f69ff17e")
        self.assertEqual(config.stt_model, "gpt-4o-mini-transcribe")
        self.assertFalse(config.offline_voice)
        client = self.client()
        policy_brain(client, config).ask("비모, 오늘 뭘 하면 좋을까?")
        request = client.responses.calls[0]
        self.assertEqual((request["model"], request["reasoning"], request["max_output_tokens"]),
                         ("gpt-6-luna", {"effort": "low"}, 320))
        self.assertFalse(request["store"])

    def test_english_voice_replaces_legacy_rule_even_with_korean_legacy_setting(self):
        client = self.client()
        config = integration_config(language="ko")
        policy_brain(client, config).ask("한국어로 대답해줘.")
        prompt = client.responses.calls[0]["instructions"]
        self.assertTrue(prompt.startswith(load_voice_policy()))
        self.assertIn("Trusted host language policy: english_voice", prompt)
        self.assertIn("honor an explicit request for another language", prompt)
        self.assertNotIn(LANGUAGE_INSTRUCTIONS["ko"], prompt)
        self.assertNotIn(LANGUAGE_INSTRUCTIONS["en"], prompt)
        self.assertNotIn(SYSTEM_PROMPT, prompt)

    def test_baseline_clears_luna_reasoning_for_both_selection_paths(self):
        for kwargs in ({"baseline": True}, {"chat_model": "gpt-4.1-mini", "reasoning_effort": "low"}):
            client = self.client()
            config = integration_config(**kwargs)
            self.assertIsNone(config.reasoning_effort)
            policy_brain(client, config).ask("hello")
            self.assertEqual(client.responses.calls[0]["model"], "gpt-4.1-mini")
            self.assertNotIn("reasoning", client.responses.calls[0])

    def test_persistent_brain_retains_two_turns_and_reset_forgets_them(self):
        client = self.client()
        brain = policy_brain(client, integration_config())
        brain.ask("remember the moon")
        brain.ask("what did I say?")
        self.assertEqual(len(client.responses.calls[1]["input"]), 3)
        self.assertEqual(client.responses.calls[1]["input"][0]["content"], "remember the moon")
        brain.reset()
        brain.ask("hello again")
        self.assertEqual(client.responses.calls[2]["input"], [{"role": "user", "content": "hello again"}])

    def test_original_general_cli_brain_keeps_legacy_defaults(self):
        client = self.client()
        BMOBrain(client, model="gpt-4.1-mini").ask("안녕")
        self.assertEqual(client.responses.calls[0]["instructions"], SYSTEM_PROMPT + "\n" + LANGUAGE_INSTRUCTIONS["en"])
        self.assertEqual(client.responses.calls[0]["max_output_tokens"], 180)

    def test_effective_config_does_not_export_paths_or_arbitrary_config_fields(self):
        config = integration_config(offline_voice=True, log_file="private-log", mic_device="private-mic",
                                    local_piper_model="private-model")
        exported = effective_config(config)
        self.assertTrue(exported["offline_voice"])
        self.assertEqual(exported["language_policy"], "english_voice")
        self.assertEqual(exported["audio_owner"], "python_alsa")
        for name in ("log_file", "local_piper_model", "local_piper_binary", "mic_device", "speaker_device", "client", "api_key"):
            self.assertNotIn(name, exported)
        self.assertNotIn("private", repr(exported))

    def test_policy_candidate_is_pinned_and_original_review_data_is_preserved(self):
        import json
        root = Path(__file__).resolve().parents[2]
        original = root / "docs/voice-policy-v1/system-prompt.en.txt"
        revised = root / "docs/character-life-v1/system-prompt.en.txt"
        raw = original.read_bytes()
        blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
        self.assertEqual(hashlib.sha1(blob, usedforsecurity=False).hexdigest(),
                         "a63a8e5c2c768b14b878aa1ca8468323a05fe554")
        self.assertEqual(load_voice_policy(), revised.read_text(encoding="utf-8"))
        self.assertNotEqual(revised.read_bytes(), raw)
        self.assertEqual(POLICY_BLOB_SHA, "2b2b5263253ffbce8c3f6c37ec312c06a221ff64")
        for path in (revised, root / "software/bmo_ai/policies/system-prompt.en.txt"):
            raw = path.read_bytes()
            blob = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
            self.assertEqual(hashlib.sha1(blob, usedforsecurity=False).hexdigest(), POLICY_BLOB_SHA)
        cases = json.loads((original.parent / "acceptance-cases.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cases["cases"]), 20)
        self.assertFalse(cases["executed"])
        self.assertEqual(cases["kind"], "review_data_only")
        provenance = effective_config(integration_config())
        self.assertIsNone(provenance["policy_source_commit"])
        self.assertEqual(provenance["policy_revision"], "character-life-20261030-v2")
        self.assertEqual(provenance["policy_blob_sha"], POLICY_BLOB_SHA)
        self.assertEqual(provenance["policy_source_path"],
                         "docs/character-life-v1/system-prompt.en.txt")

    def test_matching_but_drifted_policy_copies_fail_before_any_request(self):
        client = self.client()
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "source.txt"
            runtime = Path(directory) / "runtime.txt"
            for content in (b"Changed private policy text", b" \n", b"\xff"):
                original.write_bytes(content)
                runtime.write_bytes(content)
                self.assertEqual(original.read_bytes(), runtime.read_bytes())
                with self.subTest(content_length=len(content)), \
                     patch("bmo_ai.integration_profile._POLICY_PATH", runtime), \
                     self.assertRaisesRegex(ValueError, "does not match the pinned source") as caught:
                    policy_brain(client, integration_config())
                self.assertNotIn("private", str(caught.exception))
        self.assertEqual(client.responses.calls, [])

    def test_policy_read_failure_has_no_filesystem_details_or_client_calls(self):
        client = self.client()
        with patch.object(Path, "read_bytes", side_effect=PermissionError("private file details")), \
             self.assertRaisesRegex(ValueError, "Integration voice policy is unavailable") as caught:
            policy_brain(client, integration_config())
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(client.responses.calls, [])

    def test_optional_policy_inputs_are_validated_without_client_calls(self):
        client = self.client()
        for kwargs in ({"system_prompt": " "}, {"system_prompt": 1},
                       {"language_policy": "en"}, {"language_policy": []}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                BMOBrain(client, **kwargs)
        for policy in ("korean_voice", "auto"):
            brain = policy_brain(client, integration_config(), language_policy=policy)
            self.assertEqual(brain.language_policy, policy)
        for policy in (None, "en", []):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                policy_brain(client, integration_config(), language_policy=policy)
            with self.subTest(export_policy=policy), self.assertRaises(ValueError):
                effective_config(integration_config(), language_policy=policy)
        self.assertEqual(client.responses.calls, [])


if __name__ == "__main__":
    unittest.main()
