"""v10 photo callbacks through real Host/SDK adapter and one injected player.

No private WAV, paid provider, microphone, camera or physical playback is used.
Synthetic PCM follows the public v10 catalogue schema.
"""
from threading import Event
import time
import unittest

from bmo_ai.audio import AudioPlaybackError
from bmo_ai.features.host import HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.wake_ack import WakeAckCache
import test_real_camera_host as camera_fixtures
import test_voice_pack_v10 as pack_fixtures
import test_field_wake_handoff as wake_fixtures


class V10PhotoEventsTests(unittest.TestCase):
    def setUp(self):
        # Reuse the injected production Picamera2/PhotoStore fixture without
        # inheriting/re-running its unrelated camera/video tests.
        self.camera = camera_fixtures.SupervisedCameraHostTests('runTest')
        self.camera.setUp()
        self.addCleanup(self.camera.doCleanups)
        self.addCleanup(self.camera.tearDown)
        self.voices = pack_fixtures.VoicePackV10Tests('runTest')
        self.voices.setUp()
        self.addCleanup(self.voices.doCleanups)
        self.voices.imported(('PH_READY_03', 'PH_COUNTDOWN_05S', 'PH_REVIEW_02', 'wake_ack_01'))
        self.clock = self.camera.clock
        self.sdk = self.camera.sdk
        self.releases = []
        self.addCleanup(lambda:[release.set() for release in self.releases])

    def host(self, *, approved=('PH_READY_03', 'PH_COUNTDOWN_05S', 'PH_REVIEW_02')):
        for clip in approved:
            self.voices.approve(clip)
        host = self.camera.host(video=False, character_life=True,
                                voice_pack_directory=self.voices.pack.directory)
        self.calls = []
        self.synth_calls = []
        # Even the mock live synthesis is forbidden when the approved events
        # cover preparation/countdown/success.
        def forbidden_synth(*args, **kwargs):
            self.synth_calls.append((args, kwargs))
            raise AssertionError('Unexpected live synthesis')
        host.session.voice._fish_synthesizer = forbidden_synth
        return host

    def poll(self, host):
        return self.camera.poll(host)

    def until(self, host, predicate, message):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            self.poll(host)
            if predicate():
                return
            time.sleep(.002)
        self.fail(message)

    def question_and_preview(self, host):
        self.assertTrue(host.start_question('Take my picture please.'))
        self.until(host, lambda:host.modes.mode == 'photo' and not host.session.busy,
                   'initial deferred request did not enter photo preview')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.synth_calls, [])
        self.until(host, lambda:host.snapshot()['photo_preview_token'] is not None,
                   'preview was not ready')
        preview = host.preview_frame()
        self.assertTrue(preview['payload'].startswith(b'\xff\xd8'))
        value = host.snapshot()
        result = host.control(HostControl('photo_preview_ready', dict(
            generation=value['mode_generation'], request_id=value['photo_request_id'],
            preview_token=value['photo_preview_token'])), 'visible_actual_preview')
        self.assertNotEqual(result.status, 'rejected')

    def player(self, host, *, delayed_countdown=False, fail_countdown=False):
        countdown_entered, countdown_release = Event(), Event()
        self.releases.append(countdown_release)
        if not delayed_countdown:
            countdown_release.set()
        def play(_command, *, on_start, cancel_check, **_kwargs):
            index = len(self.calls)+1
            self.calls.append(index)
            cancel_check()
            if index == 2:
                countdown_entered.set()
                while not countdown_release.wait(.002):
                    cancel_check()
                if fail_countdown:
                    raise AudioPlaybackError('Injected player could not start.', 'device_unavailable')
            on_start()
            cancel_check()
        host.session.voice._player = play
        return countdown_entered, countdown_release

    def tick(self, seconds):
        self.clock.now += seconds
        self.sdk.sensor += int(seconds*1_000_000_000)

    def test_preview_then_ready_then_actual_countdown_start_owns_one_shutter_and_saved_event(self):
        host = self.host()
        entered, release = self.player(host, delayed_countdown=True)
        self.question_and_preview(host)
        self.until(host, lambda:entered.is_set(), 'countdown did not reach owned player')
        self.assertEqual(self.calls, [1,2])
        self.assertEqual(host._photo_phase, 'countdown_audio_start')
        self.assertIsNone(host._pending_photo['deadline'])
        self.tick(.6)
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 0)
        self.assertIsNone(host._pending_photo['deadline'])
        release.set()
        self.until(host, lambda:host._photo_phase == 'countdown' and not host.session.busy,
                   'actual countdown player start did not establish countdown')
        audio = host._pending_photo['countdown_audio']
        self.assertEqual(audio['clock'], 'actual_player_start')
        self.assertAlmostEqual(host._pending_photo['deadline']-audio['started_at'], 5.)
        # Injected WAV ended immediately. Ended is not a second shutter trigger.
        self.assertEqual(self.sdk.calls.count('still'), 0)
        self.tick(4.6)
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 0)
        self.tick(.5)
        self.camera.wait_fresh_preview(host)
        self.until(host, lambda:host._photo_phase == 'photo_review' and not host.session.busy,
                   'saved photo did not reach persistent review')
        self.assertEqual(self.sdk.calls.count('still'), 1)
        self.assertEqual(self.calls, [1,2,3])
        self.assertEqual(self.synth_calls, [])
        inventory = {row['clip_id']:row for row in self.voices.pack.inventory()}
        self.assertTrue(inventory['PH_REVIEW_02']['played'])
        self.assertFalse(inventory['wake_ack_01']['played'])
        host.control(HostControl('repeat'), 'repeat_saved_reply')
        self.until(host, lambda:not host.session.busy, 'repeat did not finish')
        self.tick(6)
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 1)
        self.assertTrue(host.ptt_button())
        self.assertEqual(host.modes.mode, 'conversation')

    def test_cancel_before_countdown_actualstart_retires_late_start_end_and_repeat(self):
        host = self.host()
        entered, release = self.player(host, delayed_countdown=True)
        self.question_and_preview(host)
        self.until(host, lambda:entered.is_set(), 'countdown player was not waiting')
        countdown_turn = host._pending_photo['countdown_turn']
        host.execute(HostIntent('return_to_face'), request_id='cancel_countdown_back')
        release.set()
        self.until(host, lambda:host.modes.mode == 'conversation' and not host.session.busy,
                   'cancel did not restore conversation')
        # A delayed old player event cannot recreate the retired photo request.
        host._playback('started', countdown_turn, self.clock())
        host._playback('ended', countdown_turn, self.clock())
        host.control(HostControl('repeat'), 'repeat_cancelled_photo')
        self.until(host, lambda:not host.session.busy, 'empty repeat did not finish')
        self.tick(7)
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 0)
        self.assertIsNone(host._pending_photo)
        self.assertFalse(next(r for r in self.voices.pack.inventory()
                              if r['clip_id'] == 'PH_REVIEW_02')['played'])

    def test_countdown_player_failure_before_start_uses_full_visual_five_seconds(self):
        host = self.host()
        self.player(host, fail_countdown=True)
        self.question_and_preview(host)
        self.until(host, lambda:host._photo_phase == 'countdown' and not host.session.busy,
                   'failed audio did not produce visual fallback')
        pending = host._pending_photo
        self.assertEqual(pending['countdown_audio']['status'], 'visual_fallback')
        self.assertEqual(pending['countdown_audio']['code'], 'countdown_player_not_started')
        self.assertAlmostEqual(pending['deadline']-self.clock(), 5., delta=.2)
        self.assertEqual(self.sdk.calls.count('still'), 0)
        self.tick(4.5)
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 0)
        host.execute(HostIntent('return_to_face'), request_id='fallback_back')

    def test_unapproved_countdown_and_mute_keep_visual_count_not_instant_capture(self):
        for muted in (False, True):
            with self.subTest(muted=muted):
                host = self.host(approved=('PH_READY_03', 'PH_REVIEW_02'))
                self.player(host)
                if muted:
                    host.volume.set(muted=True)
                self.question_and_preview(host)
                self.until(host, lambda:host._photo_phase == 'countdown' and not host.session.busy,
                           'unapproved/muted countdown did not use visual fallback')
                self.assertEqual(host._pending_photo['countdown_audio']['status'], 'visual_fallback')
                self.assertAlmostEqual(host._pending_photo['deadline']-self.clock(), 5., delta=.2)
                self.assertEqual(self.sdk.calls.count('still'), 0)
                host.execute(HostIntent('return_to_face'), request_id='unapproved_back_'+str(muted))
                self.poll(host)

    def test_countdown_start_timeout_retires_worker_and_restarts_full_visual_timer(self):
        host = self.host()
        entered, release = self.player(host, delayed_countdown=True)
        self.question_and_preview(host)
        self.until(host, lambda:entered.is_set(), 'countdown player did not wait')
        self.tick(3.2)
        self.poll(host)
        pending = host._pending_photo
        self.assertEqual(pending['countdown_audio']['code'], 'countdown_player_start_timeout')
        self.assertAlmostEqual(pending['deadline']-self.clock(), 5., delta=.2)
        release.set()
        self.until(host, lambda:not host.session.busy, 'timed-out worker did not retire')
        self.assertEqual(pending['countdown_audio']['status'], 'visual_fallback')
        self.assertEqual(self.sdk.calls.count('still'), 0)
        self.tick(4.5)
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 0)
        host.execute(HostIntent('return_to_face'), request_id='timeout_back')

    def test_failed_capture_never_plays_saved_review_or_retries_on_repeat(self):
        host = self.host()
        self.player(host)
        self.question_and_preview(host)
        self.until(host, lambda:host._photo_phase == 'countdown' and not host.session.busy,
                   'countdown did not start')
        self.sdk.fail = 'still'
        self.tick(5.1)
        self.camera.wait_fresh_preview(host)
        self.until(host, lambda:host._photo_phase == 'failed' and not host.session.busy,
                   'injected capture failure was not reported')
        self.assertEqual(self.sdk.calls.count('still'), 1)
        self.assertEqual(self.calls, [1,2])
        self.assertFalse(next(r for r in self.voices.pack.inventory()
                              if r['clip_id'] == 'PH_REVIEW_02')['played'])
        host.control(HostControl('repeat'), 'repeat_failed_capture')
        self.until(host, lambda:not host.session.busy, 'failed-photo repeat did not finish')
        self.tick(6)
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 1)
        self.assertEqual(host._photo_phase, 'failed')
        host.execute(HostIntent('return_to_face'), request_id='failure_back')
        self.assertEqual(host.modes.mode, 'conversation')

    def wake_fixture(self, **options):
        fixture = wake_fixtures.FieldWakeHandoffTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        values = fixture.make(**options)
        host = values[0]
        host.voice_pack = self.voices.pack
        host.session.voice.event_pack = self.voices.pack
        host._wake_ack_cache = WakeAckCache(host.config, fixture.root/'unused_legacy',
                                            organized_pack=self.voices.pack)
        return fixture, values

    def test_approved_pack_ack_uses_existing_wake_owner_then_preserves_first_question_bytes(self):
        self.voices.approve('wake_ack_01')
        observed = []
        def actual_player(factory, _clock):
            if not observed:
                self.assertTrue(factory.sources[0].closed.is_set())
                state = host.session.snapshot()
                observed.append((state['phase'], state['source']))
        fixture, values = self.wake_fixture(player=actual_player)
        host, client, factory, detector, clock, captures, played = values
        fixture.trigger(host, factory, detector)
        host.poll()
        wake_fixtures.eventually(lambda:len(factory.sources) == 2 and
                                  host._microphone_broker.snapshot()['capture_active'])
        self.assertEqual(host._wake_handoff['acknowledgement'], 'PLAYED')
        self.assertEqual(observed, [('speaking', 'cached_wake_ack')])
        self.assertEqual(host._wake_ack_cache.snapshot()['source_model'], 'drama-3')
        self.assertTrue(next(r for r in self.voices.pack.inventory()
                             if r['clip_id'] == 'wake_ack_01')['played'])
        self.assertEqual(client.requests, [])
        self.assertEqual(captures, [])
        fixture.speech(factory.sources[-1])
        fixture.finish(host)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(len(played), 2)  # One ACK, one injected answer.
        self.assertEqual(factory.maximum, 1)
        self.assertIn((5001).to_bytes(2, 'little')*640, captures[0])
        self.assertNotIn((8000).to_bytes(2, 'little')*640, captures[0])

    def test_registered_unapproved_pack_ack_is_visual_only_without_gpt_or_tts(self):
        fixture, values = self.wake_fixture()
        host, client, factory, detector, clock, captures, played = values
        fixture.trigger(host, factory, detector)
        host.poll()
        wake_fixtures.eventually(lambda:host._microphone_broker.snapshot()['capture_active'])
        self.assertEqual(host._wake_ack_cache.snapshot()['status'], 'ASSET_UNAPPROVED')
        self.assertEqual(played, [])
        clock.offset = 5.2
        fixture.finish(host)
        self.assertEqual(client.requests, [])
        self.assertEqual(captures, [])
        self.assertEqual(played, [])
        self.assertFalse(next(r for r in self.voices.pack.inventory()
                              if r['clip_id'] == 'wake_ack_01')['played'])


if __name__ == '__main__':
    unittest.main()
