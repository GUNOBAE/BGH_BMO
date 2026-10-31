"""Reviewable character launcher plans, never the user's Desktop or devices."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.desktop_install import desktop_install_plan, install_desktop, DesktopInstallError


class CharacterInstallTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="character-install-unit-")))
        self.project = self.home / "BMO Project"
        self.project.mkdir(mode=0o700)
        self.launcher = self.project / "launch-desktop"
        self.launcher.write_text("#!/bin/sh\n# fixture only, never executed\n")
        self.launcher.chmod(0o700)
        self.settings = dict(project_root=self.project, home=self.home, environ={"HOME":str(self.home)})
        for target in ("subprocess.Popen", "socket.socket.connect", "getpass.getpass",
                       "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("real execution forbidden")))

    def plan(self, **changes):
        return desktop_install_plan(**dict(self.settings, **changes))

    def test_none_empty_or_disabled_preserve_existing_default_plan_exactly(self):
        before = set(self.home.rglob("*"))
        original = self.plan()
        for options in (None, {}, {"character_life":False}):
            self.assertEqual(self.plan(character_options=options), original)
        self.assertEqual(original["command"], [str(self.launcher),"--live"])
        self.assertFalse(original["autostart"] or original["services_changed"])
        self.assertEqual(set(self.home.rglob("*")), before)

    def test_all_explicit_character_options_survive_dry_plan_in_stable_order(self):
        values = dict(wake_model_manifest=self.home/"models/wake manifest.json", wake_word=True,
            presence_mode="quiet", idle_voice=True, voice_bank_directory=self.home/"voice bank",
            plain_voice=True, ptt_vad_profile="balanced", ptt_trace=True, character_life=True)
        before = dict(values)
        report = self.plan(ptt_microphone=True, character_options=values)
        self.assertEqual(report["command"], [str(self.launcher),"--live","--ptt-microphone","--playable-games",
            "--character-life","--ptt-trace","--ptt-vad-profile","balanced","--plain-voice",
            "--voice-bank-directory",str(self.home/"voice bank"),"--idle-voice","--presence-mode","quiet",
            "--wake-word","--wake-model-manifest",str(self.home/"models/wake manifest.json")])
        self.assertEqual(report["files_written"],0)
        self.assertIn('"--voice-bank-directory" "'+str(self.home/"voice bank")+'"',report["template"])
        self.assertIn("X-GNOME-Autostart-enabled=false",report["template"])
        self.assertEqual(values,before)
        self.assertEqual(self.plan(ptt_microphone=True,character_options=dict(reversed(tuple(values.items())))),report)
        self.assertFalse((self.home/"Desktop").exists())
        self.assertFalse((self.home/"voice bank").exists())

    def test_false_optional_switches_stay_false_without_becoming_truthy_flags(self):
        report=self.plan(character_options=dict(character_life=True,ptt_trace=False,plain_voice=False,
            idle_voice=False,wake_word=False,ptt_vad_profile="baseline",presence_mode="off"))
        self.assertEqual(report["command"][2:], ["--playable-games","--character-life","--ptt-vad-profile","baseline","--presence-mode","off"])

    def test_install_false_routes_character_configuration_through_same_plan(self):
        options=dict(character_life=True,ptt_trace=True,plain_voice=True)
        report=install_desktop(**self.settings,install=False,character_options=options)
        self.assertEqual(report,self.plan(character_options=options))
        self.assertEqual(report["activation"],"NOT_RUN")

    def test_invalid_types_unknown_options_and_missing_feature_optin_fail_before_io(self):
        invalid=([],"--character-life",{"api_key":"private_fixture"},{"character_life":1},
            {"character_life":True,"ptt_trace":1},{"character_life":True,"plain_voice":"false"},
            {"character_life":True,"idle_voice":None},{"character_life":True,"wake_word":1},
            {"character_life":True,"ptt_vad_profile":"auto"},
            {"character_life":True,"presence_mode":"silent"},
            {"ptt_trace":True},{"character_life":False,"plain_voice":False})
        with patch("bmo_ai.desktop_install._directory",side_effect=AssertionError("invalid configuration must not access files")):
            for options in invalid:
                with self.subTest(options=options),self.assertRaises(DesktopInstallError) as error:
                    self.plan(character_options=options)
                self.assertEqual(error.exception.code,"desktop_invalid_config")
                self.assertNotIn("private_fixture",str(error.exception))

    def test_paths_reject_relative_traversal_shell_field_and_null_values(self):
        for key in ("voice_bank_directory","wake_model_manifest"):
            for value in ("relative/asset",self.home/".."/"elsewhere",str(self.home/"model%u.json"),
                          str(self.home/"model$(command).json"),None):
                options=dict(character_life=True,**{key:value})
                if key=="wake_model_manifest":options["wake_word"]=True
                with self.subTest(key=key,value=value),self.assertRaises(DesktopInstallError):
                    self.plan(ptt_microphone=True,character_options=options)

    def test_live_wake_requires_explicit_ptt_optin_but_simulated_wake_needs_no_device(self):
        options=dict(character_life=True,wake_word=True)
        with self.assertRaises(DesktopInstallError):self.plan(character_options=options)
        self.assertIn("--wake-word",self.plan(ptt_microphone=True,character_options=options)["command"])
        simulation=self.plan(simulate=True,character_options=options)
        self.assertIn("--simulate",simulation["command"])
        self.assertIn("--wake-word",simulation["command"])
        self.assertFalse(simulation["autostart"] or simulation["services_changed"])

    def test_manifest_requires_wake_and_real_manifest_is_not_accepted_in_simulation(self):
        manifest=self.home/"verified-models/manifest.json"
        for settings,options in (({},dict(character_life=True,wake_model_manifest=manifest)),
                ({"ptt_microphone":True},dict(character_life=True,wake_word=False,wake_model_manifest=manifest)),
                ({"simulate":True},dict(character_life=True,wake_word=True,wake_model_manifest=manifest))):
            with self.subTest(settings=settings),self.assertRaises(DesktopInstallError):
                self.plan(**settings,character_options=options)
        self.assertFalse(manifest.parent.exists())


if __name__=="__main__":unittest.main()
