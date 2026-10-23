"""SDK construction and safe failures. The SDK alone owns network retries."""
import os
from collections.abc import Callable
from typing import TypeVar

from .expression_contract import ExpressionFormatError

T = TypeVar("T")


class CloudError(RuntimeError):
    """Contains only a fixed public message, never a provider response body."""

    def __init__(self, code: str, safe_message: str, retryable: bool = False):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


def cloud_error(error: Exception) -> CloudError:
    """Classify without formatting exceptions which can contain keys or input."""
    if isinstance(error, ExpressionFormatError):
        return CloudError("expression_format", "The reply did not match the expression contract.")
    name = type(error).__name__
    status = getattr(error, "status_code", None)
    if name == "AuthenticationError" or status == 401:
        return CloudError("authentication", "API authentication failed; check OPENAI_API_KEY.")
    if name == "PermissionDeniedError" or status == 403:
        return CloudError("permission", "The API project cannot access this resource or model.")
    if name == "NotFoundError" or status == 404:
        return CloudError("model", "The configured API model or resource is unavailable.")
    if name == "RateLimitError" or status == 429:
        return CloudError("rate_limit", "API rate or quota limit reached; check usage before retrying.", True)
    if name == "APITimeoutError" or isinstance(error, TimeoutError) or status == 408:
        return CloudError("timeout", "The cloud request timed out; try again later.", True)
    if name == "APIConnectionError" or isinstance(error, ConnectionError):
        return CloudError("connection", "Cannot reach the cloud API; check the network.", True)
    if isinstance(status, int) and status >= 500:
        return CloudError("server", "The cloud service is temporarily unavailable.", True)
    if status in (400, 422) or name in ("BadRequestError", "UnprocessableEntityError"):
        return CloudError("request", "The API rejected the request; check model and configuration.")
    return CloudError("cloud", "Cloud operation failed; check configuration and network.")


def call_cloud(operation: Callable[[], T]) -> T:
    """Call once; set retry policy on the SDK client, never retry a whole turn."""
    try:
        return operation()
    except CloudError:
        raise
    except Exception as error:
        raise cloud_error(error) from None


def create_client(config, *, env=None, api_key=None):
    """Import the SDK lazily; an explicit key stays in this memory boundary.

    None preserves the existing environment path. An invalid explicit key never
    silently falls back to a different environment credential.
    """
    if api_key is None:
        env = os.environ if env is None else env
        selected = env.get("OPENAI_API_KEY", "")
    else:
        selected = api_key
    key = selected.strip() if type(selected) is str else ""
    if (not key or len(key) > 4096 or any(not 33 <= ord(c) <= 126 for c in key)
            or key == "YOUR_KEY_HERE" or key.startswith("YOUR_OPENAI")):
        raise CloudError("missing_key", "Set OPENAI_API_KEY in your environment before starting cloud mode.")
    try:
        from openai import OpenAI
    except ImportError:
        raise CloudError("missing_sdk", "Install software/requirements.txt in a virtual environment.") from None
    return OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                  timeout=config.timeout, max_retries=config.max_retries)
