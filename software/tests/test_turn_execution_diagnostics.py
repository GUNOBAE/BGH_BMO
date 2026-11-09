"""Private execution receipts preserve route, boundaries and primary failures."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai.completed_turn_diagnostics import CompletedTurnStore, numeric_receipt
from bmo_ai.turn_diagnostics import TurnDiagnostics


class TurnExecutionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.now = 100.
        self.run_id, self.session_id = 'a'*32, 'b'*32
        self.revision = 'c'*40
        for name in ('socket.socket.connect', 'subprocess.Popen'):
            self.enterContext(patch(name, side_effect=AssertionError('Real IO forbidden')))
        self.store = CompletedTurnStore(self.root/'private', runtime_revision=self.revision,
            wall_clock=lambda:1800000000)
        self.addCleanup(self.store.close)
        self.assertTrue(self.store.snapshot()['enabled'])
        self.trace = TurnDiagnostics(enabled=True, clock=lambda:self.now, completed_sink=self.store.record,
            run_id=self.run_id, runtime_revision=self.revision)

    def begin(self, turn=1):
        self.trace.mark_input()
        self.trace.begin(turn, turn_id=f'{self.session_id}:{turn}')

    def record(self, at, stage, code, **kw):
        self.now = 100.+at
        self.trace.record(stage, code, **kw)

    def rows(self):
        return json.loads((self.root/'private/completed-turns.json').read_text())['records']

    def drain(self, session):
        deadline = time.monotonic() + 3.
        while session.busy and time.monotonic() < deadline:
            session.poll()
            time.sleep(.002)
        self.assertFalse(session.busy)

    def test_actual_session_local_playback_never_claims_a_fish_request(self):
        from bmo_ai.integration_profile import integration_config
        from bmo_ai.integration_session import IntegrationSession, SimulatedClient
        from bmo_ai.integration_voice import IntegrationVoice
        config, client = integration_config(), SimulatedClient()
        voice = IntegrationVoice(config, client, simulate=True,
            emit=lambda *_a, **_kw:None,
            player=lambda *_a, **kw:kw['on_start']())
        session = IntegrationSession(config, client, simulate=True, voice=voice,
            diagnostics=self.trace, clock=lambda:self.now)
        self.addCleanup(session.close)
        self.assertTrue(session.start_with_input(lambda check:'Injected private question.'))
        self.drain(session)
        row, = self.rows()
        self.assertEqual(row['turn_id'], session.session_id+':1')
        self.assertEqual(row['run_id'], self.run_id)
        self.assertEqual(row['playback_path'], 'synthetic')
        self.assertNotIn('tts_started', row['markers_ms'])
        self.assertNotIn('tts_first_byte', row['markers_ms'])
        self.assertEqual(row['terminal'], 'ok')

    def test_actual_session_offline_notice_retains_primary_ai_failure_without_fish_request(self):
        from dataclasses import replace
        from bmo_ai.cloud import CloudError
        from bmo_ai.integration_profile import integration_config
        from bmo_ai.integration_session import IntegrationSession, SimulatedClient
        from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
        config, client = replace(integration_config(), offline_voice=True), SimulatedClient()
        self.enterContext(patch.object(client.responses, 'create',
            side_effect=CloudError('connection','private cause')))
        voice = IntegrationVoice(config, client, simulate=False,
            emit=lambda *_a, **_kw:None,
            piper_synthesizer=lambda *_a, **_kw:synthetic_wav(),
            player=lambda *_a, **kw:kw['on_start']())
        session = IntegrationSession(config, client, simulate=False, voice=voice,
            diagnostics=self.trace, clock=lambda:self.now)
        self.addCleanup(session.close)
        self.assertTrue(session.start_with_input(lambda check:'Injected private question.'))
        self.drain(session)
        row, = self.rows()
        self.assertEqual(row['playback_path'], 'offline')
        self.assertNotIn('tts_started', row['markers_ms'])
        self.assertEqual(row['failure'],dict(stage='ai',code='connection',outcome='error'))
        self.assertEqual(row['terminal'], 'connection')
        self.assertIn('llm_ended', row['markers_ms'])
        self.assertNotIn('private cause', json.dumps(row))

    def test_actual_cancelled_ai_has_attempt_end_and_late_result_cannot_relabel_next_turn(self):
        from bmo_ai.integration_profile import integration_config
        from bmo_ai.integration_session import IntegrationSession, SimulatedClient
        from bmo_ai.integration_voice import IntegrationVoice
        config, client = integration_config(), SimulatedClient()
        voice = IntegrationVoice(config, client, simulate=True,
            emit=lambda *_a, **_kw:None, player=lambda *_a, **kw:kw['on_start']())
        session = IntegrationSession(config, client, simulate=True, voice=voice,
            diagnostics=self.trace, clock=lambda:self.now)
        self.addCleanup(session.close)
        entered, release = Event(), Event()
        original = client.responses.create
        def late_reply(**kwargs):
            entered.set()
            if not release.wait(2.):
                raise AssertionError('Synthetic provider was not released')
            return original(**kwargs)
        with patch.object(client.responses, 'create', side_effect=late_reply):
            self.assertTrue(session.start('Injected first question.'))
            self.assertTrue(entered.wait(1.))
            self.now += 1.
            session.cancel()
            release.set()
            self.drain(session)
        first, = self.rows()
        self.assertEqual(first['terminal'],'cancelled')
        self.assertEqual(first['cancellation'],dict(stage='ai',code='cancelled',outcome='cancelled'))
        self.assertEqual(first['timings']['llm_attempt_ms']['value_ms'],1000.)
        self.assertNotIn('player_started',first['markers_ms'])
        self.assertEqual(session.brain.history,[])
        late_timing = voice.on_timing
        self.now += 1.
        self.assertTrue(session.start('Injected next question.'))
        self.drain(session)
        before = self.rows()
        late_timing('playback','answer_started',playback_path='fish_stream')
        self.assertEqual(self.rows(),before)
        self.assertEqual(before[-1]['turn_id'],session.session_id+':2')
        self.assertEqual(before[-1]['playback_path'],'synthetic')
        self.assertEqual(before[-1]['terminal'],'ok')

    def test_streaming_answer_clock_is_separate_from_transport_download_and_pcm_submission(self):
        self.begin()
        self.record(.4,'vad','last_voiced')
        self.record(1.,'ai','started')
        self.record(2.,'ai','ok')
        self.record(2.1,'fish','started',playback_path='fish_stream')
        self.record(2.3,'fish','audio_first_byte')
        self.record(2.4,'playback','first_pcm')
        self.record(2.4,'playback','playback_started')
        self.record(2.4,'playback','answer_pcm')
        self.record(2.4,'playback','answer_started',answer_onset_evidence='body_player_boundary')
        self.record(4.,'fish','audio_downloaded')
        self.record(4.1,'fish','audio_validated')
        self.record(5.,'playback','ended')
        self.trace.finish('ok')
        row, = self.rows()
        self.assertEqual((row['version'],row['runtime_revision'],row['run_id'],row['turn_id']),
            (3,self.revision,self.run_id,self.session_id+':1'))
        self.assertEqual(row['playback_path'],'fish_stream')
        self.assertEqual(row['timings']['last_voiced_to_answer_start_ms']['value_ms'],2000.)
        self.assertEqual(row['timings']['first_byte_to_pcm_ms']['value_ms'],100.)
        self.assertEqual(row['timings']['playback_download_overlap_ms']['value_ms'],1600.)
        self.assertEqual(row['terminal_elapsed_ms'],5000.)
        self.assertEqual(row['timings']['acoustic_first_sound_ms']['status'],'UNMEASURED')

    def test_reaction_start_and_body_submission_cannot_fabricate_an_answer_onset(self):
        self.begin()
        self.record(.2,'vad','last_voiced')
        self.record(1.,'fish','started',playback_path='fish_reaction_stream',prefix_duration_ms=850.)
        self.record(1.2,'playback','first_pcm')
        self.record(1.2,'playback','playback_started')
        self.record(1.2,'playback','reaction_started')
        self.record(1.4,'playback','answer_pcm',answer_offset_frames=20400,
                    answer_onset_evidence='body_pcm_submission')
        self.record(2.7,'playback','ended')
        self.trace.finish('ok')
        row, = self.rows()
        self.assertEqual(row['playback_path'],'fish_reaction_stream')
        self.assertEqual(row['playback_metadata'],dict(prefix_duration_ms=850.,answer_offset_frames=20400.))
        self.assertEqual(row['timings']['last_voiced_to_player_start_ms']['value_ms'],1000.)
        self.assertEqual(row['timings']['last_voiced_to_answer_pcm_ms']['value_ms'],1200.)
        self.assertEqual(row['timings']['last_voiced_to_answer_start_ms']['status'],'UNMEASURED')
        self.assertEqual(row['answer_onset_evidence'],'body_pcm_submission')

    def test_failed_ai_has_attempt_end_and_original_safe_code_survives_cleanup(self):
        self.begin()
        self.record(.5,'ai','started')
        self.record(3.,'ai','operation_failed',outcome='error')
        self.record(3.01,'ai','timeout',outcome='error')
        self.record(3.2,'cleanup','audio_cleanup_failed',outcome='error')
        self.now=103.3
        self.trace.finish('timeout')
        row, = self.rows()
        self.assertEqual(row['failure'],dict(stage='ai',code='timeout',outcome='error'))
        self.assertEqual(row['cleanup_failure'],dict(stage='cleanup',code='audio_cleanup_failed',outcome='error'))
        self.assertEqual((row['failure_elapsed_ms'],row['cleanup_failure_elapsed_ms'],row['terminal_elapsed_ms']),
                         (3000.,3200.,3300.))
        self.assertEqual(row['timings']['llm_attempt_ms']['value_ms'],2500.)
        self.assertEqual(row['timings']['llm_ms']['status'],'UNMEASURED')
        self.now=104.
        self.trace.record('playback','error',outcome='error')
        self.trace.finish('operation_failed')
        self.assertEqual(self.trace.snapshot()['terminal'],'timeout')
        self.assertEqual(len(self.rows()),1)

    def test_cancel_is_identified_without_overwriting_failure_or_accepting_late_turn_data(self):
        self.begin()
        self.record(.3,'fish','started',playback_path='fish_stream')
        self.record(.8,'fish','fish_connection',outcome='error')
        self.record(1.,'playback','cancelled',outcome='cancelled')
        self.trace.finish('cancelled')
        first, = self.rows()
        self.assertEqual(first['failure']['code'],'fish_connection')
        self.assertEqual(first['cancellation'],dict(stage='playback',code='cancelled',outcome='cancelled'))
        self.assertEqual(first['cancellation_elapsed_ms'],1000.)
        self.now=110.;self.begin(2)
        self.trace.record('playback','answer_started',turn=1,playback_path='fish_stream')
        self.trace.finish('cancelled')
        second=self.rows()[1]
        self.assertEqual(second['turn_id'],self.session_id+':2')
        self.assertIsNone(second['playback_path'])
        self.assertNotIn('answer_started',second['markers_ms'])
        self.assertIsNone(second['failure'])
        self.assertFalse(self.trace.bind_runtime(run_id='d'*32))
        self.assertEqual(self.trace.snapshot()['run_id'],self.run_id)

    def test_primary_error_received_after_cleanup_observation_is_still_preserved(self):
        self.begin()
        self.record(.9, 'cleanup', 'audio_cleanup_failed', outcome='error')
        self.record(1., 'fish', 'fish_connection', outcome='error')
        self.trace.finish('fish_connection')
        row, = self.rows()
        self.assertEqual(row['failure'],dict(stage='fish',code='fish_connection',outcome='error'))
        self.assertEqual(row['failure_elapsed_ms'],1000.)
        self.assertEqual(row['cleanup_failure']['code'],'audio_cleanup_failed')
        self.assertEqual(row['cleanup_failure_elapsed_ms'],900.)

    def test_all_actual_routes_remain_distinct_and_unobserved_answer_is_unknown(self):
        for turn,path in enumerate(('fish_reaction_wav','stored_voice','offline','repeat','synthetic','fish_wav'),1):
            self.begin(turn)
            self.trace.record('playback','prepared',playback_path=path)
            self.trace.record('playback','playback_started')
            self.trace.finish('ok')
        self.assertEqual([row['playback_path'] for row in self.rows()],
            ['fish_reaction_wav','stored_voice','offline','repeat','synthetic','fish_wav'])
        self.assertTrue(all(row['timings']['last_voiced_to_answer_start_ms']['status']=='UNMEASURED'
                            for row in self.rows()))

    def test_v2_history_survives_v3_append_without_invented_run_or_route(self):
        self.begin();self.trace.finish('ok')
        previous = numeric_receipt(self.trace.snapshot(),revision=self.revision,profile='baseline',
            mode='rms',speech_safe=True,recorded_at=1799999000,version=2)
        for name in list(previous['timings']):
            if name in ('last_voiced_to_answer_start_ms','last_voiced_to_answer_pcm_ms','llm_attempt_ms',
                        'stt_attempt_ms','tts_attempt_ms','playback_ms','first_byte_to_pcm_ms',
                        'first_byte_to_player_ms','playback_download_overlap_ms'):
                del previous['timings'][name]
        previous['markers_ms'].pop('turn_finished')
        previous_rows = [dict(previous, turn=number, recorded_at_unix=1799999000+number)
                         for number in range(1,31)]
        self.store.close()
        target=self.root/'private/completed-turns.json'
        target.write_text(json.dumps(dict(version=1,records=previous_rows)))
        reopened=CompletedTurnStore(self.root/'private',runtime_revision=self.revision)
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.snapshot()['enabled'])
        self.assertEqual(reopened.snapshot()['retained'],30)
        self.assertEqual(self.rows(),previous_rows)
        self.assertTrue(reopened.record(self.trace.snapshot()))
        rows=self.rows()
        self.assertEqual(rows[:-1],previous_rows)
        self.assertTrue(all('run_id' not in row and 'playback_path' not in row for row in rows[:-1]))
        new=rows[-1]
        self.assertEqual(new['run_id'],self.run_id)
        self.assertEqual(new['version'],3)

    def test_invalid_identity_and_metadata_are_discarded_not_saved_as_private_text(self):
        trace=TurnDiagnostics(enabled=True,clock=lambda:self.now)
        self.assertFalse(trace.bind_runtime(run_id='PRIVATE KEY',runtime_revision=self.revision))
        self.assertIsNone(trace.snapshot()['runtime_revision'])
        trace.begin(1,turn_id='PRIVATE QUESTION')
        trace.record('playback','prepared',playback_path='PRIVATE PATH',answer_onset_evidence='PRIVATE',
                     prefix_duration_ms=float('nan'),answer_offset_frames=10**9,secret='PRIVATE')
        trace.record('ai','PRIVATE EXCEPTION',outcome='error')
        trace.finish('PRIVATE SECRET')
        self.assertTrue(self.store.record(trace.snapshot()))
        raw=(self.root/'private/completed-turns.json').read_text()
        self.assertNotIn('PRIVATE',raw)
        row,=self.rows()
        self.assertIsNone(row['run_id']);self.assertIsNone(row['turn_id']);self.assertIsNone(row['playback_path'])
        self.assertEqual(row['terminal'],'operation_failed')
        self.assertEqual(row['playback_metadata'],{})
        self.assertEqual(row['answer_onset_evidence'],'unknown')


if __name__=='__main__':
    unittest.main()
