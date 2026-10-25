"""Production policy + injected SDK double: no actual camera/API/serial process."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.camera.live import LivePicamera2Backend
from bmo_ai.features.camera.live_codec import validate_rgb_avi
from bmo_ai.features.camera.live_video import LIVE_CONTRACT, LiveVideoRecorder
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from test_feature_camera_adapters import Clock, FakeCamera


class SupervisedCameraHostTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock=Clock()
        self.sdk=FakeCamera(self.clock)
        self.factory=self.enterContext(patch('bmo_ai.features.camera.backends._picamera_factory',return_value=self.sdk))
        # SDK frame timestamp progresses consistently with the fake host clock.
        original=self.sdk.wait
        def wait(job, **options):
            result=original(job,**options)
            if type(result) is dict and 'SensorTimestamp' in result:
                self.clock.now+=.1
            return result
        self.sdk.wait=wait
        self.hosts=[]
        self.enterContext(patch.object(LivePicamera2Backend,'_close_uncertain',False))
        self.enterContext(patch('bmo_ai.features.camera.live.probe_camera_capability',return_value={
            'backend':'picamera2','available':True,'code':'camera_backend_available',
            'modules':{},'opened':False,'physical_tested':False,'tracking_available':False,
            'activation':'explicit_supervised_opt_in'}))
        for target in ('subprocess.Popen','socket.socket.connect','socket.socket.connect_ex',
                       'bmo_ai.audio.record_wav','bmo_ai.audio.transcribe',
                       'bmo_ai.fish_tts.synthesize_wav','bmo_ai.local_piper.synthesize_wav',
                       'bmo_ai.private_credentials.resolve_credentials','bmo_ai.cloud.create_client'):
            self.enterContext(patch(target,side_effect=AssertionError('real host IO forbidden')))

    def tearDown(self):
        for host in self.hosts:
            if not host._closed:
                host.close()

    def host(self, *, video=True, **settings):
        config=integration_config()
        client=FeatureSimulatedClient()
        voice=IntegrationVoice(config,client,simulate=True,emit=lambda *a,**k:None,
            player=lambda _cmd, on_start, cancel_check, **_kw: (cancel_check(),on_start(),cancel_check()))
        host=FeatureHost(config=config,client=client,voice=voice,clock=self.clock,
            runtime=RuntimePolicy(simulate=False),camera_supervised=True,
            photo_directory=self.root/'photos',camera_video_supervised=video,
            video_directory=self.root/'videos' if video else None,**settings)
        self.hosts.append(host)
        return host

    def poll(self,host):
        host.heartbeat()
        host.poll()
        return host.snapshot()

    def test_production_flags_construct_one_lazy_real_broker_and_live_recorder(self):
        host=self.host()
        self.assertFalse(host.runtime.simulate)
        self.assertFalse(host.camera.broker.simulated)
        self.assertIsInstance(host.video,LiveVideoRecorder)
        self.assertIs(host.video.broker,host.camera.broker)
        self.factory.assert_not_called()
        self.assertFalse((self.root/'photos').exists())
        self.assertFalse((self.root/'videos').exists())
        state=self.poll(host)
        self.assertEqual(state['capabilities']['camera'],'explicit_supervised')
        self.assertEqual(state['capabilities']['camera_video'],'explicit_supervised')
        self.assertEqual(state['system_diagnostics']['arms']['readiness'],'BLOCKED')
        self.assertEqual(state['capabilities']['arms'],'denied')

    def test_simulate_cannot_be_combined_with_physical_camera_flags(self):
        with self.assertRaises(FeatureError):
            FeatureHost(camera_supervised=True,photo_directory=self.root/'photos')
        self.factory.assert_not_called()

    def test_live_camera_requires_private_destination_and_explicit_flag(self):
        for options in ({'camera_supervised':True},
                        {'photo_directory':self.root/'photos'},
                        {'camera_video_supervised':True,'video_directory':self.root/'videos'}):
            with self.subTest(options=options),self.assertRaises(FeatureError):
                FeatureHost(runtime=RuntimePolicy(simulate=False),client=FeatureSimulatedClient(),**options)
        self.factory.assert_not_called()

    def test_model_photo_and_tracking_intents_never_open_camera(self):
        host=self.host()
        for name in ('take_photo','start_face_tracking'):
            result=host.execute(HostIntent(name),request_id='model_'+name)
            self.assertEqual((result.status,result.code),('rejected','camera_human_action_required'))
        self.factory.assert_not_called()
        self.assertEqual(host.modes.mode,'conversation')

    def test_human_photo_result_and_download_preserve_real_flags(self):
        host=self.host()
        result=host.execute(HostIntent('take_photo'),request_id='human_photo')
        self.assertEqual(result.status,'running')
        self.assertFalse(result.simulated)
        self.clock.now+=3.1
        state=self.poll(host)
        photo=state['photo']
        self.assertEqual(photo['status'],'succeeded')
        self.assertFalse(photo['simulated'])
        self.assertTrue(photo['executed'])
        self.assertTrue(photo['completed'])
        raw=host.photo_bytes(photo['photo_id'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),photo['sha256'])
        self.assertIsNone(host.photo_bytes('photo_'+'f'*32))
        self.factory.assert_called_once()

    def test_human_preview_result_is_not_labelled_simulated(self):
        host=self.host(video=False)
        result=host.control(HostControl('preview',True),'human_preview')
        self.assertEqual(result.status,'succeeded')
        self.assertFalse(result.simulated)
        self.assertTrue(result.executed)
        self.assertTrue(host.camera.broker.opened)
        host.control(HostControl('preview',False),'preview_off')
        self.assertFalse(host.camera.broker.opened)

    def test_human_video_download_and_completion_receipts_are_real(self):
        host=self.host()
        started=host.control(HostControl('video_start',{'consent':True}),'human_video')
        self.assertEqual(started.status,'running')
        self.assertFalse(started.simulated)
        self.clock.now+=.5
        self.poll(host)
        stopped=host.control(HostControl('video_stop'),'human_stop')
        self.assertEqual(stopped.status,'succeeded')
        self.assertTrue(stopped.executed)
        self.assertTrue(stopped.completed)
        self.assertFalse(stopped.simulated)
        result=host.video.snapshot()['result']
        self.assertEqual(result['contract'],LIVE_CONTRACT)
        raw=host.video_bytes(result['video_id'])
        self.assertIsNotNone(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),result['sha256'])
        self.assertEqual(validate_rgb_avi(raw)['width'],320)
        self.assertIsNone(host.video_bytes('video_'+'f'*32))

    def test_auto_video_stop_updates_real_receipt_without_synthetic_result_decoder(self):
        host=self.host()
        host.queue_command('auto-start',HostControl('video_start',{'consent':True}))
        self.poll(host)
        self.clock.now+=12
        state=self.poll(host)
        self.assertFalse(state['camera_video']['recording'])
        self.assertEqual(state['last_result']['status'],'succeeded')
        self.assertFalse(state['last_result']['simulated'])
        self.assertTrue(state['last_result']['executed'])

    def test_missing_recording_consent_never_opens_sensor(self):
        host=self.host()
        result=host.control(HostControl('video_start',{'consent':False}),'human_no')
        self.assertEqual((result.status,result.code),('rejected','video_consent_required'))
        self.factory.assert_not_called()

    def test_sdk_close_unknown_propagates_to_host_cleanup_report(self):
        host=self.host(video=False)
        host.execute(HostIntent('take_photo'),request_id='human_close')
        self.clock.now+=3.1
        self.poll(host)
        self.sdk.fail='close'
        host.close()
        report=host.cleanup_snapshot()
        self.assertFalse(report['resources_cleaned'])
        self.assertTrue(report['camera']['close_unknown'])
        self.assertFalse(report['physical_release_verified'])


if __name__=='__main__':
    unittest.main()
