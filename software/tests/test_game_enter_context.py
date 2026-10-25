"""Documented Enter selection stays in the game router and never starts input."""
import unittest
from unittest.mock import Mock, patch
from bmo_ai.features.host import FeatureHost, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.features.games import GamesFeature


class GameEnterTests(unittest.TestCase):
    def test_start_selects_only_a_ready_game_and_release_never_launches(self):
        game=GamesFeature(fixture=True)
        self.assertEqual(game.menu_input('START',False)['action'],'none')
        self.assertEqual(game.menu_input('START')['action'],'launch')
        self.assertEqual(GamesFeature().menu_input('START')['action'],'select')

    def test_enter_routes_menu_game_and_escape_back_without_microphone(self):
        with patch('subprocess.Popen',side_effect=AssertionError('No process')):
            host=FeatureHost(demo_assets=True,runtime=RuntimePolicy(conversation_ptt_microphone=True))
            try:
                source=host.microphone.source_factory=Mock(side_effect=AssertionError('No mic'))
                host.execute(HostIntent('open_game_menu'))
                host.control(HostControl('input',{'key':'START','pressed':True}),'enter_game')
                self.assertEqual(host.modes.mode,'game')
                host.control(HostControl('input',{'key':'B','pressed':True}),'game_b')
                self.assertEqual(host.modes.mode,'game')
                host.control(HostControl('input',{'key':'ESC','pressed':True}),'escape_game')
                host.poll()
                self.assertEqual(host.modes.mode,'game_menu')
                host.control(HostControl('input',{'key':'ESC','pressed':True}),'escape_menu')
                self.assertEqual(host.modes.mode,'conversation')
                source.assert_not_called()
                self.assertEqual(host.session.worker.turn,0)
            finally:host.close()


if __name__=='__main__':unittest.main()
