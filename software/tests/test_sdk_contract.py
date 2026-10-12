"""Real OpenAI SDK + mock HTTP transport. Never contacts an API endpoint."""
import importlib.util
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from bmo_ai.audio import AudioTranscriptionError, speak, transcribe
from bmo_ai.cloud import CloudError
from bmo_ai.core import BMOBrain, BrainResponseError

HAS_SDK = importlib.util.find_spec("openai") is not None and importlib.util.find_spec("httpx") is not None


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\0\0" * 2400)
    return buffer.getvalue()


def response_json(text="Hello", status="completed"):
    return {
        "id": "resp_test", "object": "response", "created_at": 0,
        "model": "gpt-4.1-mini", "status": status,
        "error": None, "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": status,
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "parallel_tool_calls": False, "tools": [], "tool_choice": "auto",
        "max_output_tokens": 180, "store": False,
    }


@unittest.skipUnless(HAS_SDK, "SDK contract tests require requirements.txt; all standard-library tests still run")
class SDKContractTests(unittest.TestCase):
    def client(self, handler, retries=0):
        import httpx
        from openai import OpenAI
        client = OpenAI(api_key="test-key", base_url="https://api.openai.com/v1", timeout=3,
                        max_retries=retries, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        self.addCleanup(client.close)
        return client

    def test_responses_schema_and_local_bilingual_context(self):
        import httpx
        requests = []
        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            requests.append(json.loads(request.read()))
            return httpx.Response(200, json=response_json("안녕!"))
        brain = BMOBrain(self.client(handler))
        self.assertEqual(brain.ask("안녕 BMO"), "안녕!")
        brain.ask("Remember that?")
        self.assertFalse(requests[0]["store"])
        self.assertEqual(requests[0]["max_output_tokens"], 180)
        self.assertIn("English by default", requests[0]["instructions"])
        self.assertEqual([item["role"] for item in requests[1]["input"]], ["user", "assistant", "user"])
        self.assertNotIn("previous_response_id", requests[1])

    def test_incomplete_response_rolls_back(self):
        import httpx
        for status in ("incomplete", None):
            with self.subTest(status=status):
                brain = BMOBrain(self.client(lambda request: httpx.Response(200, json=response_json("partial", status))))
                with self.assertRaises(BrainResponseError):
                    brain.ask("hi")
                self.assertEqual(brain.history, [])

    def test_transcription_multipart_and_tts_stream_use_real_sdk(self):
        import httpx
        requests = []
        def handler(request):
            body = request.read()
            requests.append((request.url.path, body))
            if request.url.path.endswith("transcriptions"):
                self.assertIn(b'name="file"', body)
                self.assertIn(b'name="language"', body)
                self.assertIn(b"ko", body)
                return httpx.Response(200, json={"text": "안녕 BMO"})
            self.assertEqual(request.url.path, "/v1/audio/speech")
            payload = json.loads(body)
            self.assertEqual(payload["response_format"], "wav")
            self.assertEqual(payload["model"], "gpt-4o-mini-tts")
            self.assertEqual(payload["input"], "안녕 BMO")
            return httpx.Response(200, headers={"content-type": "audio/wav"}, content=wav_bytes())
        client = self.client(handler)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.wav"
            path.write_bytes(wav_bytes())
            self.assertEqual(transcribe(client, path, language="ko"), "안녕 BMO")
            with patch("bmo_ai.audio.subprocess.run") as playback:
                speak(client, "안녕 BMO", device="default")
                self.assertEqual(playback.call_args.args[0][0], "aplay")
                self.assertFalse(Path(playback.call_args.args[0][-1]).exists())
        self.assertEqual(len(requests), 2)

    def test_transient_failure_sdk_retries_once_and_auth_never_retries(self):
        import httpx
        requests = []
        def transient(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(503, json={"error": {"message": "private error", "type": "server_error"}})
            return httpx.Response(200, json=response_json())
        with patch("openai._base_client.time.sleep"):
            self.assertEqual(BMOBrain(self.client(transient, retries=1)).ask("hi"), "Hello")
        self.assertEqual(len(requests), 2)
        requests.clear()
        def denied(request):
            requests.append(request)
            return httpx.Response(401, json={"error": {"message": "private credential", "type": "invalid_request_error"}})
        with self.assertRaises(CloudError) as caught:
            BMOBrain(self.client(denied, retries=1)).ask("hi")
        self.assertEqual(caught.exception.code, "authentication")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(len(requests), 1)

    def test_connection_timeout_retries_are_bounded(self):
        import httpx
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private input", request=request)
        brain = BMOBrain(self.client(handler, retries=1))
        with patch("openai._base_client.time.sleep"), self.assertRaises(CloudError) as caught:
            brain.ask("hi")
        self.assertEqual(caught.exception.code, "timeout")
        self.assertEqual(len(calls), 2)
        self.assertEqual(brain.history, [])
