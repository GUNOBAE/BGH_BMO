"""Small structured logger: fixed events and numeric timings, no transcripts."""
import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER = logging.getLogger("bmo_ai")


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(fd)
            raise


class EventFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }, ensure_ascii=True, allow_nan=False)


def configure_logging(level="INFO", log_file=None):
    """Rotate optional private logs; do not enable HTTP/SDK payload logging."""
    for handler in LOGGER.handlers[:]:
        LOGGER.removeHandler(handler)
        handler.close()
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    stream = logging.StreamHandler()
    stream.setFormatter(EventFormatter())
    LOGGER.addHandler(stream)
    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = PrivateRotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                             encoding="utf-8", delay=False)
        handler.setFormatter(EventFormatter())
        LOGGER.addHandler(handler)
    for name in ("openai", "httpx", "httpcore"):
        provider = logging.getLogger(name)
        provider.setLevel(logging.CRITICAL)
        # OPENAI_LOG can reset levels on SDK import. Contain payload logging
        # even in that case, and prevent propagation into user/global handlers.
        provider.handlers.clear()
        provider.addHandler(logging.NullHandler())
        provider.propagate = False


def event(name, **fields):
    LOGGER.info(name, extra={"fields": fields})


@contextmanager
def measure(stage):
    start = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        event("stage", stage=stage, outcome=outcome,
              duration_ms=round((time.perf_counter() - start) * 1000, 3))
