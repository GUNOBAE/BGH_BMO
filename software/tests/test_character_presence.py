"""Deterministic local presence checks; no devices, synthesis or API calls."""
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from bmo_ai import audio
from bmo_ai.character_presence import CachedPresence, IdleGaze, PresenceError, PresenceScheduler
from bmo_ai.conversation import TurnCancelled
from bmo_ai.greeting import GreetingPlayer
from bmo_ai.greeting_cache import GreetingCache, GreetingSpec
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import synthetic_wav
from bmo_ai.voice_bank import BankPlayer, VoiceBank, VoiceBankError, catalog


class Clock:
    def __init__(self): self.now = 0.
    def __call__(self): return self.now


class FakeBank:
    def __init__(self): self.calls = [];self.clips = {"boot": "boot_01", "idle": "idle_01"}
    def select(self, category, **context):
        self.calls.append((category, context));return self.clips.get(category)


class FakePlayer:
    def __init__(self):
        self.calls = [];self.cancelled = False;self.closed = False
        self.fail = None;self.hook = None
    def play(self, clip_id, *, emit, cancel_check, on_playback_start=None):
        self.calls.append(clip_id);cancel_check()
        if self.fail == "before": raise VoiceBankError("voice_bank_asset_invalid")
        if on_playback_start is not None: on_playback_start()
        emit("started", source="voice_bank")
        if self.hook is not None: self.hook(cancel_check)
        cancel_check()
        if self.fail == "after": raise VoiceBankError("voice_bank_asset_invalid")
        emit("ended", source="voice_bank")
        return {"completed": True, "clip_id": clip_id}
    def cancel(self): self.cancelled = True
    def close(self): self.closed = True;return True


class FakeGreeting(FakePlayer):
    def __init__(self): super().__init__();self.chimes = 0
    def play_chime(self, *, cancel_check):
        cancel_check()
        if not self.chimes: self.chimes += 1
    def play(self, *, emit, cancel_check, on_playback_start=None, **_):
        self.play_chime(cancel_check=cancel_check)
        return super().play("legacy-greeting", emit=emit, cancel_check=cancel_check,
                            on_playback_start=on_playback_start)


class PresenceSchedulerTests(unittest.TestCase):
    def setUp(self): self.clock = Clock()
    def scheduler(self, mode="normal"):
        return PresenceScheduler(clock=self.clock, interval=lambda low, _high: low, mode=mode)

    def test_default_off_and_missing_clips_do_not_consume_heard_quota(self):
        default = PresenceScheduler(clock=self.clock)
        self.clock.now = 10000
        self.assertFalse(default.due());self.assertFalse(default.snapshot()["enabled"])
        value = self.scheduler()
        self.clock.now += 180
        self.assertTrue(value.due());self.assertFalse(value.due())
        value.completed(False)
        self.assertEqual(value.snapshot()["heard_in_15_minutes"], 0)
        self.clock.now += 180
        self.assertTrue(value.due())

    def test_at_most_two_successful_clips_in_rolling_fifteen_minutes(self):
        value = self.scheduler()
        for now in (180, 360):
            self.clock.now = now;self.assertTrue(value.due());value.completed(True)
        for now in (540, 1000, 1079):
            self.clock.now = now;self.assertFalse(value.due())
        self.clock.now = 1080
        self.assertTrue(value.due());value.completed(True)
        self.assertEqual(value.snapshot()["heard_in_15_minutes"], 2)
        value.set_mode("quiet");value.set_mode("normal")
        self.clock.now += 180
        self.assertTrue(value.due())  # The second old event has now aged out.

    def test_modes_strong_emotions_and_all_foreground_owners_suppress_voice(self):
        cases = [dict(phase="listening"), dict(phase="thinking"), dict(phase="speaking"),
            dict(emotion="angry"), dict(emotion="crying"), dict(conversation_busy=True),
            dict(audio_busy=True), dict(game_active=True), dict(video_active=True),
            dict(activity_mode="game_menu"), dict(activity_mode="photo"), dict(activity_mode="music")]
        for settings in cases:
            with self.subTest(settings=settings):
                value = self.scheduler();self.clock.now += 421
                self.assertFalse(value.due(**settings))
                self.assertFalse(value.due())  # A fresh quiet interval is needed.
        for mode in ("quiet", "sleep", "off"):
            value = self.scheduler(mode);self.clock.now += 421
            self.assertFalse(value.due())

    def test_recent_distress_and_user_input_reset_idle_without_counting_a_clip(self):
        value = self.scheduler();self.clock.now = 180
        self.assertTrue(value.due());value.cancel();value.completed(True)
        self.assertEqual(value.snapshot()["heard_in_15_minutes"], 0)
        value.note_distress();self.clock.now = 1079
        self.assertFalse(value.due());self.assertTrue(value.snapshot()["recent_distress"])
        self.clock.now = 1260
        self.assertTrue(value.due());value.completed(False)
        self.clock.now = 1500
        self.assertFalse(value.due(sensitive=True));self.assertTrue(value.snapshot()["recent_distress"])

    def test_invalid_delays_modes_and_boolean_inputs_fail_safely(self):
        for delay in (179, 421, float("nan"), True):
            with self.assertRaises(PresenceError): PresenceScheduler(interval=lambda *_: delay)
        value = self.scheduler()
        with self.assertRaises(PresenceError): value.set_mode("noisy")
        with self.assertRaises(PresenceError): value.due(audio_busy="false")
        with self.assertRaises(PresenceError): value.completed(1)


class IdleGazeTests(unittest.TestCase):
    def test_small_deterministic_targets_wait_and_yield_to_higher_owner(self):
        clock = Clock()
        value = IdleGaze(clock=clock, interval=lambda low, _high: low, chooser=lambda values: values[0])
        clock.now = 7.99;self.assertIsNone(value.tick())
        clock.now = 8;self.assertEqual(value.tick(), (-.08, -.025))
        clock.now = 16;self.assertIsNone(value.tick(higher_owner=True))
        self.assertIsNone(value.tick())
        clock.now = 24;self.assertIsNone(value.tick(eligible=False))
        clock.now = 32;target = value.tick()
        self.assertLessEqual(abs(target[0]), .08);self.assertLessEqual(abs(target[1]), .035)


class CachedPresenceTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                "bmo_ai.private_credentials.resolve_credentials", "bmo_ai.fish_tts.synthesize_wav",
                "bmo_ai.local_piper.synthesize_wav", "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))
        self.bank, self.player, self.greeting = FakeBank(), FakePlayer(), FakeGreeting()
        self.value = CachedPresence(self.bank, self.player, self.greeting)
        self.addCleanup(self.value.close)
        self.emit = lambda *_a, **_kw: None

    def test_bank_boot_uses_chime_once_and_missing_idle_is_silent(self):
        self.value.play(emit=self.emit);self.value.play(emit=self.emit)
        self.assertEqual(self.player.calls, ["boot_01", "boot_01"])
        self.assertEqual(self.greeting.chimes, 1);self.assertEqual(self.greeting.calls, [])
        self.bank.clips["idle"] = None
        self.assertIsNone(self.value.play_idle(emit=self.emit))
        self.assertEqual(self.player.calls, ["boot_01", "boot_01"])

    def test_missing_or_corrupt_boot_falls_back_only_before_actual_start(self):
        self.bank.clips["boot"] = None
        self.value.play(emit=self.emit)
        self.assertEqual(self.greeting.calls, ["legacy-greeting"])
        self.bank.clips["boot"] = "boot_01";self.player.fail = "before"
        self.value.play(emit=self.emit)
        self.assertEqual(self.greeting.calls, ["legacy-greeting"]*2)
        self.assertEqual(self.greeting.chimes, 1)
        self.player.fail = "after"
        with self.assertRaises(VoiceBankError): self.value.play(emit=self.emit)
        self.assertEqual(self.greeting.calls, ["legacy-greeting"]*2)

    def test_strong_or_sensitive_idle_never_selects_or_falls_back(self):
        for emotion, context in (("angry", "settled"), ("crying", "settled"),
                ("happy", "quiet"), ("sad", "distress"), ("neutral", "sleep")):
            self.assertIsNone(self.value.play_idle(emotion, context=context, emit=self.emit))
        self.assertEqual(self.bank.calls, []);self.assertEqual(self.greeting.calls, [])
        self.player.fail = "before"
        self.assertIsNone(self.value.play_idle(emit=self.emit))
        self.assertEqual(self.greeting.calls, [])

    def test_external_cancellation_precedes_selection_and_late_cancel_is_contained(self):
        def cancelled(): raise TurnCancelled()
        with self.assertRaises(TurnCancelled): self.value.play(emit=self.emit, cancel_check=cancelled)
        self.assertEqual(self.bank.calls, [])
        entered, release, errors = Event(), Event(), []
        def pause(check): entered.set();release.wait(2);check()
        self.player.hook = pause
        def run():
            try: self.value.play_idle(emit=self.emit)
            except BaseException as error: errors.append(error)
        worker = Thread(target=run);worker.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(PresenceError): self.value.play(emit=self.emit)
            self.value.cancel();release.set();worker.join(2)
            self.assertFalse(worker.is_alive());self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], TurnCancelled)
            self.assertTrue(self.player.cancelled);self.assertTrue(self.greeting.cancelled)
        finally:
            release.set();worker.join(2)

    def test_production_bank_player_uses_approved_private_synthetic_assets_without_answer_cache(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = integration_config();clock = Clock();clock.now = 10000
        bank = VoiceBank(root/"bank", config, clock=clock, chooser=lambda values: values[0], simulate=True)
        raw = synthetic_wav();self.addCleanup(audio._discard, raw, suppress=True)
        idle = next(c for c, row in catalog().items() if row["category"] == "idle"
            and "neutral" in row["allowed_emotions"] and "settled" in row["allowed_contexts"])
        for clip in ("boot_01", idle):
            result = bank.publish(clip, raw, simulated=True)
            bank.approve(clip, result["sha256"], listening_approved=True)
        def player(_command, *, on_start, cancel_check, **_): cancel_check();on_start();cancel_check()
        actual = BankPlayer(config, bank, simulate=True, player=player)
        fallback = GreetingPlayer(config, GreetingCache(root/"greeting", GreetingSpec.from_config(config)),
                                  simulate=True, player=player)
        value = CachedPresence(bank, actual, fallback);self.addCleanup(value.close)
        events = []
        self.assertTrue(value.play(emit=lambda kind, **_fields: events.append(kind))["completed"])
        self.assertTrue(value.play_idle(emit=lambda kind, **_fields: events.append(kind))["completed"])
        self.assertEqual(events, ["prepared", "started", "ended"]*2)
        self.assertFalse(actual.voice.has_cache);self.assertFalse(fallback.voice.has_cache)
        self.assertEqual(bank.snapshot()["recent_boot"], ["boot_01"])
        self.assertIsNone(value.play_idle(emit=self.emit))  # Same clip cooldown.


if __name__ == "__main__": unittest.main()
