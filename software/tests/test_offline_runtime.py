"""Unified launcher and real voice boundary, entirely injected offline IO.

No production credential resolver, network connection or ALSA/device is used.
"""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import audio
from bmo_ai.feature_launcher import main, parser
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy, DEVICE_CAPABILITIES
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav, _player_environment


class OfflineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.hosts = []
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.cloud.create_client", "getpass.getpass",
                       "bmo_ai.private_credentials.resolve_credentials",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Production IO forbidden")))

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def finish(self, host):
        deadline = time.monotonic() + 4
        while host.session.busy and time.monotonic() < deadline:
            host.poll(); time.sleep(.001)
        self.assertFalse(host.session.busy)
        host.poll()

    def test_new_entrypoint_defaults_r10_legacy_remains_original(self):
        from bmo_ai.integration import parser as legacy
        self.assertEqual(parser().parse_args([]).screen_profile, "r10")
        self.assertEqual(legacy().parse_args([]).screen_profile, "v13.2")
        self.assertEqual(parser().parse_args(["--screen-profile", "v13.2"]).screen_profile, "v13.2")

    def test_live_requires_explicit_client_and_cannot_grant_hardware(self):
        with self.assertRaises(FeatureError):
            FeatureHost(runtime=RuntimePolicy(False))
        for capability in DEVICE_CAPABILITIES:
            with self.subTest(capability=capability), self.assertRaises(FeatureError):
                RuntimePolicy(False).require(capability)
        with self.assertRaises(FeatureError):
            RuntimePolicy(simulate=1)
        self.assertFalse(RuntimePolicy(False).public()["real_features_enabled"])

    def test_live_host_rejects_device_intents_before_adapter_calls(self):
        host = FeatureHost(client=FeatureSimulatedClient(), runtime=RuntimePolicy(False))
        self.hosts.append(host)
        for target in ("take_photo", "set_tracking", "set_preview"):
            self.enterContext(patch.object(host.camera, target, side_effect=AssertionError("Camera denied")))
        self.enterContext(patch.object(host.motion, "submit", side_effect=AssertionError("Arms denied")))
        self.enterContext(patch.object(host.games, "start", side_effect=AssertionError("Game denied")))
        self.enterContext(patch.object(host.media, "start", side_effect=AssertionError("Media denied")))
        for index, (name, target) in enumerate((("take_photo", None), ("start_face_tracking", None),
                ("arm_gesture", "wave"), ("launch_game", "fixture_game_a"), ("play_song", "fixture_song_a"))):
            result = host.execute(HostIntent(name, target), request_id=f"denied_{index}")
            self.assertEqual((result.status, result.code, result.executed), ("rejected", "capability_denied", False))
        result = host.control(HostControl("preview", True), "preview_denied")
        self.assertEqual(result.code, "capability_denied")
        self.assertFalse(host.snapshot()["simulation"])
        self.assertTrue(all(host.snapshot()["capabilities"][key] == "denied" for key in DEVICE_CAPABILITIES))

    def test_help_status_simulation_never_resolve_keys(self):
        forbidden = Mock(side_effect=AssertionError("No secret access"))
        for args in (["--status"], ["--live", "--status"], ["--effective-config", "--baseline"], ["--help"]):
            with redirect_stdout(io.StringIO()), patch("os.open", side_effect=AssertionError("No file open")):
                try:
                    self.assertEqual(main(args, credential_resolver=forbidden, host_factory=forbidden), 0)
                except SystemExit as error:
                    self.assertEqual(error.code, 0)
        forbidden.assert_not_called()

    def test_injected_live_launch_reuses_one_brain_cache_and_owns_one_player(self):
        # Exercise the --live wiring and real IntegrationVoice branch without
        # resolving a real store, recording a device or sending a paid request.
        counts = dict(record=0, stt=0, fish=0, player=0, reaped=0)
        client = FeatureSimulatedClient([dict(version=1, speech="Synthetic offline answer.",
            emotion="crying", gesture="wave", intent={"name": "take_photo", "target": None})])
        def recorder(*args, **kwargs):
            counts["record"] += 1
            return synthetic_wav()
        def transcriber(*args, **kwargs):
            counts["stt"] += 1
            return "Synthetic captured input."
        def fish(text, **kwargs):
            self.assertEqual(kwargs["api_key"], "fixture-fish-only")
            self.assertEqual(kwargs["reference_id"], integration_config().fish_voice_id)
            counts["fish"] += 1
            return synthetic_wav()
        def player(command, **kwargs):
            self.assertEqual(command[0], "aplay")  # inspect, never execute it
            counts["player"] += 1
            self.assertNotIn("GH_TOKEN", kwargs["env"])
            audio._run([sys.executable, "-c", "pass"], **kwargs)
            counts["reaped"] += 1
        def factory(**settings):
            voice = IntegrationVoice(settings["config"], settings["client"], simulate=False,
                emit=lambda *args, **kw: None, fish_synthesizer=fish,
                fish_api_key=settings["fish_api_key"], player=player)
            host = FeatureHost(**settings, voice=voice)
            self.hosts.append(host)
            self.assertTrue(host.start_question())
            self.finish(host)
            self.assertEqual(host.session.snapshot()["emotion"], "crying")
            self.assertEqual(host._last_result["code"], "capability_denied")
            self.assertFalse(host.camera.snapshot()["on"])
            brain, history = host.session.brain, list(host.session.brain.history)
            host.control(HostControl("repeat"), "repeat_only")
            self.finish(host)
            self.assertIs(host.session.brain, brain)
            self.assertEqual(host.session.brain.history, history)
            self.assertEqual(host._last_result["completion"], "voice_playback")
            self.assertEqual(host._last_result["status"], "succeeded")
            self.assertFalse(host._last_result["simulated"])
            self.assertEqual(host.session.snapshot()["emotion"], "crying")
            return host
        resolver = Mock(return_value=SimpleNamespace(openai_api_key="fixture-openai-only", fish_api_key="fixture-fish-only"))
        client_factory = Mock(return_value=client)
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdin", io.StringIO("/quit\n")), \
                patch("select.select", return_value=([object()], [], [])), \
                patch("bmo_ai.audio.record_wav", side_effect=recorder), \
                patch("bmo_ai.audio.transcribe", side_effect=transcriber), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            code = main(["--live", "--no-web"], lock_path=Path(directory)/"lock", host_factory=factory,
                        credential_resolver=resolver, client_factory=client_factory)
        self.assertEqual(code, 0, error.getvalue())
        resolver.assert_called_once_with()
        self.assertEqual(client_factory.call_args.kwargs, {"api_key": "fixture-openai-only"})
        self.assertEqual(counts, dict(record=1, stt=1, fish=1, player=2, reaped=2))
        self.assertEqual(len(client.requests), 1)
        self.assertNotIn("fixture-fish-only", output.getvalue()+error.getvalue())
        self.assertNotIn("fixture-openai-only", output.getvalue()+error.getvalue())

    def test_player_environment_is_allowlisted_including_unknown_secret_names(self):
        with patch.dict(os.environ, {"GH_TOKEN": "synthetic", "CUSTOM_SECRET": "synthetic",
                                      "OPENAI_API_KEY": "synthetic", "FISH_API_KEY": "synthetic"}):
            child = _player_environment()
        self.assertFalse(set(child) & {"GH_TOKEN", "CUSTOM_SECRET", "OPENAI_API_KEY", "FISH_API_KEY"})


if __name__ == "__main__":
    unittest.main()
