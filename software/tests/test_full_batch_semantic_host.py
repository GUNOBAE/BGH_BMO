"""Current request -> real Host/session/B1/volume ownership, injected IO only.

These fixtures exercise the installed semantic/session path. They do not claim
that a live model understood English/Korean or that any hardware was operated.
Compressed container decode and actual browser keys are covered separately.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai import audio
from bmo_ai.features.camera import CameraFeature, DemoBackend, FixtureDetector, PhotoStore
from bmo_ai.features.camera.models import FaceBox
from bmo_ai.features.host import FeatureHost, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.motion.legacy_host import LegacyArmLink, LegacyMotionFeature
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from test_legacy_arm_host import FakePort, core
from test_semantic_requests import Provider, envelope


class Clock:
    def __init__(self):
        self.offset = 0.
    def __call__(self):
        return time.monotonic()+self.offset


class FullBatchSemanticHostTests(unittest.TestCase):
    def setUp(self):
        self.hosts = []
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        for name in ('socket.socket.connect', 'subprocess.Popen', 'bmo_ai.cloud.create_client',
                     'bmo_ai.private_credentials.resolve_credentials', 'bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(name, side_effect=AssertionError('actual IO forbidden')))

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def make_host(self, replies, *, camera=False, arms=False, video=False, synthesize=None, player=None,
                  character_life=True, provider=None, microphone=False, **host_options):
        index = str(len(self.hosts))
        root = self.directory / index
        root.mkdir(mode=0o700)
        config, clock = integration_config(), Clock()
        provider = provider or Provider(replies)
        played = []
        def default_player(command, *, on_start, cancel_check, **_kwargs):
            cancel_check()
            on_start()
            played.append(tuple(command))
            cancel_check()
        voice = IntegrationVoice(config, provider, simulate=synthesize is None,
            fish_synthesizer=synthesize, player=player or default_player,
            emit=lambda *_a, **_kw: None, character_life=character_life)
        if synthesize is not None:
            # Inject only the input boundary as already-transcribed text while
            # exercising real non-simulated synthesis/error/player handling.
            voice.listen = lambda: voice.message
        motion = None
        if arms:
            controller = core.LegacyBridge(None, 'batchBoot', lambda *_: None,
                lambda *_: None, lambda: 0, enabled=True, clock=clock)
            controller.enabled = True  # Memory-only staged B1 controller, no USB/PWM.
            port = FakePort(controller)
            link = LegacyArmLink(port, motor_permission=lambda: True)
            motion = LegacyMotionFeature(link, clock=clock, cooldown=0)
        def build(_options, photo_root, *, on_target, **_kwargs):
            return CameraFeature(backend_factory=lambda: DemoBackend(clock=clock),
                detector=FixtureDetector((FaceBox(.7, .2, .2, .3),)),
                store=PhotoStore(photo_root), clock=clock, on_target=on_target)
        with patch('bmo_ai.features.camera.live.build_live_camera', side_effect=build):
            host = FeatureHost(config=config, client=provider, voice=voice, clock=clock,
                runtime=RuntimePolicy(simulate=False, arms_supervised=arms,
                                      conversation_ptt_microphone=microphone), motion=motion,
                camera_supervised=camera, photo_directory=root/'photos' if camera else None,
                camera_video_supervised=video, video_directory=root/'videos' if video else None,
                character_life=character_life, **host_options)
        self.hosts.append(host)
        return host, provider, clock, played

    def finish(self, host, predicate=None):
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            host.poll()
            done = predicate() if predicate is not None else not host.session.busy and host.audio.owner is None
            if done:
                return
            time.sleep(.002)
        self.fail('Host did not finish its bounded injected operation')

    def ask(self, host, text):
        self.assertTrue(host.start_question(text))
        self.finish(host)

    @staticmethod
    def meaning(kind, name, target=None):
        return envelope(kind=kind, disposition='execute', intent={'name': name, 'target': target})

    def test_current_english_and_korean_arm_requests_reach_named_b1_once_repeat_moves_none(self):
        for text, target in [('Would you lift your left one for me?', 'raise_left_arm'),
                             ('비모 네 오른팔을 올려 줄래?', 'raise_right_arm'),
                             ('Put both arms down please.', 'both_down')]:
            with self.subTest(text=text):
                host, provider, _, played = self.make_host([self.meaning('arm', 'arm_gesture', target)], arms=True)
                self.ask(host, text)
                frames = host.motion.link.port.sent
                self.assertEqual(sum(x.startswith(b'B1 DO ') for x in frames), 1)
                self.assertIn(target.encode(), next(x for x in frames if x.startswith(b'B1 DO ')))
                self.assertEqual(host.snapshot()['motion']['operation'], 'active')
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(len(played), 1)
                repeated = host.control(HostControl('repeat'), 'repeat_'+target)
                self.assertEqual(repeated.status, 'running')
                self.finish(host)
                self.assertEqual(sum(x.startswith(b'B1 DO ') for x in frames), 1)
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(len(played), 2)
                host.control(HostControl('cancel'), 'cancel_'+target)
                host.poll()
                self.assertIsNone(host.motion.active_until)
                self.assertTrue(any(x.startswith(b'B1 HOLD ') for x in frames))
                self.assertFalse(host.snapshot()['motion']['position_verified'])

    def test_unavailable_arms_degrade_only_that_action_and_next_dialogue_still_works(self):
        host, provider, _, _ = self.make_host([self.meaning('arm', 'arm_gesture', 'wave'), envelope()])
        self.ask(host, 'Give me a wave please.')
        self.assertEqual(host.snapshot()['last_result']['status'], 'rejected')
        self.assertEqual(host.snapshot()['last_result']['code'], 'capability_denied')
        self.assertEqual(host.snapshot()['mode'], 'conversation')
        self.ask(host, 'What is two plus two?')
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(host.snapshot()['emotion'], 'neutral')

    def test_tracking_request_local_position_and_stop_preserves_explicit_preview_consumer(self):
        host, provider, clock, _ = self.make_host([
            self.meaning('tracking', 'start_face_tracking'),
            self.meaning('tracking', 'stop_face_tracking')], camera=True)
        self.ask(host, 'Could your eyes follow me?')
        state = host.snapshot()
        self.assertTrue(state['camera']['tracking_requested'])
        self.assertTrue(state['camera']['tracking'])
        self.assertTrue(state['camera']['on'])
        self.assertEqual(state['mode'], 'conversation')
        host.control(HostControl('preview', True), 'explicit_preview')
        clock.offset += .3
        host.poll()
        self.assertTrue(host.camera.snapshot()['preview'])
        self.ask(host, '이제 얼굴 추적을 멈춰 줘.')
        state = host.snapshot()
        self.assertFalse(state['camera']['tracking_requested'])
        self.assertFalse(state['camera']['tracking'])
        self.assertTrue(state['camera']['preview'])
        self.assertTrue(state['camera']['on'])
        self.assertEqual(len(provider.requests), 2)
        self.assertIsNone(host.gaze.resolve(phase='idle', generation=host.modes.generation).get('target_id'))

    def test_spoken_video_preparation_visible_ack_five_seconds_and_cancel_never_starts_recorder(self):
        host, provider, clock, played = self.make_host([
            self.meaning('video', 'start_video', 'seconds_120')], camera=True, video=True)
        self.ask(host, 'Please record a two minute video.')
        state = host.snapshot()
        self.assertEqual((state['mode'], state['video_phase']), ('video', 'preparing'))
        self.assertEqual(state['video_duration'], 60)
        self.assertTrue(state['video_duration_clamped'])
        self.assertEqual(len(played), 1)  # The same reply is preparation; no extra menu TTS.
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(host.start_question('A recording must not start PTT.'))
        self.finish(host, lambda: host.snapshot()['video_preview_token'] is not None)
        state = host.snapshot()
        self.assertIsNone(state['video_countdown'])
        ready = HostControl('video_preview_ready', {'generation': state['mode_generation'],
            'request_id': state['video_request_id'], 'preview_token': state['video_preview_token']})
        host.control(ready, 'visible_video_ack')
        clock.offset += 4.8
        host.poll()
        self.assertEqual(host.snapshot()['video_phase'], 'countdown')
        self.assertFalse(host.video.snapshot()['recording'])
        host.control(HostControl('input', {'key': 'TALK', 'pressed': True}), 'gp8_cancel_countdown')
        host.control(HostControl('input', {'key': 'TALK', 'pressed': False}), 'gp8_release_countdown')
        host.poll()
        self.assertEqual(host.snapshot()['mode'], 'conversation')
        self.assertFalse(host.video.snapshot()['recording'])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(played), 1)
        self.assertFalse(list((self.directory/'0'/'videos').glob('*.mp4')))

    def test_current_volume_and_mute_meaning_changes_output_without_new_synthesis_or_fingerprint(self):
        host, provider, _, _ = self.make_host([
            self.meaning('volume', 'set_volume', 'level_0'),
            self.meaning('volume', 'set_volume', 'level_50'),
            self.meaning('volume', 'mute_audio'),
            self.meaning('volume', 'unmute_audio')])
        gain = host.config.fish_gain
        self.ask(host, '음량을 0으로 해 줘.')
        self.assertEqual(host.snapshot()['volume']['level'], 0)
        self.ask(host, 'Make the volume fifty percent please.')
        self.assertEqual(host.snapshot()['volume']['level'], .5)
        self.ask(host, 'Please mute the sound.')
        self.assertTrue(host.snapshot()['volume']['muted'])
        self.ask(host, '음소거 풀어 줘.')
        self.assertFalse(host.snapshot()['volume']['muted'])
        self.assertEqual(host.snapshot()['volume']['level'], .5)
        self.assertEqual(host.config.fish_gain, gain)
        self.assertEqual(len(provider.requests), 4)
        self.assertTrue(all(x['max_output_tokens'] == 320 for x in provider.requests))

    def test_naked_legacy_device_proposals_cannot_record_track_or_change_volume(self):
        cases = [('start_video', None, True), ('start_face_tracking', None, False),
                 ('set_volume', 'level_100', False), ('mute_audio', None, False),
                 ('volume_up', None, False)]
        for name, target, video in cases:
            with self.subTest(name=name):
                value = dict(version=1, speech='An ordinary answer.', emotion='neutral',
                             gesture='none', intent={'name': name, 'target': target})
                host, provider, _, _ = self.make_host([value], camera=True, video=video)
                self.ask(host, 'What is two plus two?')
                self.assertEqual(host.snapshot()['mode'], 'conversation')
                self.assertFalse(host.camera.snapshot()['tracking_requested'])
                self.assertFalse(host.video.snapshot()['recording'])
                self.assertEqual(host.snapshot()['volume']['level'], .25)
                self.assertFalse(host.snapshot()['volume']['muted'])
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(host.snapshot()['last_result']['code'],
                    'camera_human_action_required' if name in ('start_video', 'start_face_tracking')
                    else 'current_user_action_required')

    def test_naked_legacy_stop_cannot_change_an_existing_tracking_or_arm_state(self):
        for name in ('stop_face_tracking', 'stop_arms'):
            with self.subTest(name=name):
                value = dict(version=1, speech='An ordinary answer.', emotion='neutral',
                             gesture='none', intent={'name': name, 'target': None})
                host, provider, _, _ = self.make_host([value], camera=True, arms=True)
                host.execute(HostIntent('start_face_tracking'), request_id='menu_tracking_start')
                host.poll()
                self.assertTrue(host.camera.snapshot()['tracking_requested'])
                self.ask(host, 'What is two plus two?')
                self.assertTrue(host.camera.snapshot()['tracking_requested'])
                self.assertFalse(host.motion.snapshot()['stopped'])
                self.assertEqual(host.snapshot()['last_result']['code'], 'current_user_action_required')
                self.assertEqual(len(provider.requests), 1)

    def test_cancel_before_reply_playback_ends_cannot_dispatch_late_named_arm(self):
        started, release = Event(), Event()
        def player(_command, *, on_start, cancel_check, **_kwargs):
            on_start()
            started.set()
            deadline = time.monotonic()+3
            while not release.wait(.002):
                cancel_check()
                if time.monotonic() >= deadline:
                    raise AssertionError('injected player was not released')
            cancel_check()
        host, provider, _, _ = self.make_host([self.meaning('arm', 'arm_gesture', 'wave')],
                                             arms=True, player=player, session_request_limit=1)
        self.assertTrue(host.start_question('Wave to me.'))
        self.assertTrue(started.wait(2))
        host.poll()
        host.control(HostControl('cancel'), 'cancel_before_finished_reply')
        release.set()
        self.finish(host)
        self.assertFalse(any(x.startswith(b'B1 DO ') for x in host.motion.link.port.sent))
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(host.snapshot()['conversation_busy'])
        self.assertEqual(host.snapshot()['request_budget']['admitted_count'], 1)
        denied = host.control(HostControl('question', 'A cancelled admission must not be refunded.'),
                              'question_after_cancelled_admission')
        self.assertEqual(denied.code, 'session_request_limit')
        self.assertEqual(len(provider.requests), 1)

    def test_live_manual_arm_session_requires_separate_opt_in_for_decorative_speech_gesture(self):
        for enabled in (False, True):
            with self.subTest(emotion_arms=enabled):
                started, release = Event(), Event()
                def player(_command, *, on_start, cancel_check, **_kwargs):
                    on_start()
                    started.set()
                    deadline = time.monotonic()+3
                    while not release.wait(.002):
                        cancel_check()
                        if time.monotonic() >= deadline:
                            raise AssertionError('injected decorative player was not released')
                    cancel_check()
                host, provider, _, _ = self.make_host([
                    envelope(model_emotion='happy', delivery='warm', gesture='wave',
                             speech='Hello there, friend!')], arms=True, player=player,
                    emotion_arms=enabled)
                try:
                    self.assertTrue(host.start_question('Hello BMO.'))
                    self.assertTrue(started.wait(2))
                    host.poll()
                    frames = host.motion.link.port.sent
                    self.assertEqual(sum(x.startswith(b'B1 DO ') for x in frames), int(enabled))
                    self.assertEqual(len(provider.requests), 1)
                finally:
                    release.set()
                    self.finish(host)
    def test_opted_in_decorative_arms_stay_still_for_crying_or_real_distress(self):
        cases = [('Please act crying for me.', envelope(kind='acting', disposition='execute',
            requested_emotion='crying', model_emotion='crying', source='acting',
            delivery='theatrical_crying', gesture='wave')),
            ('My friend died. I am grieving.', envelope(model_emotion='happy',
                delivery='warm', gesture='wave'))]
        for text, value in cases:
            with self.subTest(text=text):
                started, release = Event(), Event()
                def player(_command, *, on_start, cancel_check, **_kwargs):
                    on_start()
                    started.set()
                    deadline = time.monotonic()+3
                    while not release.wait(.002):
                        cancel_check()
                        if time.monotonic() >= deadline:
                            raise AssertionError('injected sensitive player was not released')
                    cancel_check()
                host, _, _, _ = self.make_host([value], arms=True, player=player, emotion_arms=True)
                try:
                    self.assertTrue(host.start_question(text))
                    self.assertTrue(started.wait(2))
                    host.poll()
                    self.assertFalse(any(x.startswith(b'B1 DO ') for x in host.motion.link.port.sent))
                finally:
                    release.set()
                    self.finish(host)

    def test_session_request_limit_rejects_real_ptt_path_before_opening_microphone_or_stt(self):
        opened, transcribed = [], []
        def source():
            opened.append(True)
            raise AssertionError('budget must reject before opening the microphone')
        def transcribe(*_a, **_kw):
            transcribed.append(True)
            raise AssertionError('budget must reject before STT')
        host, provider, _, _ = self.make_host([envelope()], microphone=True,
            ptt_source_factory=source, ptt_transcriber=transcribe, session_request_limit=1)
        self.ask(host, 'The one permitted fixture question.')
        self.assertEqual(host.snapshot()['request_budget']['remaining_requests'], 0)
        self.assertFalse(host.ptt_button())
        host.poll()
        self.assertEqual(opened, [])
        self.assertEqual(transcribed, [])
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(host._ptt_turn_active)
        self.assertFalse(host._ptt_handoff_pending)
        self.assertEqual(host.snapshot()['request_budget']['admitted_count'], 1)

    def test_optional_host_cooldown_and_configured_estimate_block_before_next_brain_call(self):
        host, provider, clock, _ = self.make_host([envelope(), envelope()], request_cooldown=2,
            estimated_request_cost=.1, estimated_session_cost_limit=.2)
        first = host.control(HostControl('question', 'First permitted fixture turn.'), 'budget_first')
        self.assertEqual(first.status, 'running')
        self.finish(host)
        denied = host.control(HostControl('question', 'Immediate second fixture turn.'), 'budget_cooldown')
        self.assertEqual((denied.status, denied.code), ('rejected', 'request_cooldown'))
        self.assertEqual(len(provider.requests), 1)
        clock.offset += 2.1
        second = host.control(HostControl('question', 'Second permitted fixture turn.'), 'budget_second')
        self.assertEqual(second.status, 'running')
        self.finish(host)
        denied = host.control(HostControl('question', 'Beyond configured estimate.'), 'budget_cost_limit')
        self.assertEqual((denied.status, denied.code), ('rejected', 'estimated_cost_limit'))
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(host.request_budget.snapshot()['estimated_cost'], .2)
        self.assertTrue(host.request_budget.snapshot()['billing_unknown'])

    def test_cancel_or_new_question_retires_queued_feedback_from_previous_action(self):
        for action in ('cancel', 'new_question'):
            with self.subTest(action=action):
                host, provider, _, _ = self.make_host([envelope()])
                host._voice_pack_public['approved'] = 1
                attempts = []
                # Inject approved selection, not playback. The real Host event
                # queue must retire it before any owned-playback attempt.
                with patch.object(host.voice_pack, 'select_event', return_value={'candidate_id': 'E07'}), \
                     patch.object(host.session, 'start_cached_event',
                         side_effect=lambda event: attempts.append(event) or False):
                    host._queue_local_event('error_camera')
                    if action == 'cancel':
                        host.control(HostControl('cancel'), 'cancel_previous_feedback')
                        host.poll()
                    else:
                        self.ask(host, 'A new current question supersedes the old error event.')
                    self.assertEqual(attempts, [])
                self.assertEqual(len(provider.requests), int(action == 'new_question'))

    def test_fish_authentication_failure_blocks_further_brain_calls_until_explicit_local_recheck(self):
        calls = []
        def synthesize(text, **_kwargs):
            calls.append(text)
            if len(calls) == 1:
                raise audio.AudioSynthesisError('Fixture auth error.', 'fish_authentication')
            return synthetic_wav()
        host, provider, _, played = self.make_host([envelope(), envelope()], synthesize=synthesize)
        self.ask(host, 'A first fixture question.')
        self.assertTrue(host.snapshot()['providers']['voice']['blocked'])
        self.assertEqual(host.snapshot()['providers']['voice']['code'], 'fish_authentication')
        self.assertFalse(host.start_question('This must not make another GPT call.'))
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(played)
        opened = host.execute(HostIntent('open_game_menu'), request_id='menu_after_auth')
        self.assertNotIn(opened.status, ('failed', 'rejected'))
        host.poll()
        self.assertEqual(host.snapshot()['mode'], 'game_menu')
        host.execute(HostIntent('return_to_face'), request_id='back_after_auth')
        reset = host.control(HostControl('provider_recheck'), 'explicit_auth_recheck')
        self.assertNotEqual(reset.status, 'failed')
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(calls), 1)  # Recheck is local re-admission, not API IO.
        self.ask(host, 'A second fixture question with corrected external configuration.')
        self.assertFalse(host.snapshot()['providers']['voice']['blocked'])
        self.assertEqual(host.snapshot()['providers']['voice']['code'], 'ready')
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(played), 1)


if __name__ == '__main__':
    unittest.main()
