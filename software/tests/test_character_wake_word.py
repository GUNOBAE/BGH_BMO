"""Device-free acceptance boundaries; synthetic models prove no acoustic skill."""
from hashlib import sha256
import json
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai.conversation import TurnCancelled
from bmo_ai.features.input.microphone import MicError, PcmFrame, PttRecorder
from bmo_ai.features.input.microphone_owner import MicrophoneBroker
from bmo_ai.features.input.wake_word import LocalWakeDetector, PHRASES
from bmo_ai.features.resources import AudioFocus


def eventually(predicate, timeout=1.):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError("synthetic boundary did not settle")


class SessionShape:
    def __init__(self, incoming, outgoing):
        self.incoming, self.outgoing = incoming, outgoing

    def get_inputs(self):
        return [SimpleNamespace(shape=self.incoming)]

    def get_outputs(self):
        return [SimpleNamespace(shape=self.outgoing)]


class Array:
    def copy(self):
        return self


class NumpyBoundary:
    @staticmethod
    def frombuffer(pcm, dtype):
        if len(pcm) != 2560 or dtype != "<i2":
            raise AssertionError("incorrect local frontend frame")
        return Array()


class WakeManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        shapes = {"classifier": ([1, 16, 96], [1, 1]),
                  "melspectrogram": ([1, 1280], [1, 1, 5, 32]),
                  "embedding": ([1, 76, 32, 1], [1, 96])}
        self.data = dict(schema=1, backend="openwakeword_onnx", custom_model=True,
                         sample_rate=16000, frame_samples=1280, phrases=list(PHRASES),
                         frontend="openwakeword_melspectrogram_embedding_v1",
                         license="synthetic-test-fixture-only", artifacts={})
        for key, (incoming, outgoing) in shapes.items():
            path = self.folder / (key + ".onnx")
            path.write_bytes(b"NOT AN ONNX MODEL: test boundary only " + key.encode())
            self.data["artifacts"][key] = dict(file=path.name, sha256=sha256(path.read_bytes()).hexdigest(),
                license="synthetic-test-fixture-only", input_shape=incoming, output_shape=outgoing)
        self.path = self.folder / "manifest.json"
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")

    def loader(self, paths):
        class Model:
            resets = 0
            def predict(self, samples):
                return {"classifier": .9}
            def reset(self):
                self.resets += 1
        model = Model()
        model.models = {"classifier": SessionShape([1, 16, 96], [1, 1])}
        model.preprocessor = SimpleNamespace(melspec_model=SessionShape([1, 1280], [1, 1, 5, 32]),
                                             embedding_model=SessionShape([1, 76, 32, 1], [1, 96]))
        return model, NumpyBoundary()

    def test_constructor_snapshot_missing_model_do_not_import_or_load(self):
        with patch("bmo_ai.features.input.wake_word.importlib.import_module", side_effect=AssertionError):
            detector = LocalWakeDetector()
            self.assertEqual(detector.snapshot()["status"], "DISABLED")
            self.assertFalse(detector.load())
            self.assertEqual(detector.snapshot()["status"], "MODEL_MISSING")
            self.assertEqual(detector.snapshot()["real_wake_word"], "NOT_RUN")

    def test_checked_custom_model_adapter_uses_int16_80ms_chunks(self):
        detector = LocalWakeDetector(self.path, loader=self.loader)
        self.assertTrue(detector.load())
        self.assertFalse(detector.feed(b"\x00\x00" * 640))
        self.assertFalse(detector.feed(b"\x00\x00" * 640))
        self.assertFalse(detector.feed(b"\x00\x00" * 640))
        self.assertTrue(detector.feed(b"\x00\x00" * 640))
        self.assertEqual(detector.snapshot()["acoustic_acceptance"], "NOT_RUN")
        detector.close()
        self.assertFalse(detector.feed(b"\x00\x00" * 1280))

    def test_all_three_requested_phrases_are_required_not_jarvis_substitute(self):
        self.data["phrases"] = ["hey jarvis"]
        self.write()
        detector = LocalWakeDetector(self.path, loader=self.loader)
        self.assertFalse(detector.load())
        self.assertEqual(detector.snapshot()["status"], "MODEL_INVALID")

    def test_hash_tamper_missing_license_and_shape_mismatch_fail_closed(self):
        detector = LocalWakeDetector(self.path, loader=self.loader)
        artifact = self.data["artifacts"]["classifier"]
        original = dict(artifact)
        artifact["sha256"] = "0" * 64
        self.write()
        self.assertFalse(detector.load())
        self.assertEqual(detector.snapshot()["reason_code"], "wake_model_hash_mismatch")
        artifact.update(original)
        artifact["license"] = ""
        self.write()
        self.assertFalse(detector.load())
        artifact.update(original)
        artifact["input_shape"] = [1, 8, 96]
        self.write()
        self.assertFalse(detector.load())
        self.assertEqual(detector.snapshot()["reason_code"], "wake_model_shape_mismatch")

    def test_onnx_symlink_is_rejected(self):
        asset = self.folder / "classifier.onnx"
        asset.rename(self.folder / "external.onnx")
        asset.symlink_to(self.folder / "external.onnx")
        detector = LocalWakeDetector(self.path, loader=self.loader)
        self.assertFalse(detector.load())
        self.assertEqual(detector.snapshot()["status"], "MODEL_INVALID")

    def test_optional_runtime_missing_does_not_become_model_ready(self):
        def unavailable(paths):
            raise ModuleNotFoundError("private runtime detail")
        detector = LocalWakeDetector(self.path, loader=unavailable)
        self.assertFalse(detector.load())
        self.assertEqual(detector.snapshot()["status"], "RUNTIME_MISSING")
        self.assertNotIn("private", json.dumps(detector.snapshot()))

    def test_detector_error_disables_without_leaking_exception(self):
        def broken(paths):
            model, numpy = self.loader(paths)
            model.predict = lambda frame: (_ for _ in ()).throw(RuntimeError("secret path"))
            return model, numpy
        detector = LocalWakeDetector(self.path, loader=broken)
        self.assertTrue(detector.load())
        self.assertFalse(detector.feed(b"\x00\x00" * 1280))
        self.assertEqual(detector.snapshot()["status"], "MODEL_ERROR")
        self.assertNotIn("secret", json.dumps(detector.snapshot()))


class DetectorBoundary:
    """Injected orchestration boundary; never an acoustic-model test."""
    def __init__(self):
        self.status, self.feeds, self.resets = "DISABLED", 0, 0
        self.trigger = False

    def load(self):
        self.status = "READY"
        return True

    def snapshot(self):
        return dict(status=self.status, reason_code="ok", real_wake_word="NOT_RUN")

    def feed(self, pcm):
        self.feeds += 1
        return self.trigger

    def reset(self):
        self.resets += 1

    def close(self):
        self.status = "DISABLED"
        return True


class SourceBoundary:
    sample_rate = 16000
    simulated = True
    def __init__(self, factory):
        self.factory, self.queue = factory, Queue()
        self.closed, self.entered = Event(), Event()
        self.close_result = True

    def send(self, value=0):
        self.queue.put(int(value).to_bytes(2, "little", signed=True) * 640)

    def frames(self, capture_id, *, generation, cancel_check=None):
        self.factory.active += 1
        self.factory.maximum = max(self.factory.maximum, self.factory.active)
        self.entered.set()
        sequence = 0
        try:
            while not self.closed.is_set():
                if cancel_check is not None:
                    cancel_check()
                try:
                    pcm = self.queue.get(timeout=.01)
                except Empty:
                    continue
                yield PcmFrame(pcm, sequence, time.monotonic(), generation)
                sequence += 1
            raise TurnCancelled()
        finally:
            self.factory.active -= 1

    def close(self):
        if self.close_result:
            self.closed.set()
        return self.close_result


class SourceFactory:
    def __init__(self):
        self.sources, self.active, self.maximum = [], 0, 0

    def __call__(self):
        source = SourceBoundary(self)
        self.sources.append(source)
        return source


class MicrophoneOwnerTests(unittest.TestCase):
    def setUp(self):
        self.factory, self.detector = SourceFactory(), DetectorBoundary()
        self.triggers = []
        self.playback = False
        self.broker = MicrophoneBroker(self.factory, detector=self.detector,
            on_trigger=lambda: self.triggers.append("host-talk"),
            playback_active=lambda: self.playback, cooldown=.3)
        self.addCleanup(self.broker.close)

    def start(self):
        self.assertTrue(self.broker.start_wake(live_opt_in=True))
        eventually(lambda: self.factory.sources and self.factory.sources[-1].entered.is_set())
        return self.factory.sources[-1]

    def test_default_missing_model_ptt_fallback_creates_no_persistent_source(self):
        self.broker.detector = LocalWakeDetector()
        lazy = self.broker.source_factory()
        self.assertEqual(self.factory.sources, [])
        self.assertFalse(self.broker.start_wake())
        self.assertFalse(self.broker.start_wake(live_opt_in=True))
        self.assertEqual(self.broker.snapshot()["state"], "MODEL_MISSING")
        frames = lazy.frames("ptt-1", generation=1)
        result = []
        consumer = Thread(target=lambda: result.append(next(frames)), daemon=True)
        consumer.start()
        eventually(lambda: len(self.factory.sources) == 1)
        self.factory.sources[0].send(4000)
        consumer.join(timeout=1.)
        self.assertEqual(len(result), 1)
        frames.close()
        self.assertFalse(self.broker.snapshot()["microphone_open"])

    def test_wake_and_gp8_share_one_reader_and_bounded_preroll(self):
        physical = self.start()
        for _ in range(20):
            physical.send(100)
        eventually(lambda: self.detector.feeds == 20)
        self.assertEqual(self.broker.snapshot()["buffered_bytes"], 12800)
        cursor = self.broker.source_factory()
        frames = cursor.frames("ptt-2", generation=2)
        first = next(frames)
        self.assertEqual(first.generation, 2)
        self.assertEqual(len(self.factory.sources), 1)
        self.assertEqual(self.factory.maximum, 1)
        duplicate = self.broker.source_factory().frames("ptt-3", generation=3)
        with self.assertRaises(MicError):
            next(duplicate)
        self.assertEqual(len(self.factory.sources), 1)
        frames.close()
        self.assertFalse(self.broker.snapshot()["capture_active"])

    def test_repeated_detector_hits_make_one_pending_host_talk(self):
        physical = self.start()
        self.detector.trigger = True
        for _ in range(6):
            physical.send(100)
        eventually(lambda: len(self.triggers) == 1)
        self.assertEqual(self.triggers, ["host-talk"])
        self.assertEqual(self.broker.snapshot()["state"], "WAKE_PENDING")
        cursor = self.broker.source_factory()
        frames = cursor.frames("ptt-4", generation=4)
        next(frames)
        frames.close()
        self.assertEqual(self.factory.maximum, 1)

    def test_playback_suppresses_detector_and_closes_owned_mic_then_cools_down(self):
        physical = self.start()
        self.playback = True
        physical.send(100)
        eventually(lambda: physical.closed.is_set())
        self.assertEqual(self.detector.feeds, 0)
        self.assertEqual(self.triggers, [])
        self.playback = False
        eventually(lambda: len(self.factory.sources) == 2)
        self.assertEqual(self.factory.maximum, 1)
        self.assertGreaterEqual(self.detector.resets, 1)

    def test_wake_off_stops_persistent_source_and_detector_ptt_still_works(self):
        physical = self.start()
        self.assertTrue(self.broker.stop_wake())
        self.assertTrue(physical.closed.is_set())
        self.assertEqual(self.detector.status, "DISABLED")
        self.assertFalse(self.broker.snapshot()["reader_alive"])
        self.assertFalse(self.broker.snapshot()["microphone_open"])
        self.assertEqual(self.factory.maximum, 1)
        source = self.broker.source_factory()
        iterator = source.frames("ptt-5", generation=5)
        result = []
        consumer = Thread(target=lambda: result.append(next(iterator)), daemon=True)
        consumer.start()
        eventually(lambda: len(self.factory.sources) == 2)
        self.factory.sources[-1].send(4000)
        consumer.join(timeout=1.)
        self.assertEqual(len(result), 1)
        iterator.close()

    def test_detector_error_restart_waits_for_old_owned_source_cleanup(self):
        physical=self.start()
        old_thread=self.broker._thread
        closing,release=Event(),Event()
        real_close=physical.close
        def delayed_close():
            closing.set()
            if not release.wait(1.):raise AssertionError('Cleanup test gate expired')
            return real_close()
        physical.close=delayed_close
        self.detector.status='MODEL_ERROR'
        physical.send(1000)
        self.assertTrue(closing.wait(1.))
        try:
            self.assertFalse(self.broker.snapshot()['wake_enabled'])
            self.assertFalse(self.broker.start_wake(live_opt_in=True))
            self.assertEqual(len(self.factory.sources),1)
        finally:
            release.set();old_thread.join(timeout=1.)
        self.assertFalse(old_thread.is_alive())
        self.assertTrue(self.broker.start_wake(live_opt_in=True))
        eventually(lambda:len(self.factory.sources)==2 and self.factory.sources[-1].entered.is_set())
        self.assertTrue(physical.closed.is_set())
        self.assertEqual(self.factory.maximum,1)

    def test_existing_ptt_recorder_uses_shared_stream_vad_and_releases_cursor(self):
        physical = self.start()
        recorder = PttRecorder(AudioFocus(), self.broker.source_factory, speech_safe=True)
        result, errors = [], []
        def record():
            try:
                result.append(recorder.capture("ptt-6", generation=6))
            except Exception as error:
                errors.append(type(error).__name__)
        consumer = Thread(target=record, daemon=True)
        consumer.start()
        eventually(lambda: self.broker.snapshot()["capture_active"])
        for _ in range(8):
            physical.send(5000)
        for _ in range(24):
            physical.send(0)
        consumer.join(timeout=1.)
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, "ready")
        self.assertGreater(len(result[0].pcm), 0)
        self.assertFalse(self.broker.snapshot()["capture_active"])
        self.assertEqual(self.factory.maximum, 1)
        self.assertEqual(len(self.factory.sources), 1)
        recorder.close()

    def test_missing_or_failed_detector_never_claims_real_acceptance(self):
        self.assertEqual(self.broker.snapshot()["real_wake_word"], "NOT_RUN")
        physical = self.start()
        self.detector.status = "MODEL_ERROR"
        physical.send(100)
        eventually(lambda: not self.broker.snapshot()["wake_enabled"])
        eventually(lambda: not self.broker.snapshot()["microphone_open"])
        self.assertEqual(self.broker.snapshot()["real_wake_word"], "NOT_RUN")

    def test_finite_cleanup_failure_blocks_every_new_owner(self):
        cursor = self.broker.source_factory()
        frames = cursor.frames("ptt-cleanup", generation=8)
        received = []
        consumer = Thread(target=lambda: received.append(next(frames)), daemon=True)
        consumer.start()
        eventually(lambda: len(self.factory.sources) == 1)
        physical = self.factory.sources[0]
        physical.send(5000)
        consumer.join(timeout=1.)
        physical.close_result = False
        self.assertFalse(cursor.close())
        self.assertFalse(self.broker.snapshot()["cleanup_verified"])
        self.assertFalse(self.broker.start_wake(live_opt_in=True))
        second = self.broker.source_factory().frames("ptt-blocked", generation=9)
        with self.assertRaises(MicError):
            next(second)
        self.assertEqual(len(self.factory.sources), 1)
        # Retry is explicit and only retires this test's injected source; an
        # uncertain broker remains disabled rather than silently reopening IO.
        physical.close_result = True
        self.assertTrue(cursor.close())
        frames.close()
        self.assertFalse(self.broker.close())


if __name__ == "__main__":
    unittest.main()
