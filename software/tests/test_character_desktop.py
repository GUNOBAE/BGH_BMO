"""Production Desktop/Host/voice owners with private synthetic clips, no devices.

These are software integration checks. They do not approve Aaron listening,
measure speaker latency, or exercise a physical GP8/RP2040/microphone.
"""
from pathlib import Path
import tempfile
from threading import Event, Lock
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmo_ai import audio
from bmo_ai.character_presence import CachedPresence
from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.input.microphone import SyntheticPcmSource
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.greeting import GreetingPlayer
from bmo_ai.greeting_cache import GreetingCache, GreetingSpec
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.voice_bank import BankPlayer, VoiceBank, catalog


class Clock:
    def __init__(self): self.now = 0.
    def __call__(self): return self.now


class Resource:
    _bridge = SimpleNamespace(sha256="a" * 64)
    def poll(self): return {"state": "running"}
    def mark_ready(self): return True
    def close(self): return None


class PlayerProbe:
    def __init__(self):
        self.lock, self.cached_started, self.release = Lock(), Event(), Event()
        self.cached_blocked = False
        self.active = self.maximum = self.cached_calls = self.dialogue_calls = 0

    def player(self, category):
        def play(_command, *, on_start, cancel_check, **_settings):
            with self.lock:
                self.active += 1;self.maximum = max(self.maximum, self.active)
                if category == "cached": self.cached_calls += 1
                else: self.dialogue_calls += 1
            try:
                cancel_check();on_start()
                if category == "cached":
                    self.cached_started.set()
                    deadline = time.monotonic()+3
                    while self.cached_blocked and not self.release.wait(.002):
                        cancel_check()
                        if time.monotonic() > deadline: raise AssertionError("Synthetic cache gate timed out")
                cancel_check()
            finally:
                with self.lock: self.active -= 1
        return play


class CharacterDesktopTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="bmo-character-desktop-")))
        self.apps, self.probes = [], []
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                "bmo_ai.cloud.create_client", "bmo_ai.private_credentials.resolve_credentials",
                "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav",
                "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))

    def tearDown(self):
        for probe in self.probes: probe.release.set()
        for app in reversed(self.apps): app.close()

    def fixture(self, *, blocked_boot=False, presence_mode="off", character_life=True,
                require_face_presented=False):
        clock, probe, config = Clock(), PlayerProbe(), integration_config()
        probe.cached_blocked = blocked_boot;self.probes.append(probe)
        directory = self.root/str(len(self.apps))
        directory.mkdir(mode=0o700)
        bank = VoiceBank(directory/"bank", config, clock=lambda: 10000+clock.now,
                         chooser=lambda values: values[0], simulate=True)
        idle = next(c for c, row in catalog().items() if row["category"] == "idle"
                    and "happy" in row["allowed_emotions"] and "settled" in row["allowed_contexts"])
        raw = synthetic_wav()
        try:
            for clip in ("boot_01", idle):
                record = bank.publish(clip, raw, simulated=True)
                bank.approve(clip, record["sha256"], listening_approved=True)
        finally: audio._discard(raw, suppress=True)
        bank_player = BankPlayer(config, bank, simulate=True, player=probe.player("cached"))
        legacy = GreetingPlayer(config, GreetingCache(directory/"greeting", GreetingSpec.from_config(config)),
                                simulate=True, player=probe.player("cached"))
        presence = CachedPresence(bank, bank_player, legacy)
        client = FeatureSimulatedClient([dict(version=3, speech="A short synthetic answer.",
            emotion="happy", gesture="none", intent=None, reaction="none",
            character=dict(delivery_id="warm", intensity="low", gaze_id="none", source="dialogue"))])
        voice = IntegrationVoice(config, client, simulate=True, expressive=True, character_life=character_life,
                                 player=probe.player("dialogue"), emit=lambda *_a, **_kw: None)
        sources, transcriptions = [], []
        def source():
            sources.append(1);return SyntheticPcmSource(clock=clock)
        def transcribe(*_a, **_kw): transcriptions.append(1);return "A synthetic GP8 question."
        host = FeatureHost(config=config, client=client, voice=voice, clock=clock,
            runtime=RuntimePolicy(simulate=True, conversation_ptt_microphone=True),
            ptt_source_factory=source, ptt_transcriber=transcribe,
            character_life=character_life, expressive_voice=True)
        app = DesktopRuntime(clock=clock, simulate=True, character_life=character_life,
                             presence_mode=presence_mode, require_face_presented=require_face_presented)
        self.apps.append(app)
        app.set_server(Resource());app.set_browser(Resource());app.set_host(host, presence)
        app.queue_desktop_control("characterReady", {"action": "face_ready", "value": {
            "run_id": app.run_id, "screen_sha256": "a"*64, "frame_version": "character-offline-v1"}})
        return SimpleNamespace(app=app, host=host, client=client, bank=bank, clock=clock,
                               probe=probe, sources=sources, transcriptions=transcriptions)

    def pump(self, fixture, predicate, *, timeout=4):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            fixture.app.heartbeat();fixture.app.poll()
            if predicate(): return
            time.sleep(.002)
        self.fail("The production character desktop did not reach its software boundary")

    def ready(self, fixture):
        self.pump(fixture, lambda: fixture.app._worker is None
                  and fixture.app.startup_snapshot()["phase"] == "ready")

    def finish_turn(self, fixture):
        self.pump(fixture, lambda: fixture.app._worker is None and not fixture.host.session.busy
                  and fixture.host.audio.owner is None)

    def test_cached_boot_gp8_double_edge_preempts_once_without_player_or_microphone_overlap(self):
        fixture = self.fixture(blocked_boot=True)
        self.pump(fixture, fixture.probe.cached_started.is_set)
        self.assertEqual(fixture.host.audio.owner, "greeting")
        first = fixture.app.queue_command("gp8First", HostControl("ptt"))
        fixture.app.queue_command("gp8Duplicate", HostControl("ptt"))
        self.assertEqual(first["status"], "accepted")
        self.pump(fixture, lambda: len(fixture.client.requests) == 1)
        self.finish_turn(fixture)
        self.assertEqual((len(fixture.sources), len(fixture.transcriptions), len(fixture.client.requests)), (1, 1, 1))
        self.assertEqual(fixture.probe.maximum, 1)
        self.assertEqual(fixture.probe.cached_calls, 1)
        self.assertEqual(fixture.probe.dialogue_calls, 1)
        for _ in range(3): fixture.app.poll()
        self.assertEqual(fixture.probe.cached_calls, 1)

    def test_gp8_before_face_presentation_suppresses_pending_boot_and_preserves_the_question(self):
        fixture = self.fixture(require_face_presented=True)
        fixture.app.heartbeat();fixture.app.poll()
        self.assertTrue(fixture.app._ready);self.assertIsNone(fixture.app._worker)
        fixture.app.queue_command("gp8BeforePresentation", HostControl("ptt"))
        fixture.app.queue_desktop_control("presentAfterGp8", {
            "action": "face_presented", "value": {"run_id": fixture.app.run_id}})
        self.pump(fixture, lambda: len(fixture.client.requests) == 1);self.finish_turn(fixture)
        self.assertEqual((len(fixture.sources), len(fixture.transcriptions)), (1, 1))
        self.assertEqual(fixture.probe.cached_calls, 0)
        self.assertEqual(fixture.probe.maximum, 1)

    def test_question_interrupts_boot_and_never_automatically_restarts_greeting(self):
        fixture = self.fixture(blocked_boot=True)
        self.pump(fixture, fixture.probe.cached_started.is_set)
        fixture.app.queue_command("firstQuestion", HostControl("question", "A synthetic priority question."))
        self.pump(fixture, lambda: len(fixture.client.requests) == 1)
        self.finish_turn(fixture)
        self.assertEqual(fixture.app.startup_snapshot()["greeting_attempts"], 1)
        self.assertEqual(fixture.probe.cached_calls, 1)
        self.assertEqual(fixture.probe.maximum, 1)
        self.assertEqual(fixture.host.session.brain.history[-1]["content"], "A short synthetic answer.")

    def test_explicit_boot_cancel_leaves_no_dialogue_cache_or_history_and_no_auto_restart(self):
        fixture = self.fixture(blocked_boot=True)
        self.pump(fixture, fixture.probe.cached_started.is_set)
        fixture.app.queue_command("cancelBoot", HostControl("cancel"))
        self.ready(fixture)
        for _ in range(3): fixture.app.poll()
        self.assertEqual(fixture.probe.cached_calls, 1)
        self.assertEqual(fixture.probe.dialogue_calls, 0)
        self.assertFalse(fixture.host.session.voice.has_cache)
        self.assertEqual(fixture.host.session.brain.history, [])
        self.assertEqual(len(fixture.client.requests), 0)
        self.assertEqual(fixture.probe.maximum, 1)

    def test_idle_overlay_restores_prior_face_and_does_not_replace_answer_history_or_repeat_wav(self):
        fixture = self.fixture(presence_mode="normal")
        self.ready(fixture)
        fixture.app.queue_command("seedAnswer", HostControl("question", "A synthetic good-news question."))
        self.pump(fixture, lambda: len(fixture.client.requests) == 1);self.finish_turn(fixture)
        history = [dict(row) for row in fixture.host.session.brain.history]
        cache = dict(fixture.host.session.voice._cache)
        prior = fixture.host.session.snapshot()
        fixture.probe.cached_started.clear();fixture.probe.cached_blocked = True
        fixture.app._presence.interval = lambda low, _high: low
        fixture.app._presence.set_mode("normal")
        fixture.clock.now += 180
        self.pump(fixture, lambda: fixture.app.face_snapshot().get("source") == "cached_idle"
                  and fixture.app.face_snapshot().get("playback") == "started")
        self.assertEqual(fixture.host.session.snapshot()["emotion"], prior["emotion"])
        self.assertEqual(fixture.host.session.brain.history, history)
        self.assertEqual(fixture.host.session.voice._cache, cache)
        fixture.probe.release.set();self.ready(fixture)
        self.assertEqual(fixture.app.face_snapshot()["emotion"], prior["emotion"])
        self.assertEqual(fixture.app._presence.snapshot()["heard_in_15_minutes"], 1)
        fixture.app.queue_command("repeatAfterIdle", HostControl("repeat"))
        self.finish_turn(fixture)
        self.assertEqual(fixture.host.session.brain.history, history)
        self.assertEqual(fixture.host.session.voice._cache, cache)
        self.assertEqual(len(fixture.client.requests), 1)
        self.assertEqual(fixture.probe.maximum, 1)

    def test_gp8_interrupts_idle_clip_and_starts_exactly_one_existing_microphone_turn(self):
        fixture = self.fixture(presence_mode="normal");self.ready(fixture)
        fixture.host.session.set_emotion("happy");fixture.host.session.set_emotion("auto")
        fixture.probe.cached_started.clear();fixture.probe.cached_blocked = True
        fixture.app._presence.interval = lambda low, _high: low
        fixture.app._presence.set_mode("normal");fixture.clock.now += 180
        self.pump(fixture, lambda: fixture.app.face_snapshot().get("source") == "cached_idle"
                  and fixture.app.face_snapshot().get("playback") == "started")
        fixture.app.queue_command("gp8DuringIdle", HostControl("ptt"))
        self.pump(fixture, lambda: len(fixture.client.requests) == 1);self.finish_turn(fixture)
        self.assertEqual((len(fixture.sources), len(fixture.transcriptions), len(fixture.client.requests)), (1, 1, 1))
        self.assertEqual(fixture.app._presence.snapshot()["heard_in_15_minutes"], 0)
        self.assertEqual(fixture.probe.maximum, 1)

    def test_idle_timeout_joins_owned_player_then_restores_ready_and_gp8_conversation(self):
        fixture = self.fixture(presence_mode="normal");self.ready(fixture)
        fixture.app.queue_command("beforeIdleTimeout", HostControl("question", "A synthetic happy question."))
        self.pump(fixture, lambda: len(fixture.client.requests) == 1);self.finish_turn(fixture)
        prior_cache = dict(fixture.host.session.voice._cache)
        fixture.probe.cached_started.clear();fixture.probe.cached_blocked = True
        fixture.app._presence.interval = lambda low, _high: low
        fixture.app._presence.set_mode("normal");fixture.clock.now += 180
        self.pump(fixture, lambda: fixture.app.face_snapshot().get("source") == "cached_idle"
                  and fixture.app.face_snapshot().get("playback") == "started")
        fixture.clock.now += fixture.app.greeting_timeout + 1
        self.pump(fixture, lambda: fixture.app._worker is None
                  and fixture.app.startup_snapshot()["phase"] == "ready")
        self.assertEqual(fixture.app.startup_snapshot()["character_life"]["last_idle_code"], "greeting_timeout")
        self.assertIsNone(fixture.host.audio.owner)
        self.assertEqual(fixture.host.session.voice._cache, prior_cache)
        fixture.app.queue_command("gp8AfterIdleTimeout", HostControl("ptt"))
        self.pump(fixture, lambda: len(fixture.client.requests) == 2);self.finish_turn(fixture)
        self.assertEqual((len(fixture.sources), len(fixture.transcriptions)), (1, 1))
        self.assertEqual(fixture.probe.maximum, 1)

    def test_quiet_mode_and_default_off_keep_cached_boot_but_never_start_idle_speech(self):
        for mode in ("off", "quiet", "sleep"):
            with self.subTest(mode=mode):
                fixture = self.fixture(presence_mode=mode);self.ready(fixture)
                fixture.clock.now += 5000
                fixture.app.heartbeat();fixture.app.poll()
                self.assertEqual(fixture.probe.cached_calls, 1)
                state = fixture.app.startup_snapshot()["character_life"]
                self.assertTrue(state["enabled"])
                self.assertEqual(state["presence"]["mode"], mode)
                self.assertEqual(state["voice_bank"]["approved"], 0)  # Synthetic-only assets remain explicitly marked.

    def test_failed_idle_focus_or_worker_admission_releases_pending_scheduler_without_charging(self):
        from bmo_ai.features.contracts import FeatureError
        for boundary in ("focus", "thread"):
            with self.subTest(boundary=boundary):
                fixture = self.fixture(presence_mode="normal");self.ready(fixture)
                fixture.host.session.set_emotion("happy");fixture.host.session.set_emotion("auto")
                fixture.app._presence.interval = lambda low, _high: low
                fixture.app._presence.set_mode("normal");fixture.clock.now += 180
                target = (patch.object(fixture.host.audio, "acquire", side_effect=FeatureError("audio_busy"))
                    if boundary == "focus" else patch("bmo_ai.desktop_runtime.Thread.start",
                                                      side_effect=RuntimeError("Synthetic admission failure")))
                with target:
                    fixture.app.heartbeat();fixture.app.poll()
                self.assertIsNone(fixture.app._worker)
                self.assertIsNone(fixture.host.audio.owner)
                state = fixture.app._presence.snapshot()
                self.assertFalse(state["pending"]);self.assertEqual(state["heard_in_15_minutes"], 0)

    def test_baseline_without_character_life_keeps_rejecting_input_during_greeting(self):
        fixture = self.fixture(blocked_boot=True, character_life=False)
        self.pump(fixture, fixture.probe.cached_started.is_set)
        from bmo_ai.features.contracts import FeatureError
        with self.assertRaises(FeatureError):
            fixture.app.queue_command("baselineBusy", HostControl("question", "A synthetic question."))
        self.assertEqual(len(fixture.client.requests), 0)
        fixture.probe.release.set();self.ready(fixture)


if __name__ == "__main__": unittest.main()
