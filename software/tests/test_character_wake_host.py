"""Production Host wiring with synthetic IO; no acoustic wake-model claim."""
import time
from threading import Event
import unittest
from unittest.mock import patch

from bmo_ai.character_presence import IdleGaze
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.input.microphone import SyntheticPcmSource
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from test_character_wake_word import DetectorBoundary, SourceFactory, eventually


class Clock:
    def __init__(self): self.now = 100.
    def __call__(self): return self.now


class HostWakeTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.cloud.create_client", "bmo_ai.fish_tts.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("real IO forbidden")))
        self.hosts, self.sources = [], []

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def make_host(self, **options):
        clock = options.pop("clock", time.monotonic)
        client = FeatureSimulatedClient()
        config = integration_config()
        def player(command, *, on_start, cancel_check, **kwargs):
            cancel_check(); on_start(); cancel_check()
        voice = IntegrationVoice(config, client, simulate=True, character_life=True,
                                 player=player, emit=lambda *a, **k: None)
        def source():
            value = SyntheticPcmSource(clock=clock)
            self.sources.append(value)
            return value
        options.setdefault("ptt_source_factory", source)
        host = FeatureHost(config=config, client=client, voice=voice, clock=clock,
            runtime=RuntimePolicy(conversation_ptt_microphone=True),
            character_life=True, wake_word=True, ptt_speech_safe=True, **options)
        self.hosts.append(host)
        return host, client

    def finish(self, host):
        until = time.monotonic() + 3
        while host.session.busy and time.monotonic() < until:
            host.poll(); time.sleep(.002)
        host.poll()
        self.assertFalse(host.session.busy)

    def test_opt_in_requires_character_life_and_existing_microphone_policy(self):
        for options in ({"character_life": False, "runtime": RuntimePolicy(conversation_ptt_microphone=True)},
                        {"character_life": True, "runtime": RuntimePolicy()}):
            with self.assertRaises(FeatureError) as error:
                FeatureHost(wake_word=True, **options)
            self.assertEqual(error.exception.code, "wake_word_requires_character_microphone")
        with self.assertRaises(FeatureError):
            FeatureHost(wake_model_manifest="not-loaded.json")

    def test_construction_and_boot_do_not_load_or_open_and_missing_model_preserves_ptt(self):
        host, client = self.make_host()
        self.assertEqual(host.snapshot()["wake_word"]["status"], "DISABLED")
        self.assertFalse(host.snapshot()["wake_word"]["armed"])
        self.assertEqual(self.sources, [])
        self.assertFalse(host._queue_wake())
        self.assertFalse(host.enable_wake_word())
        state = host.snapshot()
        self.assertEqual(state["wake_word"]["status"], "MODEL_MISSING")
        self.assertEqual(state["microphone_broker"]["state"], "MODEL_MISSING")
        self.assertEqual(state["wake_word"]["real_wake_word"], "NOT_RUN")
        self.assertEqual(self.sources, [])
        self.assertFalse(host._queue_wake())
        self.assertTrue(host.ptt_button())
        self.finish(host)
        self.assertEqual(len(self.sources), 1)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(host.snapshot()["wake_word"]["real_wake_word"], "NOT_RUN")

    def test_wake_off_is_explicit_and_keeps_finite_gp8_available(self):
        host, client = self.make_host()
        self.assertFalse(host.enable_wake_word())
        self.assertTrue(host.disable_wake_word())
        self.assertFalse(host.snapshot()["wake_word"]["armed"])
        self.assertEqual(host.snapshot()["wake_word"]["status"], "DISABLED")
        self.assertFalse(host.snapshot()["microphone_broker"]["reader_alive"])
        self.assertTrue(host.ptt_button())
        self.finish(host)
        self.assertEqual(len(client.requests), 1)
        host.close()
        self.assertTrue(host.cleanup_snapshot()["resources_cleaned"])
        self.assertFalse(host.cleanup_snapshot()["microphone_broker"]["microphone_open"])

    def test_queue_receipt_is_host_clock_and_retained_across_turn_admission(self):
        clock = Clock()
        host, _ = self.make_host(clock=clock)
        host.queue_command("queued-ptt-receipt", HostControl("ptt"))
        clock.now += .2
        host.poll(); self.finish(host)
        trace = host.snapshot()["turn_diagnostics"]
        self.assertEqual(trace["markers_ms"]["host_input_received"], 0.)
        self.assertAlmostEqual(trace["markers_ms"]["turn_admitted"], 200.)
        self.assertEqual(trace["timings"]["host_to_first_frame_ms"]["status"], "MEASURED")
        self.assertEqual(trace["timings"]["host_to_mic_ready_ms"]["status"], "UNMEASURED")
        self.assertEqual(trace["timings"]["acoustic_first_sound_ms"]["status"], "UNMEASURED")

    def test_old_or_disabled_wake_queue_is_rejected_without_manual_cancel_semantics(self):
        clock = Clock()
        host, client = self.make_host(clock=clock)
        # Inject an already queued control boundary, not a pretend detector.
        receipt = host.queue_command("expired-wake", HostControl("ptt"), _wake=True)
        clock.now += .6
        host.poll()
        self.assertEqual(host._records[receipt["request_id"]]["result"]["code"], "wake_stale_or_busy")
        host.queue_command("disabled-wake", HostControl("ptt"), _wake=True)
        host.poll()
        self.assertEqual(self.sources, [])
        self.assertEqual(client.requests, [])

    def ready_boundary_host(self):
        factory, detector = SourceFactory(), DetectorBoundary()
        original_snapshot = detector.snapshot
        detector.snapshot = lambda: dict(original_snapshot(), enabled=detector.status == "READY")
        with patch("bmo_ai.features.input.wake_word.LocalWakeDetector", return_value=detector):
            host, client = self.make_host(ptt_source_factory=factory)
        return host, client, factory, detector

    def test_ready_boundary_wake_queues_existing_ptt_and_uses_one_shared_reader(self):
        host, client, factory, detector = self.ready_boundary_host()
        self.assertTrue(host.enable_wake_word())
        eventually(lambda: factory.sources and factory.sources[-1].entered.is_set())
        detector.trigger = True
        physical = factory.sources[-1]
        physical.send(1000)
        eventually(lambda: bool(host._records))
        self.assertFalse(host.session.busy)  # Worker callback only enqueues.
        host.poll()
        eventually(lambda: host._microphone_broker.snapshot()["capture_active"])
        # Wake-specific speech evidence now uses the real local VAD. A DC
        # constant is not speech; retain the one-reader assertion with the
        # licensed public reference after a real quiet PCM boundary instead.
        from bmo_ai.ptt_replay import load_public_reference_pcm16k
        for _ in range(6): physical.send(0)
        reference=load_public_reference_pcm16k(target_rms=.02)
        reference+=bytes((-len(reference))%1280)
        for offset in range(0,len(reference),1280):
            physical.queue.put(reference[offset:offset+1280])
        for _ in range(24): physical.send(0)
        self.finish(host)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(factory.maximum, 1)
        self.assertEqual(len(factory.sources), 1)
        self.assertEqual(host.snapshot()["wake_word"]["real_wake_word"], "NOT_RUN")

    def test_simultaneous_manual_ptt_retires_pending_wake_without_cancelling_capture(self):
        host, client, factory, detector = self.ready_boundary_host()
        self.assertTrue(host.enable_wake_word())
        eventually(lambda: factory.sources and factory.sources[-1].entered.is_set())
        detector.trigger = True
        physical = factory.sources[-1]
        physical.send(1000)
        eventually(lambda: bool(host._records))
        self.assertTrue(host.ptt_button())
        host.poll()
        self.assertEqual(host.session.worker.turn, 1)
        self.assertTrue(host.session.busy)
        self.assertTrue(any(record["result"]["code"] == "wake_stale_or_busy" for record in host._records.values()))
        eventually(lambda: host._microphone_broker.snapshot()["capture_active"])
        for _ in range(8): physical.send(5000)
        for _ in range(24): physical.send(0)
        self.finish(host)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(factory.maximum, 1)

    def test_scheduled_handoff_retains_same_reader_and_preroll_then_closes_before_stt(self):
        host, client, factory, detector = self.ready_boundary_host()
        self.assertTrue(host.enable_wake_word())
        eventually(lambda: factory.sources and factory.sources[-1].entered.is_set())
        physical = factory.sources[-1]
        for _ in range(5): physical.send(100)
        eventually(lambda: detector.feeds == 5)
        capture_waiting, begin_capture, broker_checked = Event(), Event(), Event()
        stt_waiting, finish_stt = Event(), Event()
        original_capture, original_allowed = host.microphone.capture, host._microphone_broker.allowed
        def delayed_capture(*args, **kwargs):
            capture_waiting.set()
            if not begin_capture.wait(1.):
                raise AssertionError("synthetic worker handoff gate timed out")
            return original_capture(*args, **kwargs)
        def observed_allowed():
            allowed = original_allowed()
            if host._ptt_handoff_pending:
                broker_checked.set()
            return allowed
        def delayed_stt(*args, **kwargs):
            stt_waiting.set()
            if not finish_stt.wait(1.):
                raise AssertionError("synthetic STT gate timed out")
            return "A synthetic admitted question."
        host.microphone.capture = delayed_capture
        host._microphone_broker.allowed = observed_allowed
        host._ptt_transcriber = delayed_stt
        try:
            self.assertTrue(host.ptt_button())
            self.assertTrue(capture_waiting.wait(1.))
            self.assertTrue(broker_checked.wait(1.))
            self.assertFalse(host._wake_allowed())
            self.assertTrue(host._broker_allowed())
            self.assertFalse(physical.closed.is_set())
            self.assertEqual(host._microphone_broker.snapshot()["buffered_bytes"], 5 * 1280)
            self.assertEqual(len(factory.sources), 1)
            begin_capture.set()
            eventually(lambda: host._microphone_broker.snapshot()["capture_active"])
            for _ in range(8): physical.send(5000)
            for _ in range(24): physical.send(0)
            self.assertTrue(stt_waiting.wait(1.))
            self.assertFalse(host._ptt_handoff_pending)
            self.assertFalse(host._broker_allowed())
            eventually(lambda: physical.closed.is_set())
            self.assertEqual(len(factory.sources), 1)
            finish_stt.set()
            self.finish(host)
            self.assertEqual(len(client.requests), 1)
            self.assertEqual(factory.maximum, 1)
        finally:
            begin_capture.set(); finish_stt.set()

    def test_handoff_never_overrides_foreign_audio_or_actual_player_start(self):
        host, _ = self.make_host()
        host._wake_armed = True
        host._ptt_handoff_pending = host._ptt_turn_active = True
        lease = host.audio.acquire("foreign-output")
        self.assertFalse(host._broker_allowed())
        host.audio.release(lease)
        lease = host.audio.acquire("conversation")
        host._ptt_lease = lease
        self.assertTrue(host._broker_allowed())
        face = host.session.snapshot()
        face["playback"] = "started"
        with patch.object(host.session, "snapshot", return_value=face):
            self.assertFalse(host._broker_allowed())
        host._ptt_handoff_pending = host._ptt_turn_active = False
        host.audio.release(lease)
        host._ptt_lease = None

    def test_boot_audio_and_foreground_modes_prevent_persistent_mic_open(self):
        host, _, factory, _ = self.ready_boundary_host()
        lease = host.audio.acquire("boot-cache")
        self.assertTrue(host.enable_wake_word())
        eventually(lambda: host._microphone_broker.snapshot()["state"] == "SUPPRESSED")
        self.assertEqual(factory.sources, [])
        self.assertFalse(host._queue_wake())
        self.assertTrue(host.disable_wake_word())
        host.audio.release(lease)
        host._transition("game_menu")
        self.assertFalse(host._wake_allowed())
        host._transition("conversation")
        with patch.object(host.video, "snapshot", return_value={"recording": True}):
            self.assertFalse(host._wake_allowed())

    def test_failed_wake_release_blocks_ptt_and_reports_pending_cleanup(self):
        host, _ = self.make_host()
        with patch.object(host._microphone_broker, "stop_wake", return_value=False):
            self.assertFalse(host.disable_wake_word())
        self.assertFalse(host.ptt_button())
        self.assertTrue(host._ptt_cleanup_fault)

    def test_idle_gaze_is_small_short_lived_and_yields_to_camera_manual_and_native(self):
        clock = Clock()
        host, _ = self.make_host(clock=clock)
        host._idle_gaze = IdleGaze(clock=clock, interval=lambda a, b: a, chooser=lambda values: values[0])
        clock.now += 8.
        host.poll()
        self.assertEqual((host.snapshot()["gaze"]["x"], host.snapshot()["gaze"]["y"]), (-.08, -.025))
        self.assertAlmostEqual(host.gaze._offers["idle"].expires_at, clock.now + 2.)
        clock.now += 2.1
        host.poll()
        self.assertEqual((host.snapshot()["gaze"]["x"], host.snapshot()["gaze"]["y"]), (0., 0.))
        host.gaze.offer("camera", .7, .5, generation=host.modes.generation, expires_at=clock.now + 30.)
        clock.now += 8.
        host.poll()
        self.assertEqual(host.snapshot()["gaze"]["owner"], "camera")
        self.assertNotIn("idle", host.gaze._offers)
        host.gaze.offer("manual", -.5, 0., generation=host.modes.generation, expires_at=clock.now + 30.)
        host.poll()
        self.assertEqual(host.snapshot()["gaze"]["owner"], "manual")
        host.gaze.clear()
        face = host.session.snapshot()
        face["phase"] = "listening"
        with patch.object(host.session, "snapshot", return_value=face):
            host._tick_idle_gaze()
        self.assertNotIn("idle", host.gaze._offers)
        self.assertEqual(host.gaze.resolve(phase="listening", generation=host.modes.generation)["owner"], "native")


if __name__ == "__main__": unittest.main()
