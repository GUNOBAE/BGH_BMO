"""Desktop startup policy and dry-run boundaries, no real home or devices."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.desktop_app import main, parser, _config


class DesktopCLITests(unittest.TestCase):
    def test_help_status_and_preparation_dry_run_never_open_files_or_keys(self):
        forbidden = Mock(side_effect=AssertionError("Forbidden production IO"))
        for args in (["--help"], ["--status"], ["--dry-run"], ["--live", "--prepare-greeting", "--dry-run"]):
            with self.subTest(args=args), patch("os.open", forbidden), \
                    patch("bmo_ai.private_credentials.resolve_credentials", forbidden), \
                    redirect_stdout(io.StringIO()) as output:
                if args == ["--help"]:
                    with self.assertRaises(SystemExit) as end:
                        main(args, host_factory=forbidden, browser_factory=forbidden)
                    self.assertEqual(end.exception.code, 0)
                else:
                    self.assertEqual(main(args, host_factory=forbidden, browser_factory=forbidden), 0)
                    result = json.loads(output.getvalue())
                    self.assertFalse(result["credentials_accessed"])
                    self.assertFalse(result["greeting"]["automatic_synthesis"])
        forbidden.assert_not_called()

    def test_approved_alsa_defaults_and_voice_policy_remain_explicit(self):
        config = _config(parser().parse_args([]))
        self.assertEqual((config.mic_device, config.speaker_device),
                         ("plughw:CARD=Device,DEV=0", "plughw:CARD=MAX98357A,DEV=0"))
        self.assertEqual((config.chat_model, config.reasoning_effort, config.fish_gain, config.fish_delivery,
                          config.fish_name_mode, config.max_output_tokens), ("gpt-6-luna", "low", .25, "soft", "beemo", 320))
        override = _config(parser().parse_args(["--mic-device", "fixture_input", "--speaker-device", "fixture_output", "--baseline"]))
        self.assertEqual((override.mic_device, override.speaker_device, override.chat_model, override.reasoning_effort),
                         ("fixture_input", "fixture_output", "gpt-4.1-mini", None))

    def test_unsafe_automatic_or_conflicting_modes_fail_before_lock(self):
        for args in ([], ["--simulate", "--live"], ["--prepare-greeting"],
                     ["--simulate", "--audition-greeting"], ["--live", "--demo-assets"],
                     ["--simulate", "--startup-timeout", "nan"], ["--live", "--greeting-timeout", "inf"],
                     ["--live", "--prepare-greeting", "--install-desktop"]):
            with self.subTest(args=args), patch("os.open", side_effect=AssertionError("No lock IO")), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as end:
                main(args)
            self.assertEqual(end.exception.code, 2)

    def test_installation_dry_run_never_implicitly_installs_or_resolves_credentials(self):
        with patch("bmo_ai.desktop_install.install_desktop", return_value={"status": "DRY_RUN"}) as install, \
                patch("os.open", side_effect=AssertionError("No lock/cache IO")), \
                patch("bmo_ai.private_credentials.resolve_credentials", side_effect=AssertionError("No keys")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--install-desktop", "--dry-run"]), 0)
        install.assert_called_once_with(install=False, simulate=False, frontend="auto")

    def test_installer_rejects_arm_options_instead_of_silently_dropping_them(self):
        cases = (["--arms-supervised"], ["--arm-legacy-bridge"],
                 ["--arm-reviewed-code-sha", "a"*64], ["--arm-power-off-ready"],
                 ["--simulate", "--emotion-arms"],
                 ["--live", "--arms-supervised", "--arm-legacy-bridge",
                  "--arm-reviewed-code-sha", "a"*64, "--arm-power-off-ready", "--emotion-arms"])
        forbidden = Mock(side_effect=AssertionError("No installer, lock or credentials"))
        for options in cases:
            for suffix in ([], ["--dry-run"]):
                args = ["--install-desktop", *options, *suffix]
                with self.subTest(args=args), patch("os.open", forbidden), \
                        patch("bmo_ai.desktop_install.install_desktop", forbidden), \
                        patch("bmo_ai.private_credentials.resolve_credentials", forbidden), \
                        redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as end:
                    main(args)
                self.assertEqual(end.exception.code, 2)
                self.assertIn("session-only", error.getvalue())
                self.assertIn("--install-desktop", error.getvalue())
        forbidden.assert_not_called()

    def test_direct_supervised_arm_session_still_has_explicit_dry_run_plan(self):
        forbidden = Mock(side_effect=AssertionError("No device or credential IO"))
        with patch("os.open", forbidden), \
                patch("bmo_ai.private_credentials.resolve_credentials", forbidden), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--live", "--arms-supervised", "--arm-legacy-bridge",
                "--arm-reviewed-code-sha", "a"*64, "--arm-power-off-ready", "--emotion-arms",
                "--dry-run"], host_factory=forbidden, browser_factory=forbidden), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["arm_legacy_bridge"]["requested"])
        self.assertTrue(result["emotion_arms"]["requested"])
        self.assertFalse(result["physical_features_enabled"])
        self.assertFalse(result["credentials_accessed"])
        forbidden.assert_not_called()

    def test_explicit_prepare_and_approval_are_separate_and_never_start_engine_or_player(self):
        cache = Mock()
        cache.prepare.return_value = {"approved": False, "sha256": "a"*64}
        cache.approve.return_value = {"approved": True, "sha256": "a"*64}
        resolver = Mock(return_value=SimpleNamespace(fish_api_key="synthetic-only"))
        forbidden = Mock(side_effect=AssertionError("No engine/client/player"))
        with tempfile.TemporaryDirectory() as directory, \
                patch("bmo_ai.greeting_cache.GreetingCache", return_value=cache), \
                patch("bmo_ai.greeting_cache.default_greeting_directory", return_value=Path(directory)/"cache"), \
                patch("bmo_ai.greeting.GreetingPlayer", forbidden), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--live", "--prepare-greeting"], lock_path=Path(directory)/"app.lock",
                credential_resolver=resolver, host_factory=forbidden, client_factory=forbidden), 0)
            self.assertEqual(main(["--live", "--approve-greeting", "a"*64], lock_path=Path(directory)/"app.lock",
                credential_resolver=forbidden, host_factory=forbidden, client_factory=forbidden), 0)
        cache.prepare.assert_called_once_with(live_opt_in=True, api_key="synthetic-only")
        cache.approve.assert_called_once_with("a"*64)
        resolver.assert_called_once_with()
        forbidden.assert_not_called()


if __name__ == "__main__":
    unittest.main()
