"""Check the actual shell handoff without GUI, keys, providers or devices."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from bmo_ai.desktop_install import desktop_install_plan, install_desktop


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

    def desktop_plan(self, *, character=True, install_target="desktop-and-applications"):
        project = self.root / "BMO candidate"
        project.mkdir()
        (project / "software").mkdir()
        launcher = project / "launch-desktop"
        launcher.write_bytes((ROOT / "launch-desktop").read_bytes())
        launcher.chmod(0o700)
        for environment, label in ((".venv", "legacy"), (".venv-device", "device")):
            binary = project / environment / "bin/python"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                              "print(json.dumps({'python': '" + label +
                              "', 'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n")
            binary.chmod(0o700)
        home = Path(self.env["HOME"])
        home.mkdir(mode=0o700)
        settings = dict(project_root=project, home=home, environ={"HOME": str(home)},
                        install_target=install_target)
        if character:
            settings.update(frontend="chromium", ptt_microphone=True,
                character_options=dict(character_life=True, wake_word=True,
                    wake_model_manifest=home / "private-model/manifest.json", wake_question_wait=3.))
        self.plan_settings = settings
        return desktop_install_plan(**settings)

    def run_desktop_plan(self, plan, *, explicit=False):
        environ = dict(self.env)
        if not explicit:
            environ.pop("BMO_PYTHON", None)  # A desktop click has no shell override.
        return subprocess.run(plan["command"], env=environ, cwd="/tmp",
                              text=True, capture_output=True, timeout=5, check=False)

    def test_real_character_desktop_plan_selects_candidate_interpreter_and_keeps_options(self):
        plan = self.desktop_plan()
        before = set(Path(self.env["HOME"]).rglob("*"))
        result = self.run_desktop_plan(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(result.stdout)
        self.assertEqual(captured["python"], "device")
        self.assertEqual(captured["argv"], ["-B", "-m", "bmo_ai.desktop_app", *plan["command"][1:]])
        self.assertIn("--wake-word", captured["argv"])
        self.assertEqual(plan["files_written"], 0)
        self.assertFalse(plan["autostart"] or plan["services_changed"])
        self.assertEqual(set(Path(self.env["HOME"]).rglob("*")), before)

    def test_real_noncharacter_desktop_plan_retains_legacy_interpreter(self):
        result = self.run_desktop_plan(self.desktop_plan(character=False))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["python"], "legacy")

    def test_applications_only_private_candidate_runs_same_interpreter_and_settings(self):
        plan = self.desktop_plan(install_target="applications-only")
        source = self.plan_settings['project_root'] / 'launch-desktop'
        source.chmod(0o775)
        install_desktop(**self.plan_settings, install=True)
        result = self.run_desktop_plan(plan)
        self.assertEqual(result.returncode, 0, result.stderr)
        captured = json.loads(result.stdout)
        self.assertEqual(captured['python'], 'device')
        self.assertEqual(captured['cwd'], str(self.plan_settings['project_root'] / 'software'))
        for option in ('--character-life', '--ptt-microphone', '--wake-word', '--wake-model-manifest'):
            self.assertIn(option, captured['argv'])
        self.assertEqual(source.stat().st_mode & 0o777, 0o775)

    def test_explicit_interpreter_wins_and_missing_candidate_never_uses_legacy(self):
        plan = self.desktop_plan()
        candidate = Path(plan["command"][0]).parent / ".venv-device/bin/python"
        candidate.unlink()
        result = self.run_desktop_plan(plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        result = self.run_desktop_plan(plan, explicit=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["argv"],
                         ["-B", "-m", "bmo_ai.desktop_app", *plan["command"][1:]])


if __name__ == "__main__":
    unittest.main()
