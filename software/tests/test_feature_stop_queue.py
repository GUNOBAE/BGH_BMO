"""Stop has priority and invalidates launches admitted in its old generation."""
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.games import GamesFeature, FakeGameRunner
from bmo_ai.features.host import FeatureHost, HostControl
from bmo_ai.features.reply import HostIntent


class StopQueueTests(unittest.TestCase):
    def test_stop_blocks_queued_game_launches_in_either_admission_order(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client"):
            self.enterContext(patch(target, side_effect=AssertionError("External IO forbidden")))
        for order in (("stop", "game"), ("game", "stop")):
            with self.subTest(order=order):
                factory = Mock(side_effect=lambda item: FakeGameRunner())
                host = FeatureHost(games=GamesFeature(fixture=True, runner_factory=factory))
                try:
                    for name in order:
                        payload = HostControl("stop") if name == "stop" else HostIntent("launch_game", "fixture_game_a")
                        host.queue_command("queued_" + name, payload)
                    host.poll()
                    self.assertEqual(host.modes.mode, "conversation")
                    self.assertIsNone(host.audio.owner)
                    factory.assert_not_called()
                finally:
                    host.close()
