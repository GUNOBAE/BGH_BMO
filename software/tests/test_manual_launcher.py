"""Manual credential routing with fake keys and private test locks only."""
from contextlib import redirect_stderr, redirect_stdout
import fcntl
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai import manual_launcher
from bmo_ai.cloud import CloudError
from bmo_ai.private_credentials import CredentialError, Credentials


OPENAI_MARKER = "synthetic-openai-launcher-marker"
FISH_MARKER = "synthetic-fish-launcher-marker"


class ManualLauncherTests(unittest.TestCase):
    def setUp(self):
        self.credentials = Credentials(OPENAI_MARKER, FISH_MARKER)
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.audio.record_wav",
                       "bmo_ai.audio.transcribe", "bmo_ai.fish_tts.synthesize_wav",
                       "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Live IO forbidden")))

    def capture(self, arguments, **settings):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            result = manual_launcher.main(arguments, **settings)
        text = output.getvalue() + errors.getvalue()
        self.assertNotIn(OPENAI_MARKER, text)
        self.assertNotIn(FISH_MARKER, text)
        return result, output.getvalue(), errors.getvalue()

    def fake_session(self):
        snapshot = Mock(return_value={"sequence": 0, "phase": "idle", "playback": "idle"})
        return SimpleNamespace(brain=SimpleNamespace(history=[]), busy=False,
                               start=Mock(return_value=True), poll=Mock(return_value=()),
                               snapshot=snapshot, browser_snapshot=snapshot, close=Mock())

    def test_setup_is_standalone_force_prompt_and_does_not_start_application_or_api(self):
        with patch.object(manual_launcher, "resolve_credentials", return_value=self.credentials) as resolve, \
             patch.object(manual_launcher.integration, "main", side_effect=AssertionError("No app during setup")) as app, \
             patch("bmo_ai.cloud.create_client", side_effect=AssertionError("No SDK during setup")) as client:
            result, output, errors = self.capture(["--setup-credentials"])
        self.assertEqual(result, 0)
        self.assertIn("Private credentials saved", output)
        self.assertEqual(errors, "")
        resolve.assert_called_once_with(force_prompt=True)
        app.assert_not_called()
        client.assert_not_called()

    def test_setup_rejects_mixed_commands_before_resolving(self):
        with patch.object(manual_launcher, "resolve_credentials") as resolve, \
             patch.object(manual_launcher.integration, "main") as app:
            for other in ("--live", "--simulate", "--help", "--effective-config"):
                with self.subTest(other=other):
                    result, _, errors = self.capture(["--setup-credentials", other])
                    self.assertEqual(result, 2)
                    self.assertIn("on its own", errors)
        resolve.assert_not_called()
        app.assert_not_called()

    def test_setup_errors_use_only_fixed_code_message_and_do_not_retry(self):
        for code in ("cancelled", "input_required", "input_unavailable", "store_write_failed"):
            with self.subTest(code=code), \
                 patch.object(manual_launcher, "resolve_credentials", side_effect=CredentialError(code)) as resolve, \
                 patch.object(manual_launcher.integration, "main") as app:
                result, _, errors = self.capture(["--setup-credentials"])
                self.assertEqual(result, 2)
                self.assertIn(code, errors)
                self.assertNotIn("Traceback", errors)
                resolve.assert_called_once_with(force_prompt=True)
                app.assert_not_called()

    def test_ordinary_arguments_delegate_without_resolving_or_environment_changes(self):
        arguments = ["--simulate", "--no-web", "--emotion-mode", "auto", "--turns", "2"]
        with patch.object(manual_launcher, "resolve_credentials") as resolve, \
             patch.object(manual_launcher.integration, "main", return_value=0) as app:
            result, _, _ = self.capture(arguments, lock_path="/synthetic/lock")
        self.assertEqual(result, 0)
        resolve.assert_not_called()
        app.assert_called_once_with(arguments, lock_path="/synthetic/lock", credential_resolver=resolve)

    def test_real_help_and_effective_config_paths_do_not_resolve_or_create_client(self):
        with patch.object(manual_launcher, "resolve_credentials", side_effect=AssertionError("No credentials here")) as resolve, \
             patch("bmo_ai.cloud.create_client", side_effect=AssertionError("No client here")) as client:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit:
                manual_launcher.main(["--help"])
            self.assertEqual(exit.exception.code, 0)
            result, output, _ = self.capture(["--effective-config", "--emotion-mode", "auto"])
            self.assertEqual(result, 0)
            config = json.loads(output)
            self.assertEqual(config["chat_model"], "gpt-6-luna")
            self.assertEqual(config["fish_gain"], .25)
            self.assertEqual(config["emotion_mode"], "auto")
            resolve.assert_not_called()
            client.assert_not_called()

    def test_real_simulation_path_does_not_resolve_or_create_cloud_client(self):
        session = self.fake_session()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(manual_launcher, "resolve_credentials", side_effect=AssertionError("No simulation credentials")) as resolve, \
             patch("bmo_ai.cloud.create_client", side_effect=AssertionError("No simulation SDK")) as client, \
             patch("bmo_ai.integration_session.IntegrationSession", return_value=session) as factory:
            result, _, _ = self.capture(["--simulate", "--no-web", "--turns", "1"],
                                        lock_path=Path(directory) / "owned.lock")
        self.assertEqual(result, 0)
        resolve.assert_not_called()
        client.assert_not_called()
        self.assertNotIn("fish_api_key", factory.call_args.kwargs)
        self.assertTrue(factory.call_args.kwargs["simulate"])
        session.close.assert_called_once()

    def test_live_resolves_after_owned_lock_and_passes_keys_directly_to_providers(self):
        session, client = self.fake_session(), Mock()
        server = SimpleNamespace(url="http://127.0.0.1:0/", start=Mock(), close=Mock())
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "owned.lock"

            def resolve():
                # Resolution must happen after the ordinary per-user admission
                # lock, so an existing launcher never triggers another prompt.
                with lock_path.open("r+") as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self.credentials

            with patch.object(manual_launcher, "resolve_credentials", side_effect=resolve) as resolver, \
                 patch("bmo_ai.cloud.create_client", return_value=client) as factory, \
                 patch("bmo_ai.integration_session.IntegrationSession", return_value=session) as sessions, \
                 patch("bmo_ai.integration_face.FaceServer", return_value=server), \
                 patch("bmo_ai.integration.sys.stdin", io.StringIO("/quit\n")), \
                 patch("bmo_ai.integration.select.select", return_value=([object()], [], [])):
                result, _, _ = self.capture(["--live", "--emotion-mode", "auto"], lock_path=lock_path)
        self.assertEqual(result, 0)
        resolver.assert_called_once_with()
        self.assertEqual(factory.call_args.kwargs, {"api_key": OPENAI_MARKER})
        self.assertEqual(sessions.call_args.kwargs["fish_api_key"], FISH_MARKER)
        self.assertEqual(sessions.call_args.args[1], client)
        session.close.assert_called_once()
        server.close.assert_called_once()

    def test_already_running_live_launcher_rejects_before_credential_access(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "owned.lock"
            with lock_path.open("w") as owner, \
                 patch.object(manual_launcher, "resolve_credentials") as resolve, \
                 patch("bmo_ai.cloud.create_client") as client:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result, _, errors = self.capture(["--live"], lock_path=lock_path)
        self.assertEqual(result, 2)
        self.assertIn("already running", errors)
        resolve.assert_not_called()
        client.assert_not_called()

    def test_missing_noninteractive_credentials_fail_once_before_client_start(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(manual_launcher, "resolve_credentials", side_effect=CredentialError("credentials_missing")) as resolve, \
             patch("bmo_ai.cloud.create_client") as client, \
             patch("bmo_ai.integration_session.IntegrationSession") as session:
            result, _, errors = self.capture(["--live"], lock_path=Path(directory) / "owned.lock")
        self.assertEqual(result, 2)
        self.assertIn("credentials_missing", errors)
        self.assertNotIn("Traceback", errors)
        resolve.assert_called_once_with()
        client.assert_not_called()
        session.assert_not_called()

    def test_authentication_failure_does_not_restart_credential_resolution(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(manual_launcher, "resolve_credentials", return_value=self.credentials) as resolve, \
             patch("bmo_ai.cloud.create_client", side_effect=CloudError("authentication", "Synthetic authentication failure.")) as client:
            result, _, errors = self.capture(["--live"], lock_path=Path(directory) / "owned.lock")
        self.assertEqual(result, 1)
        self.assertIn("authentication failure", errors)
        resolve.assert_called_once_with()
        client.assert_called_once()


if __name__ == "__main__":
    unittest.main()
