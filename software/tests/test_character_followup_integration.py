import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.character_review import CharacterReview, main as review_main
from bmo_ai.desktop_app import main as desktop_main
from bmo_ai.desktop_install import desktop_install_plan
from bmo_ai.features.host import FeatureHost
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config


class FakeBank:
    config=integration_config()
    def inventory(self):
        return [dict(clip_id='boot_01',model='s2.1-pro-free',status='LISTENING_NOT_APPROVED'),
                dict(clip_id='reaction_11',model='s2.1-pro-free',status='LISTENING_NOT_APPROVED')]


class FollowupIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        for target in ('subprocess.Popen','socket.socket.connect','bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(target,side_effect=AssertionError('No real devices/network/keys')))

    def test_new_vad_cli_dry_run_preserves_baseline_and_opens_no_devices(self):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=desktop_main(['--live','--character-life','--ptt-microphone','--ptt-vad-mode','webrtc','--dry-run'])
        result=json.loads(output.getvalue())
        self.assertEqual(code,0);self.assertFalse(result['credentials_accessed'])
        self.assertEqual(result['character_life']['ptt_vad_mode'],'webrtc')
        self.assertEqual(result['character_life']['ptt_vad_profile'],'baseline')

    def test_production_host_forwards_explicit_mode_without_a_capture(self):
        opened=[]
        def source():opened.append(1);raise AssertionError('No mic')
        host=FeatureHost(runtime=RuntimePolicy(simulate=True,conversation_ptt_microphone=True),
            character_life=True,ptt_vad_mode='webrtc',ptt_source_factory=source)
        try:
            self.assertEqual(host.microphone.snapshot()['vad_mode'],'webrtc')
            self.assertEqual(host.snapshot()['character_life']['ptt_vad_mode'],'webrtc')
            self.assertEqual(opened,[])
        finally:host.close()

    def test_install_plan_keeps_mode_without_installing(self):
        result=desktop_install_plan(home=self.root,environ={'HOME':str(self.root)},simulate=True,
            character_options={'character_life':True,'ptt_vad_mode':'webrtc'})
        self.assertIn('--ptt-vad-mode',result['command']);self.assertIn('webrtc',result['command'])
        self.assertEqual(result['files_written'],0)

    def test_review_plan_never_auditions_and_manual_audition_has_a_hard_limit(self):
        calls=[]
        class Player:
            def play(self,clip,**kwargs):calls.append((clip,kwargs));return {'completed':True}
            def close(self):return True
        review=CharacterReview(FakeBank(),player=Player())
        self.assertEqual(review.plan()['mode'],'PLAN_ONLY');self.assertEqual(calls,[])
        self.assertEqual(review.action('play UNKNOWN')['status'],'SAMPLE_UNAVAILABLE')
        self.assertEqual(review.action('play boot_01')['approved'],False)
        review.action('play reaction_11')
        self.assertEqual(review.action('play boot_01')['status'],'LIMIT_REACHED')
        self.assertEqual(len(calls),2)
        self.assertTrue(all(x[1]['allow_unapproved'] for x in calls));self.assertTrue(review.close())

    def test_operator_record_and_wake_are_local_bounded_and_not_accuracy_approval(self):
        manifest=self.root/'manifest.json';manifest.write_text('{}')
        captured=[];inferred=[]
        def capture(directory,**kwargs):
            captured.append(kwargs)
            return dict(status='OFFLINE_CAPTURE_PASS',wav={'file':'mic-normal-fixture.wav','decoded':True},
                pcm={'stream_seconds':1.},resources_cleaned=True,ptt_status={'outcome':'FIXTURE'})
        def infer(m,path):
            inferred.append((m,path));return dict(status='OPERATIONAL_INFERENCE_PASS',peak_score=.8,
                activations=1,supported_phrases=['Hey Beemo'],unverified_phrases=['헤이 비모'],
                execution_providers=['CPUExecutionProvider'])
        review=CharacterReview(FakeBank(),manifest=manifest,directory=self.root,capture=capture,infer=infer)
        self.assertEqual(review.action('infer')['status'],'NO_OPERATOR_RECORDING')
        result=review.action('wake')
        self.assertEqual(len(captured),1);self.assertEqual(len(inferred),1)
        self.assertEqual(captured[0]['vad_mode'],'webrtc');self.assertEqual(captured[0]['max_seconds'],10.)
        self.assertEqual(result['wake']['human_accuracy'],'OPERATOR_REVIEW_REQUIRED')
        review.action('record');review.action('record')
        self.assertEqual(review.action('record')['status'],'LIMIT_REACHED')
        self.assertEqual(len(captured),3)

    def test_missing_model_blocks_only_wake_not_other_review_actions(self):
        review=CharacterReview(FakeBank())
        self.assertEqual(review.action('wake')['status'],'MODEL_MISSING')
        self.assertEqual(review.action('record')['status'],'PRIVATE_DIRECTORY_REQUIRED')
        self.assertEqual(len(review.action('list')['samples']),2)

    def test_blocked_inference_preserves_safe_reason_and_normalizes_relative_record_directory(self):
        manifest=self.root/'manifest.json';manifest.write_text('{}')
        review=CharacterReview(FakeBank(),manifest=manifest,directory='private-review',
            infer=lambda *_:dict(status='BLOCKED',detector=dict(status='MODEL_INVALID',
                reason_code='wake_model_hash_mismatch')))
        self.assertTrue(review.directory.is_absolute())
        review.last_wav=review.directory/'mic-normal-fixture.wav'
        result=review.action('infer')
        self.assertEqual(result['reason_code'],'wake_model_hash_mismatch')
        self.assertEqual(result['detector_status'],'MODEL_INVALID')
        self.assertEqual(result['human_accuracy'],'NOT_RUN')

    def test_capture_cleanup_failure_blocks_next_device_action_and_exit_reports_failure(self):
        def capture(*_a,**_kw):
            return dict(status='MIC_DEVICE_NOT_VERIFIED',resources_cleaned=False)
        review=CharacterReview(FakeBank(),directory=self.root,capture=capture)
        self.assertFalse(review.action('record')['resources_cleaned'])
        self.assertEqual(review.action('record')['status'],'BLOCKED')
        self.assertEqual(review.action('play boot_01')['status'],'BLOCKED')
        self.assertFalse(review.close())
        output=[]
        with patch('bmo_ai.character_review.VoiceBank',return_value=FakeBank()), \
             patch.object(CharacterReview,'close',return_value=False):
            self.assertEqual(review_main([],write=output.append),2)
        self.assertEqual(json.loads(output[-1])['status'],'CLEANUP_FAILED')


if __name__=='__main__':unittest.main()
