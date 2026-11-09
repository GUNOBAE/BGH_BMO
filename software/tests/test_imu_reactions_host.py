"""Product IMU owner/presentation lifecycle with explicit injected IO boundaries.

The fake monitor tests admission rather than claiming sensor health. Physical
qualification and six-axis data validation are exercised by test_imu_relative.
No mock observation is installed into a user's profile or diagnostic store.
"""
from collections import deque
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureEvent
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.motion.legacy_host import LegacyMotionFeature
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.imu_reactions import IMUReactions
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav


BINDING={name:'a'*64 for name in ('firmware_sha256','helper_sha256','sensor_sha256','boot_sha256')}
BINDING['source_id']='injected_cdc_owner'


class Monitor:
    def __init__(self):
        self.enabled=False;self.ready=True;self.events=deque();self.observations=[];self.bindings=[]
    def set_enabled(self,value):self.enabled=value
    def bind(self,**binding):self.bindings.append(binding)
    def ingest(self,sample,**context):
        self.observations.append((sample,context));return sample.get('events',())
    def poll(self):
        result=tuple(self.events);self.events.clear();return result
    def snapshot(self):
        return dict(health='degraded',physical_verified=False,usable_for_control=False,
            current_capabilities=dict(relative_motion=self.ready,directional_gaze=False))
    def close(self):self.enabled=False


class Transport:
    def __init__(self):
        self.imu_binding=dict(BINDING);self.configured=[];self.samples=[];self.closed=0;self.fail=False
    def configure_imu(self,value,**options):self.configured.append(value)
    def poll_imu(self):
        if self.fail:raise ValueError('injected private device error')
    def drain_imu(self):
        result,self.samples=self.samples,[];return result
    def imu_transport_snapshot(self):return dict(code='imu_snapshot_received')
    def close(self):self.closed+=1


def motion(episode='episode_1',generation=3,age=0):
    return FeatureEvent('imu_motion',generation,code='relative_motion',data=dict(
        episode_id=episode,age_seconds=age,relative_signal_validated=True,sensor_health_verified=False))


def still(episode='episode_1',generation=3):
    return FeatureEvent('imu_still',generation,code='relative_still',data=dict(episode_id=episode))


class ReactionTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(TemporaryDirectory()))
        self.now=0.;self.monitor=Monitor();self.transport=Transport();self.notices=[];self.cancellations=0
        self.face=dict(emotion='happy',emotion_mode='auto',phase='idle',playback='idle',turn_id='baseline')
        self.host=SimpleNamespace(clock=lambda:self.now,runtime=SimpleNamespace(simulate=False),
            modes=SimpleNamespace(mode='conversation',generation=3,transitioning=False),
            audio=SimpleNamespace(owner=None),preferences=None,_local_event_active=None,
            session=SimpleNamespace(busy=False,snapshot=lambda:dict(self.face)),
            motion=SimpleNamespace(snapshot=lambda:dict(active=False)),
            volume=SimpleNamespace(snapshot=lambda:dict(effective_level=.25)))
        self.host._cached_event_available=lambda _event:False
        self.host._start_owned_notice=self.notice
        self.host.cancel_conversation=self.cancel
        self.factory=Mock(return_value=self.transport)
        self.reactions=IMUReactions(self.host,monitor=self.monitor,transport_factory=self.factory,
            profile_directory=self.root/'imu')
        self.addCleanup(self.reactions.close)
        self.reactions.set_enabled(True,persist=False)
        self.available=False

    def notice(self,event,text,**settings):
        self.notices.append((event,text,settings))
        if not self.available:return False
        self.host.session.busy=True;self.host.audio.owner='imu'
        self.host._local_event_active=dict(event=event,scope='imu')
        self.face.update(turn_id='cached_imu',phase='thinking',playback='prepared')
        return True

    def cancel(self):
        self.cancellations+=1
        self.host.session.busy=False;self.host.audio.owner=None;self.host._local_event_active=None
        self.face.update(phase='idle',playback='cancelled')

    def test_missing_asset_is_face_only_and_still_restores_exact_baseline(self):
        self.reactions._event(motion())
        self.assertEqual(self.reactions.project(self.face)['emotion'],'surprised')
        self.assertEqual(self.face['emotion'],'happy')
        self.assertEqual(self.host.audio.owner,None)
        self.assertEqual(self.reactions.suppression,'face_only_assets_pending')
        self.assertTrue(all(options['cache_only'] and text=='' and options['owner']=='imu'
            for _,text,options in self.notices))
        self.reactions._event(still())
        self.assertEqual(self.reactions.project(self.face)['emotion'],'happy')

    def test_no_interruption_of_music_video_game_ptt_reply_menu_mute_or_sensitive_context(self):
        changes=(('music','foreground_activity'),('video','foreground_activity'),('photo','foreground_activity'),
                 ('game','foreground_activity'),('game_menu','foreground_activity'))
        for index,(mode,reason) in enumerate(changes):
            with self.subTest(mode=mode):
                self.host.modes.mode=mode;self.reactions._event(motion(str(index)))
                self.assertIsNone(self.reactions.active);self.assertEqual(self.reactions.suppression,reason)
        self.host.modes.mode='conversation'
        for phase in ('listening','thinking'):
            self.face['phase']=phase;self.reactions._event(motion(phase))
            self.assertIsNone(self.reactions.active);self.assertEqual(self.reactions.suppression,'input_active')
        self.face['phase']='idle';self.host.session.busy=True
        self.reactions._event(motion('reply'));self.assertEqual(self.reactions.suppression,'conversation_active')
        self.host.session.busy=False;self.host.audio.owner='music'
        self.reactions._event(motion('owner'));self.assertEqual(self.reactions.suppression,'audio_busy')
        self.host.audio.owner=None;self.reactions.presentation_allowed=False
        self.reactions._event(motion('menu'));self.assertEqual(self.reactions.suppression,'desktop_or_settings_active')
        self.reactions.presentation_allowed=True
        self.host.volume.snapshot=lambda:dict(effective_level=0)
        self.reactions._event(motion('mute'));self.assertEqual(self.reactions.suppression,'muted')
        self.host.volume.snapshot=lambda:dict(effective_level=.25)
        self.face['emotion']='crying'
        self.reactions._event(motion('serious'));self.assertEqual(self.reactions.suppression,'sensitive_context')
        self.assertEqual(self.notices,[])

    def test_continuous_episode_never_repeats_and_quota_is_rolling_not_queued(self):
        self.reactions._event(motion('first'));self.reactions._event(still('first'))
        self.now=31.;self.reactions._event(motion('first'))
        self.assertIsNone(self.reactions.active)
        self.reactions._event(motion('second'));self.reactions._event(still('second'))
        self.now=62.;self.reactions._event(motion('suppressed'))
        self.assertIsNone(self.reactions.active);self.assertEqual(len(self.reactions.started),2)
        self.now=301.;self.reactions._event(motion('suppressed'))
        self.assertIsNone(self.reactions.active)  # never replay a consumed burst
        self.reactions._event(motion('fresh'));self.assertIsNotNone(self.reactions.active)

    def test_stale_generation_age_and_old_still_cannot_replay_or_clear_current_reaction(self):
        self.reactions._event(motion('old',generation=2));self.reactions._event(motion('late',age=.8))
        self.assertEqual(self.notices,[])
        self.reactions._event(motion('current'))
        self.reactions._event(still('previous'));self.assertIsNotNone(self.reactions.active)
        self.reactions._event(still('current',generation=2));self.assertIsNotNone(self.reactions.active)
        self.reactions._event(still('current'));self.assertIsNone(self.reactions.active)

    def test_cached_face_waits_for_actual_player_start_and_next_input_cancels_owner(self):
        self.available=True;self.reactions._event(motion())
        self.assertEqual(self.reactions.project(self.face)['emotion'],'happy')
        self.face.update(phase='speaking',playback='started')
        self.assertEqual(self.reactions.project(self.face)['emotion'],'surprised')
        self.reactions.interrupt('new_question')
        self.assertEqual(self.cancellations,1);self.assertIsNone(self.host.audio.owner)
        self.assertEqual(self.reactions.project(self.face)['emotion'],'happy')
        self.assertTrue(all(value[2]['cache_only'] for value in self.notices))

    def test_factory_owned_transport_is_closed_but_current_legacy_owner_is_borrowed(self):
        self.reactions.poll();self.assertTrue(self.reactions.owned)
        self.factory.assert_called_once();self.reactions.set_enabled(False,persist=False)
        self.assertEqual(self.transport.closed,1)
        legacy=Mock(spec=LegacyMotionFeature)
        legacy.imu_binding=dict(BINDING);legacy.drain_imu.return_value=()
        legacy.snapshot.return_value=dict(active=False)
        self.host.motion=legacy;self.reactions.set_enabled(True,persist=False);self.reactions.poll()
        self.factory.assert_called_once();self.assertIs(self.reactions.transport,legacy)
        self.assertFalse(self.reactions.owned)
        self.reactions.set_enabled(False,persist=False)
        legacy.configure_imu.assert_called_with(False);legacy.close.assert_not_called()

    def test_fresh_samples_are_real_route_old_samples_drop_and_failures_only_retire_imu(self):
        self.transport.samples=[dict(received_at=-1.,controller_boot='old',sequence=1,timestamp_ms=1),
            dict(received_at=0.,controller_boot='new',sequence=2,timestamp_ms=2)]
        self.reactions.poll()
        self.assertEqual(len(self.monitor.observations),1)
        self.assertFalse(self.monitor.observations[0][1]['synthetic'])
        self.assertEqual(self.monitor.observations[0][0]['controller_boot'],'new')
        self.reactions._event(motion());self.transport.fail=True;self.reactions.poll()
        self.assertIsNone(self.reactions.active)
        self.assertEqual(self.transport.closed,1)
        self.assertEqual(self.host.modes.mode,'conversation')
        self.assertEqual(self.cancellations,0)  # no unrelated dialogue stop
        self.assertIsNone(self.host.audio.owner)
        self.reactions.poll();self.factory.assert_called_once()
        replacement=Transport();self.factory.return_value=replacement;self.now=6.
        self.reactions.poll();self.assertEqual(self.factory.call_count,2)

    def test_arm_and_audio_vibration_settling_is_local_and_does_not_enable_gaze(self):
        self.host.motion.snapshot=lambda:dict(active=True)
        self.reactions._event(motion());self.assertEqual(self.reactions.suppression,'arm_vibration')
        self.host.motion.snapshot=lambda:dict(active=False)
        self.now=1.;self.reactions._event(motion('settling'));self.assertEqual(self.reactions.suppression,'arm_settling')
        self.now=3.;self.host.audio.owner='conversation';self.reactions.poll()
        self.host.audio.owner=None;self.now=4.;self.reactions._event(motion('speaker'))
        self.assertEqual(self.reactions.suppression,'audio_settling')
        self.assertFalse(self.reactions.snapshot()['capabilities']['gaze'])


class HostRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory=Path(self.enterContext(TemporaryDirectory()))
        self.host=None;self.begin=Event();self.release=Event();self.prepared=Event();self.started=Event()
        for target in ('socket.socket.connect','subprocess.Popen','bmo_ai.cloud.create_client',
                       'bmo_ai.fish_tts.synthesize_wav','bmo_ai.audio.record_wav',
                       'bmo_ai.audio.transcribe','bmo_ai.local_piper.synthesize_wav'):
            self.enterContext(patch(target,side_effect=AssertionError('Real IO/provider forbidden')))
        self.client=FeatureSimulatedClient([dict(version=1,speech='A tiny answer.',emotion='neutral',
            gesture='none',intent=None)]);self.monitor=Monitor();self.transport=Transport()
        self.config=integration_config()
        def player(_command,*,on_start,cancel_check,**_options):
            self.prepared.set();deadline=time.monotonic()+4
            while not self.begin.wait(.002):
                cancel_check()
                if time.monotonic()>deadline:raise AssertionError('Player test gate expired')
            on_start();self.started.set()
            while not self.release.wait(.002):
                cancel_check()
                if time.monotonic()>deadline:raise AssertionError('Playback test gate expired')
            cancel_check()
        self.voice=IntegrationVoice(self.config,self.client,simulate=True,emit=lambda *_a,**_kw:None,player=player)
        self.voice_errors=[]
        speak=self.voice.speak
        def speak_with_failure(*args,**options):
            try:return speak(*args,**options)
            except Exception as error:
                self.voice_errors.append((type(error).__name__,str(error)))
                raise
        self.voice.speak=speak_with_failure
        self.host=FeatureHost(config=self.config,client=self.client,voice=self.voice,character_life=False,
            runtime=RuntimePolicy(simulate=False),
            imu_monitor=self.monitor,imu_transport_factory=lambda:self.transport,
            imu_profile_directory=self.directory/'imu')
        self.host.session.set_emotion('auto')
        self.host.session._data['emotion']='happy'
        self.host.control(HostControl('imu_reactions',True),'imu_enable')

    def tearDown(self):
        self.begin.set();self.release.set()
        if self.host:self.host.close()

    def pump(self,predicate):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            self.host.heartbeat();self.host.poll()
            if predicate():return
            time.sleep(.002)
        self.fail('Actual Host did not settle')

    def install_cache(self):
        from bmo_ai import audio
        from bmo_ai.integration_face import wav_cues
        path=synthetic_wav();self.addCleanup(audio._discard,path,suppress=True)
        asset=SimpleNamespace(path=path,baked_gain=.25,text='Ooh!',captions=dict(en='Ooh!',ko='오!'),
            clip_id='R01',candidate_id='injected_r01',cues=wav_cues(path),source_model='s2.1-pro-free',
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),close=lambda:None)
        pack=SimpleNamespace(select_event=lambda _event:asset,load_event=lambda *_a,**_kw:asset,
            mark_played=Mock(),snapshot=lambda:{},inventory=lambda:[],event_diagnostics=lambda _event:{})
        self.host.voice_pack=pack;self.voice.event_pack=pack
        return asset,pack

    def test_actual_control_and_face_observer_use_cache_player_caption_and_restore_without_ai(self):
        asset,pack=self.install_cache();self.host.session.set_subtitles('en')
        generation=self.host.modes.generation
        self.monitor.events.append(motion(generation=generation))
        self.host.heartbeat();self.host.poll();self.assertTrue(self.prepared.wait(1),self.voice_errors)
        self.assertEqual(self.host.face_snapshot()['emotion'],'happy')
        self.begin.set();self.assertTrue(self.started.wait(1))
        self.pump(lambda:self.host.face_snapshot()['playback']=='started')
        face=self.host.face_snapshot()
        self.assertEqual(face['emotion'],'surprised');self.assertEqual(face['source'],'voice_pack')
        self.assertEqual(face['caption']['text'],'Ooh!')
        self.assertEqual(face['caption_asset']['sha256'],asset.sha256)
        self.assertTrue(face['cues']);self.assertEqual(self.client.requests,[])
        self.release.set();self.pump(lambda:not self.host.session.busy and self.host.audio.owner is None)
        self.assertEqual(self.host.face_snapshot()['emotion'],'happy')
        self.assertFalse(self.voice.has_cache)  # IMU cannot replace dialogue repeat.
        self.assertEqual(pack.mark_played.call_count,1)

    def test_actual_input_cancel_releases_imu_audio_then_next_question_is_possible(self):
        self.install_cache();self.monitor.events.append(motion(generation=self.host.modes.generation))
        self.host.heartbeat();self.host.poll();self.assertTrue(self.prepared.wait(1),self.voice_errors)
        self.begin.set();self.assertTrue(self.started.wait(1))
        self.host.cancel_conversation();self.pump(lambda:not self.host.session.busy and self.host.audio.owner is None)
        self.assertEqual(self.host.face_snapshot()['emotion'],'happy')
        self.assertEqual(self.client.requests,[])
        result=self.host.control(HostControl('imu_reactions',False),'imu_disable')
        self.assertEqual(result.code,'imu_preference_saved');self.assertEqual(self.transport.closed,1)
        self.assertIsNone(self.host.imu_reactions.active)
        self.assertEqual(self.host.snapshot()['mode'],'conversation')
        self.release.set()
        self.assertTrue(self.host.start_question('Injected next question.'))
        self.pump(lambda:not self.host.session.busy and self.host.audio.owner is None)
        self.assertEqual(len(self.client.requests),1)
        self.assertTrue(self.voice.has_cache)

    def test_actual_trial_control_records_unique_fresh_samples_exports_without_approval(self):
        import json
        from bmo_ai.imu_profile import PROFILE_FILE
        def trial(first):
            result=self.host.control(HostControl('imu_trial',dict(label='stationary',action='start')),
                'trial_start_'+str(first))
            self.assertEqual(result.code,'imu_trial_updated')
            values=[]
            for i in range(first,first+16):
                sample=dict(controller_boot='actual_route_fixture',sequence=i,timestamp_ms=i*100,
                    status='READY',axes=dict(AX=32767,AY=10+i%2,AZ=16384+i%2,GX=0,GY=3000+i%2,GZ=None),
                    accel_fs=2,gyro_fs=250,sensor_id='I68A68',received_at=self.host.clock())
                values.extend((sample,sample))  # repeated CDC snapshot is not another sample
            self.transport.samples=values
            self.host.heartbeat();self.host.poll()
            self.assertEqual(self.host.imu_reactions.capture['samples'],16)
            self.assertEqual(self.client.requests,[])
            result=self.host.control(HostControl('imu_trial',dict(label='stationary',action='finish')),
                'trial_finish_'+str(first))
            self.assertEqual(result.code,'imu_trial_updated')
            self.assertEqual(self.host.imu_reactions.capture['status'],'saved')
            return self.directory/'imu'/self.host.imu_reactions.capture['evidence_file']
        first=trial(1);before=first.read_bytes();second=trial(17)
        self.assertNotEqual(first,second)
        self.assertEqual(first.read_bytes(),before)
        exported=json.loads(second.read_text())
        self.assertEqual(exported['source'],'real_b1')
        self.assertEqual(len(exported['trials']),2)
        self.assertEqual([len(t['observations']) for t in exported['trials']],[16,16])
        self.assertFalse((self.directory/'imu'/PROFILE_FILE).exists())
        self.assertFalse(self.host.imu_reactions.store.snapshot()['relative_signal_validated'])


if __name__=='__main__':unittest.main()
