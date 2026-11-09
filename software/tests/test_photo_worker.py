"""A blocked injected capture cannot block owner controls or start a second turn."""
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.camera import CameraFeature, DemoBackend, PhotoStore
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent


class PhotoWorkerTests(unittest.TestCase):
    def test_live_pipeline_capture_is_off_owner_and_back_waits_for_cancelled_worker(self):
        entered, release = Event(), Event()
        backend = DemoBackend(size=(16, 16))
        original = backend.capture_photo
        def blocked():
            entered.set()
            if not release.wait(3):
                raise AssertionError('Injected capture gate was not released')
            return original()
        backend.capture_photo = blocked
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        camera = CameraFeature(backend_factory=lambda: backend, store=PhotoStore(root/'photos'),
                               asynchronous_preview=True)
        host = FeatureHost(demo_assets=True, camera=camera, client=FeatureSimulatedClient())
        self.addCleanup(host.close)
        self.addCleanup(release.set)
        for name in ('subprocess.Popen', 'socket.socket.connect', 'bmo_ai.fish_tts.synthesize_wav'):
            self.enterContext(patch(name, side_effect=AssertionError('Device/provider IO forbidden')))
        host.execute(HostIntent('take_photo'), request_id='slow_current_photo')
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            host.heartbeat();host.poll();state=host.snapshot()
            if state['camera']['preview_available']:break
            time.sleep(.005)
        self.assertTrue(state['camera']['preview_available'])
        host.control(HostControl('photo_preview_ready', dict(generation=state['mode_generation'],
            request_id=state['photo_request_id'],preview_token=state['photo_preview_token'])), 'visible')
        host._pending_photo['deadline'] = host.clock()  # The 5-second UI path is tested separately.
        host.poll()  # Must return while the capture is still gated.
        self.assertTrue(entered.wait(1))
        self.assertEqual(host.snapshot()['photo_phase'], 'capturing')
        host.queue_command('back_while_capture_pending', HostIntent('return_to_face'))
        host.heartbeat();host.poll()
        self.assertTrue(host._photo_back_pending)
        self.assertEqual(host.modes.mode, 'photo')
        self.assertFalse(host.start_question('Must wait for the owned camera operation.'))
        release.set()
        deadline = time.monotonic()+2
        while time.monotonic()<deadline and host.modes.mode!='conversation':
            host.heartbeat();host.poll();time.sleep(.005)
        self.assertEqual(host.modes.mode, 'conversation')
        self.assertIsNone(host._photo_job)
        self.assertFalse(camera.broker.opened)
        self.assertEqual(list((root/'photos').glob('*.png')), [])
        self.assertEqual(host.client.requests, [])
