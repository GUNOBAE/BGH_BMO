"""Stateless cloud conversations with bounded, transactional local context.

Only complete user/assistant turns are retained. No hardware actions are executed
here, and callers can inject a fake client for offline tests.
"""

import os

from .cloud import call_cloud


SYSTEM_PROMPT = """You are BMO, a cheerful, curious, tiny robot companion inspired by a friendly game console.
Use natural, short replies. Be expressive but not overly verbose.
Never claim to have moved your arms, seen the room, read an IMU, or pressed buttons
unless the host actually provides that observation/action. Do not pretend to have
private memories across restarts. You are a fan-built robot, not the official
character. Avoid quoting dialogue from the show.
"""

LANGUAGE_INSTRUCTIONS = {
    "en": "Use English by default; respond in Korean when the user speaks Korean or requests Korean.",
    "ko": "Use Korean by default; respond in English when the user explicitly requests English.",
    "auto": "Respond in the language of the user's latest message; use English when the language is unclear.",
}


class BrainResponseError(ValueError):
    """The cloud returned no complete reply; the local conversation is unchanged."""


def _positive_integer(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


class BMOBrain:
    """A synchronous conversation session for the push-to-talk/text application.

    Character budgets bound both outbound context and retained history, rather
    than estimating model tokens. A turn larger than the retained history budget
    is answered but cannot be retained. SDK retries belong to the injected client.
    Instances are intended for one application loop, not concurrent calls.
    """

    def __init__(
        self,
        client,
        model=None,
        max_turns=8,
        *,
        language="en",
        max_input_chars=4000,
        max_history_chars=16000,
        max_output_tokens=180,
        reasoning_effort=None,
    ):
        for name, value in (
            ("max_turns", max_turns),
            ("max_input_chars", max_input_chars),
            ("max_history_chars", max_history_chars),
            ("max_output_tokens", max_output_tokens),
        ):
            _positive_integer(name, value)
        if language not in LANGUAGE_INSTRUCTIONS:
            raise ValueError("language must be en, ko, or auto")
        if max_input_chars > max_history_chars:
            raise ValueError("max_history_chars must be at least max_input_chars")
        selected_model = model if model is not None else os.getenv("BMO_CHAT_MODEL", "gpt-4.1-mini")
        if not isinstance(selected_model, str) or not selected_model.strip():
            raise ValueError("model must be a nonempty string")
        if reasoning_effort is not None:
            if selected_model.strip() != "gpt-6-luna" or reasoning_effort not in ("none", "low"):
                raise ValueError("reasoning_effort is only supported for gpt-6-luna (none or low) in this experiment")
        self.client = client
        self.model = selected_model.strip()
        self.reasoning_effort = reasoning_effort
        self.max_turns = max_turns
        self.language = language
        self.max_input_chars = max_input_chars
        self.max_history_chars = max_history_chars
        self.max_output_tokens = max_output_tokens
        self.history = []

    def reset(self):
        """Forget all local context without making a cloud request."""
        self.history = []

    @staticmethod
    def _characters(messages):
        return sum(len(item["content"]) for item in messages)

    def _trim_pairs(self, messages, max_turns, character_budget):
        """Drop oldest complete turns until both limits are met."""
        trimmed = [dict(item) for item in messages]
        while trimmed and (
            len(trimmed) > max_turns * 2 or self._characters(trimmed) > character_budget
        ):
            del trimmed[:2]
        return trimmed

    def ask(self, message):
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        # Check raw input too: padding cannot bypass the request budget.
        if len(message) > self.max_input_chars:
            raise ValueError(f"message exceeds max_input_chars ({self.max_input_chars})")
        message = message.strip()
        if not message:
            return ""
        previous = self._trim_pairs(
            self.history, self.max_turns - 1, self.max_history_chars - len(message)
        )
        candidate = previous + [{"role": "user", "content": message}]
        request = {
            "model": self.model,
            "instructions": SYSTEM_PROMPT + "\n" + LANGUAGE_INSTRUCTIONS[self.language],
            "input": candidate,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if self.reasoning_effort is not None:
            request["reasoning"] = {"effort": self.reasoning_effort}
        response = call_cloud(lambda: self.client.responses.create(**request))
        # Require the API completion marker even if partial text is present.
        # Injectable clients should return the same status as the SDK contract.
        status = getattr(response, "status", None)
        if status != "completed" or getattr(response, "incomplete_details", None):
            raise BrainResponseError("Cloud response was not complete; please retry.")
        if getattr(response, "error", None):
            raise BrainResponseError("Cloud response failed; please retry.")
        text = getattr(response, "output_text", None)
        if not isinstance(text, str) or not text.strip():
            raise BrainResponseError("Cloud response contained no reply; please retry.")
        answer = text.strip()
        # Commit only after every response check succeeds. Earlier history must
        # survive failed requests, even when outbound context was trimmed.
        completed = candidate + [{"role": "assistant", "content": answer}]
        self.history = self._trim_pairs(completed, self.max_turns, self.max_history_chars)
        return answer
