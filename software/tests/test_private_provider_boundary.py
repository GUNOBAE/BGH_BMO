"""Explicit synthetic credentials reach only mocked provider boundaries."""
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai import audio, cloud, fish_tts
from bmo_ai.integration_profile import effective_config, integration_config
from bmo_ai.integration_session import IntegrationSession, SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


OPENAI_MARKER = "synthetic-openai-memory-marker"
FISH_MARKER = "synthetic-fish-memory-marker"


class ExplicitProviderCredentialTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.audio.record_wav",
                       "bmo_ai.audio.transcribe", "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Live IO forbidden")))

    def test_openai_explicit_key_bypasses_environment_and_only_enters_sdk_constructor(self):
        environment = Mock()
        environment.get.side_effect = AssertionError("Explicit credentials must not read the environment")
        sdk = SimpleNamespace(OpenAI=Mock(return_value="synthetic-client"))
        config = integration_config()
        with patch.dict("sys.modules", {"openai": sdk}):
            self.assertEqual(cloud.create_client(config, env=environment, api_key=OPENAI_MARKER),
                             "synthetic-client")
        environment.get.assert_not_called()
        sdk.OpenAI.assert_called_once_with(api_key=OPENAI_MARKER, base_url="https://api.openai.com/v1",
                                          timeout=config.timeout, max_retries=config.max_retries)
        self.assertNotIn(OPENAI_MARKER, repr(effective_config(config)))

    def test_invalid_explicit_openai_key_is_fixed_error_without_environment_fallback(self):
        sdk = SimpleNamespace(OpenAI=Mock(side_effect=AssertionError("Invalid keys must not initialize SDK")))
        for key in ("", " ", True, 7, "YOUR_KEY_HERE", "YOUR_OPENAI_KEY", "bad\nmarker", "x" * 4097):
            with self.subTest(kind=type(key).__name__), patch.dict("sys.modules", {"openai": sdk}):
                with self.assertRaises(cloud.CloudError) as error:
                    cloud.create_client(integration_config(), env={"OPENAI_API_KEY": OPENAI_MARKER},
                                        api_key=key)
                self.assertEqual(error.exception.code, "missing_key")
                self.assertNotIn(OPENAI_MARKER, str(error.exception))
                self.assertNotIn("marker", str(error.exception))
        sdk.OpenAI.assert_not_called()

    def test_fish_explicit_key_only_enters_authorization_and_keeps_transport_policy(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 320)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "synthetic.wav"
            environment = Mock()
            environment.get.side_effect = AssertionError("Explicit Fish key must not read environment")
            with patch.object(fish_tts.os, "environ", environment), \
                 patch.object(fish_tts, "urlopen", return_value=io.BytesIO(buffer.getvalue())) as send, \
                 patch.object(audio, "_new_wav", return_value=destination):
                result = fish_tts.synthesize_wav("Synthetic reply.", api_key=FISH_MARKER)
            self.addCleanup(result.unlink, missing_ok=True)
            self.assertEqual(result, destination)
            environment.get.assert_not_called()
            send.assert_called_once()
            request = send.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer " + FISH_MARKER)
            self.assertEqual(request.get_header("Model"), "s2.1-pro-free")
            self.assertEqual(json.loads(request.data), {
                "text": "Synthetic reply.", "reference_id": fish_tts.AARON_BMO_VOICE_ID, "format": "wav"})
            self.assertNotIn(FISH_MARKER, request.full_url)
            self.assertNotIn(FISH_MARKER, request.data.decode())

    def test_invalid_explicit_fish_key_never_falls_back_or_allocates_output(self):
        with patch.dict(os.environ, {"FISH_API_KEY": FISH_MARKER}), \
             patch.object(fish_tts, "urlopen") as send, patch.object(audio, "_new_wav") as allocate:
            for key in ("", " ", False, 7, "YOUR_KEY_HERE", "YOUR_FISH_KEY", "bad\rmarker", "x" * 4097):
                with self.subTest(kind=type(key).__name__), self.assertRaises(audio.AudioSynthesisError) as error:
                    fish_tts.synthesize_wav("Synthetic reply.", api_key=key)
                self.assertEqual(error.exception.code, "missing_fish_key")
                self.assertNotIn(FISH_MARKER, str(error.exception))
                self.assertNotIn("marker", str(error.exception))
            send.assert_not_called()
            allocate.assert_not_called()

    @staticmethod
    def player(command, *, on_start, cancel_check, **settings):
        cancel_check()
        on_start()
        cancel_check()

    def test_session_routes_fish_key_in_memory_without_config_events_or_player_leak(self):
        config, client = integration_config(), SimulatedClient()
        session = IntegrationSession(config, client, fish_api_key=FISH_MARKER)
        self.addCleanup(session.close)
        self.assertIs(session.voice.client, client)
        events, environments = [], []
        fish = Mock(side_effect=lambda *args, **kwargs: synthetic_wav())
        session.voice._fish_synthesizer = fish
        session.voice.emit = lambda kind, **fields: events.append((kind, fields))

        def player(command, **settings):
            environments.append(settings["env"])
            self.player(command, **settings)

        session.voice._player = player
        before = dict(os.environ)
        session.voice.speak("Hello BMO.")
        self.assertEqual(os.environ, before)
        fish.assert_called_once()
        self.assertEqual(fish.call_args.kwargs["api_key"], FISH_MARKER)
        self.assertEqual(fish.call_args.args, (fish_tts.SOFT_AARON_DIRECTION + "Hello Beemo.",))
        self.assertEqual(fish.call_args.kwargs["model"], "s2.1-pro-free")
        self.assertEqual(fish.call_args.kwargs["reference_id"], fish_tts.AARON_BMO_VOICE_ID)
        self.assertNotIn(FISH_MARKER, repr(events))
        self.assertNotIn(FISH_MARKER, repr(environments))
        self.assertNotIn(FISH_MARKER, repr(session.snapshot()))
        self.assertNotIn(FISH_MARKER, repr(effective_config(config)))
        self.assertFalse(client.requests)

    def test_none_key_preserves_legacy_custom_synthesizer_signature(self):
        calls = []
        def synthesize(text, *, model, reference_id, timeout, cancel_check):
            calls.append(text)
            return synthetic_wav()
        voice = IntegrationVoice(integration_config(), None, emit=lambda *a, **kw: None,
                                 fish_synthesizer=synthesize, player=self.player)
        self.addCleanup(voice.close)
        voice.speak("Legacy boundary.")
        self.assertEqual(calls, [fish_tts.SOFT_AARON_DIRECTION + "Legacy boundary."])

    def test_opt_in_piper_never_receives_fish_key_and_preserves_same_english_reply(self):
        fish = Mock(side_effect=audio.AudioSynthesisError("Synthetic Fish failure.", "fish_connection"))
        calls = []
        def piper(text, *, binary, model, name_mode):
            calls.append(text)
            return synthetic_wav()
        voice = IntegrationVoice(integration_config(offline_voice=True), Mock(),
                                 emit=lambda *a, **kw: None, fish_api_key=FISH_MARKER,
                                 fish_synthesizer=fish, piper_synthesizer=piper, player=self.player)
        self.addCleanup(voice.close)
        voice.speak("An unchanged English answer.")
        self.assertEqual(fish.call_args.kwargs["api_key"], FISH_MARKER)
        self.assertEqual(calls, ["An unchanged English answer."])
        self.assertEqual(voice._cache["expression"]["notice"], False)
        self.assertNotIn(FISH_MARKER, repr(voice._cache))
        voice.client.responses.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
