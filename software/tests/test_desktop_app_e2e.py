"""Real desktop wrapper/Host/Server with synthetic voice and owned browser child."""
from contextlib import redirect_stdout
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from checks.check_desktop_app_browser import DesktopFixture
from bmo_ai.desktop_app import main


class DesktopAppEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-desktop-unit-")))
        self.fixtures = []

    def tearDown(self):
        for fixture in reversed(self.fixtures):fixture.close()

    def fixture(self, **settings):
        fixture = DesktopFixture(self.root, **settings)
        self.fixtures.append(fixture)
        return fixture

    def ready(self, fixture):
        self.assertEqual(fixture.ready()[0], 202)
        return fixture.wait(lambda state: state["phase"] == "ready")

    def control(self, fixture, action, value=None):
        status, payload = fixture.request("/api/control", payload={"action": action, "value": value})
        self.assertEqual(status, 202, payload)
        return json.loads(payload)

    def finish_turn(self, fixture):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            value = fixture.features()
            if not value["conversation_busy"] and value["audio_owner"] is None:return value
            time.sleep(.01)
        self.fail("Synthetic conversation failed to complete")

    def assert_clean(self, result, *, expected_code=0):
        code, counts, error = result
        self.assertEqual(code, expected_code, error)
        self.assertEqual(counts["players"], counts["reaped"])
        self.assertEqual(counts["browsers"], counts["browser_reaped"])
        self.assertEqual(counts["forbidden"], 0)
        self.assertLessEqual(counts["max_players"], 1)
        return counts

    def test_actual_desktop_wrapper_waits_for_face_then_greets_once_without_chat_or_answer_cache(self):
        fixture = self.fixture()
        self.assertEqual(fixture.plan["files_written"], 0)
        self.assertFalse(fixture.plan["autostart"])
        self.assertFalse((self.root / "Desktop" / "BMO.desktop").exists())
        state = fixture.state()
        self.assertEqual(state["phase"], "loading")
        self.assertTrue(state["server_ready"] and state["engine_ready"])
        self.assertFalse(state["face_ready"])
        self.assertEqual(state["external_api_ready"], "NOT_CHECKED")
        for _ in range(3):fixture.request("/api/state");fixture.state()
        self.assertEqual(fixture.features()["test_fixture"]["greeting_calls"], 0)
        status, html = fixture.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"BMO Loading...", html)
        self.assertNotIn(b"talk-button", html)
        self.assertEqual(fixture.request("/developer")[0], 404)
        state = self.ready(fixture)
        self.assertEqual(state["code"], "greeting_completed")
        audit = fixture.features()["test_fixture"]
        self.assertEqual((audit["greeting_calls"], audit["greeting_started"], audit["greeting_ended"]), (1, 1, 1))
        self.assertEqual((audit["brain"], audit["synthesis"], audit["history_messages"]), (0, 0, 0))
        self.assertFalse(audit["dialogue_cache"])
        self.assert_clean(fixture.finish())

    def test_refresh_readiness_and_mode_roundtrip_never_repeat_greeting(self):
        fixture = self.fixture();self.ready(fixture)
        for _ in range(3):self.assertEqual(fixture.ready()[0], 202)
        fixture.request("/api/intent", payload={"name": "open_game_menu", "target": None})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and fixture.features()["mode"] != "game_menu":time.sleep(.01)
        self.assertEqual(fixture.features()["mode"], "game_menu")
        fixture.request("/api/intent", payload={"name": "return_to_face", "target": None})
        self.assertEqual(fixture.ready()[0], 202)
        counts = self.assert_clean(fixture.finish())
        self.assertEqual((counts["greeting_calls"], counts["players"], counts["brain"]), (1, 1, 0))

    def test_greeting_and_answer_repeat_have_separate_caches_and_one_brain(self):
        fixture = self.fixture();self.ready(fixture)
        self.control(fixture, "repeat")
        time.sleep(.04)
        self.assertEqual(fixture.features()["test_fixture"]["players"], 1)
        self.control(fixture, "question", "Synthetic desktop unit question.")
        deadline = time.monotonic() + 2
        while fixture.features()["test_fixture"]["brain"] == 0 and time.monotonic() < deadline:time.sleep(.01)
        state = self.finish_turn(fixture)
        self.assertEqual(state["emotion"], "crying")
        self.assertTrue(state["test_fixture"]["dialogue_cache"])
        history = state["test_fixture"]["history_messages"]
        self.control(fixture, "repeat")
        deadline = time.monotonic() + 2
        while fixture.features()["test_fixture"]["players"] < 3 and time.monotonic() < deadline:time.sleep(.01)
        state = self.finish_turn(fixture)
        audit = state["test_fixture"]
        self.assertEqual((audit["brain"], audit["synthesis"], audit["greeting_calls"]), (1, 1, 1))
        self.assertEqual(audit["history_messages"], history)
        self.assertEqual(audit["brain_instances"], 1)
        self.assert_clean(fixture.finish())

    def test_approved_private_cache_is_reused_on_next_app_without_synthesis(self):
        first = self.fixture();self.ready(first)
        first_counts = self.assert_clean(first.finish())
        self.assertEqual(first_counts["cache_published"], 1)
        second = self.fixture();self.ready(second)
        counts = self.assert_clean(second.finish())
        self.assertEqual((counts["cache_published"], counts["greeting_calls"], counts["synthesis"]), (0, 1, 0))

    def test_corrupt_cache_is_diagnostic_and_explicit_retry_never_synthesizes(self):
        fixture = self.fixture(scenario="greeting_corrupt")
        state = self.ready(fixture)
        self.assertEqual(state["code"], "greeting_corrupt")
        self.assertEqual(fixture.features()["test_fixture"]["players"], 0)
        self.assertEqual(fixture.request("/api/desktop", payload={"action": "retry_greeting", "value": None})[0], 202)
        fixture.wait(lambda state: state["phase"] == "ready" and state["greeting_attempts"] == 2)
        counts = self.assert_clean(fixture.finish())
        self.assertEqual((counts["greeting_calls"], counts["players"], counts["synthesis"], counts["brain"]), (2, 0, 0, 0))

    def test_missing_cache_exposes_face_ready_failure_without_auto_api_or_player(self):
        fixture = self.fixture(scenario="greeting_missing")
        state = self.ready(fixture)
        self.assertEqual(state["code"], "greeting_missing")
        self.assertTrue(state["face_ready"])
        self.assertFalse(state["greeting_played"])
        counts = self.assert_clean(fixture.finish())
        self.assertEqual((counts["players"], counts["cache_published"], counts["brain"], counts["synthesis"]), (0, 0, 0, 0))

    def test_face_ready_requires_current_run_matching_hash_and_bounded_version(self):
        fixture = self.fixture()
        state = fixture.state()
        screen = json.loads(fixture.request("/api/screen")[1])
        good = dict(run_id=state["run_id"], screen_sha256=screen["sha256"], frame_version="synthetic-contract-v1")
        for changes in ({"run_id": "old_run"}, {"screen_sha256": "0" * 64}, {"frame_version": ""},
                        {"frame_version": 1}, {"frame_version": "x" * 81}, {"frame_version": "한글"}):
            with self.subTest(changes=changes):
                status, payload = fixture.request("/api/desktop", payload={"action": "face_ready", "value": good | changes})
                self.assertEqual(status, 400, payload)
        self.assertFalse(fixture.state()["face_ready"])
        self.assertEqual(fixture.features()["test_fixture"]["greeting_calls"], 0)
        self.assert_clean(fixture.finish())

    def test_desktop_post_reuses_exact_host_origin_token_and_json_contract(self):
        fixture = self.fixture()
        envelope = json.dumps(dict(version=1, nonce="fixture", payload={"action": "exit", "value": None})).encode()
        for headers, expected in (({"Origin": None}, 403), ({"Origin": "http://invalid.example"}, 403),
                                  ({"Host": "localhost:1"}, 403), ({"X-BMO-Token": "wrong"}, 403),
                                  ({"Content-Type": "text/plain"}, 415), ({"Content-Length": "8193"}, 413)):
            with self.subTest(headers=headers):
                self.assertEqual(fixture.request("/api/desktop", body=envelope, headers=headers)[0], expected)
        malformed = (b'{"version":1,"version":1,"nonce":"fixture","payload":{"action":"exit","value":null}}',
                     b'{"version":true,"nonce":"fixture","payload":{"action":"exit","value":null}}',
                     b'{"version":NaN,"nonce":"fixture","payload":{}}', b'{} {}', b'\xff')
        for body in malformed:
            with self.subTest(length=len(body)):
                self.assertEqual(fixture.request("/api/desktop", body=body)[0], 400)
        self.assertFalse(fixture.state()["exit_requested"])
        self.assert_clean(fixture.finish())

    def test_duplicate_double_click_exits_before_constructing_second_host_or_browser(self):
        fixture = self.fixture()
        duplicate = subprocess.run(fixture.command, cwd=self.root, env=fixture.environment,
                                   capture_output=True, text=True, timeout=5)
        self.assertEqual(duplicate.returncode, 2, duplicate.stderr)
        audit = [json.loads(line.removeprefix("DESKTOP_TEST_RESULT ")) for line in duplicate.stdout.splitlines()
                 if line.startswith("DESKTOP_TEST_RESULT ")]
        self.assertEqual(len(audit), 1)
        self.assertEqual((audit[0]["hosts"], audit[0]["browsers"], audit[0]["greeting_calls"]), (0, 0, 0))
        self.assertFalse(fixture.state()["exit_requested"])
        self.assert_clean(fixture.finish())

    def test_browser_start_failure_closes_server_without_constructing_engine(self):
        fixture = self.fixture(scenario="browser_start_fail")
        code, counts, error = fixture.finish(request_exit=False)
        self.assertEqual(code, 1, error)
        self.assertEqual((counts["hosts"], counts["browsers"], counts["players"]), (0, 0, 0))
        self.assertEqual(counts["forbidden"], 0)

    def test_failed_browser_poll_terminates_owned_runtime_instead_of_waiting_forever(self):
        fixture = self.fixture(scenario="browser_poll_failed")
        code, counts, error = fixture.finish(request_exit=False)
        self.assertEqual(code, 1, error)
        self.assertEqual(counts["browsers"], counts["browser_reaped"])
        self.assertEqual(counts["greeting_calls"], 0)

    def test_closing_owned_browser_after_ready_stops_host_and_reaps_players(self):
        fixture = self.fixture();self.ready(fixture)
        (self.root / "close-browser").write_text("close only the fixture child")
        self.assert_clean(fixture.finish(request_exit=False))

    def test_browser_dies_inside_mark_ready_never_starts_greeting_or_player(self):
        fixture = self.fixture(scenario="browser_ready_race")
        self.assertEqual(fixture.ready()[0], 202)
        _, counts, error = fixture.finish(request_exit=False)
        self.assertEqual((counts["greeting_calls"], counts["players"]), (0, 0), error)
        self.assertEqual(counts["browsers"], counts["browser_reaped"])

    def test_backend_poll_failure_cleans_owned_browser_host_and_server(self):
        fixture = self.fixture(scenario="engine_poll_fail")
        code, counts, error = fixture.finish(request_exit=False)
        self.assertEqual(code, 1, error)
        self.assertEqual(counts["browsers"], counts["browser_reaped"])
        self.assertEqual((counts["hosts"], counts["players"], counts["forbidden"]), (1, 0, 0))
        self.assertNotIn("Private backend", error)

    def test_face_timeout_stops_loading_and_stays_diagnostic_until_explicit_exit(self):
        fixture = self.fixture()
        (self.root / "clock.txt").write_text("20")
        state = fixture.wait(lambda state: state["phase"] == "error")
        self.assertEqual(state["code"], "face_ready_timeout")
        self.assertEqual(fixture.features()["test_fixture"]["greeting_calls"], 0)
        self.assert_clean(fixture.finish(), expected_code=1)

    def test_engine_start_failure_keeps_diagnostic_product_server_without_private_detail(self):
        fixture = self.fixture(scenario="engine_fail")
        state = fixture.state()
        self.assertEqual((state["phase"], state["code"]), ("error", "engine_startup_failed"))
        self.assertTrue(state["server_ready"])
        self.assertFalse(state["engine_ready"])
        self.assertNotIn("Private provider", json.dumps(state))
        self.assert_clean(fixture.finish(), expected_code=1)

    def test_exit_during_greeting_reaps_player_and_releases_audio_owner(self):
        fixture = self.fixture();fixture.ready()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and fixture.features()["test_fixture"]["players"] == 0:time.sleep(.01)
        self.assertEqual(fixture.features()["test_fixture"]["players"], 1)
        counts = self.assert_clean(fixture.finish())
        self.assertEqual((counts["greeting_calls"], counts["players"]), (1, 1))

    def test_server_is_unreachable_after_product_exit(self):
        fixture = self.fixture()
        address = urlsplit(fixture.url)
        self.assert_clean(fixture.finish())
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=.3)
        try:
            with self.assertRaises(ConnectionRefusedError):connection.request("GET", "/api/desktop")
        finally:connection.close()

    def test_explicit_developer_option_keeps_development_shell_separate(self):
        fixture = self.fixture(developer=True)
        self.assertEqual(fixture.request("/developer")[0], 200)
        product = fixture.request("/")[1]
        development = fixture.request("/developer")[1]
        self.assertNotIn(b"talk-button", product)
        self.assertIn(b"talk-button", development)
        self.assertTrue(fixture.state()["developer_enabled"])
        self.assert_clean(fixture.finish())

    def test_help_status_dryrun_never_open_lock_secret_browser_or_host(self):
        factory = Mock(side_effect=AssertionError("No runtime should be constructed"))
        for arguments in (["--status"], ["--dry-run"], ["--simulate", "--dry-run"]):
            with self.subTest(arguments=arguments), patch("os.open", side_effect=AssertionError("No descriptor")), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(main(arguments, host_factory=factory, browser_factory=factory,
                                      greeting_factory=factory, credential_resolver=factory), 0)
        with patch("os.open", side_effect=AssertionError("No descriptor")), redirect_stdout(io.StringIO()), \
             self.assertRaises(SystemExit) as caught:
            main(["--help"], host_factory=factory, browser_factory=factory, credential_resolver=factory)
        self.assertEqual(caught.exception.code, 0)
        factory.assert_not_called()


if __name__ == "__main__":unittest.main()
