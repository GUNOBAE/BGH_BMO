"""The integration host preserves the five mock actions and cannot run live IO."""
from contextlib import ExitStack
from unittest.mock import Mock, patch
import unittest

from bmo_ai.actions import ActionDispatcher
from bmo_ai.integration_actions import MockActionHost, demo_requests


RESULT_FIELDS = {"request_id", "action", "status", "code", "message", "simulated", "executed"}


class MockActionHostTests(unittest.TestCase):
    def assert_result(self, result, status, code):
        self.assertEqual(set(result), RESULT_FIELDS)
        self.assertEqual((result["status"], result["code"]), (status, code))
        self.assertIs(result["executed"], False)
        self.assertIs(result["simulated"], status == "simulated")

    def test_all_five_structured_demo_requests_return_mock_results_without_io(self):
        forbidden = (
            "builtins.open", "io.open", "os.open", "os.mkdir", "os.makedirs",
            "os.remove", "os.unlink", "os.rename", "os.replace", "os.system",
            "os.popen", "socket.socket", "socket.create_connection",
            "subprocess.Popen", "subprocess.run", "subprocess.call",
            "subprocess.check_call", "subprocess.check_output",
        )
        with ExitStack() as guards:
            blocked = [guards.enter_context(patch(target, side_effect=AssertionError("IO forbidden")))
                       for target in forbidden]
            host = MockActionHost(simulate=True)
            requests = demo_requests()
            self.assertEqual(len(requests), 5)
            for request in requests:
                result = host.dispatch_request(request)
                self.assert_result(result, "simulated", "mock_only")
                self.assertEqual(result["action"], request["action"])
                self.assertEqual(result["request_id"], request["request_id"])
            for guard in blocked:
                guard.assert_not_called()

    def test_demo_ids_are_stable_and_results_remain_duplicates_across_turns(self):
        host = MockActionHost(simulate=True)
        first = demo_requests()
        self.assertEqual(first, demo_requests())
        for request in first:
            self.assert_result(host.dispatch_request(request), "simulated", "mock_only")
        # Conversation reset must keep this host, rather than clear or recreate
        # the dispatcher. No conversation or reset API is involved in dispatch.
        for request in demo_requests():
            self.assert_result(host.dispatch_request(request), "duplicate", "duplicate_request")

    def test_same_id_with_different_valid_request_is_a_conflict(self):
        host = MockActionHost(simulate=True)
        request = demo_requests()[0]
        self.assert_result(host.dispatch_request(request), "simulated", "mock_only")
        conflict = dict(request, action="take_photo")
        self.assert_result(host.dispatch_request(conflict), "rejected", "request_id_conflict")

    def test_invalid_arguments_and_unstructured_commands_are_rejected_safely(self):
        host = MockActionHost(simulate=True)
        invalid = (
            ("take_photo", "invalid_request"),
            ('{"action":"take_photo"}', "invalid_request"),
            ({"action": "take_photo", "arguments": {"path": "private-value"},
              "request_id": "invalid-photo"}, "invalid_arguments"),
            ({"action": "play_song", "arguments": {"song_id": "private-value"},
              "request_id": "invalid-song"}, "invalid_arguments"),
            ({"action": "take_photo", "arguments": {}, "request_id": "invalid-extra",
              "extra": "private-value"}, "invalid_request"),
        )
        for payload, code in invalid:
            with self.subTest(code=code):
                result = host.dispatch_request(payload)
                self.assert_result(result, "rejected", code)
                self.assertNotIn("private-value", repr(result))

    def test_bounded_capacity_fails_closed_and_retains_existing_ids(self):
        host = MockActionHost(simulate=True, dispatcher=ActionDispatcher(max_requests=1))
        first, second = demo_requests()[:2]
        self.assert_result(host.dispatch_request(first), "simulated", "mock_only")
        self.assert_result(host.dispatch_request(second), "rejected", "request_capacity_exceeded")
        self.assert_result(host.dispatch_request(first), "duplicate", "duplicate_request")

    def test_live_mode_blocks_dispatch_without_reserving_the_id(self):
        dispatcher = ActionDispatcher()
        live = MockActionHost(simulate=False, dispatcher=dispatcher)
        request = demo_requests()[0]
        with patch.object(dispatcher, "dispatch", side_effect=AssertionError("live dispatch forbidden")) as dispatch:
            for payload in (request, {"private-value": "private-value"}, "private-value"):
                result = live.dispatch_request(payload)
                self.assert_result(result, "rejected", "mock_mode_required")
                self.assertNotIn("private-value", repr(result))
            dispatch.assert_not_called()
        self.assert_result(MockActionHost(simulate=True, dispatcher=dispatcher).dispatch_request(request),
                           "simulated", "mock_only")

    def test_constructor_requires_explicit_bool_and_concrete_mock_dispatcher(self):
        class CustomDispatcher(ActionDispatcher):
            pass
        for simulate in (None, 0, 1, "true"):
            with self.subTest(simulate_type=type(simulate).__name__), self.assertRaises(ValueError):
                MockActionHost(simulate=simulate)
        for dispatcher in (Mock(), object(), CustomDispatcher()):
            with self.subTest(dispatcher_type=type(dispatcher).__name__), self.assertRaises(ValueError):
                MockActionHost(simulate=True, dispatcher=dispatcher)

    def test_demo_payloads_are_detached_and_prefixes_use_the_original_id_contract(self):
        requests = demo_requests("morning")
        self.assertTrue(all(request["request_id"].startswith("morning-") for request in requests))
        requests[3]["arguments"]["song_id"] = "changed"
        self.assertEqual(demo_requests("morning")[3]["arguments"], {"song_id": "demo_song"})
        for prefix in (None, True, "", "bad prefix", "../path", "x" * 59):
            with self.subTest(prefix_type=type(prefix).__name__), self.assertRaises(ValueError):
                demo_requests(prefix)


if __name__ == "__main__":
    unittest.main()
