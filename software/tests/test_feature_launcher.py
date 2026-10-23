"""Actual feature launcher/script with credential-free child environments."""
from contextlib import redirect_stderr, redirect_stdout
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.feature_launcher import main


ROOT = Path(__file__).resolve().parents[2]
HAS_SDK = importlib.util.find_spec("openai") is not None and importlib.util.find_spec("httpx") is not None

CHILD = r'''
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

allowed_environment = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH",
                       "PYTHONDONTWRITEBYTECODE", "BMO_PYTHON", "BMO_FEATURE_TEST_LOCK",
                       "PWD", "OLDPWD"}
if set(os.environ) - allowed_environment:
    raise AssertionError("Unexpected feature-launcher environment field names: "
                         + ",".join(sorted(set(os.environ) - allowed_environment)))
if "PWD" in os.environ and Path(os.environ["PWD"]).resolve() != Path.cwd():
    raise AssertionError("Shell working directory must match its actual cwd")
arguments = sys.argv[1:]
if arguments[:3] == ["-B", "-m", "bmo_ai.feature_launcher"]:
    arguments = arguments[3:]
use_sdk = bool(arguments and arguments[0] == "__sdk_fixture__")
if use_sdk:
    arguments = arguments[1:]
accelerate = bool(arguments and arguments[0] == "__fixture_clock__")
if accelerate:
    arguments = arguments[1:]

from bmo_ai.feature_launcher import main
from bmo_ai.features.host import FeatureHost

children, hosts, spies, wire = [], [], [], []
original_popen, original_open = subprocess.Popen, os.open
def harmless_child(command, *args, **kwargs):
    if command != [sys.executable, "-c", "import time; time.sleep(0.8)"]:
        raise AssertionError("Only the owned harmless Python fixture may spawn")
    if set(kwargs.get("env", {})) - allowed_environment:
        raise AssertionError("Unexpected player environment field")
    child = original_popen(command, *args, **kwargs)
    children.append(child)
    return child

def no_credential_file(path, *args, **kwargs):
    if isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)).name in (
            "credentials.json", ".credentials.lock"):
        raise AssertionError("Production credential store must stay unopened")
    return original_open(path, *args, **kwargs)

class FixtureClock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
fixture_clock = FixtureClock()
clock = fixture_clock if accelerate else time.monotonic

def sdk_client():
    import httpx
    from openai import OpenAI
    def handler(request):
        if request.url.path != "/v1/responses":
            raise AssertionError("Only mock Responses requests are permitted")
        body = json.loads(request.read())
        wire.append(body)
        reply = {"version": 1, "speech": "A clean synthetic reply.", "emotion": "crying",
                 "gesture": "none", "intent": None}
        return httpx.Response(200, json={
            "id": "resp_fixture", "object": "response", "created_at": 0,
            "model": body["model"], "status": "completed", "error": None,
            "incomplete_details": None, "store": False,
            "output": [{"id": "msg_fixture", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": json.dumps(reply), "annotations": []}]}],
        })
    return OpenAI(api_key="offline-feature-sdk-fixture", base_url="https://api.openai.com/v1",
                  max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

with ExitStack() as guards:
    for target in ("socket.socket.connect", "socket.socket.connect_ex", "bmo_ai.cloud.create_client",
                   "bmo_ai.private_credentials.resolve_credentials", "getpass.getpass",
                   "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                   "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav",
                   "bmo_ai.features.camera.backends.Picamera2Backend.open",
                   "bmo_ai.features.camera.backends.OpenCVFaceDetector.detect"):
        guards.enter_context(patch(target, side_effect=AssertionError("Live operation forbidden")))
    guards.enter_context(patch("subprocess.Popen", side_effect=harmless_child))
    guards.enter_context(patch("os.open", side_effect=no_credential_file))
    def factory(**settings):
        client = sdk_client() if use_sdk else None
        host = FeatureHost(**settings, **({"client": client} if client is not None else {}))
        spy = guards.enter_context(patch.object(host.client.responses, "create", wraps=host.client.responses.create))
        spies.append(spy)
        host.audit = {"modes": [], "intent_names": [], "control_names": [], "photo_saved": False,
                      "history_sizes": [], "brain_ids": set(), "rejected": 0}
        real_poll, real_execute, real_control = host.poll, host.execute, host.control
        def execute(intent, **kwargs):
            host.audit["intent_names"].append(intent.name)
            result = real_execute(intent, **kwargs)
            host.audit["rejected"] += result.status in ("rejected", "failed", "unsupported")
            return result
        def control(value, request):
            host.audit["control_names"].append(value.action)
            result = real_control(value, request)
            host.audit["rejected"] += result.status in ("rejected", "failed", "unsupported")
            return result
        def poll():
            if accelerate:
                fixture_clock.now += 1.1 if host.modes.mode == "photo" else .01
            result = real_poll()
            state = host.snapshot()
            if not host.audit["modes"] or host.audit["modes"][-1] != state["mode"]:
                host.audit["modes"].append(state["mode"])
            host.audit["photo_saved"] |= bool(state.get("photo") and state["photo"].get("completed"))
            size = len(host.session.brain.history)
            if size and (not host.audit["history_sizes"] or host.audit["history_sizes"][-1] != size):
                host.audit["history_sizes"].append(size)
            host.audit["brain_ids"].add(id(host.session.brain))
            return result
        host.poll, host.execute, host.control = poll, execute, control
        hosts.append(host)
        return host

    result = main(arguments, lock_path=Path(os.environ["BMO_FEATURE_TEST_LOCK"]),
                  clock=clock, host_factory=factory)
    audit = {"hosts": len(hosts), "delegated_calls": sum(spy.call_count for spy in spies),
             "wire_calls": len(wire), "players": len(children),
             "reaped": sum(child.poll() is not None for child in children),
             "all_structured": all(body.get("text", {}).get("format", {}).get("strict") is True
                                   and body["text"]["format"]["name"] == "bmo_feature_reply_v1" for body in wire),
             "profile_preserved": all(body.get("model") == "gpt-6-luna"
                                   and body.get("reasoning") == {"effort": "low"}
                                   and body.get("max_output_tokens") == 320 for body in wire)}
    if hosts:
        host = hosts[0]
        audit.update({**{name: value for name, value in host.audit.items() if name != "brain_ids"},
                      "brain_count": len(host.audit["brain_ids"]),
                      "game_items": len(host.games.catalog()), "media_items": len(host.media.catalog()),
                      "closed": host._closed,
                      "history_clean": all('"speech"' not in item["content"]
                                           and '"emotion"' not in item["content"]
                                           for item in host.session.brain.history)})
    print("LAUNCHER_AUDIT=" + json.dumps(audit), flush=True)
    raise SystemExit(result)
'''


class FeatureLauncherTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("bmo_ai.private_credentials.resolve_credentials",
                                side_effect=AssertionError("No production credentials are permitted")))
        self.enterContext(patch("getpass.getpass", side_effect=AssertionError("No secret prompt is permitted")))

    def launch(self, *arguments, stdin=None, script=False):
        with tempfile.TemporaryDirectory(prefix="bmo-feature-launcher-fixture-") as directory:
            root = Path(directory)
            env = {"PATH": os.defpath, "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPATH": str(ROOT / "software"), "BMO_FEATURE_TEST_LOCK": str(root / "launcher.lock")}
            command = [sys.executable, "-B", "-c", CHILD, *arguments]
            if script:
                entry = root / "guarded.py"
                entry.write_text(CHILD, encoding="utf-8")
                wrapper = root / "python-entry"
                wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable)
                                   + " -B " + shlex.quote(str(entry)) + ' "$@"\n', encoding="utf-8")
                wrapper.chmod(0o700)
                env["BMO_PYTHON"] = str(wrapper)
                command = [str(ROOT / "launch-features"), *arguments]
            return subprocess.run(command, cwd=root, env=env, input=stdin,
                                  capture_output=True, text=True, timeout=20)

    def audit(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line.removeprefix("LAUNCHER_AUDIT=") for line in result.stdout.splitlines()
                if line.startswith("LAUNCHER_AUDIT=")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(rows[0])

    def test_effective_config_and_help_do_not_open_files_keys_or_construct_host(self):
        factory = Mock(side_effect=AssertionError("Host must remain unconstructed"))
        with patch("os.open", side_effect=AssertionError("No file may be opened")), \
             patch("bmo_ai.private_credentials.resolve_credentials", side_effect=AssertionError("No credentials")), \
             patch("getpass.getpass", side_effect=AssertionError("No prompt")), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--effective-config"], host_factory=factory), 0)
            config = json.loads(output.getvalue())
        self.assertEqual((config["chat_model"], config["reasoning_effort"], config["fish_gain"]),
                         ("gpt-6-luna", "low", .25))
        self.assertTrue(config["simulation"])
        self.assertFalse(config["real_features_enabled"])
        self.assertEqual(config["feature_reply_version"], 1)
        self.assertNotIn("api_key", config)
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--help"], host_factory=factory)
        self.assertEqual(caught.exception.code, 0)
        factory.assert_not_called()

    def test_live_and_implicit_execution_or_bad_limits_are_rejected_before_host(self):
        factory = Mock()
        for arguments in ([], ["--live"], ["--simulate", "--live"],
                          ["--simulate", "--turns", "21"], ["--simulate", "--port", "-1"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(arguments, host_factory=factory)
            self.assertEqual(caught.exception.code, 2)
        factory.assert_not_called()

    def test_existing_launcher_lock_refuses_host_creation_and_preserves_lock_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owned.lock"
            path.write_bytes(b"synthetic existing lock owner")
            with path.open("r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                factory = Mock()
                with redirect_stderr(io.StringIO()), patch("bmo_ai.private_credentials.resolve_credentials") as credentials:
                    self.assertEqual(main(["--simulate", "--no-web"], lock_path=path, host_factory=factory), 2)
                factory.assert_not_called(); credentials.assert_not_called()
                self.assertEqual(path.read_bytes(), b"synthetic existing lock owner")

    def test_owned_cleanup_failure_returns_nonzero_and_releases_private_lock(self):
        host = SimpleNamespace(poll=Mock(), snapshot=lambda: {"mode": "conversation", "mode_generation": 0,
            "emotion": "neutral", "phase": "idle", "audio_owner": None},
            close=Mock(side_effect=RuntimeError("private cleanup fixture")))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owned.lock"
            with patch("sys.stdin", io.StringIO("/quit\n")), \
                 patch("select.select", return_value=([object()], [], [])), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as error:
                result = main(["--simulate", "--no-web"], lock_path=path, host_factory=lambda **kw: host)
            self.assertEqual(result, 1)
            self.assertIn("Owned feature cleanup needs review.", error.getvalue())
            self.assertNotIn("private", error.getvalue())
            host.close.assert_called_once()
            with path.open("r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_factory_failure_is_fixed_and_does_not_retain_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owned.lock"
            with redirect_stderr(io.StringIO()) as error:
                result = main(["--simulate", "--no-web"], lock_path=path,
                              host_factory=Mock(side_effect=RuntimeError("private factory fixture")))
            self.assertEqual(result, 1)
            self.assertNotIn("private", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())
            with path.open("r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_two_turns_keep_one_brain_one_delegate_per_turn_and_reap_owned_player(self):
        result = self.launch("--simulate", "--no-web", "--turns", "2")
        audit = self.audit(result)
        self.assertEqual(audit["history_sizes"], [2, 4])
        self.assertEqual((audit["hosts"], audit["brain_count"], audit["delegated_calls"]), (1, 1, 2))
        self.assertEqual((audit["players"], audit["reaped"]), (2, 2))
        self.assertTrue(audit["history_clean"])
        self.assertTrue(audit["closed"])

    @unittest.skipUnless(HAS_SDK, "Real SDK MockTransport requires the isolated requirements environment")
    def test_real_sdk_serializes_one_structured_request_per_turn_in_same_launcher(self):
        audit = self.audit(self.launch("__sdk_fixture__", "--simulate", "--no-web", "--turns", "2"))
        self.assertEqual((audit["delegated_calls"], audit["wire_calls"]), (2, 2))
        self.assertTrue(audit["all_structured"])
        self.assertTrue(audit["profile_preserved"])
        self.assertTrue(audit["history_clean"])
        self.assertEqual(audit["history_sizes"], [2, 4])

    def test_parent_credential_markers_are_not_inherited_by_child(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-private-parent", "FISH_API_KEY": "synthetic-private-parent",
                                    "GH_TOKEN": "synthetic-private-parent", "BMO_FISH_GAIN": "0.99"}):
            result = self.launch("--effective-config")
        audit = self.audit(result)
        self.assertEqual(audit["hosts"], 0)
        self.assertNotIn("synthetic-private-parent", result.stdout + result.stderr)
        config = json.loads(result.stdout.split("LAUNCHER_AUDIT=")[0])
        self.assertEqual(config["fish_gain"], .25)

    def test_generated_catalogs_require_explicit_demo_assets(self):
        plain = self.audit(self.launch("--simulate", "--no-web", stdin="/quit\n"))
        demo = self.audit(self.launch("--simulate", "--no-web", "--demo-assets", stdin="/quit\n"))
        self.assertEqual((plain["game_items"], plain["media_items"]), (0, 0))
        self.assertEqual((demo["game_items"], demo["media_items"]), (2, 2))
        self.assertEqual((plain["players"], demo["players"]), (0, 0))

    def test_cli_game_photo_tracking_music_and_recovery_commands_use_real_mock_host(self):
        commands = ("/games\n/game fixture_game_a\n/key B down\n/key B up\n/key ESC down\n"
                    "/game fixture_game_b\n/face\n/track\n/photo\n/key UP down\n/key UP up\n/key DOWN down\n"
                    "/face\n/camera-off\n/song fixture_song_a\n/media pause\n/media resume\n/media next\n/media stop\n"
                    "/gesture wave\n/stop\n/emotion crying\n/emotion auto\n/cancel\n/repeat\n/reset\n"
                    "/unknown\n/game ../private\n/face\n/quit\n")
        result = self.launch("__fixture_clock__", "--simulate", "--no-web", "--demo-assets", stdin=commands)
        audit = self.audit(result)
        for mode in ("game_menu", "game", "photo", "music", "conversation"):
            self.assertIn(mode, audit["modes"])
        for intent in ("open_game_menu", "launch_game", "take_photo", "start_face_tracking", "stop_face_tracking",
                       "play_song", "stop_media", "arm_gesture", "return_to_face"):
            self.assertIn(intent, audit["intent_names"])
        for control in ("pause", "resume", "next", "stop", "emotion", "cancel", "repeat", "reset"):
            self.assertIn(control, audit["control_names"])
        self.assertTrue(audit["photo_saved"])
        self.assertGreaterEqual(audit["rejected"], 1)
        self.assertIn("Unknown command.", result.stdout)
        self.assertIn("The requested feature is unavailable.", result.stdout)
        self.assertEqual(audit["players"], 0)
        self.assertTrue(audit["closed"])

    def test_launch_script_honors_private_bmo_python_override_from_another_cwd(self):
        audit = self.audit(self.launch("--effective-config", script=True))
        self.assertEqual(audit["hosts"], 0)
        audit = self.audit(self.launch("--simulate", "--no-web", "--turns", "2", script=True))
        self.assertEqual((audit["delegated_calls"], audit["players"], audit["reaped"]), (2, 2, 2))
        self.assertEqual(audit["history_sizes"], [2, 4])


if __name__ == "__main__":
    unittest.main()
