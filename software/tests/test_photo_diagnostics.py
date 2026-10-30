"""SDK wait/store/review evidence without camera, network or paid providers."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.camera import CameraFeature, PhotoStore, Picamera2Backend
from bmo_ai.features.camera.diagnostics import photo_diagnostic
from bmo_ai.features.camera.models import PhotoResult
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from test_feature_camera_adapters import Clock, FakeCamera


class PhotoEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock = Clock()
        for name in ('subprocess.Popen', 'socket.socket.connect', 'socket.socket.connect_ex',
                     'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.cloud.create_client'):
            self.enterContext(patch(name, side_effect=AssertionError('Device/provider IO forbidden')))

    def camera(self, phase, *, photo_timeout=.2):
        sdk = FakeCamera(self.clock)
        original = sdk.wait
        def wait(job, *, timeout):
            if job[0] == phase:
                self.clock.now += timeout
                raise TimeoutError('private SDK details must stay private')
            return original(job, timeout=timeout)
        sdk.wait = wait
        backend = Picamera2Backend(enabled=True, camera_factory=lambda: sdk,
            clock=self.clock, wait_clock=self.clock, focus_timeout=.1, photo_timeout=photo_timeout)
        camera = CameraFeature(backend_factory=lambda: backend,
            store=PhotoStore(self.root/'photos'), clock=self.clock)
        self.addCleanup(camera.close)
        return camera, sdk

    def test_focus_and_still_timeouts_keep_distinct_journal_stages_without_retry(self):
        for phase, expected in (('focus', 'focus'), ('photo', 'still_capture')):
            with self.subTest(phase=phase):
                camera, sdk = self.camera(phase)
                request = 'bounded_' + phase
                before = self.clock()
                result = camera.take_photo(request, generation=1)
                self.assertEqual((result.code, result.stage, result.completed),
                                 ('camera_timeout', expected, False))
                self.assertTrue(result.executed)
                self.assertLessEqual(self.clock()-before, .200001)
                self.assertFalse(camera.broker.opened)
                self.assertEqual(list((self.root/'photos').glob('*.png')), [])
                record = json.loads((self.root/'photos'/'.photo-journal.json').read_text())[request]['result']
                self.assertEqual((record['code'], record['stage']), ('camera_timeout', expected))
                calls = list(sdk.calls)
                self.assertEqual(camera.take_photo(request, generation=1), result)
                self.assertEqual(sdk.calls, calls)
                self.assertNotIn('private', repr(record))

    def test_legacy_timeout_is_not_relabelled_as_focus_or_still(self):
        old = dict(request_id='old_failure', status='failed', code='camera_timeout',
                   simulated=False, executed=True, completed=False, completion='capture_attempted')
        result = PhotoResult(**old)
        self.assertEqual(result.stage, 'unknown')
        self.assertEqual(photo_diagnostic(old)['stage'], 'unknown')

    def test_current_still_exposure_is_not_rejected_for_png_and_preview_restore_latency(self):
        camera, sdk = self.camera('never_timeout', photo_timeout=1.5)
        camera.set_preview(True, generation=1)
        camera.poll()  # Establish the live SensorTimestamp/host clock mapping.
        self.clock.now += 1.
        sdk.sensor += 900_000_000  # Fake SDK adds the remaining 100 ms on capture.
        original = sdk.wait
        def encoded_result(job, **options):
            result = original(job, **options)
            if job[0] == 'photo':
                self.clock.now += .8  # PNG encoding + preview restore, within SDK deadline.
            return result
        sdk.wait = encoded_result
        result = camera.take_photo('current_exposure_slow_encoding', generation=1)
        self.assertTrue(result.completed, result)
        self.assertEqual(result.code, 'photo_saved')
        self.assertTrue(camera.photo_bytes(result.photo_id))

    def test_exposure_from_before_current_capture_is_still_rejected(self):
        camera, sdk = self.camera('never_timeout')
        camera.set_preview(True, generation=1)
        camera.poll()
        self.clock.now += 1.
        # Old sensor time cannot be made current by a later successful Job.
        result = camera.take_photo('old_exposure', generation=1)
        self.assertEqual((result.code, result.completed), ('stale_photo', False))
        self.assertEqual(list((self.root/'photos').glob('*.png')), [])

    def test_diagnostic_projection_does_not_publish_unknown_codes_paths_or_exceptions(self):
        projected = photo_diagnostic(dict(stage='/home/private/photo.png',
            code='private_error_123', status='failed', exception='private SDK text',
            completed=False, request_id='not_for_display'))
        self.assertEqual(projected, dict(stage='unknown', code='photo_failed',
            status='failed', saved=False, automatic_retry=False))

    def host_photo(self, *, player):
        client = FeatureSimulatedClient()
        voice = IntegrationVoice(integration_config(), client, simulate=True,
            emit=lambda *_a, **_kw: None, player=player)
        host = FeatureHost(demo_assets=True, client=client, voice=voice, clock=self.clock)
        self.addCleanup(host.close)
        host.execute(HostIntent('take_photo'), request_id='current_photo')
        host.poll()
        state = host.snapshot()
        host.control(HostControl('photo_preview_ready', dict(generation=state['mode_generation'],
            request_id=state['photo_request_id'], preview_token=state['photo_preview_token'])), 'shown')
        self.clock.now += 5.1
        return host, client

    def finish(self, host):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            host.heartbeat(); host.poll()
            if not host.session.busy and host.audio.owner is None:
                return host.snapshot()
            time.sleep(.002)
        self.fail('Owned voice worker did not finish')

    def test_success_feedback_failure_preserves_saved_photo_and_next_dialogue(self):
        def failed_player(*_a, **_kw):
            raise RuntimeError('private player detail')
        host, client = self.host_photo(player=failed_player)
        state = self.finish(host)
        self.assertEqual(state['photo_phase'], 'photo_review')
        self.assertTrue(state['photo']['completed'])
        self.assertTrue(state['photo_diagnostic']['saved'])
        self.assertEqual(state['photo_feedback'], dict(status='failed', code='voice_failed'))
        self.assertEqual(len(list(Path(host.camera.store.root).glob('*.png'))), 1)
        self.assertEqual(client.requests, [])  # Feedback never calls the brain.
        host.control(HostControl('repeat'), 'repeat_after_failed_feedback')
        self.finish(host)
        self.assertEqual(len(list(Path(host.camera.store.root).glob('*.png'))), 1)
        host.control(HostControl('input', dict(key='TALK', pressed=True)), 'back')
        host.session.voice._player = lambda _cmd, on_start, **_kw: on_start()
        self.assertTrue(host.start_question('An offline question.'))
        self.finish(host)
        self.assertEqual(host.modes.mode, 'conversation')

    def test_review_read_failure_preserves_file_and_stage_without_success_voice(self):
        played = []
        host, client = self.host_photo(player=lambda *_a, **_kw: played.append(True))
        with patch.object(host.camera, 'photo_bytes', side_effect=OSError('private path')):
            state = self.finish(host)
        self.assertEqual(state['photo_phase'], 'failed')
        self.assertEqual(state['photo_diagnostic']['stage'], 'review')
        self.assertEqual(state['photo_diagnostic']['code'], 'photo_review_unavailable')
        self.assertTrue(state['photo_diagnostic']['saved'])
        self.assertEqual(len(list(Path(host.camera.store.root).glob('*.png'))), 1)
        self.assertFalse(state['photo']['completed'])
        self.assertEqual(played, [])
        self.assertEqual(client.requests, [])


if __name__ == '__main__':
    unittest.main()
