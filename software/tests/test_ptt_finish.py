"""Done-speaking uses real capture completion, never the PTT toggle/cancel path."""
from types import SimpleNamespace
import unittest

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import HostControl
import test_ptt_session_integration as fixtures


class PttFinishTests(unittest.TestCase):
    def setUp(self):
        # Reuse the existing injected PCM/SDK/output harness without inheriting
        # or discovering its unrelated tests a second time.
        self.fixture = fixtures.PttSessionIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def done(self, host, request):
        return host.control(HostControl('ptt_finish'), request)

    def test_payload_validation_and_idle_do_not_open_capture_or_start_turn(self):
        for value in (True, {}, 'start'):
            with self.assertRaises(FeatureError):
                HostControl('ptt_finish', value)
        for enabled in (False, True):
            host, trace = self.fixture.host(enabled=enabled)
            for index in range(3):
                self.assertEqual(self.done(host, f'idle_{enabled}_{index}').code,
                                 'capture_not_listening')
            self.assertEqual(trace.sources, [])
            self.assertEqual(trace.stt, [])
            self.assertEqual(host.client.requests, [])
            self.assertFalse(host.session.busy)

    def test_done_then_repeats_thinking_playback_and_idle_never_duplicate_or_cancel(self):
        capture_entered, capture_release = self.fixture.gate()
        stt_entered, stt_release = self.fixture.gate()
        player_entered, player_release = self.fixture.gate()

        def stt(**_):
            stt_entered.set()
            self.assertTrue(stt_release.wait(3), 'Injected STT gate timed out')
            return SimpleNamespace(text='Synthetic spoken question.')

        def player(_command, **options):
            player_entered.set()
            self.assertTrue(player_release.wait(3), 'Injected player gate timed out')
            options['cancel_check']()

        host, trace = self.fixture.host(patterns=[(0, 10), (10000, 80), (0, 24)],
            source_settings=dict(entered=capture_entered, release=capture_release, block_at=18),
            stt_callback=stt, player_callback=player)
        self.assertTrue(host.ptt_button())
        self.assertTrue(capture_entered.wait(2))
        self.assertEqual(host.microphone.snapshot()['state'], 'speech')
        first = host.queue_command('touch_done', HostControl('ptt_finish'))
        host.heartbeat(); host.poll()
        self.assertEqual(host.queue_command('touch_done', HostControl('ptt_finish'))['request_id'],
                         first['request_id'])
        self.assertEqual(self.done(host, 'second_touch').code, 'capture_finish_requested')
        capture_release.set()
        self.assertTrue(stt_entered.wait(2))
        self.assertEqual(self.done(host, 'late_thinking_touch').code, 'capture_not_listening')
        self.assertTrue(host.session.busy)
        stt_release.set()
        self.assertTrue(player_entered.wait(2))
        self.assertEqual(self.done(host, 'late_playback_touch').code, 'capture_not_listening')
        player_release.set()
        self.fixture.finish(host)
        self.assertEqual(len(trace.sources), 1)
        self.assertEqual(len(trace.stt), 1)
        self.assertEqual(len(trace.synthesis), 1)
        self.assertEqual(len(trace.players), 1)
        self.assertEqual(len(host.client.requests), 1)
        self.assertEqual(len(host.session.brain.history), 2)
        self.assertTrue(host.session.voice.has_cache)
        self.assertIsNone(host.audio.owner)
        self.assertEqual(self.done(host, 'after_completion').code, 'capture_not_listening')
        self.fixture.turn(host)
        self.assertEqual(len(trace.stt), 2)
        self.assertEqual(len(host.client.requests), 2)

    def test_done_before_any_speech_finishes_empty_without_stt(self):
        entered, release = self.fixture.gate()
        host, trace = self.fixture.host(patterns=[(0, 100)],
            source_settings=dict(entered=entered, release=release, block_at=12))
        self.assertTrue(host.ptt_button())
        self.assertTrue(entered.wait(2))
        self.assertEqual(self.done(host, 'empty_done').code, 'capture_finish_requested')
        release.set()
        self.fixture.finish(host)
        self.assertEqual(trace.stt, [])
        self.assertEqual(host.client.requests, [])
        self.assertEqual(trace.players, [])
        self.assertIsNone(host.audio.owner)

    def test_queued_old_done_does_not_finish_a_new_capture_in_same_mode(self):
        entered, release = self.fixture.gate()
        host, trace = self.fixture.host(patterns=[(0, 10), (10000, 80), (0, 24)],
            source_settings=dict(entered=entered, release=release, block_at=18))
        old = host.queue_command('old_done', HostControl('ptt_finish'))
        self.assertTrue(host.ptt_button())
        self.assertTrue(entered.wait(2))
        host.heartbeat(); host.poll()
        result = host.queue_command('old_done', HostControl('ptt_finish'))
        self.assertEqual(result['request_id'], old['request_id'])
        self.assertEqual(result['code'], 'capture_not_listening')
        self.assertFalse(host.microphone._finish.is_set())
        self.assertEqual(host.microphone.snapshot()['state'], 'speech')
        self.assertEqual(self.done(host, 'current_done').code, 'capture_finish_requested')
        release.set()
        self.fixture.finish(host)
        self.assertEqual(len(trace.stt), 1)


if __name__ == '__main__':
    unittest.main()
