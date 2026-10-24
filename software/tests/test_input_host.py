"""Synthetic sources and pure PCM use the persistent offline FeatureHost only."""
import json
import struct
import time
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.input import RawInputEvent, SyntheticEventSource, VerifiedMapping
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class InputHostTests(unittest.TestCase):
    def setUp(self):
        self.now, self.hosts = 0., []
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Live operation forbidden")))

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def host(self, **settings):
        client = FeatureSimulatedClient([dict(version=1, speech="Synthetic response.", emotion="crying",
                                              gesture="none", intent=None)] * 3)
        def player(command, *, cancel_check, on_start, **kwargs):
            cancel_check(); on_start(); cancel_check()
        voice = IntegrationVoice(integration_config(), client, simulate=True,
                                 emit=lambda *a, **kw: None, player=player)
        host = FeatureHost(demo_assets=True, clock=lambda: self.now, client=client, voice=voice, **settings)
        self.hosts.append(host)
        return host

    def finish_voice(self, host):
        deadline = time.monotonic() + 3
        while host.session.busy and time.monotonic() < deadline:
            host.poll(); time.sleep(.002)
        self.assertFalse(host.session.busy)
        host.poll()

    def ready(self, host, capture_id="ptt:1"):
        self.assertTrue(host.start_ptt(capture_id))
        generation = host.pcm.snapshot()["generation"]
        for sequence in range(4):
            self.now += .02
            self.assertTrue(host.pcm.feed(struct.pack("<h", 2000) * 320, sequence=sequence,
                                         timestamp=self.now, generation=generation))
        self.assertTrue(host.pcm.release_ptt(generation=generation))
        return generation

    def execute(self, host, name, target=None, request="input:mode"):
        return host.execute(HostIntent(name, target), request_id=request,
                            expected_generation=host.modes.generation)

    def test_pcm_ready_uses_same_brain_and_start_question_once_without_live_audio(self):
        transcriber = Mock(return_value="Synthetic PCM question.")
        host = self.host(pcm_transcriber=transcriber)
        self.assertTrue(host.start_question("Synthetic seed."))
        self.finish_voice(host)
        brain, history_count = host.session.brain, len(host.session.brain.history)
        self.ready(host)
        host.poll(); self.finish_voice(host)
        for _ in range(5): host.poll()
        transcriber.assert_called_once()
        self.assertEqual(transcriber.call_args.args[0].status, "ready")
        self.assertIs(host.session.brain, brain)
        self.assertGreater(len(brain.history), history_count)
        self.assertEqual(len(host.client.requests), 2)
        self.assertEqual(host.snapshot()["emotion"], "crying")
        self.assertNotIn("Synthetic PCM question", json.dumps(host.snapshot()))
        self.assertEqual(host.snapshot()["ptt"]["real_audio"], "NOT_RUN")
        self.assertFalse(host.snapshot()["wake_word"]["enabled"])

    def test_no_speech_and_default_disabled_never_transcribe_or_create(self):
        disabled = self.host()
        self.assertFalse(disabled.start_ptt("disabled:1"))
        transcriber = Mock(return_value="Must not reach conversation.")
        host = self.host(pcm_transcriber=transcriber)
        self.assertTrue(host.start_ptt("ptt:silent"))
        generation = host.pcm.snapshot()["generation"]
        host.pcm.feed(bytes(640), sequence=0, timestamp=self.now, generation=generation)
        self.now = 3.1
        host.poll()
        transcriber.assert_not_called()
        self.assertEqual(host.client.requests, [])

    def test_mode_change_after_ready_discards_pcm_and_does_not_return_to_conversation(self):
        transcriber = Mock(return_value="Discarded mode candidate.")
        host = self.host(pcm_transcriber=transcriber)
        self.ready(host)
        self.execute(host, "open_game_menu")
        host.poll()
        transcriber.assert_not_called()
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertEqual(host.client.requests, [])
        self.assertFalse(host.start_ptt("ptt:game"))

    def test_cancelled_capture_and_old_generation_cannot_feed_new_capture(self):
        transcriber = Mock(return_value="Only the new capture.")
        host = self.host(pcm_transcriber=transcriber)
        old = self.ready(host)
        host.cancel_conversation(); host.poll()
        self.assertTrue(host.start_ptt("ptt:new"))
        new = host.pcm.snapshot()["generation"]
        self.assertGreater(new, old)
        self.assertFalse(host.pcm.feed(struct.pack("<h", 2000) * 320, sequence=999,
                                       timestamp=self.now, generation=old))
        for sequence in range(4):
            self.now += .02
            self.assertTrue(host.pcm.feed(struct.pack("<h", 2000) * 320, sequence=sequence,
                                         timestamp=self.now, generation=new))
        host.pcm.release_ptt(generation=new)
        host.poll(); self.finish_voice(host)
        transcriber.assert_called_once()
        self.assertEqual(transcriber.call_args.args[0].generation, new)
        self.assertEqual(len(host.client.requests), 1)

    def test_queued_stop_prevents_pending_pcm_transcription_before_side_effect(self):
        transcriber = Mock(return_value="Must not start before STOP.")
        host = self.host(pcm_transcriber=transcriber)
        self.ready(host)
        host.queue_command("stop:pending", HostControl("stop"))
        start = self.enterContext(patch.object(host, "start_question", wraps=host.start_question))
        host.poll()
        transcriber.assert_not_called()
        start.assert_not_called()
        self.assertEqual(host.client.requests, [])

    def test_queued_cancel_prevents_pending_pcm_transcription(self):
        transcriber = Mock(return_value="Must not start before cancel.")
        host = self.host(pcm_transcriber=transcriber)
        self.ready(host)
        host.queue_command("cancel:pending", HostControl("cancel"))
        host.poll()
        transcriber.assert_not_called()
        self.assertEqual(host.client.requests, [])

    def test_transcriber_cancel_invalidates_its_own_late_result(self):
        holder = {}
        def transcribe(capture):
            holder["host"].cancel_conversation()
            return "Late transcript after cancellation."
        host = holder["host"] = self.host(pcm_transcriber=transcribe)
        self.ready(host)
        start = self.enterContext(patch.object(host, "start_question", wraps=host.start_question))
        host.poll()
        start.assert_not_called()
        self.assertEqual(host.client.requests, [])

    def test_transcriber_mode_change_cannot_be_overwritten_by_late_text(self):
        holder = {}
        def transcribe(capture):
            self.execute(holder["host"], "open_game_menu")
            return "Late transcript from old mode."
        host = holder["host"] = self.host(pcm_transcriber=transcribe)
        self.ready(host)
        start = self.enterContext(patch.object(host, "start_question", wraps=host.start_question))
        host.poll()
        start.assert_not_called()
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertEqual(host.client.requests, [])

    def test_transcriber_new_capture_cannot_be_hijacked_by_old_result(self):
        holder = {}
        def transcribe(capture):
            self.assertTrue(holder["host"].start_ptt("ptt:newer"))
            return "Late text from older capture."
        host = holder["host"] = self.host(pcm_transcriber=transcribe)
        old = self.ready(host)
        start = self.enterContext(patch.object(host, "start_question", wraps=host.start_question))
        host.poll()
        start.assert_not_called()
        self.assertGreater(host.pcm.snapshot()["generation"], old)
        self.assertEqual(host.pcm.snapshot()["state"], "listening")
        self.assertEqual(host.client.requests, [])

    def test_transcriber_invalid_or_error_result_does_not_leak_or_create_request(self):
        for result in (None, " ", "a" * 513, RuntimeError("private PCM payload")):
            transcriber = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
            host = self.host(pcm_transcriber=transcriber)
            self.ready(host, "ptt:invalid")
            host.poll()
            transcriber.assert_called_once()
            self.assertEqual(host.client.requests, [])
            self.assertNotIn("private PCM", json.dumps(host.snapshot()))

    def test_input_stop_preempts_talk_and_pending_pcm_in_same_owner_poll(self):
        source = SyntheticEventSource((RawInputEvent("synthetic", "talk", True, 0, .08),
                                       RawInputEvent("synthetic", "stop", True, 1, .08)), clock=lambda: self.now)
        mapping = VerifiedMapping("synthetic", {"talk": "TALK", "stop": "STOP"},
                                  verified=True, provenance="synthetic_test")
        transcriber = Mock(return_value="Discarded PCM.")
        host = self.host(input_source=source, input_mapping=mapping, pcm_transcriber=transcriber)
        self.ready(host)
        start = self.enterContext(patch.object(host, "start_question", wraps=host.start_question))
        host.poll()
        transcriber.assert_not_called()
        start.assert_not_called()
        self.assertEqual(host.client.requests, [])
        self.assertEqual(host.snapshot()["input"]["real_input"], "NOT_RUN")

    def test_input_trace_preserves_game_b_and_delegates_held_exit_to_game(self):
        events = tuple(RawInputEvent("synthetic", key, pressed, seq, 0.) for seq, (key, pressed) in enumerate(
                       (("b", True), ("b", False), ("start", True), ("select", True))))
        source = SyntheticEventSource(events, clock=lambda: self.now)
        mapping = VerifiedMapping("synthetic", {"b": "B", "start": "START", "select": "SELECT"},
                                  verified=True, provenance="synthetic_test")
        host = self.host(input_source=source, input_mapping=mapping)
        self.execute(host, "launch_game", "fixture_game_a")
        host.poll()
        runner = host.games._runner
        self.assertEqual(host.modes.mode, "game")
        self.assertIn(("B", True), runner.inputs)
        self.now = 1.199; host.poll()
        self.assertEqual(host.modes.mode, "game")
        self.now = 1.2; host.poll()
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertIsNone(host.audio.owner)
        self.assertFalse(runner.running)
        host.poll()
        self.assertEqual(host.modes.mode, "game_menu")

    def test_live_runtime_refuses_synthetic_input_before_creating_resources(self):
        client = FeatureSimulatedClient()
        for settings in ({"input_source": Mock()}, {"pcm_transcriber": Mock()}):
            with self.subTest(settings=settings), self.assertRaises(FeatureError) as caught:
                FeatureHost(runtime=RuntimePolicy(simulate=False), client=client, **settings)
            self.assertEqual(caught.exception.code, "input_disabled")
        self.assertEqual(client.requests, [])

    def test_close_retires_source_and_pending_audio_without_consuming_result(self):
        source = SyntheticEventSource(clock=lambda: self.now)
        transcriber = Mock(return_value="Never consumed.")
        host = self.host(input_source=source, pcm_transcriber=transcriber)
        self.ready(host)
        host.close(); host.close()
        host.poll()
        self.assertTrue(source.closed)
        self.assertEqual(host.pcm.poll(), ())
        self.assertEqual(host.pcm.snapshot()["state"], "closed")
        transcriber.assert_not_called()


if __name__ == "__main__":
    unittest.main()
