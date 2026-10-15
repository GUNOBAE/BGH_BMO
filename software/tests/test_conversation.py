"""Deterministic worker contracts: synthetic adapters, gates and fake time only."""
from dataclasses import replace
from threading import Event, get_ident
import time
import tracemalloc
import gc
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.app import ConversationApp
from bmo_ai.audio import AudioValidationError
from bmo_ai.cloud import CloudError
from bmo_ai.config import BMOConfig
from bmo_ai.conversation import ConversationWorker, TurnCancelled
from bmo_ai.hardware import FaceState
from bmo_ai.offline_demo import offline_app


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.workers = []
        self.releases = []
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe", "bmo_ai.audio.speak",
                       "bmo_ai.cloud.create_client"):
            self.enterContext(patch(target, side_effect=AssertionError("external operation forbidden")))

    def tearDown(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.close()
            if worker._thread is not None:
                worker._thread.join(timeout=2)
            self.assertFalse(worker.worker_alive)

    def worker(self, factory=None, **kwargs):
        worker = ConversationWorker(factory or (lambda face, cancel:
            offline_app("success", face, cancel, delay=0.001)), **kwargs)
        self.workers.append(worker)
        return worker

    def finish(self, worker):
        items = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            items.extend(worker.poll())
            if any(item.kind == "finished" for item in items):
                return items
            time.sleep(0.001)
        self.fail("bounded worker test did not finish")

    def gated(self, phase, *, cooperative=False):
        entered, release = Event(), Event()
        self.releases.append(release)
        calls = []

        def factory(face, cancellation):
            def step(name):
                calls.append(name)
                if name == phase:
                    entered.set()
                    if cooperative:
                        if cancellation.wait(2):
                            raise TurnCancelled()
                    elif not release.wait(2):
                        raise AssertionError("test gate exceeded bound")
                if cancellation.is_set():
                    raise TurnCancelled()

            step("prepare")

            def listen():
                step("input")
                return "synthetic input"

            def ask(message):
                step("llm")
                return "synthetic reply"

            def speak(text, *, on_playback_start):
                step("tts")
                on_playback_start()
                step("playback")

            return ConversationApp(SimpleNamespace(ask=ask), None, BMOConfig(), face=face,
                voice=SimpleNamespace(listen=listen, speak=speak))

        return factory, entered, release, calls

    def test_success_events_and_only_owner_applies_face_states(self):
        worker = self.worker(synthetic=True)
        self.assertTrue(worker.start())
        items = self.finish(worker)
        states = [item.state for item in items if item.kind == "state"]
        self.assertEqual(states, [FaceState.LISTENING, FaceState.THINKING, FaceState.THINKING,
                                 FaceState.SPEAKING, FaceState.IDLE])
        owner = get_ident()
        face = Mock()
        face.set_state.side_effect = lambda state: self.assertEqual(get_ident(), owner)
        for item in items:
            if item.state is not None:
                face.set_state(item.state)
        self.assertEqual(items[-1].code, "ok")
        self.assertTrue(items[-1].synthetic)
        self.assertFalse(worker.busy)

    def test_duplicate_inputs_never_create_another_worker_or_queue_commands(self):
        factory, entered, release, calls = self.gated("llm")
        worker = self.worker(factory)
        worker.start()
        self.assertTrue(entered.wait(1))
        thread = worker._thread
        for _ in range(100):
            self.assertFalse(worker.start())
        self.assertIs(worker._thread, thread)
        self.assertTrue(thread.daemon)
        release.set()
        self.finish(worker)
        self.assertEqual(calls.count("llm"), 1)

    def test_cancel_at_each_stage_requests_then_waits_for_actual_completion(self):
        for phase in ("prepare", "input", "llm", "tts", "playback"):
            with self.subTest(phase=phase):
                factory, entered, release, calls = self.gated(phase)
                worker = self.worker(factory)
                worker.start()
                self.assertTrue(entered.wait(1))
                self.assertTrue(worker.cancel())
                self.assertFalse(worker.cancel())
                items = worker.poll()
                self.assertEqual([item.kind for item in items], ["cancel_requested"])
                self.assertTrue(worker.worker_alive)
                self.assertTrue(worker.busy)
                self.assertFalse(worker.start())
                release.set()
                items = self.finish(worker)
                self.assertEqual(items[-1].code, "cancelled")
                self.assertFalse(any(item.kind == "state" for item in items))
                self.assertEqual(calls[-1], phase)

    def test_cooperative_cancellation_stops_blocked_adapter(self):
        factory, entered, release, calls = self.gated("llm", cooperative=True)
        worker = self.worker(factory)
        worker.start()
        self.assertTrue(entered.wait(1))
        worker.cancel()
        self.assertEqual(self.finish(worker)[-1].code, "cancelled")
        self.assertNotIn("tts", calls)

    def test_timeout_is_a_request_until_slow_worker_actually_exits(self):
        now = [0.0]
        factory, entered, release, calls = self.gated("llm")
        worker = self.worker(factory, timeout=1, clock=lambda: now[0])
        worker.start()
        self.assertTrue(entered.wait(1))
        now[0] = 2
        notice = worker.poll()[-1]
        self.assertEqual((notice.kind, notice.code, notice.state),
                         ("cancel_requested", "timeout", FaceState.ERROR))
        self.assertTrue(worker.busy)
        self.assertFalse(worker.start())
        release.set()
        result = self.finish(worker)[-1]
        self.assertEqual(result.code, "timeout")
        self.assertEqual(result.state, FaceState.IDLE)

    def test_slow_ui_poll_does_not_timeout_already_completed_turn(self):
        now = [0.0]
        worker = self.worker(clock=lambda: now[0], timeout=1)
        worker.start()
        # A non-UI test join establishes completion before advancing fake time.
        worker._thread.join(timeout=1)
        self.assertFalse(worker.worker_alive)
        now[0] = 100
        self.assertEqual(self.finish(worker)[-1].code, "ok")

    def test_close_is_nonblocking_and_late_response_cannot_reach_ui(self):
        factory, entered, release, calls = self.gated("llm")
        worker = self.worker(factory)
        worker.start()
        self.assertTrue(entered.wait(1))
        with patch.object(worker._thread, "join", side_effect=AssertionError("UI joined worker")):
            self.assertFalse(worker.close())
        self.assertEqual(worker.poll(), ())
        self.assertFalse(worker.start())
        release.set()
        worker._thread.join(timeout=1)
        self.assertTrue(worker.close())
        self.assertEqual(worker.poll(), ())
        self.assertNotIn("tts", calls)

    def test_queue_saturation_is_bounded_and_cancel_releases_backpressure(self):
        entered = Event()

        def factory(face, cancel):
            entered.set()
            for _ in range(100):
                face.set_state(FaceState.THINKING)
            return offline_app("success", face, cancel, delay=0.001)

        worker = self.worker(factory, queue_size=2)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertLessEqual(worker.queued_events, 2)
        worker.cancel()
        self.assertEqual(self.finish(worker)[-1].code, "cancelled")
        self.assertLessEqual(worker.queued_events, 2)

    def test_queue_size_one_preserves_completion_and_state_order(self):
        worker = self.worker(queue_size=1)
        worker.start()
        items = self.finish(worker)
        self.assertEqual(items[-1].kind, "finished")
        self.assertEqual(items[-1].code, "ok")
        self.assertIn(FaceState.SPEAKING, [item.state for item in items])

    def test_cancel_during_full_state_queue_never_starts_next_dependency(self):
        for target in ("input", "llm", "tts"):
            with self.subTest(target=target):
                blocked = Event()
                release = Event()
                self.releases.append(release)
                calls = []

                def factory(face, cancellation):
                    # Fill the queue immediately before the target's state
                    # publication. Dependencies deliberately ignore cancellation.
                    def listen():
                        calls.append("input")
                        return "synthetic"

                    def ask(text):
                        calls.append("llm")
                        return "synthetic"

                    def speak(text, **kwargs):
                        calls.append("tts")

                    if target == "input":
                        face.set_state(FaceState.IDLE)
                    return ConversationApp(SimpleNamespace(ask=ask), None, BMOConfig(), face=face,
                        voice=SimpleNamespace(listen=listen, speak=speak))

                worker = self.worker(factory, queue_size=1)
                real_put = worker._events.put
                def put(item, *args, **kwargs):
                    at_target = (
                        (target == "input" and item.state == FaceState.LISTENING)
                        or (target == "llm" and item.state == FaceState.THINKING)
                        or (target == "tts" and item.state == FaceState.THINKING and "llm" in calls)
                    )
                    if at_target:
                        blocked.set()
                        if not release.wait(1):
                            raise AssertionError("test gate exceeded bound")
                    return real_put(item, *args, **kwargs)

                worker._events.put = put
                worker.start()
                deadline = time.monotonic() + 1
                while not blocked.is_set() and time.monotonic() < deadline:
                    worker.poll()
                    time.sleep(0.001)
                self.assertTrue(blocked.is_set())
                worker.cancel()
                release.set()
                self.assertEqual(self.finish(worker)[-1].code, "cancelled")
                self.assertNotIn(target, calls)

    def test_stale_turn_metadata_is_discarded(self):
        worker = self.worker()
        worker.start()
        first = self.finish(worker)[-1]
        worker.start()
        worker._events.put_nowait(replace(first, kind="state", state=FaceState.ERROR))
        items = self.finish(worker)
        self.assertTrue(all(item.turn == 2 for item in items))
        self.assertNotIn(FaceState.ERROR, [item.state for item in items])

    def test_repeat_100_turns_releases_threads_and_queues(self):
        worker = self.worker(lambda face, cancel: offline_app("empty_stt", face, cancel, delay=0.00001),
                             queue_size=2)
        for turn in range(1, 101):
            self.assertTrue(worker.start())
            result = self.finish(worker)[-1]
            self.assertEqual((result.turn, result.code), (turn, "empty_stt"))
            self.assertIsNone(worker._thread)
            self.assertIsNone(worker._result)
            self.assertEqual(worker.queued_events, 0)
        self.assertEqual(worker.turn, 100)

    def test_finite_repeated_turns_have_bounded_retained_python_memory(self):
        worker = self.worker(lambda face, cancel: offline_app("empty_stt", face, cancel, delay=0.00001))
        already_tracing = tracemalloc.is_tracing()
        if not already_tracing:
            tracemalloc.start()
        try:
            for _ in range(10):
                worker.start()
                self.finish(worker)
            gc.collect()
            baseline = tracemalloc.get_traced_memory()[0]
            for _ in range(100):
                worker.start()
                self.finish(worker)
            gc.collect()
            retained = tracemalloc.get_traced_memory()[0] - baseline
            # A finite regression bound, not a claim about native SDL memory or
            # arbitrarily long runs. Transient Thread/app objects must be freed.
            self.assertLess(retained, 256 * 1024)
            self.assertIsNone(worker._thread)
            self.assertEqual(worker.queued_events, 0)
        finally:
            if not already_tracing:
                tracemalloc.stop()

    def test_exception_codes_are_sanitized_and_next_turn_recovers(self):
        errors = [CloudError("authentication", "private provider body"),
                  CloudError("rate_limit", "private provider body"),
                  AudioValidationError("private path", "invalid_wav"),
                  TimeoutError("private request"), RuntimeError("private prompt"),
                  CloudError("private_code", "private body")]
        for error, expected in zip(errors, ("authentication", "rate_limit", "invalid_wav", "timeout",
                                             "operation_failed", "operation_failed")):
            with self.subTest(expected=expected):
                fail = [True]

                def factory(face, cancel):
                    if fail[0]:
                        raise error
                    return offline_app("success", face, cancel, delay=0.001)

                worker = self.worker(factory)
                worker.start()
                result = self.finish(worker)[-1]
                self.assertEqual(result.code, expected)
                self.assertEqual(result.state, FaceState.ERROR)
                self.assertNotIn("private", repr(result))
                fail[0] = False
                worker.start()
                self.assertEqual(self.finish(worker)[-1].code, "ok")

    def test_tts_completion_is_not_speaking_until_player_start(self):
        factory, entered, release, calls = self.gated("tts")
        worker = self.worker(factory)
        worker.start()
        self.assertTrue(entered.wait(1))
        items = worker.poll()
        self.assertNotIn(FaceState.SPEAKING, [item.state for item in items])
        release.set()
        items = list(items) + self.finish(worker)
        self.assertIn(FaceState.SPEAKING, [item.state for item in items])

    def test_mock_timings_separate_prepare_input_llm_tts_and_playback(self):
        now = [0.0]

        def advance(seconds, value=None):
            now[0] += seconds
            return value

        def factory(face, cancel):
            advance(0.1)

            def speak(text, *, on_playback_start):
                advance(0.4)
                on_playback_start()
                advance(0.5)

            return ConversationApp(SimpleNamespace(ask=lambda text: advance(0.3, "reply")),
                None, BMOConfig(), face=face,
                voice=SimpleNamespace(listen=lambda: advance(0.2, "input"), speak=speak))

        worker = self.worker(factory, clock=lambda: now[0], synthetic=True)
        worker.start()
        result = self.finish(worker)[-1]
        self.assertEqual([stage for stage, _ in result.timings], ["prepare", "input", "llm", "tts", "playback"])
        for (_, seconds), expected in zip(result.timings, (0.1, 0.2, 0.3, 0.4, 0.5)):
            self.assertAlmostEqual(seconds, expected)
        self.assertTrue(result.synthetic)

    def test_failed_tts_has_no_playback_timing_or_speaking_state(self):
        worker = self.worker(lambda face, cancel: offline_app("broken_tts", face, cancel, delay=0.001))
        worker.start()
        items = self.finish(worker)
        self.assertEqual(items[-1].code, "invalid_wav")
        self.assertNotIn("playback", dict(items[-1].timings))
        self.assertNotIn(FaceState.SPEAKING, [item.state for item in items])

    def test_empty_stt_never_calls_llm_or_tts(self):
        voice = SimpleNamespace(listen=lambda: "  ", speak=Mock())
        brain = Mock()
        worker = self.worker(lambda face, cancel: ConversationApp(brain, None, BMOConfig(),
                                                                 face=face, voice=voice))
        worker.start()
        result = self.finish(worker)[-1]
        self.assertEqual(result.code, "empty_stt")
        brain.ask.assert_not_called()
        voice.speak.assert_not_called()

    def test_invalid_settings_rejected_before_thread_creation(self):
        for value in (0, -1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ConversationWorker(Mock(), timeout=value)
        for value in (0, 257, True):
            with self.assertRaises(ValueError):
                ConversationWorker(Mock(), queue_size=value)

    def test_owner_thread_contract_rejects_worker_control(self):
        errors = []
        worker = self.worker()

        def factory(face, cancel):
            for operation in (worker.start, worker.poll, worker.cancel, worker.close):
                try:
                    operation()
                except RuntimeError:
                    errors.append(True)
            return offline_app("success", face, cancel, delay=0.001)

        worker._factory = factory
        worker.start()
        self.assertEqual(self.finish(worker)[-1].code, "ok")
        self.assertEqual(len(errors), 4)


if __name__ == "__main__":
    unittest.main()
