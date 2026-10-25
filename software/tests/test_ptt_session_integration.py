"""Same-worker PTT integration: synthetic PCM, fake SDK, silent injected output.

These tests execute the actual audio.transcribe implementation against a fake
SDK endpoint, not a paid service. No device, external process or network opens.
"""
import json
from pathlib import Path
import stat
import struct
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.input.microphone import PcmFrame
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ControlledSource:
    sample_rate = 16000
    simulated = True

    def __init__(self, clock, patterns, *, entered=None, release=None, block_at=None, failure=None,
                 close_result=True):
        self.clock, self.patterns = clock, patterns
        self.entered, self.release, self.block_at = entered, release, block_at
        self.failure, self.closed, self.frames_seen = failure, 0, 0
        self.close_result = close_result

    def frames(self, capture_id, *, generation, cancel_check):
        if self.failure is not None:
            raise self.failure
        index = 0
        for amplitude, count in self.patterns:
            for _ in range(count):
                cancel_check()
                if self.block_at == index:
                    self.entered.set()
                    deadline = time.monotonic() + 4
                    while not self.release.wait(.005):
                        cancel_check()
                        if time.monotonic() > deadline:
                            raise AssertionError("synthetic source gate timed out")
                    cancel_check()
                self.clock.now += .04
                self.frames_seen += 1
                yield PcmFrame(struct.pack("<h", amplitude) * 640, index, self.clock(), generation)
                index += 1

    def close(self):
        self.closed += 1
        return self.close_result


class PttSessionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.hosts, self.releases = [], []
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("socket.socket.connect_ex", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("real process forbidden")))
        for target in ("bmo_ai.audio.record_wav", "bmo_ai.fish_tts.synthesize_wav",
                       "bmo_ai.cloud.create_client", "bmo_ai.private_credentials.resolve_credentials"):
            self.enterContext(patch(target, side_effect=AssertionError("real IO forbidden")))

    def tearDown(self):
        for release in self.releases:
            release.set()
        for host in self.hosts:
            if not host._closed:
                host.cancel_conversation()
                self.finish(host)
                host.close()

    def gate(self):
        entered, release = Event(), Event()
        self.releases.append(release)
        return entered, release

    def host(self, *, patterns=None, simulate=False, enabled=True, source_settings=None,
             stt_callback=None, synth_callback=None, player_callback=None, replies=(), demo_assets=False):
        clock = Clock()
        client = FeatureSimulatedClient(replies=replies)
        trace = SimpleNamespace(sources=[], stt=[], synthesis=[], players=[], stt_paths=[], clock=clock)
        patterns = patterns if patterns is not None else [(0, 10), (10000, 8), (0, 24)]

        def source_factory():
            source = ControlledSource(clock, patterns, **(source_settings or {}))
            trace.sources.append(source)
            return source

        def stt(*, file, **options):
            path = Path(file.name)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with wave.open(file, "rb") as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
                self.assertGreater(wav.getnframes(), 0)
            trace.stt.append(options)
            trace.stt_paths.append(path)
            if stt_callback is not None:
                return stt_callback(file=file, **options)
            return SimpleNamespace(text="Synthetic spoken question.")

        client.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=stt))
        config = integration_config(language="ko")

        def synth(*args, **options):
            trace.synthesis.append((args, options))
            if synth_callback is not None:
                return synth_callback(*args, **options)
            return synthetic_wav()

        def player(command, **options):
            options["cancel_check"]()
            trace.players.append(tuple(command))
            options["on_start"]()
            if player_callback is not None:
                player_callback(command, **options)
            options["cancel_check"]()

        # Production voice branch with injected generated synthesis/player.
        # This verifies single-output ownership without running Fish/ALSA.
        voice = IntegrationVoice(config, client, emit=lambda *a, **k: None,
                                 simulate=False, fish_synthesizer=synth, player=player)
        settings = {"ptt_source_factory": source_factory} if enabled else {}
        host = FeatureHost(config=config, client=client, voice=voice, clock=clock,
            runtime=RuntimePolicy(simulate=simulate, conversation_ptt_microphone=enabled),
            demo_assets=demo_assets, **settings)
        self.hosts.append(host)
        return host, trace

    def pump_until(self, host, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            host.heartbeat()
            host.poll()
            time.sleep(.002)
        self.assertTrue(predicate(), "Synthetic integration condition timed out")

    def finish(self, host):
        self.pump_until(host, lambda: not host.session.busy)
        host.heartbeat(); host.poll()

    def turn(self, host):
        self.assertTrue(host.ptt_button())
        self.finish(host)

    def test_idle_default_disabled_and_opt_in_idle_never_open_source_or_sdk(self):
        for enabled in (False, True):
            host, trace = self.host(enabled=enabled)
            for _ in range(3):
                host.heartbeat(); host.poll()
            self.assertEqual(trace.sources, [])
            self.assertEqual(trace.stt, [])
            self.assertEqual(host.client.requests, [])
            self.assertEqual(trace.synthesis, [])
            self.assertFalse(host.microphone.snapshot()["automatic_capture"] if enabled else False)
            self.assertFalse(host.snapshot()["wake_word"]["enabled"])
            if not enabled:
                self.assertFalse(host.ptt_button())

    def test_live_opt_in_fake_pcm_uses_one_stt_same_brain_voice_and_cache(self):
        host, trace = self.host()
        session, brain, worker, voice = host.session, host.session.brain, host.session.worker, host.session.voice
        self.turn(host)
        self.assertIs(host.session, session); self.assertIs(session.brain, brain)
        self.assertIs(session.worker, worker); self.assertIs(session.voice, voice)
        self.assertEqual(len(trace.sources), 1)
        self.assertEqual(trace.sources[0].closed, 1)
        self.assertEqual(trace.stt, [{"model": "gpt-4o-mini-transcribe", "response_format": "json", "language": "ko"}])
        self.assertEqual(len(host.client.requests), 1)
        self.assertEqual(len(trace.synthesis), 1); self.assertEqual(len(trace.players), 1)
        self.assertEqual(len(brain.history), 2)
        self.assertTrue(voice.has_cache)
        self.assertFalse(any(path.exists() for path in trace.stt_paths))
        self.assertIsNone(host.audio.owner)
        self.assertEqual(session.snapshot()["emotion"], "happy")
        self.assertEqual(session.snapshot()["phase"], "idle")
        self.assertEqual(trace.synthesis[0][1]["model"], "s2.1-pro-free")
        self.assertEqual(host.config.fish_gain, .25)
        self.assertEqual(host.config.fish_name_mode, "beemo")
        self.assertEqual(host.config.fish_delivery, "soft")

    def test_two_valid_turns_preserve_same_dialogue_context_and_replace_one_cache(self):
        host, trace = self.host()
        self.turn(host)
        first = host.session.voice._cache["path"]
        self.assertTrue(first.exists())
        self.turn(host)
        self.assertEqual(len(trace.stt), 2)
        self.assertEqual(len(host.client.requests), 2)
        self.assertEqual([len(r["input"]) for r in host.client.requests], [1, 3])
        self.assertEqual(len(host.session.brain.history), 4)
        self.assertFalse(first.exists())
        self.assertTrue(host.session.voice._cache["path"].exists())

    def test_simulate_opt_in_never_uses_stt_sdk_even_with_valid_pcm(self):
        host, trace = self.host(simulate=True)
        self.turn(host)
        self.assertEqual(len(trace.sources), 1)
        self.assertEqual(trace.stt, [])
        self.assertEqual(len(host.client.requests), 1)
        self.assertEqual(len(trace.players), 1)

    def test_no_speech_returns_idle_with_zero_stt_brain_synthesis_or_cache(self):
        host, trace = self.host(patterns=[(0, 100)])
        self.turn(host)
        self.assertEqual(trace.stt, []); self.assertEqual(host.client.requests, [])
        self.assertEqual(trace.synthesis, []); self.assertEqual(trace.players, [])
        self.assertEqual(host.session.brain.history, [])
        self.assertFalse(host.session.voice.has_cache)
        self.assertEqual(host.session.snapshot()["phase"], "idle")
        self.assertEqual(host.microphone.snapshot()["reason_code"], "no_speech")
        self.assertIsNone(host.audio.owner)

    def test_short_noise_bursts_never_reach_stt(self):
        host, trace = self.host(patterns=[(0, 10), (20000, 3), (0, 3), (20000, 3), (0, 100)])
        self.turn(host)
        self.assertEqual(trace.stt, []); self.assertEqual(host.client.requests, [])
        self.assertEqual(trace.players, [])

    def test_noisy_calibration_rejects_input_and_releases_conversation_focus(self):
        host, trace = self.host(patterns=[(20000, 100)])
        self.turn(host)
        self.assertEqual(host.microphone.snapshot()["reason_code"], "calibration_noisy")
        self.assertEqual(trace.stt, []); self.assertEqual(trace.players, [])
        self.assertIsNone(host.audio.owner)
        self.assertFalse(host.microphone.snapshot()["microphone_open"])

    def test_duplicate_press_within_debounce_never_finishes_or_starts_second_capture(self):
        entered, release = self.gate()
        host, trace = self.host(source_settings={"entered": entered, "release": release, "block_at": 0})
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        for _ in range(10):
            self.assertTrue(host.ptt_button())
        self.assertEqual(len(trace.sources), 1)
        self.assertFalse(host.microphone._finish.is_set())
        release.set(); self.finish(host)
        self.assertEqual(len(trace.stt), 1)

    def test_second_intentional_press_finishes_valid_speech_once(self):
        entered, release = self.gate()
        host, trace = self.host(patterns=[(0, 10), (10000, 100)],
            source_settings={"entered": entered, "release": release, "block_at": 18})
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        self.assertEqual(host.microphone.snapshot()["state"], "speech")
        self.assertTrue(host.ptt_button())
        release.set(); self.finish(host)
        self.assertEqual(host.microphone.snapshot()["reason_code"], "transcribed")
        self.assertEqual(len(trace.sources), 1); self.assertEqual(len(trace.stt), 1)
        self.assertEqual(len(trace.players), 1)

    def test_external_stop_during_capture_discards_partial_without_stt(self):
        entered, release = self.gate()
        host, trace = self.host(source_settings={"entered": entered, "release": release, "block_at": 18})
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        host.cancel_conversation(); release.set(); self.finish(host)
        self.assertEqual(trace.stt, []); self.assertEqual(trace.players, [])
        self.assertEqual(host.session.brain.history, [])
        self.assertIsNone(host.audio.owner)
        self.assertEqual(trace.sources[0].closed, 1)
        self.assertFalse(host.microphone.snapshot()["microphone_open"])

    def test_late_stt_after_cancel_is_not_sent_to_brain_and_wav_is_removed(self):
        entered, release = self.gate()
        def late(**options):
            entered.set()
            self.assertTrue(release.wait(3))
            return SimpleNamespace(text="LATE_SYNTHETIC_TRANSCRIPT")
        host, trace = self.host(stt_callback=late)
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        self.assertEqual(host.session.snapshot()["phase"], "listening")
        host.cancel_conversation()
        self.assertTrue(host.session.busy)
        self.assertEqual(host.audio.owner, "conversation")
        self.assertTrue(host.ptt_button())  # cancellation, never automatic capture
        self.assertEqual(len(trace.sources), 1)
        release.set(); self.finish(host)
        self.assertEqual(host.client.requests, [])
        self.assertEqual(trace.players, []); self.assertEqual(trace.synthesis, [])
        self.assertFalse(any(path.exists() for path in trace.stt_paths))
        self.assertEqual(host.session.brain.history, [])
        self.assertIsNone(host.audio.owner)

    def test_stt_error_is_safe_and_next_explicit_turn_recovers(self):
        attempt = [0]
        def fail_once(**options):
            attempt[0] += 1
            if attempt[0] == 1:
                raise RuntimeError("PRIVATE_FIXTURE_PROVIDER_DETAIL")
            return SimpleNamespace(text="Recovered synthetic turn")
        host, trace = self.host(stt_callback=fail_once)
        self.turn(host)
        self.assertEqual(host.client.requests, [])
        self.assertFalse(any(path.exists() for path in trace.stt_paths))
        self.assertNotIn("PRIVATE_FIXTURE", json.dumps(host.snapshot()))
        self.turn(host)
        self.assertEqual(len(trace.stt), 2)
        self.assertEqual(len(host.client.requests), 1)
        self.assertEqual(len(trace.players), 1)

    def test_full_normal_command_queue_does_not_block_reserved_stop(self):
        entered, release = self.gate()
        host, trace = self.host(source_settings={"entered": entered, "release": release, "block_at": 18})
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        for index in range(32):
            host.queue_command(f"queued_{index}", HostControl("question", "must stay retired"))
        with self.assertRaises(FeatureError):
            host.queue_command("overflow", HostControl("question", "overflow"))
        receipt = host.queue_command("priority_stop", HostControl("stop"))
        self.assertEqual(receipt["status"], "accepted")
        host.poll(); release.set(); self.finish(host)
        self.assertEqual(len(trace.sources), 1)
        self.assertEqual(trace.stt, []); self.assertEqual(host.client.requests, [])
        self.assertIsNone(host.audio.owner)
        safety = [item for item in host.snapshot()["requests"] if item["request_id"] == receipt["request_id"]]
        self.assertEqual(safety[0]["code"], "resources_stopped")

    def test_mode_change_invalidates_capture_then_menu_can_open_after_cleanup(self):
        entered, release = self.gate()
        host, trace = self.host(source_settings={"entered": entered, "release": release, "block_at": 18})
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        result = host.execute(HostIntent("open_game_menu"))
        self.assertEqual(result.status, "rejected")
        release.set(); self.finish(host)
        self.assertEqual(trace.stt, [])
        self.assertEqual(host.execute(HostIntent("open_game_menu")).status, "simulated")
        self.assertEqual(host.modes.mode, "game_menu")
        self.assertFalse(host.ptt_button())
        self.assertEqual(len(trace.sources), 1)

    def test_game_menu_and_active_game_keep_talk_disabled(self):
        host, trace = self.host(simulate=True, demo_assets=True)
        host.execute(HostIntent("open_game_menu"))
        self.assertFalse(host.ptt_button())
        self.assertFalse(host.inputs.route("TALK"))
        outcome = host.inputs.route("A")
        self.assertTrue(outcome.simulated)
        self.assertEqual(host.modes.mode, "game")
        self.assertFalse(host.ptt_button())
        host.inputs.route("B")
        self.assertEqual(host.modes.mode, "game")
        self.assertIn(("B", True), host.games._runner.inputs)
        self.assertEqual(trace.sources, []); self.assertEqual(trace.stt, [])
        host.execute(HostIntent("return_to_face"))
        self.turn(host)
        self.assertEqual(len(trace.sources), 1)

    def test_other_audio_focus_owner_blocks_source_without_preemption(self):
        host, trace = self.host()
        stop = Mock(return_value=True)
        lease = host.audio.acquire("music", stop=stop)
        self.assertFalse(host.ptt_button())
        self.assertEqual(trace.sources, []); self.assertEqual(trace.stt, [])
        self.assertIs(host.audio.lease, lease)
        stop.assert_not_called()
        host.audio.release(lease)

    def test_emotion_remains_during_listening_thinking_and_changes_only_at_output_start(self):
        capture_entered, capture_release = self.gate()
        synth_entered, synth_release = self.gate()
        def synth(*args, **options):
            synth_entered.set()
            self.assertTrue(synth_release.wait(3))
            return synthetic_wav()
        host, trace = self.host(source_settings={"entered": capture_entered, "release": capture_release, "block_at": 0},
                                synth_callback=synth)
        host.session.set_emotion("sad"); host.session.set_emotion("auto")
        self.assertTrue(host.ptt_button()); self.assertTrue(capture_entered.wait(1))
        self.assertEqual(host.session.snapshot()["emotion"], "sad")
        self.assertEqual(host.session.snapshot()["phase"], "listening")
        capture_release.set(); self.assertTrue(synth_entered.wait(1))
        self.assertEqual(host.session.snapshot()["emotion"], "sad")
        self.assertEqual(host.session.snapshot()["phase"], "thinking")
        synth_release.set(); self.finish(host)
        self.assertEqual(host.session.snapshot()["emotion"], "happy")
        self.assertEqual(host.session.snapshot()["phase"], "idle")
        self.assertTrue(all(item["emotion"] == "sad" for item in host.session.events
                            if item["kind"] == "prepared"))

    def test_repeat_after_ptt_uses_same_cached_expression_without_stt_or_resynthesis(self):
        host, trace = self.host()
        self.turn(host)
        cached = host.session.voice._cache["path"]
        history = list(host.session.brain.history)
        before = (len(trace.sources), len(trace.stt), len(trace.synthesis), len(host.client.requests))
        result = host.control(HostControl("repeat"), "repeat_ptt")
        self.assertEqual(result.code, "repeat_started")
        self.finish(host)
        self.assertEqual((len(trace.sources), len(trace.stt), len(trace.synthesis), len(host.client.requests)), before)
        self.assertEqual(len(trace.players), 2)
        self.assertIs(host.session.voice._cache["path"], cached)
        self.assertEqual(host.session.brain.history, history)
        self.assertEqual(host.session.snapshot()["emotion"], "happy")

    def test_ptt_during_speaking_cancels_output_without_recording_again(self):
        entered, release = self.gate()
        def player(command, **options):
            entered.set()
            while not release.wait(.005):
                options["cancel_check"]()
        host, trace = self.host(player_callback=player)
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        self.assertEqual(host.session.snapshot()["playback"], "started")
        self.assertTrue(host.ptt_button()); release.set(); self.finish(host)
        self.assertEqual(len(trace.sources), 1)
        self.assertEqual(len(trace.stt), 1)
        self.assertEqual(len(trace.players), 1)
        self.assertFalse(host.session.voice.has_cache)
        self.assertEqual(host.session.snapshot()["playback"], "cancelled")

    def test_app_close_cancels_blocked_source_reaps_worker_and_leaves_no_audio_files(self):
        entered, release = self.gate()
        host, trace = self.host(source_settings={"entered": entered, "release": release, "block_at": 18})
        self.assertTrue(host.ptt_button()); self.assertTrue(entered.wait(1))
        host.close()
        self.assertTrue(host._closed)
        self.assertFalse(host.session.worker.worker_alive)
        self.assertFalse(host.microphone.snapshot()["microphone_open"])
        self.assertEqual(trace.sources[0].closed, 1)
        self.assertEqual(trace.stt, []); self.assertEqual(trace.players, [])
        self.assertFalse(host.ptt_button())

    def test_unconfirmed_source_cleanup_latches_exact_lease_and_blocks_new_audio(self):
        host, trace = self.host(source_settings={"close_result": False})
        self.turn(host)
        self.assertTrue(host.microphone.snapshot()["cleanup_pending"])
        self.assertTrue(host._ptt_cleanup_fault)
        self.assertEqual(host.audio.owner, "conversation")
        self.assertIs(host.audio.lease, host._ptt_lease)
        self.assertFalse(host.ptt_button())
        self.assertFalse(host.start_question("must not run"))
        self.assertFalse(host.stop())
        repeat = host.control(HostControl("repeat"), "repeat_after_fault")
        self.assertEqual((repeat.status, repeat.code), ("rejected", "pcm_cleanup_failed"))
        self.assertEqual(len(trace.sources), 1)
        self.assertEqual(trace.stt, []); self.assertEqual(trace.players, [])
        # Only confirmed cleanup during explicit shutdown releases the lease.
        trace.sources[0].close_result = True
        host.close()
        self.assertIsNone(host.audio.owner)
        self.assertFalse(host.microphone.snapshot()["cleanup_pending"])

    def test_initial_ptt_receipt_is_finished_without_duplicate_edge_replacing_it(self):
        entered, release = self.gate()
        host, trace = self.host(source_settings={"entered": entered, "release": release, "block_at": 0})
        first = host.control(HostControl("ptt"), "ptt_initial")
        self.assertEqual((first.status, first.code), ("running", "ptt_started"))
        self.assertTrue(entered.wait(1))
        duplicate = host.control(HostControl("ptt"), "ptt_duplicate")
        self.assertEqual(duplicate.code, "ptt_input_accepted")
        self.assertEqual(host._voice_control_request, "ptt_initial")
        release.set(); self.finish(host)
        self.assertEqual(host._result_history["ptt_initial"].code, "voice_completed")
        self.assertEqual(host._result_history["ptt_initial"].status, "succeeded")
        self.assertEqual(host._result_history["ptt_duplicate"].status, "accepted")
        self.assertEqual(len(trace.sources), 1); self.assertEqual(len(trace.stt), 1)


if __name__ == "__main__":
    unittest.main()
