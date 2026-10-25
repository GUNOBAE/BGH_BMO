"""Expressive request contracts and private synthetic caches; no provider/device IO."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio
from bmo_ai.expression_contract import ExpressionFormatError
from bmo_ai.expressive_voice import ExpressivePolicy, REACTIONS
from bmo_ai.features.reply import FeatureClient, decode_feature_reply, feature_text_format
from bmo_ai.greeting_cache import GreetingError
from bmo_ai.integration_profile import integration_config, policy_brain
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.voice_benchmark import SAMPLES, _prepare_lock, prepare, run, sample_cache


def payload(**changes):
    return dict(version=2, speech="A tiny happy answer.", emotion="happy",
                gesture="none", intent=None, reaction="chuckle") | changes


class ExpressivePolicyTests(unittest.TestCase):
    def metadata(self, **changes):
        return dict(emotion="happy", emotion_validation="validated", reaction="chuckle") | changes

    def test_fixed_supported_s2_tags_and_no_unsupported_request_parameters(self):
        policy = ExpressivePolicy()
        plan = policy.plan("Hello Beemo.", self.metadata(), model="s2.1-pro-free")
        self.assertEqual(plan.text, "[happy] [chuckling] Hehe. Hello Beemo.")
        self.assertEqual((plan.reaction, plan.emotion), ("chuckle", "happy"))

    def test_unvalidated_unknown_or_unsupported_metadata_cannot_become_tags(self):
        for metadata, model in ((self.metadata(emotion_validation="manual"), "s2.1-pro-free"),
                                (self.metadata(), "drama-3"),
                                (self.metadata(emotion="private"), "s2.1-pro-free")):
            with self.subTest(model=model, metadata=metadata):
                plan = ExpressivePolicy().plan("Hello.", metadata, model=model)
                self.assertNotIn("private", plan.text)
                self.assertEqual(plan.reaction, "none")
                self.assertNotIn("[", plan.text)

    def test_model_markup_and_arbitrary_voice_commands_are_rejected(self):
        for text in ("[speaker:new] Hello.", "<speak>Hello</speak>", "{private}"):
            with self.assertRaises(ExpressionFormatError):
                ExpressivePolicy().plan(text, self.metadata(), model="s2.1-pro-free")

    def test_only_congruent_allowlisted_reactions_are_used(self):
        for reaction in (*REACTIONS, "[screaming]", [], True):
            plan = ExpressivePolicy().plan("I am here.", self.metadata(
                emotion="sad", reaction=reaction), model="s2.1-pro-free")
            self.assertEqual(plan.reaction, "none")
            self.assertEqual(plan.text, "[sad] I am here.")

    def test_reaction_budget_changes_only_after_successful_new_audio(self):
        policy = ExpressivePolicy()
        first = policy.plan("Hello.", self.metadata(), model="s2.1-pro-free")
        self.assertEqual(policy.plan("Retry.", self.metadata(), model="s2.1-pro-free").reaction, "chuckle")
        policy.completed(first)
        for _ in range(2):
            quiet = policy.plan("Next.", self.metadata(), model="s2.1-pro-free")
            self.assertEqual(quiet.reaction, "none")
            policy.completed(quiet)
        self.assertEqual(policy.plan("Later.", self.metadata(), model="s2.1-pro-free").reaction, "chuckle")


class ExpressiveReplyTests(unittest.TestCase):
    def test_original_v1_schema_and_decode_are_unchanged(self):
        old = payload(version=1)
        del old["reaction"]
        self.assertEqual(decode_feature_reply(json.dumps(old)).reaction, "none")
        self.assertEqual(feature_text_format()["format"]["name"], "bmo_feature_reply_v1")

    def test_v2_schema_is_strict_and_reaction_enum_is_bounded(self):
        schema = feature_text_format(expressive=True)["format"]
        self.assertEqual(schema["name"], "bmo_feature_reply_v2")
        self.assertFalse(schema["schema"]["additionalProperties"])
        self.assertEqual(schema["schema"]["properties"]["version"]["enum"], [2])
        self.assertEqual(schema["schema"]["properties"]["reaction"]["enum"], list(REACTIONS))
        self.assertIn("reaction", schema["schema"]["required"])
        self.assertEqual(decode_feature_reply(json.dumps(payload())).reaction, "chuckle")

    def test_invalid_reaction_tags_and_missing_v2_fields_fail(self):
        bad = [payload(reaction=value) for value in ("evil", "[laugh]", [], None, True)]
        missing = payload()
        del missing["reaction"]
        bad.append(missing)
        bad.append(payload(speech="[laughing] spoken tag"))
        for value in bad:
            with self.subTest(fields=list(value)):
                with self.assertRaises(ExpressionFormatError):
                    decode_feature_reply(json.dumps(value))

    def test_one_brain_call_clean_history_and_host_permission_for_sob(self):
        for question, expected in (("I am very sad.", "none"),
                                   ("Please pretend to cry for this scene.", "sob")):
            raw = json.dumps(payload(emotion="crying", reaction="sob"))
            provider = Mock()
            provider.responses.create.return_value = SimpleNamespace(
                status="completed", incomplete_details=None, error=None,
                output_text=raw, output=[])
            view = FeatureClient(provider, expressive=True)
            brain = policy_brain(provider, integration_config(), expression_client=view)
            self.assertEqual(brain.ask(question), "A tiny happy answer.")
            self.assertEqual(view.last_reply.reaction, expected)
            provider.responses.create.assert_called_once()
            request = provider.responses.create.call_args.kwargs
            self.assertEqual(request["text"]["format"]["name"], "bmo_feature_reply_v2")
            self.assertEqual(brain.history[-1]["content"], "A tiny happy answer.")
            self.assertNotIn("reaction", brain.history[-1]["content"])

    def test_silly_reaction_needs_current_request_and_negative_request_suppresses_sob(self):
        for question, reaction, emotion, expected in (
            ("Tell me a fact.", "playful_puff", "happy", "none"),
            ("Please be silly and make a tiny puff.", "playful_puff", "happy", "playful_puff"),
            ("Please do not pretend to cry.", "sob", "crying", "none"),
        ):
            provider = Mock()
            provider.responses.create.return_value = SimpleNamespace(
                status="completed", incomplete_details=None, error=None, output=[],
                output_text=json.dumps(payload(reaction=reaction, emotion=emotion)))
            view = FeatureClient(provider, expressive=True)
            brain = policy_brain(provider, integration_config(), expression_client=view)
            brain.ask(question)
            self.assertEqual(view.last_reply.reaction, expected)


class ExpressiveVoiceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network")))
        self.config = integration_config()
        self.sent, self.events = [], []

    def voice(self, **changes):
        def synth(text, **kwargs):
            self.sent.append((text, kwargs))
            return synthetic_wav()
        settings = dict(fish_synthesizer=synth, player=lambda command, on_start, **kwargs:on_start())
        settings.update(changes)
        value = IntegrationVoice(self.config, None, emit=lambda kind, **data:self.events.append((kind, data)),
                                 expressive=True, **settings)
        self.addCleanup(value.close)
        value.expression_metadata = dict(emotion="happy", emotion_validation="validated", reaction="chuckle")
        return value

    def test_aaron_name_gain_and_actual_audio_owner_are_preserved(self):
        voice = self.voice()
        voice.speak("Hello BMO.")
        self.assertTrue(self.sent[0][0].endswith("[happy] [chuckling] Hehe. Hello Beemo."))
        self.assertTrue(self.sent[0][0].startswith("[gentle, warm, slightly lower pitch, still lightly playful] "))
        self.assertEqual(self.sent[0][1]["reference_id"], self.config.fish_voice_id)
        self.assertEqual(self.sent[0][1]["model"], "s2.1-pro-free")
        self.assertEqual([kind for kind, _ in self.events], ["prepared", "started", "ended"])
        self.assertEqual(self.events[1][1]["reaction"], "chuckle")
        self.assertEqual(self.events[1][1]["emotion"], "happy")
        self.assertNotIn("text", self.events[1][1])

    def test_repeat_preserves_audio_emotion_reaction_without_provider_or_budget(self):
        voice = self.voice()
        voice.speak("Hello BMO.")
        previous = voice._cache["path"]
        voice.expression_metadata = dict(emotion="sad", emotion_validation="validated", reaction="sob")
        budget = voice._expressive_policy._successful
        self.assertTrue(voice.repeat())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(voice._cache["path"], previous)
        self.assertEqual(self.events[-2][1]["reaction"], "chuckle")
        self.assertEqual(voice._expressive_policy._successful, budget)

    def test_metadata_change_during_synthesis_cannot_desync_current_audio_face(self):
        voice = self.voice()
        def synth(text, **kwargs):
            self.sent.append((text, kwargs))
            voice.expression_metadata = dict(emotion="sad", emotion_validation="validated", reaction="none")
            return synthetic_wav()
        voice._fish_synthesizer = synth
        voice.speak("Hello BMO.")
        self.assertEqual(self.events[1][1]["emotion"], "happy")
        self.assertEqual(self.events[1][1]["voice_emotion"], "happy")
        self.assertEqual(self.events[1][1]["reaction"], "chuckle")
        self.assertEqual(voice._cache["expression"]["emotion"], "happy")

    def test_piper_fallback_never_reads_fish_direction_or_reaction_tags(self):
        piper = Mock(side_effect=lambda text, **kwargs:synthetic_wav())
        voice = self.voice(fish_synthesizer=Mock(side_effect=audio.AudioSynthesisError("Unavailable")),
                           piper_synthesizer=piper)
        voice.config = replace(self.config, offline_voice=True)
        voice.speak("Plain answer.")
        self.assertEqual(piper.call_args.args[0], "Plain answer.")
        self.assertEqual(self.events[1][1]["source"], "piper")
        self.assertEqual(self.events[1][1]["reaction"], "none")
        self.assertEqual(voice._expressive_policy._successful, 0)


class VoiceBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network")))

    def test_plan_has_no_files_secrets_synthesis_or_playback(self):
        with patch("os.open", side_effect=AssertionError("No file IO")), \
             patch("bmo_ai.voice_benchmark.resolve_credentials", side_effect=AssertionError("No credentials")), \
             patch("bmo_ai.fish_tts.synthesize_wav", side_effect=AssertionError("No API")), \
             patch("bmo_ai.voice_benchmark.play_wav", side_effect=AssertionError("No playback")):
            lines = []
            self.assertEqual(run([], output=lines.append), 0)
        self.assertIn("ASSET_PENDING", " ".join(lines))
        self.assertEqual(len(SAMPLES), 6)

    def test_paid_opt_in_required_before_creating_asset_or_resolving_credentials(self):
        cache = sample_cache(self.root, "happy")
        resolver = Mock(side_effect=AssertionError("No credentials"))
        with self.assertRaises(GreetingError) as error:
            prepare(cache, credential_resolver=resolver)
        self.assertEqual(error.exception.code, "greeting_prepare_denied")
        self.assertFalse(cache.directory.exists())

    def test_one_synthesis_then_reuse_and_hash_bound_approval_no_startup_synthesis(self):
        cache = sample_cache(self.root, "happy")
        synth = Mock(side_effect=lambda text, **kwargs:synthetic_wav())
        resolver = Mock(return_value=SimpleNamespace(fish_api_key="test-secret-not-logged"))
        first = prepare(cache, allow_paid_api=True, synthesizer=synth, credential_resolver=resolver)
        second = prepare(cache, allow_paid_api=True, synthesizer=synth, credential_resolver=resolver)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["sha256"], second["sha256"])
        synth.assert_called_once()
        resolver.assert_called_once_with(interactive=False)
        self.assertEqual(cache.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((cache.directory / "greeting.cache").stat().st_mode & 0o777, 0o600)
        cache.approve(first["sha256"])
        with cache.load() as asset:
            self.assertTrue(asset.approved)
            self.assertEqual(asset.sha256, first["sha256"])
            self.assertEqual(asset.cues, tuple(asset.cues))

    def test_corrupt_or_changed_asset_never_spends_again(self):
        cache = sample_cache(self.root, "sad")
        raw = synthetic_wav()
        try:
            cache.publish(raw, simulated=False)
        finally:
            audio._discard(raw, suppress=True)
        path = cache.directory / "greeting.cache"
        path.write_bytes(b"private corrupt evidence")
        synth, resolver = Mock(), Mock()
        with self.assertRaises(GreetingError):
            prepare(cache, allow_paid_api=True, synthesizer=synth, credential_resolver=resolver)
        synth.assert_not_called()
        resolver.assert_not_called()
        self.assertEqual(path.read_bytes(), b"private corrupt evidence")

    def test_concurrent_prepare_is_busy_before_any_provider_or_credentials(self):
        cache = sample_cache(self.root, "happy")
        synth, resolver = Mock(), Mock()
        with _prepare_lock(cache):
            with self.assertRaises(GreetingError) as error:
                prepare(cache, allow_paid_api=True, synthesizer=synth, credential_resolver=resolver)
        self.assertEqual(error.exception.code, "greeting_busy")
        synth.assert_not_called()
        resolver.assert_not_called()

    def test_audition_uses_saved_gain_once_and_does_not_approve(self):
        cache = sample_cache(self.root, "neutral")
        raw = synthetic_wav()
        try:
            cache.publish(raw, simulated=False)
        finally:
            audio._discard(raw, suppress=True)
        player = Mock()
        self.assertEqual(run(["--audition", "neutral", "--cache-directory", str(self.root)],
                             player=player, output=lambda line:None), 0)
        player.assert_called_once()
        self.assertEqual(player.call_args.kwargs["gain"], 1.0)
        with cache.load(require_approved=False) as asset:
            self.assertFalse(asset.approved)


class FeatureHostExpressiveIntegrationTests(unittest.TestCase):
    """Product-path input/provider/player boundaries are all injected OFFLINE."""
    def setUp(self):
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                       "bmo_ai.cloud.create_client", "bmo_ai.audio.record_wav",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("No production IO")))

    def fixture(self, client):
        from bmo_ai.features.host import FeatureHost
        from bmo_ai.features.input.microphone import SyntheticPcmSource
        from bmo_ai.features.runtime import RuntimePolicy
        config = integration_config()
        sent, playback = [], []

        def synth(text, **settings):
            sent.append((text, settings))
            return synthetic_wav()

        def player(command, **settings):
            playback.append(command)
            settings["cancel_check"]()
            settings["on_start"]()
            settings["cancel_check"]()

        voice = IntegrationVoice(config, client, emit=lambda *a, **kw:None,
            simulate=False, expressive=True, fish_synthesizer=synth, player=player)
        host = FeatureHost(config=config, client=client, voice=voice, expressive_voice=True,
            runtime=RuntimePolicy(simulate=False, conversation_ptt_microphone=True),
            ptt_source_factory=SyntheticPcmSource, ptt_trace=True)
        self.addCleanup(host.close)
        return host, sent, playback

    def finish(self, host):
        deadline = time.monotonic() + 4
        while host.session.busy and time.monotonic() < deadline:
            host.heartbeat()
            host.poll()
            time.sleep(.002)
        host.poll()
        self.assertFalse(host.session.busy, "Offline expressive turn timed out")

    def test_default_simulated_client_v1_reply_rolls_out_under_v2_request(self):
        from bmo_ai.features.host import FeatureSimulatedClient
        client = FeatureSimulatedClient()
        calls = []
        def transcribe(**request):
            calls.append(request["model"])
            return SimpleNamespace(text="Synthetic question.")
        client.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe))
        host, sent, playback = self.fixture(client)
        self.assertTrue(host.ptt_button())
        self.finish(host)
        self.assertEqual(calls, ["gpt-4o-mini-transcribe"])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(client.requests[0]["text"]["format"]["name"], "bmo_feature_reply_v2")
        self.assertEqual((len(sent), len(playback)), (1, 1))
        expression = host.session.voice._cache["expression"]
        self.assertEqual((expression["emotion"], expression["reaction"]), ("happy", "none"))
        self.assertEqual(host.snapshot()["turn_diagnostics"]["terminal"], "ok")

    def test_real_sdk_offline_v2_acting_sob_keeps_one_audio_owner_and_diagnoses_stages(self):
        import httpx
        from openai import OpenAI
        from test_expression_sdk import sdk_response
        requests = []
        def transport(request):
            requests.append(request.url.path)
            if request.url.path == "/v1/audio/transcriptions":
                self.assertIn(b"gpt-4o-mini-transcribe", request.read())
                return httpx.Response(200, json={"text": "Please pretend to cry for this scene."})
            self.assertEqual(request.url.path, "/v1/responses")
            body = json.loads(request.read())
            self.assertEqual(body["text"]["format"]["name"], "bmo_feature_reply_v2")
            return httpx.Response(200, json=sdk_response(json.dumps(payload(
                speech="My little game is gone.", emotion="crying", reaction="sob"))))
        client = OpenAI(api_key="offline-expression-fixture", max_retries=0,
                        http_client=httpx.Client(transport=httpx.MockTransport(transport)))
        self.addCleanup(client.close)
        host, sent, playback = self.fixture(client)
        self.assertTrue(host.ptt_button())
        self.finish(host)
        self.assertEqual(requests, ["/v1/audio/transcriptions", "/v1/responses"])
        self.assertEqual((len(sent), len(playback)), (1, 1))
        self.assertIn("[sad] [sobbing] Oh... My little game is gone.", sent[0][0])
        cached = host.session.voice._cache
        self.assertEqual(cached["expression"]["emotion"], "crying")
        self.assertEqual(cached["expression"]["voice_emotion"], "crying")
        self.assertEqual(cached["expression"]["reaction"], "sob")
        self.assertTrue(cached["cues"])
        self.assertEqual(host.session.snapshot()["emotion"], "crying")
        diagnosis = host.turn_diagnostics.snapshot()
        self.assertEqual(diagnosis["terminal"], "ok")
        self.assertIsNone(diagnosis["failure"])
        self.assertTrue({"microphone", "vad", "stt", "ai", "fish", "playback"}.issubset(
            {entry["stage"] for entry in diagnosis["events"]}))
        self.assertNotIn("My little game", json.dumps(host.snapshot()))
        self.assertNotIn("Please pretend", json.dumps(diagnosis))


if __name__ == "__main__":
    unittest.main()
