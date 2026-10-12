import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

from bmo_ai.telemetry import LOGGER, EventFormatter, configure_logging, event, measure


class TelemetryTests(unittest.TestCase):
    def tearDown(self):
        for handler in LOGGER.handlers[:]:
            LOGGER.removeHandler(handler)
            handler.close()

    def test_structured_failure_timing_contains_no_exception(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(EventFormatter())
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        with self.assertRaises(RuntimeError):
            with measure("chat"):
                raise RuntimeError("private transcript test-key")
        data = json.loads(output.getvalue())
        self.assertEqual(data["outcome"], "error")
        self.assertGreaterEqual(data["duration_ms"], 0)
        self.assertNotIn("private", output.getvalue())
        self.assertNotIn("test-key", output.getvalue())

    def test_file_permissions_and_payload_loggers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            configure_logging(log_file=path)
            event("ready", mode="text")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            data = json.loads(path.read_text())
            self.assertEqual(data["event"], "ready")
            self.assertEqual(logging.getLogger("openai").level, logging.CRITICAL)

    def test_log_rotation_keeps_private_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            configure_logging(log_file=path)
            event("ready")
            file_handler = next(handler for handler in LOGGER.handlers if hasattr(handler, "doRollover"))
            file_handler.doRollover()
            event("shutdown")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(str(path) + ".1").stat().st_mode & 0o777, 0o600)

    def test_sdk_debug_override_cannot_propagate_payload(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            configure_logging()
            logging.getLogger("openai").setLevel(logging.DEBUG)
            logging.getLogger("openai._base_client").debug("private transcript credential")
            self.assertNotIn("private", output.getvalue())
        finally:
            root.removeHandler(handler)
