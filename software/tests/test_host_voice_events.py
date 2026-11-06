"""Real Host/worker/private pack events; all provider/device/player IO injected."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

from bmo_ai import fish_tts
from bmo_ai.audio import AudioPlaybackError
from bmo_ai.desktop_app import main, parser, _config
from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from bmo_ai.voice_pack import OrganizedVoicePack
from test_organized_voice_pack import pcm
from test_desktop_runtime_failures import Host as RuntimeHost, Resource, Greeting


class HostVoiceEventTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-event-host-')))
        self.now = 10.
        self.hosts = []
        self.releases = []
        for name in ('subprocess.Popen', 'socket.socket.connect', 'socket.socket.connect_ex',
                     'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.cloud.create_client',
                     'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                     'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(name, side_effect=AssertionError('Actual provider/device IO forbidden')))
        self.pack_path = self.root/'imported-pack'

    def tearDown(self):
        for release in self.releases:
            release.set()
        for host in self.hosts:
            host.close()

    def pack(self, *, approve=('G02', 'G06', 'TRACK_ON', 'TRACK_OFF', 'M10')):
        entries = [('G02', 'Ooh, game time!', []), ('G06', 'Oh... game over.', []),
                   ('TRACK_ON', 'I am looking over here.', ['tracking_started']),
                   ('TRACK_OFF', 'Eyes back to home.', ['tracking_stopped']),
                   ('M10', 'Music is all done.', []),
                   ('M04', 'Here comes the music.', []),
                   ('MUSIC_PLAYLIST_FINISHED', 'That was the last song.', [])]
        rows = []
        archive = self.root/'source.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            for index, (clip, text, events) in enumerate(entries):
                raw = pcm(8000+index*100)
                digest = hashlib.sha256(raw).hexdigest()
                path = 'RAW/'+clip+'.wav'
                bundle.writestr(path, raw)
                rows.append(dict(candidate_id=clip+'_A', clip_id=clip, text=text,
                    model='drama-3', voice_id=fish_tts.AARON_BMO_VOICE_ID, events=events,
                    files=[dict(path=path, sha256=digest, baked_gain=1.)]))
            bundle.writestr('catalogue.json', json.dumps(dict(candidates=rows)))
        pack = OrganizedVoicePack(self.pack_path)
        pack.import_zip(archive, expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        for row in pack.inventory():
            if row['clip_id'] in approve:
                pack.approve(row['id'], row['variants']['raw']['sha256'], listening_approved=True)
        return pack

    def host(self, **settings):
        client = FeatureSimulatedClient()
        counts = dict(players=0, started=0)
        entered, release = Event(), Event();release.set();self.releases.append(release)
        def player(command, *, on_start, cancel_check, **_):
            counts['players'] += 1
            cancel_check();on_start();counts['started'] += 1;entered.set()
            while not release.wait(.002):
                cancel_check()
            cancel_check()
        voice = IntegrationVoice(integration_config(), client, simulate=True,
                                 emit=lambda *_a, **_kw:None, player=player)
        host = FeatureHost(config=integration_config(), client=client, voice=voice,
            playable_games=True, demo_assets=True, voice_pack_directory=self.pack_path,
            clock=lambda:self.now, **settings)
        self.hosts.append(host)
        return host, counts, entered, release

    def finish(self, host, *, mode=None):
        until = time.monotonic()+3
        while time.monotonic() < until:
            host.poll()
            if not host.session.busy and not host._local_events and not host._local_event_active:
                if mode is None or host.modes.mode == mode:
                    break
            time.sleep(.002)
        self.assertFalse(host.session.busy)
        if mode is not None:
            self.assertEqual(host.modes.mode, mode)
        host.poll()

    def launch(self, host):
        result = host.execute(HostIntent('launch_game', 'bmo_blocks'), request_id='game_launch')
        self.assertEqual(result.status, 'accepted')
        web = host.games.snapshot()['web']
        value = dict(launch_id=web['launch_id'], generation=host.modes.generation)
        host.control(HostControl('game_ready', value), 'game_ready')
        return value

    def test_tracking_and_game_events_play_approved_clips_once_without_model_or_tts(self):
        self.pack()
        host, counts, _, _ = self.host()
        forbidden = Mock(side_effect=AssertionError('A local event must not synthesize'))
        host.session.voice._synthesize = forbidden
        host.execute(HostIntent('start_face_tracking'), request_id='tracking_on')
        self.finish(host)
        self.assertTrue(host.camera.snapshot()['tracking'])
        self.assertEqual(counts['players'], 1)
        self.assertIsNone(host.audio.owner)
        host.execute(HostIntent('start_face_tracking'), request_id='tracking_on')
        self.finish(host)
        self.assertEqual(counts['players'], 1)
        host.execute(HostIntent('stop_face_tracking'), request_id='tracking_off')
        self.finish(host)
        self.assertEqual(counts['players'], 2)
        self.launch(host);self.finish(host, mode='game')
        self.assertEqual(counts['players'], 3)
        self.assertEqual(host.audio.owner, 'game')
        self.assertEqual(host.session.snapshot()['source'], 'voice_pack')
        self.assertEqual(len(host.client.requests), 0);forbidden.assert_not_called()

    def test_actual_game_outcome_once_and_repeat_cannot_retrigger_effect(self):
        self.pack()
        host, counts, _, _ = self.host()
        # Seed a successful spoken answer before local effects. Its repeat must
        # retain the dialogue WAV, never repeat the engine outcome or its action.
        host.session.voice.speak('An earlier successful answer.')
        original = host.session.voice._cache['path'].read_bytes()
        start = counts['players']
        ready = self.launch(host);self.finish(host, mode='game')
        event = dict(ready, event_id=ready['launch_id']+':outcome', outcome='gameover')
        host.control(HostControl('game_event', event), 'actual_game_outcome')
        self.finish(host, mode='game')
        self.assertEqual(counts['players'], start+2)
        host.control(HostControl('game_event', event), 'duplicate_outcome_nonce')
        self.finish(host, mode='game')
        self.assertEqual(counts['players'], start+2)
        self.assertEqual(host.session.voice._cache['path'].read_bytes(), original)
        result = host.control(HostControl('repeat'), 'repeat_last_answer')
        self.assertEqual(result.status, 'running');self.finish(host, mode='conversation')
        self.assertEqual(counts['players'], start+3)
        self.assertEqual(len(host.client.requests), 0)
        self.assertFalse(host._local_events)

    def test_unapproved_or_unmatched_event_has_no_audio_or_failed_optional_turn(self):
        # A different approved clip does not grant the unapproved tracking one.
        self.pack(approve=('G02',))
        host, counts, _, _ = self.host()
        before = host.session.worker.turn
        host.execute(HostIntent('start_face_tracking'), request_id='unapproved_tracking')
        self.finish(host)
        self.assertTrue(host.camera.snapshot()['tracking'])
        self.assertEqual(counts['players'], 0)
        self.assertEqual(len(host.client.requests), 0)
        self.assertEqual(host.session.worker.turn, before)
        self.assertNotEqual(host.session.snapshot()['playback'], 'error')

    def test_missing_pack_and_silent_feedback_option_never_output_or_call_a_provider(self):
        missing, counts, _, _ = self.host()
        missing.execute(HostIntent('start_face_tracking'), request_id='missing_tracking')
        self.finish(missing)
        self.assertEqual(counts['players'], 0)
        self.pack()
        silent, silent_counts, _, _ = self.host(photo_feedback=False)
        silent.execute(HostIntent('start_face_tracking'), request_id='silent_tracking')
        self.finish(silent)
        self.assertTrue(silent.camera.snapshot()['tracking'])
        self.assertEqual(silent_counts['players'], 0)
        self.assertEqual(len(silent.client.requests), 0)

    def test_back_cancels_active_game_clip_and_returns_without_restarting_effect(self):
        self.pack()
        host, counts, entered, release = self.host()
        release.clear()
        self.launch(host);host.poll()
        self.assertTrue(entered.wait(1))
        self.assertEqual(host.audio.owner, 'game')
        host.execute(HostIntent('return_to_face'), request_id='back_during_clip')
        self.finish(host, mode='conversation')
        self.assertIsNone(host.audio.owner)
        self.assertEqual(counts['players'], 1)
        for _ in range(4):
            host.poll()
        self.assertEqual(counts['players'], 1)
        self.assertEqual(len(host.client.requests), 0)

    def test_mode_change_before_optional_event_start_discards_old_generation(self):
        self.pack()
        host, counts, _, _ = self.host()
        host.execute(HostIntent('start_face_tracking'), request_id='queued_tracking')
        self.assertTrue(host._local_events)
        host.execute(HostIntent('open_game_menu'), request_id='menu_wins')
        self.finish(host, mode='game_menu')
        self.assertEqual(counts['players'], 0)
        self.assertEqual(len(host.client.requests), 0)

    def review(self, host, pack, request):
        row = next(row for row in pack.inventory() if row['clip_id'] == 'M10')
        admitted = host.queue_command(request, HostControl('voice_preview', dict(
            candidate_id=row['id'], sha256=row['variants']['raw']['sha256'])))
        host.poll()
        return admitted['request_id']

    def receipt(self, host, request):
        return next(row for row in host.snapshot()['requests'] if row['request_id'] == request)

    def test_review_receipt_tracks_current_playback_not_historical_played(self):
        pack = self.pack(approve=())
        host, counts, _, _ = self.host()
        request = self.review(host, pack, 'review_ok')
        self.finish(host)
        receipt = self.receipt(host, request)
        self.assertEqual((receipt['code'], receipt['completed'], receipt['completion']),
                         ('voice_review_completed', True, 'review_playback'))
        self.assertEqual((pack.snapshot()['played'], pack.snapshot()['approved']), (1, 0))
        original = host.session.voice._player
        host.session.voice._player = Mock(side_effect=AudioPlaybackError('Injected failure.'))
        request = self.review(host, pack, 'review_failed')
        self.finish(host)
        failed = self.receipt(host, request)
        self.assertEqual((failed['code'], failed['completed']), ('voice_review_failed', False))
        self.assertEqual(pack.snapshot()['played'], 1)
        self.assertEqual(counts['players'], 1)
        self.assertIsNone(host.audio.owner)
        host.session.voice._player = original
        self.assertTrue(host.start_question('Can we talk after that failed preview?'))
        self.finish(host)
        self.assertEqual(len(host.client.requests), 1)
        self.assertIsNone(host.audio.owner)

    def test_cancelled_review_and_missing_player_start_never_complete(self):
        pack = self.pack(approve=())
        host, _, entered, release = self.host()
        release.clear()
        request = self.review(host, pack, 'review_cancel')
        self.assertTrue(entered.wait(1))
        host.control(HostControl('cancel'), 'cancel_review')
        self.finish(host)
        cancelled = self.receipt(host, request)
        self.assertEqual((cancelled['code'], cancelled['completed']), ('voice_review_cancelled', False))
        self.assertIsNone(host.audio.owner)
        # A broken injected player returning without on_start cannot attest to
        # playback just because the worker returned normally or history exists.
        host.session.voice._player = lambda *_a, **_kw: None
        request = self.review(host, pack, 'review_no_start')
        self.finish(host)
        no_start = self.receipt(host, request)
        self.assertEqual((no_start['code'], no_start['completed']), ('voice_review_failed', False))
        self.assertEqual(pack.snapshot()['approved'], 0)

    def test_music_preparation_manual_and_natural_end_are_ordered_and_once(self):
        pack = self.pack(approve=('M04', 'M10', 'MUSIC_PLAYLIST_FINISHED'))
        host, counts, entered, release = self.host()
        release.clear()
        host.execute(HostIntent('play_song', 'fixture_song_a'), request_id='music_begin')
        self.assertTrue(entered.wait(1))
        self.assertEqual(host.audio.owner, 'music_preparation')
        self.assertIsNone(host.media.snapshot()['active_id'])
        release.set();self.finish(host, mode='music')
        self.assertEqual(counts['players'], 1)
        self.assertEqual(host.audio.owner, 'music')
        host.execute(HostIntent('stop_media'), request_id='manual_end')
        self.finish(host, mode='conversation')
        self.assertEqual(counts['players'], 2)
        host.execute(HostIntent('stop_media'), request_id='new_nonce_already_stopped')
        self.finish(host)
        self.assertEqual(counts['players'], 2)
        host.execute(HostIntent('play_song', 'fixture_song_a'), request_id='music_again')
        self.finish(host, mode='music')
        self.assertEqual(counts['players'], 3)
        self.now += 1.;self.finish(host, mode='conversation')
        self.assertEqual(counts['players'], 4)
        for _ in range(4):host.poll()
        host.execute(HostIntent('stop_media'), request_id='stop_after_natural_end')
        self.finish(host)
        self.assertEqual(counts['players'], 4)
        self.assertEqual({r['clip_id'] for r in pack.inventory() if r['played']},
                         {'M04', 'M10', 'MUSIC_PLAYLIST_FINISHED'})
        self.assertIsNone(host.audio.owner)
        self.assertEqual(host.client.requests, [])

    def test_event_off_cancels_only_current_matching_cache_then_on_restores(self):
        self.pack()
        host, counts, entered, release = self.host()
        release.clear()
        host.control(HostControl('voice_event', 'music_exit'), 'exit_preview')
        host.poll();self.assertTrue(entered.wait(1))
        host.control(HostControl('voice_batch_event', dict(event='tracking_started', enabled=False)), 'unrelated_off')
        host.poll()
        self.assertTrue(host.session.busy)
        host.control(HostControl('voice_batch_event', dict(event='music_exit', enabled=False)), 'exit_off')
        self.finish(host)
        self.assertEqual(host.session.snapshot()['playback'], 'cancelled')
        self.assertIsNone(host.audio.owner)
        self.assertIsNone(host.voice_pack.select_event('music_exit'))
        host.control(HostControl('voice_event', 'music_exit'), 'disabled_event')
        self.finish(host)
        self.assertEqual(counts['players'], 1)
        host.control(HostControl('voice_batch_event', dict(event='music_exit', enabled=True)), 'exit_on')
        release.set()
        host.control(HostControl('voice_event', 'music_exit'), 'restored_event')
        self.finish(host)
        self.assertEqual(counts['players'], 2)
        entered.clear();release.clear()
        self.assertTrue(host.start_question('One unrelated dialogue turn.'))
        deadline = time.monotonic()+2
        while time.monotonic()<deadline and not entered.is_set():
            host.poll();time.sleep(.002)
        self.assertTrue(entered.is_set())
        host.control(HostControl('voice_batch_event', dict(event='music_exit', enabled=False)), 'off_during_dialogue')
        host.poll();self.assertTrue(host.session.busy)
        release.set();self.finish(host)
        self.assertEqual(host.session.snapshot()['playback'], 'ended')
        self.assertIsNone(host.audio.owner)

    def test_batch_revoke_during_preparation_cancels_song_admission(self):
        import test_voice_pack_v10 as fixture
        voices = fixture.VoicePackV10Tests('runTest');voices.setUp()
        self.addCleanup(voices.doCleanups)
        voices.imported(fixture.V10_IDS)
        self.pack_path = voices.pack.directory
        host, counts, entered, release = self.host()
        # Actual injected player-start observations satisfy the existing bundle
        # gate; they are not human listening approvals.
        from bmo_ai.voice_pack import BATCH_PREVIEW_IDS, BATCH_PREVIEW_MINIMUM
        rows = {r['id']:r for r in voices.pack.inventory()}
        for index, clip in enumerate(BATCH_PREVIEW_IDS[:BATCH_PREVIEW_MINIMUM]):
            host.control(HostControl('voice_preview', dict(candidate_id=clip,
                sha256=rows[clip]['variants']['raw']['sha256'])), 'sample_'+str(index))
            self.finish(host)
        accepted = host.control(HostControl('voice_batch_accept', True), 'accept_bundle')
        self.assertEqual(accepted.code, 'voice_batch_accepted')
        entered.clear();release.clear()
        host.execute(HostIntent('play_song', 'fixture_song_a'), request_id='revoked_song')
        self.assertTrue(entered.wait(1))
        self.assertEqual(host.audio.owner, 'music_preparation')
        host.control(HostControl('voice_batch_disable'), 'revoke_bundle')
        self.finish(host, mode='conversation')
        self.assertIsNone(host.media.snapshot()['active_id'])
        self.assertIsNone(host.audio.owner)
        self.assertIsNone(host._pending_media_start)
        self.assertEqual(voices.pack.snapshot()['batch_released'], 0)
        self.assertEqual(voices.pack.snapshot()['approved'], 0)
        self.assertEqual(voices.pack.snapshot()['listened'], 0)
        before = counts['players']
        release.set()
        for _ in range(4):host.poll()
        self.assertEqual(counts['players'], before)


class SilentStartTests(unittest.TestCase):
    def test_runtime_silent_start_reaches_ready_without_greeting_chime_or_audio_lease(self):
        app = DesktopRuntime(simulate=True, silent_start=True)
        greeting = Greeting()
        app.set_server(Resource());app.set_browser(Resource());app.set_host(RuntimeHost(), greeting)
        self.addCleanup(app.close)
        app.queue_desktop_control('face_ready', dict(action='face_ready', value=dict(
            run_id=app.run_id, screen_sha256='a'*64, frame_version='synthetic-contract-v1')))
        for _ in range(3):
            app.poll()
        self.assertEqual(app.startup_snapshot()['phase'], 'ready')
        self.assertEqual(app.startup_snapshot()['code'], 'greeting_cancelled')
        self.assertEqual(greeting.calls, 0)
        self.assertIsNone(app.host.audio.owner)
        self.assertIsNone(app._worker)

    def test_explicit_silent_cli_dry_run_preserves_voice_defaults_and_opens_nothing(self):
        args = ['--simulate', '--silent-start', '--no-feedback-voice', '--no-startup-chime', '--dry-run']
        parsed = parser().parse_args(args)
        self.assertTrue(parsed.silent_start and parsed.no_feedback_voice and parsed.no_startup_chime)
        config = _config(parsed)
        self.assertEqual((config.fish_gain, config.chat_model, config.fish_delivery), (.25, 'gpt-6-luna', 'soft'))
        forbidden = Mock(side_effect=AssertionError('Smoke planning must not open devices or keys'))
        with patch('os.open', forbidden), patch('bmo_ai.private_credentials.resolve_credentials', forbidden), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args, host_factory=forbidden, browser_factory=forbidden), 0)
        forbidden.assert_not_called()
