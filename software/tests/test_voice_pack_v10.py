"""v10 public handoff schema exercised with generated PCM, never private audio."""
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import wave
import zipfile

from bmo_ai import fish_tts
from bmo_ai.voice_bank import VoiceBankError
from bmo_ai.voice_pack import (OrganizedVoicePack, EVENT_CANDIDATES, INITIAL_V10_IDS,
    V10_SCHEMA, V10_ARCHIVE_SHA256, V10_WAKE_SHA256, V10_COUNTDOWN_SHA256)


# All142 public stable IDs, without shipping user audio or synthesis instructions.
V10_IDS = (
    'B01 B02 B03 B04 B05 B06 G01 G02 G03 G04 G05 G06 G07 '
    'M01 M02 M03 M04 M05 M06 M07 M08 M09 M10 '
    'PH_READY_01 PH_READY_02 PH_READY_03 PH_READY_04 PH_READY_05 PH_READY_06 '
    'PH_COUNTDOWN_05S PH_REVIEW_01 PH_REVIEW_02 PH_REVIEW_03 PH_REVIEW_04 PH_REVIEW_05 PH_REVIEW_06 '
    'PH_RETAKE_01 PH_RETAKE_02 PH_RETURN_GUIDE_01 PH_EXIT_01 PH_EXIT_02 PH_EXIT_03 PH_CANCEL_01 '
    'E01 E02 E03 E04 E05 E06 E07 E08 E09 E10 E11 E12 E13 E14 E15 E16 '
    'RX01 RX02 RX03 RX04 RX05 RX06 RX07 RX08 C01 C02 C03 C04 C05 C06 C07 C08 '
    'C09 C10 C11 C12 C13 C14 C15 C16 F01 F02 F03 F04 F05 F06 C17 C18 A01 A02 A03 wake_ack_01 '
    'REACTION_LINE_CHUCKLE_SILLY REACTION_LINE_OOH_INTERESTING REACTION_LINE_AHA_IDEA '
    'REACTION_LINE_OH_SURPRISE REACTION_LINE_HMM_MYSTERY REACTION_LINE_POUT_NO_FAIR '
    'REACTION_LINE_WHEW_RELIEF REACTION_LINE_TADA_SUCCESS REACTION_LINE_GIGGLE_FUNNY '
    'REACTION_LINE_AWW_KIND REACTION_LINE_OOPS_MY_MISTAKE REACTION_LINE_GASP_AMAZING '
    'REACTION_LINE_PROUD_HELP REACTION_LINE_SOFT_SOB_ACTING '
    'VIDEO_PREP_MOVIE VIDEO_PREP_READY VIDEO_PREP_ACTING VIDEO_RECORDING_STARTED VIDEO_TAKE_FINISHED '
    'VIDEO_SAVED_PRODUCTION VIDEO_REVIEW_WATCH VIDEO_RETAKE VIDEO_ERROR_START VIDEO_ERROR_SAVE '
    'VIDEO_LIMIT_REACHED VIDEO_CAMERA_BUSY VIDEO_NOT_RECORDING VIDEO_REVIEW_EXIT '
    'TRACK_START TRACK_TARGET_FOUND TRACK_TARGET_LOST TRACK_TARGET_RETURNED TRACK_STOP TRACK_UNAVAILABLE '
    'TRACK_NO_TARGET_YET MUSIC_CHOOSE_MODE MUSIC_REPEAT_SELECTED MUSIC_PLAYLIST_FINISHED '
    'SYSTEM_VOLUME_UP SYSTEM_VOLUME_DOWN PHOTO_FOCUS_UNCERTAIN PHOTO_REFOCUS_ONCE SYSTEM_STORAGE_FULL '
    'SYSTEM_SAVED_FILE_OPEN_FAILED SYSTEM_CAMERA_OFF SYSTEM_VOLUME_MAX SYSTEM_NOTHING_TO_REPEAT'
).split()


def pcm(seconds=.1, *, rate=44100, width=2, channels=1):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setparams((channels, width, rate, int(rate*seconds), 'NONE', 'not compressed'))
        wav.writeframes((struct.pack('<h', 2400) if width == 2 else b'\x80') * int(rate*seconds)*channels)
    return out.getvalue()


class VoicePackV10Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='bmo-v10-')))
        self.pack = OrganizedVoicePack(self.root/'pack-v10')

    def archive(self, ids=('RX01', 'wake_ack_01', 'PH_COUNTDOWN_05S'), *, mutate=None, wrapped=True):
        base = 'release/' if wrapped else ''
        rows = []
        payloads = {}
        for clip in ids:
            seconds = {'PH_COUNTDOWN_05S':5., 'wake_ack_01':1.76, 'C09':13.19, 'C12':11.10}.get(clip, .1)
            raw = pcm(seconds)
            path = 'audio/GROUP/'+clip+'__renamed.wav'
            payloads[base+path] = raw
            rows.append(dict(clip_id=clip, spoken_text='' if clip == 'RX01' else
                "Yeah? What's up?" if clip == 'wake_ack_01' else 'Five. Four. Three. Two. One.' if clip == 'PH_COUNTDOWN_05S' else 'Synthetic fixture.',
                file=path, sha256=hashlib.sha256(raw).hexdigest(), baked_gain=1.,
                requested_model='drama-3', voice_id=fish_tts.AARON_BMO_VOICE_ID,
                provenance=dict(provider='fish', requested_model='drama-3', server_model_attestation='NOT_AVAILABLE'),
                source_pack='private_selected', source_job_id='job-fixture', source_raw_sha256=hashlib.sha256(raw).hexdigest(),
                approvals=dict(listening_approved=False), phrase_timecodes_status='UNRESOLVED',
                face_direction_draft={'instruction':'Never execute this field.'}))
        if mutate:
            mutate(rows, payloads)
        file = self.root/'source.zip'
        with zipfile.ZipFile(file, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(base+'catalogue.json', json.dumps(dict(schema=V10_SCHEMA,
                not_runtime_compatible_manifest=True, entries=rows)))
            for name, raw in payloads.items():
                archive.writestr(name, raw)
            archive.writestr(base+'CHECK_FILES.py', "raise RuntimeError('never run')")
        return file, hashlib.sha256(file.read_bytes()).hexdigest()

    def imported(self, *args, **kwargs):
        path, digest = self.archive(*args, **kwargs)
        return self.pack.import_zip(path, expected_sha256=digest)

    def approve(self, clip):
        row = next(r for r in self.pack.inventory() if r['clip_id'] == clip)
        sha = row['variants']['raw']['sha256']
        self.pack.approve(row['id'], sha, listening_approved=True)
        return sha

    def test_all142_retained_priority60_has_no_approval_selection_or_played(self):
        self.assertEqual(len(V10_IDS), 142)
        self.assertEqual(len(INITIAL_V10_IDS), 60)
        state = self.imported(V10_IDS)
        self.assertEqual((state['version'], state['candidates'], state['initial_priority']), (10, 142, 60))
        self.assertEqual((state['approved'], state['listened'], state['played']), (0, 0, 0))
        self.assertEqual(state['selected_events'], {})
        rows = {r['clip_id']:r for r in self.pack.inventory()}
        self.assertEqual(rows['RX01']['text'], '')
        self.assertTrue(rows['RX01']['nonverbal'])
        self.assertGreater(rows['C09']['variants']['raw']['duration_seconds'], 13)
        self.assertGreater(rows['C12']['variants']['raw']['duration_seconds'], 11)
        self.assertTrue(state['wake_ack_registered'])
        self.assertFalse(state['wake_ack_available'])
        self.assertEqual(rows['C09']['handoff_metadata']['provenance']['server_model_attestation'], 'NOT_AVAILABLE')
        self.assertFalse(any(p.suffix == '.py' for p in self.pack.directory.iterdir()))

    def test_empty_speech_only_explicit_rx01_no_display_text_substitution(self):
        def mutate(rows, _): rows[1]['spoken_text'] = ''
        path, sha = self.archive(mutate=mutate)
        with self.assertRaises(VoiceBankError):
            self.pack.import_zip(path, expected_sha256=sha)
        self.assertFalse((self.pack.directory/'pack.json').exists())
        self.imported()
        self.approve('RX01')
        with self.pack.load_event('reaction_chuckle', context='playful') as asset:
            self.assertEqual(asset.text, '')
            self.assertEqual(asset.source_model, 'drama-3')

    def test_actual_approval_selection_load_and_player_start_are_distinct(self):
        self.imported()
        rows = {r['clip_id']:r for r in self.pack.inventory()}
        row = rows['wake_ack_01']
        self.assertIsNone(self.pack.select_event('wake_ack', row['id']))
        self.assertIsNone(self.pack.load_wake_ack())
        with self.assertRaises(VoiceBankError):
            self.pack.approve(row['id'], row['variants']['raw']['sha256'])
        sha = self.approve('wake_ack_01')
        self.assertTrue(self.pack.snapshot()['wake_ack_available'])
        self.assertEqual(self.pack.snapshot()['played'], 0)
        with self.pack.load_wake_ack() as asset:
            self.assertEqual(asset.sha256, sha)
            self.assertEqual(asset.baked_gain, 1.)
            self.assertEqual(asset.provenance['voice_id'], fish_tts.AARON_BMO_VOICE_ID)
            self.assertEqual(self.pack.snapshot()['played'], 0)
            self.pack.mark_played(asset.candidate_id, asset.sha256)
        state = self.pack.snapshot()
        self.assertEqual((state['approved'], state['listened'], state['played']), (1, 1, 1))
        self.assertEqual(state['selected_events'], {'wake_ack':row['id']})

    def test_manual_per_sha_review_uses_pcm_and_records_played_without_approval(self):
        self.imported()
        row = next(r for r in self.pack.inventory() if r['clip_id'] == 'RX01')
        sha = row['variants']['raw']['sha256']
        with self.assertRaises(VoiceBankError): self.pack.load_candidate('RX01', sha)
        with self.assertRaises(VoiceBankError): self.pack.load_candidate('RX01', '0'*64, review_requested=True)
        with self.assertRaises(VoiceBankError): self.pack.mark_played('RX01', sha)
        with self.pack.load_candidate('RX01', sha, review_requested=True) as asset:
            self.assertEqual(asset.text, '')
            self.assertTrue(asset.provenance['review_only'])
            self.assertEqual(self.pack.snapshot()['played'], 0)
            self.pack.mark_played(asset.candidate_id, asset.sha256, review_requested=True)
        state = self.pack.snapshot()
        self.assertEqual((state['played'], state['listened'], state['approved']), (1,0,0))
        self.assertIsNone(self.pack.load_event('reaction_chuckle'))

    def test_countdown_is_five_seconds_and_only_approved_current_pcm(self):
        self.imported()
        self.assertIsNone(self.pack.load_countdown())
        self.approve('PH_COUNTDOWN_05S')
        with self.pack.load_countdown() as asset:
            self.assertEqual(asset.info['duration_seconds'], 5.)
        self.assertEqual(self.pack.snapshot()['played'], 0)
        def shortened(rows, files):
            row = rows[-1]
            raw = pcm(4.9)
            files['release/'+row['file']] = raw
            row['sha256'] = hashlib.sha256(raw).hexdigest()
        fresh = OrganizedVoicePack(self.root/'bad-count')
        path, sha = self.archive(mutate=shortened)
        with self.assertRaises(VoiceBankError): fresh.import_zip(path, expected_sha256=sha)

    def test_correct_semantic_events_cannot_reintroduce_wrong_alias_via_row_events(self):
        ids = ('E01','E03','E07','E08','E13','E14','E15','E16','G01','G02','G04','G05','G06',
            'SYSTEM_SAVED_FILE_OPEN_FAILED','VIDEO_ERROR_START','VIDEO_ERROR_SAVE','TRACK_START','TRACK_STOP')
        def bad_advice(rows, _):
            for row in rows: row['events'] = ['error_network','game_start','video_saved']
        self.imported(ids, mutate=bad_advice)
        for clip in ids: self.approve(clip)
        expected = dict(error_network='E07', error_service='E08', error_camera='E14', error_capture='E15',
            error_storage='E16', error_file='SYSTEM_SAVED_FILE_OPEN_FAILED', error_music='E13',
            game_menu='G01', game_start='G02', game_launch_failed='G04', game_win='G05', game_loss='G06',
            video_start_failed='VIDEO_ERROR_START', video_save_failed='VIDEO_ERROR_SAVE',
            tracking_started='TRACK_START', tracking_stopped='TRACK_STOP')
        for event, clip in expected.items():
            with self.subTest(event=event): self.assertEqual(self.pack.select_event(event), clip)
        for event in ('error_playback','game_result','video_failed','video_saved'):
            self.assertIsNone(self.pack.select_event(event))
        for clip,event in (('E01','error_network'),('E03','error_service'),('G01','game_start'),('G04','game_win')):
            with self.assertRaises(VoiceBankError): self.pack.select_event(event,clip)

    def test_provenance_audio_gain_and_path_checks_are_not_bypassed_for_v10(self):
        changes = [lambda rows, files: rows[0].update(requested_model='s2.1-pro-free'),
                   lambda rows, files: rows[0].update(baked_gain=.25),
                   lambda rows, files: rows[0].update(file='../outside.wav'),
                   lambda rows, files: rows[0].update(sha256='0'*64)]
        for i, change in enumerate(changes):
            path, sha = self.archive(mutate=change)
            with self.subTest(i=i), self.assertRaises(VoiceBankError):
                OrganizedVoicePack(self.root/f'bad-{i}').import_zip(path, expected_sha256=sha)
        def wrong_rate(rows, files):
            raw = pcm(rate=16000)
            files['release/'+rows[0]['file']] = raw
            rows[0]['sha256'] = hashlib.sha256(raw).hexdigest()
        path, sha = self.archive(mutate=wrong_rate)
        with self.assertRaises(VoiceBankError): self.pack.import_zip(path, expected_sha256=sha)

    def test_conflicting_requested_provenance_is_not_reported_as_verified_drama(self):
        for key, value in (('requested_model', 's2.1-pro-free'), ('voice_id', 'another_voice')):
            def conflict(rows, _):
                rows[0]['provenance'][key] = value
            path, sha = self.archive(mutate=conflict)
            with self.subTest(key=key), self.assertRaises(VoiceBankError):
                self.pack.import_zip(path, expected_sha256=sha)
            self.assertFalse((self.pack.directory/'pack.json').exists())

    def test_archive_pin_and_critical_asset_constants_match_published_handoff(self):
        self.assertEqual(V10_ARCHIVE_SHA256, '6bcbf926b2d5519e63eb502c141fab4571e06eafc72315597cb147121c3a189e')
        self.assertEqual(V10_WAKE_SHA256, '28ea53663ab2fb0218ce04138b9698957772c6c84444f9e3a7119f2b7ea329bf')
        self.assertEqual(V10_COUNTDOWN_SHA256, '7444e92dade1ac3129aaebfc77aa700b26366bd284c4172e62d14397c372d2e4')

    def test_batch_release_requires_actual_sample_player_starts_and_keeps_individual_listened_zero(self):
        from bmo_ai.voice_pack import BATCH_RELEASE_IDS, BATCH_PREVIEW_IDS, BATCH_PREVIEW_MINIMUM
        self.imported(V10_IDS)
        self.assertEqual(len(BATCH_RELEASE_IDS), 61)
        plan = self.pack.batch_status()
        self.assertTrue(plan['ready'])
        self.assertEqual((plan['batch_selected'],plan['release_candidate_count']), (60,61))
        self.assertEqual(plan['extra_release_ids'], ['MUSIC_PLAYLIST_FINISHED'])
        self.assertEqual(len(plan['sample_ids']), 10)
        self.assertFalse(plan['batch_user_accepted'])
        self.assertIsNone(self.pack.load_event('music_selected'))
        with self.assertRaises(VoiceBankError):
            self.pack.accept_batch()
        with self.assertRaises(VoiceBankError):
            self.pack.accept_batch(user_accepted=True)
        self.assertFalse((self.pack.directory/'batch_release.json').exists())
        for clip in BATCH_PREVIEW_IDS[:BATCH_PREVIEW_MINIMUM]:
            row = next(r for r in self.pack.inventory() if r['clip_id'] == clip)
            self.pack.mark_played(row['id'], row['variants']['raw']['sha256'], review_requested=True)
        self.assertEqual(self.pack.batch_status()['preview_played'], 5)
        result = self.pack.accept_batch(user_accepted=True)
        state = self.pack.snapshot()
        self.assertEqual(result['status'], 'BATCH_ACCEPTED')
        self.assertEqual((state['batch_released'],state['approved'],state['listened'],state['played']),
                         (61,0,0,5))
        self.assertTrue(state['batch']['batch_user_accepted'])
        self.assertFalse(state['automatic_approval'])
        self.assertTrue(state['wake_ack_available'])
        for event, expected in [('music_selected','M04'), ('music_exit','M10'),
                                ('music_finished','MUSIC_PLAYLIST_FINISHED'),
                                ('photo_ready','PH_READY_03'), ('photo_countdown','PH_COUNTDOWN_05S'),
                                ('video_ready','VIDEO_PREP_ACTING'), ('game_start','G02'),
                                ('reaction_chuckle','RX01'), ('wake_ack','wake_ack_01')]:
            with self.subTest(event=event):
                context = 'playful' if event == 'reaction_chuckle' else 'settled'
                self.assertEqual(self.pack.select_event(event, context=context), expected)
                with self.pack.load_event(event, context=context) as asset:
                    self.assertEqual(asset.clip_id, expected)
                    self.assertEqual(asset.source_model, 'drama-3')
        with self.pack.load_countdown() as asset:
            self.assertEqual(asset.info['duration_seconds'], 5)
        # M04 was already heard in representative preview; test a fresh event
        # so distinct player-start observations increase by exactly one.
        with self.pack.load_event('video_saved') as asset:
            self.pack.mark_played(asset.candidate_id, asset.sha256)
        self.assertEqual(self.pack.snapshot()['played'], 6)
        self.assertEqual(self.pack.snapshot()['listened'], 0)
        self.assertFalse((self.pack.directory/'approvals.json').exists())
        self.assertFalse((self.pack.directory/'listened.json').exists())

    def test_batch_event_optout_rollback_and_provenance_tamper_fail_closed(self):
        from bmo_ai.voice_pack import BATCH_PREVIEW_IDS, BATCH_PREVIEW_MINIMUM
        self.imported(V10_IDS)
        for clip in BATCH_PREVIEW_IDS[:BATCH_PREVIEW_MINIMUM]:
            row = next(r for r in self.pack.inventory() if r['clip_id'] == clip)
            self.pack.mark_played(clip, row['variants']['raw']['sha256'], review_requested=True)
        self.pack.accept_batch(user_accepted=True)
        self.pack.enable_event('music_selected', enabled=False)
        self.assertIsNone(self.pack.select_event('music_selected'))
        self.assertIsNone(self.pack.load_event('music_selected'))
        self.assertEqual(self.pack.select_event('music_exit'), 'M10')
        self.pack.enable_event('music_selected', enabled=True)
        self.assertEqual(self.pack.select_event('music_selected'), 'M04')
        self.pack.enable_event('wake_ack', enabled=False)
        self.assertFalse(self.pack.snapshot()['wake_ack_available'])
        self.assertIsNone(self.pack.load_wake_ack())
        self.pack.enable_event('wake_ack', enabled=True)
        self.assertTrue(self.pack.snapshot()['wake_ack_available'])
        with self.pack.load_wake_ack() as asset:
            self.assertEqual(asset.clip_id, 'wake_ack_01')
        data = json.loads((self.pack.directory/'pack.json').read_text())
        next(r for r in data['candidates'] if r['clip_id'] == 'M04')['text'] += ' altered'
        (self.pack.directory/'pack.json').write_text(json.dumps(data))
        self.assertIsNone(self.pack.load_event('music_selected'))
        self.assertEqual(self.pack.snapshot()['batch_released'], 60)
        self.pack.disable_batch()
        self.assertFalse(self.pack.snapshot()['batch']['batch_user_accepted'])
        self.assertEqual(self.pack.snapshot()['batch_released'], 0)
        self.assertIsNone(self.pack.load_event('music_finished'))
        self.assertEqual(self.pack.snapshot()['approved'], 0)
        self.assertEqual(self.pack.snapshot()['listened'], 0)

    def test_batch_cannot_enable_on_partial_or_legacy_import(self):
        self.imported()
        state = self.pack.snapshot()['batch']
        self.assertFalse(state['ready'])
        self.assertEqual(state['batch_selected'], 0)
        with self.assertRaises(VoiceBankError):
            self.pack.accept_batch(user_accepted=True)

    def test_source_metadata_change_invalidates_individual_listening_and_played_attestations(self):
        def edit(rows, _):
            rows[-1]['local_edit'] = True
            rows[-1]['local_edit_record'] = {'source_sha256':'a'*64,'operation':'spacing-only'}
            rows[-1]['source_review_flags'] = ['Historical unaligned countdown title retained']
        self.imported(mutate=edit)
        sha = self.approve('PH_COUNTDOWN_05S')
        self.pack.mark_played('PH_COUNTDOWN_05S', sha)
        data = json.loads((self.pack.directory/'pack.json').read_text())
        row = next(r for r in data['candidates'] if r['clip_id']=='PH_COUNTDOWN_05S')
        row['handoff_metadata']['local_edit_record']['source_sha256'] = 'b'*64
        (self.pack.directory/'pack.json').write_text(json.dumps(data))
        state = self.pack.snapshot()
        self.assertEqual((state['approved'],state['listened'],state['played']),(0,0,0))
        self.assertIsNone(self.pack.load_countdown())
        self.assertEqual(state['import_audit'][0]['actual_frames'], 220500)


    def imported_actual_transcripts(self):
        from bmo_ai.voice_pack_context import context_catalog
        catalog = context_catalog()
        def actual(rows, files):
            for index, row in enumerate(rows):
                row['spoken_text'] = catalog[row['clip_id']]['en']
                path = 'release/'+row['file']
                raw = files[path][:-2] + struct.pack('<h', 1000+index)
                files[path] = raw
                row['sha256'] = row['source_raw_sha256'] = hashlib.sha256(raw).hexdigest()
        return self.imported(V10_IDS, mutate=actual)

    def test_full_use_is_explicit_sha_pinned_permission_without_reaudition_or_listening_claim(self):
        self.imported_actual_transcripts()
        with self.assertRaises(VoiceBankError):self.pack.accept_full_use()
        self.assertEqual(self.pack.snapshot()['played'], 0)
        result = self.pack.accept_full_use(user_accepted=True)
        self.assertEqual((result['status'], result['full_released']), ('FULL_USE_ACCEPTED', 142))
        state = self.pack.snapshot()
        self.assertTrue(state['full_user_accepted'])
        self.assertEqual((state['full_released'], state['batch_released']), (142, 142))
        self.assertEqual((state['approved'], state['listened'], state['played']), (0, 0, 0))
        self.assertEqual(state['usage']['total'], 142)
        self.assertFalse((self.pack.directory/'approvals.json').exists())
        self.assertFalse((self.pack.directory/'listened.json').exists())
        self.assertFalse((self.pack.directory/'played.json').exists())
        self.approve('M04')
        self.pack.disable_batch()
        state = self.pack.snapshot()
        self.assertFalse(state['full_user_accepted'])
        self.assertEqual(state['full_released'], 0)
        self.assertEqual((state['approved'], state['listened']), (1, 1))
        self.assertEqual(self.pack.select_event('music_selected'), 'M04')
        self.assertIsNone(self.pack.load_event('music_random'))

    def test_queries_do_not_choose_or_write_and_actual_pool_is_fair_without_immediate_repeat(self):
        from unittest.mock import Mock
        self.imported_actual_transcripts()
        self.pack.accept_full_use(user_accepted=True)
        chooser = Mock(side_effect=lambda values: values[0])
        self.pack.chooser = chooser
        before = {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in self.pack.directory.iterdir()}
        for _ in range(4):
            self.assertTrue(self.pack.has_event('music_selected'))
            self.assertEqual(self.pack.select_event('music_selected'), 'M04')
            self.pack.snapshot()
        self.assertEqual(chooser.call_count, 0)
        self.assertEqual(before, {p.name:(p.read_bytes(), p.stat().st_mtime_ns) for p in self.pack.directory.iterdir()})
        chosen = []
        for _ in range(9):
            with self.pack.load_event('music_selected') as asset:
                chosen.append(asset.clip_id)
        self.assertEqual(chooser.call_count, 9)
        self.assertTrue(all(left != right for left, right in zip(chosen, chosen[1:])), chosen)
        for index in range(0, 9, 3):self.assertEqual(set(chosen[index:index+3]), {'M04','M05','M06'})
        for _ in range(3):
            with self.pack.load_event('music_random') as asset:
                self.assertIn(asset.clip_id, ('M01','M02','M03'))
        # Loading/requesting alone is not actual playback or human approval.
        self.assertEqual(self.pack.snapshot()['played'], 0)

    def test_legacy_generated_pins_migrate_but_manual_pins_off_and_source_files_survive(self):
        from bmo_ai.voice_pack import BATCH_PREVIEW_IDS, BATCH_PREVIEW_MINIMUM
        self.imported_actual_transcripts()
        rows = {r['id']:r for r in self.pack.inventory()}
        for clip in BATCH_PREVIEW_IDS[:BATCH_PREVIEW_MINIMUM]:
            self.pack.mark_played(clip, rows[clip]['variants']['raw']['sha256'], review_requested=True)
        self.pack.accept_batch(user_accepted=True)
        # Simulate the old activation's selection.json, plus a different
        # legacy manual pin and an explicitly confirmed same-default pin.
        selected = {'music_selected':'M04', 'music_random':'M02', 'music_exit':'M10'}
        path = self.pack.directory/'selection.json'
        path.write_text(json.dumps(selected));path.chmod(0o600)
        self.pack.select_event('music_exit', 'M10')
        original = path.read_bytes()
        self.pack.enable_event('music_selected', enabled=False)
        self.pack.accept_full_use(user_accepted=True)
        state = self.pack.snapshot()
        self.assertEqual(path.read_bytes(), original)
        self.assertNotIn('music_selected', state['selected_events'])
        self.assertEqual(state['selected_events'], {'music_random':'M02','music_exit':'M10'})
        self.assertEqual(state['selection_origins']['music_selected'], 'legacy_batch_default')
        self.assertEqual(state['selection_origins']['music_random'], 'legacy_manual')
        self.assertEqual(state['selection_origins']['music_exit'], 'manual')
        self.assertFalse(self.pack.has_event('music_selected'))
        self.pack.enable_event('music_selected', enabled=True)
        chosen = []
        for _ in range(3):
            with self.pack.load_event('music_selected') as asset:chosen.append(asset.clip_id)
            with self.pack.load_event('music_random') as asset:self.assertEqual(asset.clip_id, 'M02')
        self.assertEqual(set(chosen), {'M04','M05','M06'})
        self.pack.select_event('music_selected', 'M05')
        for _ in range(3):
            with self.pack.load_event('music_selected') as asset:self.assertEqual(asset.clip_id, 'M05')

    def test_selected_caption_id_pcm_sha_and_context_are_one_asset(self):
        from bmo_ai.voice_pack_context import context_catalog
        catalog = context_catalog()
        self.imported_actual_transcripts()
        self.pack.accept_full_use(user_accepted=True)
        rows = {r['id']:r for r in self.pack.inventory()}
        self.assertEqual(len({rows[c]['variants']['raw']['sha256'] for c in ('M04','M05','M06')}), 3)
        for _ in range(3):
            with self.pack.load_event('music_selected') as asset:
                self.assertEqual(asset.sha256, hashlib.sha256(asset.path.read_bytes()).hexdigest())
                self.assertEqual(asset.sha256, rows[asset.candidate_id]['variants']['raw']['sha256'])
                self.assertEqual(asset.text, catalog[asset.clip_id]['en'])
                self.assertEqual(asset.captions, {lang:catalog[asset.clip_id][lang] for lang in ('en','ko')})
        with self.pack.load_event('reaction_chuckle', context='playful') as asset:
            self.assertEqual(asset.captions, {'en':'','ko':''})
        self.assertFalse(self.pack.has_event('reaction_chuckle', context='distress'))
        self.assertIsNone(self.pack.load_event('reaction_sob', context='settled'))
        self.assertFalse(self.pack.has_event('idle', emotion='sad'))
        self.assertFalse(self.pack.has_event('idle', context='distress'))
        recent = [cid for cid in catalog if cid.startswith('C')]
        self.assertFalse(self.pack.has_event('idle', recent=recent))
        with self.pack.load_event('idle') as asset:
            self.assertLessEqual(catalog[asset.clip_id]['duration_seconds'], 10.)
        self.pack.select_event('music_selected', 'M05')
        digest = rows['M05']['variants']['raw']['sha256']
        (self.pack.directory/(digest+'.wav')).write_bytes(pcm(.2))
        with self.assertRaises(VoiceBankError):self.pack.load_event('music_selected')

    def test_full_use_rejects_incomplete_inventory_and_cli_does_not_play(self):
        from contextlib import redirect_stdout
        from bmo_ai.voice_pack import main
        self.imported()
        with self.assertRaises(VoiceBankError):self.pack.accept_full_use(user_accepted=True)
        self.pack = OrganizedVoicePack(self.root/'complete-pack')
        self.imported_actual_transcripts()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['--root',str(self.pack.directory),'accept-full-use','--user-accepted']), 0)
        self.assertEqual(json.loads(output.getvalue())['full_released'], 142)
        self.assertEqual(self.pack.snapshot()['played'], 0)


if __name__ == '__main__': unittest.main()
