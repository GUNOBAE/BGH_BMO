"""Actual Host mode/gaze/GP8 flow with injected local position and voice IO."""
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class TrackingHostFlowTests(unittest.TestCase):
    def setUp(self):
        self.now = 1.
        for name in ('socket.socket.connect', 'subprocess.Popen', 'bmo_ai.cloud.create_client',
                     'bmo_ai.audio.record_wav', 'bmo_ai.audio.transcribe',
                     'bmo_ai.fish_tts.synthesize_wav', 'bmo_ai.local_piper.synthesize_wav'):
            self.enterContext(patch(name, side_effect=AssertionError('physical/provider IO forbidden')))
        self.client = FeatureSimulatedClient()
        voice = IntegrationVoice(integration_config(), self.client, simulate=True,
                                 emit=lambda *_a, **_kw: None,
                                 player=lambda _cmd, on_start, cancel_check, **_kw:
                                     (cancel_check(), on_start(), cancel_check()))
        self.host = FeatureHost(demo_assets=True, clock=lambda: self.now,
                                client=self.client, voice=voice, photo_feedback=False)
        self.addCleanup(self.host.close)
        self.serial = 0

    def execute(self, name):
        self.serial += 1
        result = self.host.execute(HostIntent(name), request_id=f'tracking_flow_{self.serial}')
        self.host.heartbeat()
        self.host.poll()
        return result

    def poll(self, seconds=.11):
        self.now += seconds
        self.host.heartbeat()
        self.host.poll()
        return self.host.snapshot()

    def prepare_photo(self):
        self.execute('take_photo')
        state = self.poll()
        self.assertEqual(state['mode'], 'photo')
        self.assertFalse(state['camera']['tracking'])
        self.assertTrue(state['camera']['tracking_requested'])
        self.assertIn('photo', state['camera']['tracking_suspended'])
        self.host.control(HostControl('photo_preview_ready', {
            'generation':state['mode_generation'], 'request_id':state['photo_request_id'],
            'preview_token':state['photo_preview_token']}), f'tracking_photo_visible_{self.serial}')
        return state

    def finish_photo(self):
        state = self.poll(5.01)
        deadline = time.monotonic() + 2
        while state['photo_phase'] != 'photo_review' and time.monotonic() < deadline:
            state = self.poll(.01)
            time.sleep(.001)
        self.assertEqual(state['photo_phase'], 'photo_review')
        self.assertTrue(state['photo']['completed'])
        return state

    def test_menu_tracking_photo_review_gp8_restores_only_prior_tracking(self):
        self.execute('start_face_tracking')
        state = self.poll()
        self.assertTrue(state['camera']['tracking'])
        self.assertFalse(state['camera']['preview'])
        self.assertEqual(state['gaze']['owner'], 'camera')
        brain = self.host.session.brain
        self.prepare_photo()
        self.finish_photo()
        self.assertTrue(self.host.ptt_button())
        state = self.poll()
        self.assertEqual(state['mode'], 'conversation')
        self.assertTrue(state['camera']['tracking'])
        self.assertFalse(state['camera']['preview'])
        self.assertIs(self.host.session.brain, brain)
        self.assertEqual(self.client.requests, [])

    def test_stop_tracking_during_photo_cannot_be_revived_by_review_back(self):
        self.execute('start_face_tracking')
        self.prepare_photo()
        self.execute('stop_face_tracking')
        state = self.poll()
        self.assertEqual(state['mode'], 'photo')
        self.assertTrue(state['camera']['preview'])
        self.assertFalse(state['camera']['tracking_requested'])
        self.finish_photo()
        self.assertTrue(self.host.ptt_button())
        state = self.poll()
        self.assertEqual(state['mode'], 'conversation')
        self.assertFalse(state['camera']['on'])
        self.assertFalse(state['camera']['tracking'])
        self.assertEqual(self.client.requests, [])

    def test_stop_tracking_preserves_preview_camera_off_clears_every_consumer(self):
        self.execute('start_face_tracking')
        self.host.control(HostControl('preview', True), 'tracking_preview_on')
        state = self.poll()
        self.assertTrue(state['camera']['preview'])
        self.execute('stop_face_tracking')
        state = self.poll()
        self.assertTrue(state['camera']['on'])
        self.assertTrue(state['camera']['preview'])
        self.assertFalse(state['camera']['tracking'])
        self.host.control(HostControl('camera_off'), 'tracking_camera_off')
        state = self.poll()
        self.assertFalse(state['camera']['on'])
        self.assertFalse(state['camera']['preview'])
        self.assertFalse(state['camera']['tracking_requested'])
        self.assertEqual(state['gaze']['owner'], 'idle')

    def test_actual_target_yields_to_native_activity_and_recovers_without_new_capture(self):
        self.execute('start_face_tracking')
        state = self.poll()
        self.assertEqual(state['gaze']['owner'], 'camera')
        generation = state['mode_generation']
        for phase in ('listening', 'thinking'):
            gaze = self.host.gaze.resolve(phase=phase, generation=generation)
            self.assertEqual(gaze['owner'], 'native')
            self.assertFalse(gaze['apply'])
        self.assertEqual(self.host.gaze.resolve(phase='idle', generation=generation)['owner'], 'camera')
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.host.camera.broker._backend.photo_count, 0)


if __name__ == '__main__':
    unittest.main()
