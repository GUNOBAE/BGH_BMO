import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bmo_ai.cloud import CloudError, call_cloud, cloud_error, create_client


class CloudTests(unittest.TestCase):
    def test_sanitizes_status_failures(self):
        for status, code in ((401, "authentication"), (403, "permission"), (404, "model"),
                             (429, "rate_limit"), (500, "server"), (400, "request")):
            with self.subTest(status=status):
                error = RuntimeError("private transcript and credential")
                error.status_code = status
                result = cloud_error(error)
                self.assertEqual(result.code, code)
                self.assertNotIn("private", str(result))
                self.assertNotIn("credential", str(result))

    def test_sdk_alone_retries(self):
        operation = Mock(side_effect=TimeoutError("private input"))
        with self.assertRaises(CloudError) as caught:
            call_cloud(operation)
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(operation.call_count, 1)

    def test_cancel_propagates(self):
        with self.assertRaises(KeyboardInterrupt):
            call_cloud(Mock(side_effect=KeyboardInterrupt))

    def test_key_missing_no_sdk_or_cloud(self):
        for env in ({}, {"OPENAI_API_KEY": "YOUR_KEY_HERE"}, {"OPENAI_API_KEY": "  "}):
            with self.assertRaises(CloudError):
                create_client(SimpleNamespace(timeout=30, max_retries=1), env=env)

    def test_client_transport_configuration(self):
        fake_sdk = SimpleNamespace(OpenAI=Mock(return_value="client"))
        with patch.dict("sys.modules", {"openai": fake_sdk}):
            config = SimpleNamespace(timeout=12.5, max_retries=1)
            self.assertEqual(create_client(config, env={"OPENAI_API_KEY": "test-key"}), "client")
        fake_sdk.OpenAI.assert_called_once_with(
            api_key="test-key", base_url="https://api.openai.com/v1", timeout=12.5, max_retries=1)
