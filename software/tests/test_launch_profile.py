"""The actual shell, direct module and installed private icon agree on games."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bmo_ai.desktop_app import parser, public_plan
from bmo_ai.desktop_install import install_desktop
from bmo_ai.launch_profile import playable_games_enabled


ROOT = Path(__file__).resolve().parents[2]


class LaunchProfileTests(unittest.TestCase):
    def test_character_module_profile_includes_reviewed_games_without_granting_devices(self):
        args = parser().parse_args(['--character-life', '--live', '--dry-run'])
        value = public_plan(args)
        self.assertTrue(value['games']['enabled'])
        self.assertEqual(value['games']['bundle_validation'], 'NOT_CHECKED')
        self.assertFalse(value['games']['automatic_download'])
        self.assertFalse(value['physical_features_enabled'])
        self.assertFalse(args.ptt_microphone or args.camera_supervised or args.arms_supervised
                         or args.wake_word or args.music_supervised)
        self.assertFalse(parser().parse_args([]).playable_games)
        self.assertFalse(parser().parse_args(['--character-life', '--no-playable-games']).playable_games)
        self.assertTrue(parser().parse_args(['--playable-games']).playable_games)
        for invalid in (0, 1, 'yes', []):
            with self.assertRaises(ValueError):
                playable_games_enabled(invalid, character_life=True)

    def test_actual_shell_and_direct_module_resolve_same_profile_and_explicit_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, HOME=directory, BMO_PYTHON=sys.executable)
            for extra, expected in (([], True), (['--no-playable-games'], False)):
                result = subprocess.run(['/bin/sh', str(ROOT/'launch-character-life'), '--dry-run', *extra],
                    env=env, cwd='/tmp', capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                plan = json.loads(result.stdout)
                self.assertEqual(plan['games']['enabled'], expected)
                self.assertFalse(plan['processes_started'] or plan['ports_opened'])
                direct = public_plan(parser().parse_args(['--character-life', '--live', '--dry-run', *extra]))
                self.assertEqual(plan['games'], direct['games'])

    def test_private_icon_executes_same_module_profile_and_persists_explicit_disable(self):
        for requested, expected in ((None, True), (False, False)):
            with self.subTest(requested=requested), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                env = dict(os.environ, HOME=directory, BMO_PYTHON=sys.executable)
                settings = dict(project_root=ROOT, home=home, environ={'HOME': directory},
                    simulate=True, character_options={'character_life': True},
                    install_target='applications-only')
                if requested is not None:
                    settings['playable_games'] = requested
                report = install_desktop(**settings, install=True)
                self.assertEqual(report['playable_games_opt_in'], expected)
                result = subprocess.run([*report['command'], '--dry-run'], env=env, cwd='/tmp',
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['games']['enabled'], expected)
                self.assertFalse((home/'Desktop').exists())


if __name__ == '__main__':
    unittest.main()
