"""HTTP admission during synchronous capture must beat success playback."""
from pathlib import Path
import unittest
from unittest.mock import patch

from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class PhotoCancelAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.hosts = []
        for name in ('subprocess.Popen', 'socket.socket.connect', 'socket.socket.connect_ex',
                     'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(name, side_effect=AssertionError('Physical/paid IO forbidden')))

    def tearDown(self):
        for host in self.hosts:
            host.close()

    def preparing(self):
        client = FeatureSimulatedClient()
        played = []
        def player(command, *, on_start, cancel_check, **_):
            cancel_check(); on_start(); played.append(command); cancel_check()
        voice = IntegrationVoice(integration_config(), client, simulate=True,
            emit=lambda *_a, **_kw: None, player=player)
        host = FeatureHost(demo_assets=True, client=client, voice=voice, clock=lambda: self.now)
        self.hosts.append(host)
        host.execute(HostIntent('take_photo'), request_id='current_photo')
        host.poll()
        current = host.snapshot()
        host.control(HostControl('photo_preview_ready', dict(generation=current['mode_generation'],
            request_id=current['photo_request_id'], preview_token=current['photo_preview_token'])), 'shown')
        self.now += 5.1
        return host, client, played

    def test_cancel_stop_reset_and_back_admitted_during_capture_prevent_save_and_success(self):
        commands = [HostControl('cancel'), HostControl('stop'), HostControl('reset'),
                    HostIntent('return_to_face')]
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                host, client, played = self.preparing()
                original = host.camera.take_photo
                def capture(*args, **kwargs):
                    host.queue_command('cancel_in_capture_'+str(index), command)
                    return original(*args, **kwargs)
                with patch.object(host.camera, 'take_photo', side_effect=capture):
                    host.poll()
                self.assertEqual(list(Path(host.camera.store.root).glob('*.png')), [])
                self.assertEqual(played, [])
                self.assertEqual(client.requests, [])
                self.assertNotEqual(host.snapshot()['photo_phase'], 'photo_review')
                self.assertFalse(host.camera.snapshot()['on'])
                host.poll()
                self.assertEqual(host.modes.mode, 'conversation')

    def test_back_after_storage_commit_does_not_start_success_feedback_or_retry(self):
        host, client, played = self.preparing()
        original = host.camera.photo_bytes
        def read(photo_id):
            pixels = original(photo_id)
            host.queue_command('back_after_store', HostIntent('return_to_face'))
            return pixels
        with patch.object(host.camera, 'photo_bytes', side_effect=read):
            host.poll()
        self.assertEqual(len(list(Path(host.camera.store.root).glob('*.png'))), 1)
        self.assertEqual(played, [])
        self.assertEqual(client.requests, [])
        host.poll()
        self.now += 20.; host.poll()
        self.assertEqual(host.modes.mode, 'conversation')
        self.assertEqual(len(list(Path(host.camera.store.root).glob('*.png'))), 1)
        self.assertEqual(played, [])
