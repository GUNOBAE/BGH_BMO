"""Real Host/broker/recorder handoff with injected PCM/model/player only."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient
from bmo_ai.features.input.microphone import PcmFrame
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.greeting_cache import GreetingCache
from bmo_ai import audio
from bmo_ai.wake_ack import WakeAckCache, WakeAckSpec
from bmo_ai.features.input.ambient_vad import VoicingEvidence, WebRtcVoiceEvidence as ActualWebRtc
from test_character_wake_word import SourceBoundary, DetectorBoundary, eventually


class Clock:
    def __init__(self):self.offset=0.
    def __call__(self):return time.monotonic()+self.offset


class Factory:
    def __init__(self, clock):
        self.clock=clock;self.sources=[];self.active=self.maximum=0
    def __call__(self):
        clock=self.clock
        class Source(SourceBoundary):
            def frames(self, capture_id, *, generation, cancel_check=None):
                for frame in super().frames(capture_id,generation=generation,cancel_check=cancel_check):
                    yield PcmFrame(frame.pcm,frame.sequence,clock(),generation)
        source=Source(self);self.sources.append(source);return source


class FieldWakeHandoffTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        for target in ('subprocess.Popen','socket.socket.connect','socket.socket.connect_ex',
                       'bmo_ai.fish_tts.synthesize_wav','bmo_ai.private_credentials.resolve_credentials'):
            self.enterContext(patch(target,side_effect=AssertionError('Actual IO forbidden')))
        self.hosts=[]
        class Boundary:
            def classify(self,pcm):
                voiced=any(pcm)
                return VoicingEvidence(voiced,2,2 if voiced else 0,len(pcm)/32000 if voiced else 0.)
            def close(self):pass
            def snapshot(self):return dict(backend='INJECTED_BOUNDARY_ONLY',human_accuracy='NOT_RUN')
        # Acoustic boundary is injected here; separate public-reference/real C
        # VAD tests must not be counted as human pronunciation acceptance.
        self.enterContext(patch('bmo_ai.features.input.ambient_vad.WebRtcVoiceEvidence',Boundary))

    def tearDown(self):
        for host in self.hosts:host.close()

    def make(self, *, ack=False, player=None):
        clock=Clock();factory=Factory(clock);detector=DetectorBoundary()
        original=detector.snapshot
        detector.snapshot=lambda:dict(original(),enabled=detector.status=='READY')
        client=FeatureSimulatedClient();config=integration_config()
        captures=[];played=[]
        def transcribe(_client,path,**kwargs):
            with wave.open(str(path),'rb') as saved:captures.append(saved.readframes(saved.getnframes()))
            return 'A new synthetic question.'
        def play(command, *, on_start, cancel_check, **kwargs):
            cancel_check();on_start();played.append(command)
            if player:player(factory,clock)
            cancel_check()
        voice=IntegrationVoice(config,client,simulate=True,character_life=True,
                               player=play,emit=lambda *_a,**_kw:None)
        with patch('bmo_ai.features.input.wake_word.LocalWakeDetector',return_value=detector):
            host=FeatureHost(config=config,client=client,voice=voice,clock=clock,
                runtime=RuntimePolicy(simulate=True,conversation_ptt_microphone=True),
                character_life=True,ptt_speech_safe=True,wake_word=True,
                ptt_source_factory=factory,ptt_transcriber=transcribe)
        self.hosts.append(host)
        host._wake_ack_cache=WakeAckCache(config,self.root/('ack'+str(len(self.hosts))))
        if ack:
            raw=synthetic_wav()
            try:GreetingCache(host._wake_ack_cache.directory,WakeAckSpec.from_config(config)).publish(
                raw,approved=True,simulated=True)
            finally:audio._discard(raw,suppress=True)
        self.assertTrue(host.enable_wake_word())
        eventually(lambda:factory.sources and factory.sources[-1].entered.is_set())
        return host,client,factory,detector,clock,captures,played

    def trigger(self,host,factory,detector):
        source=factory.sources[-1]
        for _ in range(10):source.send(8000)  # old ring contains >=.24s keyword
        eventually(lambda:detector.feeds>=10)
        detector.trigger=True;source.send(8000)
        eventually(lambda:bool(host._records));detector.trigger=False
        return source

    def finish(self,host):
        deadline=time.monotonic()+2.
        while host.session.busy and time.monotonic()<deadline:
            host.poll();time.sleep(.002)
        self.assertFalse(host.session.busy)

    def speech(self,source):
        for _ in range(3):source.send(0)  # observed local comma, never a sleep
        source.send(5001)  # unique first syllable bytes
        for _ in range(7):source.send(5000)
        for _ in range(24):source.send(0)

    def test_wake_only_one_three_five_seconds_never_enters_thinking_or_calls_providers(self):
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector);host.poll()
        eventually(lambda:host._microphone_broker.snapshot()['capture_active'])
        for elapsed in (1.,3.,5.):
            clock.offset=elapsed
            for _ in range(3):source.send(0)
            if elapsed<5:
                eventually(lambda:host.microphone.snapshot()['frames']>=int(elapsed==3.)+1)
                host.poll()
                self.assertEqual(host.snapshot()['wake_word']['handoff']['phase'],'question_listening')
            else:self.finish(host)
            self.assertEqual(client.requests,[]);self.assertEqual(captures,[]);self.assertEqual(played,[])
            self.assertFalse(any(e['phase']=='thinking' for e in host.session.events))
        self.assertEqual(host.snapshot()['wake_word']['handoff']['phase'],'timed_out')
        self.assertEqual(factory.maximum,1)
        self.assertEqual(host.microphone.settings['no_speech_timeout'],3.)
        self.assertEqual(host.microphone.settings['silence_timeout'],.9)

    def test_question_after_three_seconds_preserves_first_bytes_and_excludes_keyword_ring(self):
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector);host.poll()
        eventually(lambda:host._microphone_broker.snapshot()['capture_active'])
        clock.offset=3.;self.speech(source);self.finish(host)
        self.assertEqual(len(client.requests),1);self.assertEqual(len(captures),1)
        self.assertIn((5001).to_bytes(2,'little')*640,captures[0])
        self.assertNotIn((8000).to_bytes(2,'little')*640,captures[0])
        self.assertEqual(factory.maximum,1);self.assertEqual(len(played),1)

    def test_immediate_followup_queued_before_owner_skips_ack_and_keeps_first_word(self):
        host,client,factory,detector,clock,captures,played=self.make(ack=True)
        source=self.trigger(host,factory,detector)
        self.speech(source)
        eventually(lambda:host._microphone_broker.snapshot()['question_buffered_bytes']>=35*1280)
        host.poll();self.finish(host)
        self.assertEqual(host.snapshot()['wake_word']['handoff']['acknowledgement'],'SKIPPED_FOLLOWUP')
        self.assertEqual(len(client.requests),1);self.assertEqual(len(played),1)
        self.assertIn((5001).to_bytes(2,'little')*640,captures[0])

    def test_cached_ack_finishes_with_mic_closed_then_fresh_question_has_new_deadline(self):
        acknowledgements=[]
        def observed(factory,clock):
            if not acknowledgements:
                self.assertTrue(factory.sources[0].closed.is_set())
                clock.offset+=1.;acknowledgements.append('actually-ended')
        host,client,factory,detector,clock,captures,played=self.make(ack=True,player=observed)
        self.trigger(host,factory,detector);host.poll()
        eventually(lambda:len(factory.sources)==2 and host._microphone_broker.snapshot()['capture_active'])
        self.assertEqual(acknowledgements,['actually-ended'])
        self.assertEqual(host._wake_handoff['acknowledgement'],'PLAYED')
        clock.offset+=3.;self.speech(factory.sources[-1]);self.finish(host)
        self.assertEqual(len(client.requests),1);self.assertEqual(len(played),2)
        self.assertEqual(factory.maximum,1)
        self.assertEqual(len(host.session.brain.history),2)
        self.assertIn((5001).to_bytes(2,'little')*640,captures[0])

    def test_residual_keyword_tail_after_detector_hit_never_becomes_question(self):
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector)
        for _ in range(8):source.send(8000)
        for _ in range(3):source.send(0)
        eventually(lambda:host._microphone_broker.snapshot()['question_buffered_bytes']>=11*1280)
        host.poll()
        eventually(lambda:host._microphone_broker.snapshot()['question_boundary']['discarded_tail_samples']==8*640)
        clock.offset=5.2;self.finish(host)
        self.assertEqual(client.requests,[]);self.assertEqual(captures,[]);self.assertEqual(played,[])
        self.assertFalse(any(e['phase']=='thinking' for e in host.session.events))
        boundary=host.snapshot()['microphone_broker']['question_boundary']
        self.assertEqual(boundary['discarded_tail_samples'],8*640)

    def test_buffered_followup_survives_worker_delay_without_legacy_preroll_age_drop(self):
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector);self.speech(source)
        eventually(lambda:host._microphone_broker.snapshot()['question_buffered_bytes']>=35*1280)
        original=host._confirm_wake
        def delayed(check):
            original(check);clock.offset+=.7
        host._confirm_wake=delayed
        host.poll();self.finish(host)
        self.assertEqual(len(client.requests),1)
        self.assertIn((5001).to_bytes(2,'little')*640,captures[0])

    def test_unsegmented_post_trigger_speech_is_quarantined_without_guessing_word_alignment(self):
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector)
        for _ in range(24):source.send(5001)
        for _ in range(3):source.send(0)
        eventually(lambda:host._microphone_broker.snapshot()['question_buffered_bytes']>=27*1280)
        host.poll();eventually(lambda:host._microphone_broker.snapshot()['question_boundary']['discarded_tail_samples']==24*640)
        clock.offset=5.2;self.finish(host)
        self.assertEqual(client.requests,[]);self.assertEqual(captures,[])
        self.assertFalse(any(e['phase']=='thinking' for e in host.session.events))

    def test_gp8_cancels_wake_without_inherited_button_debounce_and_wake_off_releases(self):
        host,client,factory,detector,clock,captures,played=self.make()
        self.trigger(host,factory,detector);host.poll()
        eventually(lambda:host._microphone_broker.snapshot()['capture_active'])
        self.assertTrue(host.ptt_button())
        self.finish(host)
        self.assertEqual(client.requests,[])
        self.assertTrue(host.disable_wake_word())
        self.assertFalse(host._microphone_broker.snapshot()['reader_alive'])
        self.assertFalse(host._microphone_broker.snapshot()['microphone_open'])
        clock.offset+=.3
        self.assertTrue(host.ptt_button())
        eventually(lambda:host._microphone_broker.snapshot()['capture_active'])
        self.speech(factory.sources[-1]);self.finish(host)
        self.assertEqual(len(client.requests),1)
        self.assertEqual(factory.maximum,1)

    def test_ambiguous_first_words_then_short_pause_never_transcribes_only_sentence_suffix(self):
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector)
        for _ in range(8):source.send(5001)  # e.g. what's your, before any quiet edge
        for _ in range(3):source.send(0)     # a sentence-internal 120ms pause
        for _ in range(8):source.send(5002)  # e.g. name, NOT a new whole question
        for _ in range(24):source.send(0)
        eventually(lambda:host._microphone_broker.snapshot()['question_buffered_bytes']>=43*1280)
        host.poll();eventually(lambda:host.microphone.snapshot()['frames']>=1)
        clock.offset=5.2;self.finish(host)
        self.assertEqual(client.requests,[])
        self.assertEqual(captures,[])
        self.assertEqual(played,[])
        self.assertGreater(host.snapshot()['microphone_broker']['question_boundary']['discarded_tail_samples'],0)

    def test_ambiguous_segment_allows_a_whole_fresh_question_only_after_full_utterance_silence(self):
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector)
        for _ in range(8):source.send(8000)
        for _ in range(23):source.send(0)  # .92s: fresh utterance, not a short comma
        self.speech(source)
        eventually(lambda:host._microphone_broker.snapshot()['question_buffered_bytes']>=66*1280)
        host.poll();self.finish(host)
        self.assertEqual(len(client.requests),1)
        self.assertIn((5001).to_bytes(2,'little')*640,captures[0])
        self.assertNotIn((8000).to_bytes(2,'little')*640,captures[0])

    def test_real_c_vad_drains_public_reference_tail_without_transcribing_keyword_only_audio(self):
        from bmo_ai.ptt_replay import load_public_reference_pcm16k
        host,client,factory,detector,clock,captures,played=self.make()
        source=self.trigger(host,factory,detector)
        reference=load_public_reference_pcm16k(target_rms=.02)
        padded=reference+bytes((-len(reference))%1280)
        for offset in range(0,len(padded),1280):source.queue.put(padded[offset:offset+1280])
        for _ in range(20):source.send(0)
        eventually(lambda:host._microphone_broker.snapshot()['question_buffered_bytes']>len(padded))
        with patch('bmo_ai.features.input.ambient_vad.WebRtcVoiceEvidence',ActualWebRtc):
            host.poll();eventually(lambda:host._microphone_broker.snapshot()['question_boundary']['discarded_tail_samples']>0)
            self.assertEqual(captures,[]);self.assertEqual(client.requests,[])
            clock.offset=5.2;self.finish(host)
        self.assertEqual(captures,[]);self.assertEqual(client.requests,[]);self.assertEqual(played,[])
        self.assertGreater(host._microphone_broker.snapshot()['question_boundary']['discarded_tail_samples'],0)

    def test_optional_ack_device_failure_uses_visual_fallback_without_retry_or_dialogue_cache_loss(self):
        calls=[]
        def fail_once(factory,clock):
            calls.append(1)
            if len(calls)==1:raise audio.AudioPlaybackError('Fixture unavailable','device_unavailable')
        host,client,factory,detector,clock,captures,played=self.make(ack=True,player=fail_once)
        self.trigger(host,factory,detector);host.poll()
        eventually(lambda:len(factory.sources)==2 and host._microphone_broker.snapshot()['capture_active'])
        self.assertEqual(host._wake_handoff['acknowledgement'],'FAILED_VISUAL')
        self.speech(factory.sources[-1]);self.finish(host)
        self.assertEqual(len(client.requests),1);self.assertEqual(len(played),2)
        self.assertTrue(host.session.voice.has_cache)

    def test_approved_ack_face_cues_follow_actual_player_without_promoting_dialogue_or_repeat(self):
        from bmo_ai.integration_face import _public_snapshot
        host,client,factory,detector,clock,captures,played=self.make(ack=True)
        voice=host.session.voice
        voice.speak('A prior synthetic answer.')
        cached=dict(voice._cache)
        history=list(host.session.brain.history)
        host.session.set_emotion('sad')
        observed=[]
        def player(command, *, on_start, cancel_check, **kwargs):
            cancel_check()
            before=_public_snapshot(host.face_snapshot())
            self.assertEqual(before['source'],'cached_wake_ack')
            self.assertEqual(before['playback'],'prepared')
            self.assertEqual(before['phase'],'listening')
            self.assertEqual(before['position'],0.)
            self.assertEqual(before['cues'],[])  # Face adapter stays still before actual start.
            on_start()
            started=_public_snapshot(host.face_snapshot())
            self.assertEqual(started['playback'],'started')
            self.assertEqual(started['phase'],'speaking')
            self.assertTrue(started['cues'])
            self.assertEqual(started['emotion'],'sad')
            self.assertEqual(started['emotion_mode'],'manual')
            self.assertIn(':wake_ack:',started['utterance_id'])
            clock.offset+=.2
            self.assertGreaterEqual(host.face_snapshot()['position'],.2)
            self.assertEqual(voice._cache,cached)
            self.assertEqual(host.session.brain.history,history)
            self.assertIsNone(host.session.snapshot()['character'])
            self.assertIsNone(host.session.snapshot()['expression_trace'])
            observed.append(started['utterance_id'])
            cancel_check()
        voice._player=player
        self.trigger(host,factory,detector);host.poll()
        eventually(lambda:host._microphone_broker.snapshot()['capture_active'])
        after=host.face_snapshot()
        self.assertEqual(len(observed),1)
        self.assertNotEqual(after['source'],'cached_wake_ack')
        self.assertNotEqual(after['utterance_id'],observed[0])
        self.assertEqual(after['cues'],[])
        self.assertEqual(after['emotion'],'sad')
        self.assertEqual(voice._cache,cached)
        self.assertEqual(host.session.brain.history,history)
        self.assertFalse(any(e['source']=='cached_wake_ack' for e in host.session.events))
        self.assertEqual(host._voice_started_turns,set())
        clock.offset+=5.2
        for _ in range(3):factory.sources[-1].send(0)
        self.finish(host)
        self.assertEqual(client.requests,[])
        self.assertEqual(captures,[])

    def test_ack_cancel_or_player_error_clears_face_overlay_and_owned_voice_lock(self):
        from bmo_ai.conversation import TurnCancelled
        for outcome in ('cancel','device_error'):
            with self.subTest(outcome=outcome):
                host,client,factory,detector,clock,captures,played=self.make(ack=True)
                seen=[]
                def player(command, *, on_start, cancel_check, **kwargs):
                    on_start()
                    seen.append(host.face_snapshot())
                    self.assertEqual(seen[-1]['source'],'cached_wake_ack')
                    self.assertEqual(seen[-1]['playback'],'started')
                    if outcome=='cancel':
                        host.cancel_conversation();cancel_check()
                        raise AssertionError('Cancellation was ignored')
                    raise audio.AudioPlaybackError('Fixture device failure','device_unavailable')
                host.session.voice._player=player
                self.trigger(host,factory,detector);host.poll()
                eventually(lambda:bool(seen))
                if outcome=='device_error':
                    eventually(lambda:host._microphone_broker.snapshot()['capture_active'])
                    clock.offset+=5.2
                    for _ in range(3):factory.sources[-1].send(0)
                self.finish(host)
                face=host.face_snapshot()
                self.assertNotEqual(face['source'],'cached_wake_ack')
                self.assertEqual(face['cues'],[])
                self.assertNotEqual(face['playback'],'started')
                self.assertTrue(host.session.voice._speaking.acquire(blocking=False))
                host.session.voice._speaking.release()
                self.assertTrue(host.session.voice._idle.is_set())
                self.assertEqual(client.requests,[])
                self.assertEqual(captures,[])
                self.assertEqual(len(seen),1)

    def test_resume_failure_still_releases_existing_voice_lock_and_admission_failure_retires_wake(self):
        host,client,factory,detector,clock,captures,played=self.make(ack=True)
        self.trigger(host,factory,detector)
        with patch.object(host._microphone_broker,'resume_question',side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):host._confirm_wake(lambda:None)
        self.assertTrue(host.session.voice._speaking.acquire(blocking=False))
        host.session.voice._speaking.release();self.assertTrue(host.session.voice._idle.is_set())
        self.assertTrue(host.disable_wake_word())
        with patch.object(host,'_begin_conversation',return_value=False):
            self.assertFalse(host.ptt_button(_wake=True))
        self.assertFalse(host._wake_input_active)

    def test_wake_wait_survives_install_plan_and_manual_thresholds_remain_unchanged(self):
        from bmo_ai.desktop_install import desktop_install_plan,DesktopInstallError
        plan=desktop_install_plan(home=self.root,environ={'HOME':str(self.root)},simulate=True,
            character_options=dict(character_life=True,wake_word=True,wake_question_wait=7.))
        self.assertIn('--wake-question-wait',plan['command']);self.assertIn('7.0',plan['command'])
        self.assertEqual(plan['files_written'],0)
        with self.assertRaises(DesktopInstallError):
            desktop_install_plan(home=self.root,environ={'HOME':str(self.root)},simulate=True,
                character_options=dict(character_life=True,wake_question_wait=7.))

    def test_simulation_default_ack_cache_never_uses_real_operator_voice_pack(self):
        with patch('bmo_ai.wake_ack.WakeAckCache',wraps=WakeAckCache) as constructor:
            host,*_=self.make()
        directory=constructor.call_args.kwargs['directory']
        self.assertTrue(directory.is_relative_to(Path(host._temporary.name)))


if __name__=='__main__':unittest.main()
