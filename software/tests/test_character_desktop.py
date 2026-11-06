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
        self.cached_ready, self.begin = Event(), Event()
        self.cached_prestart = self.fail_cached = False
        self.cached_blocked = False
        self.active = self.maximum = self.cached_calls = self.dialogue_calls = 0

    def player(self, category):
        def play(_command, *, on_start, cancel_check, **_settings):
            with self.lock:
                self.active += 1;self.maximum = max(self.maximum, self.active)
                if category == "cached": self.cached_calls += 1
                else: self.dialogue_calls += 1
            try:
                cancel_check()
                if category == 'cached':
                    self.cached_ready.set()
                    deadline = time.monotonic()+3
                    while self.cached_prestart and not self.begin.wait(.002):
                        cancel_check()
                        if time.monotonic() > deadline: raise AssertionError('Synthetic pre-start gate timed out')
                on_start()
                if category == "cached":
                    self.cached_started.set()
                    deadline = time.monotonic()+3
                    while self.cached_blocked and not self.release.wait(.002):
                        cancel_check()
                        if time.monotonic() > deadline: raise AssertionError("Synthetic cache gate timed out")
                    if self.fail_cached: raise RuntimeError('Synthetic player failure')
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
        for probe in self.probes: probe.begin.set();probe.release.set()
        for app in reversed(self.apps): app.close()

    def fixture(self, *, blocked_boot=False, presence_mode="off", character_life=True,
                require_face_presented=False, preferences=None):
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
                             presence_mode=presence_mode, require_face_presented=require_face_presented,
                             preferences=preferences)
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

    def attach_v10_idle(self, fixture, clips=('C02', 'C05')):
        # Use the actual importer, asset selector and IntegrationVoice player.
        # PCM is generated here; no private recording or device is opened.
        import test_voice_pack_v10 as assets
        from bmo_ai.voice_pack_context import context_for
        helper = assets.VoicePackV10Tests();helper.setUp()
        self.addCleanup(helper.doCleanups)
        def script(rows, _files):
            for row in rows: row['spoken_text'] = context_for(row['clip_id'])['en']
        helper.imported(clips, mutate=script)
        for clip in clips: helper.approve(clip)
        fixture.app.greeting.bank_player.voice.event_pack = helper.pack
        fixture.host.voice_pack = helper.pack
        fixture.host.session.voice.event_pack = helper.pack
        return helper.pack

    def idle_due(self, fixture):
        fixture.host.session.set_emotion('happy');fixture.host.session.set_emotion('auto')
        fixture.app._presence.interval = lambda low, _high: low
        fixture.app._presence.set_mode('normal');fixture.clock.now += 180

    def test_v10_idle_captions_follow_actual_start_exact_clip_language_off_and_end(self):
        from bmo_ai.voice_pack_context import context_for
        fixture = self.fixture();self.ready(fixture)
        pack = self.attach_v10_idle(fixture)
        fixture.app.queue_command('idleKo', HostControl('subtitles', 'ko'));fixture.app.poll()
        fixture.probe.cached_ready.clear();fixture.probe.cached_prestart = True
        fixture.probe.cached_blocked = True
        self.idle_due(fixture)
        self.pump(fixture, lambda: fixture.app.face_snapshot().get('playback') == 'prepared')
        self.assertEqual(fixture.app.face_snapshot()['caption']['text'], '')
        fixture.probe.begin.set()
        self.pump(fixture, lambda: fixture.app.face_snapshot().get('playback') == 'started')
        state = fixture.app.face_snapshot();identity = state['caption_asset']
        selected = context_for(identity['clip_id'])
        row = next(row for row in pack.inventory() if row['id'] == identity['candidate_id'])
        self.assertEqual(identity['sha256'], row['variants']['raw']['sha256'])
        self.assertEqual(state['caption'], dict(mode='ko', language='ko', text=selected['ko']))
        fixture.app.queue_command('idleEn', HostControl('subtitles', 'en'));fixture.app.poll()
        self.assertEqual(fixture.app.face_snapshot()['caption']['text'], selected['en'])
        self.assertEqual(fixture.app.face_snapshot()['caption_asset'], identity)
        fixture.app.queue_command('idleOff', HostControl('subtitles', 'off'));fixture.app.poll()
        self.assertEqual(fixture.app.face_snapshot()['caption']['text'], '')
        self.assertNotIn('caption_asset', fixture.app.face_snapshot())
        fixture.app.queue_command('idleKoAgain', HostControl('subtitles', 'ko'));fixture.app.poll()
        self.assertEqual(fixture.app.face_snapshot()['caption']['text'], selected['ko'])
        fixture.probe.release.set();self.ready(fixture)
        self.assertEqual(fixture.app.face_snapshot()['caption']['text'], '')
        self.assertNotIn('caption_asset', fixture.app.face_snapshot())
        self.assertEqual(fixture.host.session.brain.history, [])
        self.assertFalse(fixture.host.session.voice.has_cache)
        self.assertEqual(fixture.client.requests, [])

    def test_idle_performance_uses_exact_started_clip_then_yields_to_tracking_and_expires_once(self):
        from bmo_ai.integration_face import _public_snapshot
        fixture = self.fixture();self.ready(fixture)
        pack = self.attach_v10_idle(fixture, ('C01',))
        fixture.probe.cached_prestart = True;fixture.probe.cached_blocked = True
        self.idle_due(fixture)
        self.pump(fixture, lambda: fixture.app.face_snapshot().get('playback') == 'prepared')
        prepared = fixture.app.face_snapshot()
        self.assertEqual(prepared['emotion'], 'happy')  # Prior session, not the neutral clip pose.
        self.assertNotIn('performance_plan', prepared)
        self.assertNotEqual(fixture.app.snapshot()['gaze']['owner'], 'presence')
        fixture.probe.begin.set()
        self.pump(fixture, lambda: fixture.app.face_snapshot().get('playback') == 'started')
        face = fixture.app.face_snapshot();plan = face['performance_plan']
        self.assertEqual(face['emotion'], 'neutral')
        self.assertEqual(plan['clip_id'], 'C01')
        self.assertEqual(plan['sha256'], pack.inventory()[0]['variants']['raw']['sha256'])
        self.assertEqual(_public_snapshot(face)['performance_plan'], plan)
        self.assertTrue(face['cues'])  # Existing WAV mouth path is retained.
        self.assertEqual(fixture.app.snapshot()['gaze'], dict(owner='presence',apply=True,x=.08,y=-.04))
        generation = fixture.host.modes.generation
        fixture.host.gaze.offer('camera', -.3, .2, generation=generation, expires_at=fixture.clock.now+2)
        fixture.host._refresh()
        self.assertEqual(fixture.app.snapshot()['gaze']['owner'], 'camera')
        fixture.host.gaze.clear('camera')
        fixture.clock.now += 1.21;fixture.app.heartbeat();fixture.app.poll()
        self.assertNotEqual(fixture.app.snapshot()['gaze']['owner'], 'presence')
        self.assertEqual(fixture.app.face_snapshot()['emotion'], 'neutral')
        fixture.probe.release.set();self.ready(fixture)
        self.assertEqual(fixture.app.face_snapshot()['emotion'], 'happy')
        self.assertNotIn('performance_plan', fixture.app.face_snapshot())
        self.assertEqual(fixture.host.session.brain.history, [])
        self.assertFalse(fixture.host.session.voice.has_cache)

    def test_idle_performance_terminal_and_priority_input_clear_pose_gaze_and_allow_next_turn(self):
        for terminal in ('cancel', 'question', 'ptt', 'error', 'off', 'mute'):
            with self.subTest(terminal=terminal):
                fixture = self.fixture();self.ready(fixture);self.attach_v10_idle(fixture, ('C01',))
                fixture.probe.cached_blocked = True;self.idle_due(fixture)
                self.pump(fixture, lambda: fixture.app.face_snapshot().get('performance_plan') is not None)
                if terminal == 'error':
                    fixture.probe.fail_cached = True;fixture.probe.release.set()
                elif terminal == 'off':
                    fixture.app.queue_desktop_control('idleModeOff', dict(action='presence_mode',value='off'))
                elif terminal == 'mute':
                    fixture.app.queue_command('idleMute', HostControl('volume',dict(level=.25,muted=True)))
                else:
                    fixture.app.queue_command('idlePriority', HostControl(terminal,
                        'A synthetic next question.' if terminal == 'question' else None))
                    # Admission suppresses the UI before the worker acknowledges cancellation.
                    self.assertNotIn('performance_plan', fixture.app.face_snapshot())
                    self.assertNotEqual(fixture.app.snapshot()['gaze']['owner'], 'presence')
                self.ready(fixture)
                self.assertNotIn('performance_plan', fixture.app.face_snapshot())
                self.assertNotIn('presence', fixture.host.gaze._offers)
                if terminal not in ('question', 'ptt'):
                    fixture.app.queue_command('nextAfterIdle', HostControl('question','A synthetic next question.'))
                self.pump(fixture, lambda: len(fixture.client.requests) == 1);self.finish_turn(fixture)
                self.assertEqual(fixture.probe.maximum, 1)

    def test_idle_diagnostics_explain_mode_release_off_and_cooldown_without_selecting(self):
        fixture = self.fixture();self.ready(fixture);pack = self.attach_v10_idle(fixture, ('C01',))
        snapshot = lambda: fixture.app.startup_snapshot()['character_life']['presence']
        before = (dict(pack._bags), dict(pack._last_selected))
        state = snapshot()
        self.assertEqual(state['mode'], 'off');self.assertEqual(state['suppression_reason'], 'mode_off')
        self.assertEqual(state['eligible_candidate_count'], 1)
        self.assertGreater(state['next_in_seconds'], 0)
        self.assertEqual((pack._bags, pack._last_selected), before)
        fixture.probe.cached_blocked = True;self.idle_due(fixture)
        self.pump(fixture, lambda: fixture.app.face_snapshot().get('playback') == 'started')
        self.assertEqual(snapshot()['suppression_reason'], 'playing')
        fixture.probe.release.set();self.ready(fixture)
        state = snapshot()
        self.assertEqual(state['eligible_candidate_count'], 0)
        self.assertEqual(state['suppression_reason'], 'recent_cooldown')
        pack.enable_event('idle', enabled=False);fixture.clock.now += 1.1
        self.assertEqual(snapshot()['suppression_reason'], 'event_off')

    def test_v10_idle_cancel_error_and_new_question_remove_caption_then_allow_next_turn(self):
        for terminal in ('cancel', 'error', 'question'):
            with self.subTest(terminal=terminal):
                fixture = self.fixture();self.ready(fixture);self.attach_v10_idle(fixture)
                fixture.app.queue_command('idleEnglish', HostControl('subtitles', 'en'));fixture.app.poll()
                fixture.probe.cached_blocked = True;self.idle_due(fixture)
                self.pump(fixture, lambda: bool(fixture.app.face_snapshot()['caption']['text']))
                if terminal == 'error':
                    fixture.probe.fail_cached = True;fixture.probe.release.set()
                else:
                    fixture.app.queue_command('idleTerminal', HostControl(terminal,
                        'A synthetic next question.' if terminal == 'question' else None))
                    self.assertEqual(fixture.app.face_snapshot()['caption']['text'], '')
                self.ready(fixture)
                if terminal != 'question':
                    self.assertEqual(fixture.app.face_snapshot()['caption']['text'], '')
                    fixture.app.queue_command('afterIdleTerminal', HostControl('question', 'A synthetic next question.'))
                self.pump(fixture, lambda: len(fixture.client.requests) == 1);self.finish_turn(fixture)
                self.assertEqual(fixture.probe.maximum, 1)
                self.assertEqual(len(fixture.host.session.brain.history), 2)

    def test_settings_lease_idle_revoke_and_mute_preempt_without_generation_or_legacy_fallback(self):
        for terminal in ('settings', 'mute', 'idle_off'):
            with self.subTest(terminal=terminal):
                fixture = self.fixture();self.ready(fixture);pack = self.attach_v10_idle(fixture)
                fixture.probe.cached_blocked = True;self.idle_due(fixture)
                self.pump(fixture, lambda: fixture.app.face_snapshot().get('playback') == 'started')
                if terminal == 'settings':
                    fixture.app.queue_desktop_control('openSettings', dict(action='ui_activity', value=True))
                elif terminal == 'mute':
                    fixture.app.queue_command('idleMute', HostControl('volume', dict(level=.25, muted=True)))
                else:
                    fixture.app.queue_command('idleOff', HostControl('voice_batch_event', dict(event='idle', enabled=False)))
                self.ready(fixture)
                self.assertIsNone(fixture.host.audio.owner)
                self.assertEqual(fixture.client.requests, [])
                self.assertEqual(fixture.host.session.brain.history, [])
                if terminal == 'settings':
                    self.assertTrue(fixture.app.startup_snapshot()['character_life']['settings_active'])
                    fixture.clock.now += 11;fixture.app.heartbeat();fixture.app.poll()
                    self.assertFalse(fixture.app.startup_snapshot()['character_life']['settings_active'])
                    self.assertEqual(fixture.probe.cached_calls, 2)  # Fresh quiet interval after UI use.
                elif terminal == 'idle_off':
                    self.assertFalse(pack.has_event('idle', emotion='happy', context='settled'))
                    fixture.clock.now += 180;self.ready(fixture)
                    self.assertEqual(fixture.probe.cached_calls, 2)  # No legacy fallback bypasses OFF.
                else:
                    fixture.clock.now += 180;fixture.app.heartbeat();fixture.app.poll()
                    self.assertEqual(fixture.probe.cached_calls, 2)
                fixture.app.queue_command('nextConversation', HostControl('question', 'A synthetic next question.'))
                self.pump(fixture, lambda: len(fixture.client.requests) == 1);self.finish_turn(fixture)
                self.assertEqual(fixture.probe.maximum, 1)

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
        self.assertEqual(fixture.app._presence.snapshot()["heard_in_15_minutes"], 1)
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

    def test_manual_idle_from_menu_while_off_uses_actual_v10_pose_captions_and_no_auto_quota(self):
        fixture=self.fixture(presence_mode='off');self.ready(fixture)
        self.attach_v10_idle(fixture, ('C01', 'C02'))
        fixture.app.queue_command('manualKo',HostControl('subtitles','ko'));fixture.app.poll()
        fixture.app.queue_desktop_control('menuOpen',dict(action='ui_activity',value=True))
        fixture.app.queue_desktop_control('menuClosed',dict(action='ui_activity',value=False))
        fixture.probe.cached_prestart=True;fixture.probe.cached_blocked=True
        result=fixture.app.queue_desktop_control('manualFirst',dict(action='presence_test',value=None))
        self.assertEqual(result,dict(status='accepted',code='presence_test_queued'))
        self.assertEqual(fixture.app.queue_desktop_control('manualFirst',dict(action='presence_test',value=None))['code'],
                         'already_requested')
        self.assertEqual(fixture.app.queue_desktop_control('manualDouble',dict(action='presence_test',value=None))['status'],
                         'blocked')
        self.pump(fixture,lambda:fixture.app.face_snapshot().get('playback')=='prepared')
        self.assertEqual(fixture.app.face_snapshot()['caption']['text'],'')
        self.assertNotIn('performance_plan',fixture.app.face_snapshot())
        fixture.probe.begin.set()
        self.pump(fixture,lambda:fixture.app.face_snapshot().get('playback')=='started')
        face=fixture.app.face_snapshot();identity=face['caption_asset']
        self.assertEqual(face['performance_plan']['sha256'],identity['sha256'])
        self.assertEqual(face['performance_plan']['clip_id'],identity['clip_id'])
        self.assertTrue(face['cues']);self.assertTrue(face['caption']['text'])
        state=fixture.app.startup_snapshot()['character_life']
        self.assertEqual(state['manual_test']['started_count'],1)
        self.assertEqual(state['presence']['mode'],'off')
        self.assertEqual(state['presence']['heard_in_15_minutes'],0)
        self.assertEqual(fixture.app.queue_desktop_control('manualWhilePlaying',dict(action='presence_test',value=None))['code'],
                         'presence_test_busy')
        fixture.probe.release.set();self.ready(fixture)
        self.assertEqual(fixture.app.startup_snapshot()['character_life']['manual_test']['state'],'completed')
        self.assertIsNone(fixture.host.audio.owner)
        self.assertNotIn('performance_plan',fixture.app.face_snapshot())
        self.assertEqual(fixture.app.face_snapshot()['caption']['text'],'')
        fixture.probe.release.clear()
        fixture.app.queue_desktop_control('manualSecond',dict(action='presence_test',value=None))
        self.pump(fixture,lambda:fixture.app.face_snapshot().get('playback')=='started')
        self.assertNotEqual(fixture.app.face_snapshot()['caption_asset']['clip_id'],identity['clip_id'])
        fixture.probe.release.set();self.ready(fixture)
        self.assertEqual(fixture.probe.maximum,1)
        self.assertEqual(fixture.host.session.brain.history,[])
        self.assertEqual(fixture.client.requests,[])
        self.assertFalse(fixture.host.session.voice.has_cache)

    def test_manual_idle_bypasses_full_automatic_quota_but_does_not_charge_it(self):
        fixture=self.fixture();self.ready(fixture)
        self.attach_v10_idle(fixture,('C01','C02','C05','C07'))
        fixture.host.session.set_emotion('happy');fixture.host.session.set_emotion('auto')
        fixture.app._presence.interval=lambda low,_high:low
        fixture.app._presence.set_mode('normal')
        for index in range(3):
            fixture.clock.now+=180
            self.pump(fixture,lambda:fixture.app._presence.snapshot()['heard_in_15_minutes']==index+1)
            self.ready(fixture)
        before=fixture.app._presence.snapshot()
        self.assertEqual(before['suppression_reason'],'quota')
        fixture.app.queue_desktop_control('manualQuota',dict(action='presence_test',value=None))
        self.pump(fixture,lambda:fixture.app.startup_snapshot()['character_life']['manual_test']['state']=='completed')
        state=fixture.app.startup_snapshot()['character_life']
        self.assertEqual(state['manual_test']['started_count'],1)
        self.assertEqual(state['presence']['heard_in_15_minutes'],3)
        self.assertGreaterEqual(state['presence']['next_in_seconds'],180)

    def test_manual_idle_cancel_before_start_and_gp8_after_start_restore_owner(self):
        for before_start in (True,False):
            with self.subTest(before_start=before_start):
                fixture=self.fixture();self.ready(fixture);self.attach_v10_idle(fixture,('C01',))
                fixture.probe.cached_prestart=before_start;fixture.probe.cached_blocked=True
                fixture.app.queue_desktop_control('manualInterrupt',dict(action='presence_test',value=None))
                self.pump(fixture,lambda:fixture.app.face_snapshot().get('playback')==('prepared' if before_start else 'started'))
                fixture.app.queue_command('manualGp8',HostControl('ptt'))
                self.assertNotIn('performance_plan',fixture.app.face_snapshot())
                self.pump(fixture,lambda:len(fixture.client.requests)==1);self.finish_turn(fixture)
                state=fixture.app.startup_snapshot()['character_life']
                self.assertEqual(state['manual_test']['state'],'cancelled')
                self.assertEqual(state['manual_test']['started_count'],0 if before_start else 1)
                self.assertEqual(state['presence']['heard_in_15_minutes'],0)
                self.assertIsNone(fixture.host.audio.owner)
                self.assertNotIn('presence',fixture.host.gaze._offers)
                self.assertEqual(fixture.probe.maximum,1)
                self.assertEqual((len(fixture.sources),len(fixture.transcriptions)),(1,1))

    def test_manual_idle_blocks_mute_audio_distress_and_honours_event_off(self):
        fixture=self.fixture();self.ready(fixture);pack=self.attach_v10_idle(fixture,('C01',))
        initial=fixture.probe.cached_calls
        fixture.host.volume.set(muted=True)
        self.assertEqual(fixture.app.queue_desktop_control('manualMuted',dict(action='presence_test',value=None))['code'],
                         'presence_test_muted')
        fixture.host.volume.set(muted=False)
        lease=fixture.host.audio.acquire('other',stop=lambda:True)
        self.assertEqual(fixture.app.queue_desktop_control('manualBusy',dict(action='presence_test',value=None))['code'],
                         'presence_test_audio_busy')
        fixture.host.audio.release(lease)
        fixture.app._presence.note_distress()
        self.assertEqual(fixture.app.queue_desktop_control('manualSad',dict(action='presence_test',value=None))['code'],
                         'presence_test_recent_distress')
        self.assertEqual(fixture.probe.cached_calls,initial)
        fixture.clock.now+=901;pack.enable_event('idle',enabled=False)
        fixture.app.queue_desktop_control('manualDisabledAsset',dict(action='presence_test',value=None))
        self.pump(fixture,lambda:fixture.app.startup_snapshot()['character_life']['manual_test']['code']=='presence_test_assets_unavailable')
        self.assertEqual(fixture.probe.cached_calls,initial)
        self.assertIsNone(fixture.host.audio.owner)

    def test_manual_queue_loses_to_new_input_and_error_does_not_strand_face(self):
        fixture=self.fixture();self.ready(fixture);self.attach_v10_idle(fixture,('C01','C02'))
        initial=fixture.probe.cached_calls
        fixture.app.queue_desktop_control('manualLate',dict(action='presence_test',value=None))
        fixture.app.queue_command('newQuestionFirst',HostControl('question','A synthetic priority question.'))
        self.pump(fixture,lambda:len(fixture.client.requests)==1);self.finish_turn(fixture)
        self.assertEqual(fixture.probe.cached_calls,initial)
        fixture.probe.fail_cached=True
        fixture.app.queue_desktop_control('manualFailure',dict(action='presence_test',value=None))
        self.pump(fixture,lambda:fixture.app.startup_snapshot()['character_life']['manual_test']['state']=='error')
        self.assertEqual(fixture.app.startup_snapshot()['phase'],'ready')
        self.assertIsNone(fixture.host.audio.owner)
        self.assertNotIn('performance_plan',fixture.app.face_snapshot())
        self.assertEqual(fixture.app._presence.snapshot()['heard_in_15_minutes'],0)

    def test_presence_choice_restores_without_startup_write_and_manual_test_does_not_save_mode(self):
        class Preferences:
            def __init__(self):self.values={'presence_mode':'quiet'};self.writes=[]
            def load(self):return dict(self.values)
            def update(self,**values):self.values.update(values);self.writes.append(values);return True
            def snapshot(self):return dict(enabled=True,code='ready')
        prefs=Preferences();fixture=self.fixture(preferences=prefs,presence_mode='normal');self.ready(fixture)
        self.assertEqual(fixture.app._presence.mode,'quiet');self.assertEqual(prefs.writes,[])
        fixture.app.queue_desktop_control('persistPresence',dict(action='presence_mode',value='off'));fixture.app.poll()
        self.assertEqual(prefs.writes,[{'presence_mode':'off'}])
        self.attach_v10_idle(fixture,('C01',))
        fixture.app.queue_desktop_control('manualNoSave',dict(action='presence_test',value=None))
        self.pump(fixture,lambda:fixture.app.startup_snapshot()['character_life']['manual_test']['state']=='completed')
        self.assertEqual(prefs.writes,[{'presence_mode':'off'}])
        restarted=self.fixture(preferences=prefs,presence_mode='normal');self.ready(restarted)
        self.assertEqual(restarted.app._presence.mode,'off')
        fixture.host._presence_request='normal';fixture.app.poll()
        self.assertEqual(prefs.values['presence_mode'],'normal')

    def test_existing_chime_waits_for_face_once_and_missing_spoken_greeting_stays_separate(self):
        from contextlib import contextmanager
        import array
        import wave
        fixture=self.fixture(require_face_presented=True)
        fixture.app.greeting.bank=VoiceBank(self.root/'empty-boot-bank',integration_config(),simulate=True)
        owner=fixture.app.greeting.greeting_player
        owner.startup_chime=True;owner.startup_chime_status='NOT_ATTEMPTED'
        owner.allow_synthesized_chime=False;owner._chime_home=self.root/'chime-home'
        local=owner._chime_home/'.config'/'bmo'/'startup_chime.wav'
        local.parent.mkdir(parents=True,mode=0o700)
        raw=synthetic_wav()
        try:local.write_bytes(raw.read_bytes());local.chmod(0o600)
        finally:audio._discard(raw,suppress=True)
        original=local.read_bytes()
        owner.voice.volume=fixture.host.volume;fixture.host.volume.set(level=.125)
        prepare=fixture.host.volume.prepare;played=[]
        @contextmanager
        def observed_prepare(source,**settings):
            with prepare(source,**settings) as copy:
                with wave.open(str(copy),'rb') as pcm:played.append(array.array('h',pcm.readframes(pcm.getnframes())))
                yield copy
        self.enterContext(patch.object(fixture.host.volume,'prepare',side_effect=observed_prepare))
        fixture.app.poll()
        self.assertEqual(fixture.probe.cached_calls,0)
        self.assertEqual(fixture.app.startup_snapshot()['spoken_greeting_status'],'not_attempted')
        fixture.app.queue_desktop_control('chimeFace',dict(action='face_presented',value=dict(run_id=fixture.app.run_id)))
        self.ready(fixture)
        state=fixture.app.startup_snapshot()
        self.assertEqual(state['startup_chime_status'],'PLAYED')
        self.assertEqual(state['spoken_greeting_status'],'greeting_missing')
        self.assertFalse(state['greeting_played'])
        with wave.open(str(local),'rb') as pcm:source=array.array('h',pcm.readframes(pcm.getnframes()))
        self.assertLessEqual(abs(max(map(abs,played[0]))-max(map(abs,source))*.5),1)
        self.assertEqual(local.read_bytes(),original)
        fixture.app.queue_desktop_control('chimeReconnected',dict(action='face_presented',value=dict(run_id=fixture.app.run_id)))
        fixture.app.queue_desktop_control('chimeMenu',dict(action='ui_activity',value=True));fixture.app.poll()
        fixture.app.queue_desktop_control('chimeRetry',dict(action='retry_greeting',value=None));self.ready(fixture)
        self.assertEqual(fixture.probe.cached_calls,1)
        self.assertEqual(len(played),1)
        self.assertEqual(fixture.probe.maximum,1)
        fixture.app.queue_command('afterMissingGreeting',HostControl('cancel'));fixture.app.poll()
        self.assertEqual(fixture.app.startup_snapshot()['spoken_greeting_status'],'greeting_missing')

    def test_manual_browser_disconnect_settles_receipt_before_reconnect_and_next_input(self):
        fixture=self.fixture();self.ready(fixture);self.attach_v10_idle(fixture,('C01',))
        fixture.probe.cached_blocked=True
        fixture.app.queue_desktop_control('manualDisconnect',dict(action='presence_test',value=None))
        self.pump(fixture,lambda:fixture.app.face_snapshot().get('playback')=='started')
        fixture.clock.now+=fixture.app.disconnect_timeout+1
        fixture.app.poll()
        self.assertEqual(fixture.app.startup_snapshot()['code'],'browser_disconnected')
        deadline=time.monotonic()+2
        while fixture.app._worker is not None and time.monotonic()<deadline:
            fixture.app.poll();time.sleep(.002)
        self.assertIsNone(fixture.app._worker)
        self.assertEqual(fixture.app.startup_snapshot()['character_life']['manual_test']['state'],'cancelled')
        self.assertIsNone(fixture.host.audio.owner)
        fixture.app.heartbeat();fixture.app.poll()
        self.assertEqual(fixture.app.startup_snapshot()['phase'],'ready')
        fixture.app.queue_command('afterManualDisconnect',HostControl('ptt'))
        self.pump(fixture,lambda:len(fixture.client.requests)==1);self.finish_turn(fixture)
        self.assertEqual(fixture.probe.maximum,1)

    def test_baseline_without_character_life_keeps_rejecting_input_during_greeting(self):
        fixture = self.fixture(blocked_boot=True, character_life=False)
        self.pump(fixture, fixture.probe.cached_started.is_set)
        from bmo_ai.features.contracts import FeatureError
        with self.assertRaises(FeatureError):
            fixture.app.queue_command("baselineBusy", HostControl("question", "A synthetic question."))
        self.assertEqual(len(fixture.client.requests), 0)
        fixture.probe.release.set();self.ready(fixture)


if __name__ == "__main__": unittest.main()
