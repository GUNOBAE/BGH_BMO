"""Controlled-clock synthetic soak, not a real-time Pi performance claim."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.journal import ActionJournal
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class OfflineSoakTests(unittest.TestCase):
    def test_48_virtual_hours_keep_one_brain_bounded_queues_and_release_owned_resources(self):
        now = [0.]
        client = FeatureSimulatedClient()
        def silent_player(command, *, cancel_check, on_start, **kwargs):
            cancel_check(); on_start(); cancel_check()
        voice = IntegrationVoice(integration_config(), client, simulate=True,
                                 emit=lambda *a, **kw: None, player=silent_player)
        baseline_threads = {t.ident for t in threading.enumerate()}
        before_fd = len(os.listdir('/proc/self/fd')) if Path('/proc/self/fd').exists() else None
        output, error = io.StringIO(), io.StringIO()
        with patch('socket.socket.connect', side_effect=AssertionError('No network')), \
                patch('subprocess.Popen', side_effect=AssertionError('No processes in clock soak')), \
                patch('bmo_ai.private_credentials.resolve_credentials', side_effect=AssertionError('No secrets')), \
                redirect_stdout(output), redirect_stderr(error):
            host = FeatureHost(demo_assets=True, client=client, voice=voice, clock=lambda: now[0])
            brain = host.session.brain
            temporary_root = Path(host._temporary.name)
            try:
                for cycle in range(48):
                    now[0] = cycle * 3600.
                    host.heartbeat()
                    self.assertTrue(host.start_question('Synthetic soak utterance.'))
                    deadline = time.monotonic()+3
                    while host.session.busy and time.monotonic() < deadline:
                        host.poll(); time.sleep(.001)
                    host.poll()
                    self.assertFalse(host.session.busy)
                    self.assertIs(host.session.brain, brain)
                    for name, target in (('open_game_menu', None), ('launch_game', 'fixture_game_a'),
                                         ('return_to_face', None), ('play_song', 'fixture_song_a'),
                                         ('stop_media', None)):
                        result = host.execute(HostIntent(name, target), request_id=f'soak_{cycle}_{name}')
                        self.assertEqual(result.status, 'simulated')
                        host.poll()
                    self.assertEqual(host.session.snapshot()['emotion'], 'happy')
                    host.heartbeat()  # reconnect/refresh is state retrieval only
                    for _ in range(4):
                        host.face_snapshot(); host.snapshot()
                    self.assertEqual(len(client.requests), cycle+1)
                    self.assertIsNone(host.audio.owner)
                    self.assertLessEqual(len(host.session.events), 128)
                    self.assertLessEqual(len(host._result_history), 512)
                    self.assertEqual(host._voice_events.qsize(), 0)
                    self.assertEqual(host._turn_generations, {})
                    self.assertEqual(host._proposals, {})
                    self.assertFalse(host.camera.snapshot()['on'])
                for i in range(32):
                    host.queue_command(f'overflow_{i}', HostIntent('open_game_menu'))
                with self.assertRaises(FeatureError):
                    host.queue_command('overflow_33', HostIntent('open_game_menu'))
                self.assertEqual(host._commands.qsize(), 32)
                host.poll()
                host.stop()
                # Duplicate and stale admission never launch a stopped game.
                generation = host.modes.generation
                host.queue_command('stale_launch', HostIntent('launch_game', 'fixture_game_a'))
                host.control(HostControl('stop'), 'stop_before_queue')
                host.poll()
                self.assertEqual(host.modes.mode, 'conversation')
                self.assertGreater(host.modes.generation, generation)
            finally:
                host.close()
            self.assertFalse(temporary_root.exists())
            self.assertFalse(host.session.worker.busy)
            self.assertIsNone(host.audio.owner)
        self.assertEqual(output.getvalue()+error.getvalue(), '')
        self.assertFalse({t.ident for t in threading.enumerate()} - baseline_threads)
        if before_fd is not None:
            self.assertLessEqual(len(os.listdir('/proc/self/fd')), before_fd)

    def test_journal_capacity_preserves_duplicates_and_restart_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'journal'
            journal = ActionJournal(root, max_entries=2)
            intent = dict(name='take_photo', target=None)
            journal.begin('old_pending', intent)
            journal.begin('finished', intent); journal.finish('finished', 'simulated', 'ok')
            with self.assertRaises(FeatureError) as error:
                journal.begin('overflow', intent)
            self.assertEqual(error.exception.code, 'journal_capacity')
            self.assertTrue(journal.begin('finished', intent)['duplicate'])
            journal.close()
            journal = ActionJournal(root, max_entries=2)
            self.assertEqual(journal.begin('old_pending', intent),
                             dict(status='unknown', code='restart_unknown', duplicate=True))
            journal.close()


if __name__ == '__main__':
    unittest.main()
