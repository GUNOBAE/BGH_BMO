"""Offline checks for the standalone, mock-only action boundary."""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import types
import unittest
from collections import UserDict
from collections.abc import Mapping
from types import MappingProxyType
from unittest.mock import patch

from bmo_ai import actions
from bmo_ai.actions import (
    ALLOWED_GAME_IDS,
    ALLOWED_SONG_IDS,
    DEFAULT_MAX_REQUESTS,
    MAX_ACTION_CHARS,
    MAX_ASSET_ID_CHARS,
    MAX_REQUEST_ID_CHARS,
    MAX_TRACKED_REQUESTS,
    SUPPORTED_ACTIONS,
    ActionDispatcher,
    ActionRequest,
    ActionResult,
    ActionValidationError,
    MockActionExecutor,
)


def request_payload(action="raise_left_arm", arguments=None, request_id="request_1"):
    return {
        "action": action,
        "arguments": {} if arguments is None else arguments,
        "request_id": request_id,
    }


def forged_record(record_type, **fields):
    """Exercise the execution boundary even when construction was bypassed."""
    record = object.__new__(record_type)
    for name, value in fields.items():
        object.__setattr__(record, name, value)
    return record


class ActionRequestTests(unittest.TestCase):
    def test_public_allowlists_only_contain_the_five_mock_actions(self):
        self.assertEqual(
            SUPPORTED_ACTIONS,
            frozenset({"raise_left_arm", "raise_right_arm", "take_photo", "play_song", "launch_game"}),
        )
        self.assertIsInstance(SUPPORTED_ACTIONS, frozenset)
        self.assertEqual(ALLOWED_SONG_IDS, {"demo_song"})
        self.assertEqual(ALLOWED_GAME_IDS, {"demo_game"})
        self.assertEqual(DEFAULT_MAX_REQUESTS, 1024)
        self.assertEqual(MAX_TRACKED_REQUESTS, 10000)

    def test_request_is_frozen_and_owns_an_immutable_argument_snapshot(self):
        payload = request_payload("play_song", {"song_id": "demo_song"})
        request = ActionRequest.from_payload(payload)
        payload["arguments"]["song_id"] = "arbitrary_file.wav"
        self.assertEqual(dict(request.arguments), {"song_id": "demo_song"})
        with self.assertRaises(TypeError):
            request.arguments["song_id"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            request.action = "take_photo"
        exported = request.to_dict()
        exported["arguments"]["song_id"] = "changed"
        self.assertEqual(request.arguments["song_id"], "demo_song")
        self.assertEqual(
            json.loads(json.dumps(request.to_dict())),
            request_payload("play_song", {"song_id": "demo_song"}),
        )

    def test_an_existing_request_can_be_validated_again(self):
        request = ActionRequest("take_photo", {}, "photo_1")
        self.assertEqual(ActionRequest.from_payload(request).to_dict(), request.to_dict())

    def test_required_fields_and_extra_fields_are_rejected(self):
        for missing in ("action", "arguments", "request_id"):
            payload = request_payload()
            del payload[missing]
            with self.subTest(missing=missing), self.assertRaises(ActionValidationError):
                ActionRequest.from_payload(payload)
        for extra in ("execute", "path", "command", "adapter"):
            payload = request_payload()
            payload[extra] = "unused"
            with self.subTest(extra=extra), self.assertRaises(ActionValidationError):
                ActionRequest.from_payload(payload)

    def test_strings_and_other_unstructured_payloads_are_rejected(self):
        for payload in (
            None, True, 1, [], (), b"raise_left_arm",
            "raise_left_arm", '{"action":"take_photo","arguments":{},"request_id":"photo_1"}',
            "Please raise_left_arm and then take_photo.",
        ):
            with self.subTest(type=type(payload).__name__), self.assertRaises(ActionValidationError):
                ActionRequest.from_payload(payload)

    def test_custom_mappings_and_subclasses_are_rejected_without_callbacks(self):
        class CustomDict(dict):
            def items(self):
                raise AssertionError("custom mapping callback must not run")

        class CustomString(str):
            def __str__(self):
                raise AssertionError("custom string callback must not run")

        class ReadTrapMapping(Mapping):
            def __getitem__(self, key):
                raise AssertionError("mapping item access must not run")

            def __iter__(self):
                raise AssertionError("mapping iteration must not run")

            def __len__(self):
                raise AssertionError("mapping length access must not run")

        candidates = [
            UserDict(request_payload()),
            CustomDict(request_payload()),
            request_payload(action=CustomString("take_photo")),
            request_payload(request_id=CustomString("request_1")),
            request_payload(arguments=CustomDict()),
            request_payload(arguments=MappingProxyType({})),
            request_payload(arguments=MappingProxyType(ReadTrapMapping())),
            request_payload("play_song", {"song_id": CustomString("demo_song")}),
        ]
        for payload in candidates:
            with self.subTest(payload_type=type(payload).__name__), self.assertRaises(ActionValidationError):
                ActionRequest.from_payload(payload)
        with self.assertRaises(ActionValidationError):
            ActionRequest("take_photo", MappingProxyType(ReadTrapMapping()), "proxy_1")

    def test_request_ids_have_bounded_ascii_identifier_syntax(self):
        for request_id in ("A", "Photo-1_ok", "A" * MAX_REQUEST_ID_CHARS):
            with self.subTest(valid_id=request_id):
                self.assertEqual(ActionRequest("take_photo", {}, request_id).request_id, request_id)
        for request_id in (
            None, True, 1, "", "_start", "-start", "with space", "line\nbreak",
            "사진_1", "../photo", "/tmp/photo", "https://example.invalid", "x;echo_bad",
            "A" * (MAX_REQUEST_ID_CHARS + 1),
        ):
            with self.subTest(invalid_id=request_id), self.assertRaises(ActionValidationError):
                ActionRequest("take_photo", {}, request_id)

    def test_constructor_validates_actions_and_arguments(self):
        for action in (None, True, 1, "", "RAISE_LEFT_ARM", "restart", "take_photo; reboot"):
            with self.subTest(action=action), self.assertRaises(ActionValidationError):
                ActionRequest(action, {}, "request_1")
        for arguments in (None, True, 1, "", [], (), {"unexpected": 1}):
            with self.subTest(arguments=arguments), self.assertRaises(ActionValidationError):
                ActionRequest("raise_left_arm", arguments, "request_1")

    def test_only_explicit_song_and_game_ids_are_accepted(self):
        invalid_ids = (
            "", "other_song", "other_game", "/tmp/demo_song", "../demo_song",
            "file:///tmp/song.wav", "https://example.invalid/song", "demo_song; reboot",
            "$(echo bad)", "demo_song\n", 1, True, None, [], {},
        )
        for action, key in (("play_song", "song_id"), ("launch_game", "game_id")):
            for asset_id in invalid_ids:
                with self.subTest(action=action, asset_id=asset_id), self.assertRaises(ActionValidationError):
                    ActionRequest(action, {key: asset_id}, "request_1")
            for arguments in ({}, {key: "demo_song", "path": "/tmp/file"}, {key: {"id": "demo_song"}}):
                with self.subTest(action=action, arguments=arguments), self.assertRaises(ActionValidationError):
                    ActionRequest(action, arguments, "request_1")

    def test_overlong_strings_are_classified_and_bounded(self):
        cases = (
            request_payload(action="a" * (MAX_ACTION_CHARS + 1)),
            request_payload(request_id="a" * (MAX_REQUEST_ID_CHARS + 1)),
            request_payload("play_song", {"song_id": "a" * (MAX_ASSET_ID_CHARS + 1)}),
            request_payload("launch_game", {"game_id": "a" * (MAX_ASSET_ID_CHARS + 1)}),
        )
        for payload in cases:
            with self.subTest(field=payload), self.assertRaises(ActionValidationError) as caught:
                ActionRequest.from_payload(payload)
            self.assertEqual(caught.exception.code, "request_too_large")
            self.assertIsInstance(caught.exception, ValueError)
            self.assertIsInstance(caught.exception.message, str)


class MockActionDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = ActionDispatcher()

    def assert_simulated(self, result, action, request_id):
        self.assertIs(type(result), ActionResult)
        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.code, "mock_only")
        self.assertEqual(result.action, action)
        self.assertEqual(result.request_id, request_id)
        self.assertTrue(result.simulated)
        self.assertIs(result.executed, False)
        self.assertIsInstance(result.message, str)
        self.assertTrue(result.message)

    def test_all_five_commands_return_explicit_simulated_results(self):
        commands = (
            ("raise_left_arm", {}), ("raise_right_arm", {}), ("take_photo", {}),
            ("play_song", {"song_id": "demo_song"}),
            ("launch_game", {"game_id": "demo_game"}),
        )
        for index, (action, arguments) in enumerate(commands):
            request_id = "command_" + str(index)
            with self.subTest(action=action):
                result = self.dispatcher.dispatch(request_payload(action, arguments, request_id))
                self.assert_simulated(result, action, request_id)

    def test_result_is_frozen_and_serializes_execution_flags(self):
        result = self.dispatcher.dispatch(request_payload())
        with self.assertRaises(FrozenInstanceError):
            result.status = "executed"
        exported = result.to_dict()
        self.assertEqual(
            set(exported), {"request_id", "action", "status", "code", "message", "simulated", "executed"},
        )
        self.assertEqual(json.loads(json.dumps(exported)), exported)
        self.assertIs(exported["executed"], False)
        self.assertIs(exported["simulated"], True)

    def test_unsupported_action_is_rejected(self):
        result = self.dispatcher.dispatch(request_payload(action="reboot"))
        self.assertEqual((result.status, result.code), ("rejected", "unsupported_action"))
        self.assertFalse(result.simulated)
        self.assertFalse(result.executed)

    def test_invalid_payloads_return_results_without_executing(self):
        payloads = [None, False, 0, [], "take_photo", "x" * 100000]
        payloads += [request_payload(arguments={"angle": 90}), request_payload(request_id=True)]
        payloads += [dict(request_payload(), shell="echo bad")]
        payloads += [{"extra_" + str(index): "x" for index in range(1000)}]
        with patch.object(MockActionExecutor, "execute") as execute:
            for payload in payloads:
                with self.subTest(payload_type=type(payload).__name__):
                    result = self.dispatcher.dispatch(payload)
                    self.assertEqual(result.status, "rejected")
                    self.assertFalse(result.executed)
            execute.assert_not_called()

    def test_invalid_arguments_are_classified(self):
        for payload in (
            request_payload("take_photo", {"output": "/tmp/photo.jpg"}),
            request_payload("play_song", {"song_id": "unknown"}),
            request_payload("launch_game", {}),
        ):
            with self.subTest(action=payload["action"]):
                result = self.dispatcher.dispatch(payload)
                self.assertEqual((result.status, result.code), ("rejected", "invalid_arguments"))

    def test_same_request_id_executes_only_once(self):
        executor = MockActionExecutor()
        with patch.object(MockActionExecutor, "execute", wraps=executor.execute) as execute:
            first = self.dispatcher.dispatch(request_payload())
            duplicate = self.dispatcher.dispatch(request_payload())
            self.assert_simulated(first, "raise_left_arm", "request_1")
            self.assertEqual((duplicate.status, duplicate.code), ("duplicate", "duplicate_request"))
            self.assertFalse(duplicate.simulated)
            self.assertFalse(duplicate.executed)
            execute.assert_called_once()

    def test_different_valid_request_with_same_id_is_a_conflict(self):
        self.dispatcher.dispatch(request_payload())
        result = self.dispatcher.dispatch(request_payload("raise_right_arm"))
        self.assertEqual((result.status, result.code), ("rejected", "request_id_conflict"))

    def test_different_ids_allow_independent_mock_requests(self):
        for request_id in ("one", "two"):
            self.assert_simulated(
                self.dispatcher.dispatch(request_payload(request_id=request_id)),
                "raise_left_arm", request_id,
            )

    def test_invalid_requests_do_not_reserve_ids_or_ledger_capacity(self):
        dispatcher = ActionDispatcher(max_requests=1)
        self.assertEqual(dispatcher.dispatch(request_payload("unsupported")).status, "rejected")
        self.assertEqual(dispatcher.dispatch(request_payload(arguments={"angle": 1})).status, "rejected")
        self.assert_simulated(dispatcher.dispatch(request_payload()), "raise_left_arm", "request_1")

    def test_bounded_ledger_rejects_new_ids_without_evicting_existing_ids(self):
        dispatcher = ActionDispatcher(max_requests=1)
        dispatcher.dispatch(request_payload())
        full = dispatcher.dispatch(request_payload(request_id="request_2"))
        self.assertEqual((full.status, full.code), ("rejected", "request_capacity_exceeded"))
        duplicate = dispatcher.dispatch(request_payload())
        self.assertEqual(duplicate.code, "duplicate_request")
        self.assertEqual(dispatcher.dispatch(request_payload("take_photo")).code, "request_id_conflict")

    def test_capacity_configuration_is_bounded_and_mock_executor_is_fixed(self):
        for capacity in (False, True, 0, -1, 1.0, "1", None, MAX_TRACKED_REQUESTS + 1):
            with self.subTest(capacity=capacity), self.assertRaises((TypeError, ValueError)):
                ActionDispatcher(max_requests=capacity)
        ActionDispatcher(max_requests=MAX_TRACKED_REQUESTS)
        with self.assertRaises(TypeError):
            ActionDispatcher(executor=MockActionExecutor())

    def test_executor_exceptions_are_sanitized_and_failed_attempt_is_reserved(self):
        dispatcher = ActionDispatcher(max_requests=1)
        private_detail = "secret-token /dev/video0 https://example.invalid"
        with patch.object(MockActionExecutor, "execute", side_effect=RuntimeError(private_detail)) as execute:
            error = dispatcher.dispatch(request_payload())
            self.assertEqual((error.status, error.code), ("error", "executor_error"))
            self.assertNotIn(private_detail, json.dumps(error.to_dict()))
            self.assertFalse(error.executed)
            duplicate = dispatcher.dispatch(request_payload())
            self.assertEqual(duplicate.code, "duplicate_request")
            self.assertEqual(
                dispatcher.dispatch(request_payload(request_id="another")).code,
                "request_capacity_exceeded",
            )
            execute.assert_called_once()

    def test_invalid_executor_outputs_cannot_claim_real_execution(self):
        baseline = MockActionExecutor().execute(ActionRequest.from_payload(request_payload()))
        fields = {name: getattr(baseline, name) for name in ("request_id", "action", "status", "code", "message")}
        outputs = [None, True, {}, "success"]
        for field, value in (
            ("request_id", "someone_else"), ("action", "take_photo"), ("status", "executed"),
            ("code", "real_success"), ("message", None), ("message", "Claimed real execution"),
        ):
            outputs.append(forged_record(ActionResult, **dict(fields, **{field: value})))
        for output in outputs:
            with self.subTest(output_type=type(output).__name__):
                dispatcher = ActionDispatcher()
                with patch.object(MockActionExecutor, "execute", return_value=output) as execute:
                    result = dispatcher.dispatch(request_payload())
                    self.assertEqual((result.status, result.code), ("error", "invalid_executor_result"))
                    self.assertFalse(result.executed)
                    self.assertEqual(dispatcher.dispatch(request_payload()).code, "duplicate_request")
                    execute.assert_called_once()

    def test_parallel_duplicates_execute_once(self):
        started = threading.Event()
        release = threading.Event()
        executor = MockActionExecutor()

        original_execute = MockActionExecutor.execute

        def safe_slow_mock(request):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test synchronization timed out")
            return original_execute(executor, request)

        with patch.object(MockActionExecutor, "execute", side_effect=safe_slow_mock) as execute:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(self.dispatcher.dispatch, request_payload()) for _ in range(8)]
                try:
                    self.assertTrue(started.wait(timeout=5))
                finally:
                    release.set()
                results = [future.result(timeout=5) for future in futures]
            self.assertEqual(sum(result.status == "simulated" for result in results), 1)
            self.assertEqual(sum(result.status == "duplicate" for result in results), 7)
            execute.assert_called_once()


class OfflineExecutionTests(unittest.TestCase):
    def test_direct_executor_validates_requests_and_cannot_accept_raw_commands(self):
        executor = MockActionExecutor()
        for raw in ("take_photo", request_payload(), None):
            with self.subTest(raw_type=type(raw).__name__), self.assertRaises(ActionValidationError):
                executor.execute(raw)
        forged = forged_record(
            ActionRequest, action="play_song", arguments={"song_id": "/tmp/file.wav"}, request_id="forged",
        )
        with self.assertRaises(ActionValidationError):
            executor.execute(forged)

    def test_action_module_has_only_standard_library_imports(self):
        source = Path(actions.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [alias.name.partition(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "actions must remain independent of existing BMO modules")
                names = [(node.module or "").partition(".")[0]]
            else:
                continue
            for name in names:
                self.assertIn(name, sys.stdlib_module_names | {"__future__"})

    def test_fresh_module_execution_and_all_commands_do_not_access_io(self):
        source = Path(actions.__file__).read_text(encoding="utf-8")
        code = compile(source, actions.__file__, "exec")
        module = types.ModuleType("bmo_ai._actions_offline_test")
        forbidden = (
            ("builtins.open", "file access"), ("io.open", "path access"),
            ("os.open", "device access"), ("os.mkdir", "directory creation"),
            ("os.makedirs", "directory creation"), ("os.remove", "file removal"),
            ("os.unlink", "file removal"), ("os.rename", "file rename"),
            ("os.replace", "file replacement"), ("os.system", "shell execution"),
            ("os.popen", "shell execution"), ("socket.socket", "network access"),
            ("socket.create_connection", "network access"),
            ("subprocess.Popen", "process execution"), ("subprocess.run", "process execution"),
            ("subprocess.call", "process execution"), ("subprocess.check_call", "process execution"),
            ("subprocess.check_output", "process execution"),
        )
        with ExitStack() as stack:
            guards = []
            for target, description in forbidden:
                guard = stack.enter_context(patch(target, side_effect=AssertionError(description)))
                guards.append(guard)
            stack.enter_context(patch.dict(sys.modules, {module.__name__: module}))
            exec(code, module.__dict__)
            dispatcher = module.ActionDispatcher()
            commands = (
                ("raise_left_arm", {}), ("raise_right_arm", {}), ("take_photo", {}),
                ("play_song", {"song_id": "demo_song"}),
                ("launch_game", {"game_id": "demo_game"}),
            )
            for index, (action, arguments) in enumerate(commands):
                result = dispatcher.dispatch(request_payload(action, arguments, "offline_" + str(index)))
                self.assertEqual(result.status, "simulated")
                self.assertFalse(result.executed)
            self.assertEqual(dispatcher.dispatch("take_photo").status, "rejected")
            for guard in guards:
                guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
