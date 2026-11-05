"""Reviewed public v10 scripts and local captions, without private audio."""
import hashlib
import json
import unittest

from bmo_ai.captions import clean_caption
from bmo_ai.voice_pack_context import (
    captions_for, context_catalog, context_for, eligible, event_groups, usage_report,
    performance_for, validated_performance,
)
from test_voice_pack_v10 import V10_IDS


class VoicePackContextTests(unittest.TestCase):
    def test_all_public_scripts_retained_exactly_with_local_korean_translations(self):
        rows = context_catalog()
        self.assertEqual(set(rows), set(V10_IDS))
        self.assertEqual(len(rows), 142)
        corpus = json.dumps([[clip, row['category'], row['duration_seconds'], row['en']]
                            for clip, row in rows.items()], ensure_ascii=False,
                            separators=(',', ':')).encode()
        # Public SCRIPT_INDEX_142.csv at documented ref2023f3a9, normalized
        # into the four fields above; edits cannot silently change the source.
        self.assertEqual(hashlib.sha256(corpus).hexdigest(),
                         'd970c23eb0e21f2cf816f96ad65716ff1e8597136f0231337799f46e909a00d2')
        for clip, row in rows.items():
            with self.subTest(clip=clip):
                if clip == 'RX01':
                    self.assertEqual((row['en'], row['ko']), ('', ''))
                else:
                    self.assertTrue(clean_caption(row['en']))
                    self.assertTrue(clean_caption(row['ko']))
                    self.assertNotIn('ㅋㅋ', row['ko'])
                self.assertTrue(row['reason'])

    def test_selected_clip_exact_transcript_is_required_for_translation(self):
        row = context_for('M05')
        self.assertEqual(captions_for('M05', row['en']),
                         dict(en=row['en'], ko=row['ko']))
        self.assertEqual(captions_for('M04', row['en']), dict(en=row['en'], ko=''))
        self.assertEqual(captions_for('M05', 'Synthetic fixture.'),
                         dict(en='Synthetic fixture.', ko=''))
        self.assertEqual(captions_for('RX01', ''), dict(en='', ko=''))
        self.assertEqual(captions_for('unknown', '[stage direction]'), dict(en='', ko=''))
        altered = context_catalog()
        altered['M05']['ko'] = 'Wrong caption'
        self.assertEqual(captions_for('M05', row['en'])['ko'], row['ko'])

    def test_semantic_pools_do_not_mix_selection_pause_count_or_success(self):
        groups = event_groups()
        self.assertEqual(set(groups['music_random']), {'M01', 'M02', 'M03'})
        self.assertEqual(set(groups['music_selected']), {'M04', 'M05', 'M06'})
        self.assertEqual(groups['music_paused'], ('M07',))
        self.assertEqual(groups['music_resumed'], ('M08',))
        self.assertEqual(groups['music_exit'], ('M10',))
        self.assertEqual(groups['music_finished'], ('MUSIC_PLAYLIST_FINISHED',))
        self.assertEqual(groups['photo_countdown'], ('PH_COUNTDOWN_05S',))
        self.assertEqual(groups['video_stopped'], ('VIDEO_TAKE_FINISHED',))
        self.assertNotIn('VIDEO_TAKE_FINISHED', groups['video_saved'])
        self.assertEqual(groups['error_network'], ('E07',))
        self.assertNotIn('video_recording_started', groups)

    def test_idle_performance_is_separate_from_selection_conditions_and_has_no_guessed_cues(self):
        rows = context_catalog()
        idle = {clip: row for clip, row in rows.items() if 'idle' in row['events']}
        self.assertEqual(len(idle), 18)
        for clip, row in idle.items():
            plan = performance_for(clip, row['en'], candidate_id=clip, sha256='a'*64)
            self.assertEqual(plan['source'], 'local_clip_plan')
            self.assertIn(plan['face_emotion'], ('neutral', 'happy'))
            self.assertEqual(row['emotions'], ['neutral', 'happy'])
            self.assertNotIn('cues', plan)
            if 'gaze' in plan:
                self.assertLessEqual(abs(plan['gaze']['x']), .12)
                self.assertLessEqual(abs(plan['gaze']['y']), .08)
                self.assertLessEqual(plan['gaze']['hold_seconds'], 2.)
        # Only a few lines have a reason for a brief glance, not an eye loop
        # on every line. Long retained lines stay outside automatic idle.
        self.assertEqual(sum('gaze' in row['performance'] for row in idle.values()), 4)
        self.assertFalse(eligible('C09', 'idle'))
        self.assertFalse(eligible('C12', 'idle'))

    def test_performance_requires_exact_script_identity_and_rejects_changed_plan(self):
        row = context_for('C01')
        identity = dict(clip_id='C01', candidate_id='C01', sha256='a'*64)
        plan = performance_for('C01', row['en'], candidate_id='C01', sha256='a'*64)
        self.assertEqual(validated_performance(plan, identity), plan)
        for other in (dict(identity, clip_id='C02'), dict(identity, sha256='b'*64),
                      dict(identity, candidate_id='C01_B')):
            self.assertIsNone(validated_performance(plan, other))
        self.assertIsNone(validated_performance(dict(plan, face_emotion='angry'), identity))
        self.assertIsNone(validated_performance(dict(plan, cues=[{'at': 1., 'emotion':'happy'}]), identity))
        self.assertIsNone(performance_for('C01', 'Different take.', candidate_id='C01', sha256='a'*64))
        self.assertIsNone(performance_for('C01', row['en'], candidate_id='C01', sha256='not a hash'))
        self.assertIsNone(performance_for('M04', context_for('M04')['en'], candidate_id='M04', sha256='a'*64))

    def test_idle_and_reaction_eligibility_respects_context_emotion_and_length(self):
        self.assertTrue(eligible('C02', 'idle', emotion='neutral', context='settled'))
        for emotion in ('angry', 'crying', 'sad'):
            self.assertFalse(eligible('C02', 'idle', emotion=emotion, context='settled'))
        for context in ('distress', 'quiet', 'sleep', 'off'):
            self.assertFalse(eligible('C02', 'idle', context=context))
        for clip in ('C09', 'C12'):
            self.assertFalse(eligible(clip, 'idle', context='settled'))
            self.assertEqual(context_for(clip)['status'], 'conditional')
        self.assertFalse(eligible('RX04', 'reaction_sob', context='dialogue'))
        self.assertTrue(eligible('RX04', 'reaction_sob', context='acting'))
        self.assertFalse(eligible('REACTION_LINE_OOPS_MY_MISTAKE', 'reaction_oops', context='playful'))
        self.assertTrue(eligible('REACTION_LINE_OOPS_MY_MISTAKE', 'reaction_oops', context='correction'))
        self.assertFalse(eligible('REACTION_LINE_TADA_SUCCESS', 'reaction_tada', context='dialogue'))
        self.assertTrue(eligible('REACTION_LINE_TADA_SUCCESS', 'photo_saved'))
        self.assertFalse(eligible('PH_READY_04', 'photo_group_ready', context='settled'))
        self.assertTrue(eligible('PH_READY_04', 'photo_group_ready', context='group_photo'))
        self.assertFalse(eligible('VIDEO_RECORDING_STARTED', 'video_recording_started'))

    def test_usage_report_covers_every_retained_asset_without_approval_claims(self):
        report = usage_report()
        self.assertEqual(report['total'], 142)
        self.assertEqual(sum(report['counts'].values()), 142)
        self.assertEqual({row['clip_id'] for row in report['clips']}, set(V10_IDS))
        self.assertFalse(report['individual_listening_approval'])
        self.assertFalse(report['physical_playback_verified'])
        excluded = [r['clip_id'] for r in report['clips'] if r['status'] == 'excluded']
        self.assertEqual(excluded, ['VIDEO_RECORDING_STARTED'])


if __name__ == '__main__':
    unittest.main()
