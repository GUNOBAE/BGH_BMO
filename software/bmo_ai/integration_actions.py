"""Explicit structured, mock-only action entry point for the manual launcher.

The action contract, validator and fixed executor are copied unchanged from
b346cc3017ca0ee4cb2162384d1fa1499659739a. This host adds no LLM tools, prose
command detection, media, API, device or hardware adapters. Keep one instance
for the launcher lifetime, including conversation reset: its bounded in-memory
deduplication ledger does not survive process replacement.
"""
from .actions import (
    ActionDispatcher,
    ActionRequest,
    ActionResult,
    ActionValidationError,
)


ACTION_SOURCE_COMMIT = "b346cc3017ca0ee4cb2162384d1fa1499659739a"


class MockActionHost:
    """Dispatch caller-owned logical IDs only in an explicit simulated session.

    Repeated delivery must preserve the same request_id. This host never creates
    a replacement ID, retries an action, clears its ledger, or claims physical
    success. An optional dispatcher must be the original concrete mock class.
    """

    def __init__(self, *, simulate: bool, dispatcher: ActionDispatcher | None = None):
        if type(simulate) is not bool:
            raise ValueError("simulate must be a boolean")
        if dispatcher is not None and type(dispatcher) is not ActionDispatcher:
            raise ValueError("dispatcher must be the concrete mock ActionDispatcher")
        self._simulate = simulate
        self._dispatcher = dispatcher if dispatcher is not None else ActionDispatcher()

    def dispatch_request(self, payload: object) -> dict:
        """Return all seven result fields without interpreting ordinary dialogue."""
        if not self._simulate:
            # Validate only to retain safe correlation identifiers. Never call
            # even the mock dispatcher through a live launcher path.
            try:
                request = ActionRequest.from_payload(payload)
            except ActionValidationError:
                request = None
            return ActionResult(
                request.request_id if request is not None else None,
                request.action if request is not None else None,
                "rejected", "mock_mode_required",
                "Actions are available only in explicit simulation; no operation occurred.",
            ).to_dict()
        return self._dispatcher.dispatch(payload).to_dict()


def demo_requests(request_prefix: str = "integration-demo") -> list[dict]:
    """Return five detached structured requests with stable caller-visible IDs.

    Repeating the helper with the same prefix delivers the same logical requests
    and therefore produces duplicates in the same host. A new prefix denotes
    deliberately new logical requests, not an automatic retry workaround.
    demo_song and demo_game are mock catalog IDs, not installed media.
    """
    if type(request_prefix) is not str:
        raise ValueError("request_prefix must be a bounded ASCII identifier")
    commands = (
        ("raise_left_arm", {}, "left"),
        ("raise_right_arm", {}, "right"),
        ("take_photo", {}, "photo"),
        ("play_song", {"song_id": "demo_song"}, "song"),
        ("launch_game", {"game_id": "demo_game"}, "game"),
    )
    return [ActionRequest(action, arguments, f"{request_prefix}-{suffix}").to_dict()
            for action, arguments, suffix in commands]
