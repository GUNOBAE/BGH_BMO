"""Production policy + injected SDK double: no actual camera/API/serial process."""
import hashlib
from pathlib import Path
import tempfile
import time
from threading import Event
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

    def finish_dialogue(self, host):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.poll(host)
            if not host.session.busy and host.audio.owner is None:
                return
            time.sleep(.002)
        self.fail('dialogue did not finish')

    def present_photo_preview(self, host):
        # Model the product image-load acknowledgement, not a PID/open sensor.
        state = self.poll(host)
        for _ in range(4):
            if state['camera']['preview_available']:
                break
            self.clock.now += .11
            self.sdk.sensor += 110_000_000
            state = self.poll(host)
        self.assertEqual(state['photo_phase'], 'preparing')
        self.assertIsNone(state['photo_countdown'])
        self.assertTrue(state['camera']['preview_available'])
        self.assertIsNotNone(host.preview_bytes())
        result = host.control(HostControl('photo_preview_ready', {
            'generation': state['mode_generation'],
            'request_id': state['photo_request_id'],
            'preview_token': state['photo_preview_token'],
        }), 'visible_preview_' + state['photo_request_id'])
        self.assertNotEqual(result.status, 'rejected')
        self.assertEqual(host._photo_phase, 'countdown')
        self.assertAlmostEqual(host._pending_photo['deadline'] - self.clock(), 5.)

    def finish_photo(self, host):
        self.present_photo_preview(host)
        self.clock.now += 5.1
        self.sdk.sensor += 5_100_000_000
        self.poll(host)
        self.finish_dialogue(host)
        return host.snapshot()

    def test_spoken_photo_after_reply_uses_one_existing_real_camera_and_repeat_does_not_retake(self):
        host = self.host(video=False)
        self.assertTrue(host.start_question('Take my picture please.'))
        self.finish_dialogue(host)
        self.assertEqual(host.modes.mode, 'photo')
        self.assertTrue(host._last_result['request_id'].startswith('spoken_photo_'))
        self.factory.assert_called_once()
        state = self.finish_photo(host)
        photo_id = state['photo']['photo_id']
        self.assertEqual(state['photo']['status'], 'succeeded')
        self.assertFalse(state['photo']['simulated'])
        self.assertEqual(state['mode'], 'photo')
        self.assertEqual(state['photo_phase'], 'photo_review')
        self.assertFalse(state['camera']['on'])
        photos_before = list((self.root/'photos').glob('*.png'))
        previous_turn = host.session.snapshot()['turn_id']
        host.control(HostControl('repeat'), 'spoken_photo_repeat')
        self.finish_dialogue(host)
        self.assertNotEqual(host.session.snapshot()['turn_id'], previous_turn)
        self.clock.now += 4
        self.poll(host)
        self.assertEqual(list((self.root/'photos').glob('*.png')), photos_before)
        self.assertIsNotNone(host.photo_bytes(photo_id))
        self.assertEqual(host.snapshot()['photo_phase'], 'photo_review')

    def test_korean_spoken_photo_review_back_restores_tracking_and_next_dialogue(self):
        host = self.host(video=False)
        host.execute(HostIntent('start_face_tracking'), request_id='human_tracking')
        self.assertTrue(host.start_question('사진 찍어줘.'))
        self.finish_dialogue(host)
        self.assertEqual(host.modes.mode, 'photo')
        state = self.finish_photo(host)
        self.assertEqual(state['photo']['status'], 'succeeded')
        self.assertEqual(state['mode'], 'photo')
        self.assertEqual(state['photo_phase'], 'photo_review')
        self.assertFalse(state['camera']['on'])
        self.assertFalse(state['camera']['tracking'])
        self.assertTrue(host.ptt_button())  # GP8 review action returns, never records.
        state = self.poll(host)
        self.assertEqual(state['mode'], 'conversation')
        self.assertTrue(state['camera']['tracking'])
        self.assertFalse(state['camera']['preview'])
        photos_before = list((self.root/'photos').glob('*.png'))
        self.assertTrue(host.start_question('What is two plus two?'))
        self.finish_dialogue(host)
        self.clock.now += 4
        self.poll(host)
        self.assertEqual(list((self.root/'photos').glob('*.png')), photos_before)

    def test_model_photo_after_nonrequest_still_cannot_open_camera(self):
        host = self.host(video=False)
        host.session.expression_client.responses._responses.replies.append(dict(
            version=1, speech='A fixture answer.', emotion='neutral', gesture='none',
            intent={'name':'take_photo','target':None}))
        self.assertTrue(host.start_question('Do not take my picture.'))
        self.finish_dialogue(host)
        self.assertEqual(host._last_result['code'], 'camera_human_action_required')
        self.factory.assert_not_called()

    def test_failed_spoken_capture_has_no_success_feedback_and_recovers_without_a_retry(self):
        host = self.host(video=False)
        self.assertTrue(host.start_question('사진 찍어줘.'))
        self.finish_dialogue(host)
        self.present_photo_preview(host)
        before_feedback = host.session.worker.turn
        self.sdk.fail = 'still'
        self.clock.now += 5.1
        self.sdk.sensor += 5_100_000_000
        state = self.poll(host)
        self.assertEqual(state['photo']['status'], 'failed')
        self.assertEqual(state['mode'], 'photo')
        self.assertEqual(state['photo_phase'], 'failed')
        self.assertEqual(host.session.worker.turn, before_feedback)
        host.execute(HostIntent('return_to_face'), request_id='failed_photo_back')
        self.assertEqual(host.modes.mode, 'conversation')
        self.assertFalse(state['camera']['on'])
        self.clock.now += 4
        self.poll(host)
        self.assertEqual(self.sdk.calls.count('still'), 1)

    def test_cancelled_spoken_photo_cannot_capture_or_authorize_a_later_model_photo(self):
        host = self.host(video=False)
        started = Event()
        original_player = host.session.voice._player
        def player(_command, *, on_start, cancel_check, **_options):
            # Prepared audio has not started; cancellation must not promote
            # the already-resolved human photo command to an action.
            started.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                cancel_check()
                time.sleep(.002)
            raise AssertionError('cancel did not retire spoken photo response')
        host.session.voice._player = player
        self.assertTrue(host.start_question('사진 찍어줘.'))
        self.assertTrue(started.wait(2))
        self.poll(host)
        host.cancel_conversation()
        self.finish_dialogue(host)
        self.factory.assert_not_called()
        host.session.voice._player = original_player
        host.session.expression_client.responses._responses.replies.append(dict(
            version=1, speech='Two plus two is four.', emotion='neutral', gesture='none',
            intent={'name':'take_photo','target':None}))
        self.assertTrue(host.start_question('What is two plus two?'))
        self.finish_dialogue(host)
        self.assertEqual(host._last_result['code'], 'camera_human_action_required')
        self.factory.assert_not_called()

    def test_spoken_photo_countdown_can_be_cancelled_before_capture(self):
        host = self.host(video=False)
        self.assertTrue(host.start_question('사진 한 장 찍어줘.'))
        self.finish_dialogue(host)
        self.assertEqual(host.modes.mode, 'photo')
        host.control(HostControl('cancel'), 'spoken_photo_cancel')
        self.clock.now += 5.1
        self.poll(host)
        self.assertEqual(host.modes.mode, 'conversation')
        self.assertFalse(host.camera.snapshot()['on'])
        self.assertNotIn('still', self.sdk.calls)
        self.assertFalse((self.root/'photos').exists())

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
        self.assertEqual(state['system_diagnostics']['arms']['source']['readiness'],'BLOCKED')
        self.assertFalse(state['system_diagnostics']['arms']['physical_verified'])
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
        state=self.finish_photo(host)
        photo=state['photo']
        self.assertEqual(state['mode'], 'photo')  # Existing UI result/review flow.
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
        self.poll(host)
        self.sdk.fail='close'
        host.close()
        report=host.cleanup_snapshot()
        self.assertFalse(report['resources_cleaned'])
        self.assertTrue(report['camera']['close_unknown'])
        self.assertFalse(report['physical_release_verified'])


if __name__=='__main__':
    unittest.main()
