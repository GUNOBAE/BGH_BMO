"""Approved v10 device events replace one request's live confirmation.

The production GPT/Host/voice path uses injected provider/PCM/camera IO only.
These checks do not assert that a live model understood a spoken request.
"""
from threading import Event
import unittest
from unittest.mock import patch

from bmo_ai.features.host import HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.media import FakeMediaBackend
from bmo_ai.features.media.library import install_wav, review_wav
from bmo_ai.integration_voice import synthetic_wav
from test_compound_requests import compound, step
from test_semantic_requests import envelope
import test_full_batch_semantic_host as host_fixtures
import test_voice_pack_v10 as pack_fixtures


class V10SemanticCacheAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.app = host_fixtures.FullBatchSemanticHostTests('runTest')
        self.app.setUp()
        self.addCleanup(self.app.doCleanups)
        self.addCleanup(self.app.tearDown)
        self.voices = pack_fixtures.VoicePackV10Tests('runTest')
        self.voices.setUp()
        self.addCleanup(self.voices.doCleanups)
        self.voices.imported(('PH_READY_03','PH_COUNTDOWN_05S','VIDEO_PREP_ACTING','TRACK_STOP','M04'))
        for clip in ('PH_READY_03','PH_COUNTDOWN_05S','VIDEO_PREP_ACTING','TRACK_STOP','M04'):
            self.voices.approve(clip)
        self.synthesized = []

    def host(self, reply, *, video=False):
        def synth(text, **_):
            self.synthesized.append(text)
            return synthetic_wav()
        return self.app.make_host([reply], camera=True, video=video, synthesize=synth,
            voice_pack_directory=self.voices.pack.directory)

    def played(self, clip):
        return next(row['played'] for row in self.voices.pack.inventory() if row['clip_id']==clip)

    def test_compound_photo_defers_cached_preparation_until_actual_preview(self):
        host, provider, _, played = self.host(compound(step('stop_face_tracking'),step('take_photo')))
        self.assertTrue(host.start_question('Stop following my face and take a photo.'))
        self.app.finish(host,lambda: host.modes.mode=='photo' and bool(host.snapshot()['photo_preview_token']))
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(self.synthesized, [])
        self.assertEqual(played, [])
        self.assertFalse(self.played('PH_READY_03'))
        state = host.snapshot()
        host.control(HostControl('photo_preview_ready',dict(generation=state['mode_generation'],
            request_id=state['photo_request_id'],preview_token=state['photo_preview_token'])),
            'compound_visible_photo_preview')
        self.app.finish(host,lambda:self.played('PH_READY_03') and self.played('PH_COUNTDOWN_05S') and not host.session.busy)
        self.assertEqual(self.synthesized, [])
        self.assertEqual(len(played),2)
        self.assertEqual(host._photo_phase,'countdown')

    def test_spoken_video_uses_approved_preparation_instead_of_live_tts(self):
        host, provider, _, played = self.host(envelope(kind='video',disposition='execute',
            intent=dict(name='start_video',target='seconds_5')),video=True)
        self.assertTrue(host.start_question('Record a five-second video.'))
        self.app.finish(host,lambda:host.modes.mode=='video' and bool(host.snapshot()['video_preview_token']))
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(self.synthesized, [])
        self.assertTrue(self.played('VIDEO_PREP_ACTING'))
        self.assertEqual(len(played),1)

    def test_spoken_tracking_stop_uses_one_cache_and_no_duplicate_live_confirmation(self):
        host, provider, _, played = self.host(envelope(kind='tracking',disposition='execute',
            intent=dict(name='stop_face_tracking',target=None)))
        host.camera.set_tracking(True,generation=host.modes.generation)
        self.assertTrue(host.start_question('Stop following my face.'))
        self.app.finish(host,lambda:self.played('TRACK_STOP') and not host.session.busy)
        self.assertEqual(len(provider.requests),1)
        self.assertFalse(host.camera.snapshot()['tracking_requested'])
        self.assertEqual(self.synthesized, [])
        self.assertEqual(len(played),1)


    def music_host(self, *, menu=False):
        source = self.app.directory/'fixture-song.wav'
        source.write_bytes(pack_fixtures.pcm(seconds=3.))
        source.chmod(0o600)
        library = self.app.directory/'fixture-music-library'
        install_wav(review_wav(source),library,'space_potatoes',title='Synthetic test song',
            license_id='test_generated',license_reviewed=True)
        cache_started, release = Event(), Event()
        self.addCleanup(release.set)
        events = []
        def player(_command, *, on_start, cancel_check, **_):
            cancel_check();on_start()
            events.append('cache_started');cache_started.set()
            while not release.wait(.002):cancel_check()
            cancel_check();events.append('cache_finished')
        class ObservedMusicBackend(FakeMediaBackend):
            simulated = False  # Injected live boundary, no subprocess/output.
            def start(self, handle):
                events.append('music_started')
                super().start(handle)
        backend = ObservedMusicBackend(pausable=True)
        def factory(*, clock, **_):
            backend.clock = clock
            return backend
        def synth(text, **_):
            self.synthesized.append(text)
            return synthetic_wav()
        value = envelope(kind='media',disposition='execute',intent=dict(name='play_song',target='space_potatoes'))
        with patch('bmo_ai.features.media.SampleClockWavBackend',side_effect=factory):
            host,provider,clock,_ = self.app.make_host([] if menu else [value],player=player,
                synthesize=synth,music_directory=library,music_supervised=True,
                voice_pack_directory=self.voices.pack.directory)
        return host,provider,clock,backend,cache_started,release,events

    def test_current_song_request_finishes_one_approved_cache_before_music_owner_starts(self):
        host,provider,_,backend,entered,release,events = self.music_host()
        self.assertTrue(host.start_question('Play Space Potatoes.'))
        self.app.finish(host,lambda:entered.is_set())
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(self.synthesized,[])
        self.assertTrue(self.played('M04'))
        self.assertEqual(events,['cache_started'])
        self.assertIsNone(backend.handle)
        self.assertNotEqual(host.audio.owner,'music')
        release.set()
        self.app.finish(host,lambda:host.modes.mode=='music' and backend.handle is not None)
        self.assertEqual(events,['cache_started','cache_finished','music_started'])
        self.assertEqual(host.audio.owner,'music')
        self.assertEqual(self.synthesized,[])
        self.assertEqual(len(provider.requests),1)

    def test_menu_song_cancel_retires_cached_preparation_and_late_completion_cannot_start_song(self):
        host,provider,_,backend,entered,release,events = self.music_host(menu=True)
        approvals_before = (self.voices.pack.directory/'approvals.json').read_bytes()
        host.execute(HostIntent('play_song','space_potatoes'),request_id='menu_song_with_cache')
        self.app.finish(host,lambda:entered.is_set())
        turn = host.session.snapshot()['turn_id']
        self.assertEqual(events,['cache_started'])
        self.assertTrue(self.played('M04'))  # Observation does not create approval.
        self.assertIsNone(backend.handle)
        host.control(HostControl('cancel'),'cancel_menu_song_prep')
        release.set()
        self.app.finish(host,lambda:not host.session.busy and host.audio.owner is None)
        host._playback('ended',turn,host.clock())
        for _ in range(4):host.poll()
        self.assertEqual(host.modes.mode,'conversation')
        self.assertIsNone(backend.handle)
        self.assertNotIn('music_started',events)
        self.assertEqual(provider.requests,[])
        self.assertEqual(self.synthesized,[])
        self.assertEqual((self.voices.pack.directory/'approvals.json').read_bytes(),approvals_before)
        self.assertFalse(self.voices.pack.snapshot()['automatic_approval'])


if __name__=='__main__': unittest.main()
