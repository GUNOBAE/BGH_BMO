"""Standalone, structured, mock-only actions with no application or device imports.

There is deliberately no handler registration, real executor option, environment
opt-in, JSON/prose parser, or connection to the BMO application in this module.
"""

from dataclasses import dataclass
import re
from threading import Lock
from types import MappingProxyType
from typing import Literal, Mapping


SUPPORTED_ACTIONS = frozenset(
    {"raise_left_arm", "raise_right_arm", "take_photo", "play_song", "launch_game"}
)
ALLOWED_SONG_IDS = frozenset({"demo_song"})
ALLOWED_GAME_IDS = frozenset({"demo_game"})
MAX_ACTION_CHARS = 32
MAX_REQUEST_ID_CHARS = 64
MAX_ASSET_ID_CHARS = 32
DEFAULT_MAX_REQUESTS = 1024
MAX_TRACKED_REQUESTS = 10000

_REQUEST_FIELDS = frozenset({"action", "arguments", "request_id"})
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", re.ASCII)
_MOCK_MESSAGE = "Mock simulation only; no device, file, media, or process operation occurred."
ActionStatus = Literal["simulated", "rejected", "duplicate", "error"]


class ActionValidationError(ValueError):
    """A fixed, safe validation error; messages never include submitted values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _valid_request_id(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_REQUEST_ID_CHARS
        and _REQUEST_ID.fullmatch(value) is not None
    )


def _validate_fields(action: object, arguments: object, request_id: object) -> dict:
    if type(request_id) is str and len(request_id) > MAX_REQUEST_ID_CHARS:
        raise ActionValidationError("request_too_large", "Request ID exceeds its length limit.")
    if not _valid_request_id(request_id):
        raise ActionValidationError("invalid_request", "Request ID must be a bounded ASCII identifier.")
    if type(action) is not str:
        raise ActionValidationError("invalid_request", "Action must be a string.")
    if len(action) > MAX_ACTION_CHARS:
        raise ActionValidationError("request_too_large", "Action exceeds its length limit.")
    if action not in SUPPORTED_ACTIONS:
        raise ActionValidationError("unsupported_action", "Action is not supported.")
    # External arguments must be a concrete dict: even a mapping proxy can
    # wrap a caller-defined Mapping with arbitrary access hooks.
    if type(arguments) is not dict:
        raise ActionValidationError("invalid_arguments", "Arguments must be an object.")
    if len(arguments) > 1:
        raise ActionValidationError("invalid_arguments", "Unexpected argument fields.")
    snapshot = dict(arguments)
    if any(type(key) is not str for key in snapshot):
        raise ActionValidationError("invalid_arguments", "Argument field names must be strings.")
    if any(len(key) > MAX_ASSET_ID_CHARS for key in snapshot):
        raise ActionValidationError("request_too_large", "Argument field name exceeds its length limit.")
    if action in ("raise_left_arm", "raise_right_arm", "take_photo"):
        if snapshot:
            raise ActionValidationError("invalid_arguments", "This action accepts no arguments.")
        return snapshot
    field = "song_id" if action == "play_song" else "game_id"
    if snapshot.keys() != {field}:
        raise ActionValidationError("invalid_arguments", "Exactly one approved asset ID field is required.")
    asset_id = snapshot[field]
    if type(asset_id) is not str:
        raise ActionValidationError("invalid_arguments", "Asset ID must be a string.")
    if len(asset_id) > MAX_ASSET_ID_CHARS:
        raise ActionValidationError("request_too_large", "Asset ID exceeds its length limit.")
    allowed_ids = ALLOWED_SONG_IDS if action == "play_song" else ALLOWED_GAME_IDS
    if asset_id not in allowed_ids:
        raise ActionValidationError("invalid_arguments", "Asset ID is not in the explicit allowlist.")
    return snapshot


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """Validated command data. Construction snapshots and freezes arguments."""

    action: str
    arguments: Mapping[str, str]
    request_id: str

    def __post_init__(self) -> None:
        arguments = _validate_fields(self.action, self.arguments, self.request_id)
        object.__setattr__(self, "arguments", MappingProxyType(arguments))

    @classmethod
    def from_payload(cls, payload: object) -> "ActionRequest":
        """Accept an exact dictionary or ActionRequest, never search text."""
        if type(payload) is ActionRequest:
            # Revalidate even typed inputs at each public execution boundary.
            if type(payload.arguments) is not MappingProxyType:
                raise ActionValidationError("invalid_arguments", "Request arguments must be immutable.")
            return cls(payload.action, dict(payload.arguments), payload.request_id)
        if type(payload) is not dict:
            raise ActionValidationError("invalid_request", "Request must be a structured object.")
        if len(payload) > len(_REQUEST_FIELDS):
            raise ActionValidationError("invalid_request", "Unexpected request fields.")
        snapshot = payload.copy()
        if any(type(key) is not str for key in snapshot):
            raise ActionValidationError("invalid_request", "Request field names must be strings.")
        if any(len(key) > MAX_ACTION_CHARS for key in snapshot):
            raise ActionValidationError("request_too_large", "Request field name exceeds its length limit.")
        if snapshot.keys() != _REQUEST_FIELDS:
            raise ActionValidationError("invalid_request", "Exactly action, arguments and request_id are required.")
        return cls(snapshot["action"], snapshot["arguments"], snapshot["request_id"])

    def to_dict(self) -> dict:
        """Return a detached, JSON-compatible representation for future adapters."""
        return {"action": self.action, "arguments": dict(self.arguments), "request_id": self.request_id}


@dataclass(frozen=True, slots=True)
class ActionResult:
    """An explicit outcome; this v0 result can never report real execution."""

    request_id: str | None
    action: str | None
    status: ActionStatus
    code: str
    message: str

    @property
    def simulated(self) -> bool:
        return self.status == "simulated"

    @property
    def executed(self) -> bool:
        return False

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "action": self.action,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "simulated": self.simulated,
            "executed": self.executed,
        }


def _simulated_result(request: ActionRequest) -> ActionResult:
    return ActionResult(request.request_id, request.action, "simulated", "mock_only", _MOCK_MESSAGE)


class MockActionExecutor:
    """Pure result construction. No device, media, filesystem or process access."""

    def execute(self, request: ActionRequest) -> ActionResult:
        if type(request) is not ActionRequest:
            raise ActionValidationError("invalid_request", "Executor requires an ActionRequest.")
        return _simulated_result(ActionRequest.from_payload(request))


def _safe_context(payload: object) -> tuple[str | None, str | None]:
    """Correlate errors with validated identifiers, without echoing raw input."""
    if type(payload) is ActionRequest:
        request_id, action = payload.request_id, payload.action
    elif type(payload) is dict:
        # Only inspect known string keys after rejecting custom key hooks.
        if len(payload) > len(_REQUEST_FIELDS) or any(type(key) is not str for key in payload):
            return None, None
        request_id, action = payload.get("request_id"), payload.get("action")
    else:
        return None, None
    return (
        request_id if _valid_request_id(request_id) else None,
        action if type(action) is str and len(action) <= MAX_ACTION_CHARS and action in SUPPORTED_ACTIONS else None,
    )


class ActionDispatcher:
    """Bounded, thread-safe, per-instance deduplication with a fixed mock executor.

    Valid attempts reserve their ID before execution, including failed attempts.
    IDs are never evicted. Once full, new IDs fail closed; duplicates remain
    recognizable. This in-memory ledger does not survive process replacement.
    """

    def __init__(self, *, max_requests: int = DEFAULT_MAX_REQUESTS) -> None:
        if type(max_requests) is not int or not 1 <= max_requests <= MAX_TRACKED_REQUESTS:
            raise ValueError("max_requests must be an integer from 1 through 10000.")
        self._max_requests = max_requests
        self._requests: dict[str, ActionRequest] = {}
        self._lock = Lock()
        self._executor = MockActionExecutor()

    def dispatch(self, payload: object) -> ActionResult:
        """Validate structured data and simulate once, returning fixed safe errors."""
        try:
            request = ActionRequest.from_payload(payload)
        except ActionValidationError as error:
            request_id, action = _safe_context(payload)
            return ActionResult(request_id, action, "rejected", error.code, str(error))

        # Mock execution is synchronous and purely in memory, so the lock can
        # cover lookup, reservation and completion without any device wait.
        with self._lock:
            previous = self._requests.get(request.request_id)
            if previous is not None:
                if previous == request:
                    return ActionResult(
                        request.request_id, request.action, "duplicate", "duplicate_request",
                        "Request ID was already attempted; mock execution was not repeated.",
                    )
                return ActionResult(
                    request.request_id, request.action, "rejected", "request_id_conflict",
                    "Request ID is already reserved for a different request.",
                )
            if len(self._requests) >= self._max_requests:
                return ActionResult(
                    request.request_id, request.action, "rejected", "request_capacity_exceeded",
                    "Request ledger is full; no mock execution was attempted.",
                )
            self._requests[request.request_id] = request
            try:
                result = self._executor.execute(request)
                if type(result) is not ActionResult or result != _simulated_result(request):
                    return ActionResult(
                        request.request_id, request.action, "error", "invalid_executor_result",
                        "Mock executor returned an invalid result; request ID remains reserved.",
                    )
            except Exception:
                return ActionResult(
                    request.request_id, request.action, "error", "executor_error",
                    "Mock executor failed; request ID remains reserved.",
                )
            return result
