"""Check the actual shell handoff without GUI, keys, providers or devices."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CharacterCameraLauncherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.python = self.root / "python stub"
        # The script is the executable boundary. It records arguments and exits;
        # the real Desktop and every physical resource remain unopened.
        self.python.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                               "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n")
        self.python.chmod(0o700)
        self.env = dict(os.environ, HOME=str(self.root / "operator home"),
                        BMO_PYTHON=str(self.python))

    def launch(self, *extra):
        return subprocess.run(["/bin/sh", str(ROOT / "launch-character-life"), *extra],
            env=self.env, text=True, capture_output=True, timeout=5, check=False)

    def test_one_existing_desktop_with_on_demand_camera_and_no_implicit_arms_or_wake(self):
        result = self.launch("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(result.stdout)
        argv = captured["argv"]
        self.assertEqual(captured["cwd"], str(ROOT / "software"))
        self.assertEqual(argv[:3], ["-B", "-m", "bmo_ai.desktop_app"])
        for option in ("--live", "--character-life", "--ptt-microphone",
                       "--camera-supervised", "--camera-video-supervised", "--dry-run"):
            self.assertIn(option, argv)
        self.assertEqual(argv[argv.index("--photo-directory") + 1],
                         str(Path(self.env["HOME"]) / ".local/share/bmo/photos"))
        self.assertEqual(argv[argv.index("--video-directory") + 1],
                         str(Path(self.env["HOME"]) / ".local/share/bmo/videos"))
        for absent in ("--arms-supervised", "--arm-legacy-bridge", "--emotion-arms",
                       "--wake-word", "--prepare-greeting", "--approve-greeting"):
            self.assertNotIn(absent, argv)
        self.assertFalse(Path(self.env["HOME"]).exists())

    def test_existing_explicit_wake_and_arm_arguments_reach_unchanged_desktop_gate(self):
        extra = ["--wake-word", "--wake-model-manifest", "/PRIVATE/model path.json",
                 "--arms-supervised", "--arm-legacy-bridge", "--arm-reviewed-code-sha",
                 "a" * 64, "--arm-power-off-ready", "--emotion-arms"]
        result = self.launch(*extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["argv"][-len(extra):], extra)

    def test_missing_interpreter_stops_with_clear_error(self):
        self.env["BMO_PYTHON"] = str(self.root / "missing-python")
        result = self.launch()
        self.assertEqual(result.returncode, 2)
        self.assertIn(".venv-device/bin/python", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
