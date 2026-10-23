"""Closing during an owned playback cancels it and admits no new commands."""
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class FeatureHostCloseTests(unittest.TestCase):
    def test_close_during_owned_mock_player_releases_focus_and_rejects_late_input(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                       "bmo_ai.audio.record_wav", "bmo_ai.fish_tts.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("External IO forbidden")))
        entered = Event()
        def player(command, *, on_start, cancel_check, **settings):
            on_start()
            entered.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                cancel_check()
                time.sleep(.001)
            raise AssertionError("Mock player cancellation did not complete")
        voice = IntegrationVoice(integration_config(), None, simulate=True,
                                 emit=lambda *args, **kwargs: None, player=player)
        host = FeatureHost(voice=voice)
        self.addCleanup(host.close)
        self.assertTrue(host.start_question())
        self.assertTrue(entered.wait(1))
        host.close()
        self.assertIsNone(host.audio.owner)
        self.assertFalse(host.session.worker.worker_alive)
        self.assertFalse(host.start_question())
        with self.assertRaises(FeatureError):
            host.queue_command("late-command", HostIntent("open_game_menu"))
