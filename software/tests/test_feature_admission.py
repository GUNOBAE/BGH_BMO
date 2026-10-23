"""Validate a new target before stopping a working foreground resource."""
from unittest.mock import Mock, patch
import time
import unittest

from bmo_ai.features.contracts import CatalogItem
from bmo_ai.features.games import GamesFeature
from bmo_ai.features.host import FeatureHost
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class FeatureAdmissionTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("External IO forbidden")))

    def host(self, **kwargs):
        host = FeatureHost(**kwargs)
        self.addCleanup(host.close)
        return host

    def test_unknown_target_keeps_the_existing_game_and_audio_owner(self):
        host = self.host(demo_assets=True)
        host.execute(HostIntent("launch_game", "fixture_game_a"))
        generation = host.modes.generation
        result = host.execute(HostIntent("launch_game", "missing_game"))
        host.poll()
        self.assertEqual((result.status, result.code), ("rejected", "unknown_game"))
        self.assertEqual(host.modes.generation, generation)
        self.assertEqual(host.snapshot()["games"]["active_id"], "fixture_game_a")
        self.assertEqual(host.audio.owner, "game")

    def test_simulation_host_does_not_construct_a_real_game_runner(self):
        factory = Mock(side_effect=AssertionError("Real runner must not be constructed"))
        games = GamesFeature([CatalogItem("real_game", "Unverified local game", "READY", "ok")],
                             runner_factory=factory)
        host = self.host(games=games)
        result = host.execute(HostIntent("launch_game", "real_game"))
        self.assertEqual((result.status, result.code), ("rejected", "real_disabled"))
        factory.assert_not_called()
        self.assertEqual(host.audio.owner, None)

    def test_late_same_id_receipt_cannot_replace_success_with_a_stale_rejection(self):
        host = self.host(demo_assets=True)
        old_generation = host.modes.generation
        intent = HostIntent("launch_game", "fixture_game_a")
        first = host.execute(intent, request_id="same_logical_game", expected_generation=old_generation)
        late = host.execute(intent, request_id="same_logical_game", expected_generation=old_generation)
        self.assertEqual(late, first)
        self.assertEqual(host.games.snapshot()["active_id"], "fixture_game_a")

    def test_one_model_request_contains_only_trusted_capability_ids(self):
        voice = IntegrationVoice(integration_config(), None, simulate=True,
            emit=lambda *args, **kwargs: None, player=lambda command, on_start, **kwargs: on_start())
        host = self.host(demo_assets=True, voice=voice)
        self.assertTrue(host.start_question())
        deadline = time.monotonic() + 2
        while host.session.busy and time.monotonic() < deadline:
            host.poll()
            time.sleep(.001)
        host.poll()
        self.assertFalse(host.session.busy)
        self.assertEqual(len(host.client.requests), 1)
        instructions = host.client.requests[0]["instructions"]
        self.assertIn('"real_features_enabled":false', instructions)
        self.assertIn('"id":"fixture_game_a"', instructions)
        self.assertIn('"id":"fixture_song_a"', instructions)
        self.assertNotIn("/tmp/", instructions)
