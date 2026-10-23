"""The public manual script forwards options without shell key arguments."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ManualShellLauncherTests(unittest.TestCase):
    def run_script(self, *arguments):
        with tempfile.TemporaryDirectory() as home:
            env = {"PATH": os.defpath, "HOME": home, "LC_ALL": "C.UTF-8",
                   "PYTHONDONTWRITEBYTECODE": "1", "BMO_PYTHON": sys.executable}
            result = subprocess.run([str(ROOT / "launch-bmo"), *arguments],
                                    cwd="/tmp", env=env, capture_output=True, text=True,
                                    timeout=6)
            self.assertFalse((Path(home) / ".config" / "bmo").exists())
            return result

    def test_effective_config_works_from_another_directory_without_key_storage(self):
        result = self.run_script("--emotion-mode", "auto", "--screen-profile", "r10",
                                 "--effective-config")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual((value["emotion_mode"], value["screen_profile"]), ("auto", "r10"))
        self.assertEqual((value["fish_gain"], value["fish_delivery"]), (.25, "soft"))
        self.assertFalse(any("key" in name.lower() for name in value))

    def test_no_implicit_live_start_without_an_explicit_execution_mode(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn("choose --simulate or --live", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
