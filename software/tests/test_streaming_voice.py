"""Injected Fish bytes + harmless owned stdin consumer; no devices/API calls."""
import io
import os
from pathlib import Path
import struct
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
import wave

from bmo_ai import audio, fish_tts
from bmo_ai.config import BMOConfig
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.streaming_voice import PCMParser, StreamingPCMPlayer, _StreamingMouth


def fixture(seconds=.8):
    data = io.BytesIO()
    with wave.open(data,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
        wav.writeframes(struct.pack('<h',12000)*round(16000*seconds))
    return data.getvalue()


class ParserTests(unittest.TestCase):
    def test_split_headers_whole_frames_and_placeholder(self):
        for placeholder in (False,True):
            raw = bytearray(fixture())
            if placeholder:
                struct.pack_into('<I',raw,4,fish_tts.FISH_RIFF_PLACEHOLDER)
                struct.pack_into('<I',raw,40,fish_tts.FISH_DATA_PLACEHOLDER)
            values = []
            parser = PCMParser(lambda fmt,pcm:values.append((fmt,pcm)))
            for byte in raw:
                parser.feed(bytes([byte]))
            parser.finish()
            self.assertEqual(b''.join(pcm for _,pcm in values),bytes(raw[44:]))
            self.assertTrue(all(fmt == (1,16000,2,2) and len(pcm)%2 == 0 for fmt,pcm in values))

    def test_json_invalid_format_and_oversized_headers_never_reach_player(self):
        invalid = [b'{"error":"private provider payload"}']
        raw = bytearray(fixture());struct.pack_into('<H',raw,20,3);invalid.append(bytes(raw))
        raw = bytearray(fixture());struct.pack_into('<I',raw,16,70000);invalid.append(bytes(raw))
        raw = bytearray(fixture());struct.pack_into('<I',raw,28,10);invalid.append(bytes(raw))
        for raw in invalid:
            called = []
            with self.assertRaises(audio.AudioValidationError):
                PCMParser(lambda *args:called.append(args)).feed(raw)
            self.assertFalse(called)

    def test_truncated_frame_or_declared_length_rejected(self):
        for cut in (1,2,100):
            parser = PCMParser(lambda *args:None)
            parser.feed(fixture()[:-cut])
            with self.assertRaises(audio.AudioValidationError):parser.finish()

    def test_finite_data_footer_is_validated_but_never_played(self):
        raw=bytearray(fixture(.05)+b'LIST'+struct.pack('<I',4)+b'INFO')
        struct.pack_into('<I',raw,4,len(raw)-8)
        played=[];parser=PCMParser(lambda fmt,pcm:played.append(pcm))
        for start in range(0,len(raw),7):parser.feed(bytes(raw[start:start+7]))
        parser.finish()
        self.assertEqual(b''.join(played),fixture(.05)[44:])
        raw[-8:-4]=struct.pack('<I',70000)
        with self.assertRaises(audio.AudioValidationError):PCMParser(lambda *args:None).feed(bytes(raw))

    def test_mouth_has_no_network_packet_closed_beats(self):
        fmt=(1,16000,2,2)
        pcm=fixture(1.)[44:]
        def cues(size):
            mouth=_StreamingMouth(fmt,pcm[:7680]);result=[]
            for offset in range(0,len(pcm),size):result+=mouth.feed(pcm[offset:offset+size],offset/32000)
            return result
        whole,fragmented=cues(len(pcm)),cues(222)
        self.assertEqual(whole,fragmented)
        self.assertFalse(any(cue['mouth']=='closed' for cue in fragmented))
        mouth=_StreamingMouth(fmt,pcm[:7680])
        mouth.feed(pcm[:222],0.)
        resumed=mouth.feed(pcm[:4000],.6)
        self.assertTrue(resumed)
        self.assertGreaterEqual(resumed[0]['time'],.6)
        mouth=_StreamingMouth(fmt,pcm[:7680])
        mouth.feed(pcm[:2660],0.) # Exactly ten RMS windows, no residue.
        resumed=mouth.feed(pcm[:4000],.6)
        self.assertTrue(resumed)
        self.assertGreaterEqual(resumed[0]['time'],.6)


class StreamingVoiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.capture = Path(self.directory)/'heard.pcm'
        self.processes, self.commands, self.events = [],[],[]
        self.started, self.download_done = Event(),Event()
        self.cancel = Event()
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def check(self):
        if self.cancel.is_set():raise TurnCancelled()

    def factory(self,command,**kwargs):
        self.commands.append(command)
        # No ALSA: an owned harmless child consumes PCM on stdin.
        child = subprocess.Popen([sys.executable,'-c',
            'import sys;open(sys.argv[1],"wb").write(sys.stdin.buffer.read())',str(self.capture)],**kwargs)
        self.processes.append(child)
        return child

    def stream(self,**kwargs):
        return StreamingPCMPlayer(**kwargs,process_factory=self.factory)

    def emit(self,kind,**fields):
        self.events.append((kind,fields))
        if kind == 'started':
            self.started.set()

    def voice(self,**kwargs):
        voice = IntegrationVoice(BMOConfig(tts_provider='fish',fish_gain=.25),None,
            emit=self.emit,cancel_check=self.check,fish_api_key='test-only',stream_factory=self.stream,**kwargs)
        self.addCleanup(voice.close)
        voice.expression_metadata = dict(emotion='happy',emotion_validation='validated',
            caption_payload={'en':'A whole checked reply.','ko':'검증된 전체 답변이에요.'})
        return voice

    def response(self,raw=None, *, wait_after_prefix=None):
        owner = self
        raw = fixture() if raw is None else raw
        class Response:
            def __init__(self):self.offset=0;self.waited=False
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):raise AssertionError('stream must use available-byte read1')
            def read1(self,size):
                if self.offset >= 9000 and not self.waited:
                    self.waited=True
                    if wait_after_prefix:wait_after_prefix()
                chunk=raw[self.offset:self.offset+size];self.offset+=len(chunk)
                if not chunk:owner.download_done.set()
                return chunk
        return Response()

    def test_first_real_pcm_starts_before_download_completion_and_repeat_is_complete(self):
        voice = self.voice()
        observations=[]
        def delayed_tail():
            self.assertTrue(self.started.wait(2),'player waited for the complete WAV')
            observations.append(self.download_done.is_set())
            time.sleep(.04)
        with patch.object(fish_tts,'urlopen',return_value=self.response(wait_after_prefix=delayed_tail)) as request:
            voice.speak('A whole checked reply.')
        self.assertEqual(observations,[False])
        self.assertEqual(request.call_count,1)
        kinds=[kind for kind,_ in self.events]
        self.assertEqual(kinds.count('prepared'),1);self.assertEqual(kinds.count('started'),1)
        self.assertEqual(kinds[-1],'ended');self.assertIn('progress',kinds)
        self.assertEqual(self.events[kinds.index('started')][1]['caption_payload']['ko'],'검증된 전체 답변이에요.')
        pcm=self.capture.read_bytes()
        self.assertEqual(pcm,struct.pack('<h',3000)*12800) # .25 exactly once
        self.assertEqual(len(self.processes),1);self.assertIsNotNone(self.processes[0].poll())
        self.assertTrue(voice.has_cache)
        self.assertEqual(voice._cache['info']['duration_seconds'],.8)
        heard=[]
        def repeat(command,*,on_start,cancel_check,**kwargs):
            with wave.open(command[-1],'rb') as wav:heard.append(wav.readframes(wav.getnframes()))
            on_start()
        voice._player=repeat
        self.assertTrue(voice.repeat());self.assertEqual(heard,[pcm])

    def reaction_asset(self,seconds=.35):
        path=Path(self.directory)/'approved-prefix.wav'
        raw=bytearray(fixture(seconds));raw[44:]=struct.pack('<h',2000)*round(16000*seconds)
        path.write_bytes(raw)
        return SimpleNamespace(path=path,baked_gain=.25,clip_id='RX01',candidate_id='RX01_fixture',
            sha256='a'*64,captions={'en':'Hehe.','ko':'헤헤.'},info=audio._wav_info(path),close=lambda:None)

    def reaction_voice(self,asset):
        from bmo_ai.expressive_voice import VoiceDelivery
        voice=self.voice(expressive=True,character_life=True)
        proposed=VoiceDelivery('[chuckling] Hehe. A whole checked reply.','happy','chuckle',
            'fish_character_s2_v1',True,False,'chuckle','eligible')
        plain=__import__('dataclasses').replace(proposed,text='A whole checked reply.')
        voice._expressive_policy.plan=lambda *args,**kwargs:proposed
        voice._cached_reaction=lambda *args:(asset,asset.clip_id,plain)
        played=[];voice.event_pack=SimpleNamespace(mark_played=lambda *args,**kwargs:played.append(args))
        return voice,played

    def test_cached_prefix_streams_before_eof_on_one_player_and_body_is_not_prefix_ttfa(self):
        asset=self.reaction_asset();voice,played=self.reaction_voice(asset)
        timings=[];voice.on_timing=lambda stage,code,**fields:timings.append((stage,code,fields))
        def tail():
            self.assertTrue(self.started.wait(2),'cached prefix restored the whole WAV gate')
            self.assertFalse(self.download_done.is_set())
        with patch.object(fish_tts,'urlopen',return_value=self.response(wait_after_prefix=tail)) as request:
            voice.speak('A whole checked reply.')
        self.assertEqual(request.call_count,1)
        self.assertEqual(len(self.processes),1)
        expected=struct.pack('<h',2000)*5600+struct.pack('<h',3000)*12800
        self.assertEqual(self.capture.read_bytes(),expected)
        self.assertEqual(len(played),1)
        start=next(fields for kind,fields in self.events if kind=='started')
        self.assertEqual(start['playback_path'],'fish_reaction_stream')
        self.assertEqual(start['caption_asset']['sha256'],asset.sha256)
        self.assertEqual(start['cached_reaction_id'],asset.clip_id)
        self.assertTrue(start['caption_payload']['ko'].startswith('헤헤.'))
        markers=[code for stage,code,_ in timings if stage=='playback']
        self.assertEqual(markers.count('first_pcm'),1)
        self.assertEqual(markers.count('reaction_started'),1)
        self.assertEqual(markers.count('answer_pcm'),1)
        self.assertNotIn('answer_started',markers)
        self.assertGreater(markers.index('answer_pcm'),markers.index('reaction_started'))
        body=next(fields for stage,code,fields in timings if code=='answer_pcm')
        self.assertEqual(body['answer_offset_frames'],5600)
        self.assertEqual(body['answer_onset_evidence'],'body_pcm_submission')
        with wave.open(str(voice._cache['path']),'rb') as wav:
            self.assertEqual(wav.readframes(wav.getnframes()),expected)
        heard=[]
        def repeat(command,*,on_start,**kwargs):
            with wave.open(command[-1],'rb') as wav:heard.append(wav.readframes(wav.getnframes()))
            on_start()
        voice._player=repeat
        self.assertTrue(voice.repeat())
        self.assertEqual(heard,[expected]);self.assertEqual(len(played),1)
        self.assertEqual(next(fields for kind,fields in reversed(self.events) if kind=='started')['playback_path'],'repeat')

    def test_long_approved_reaction_uses_same_fish_inline_stream_without_prefix_or_fake_body_start(self):
        asset=self.reaction_asset(2.2);before=asset.path.read_bytes()
        voice,played=self.reaction_voice(asset);timings=[]
        voice.on_timing=lambda stage,code,**fields:timings.append((stage,code,fields))
        with patch.object(fish_tts,'urlopen',return_value=self.response()) as request:
            voice.speak('A whole checked reply.')
        self.assertEqual(request.call_count,1)
        payload=__import__('json').loads(request.call_args.args[0].data)
        self.assertIn('[chuckling]',payload['text'])
        start=next(fields for kind,fields in self.events if kind=='started')
        self.assertEqual(start['playback_path'],'fish_stream')
        self.assertEqual(start['reaction_cache_status'],'too_long_inline')
        self.assertFalse(start['cached_reaction'])
        self.assertEqual(self.capture.read_bytes(),struct.pack('<h',3000)*12800)
        self.assertEqual(played,[]);self.assertEqual(asset.path.read_bytes(),before)
        self.assertFalse(any(code=='answer_started' for _,code,_ in timings))
        body=next(fields for _,code,fields in timings if code=='answer_pcm')
        self.assertEqual(body['answer_onset_evidence'],'unlocated_vocal_cue')

    def test_loud_baked_prefix_stream_and_repeat_apply_same_volume_without_unbake_clipping(self):
        asset=self.reaction_asset()
        raw=bytearray(asset.path.read_bytes());raw[44:]=struct.pack('<h',20000)*5600
        asset.path.write_bytes(raw)
        voice,_=self.reaction_voice(asset)
        voice.volume.set(level=.4)
        with patch.object(fish_tts,'urlopen',return_value=self.response()):
            voice.speak('A whole checked reply.')
        heard=self.capture.read_bytes()
        self.assertEqual(heard,struct.pack('<h',32000)*5600+struct.pack('<h',4800)*12800)
        replay=[]
        def player(command,*,on_start,**kwargs):
            with wave.open(command[-1],'rb') as wav:replay.append(wav.readframes(wav.getnframes()))
            on_start()
        voice._player=player
        self.assertTrue(voice.repeat())
        self.assertEqual(replay,[heard])
        self.assertEqual(asset.path.read_bytes(),raw)

    def test_unsupported_prefix_conversion_keeps_valid_body_stream_without_claiming_reaction(self):
        asset=self.reaction_asset();voice,played=self.reaction_voice(asset)
        raw=io.BytesIO()
        with wave.open(raw,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(3);wav.setframerate(16000)
            wav.writeframes((12000).to_bytes(3,'little',signed=True)*12800)
        timing=[];voice.on_timing=lambda stage,code,**fields:timing.append((stage,code,fields))
        with patch.object(fish_tts,'urlopen',return_value=self.response(raw.getvalue())):
            voice.speak('A whole checked reply.')
        start=next(fields for kind,fields in self.events if kind=='started')
        self.assertEqual(start['reaction_cache_status'],'format_omitted')
        self.assertEqual(start['playback_path'],'fish_stream')
        self.assertEqual(start['reaction'],'none')
        self.assertEqual(start['played_reaction'],'none')
        self.assertFalse(start['cached_reaction']);self.assertEqual(played,[])
        self.assertEqual(self.capture.read_bytes(),(3000).to_bytes(3,'little',signed=True)*12800)
        self.assertEqual(sum(code=='answer_started' for _,code,_ in timing),1)
        self.assertEqual(voice._cache['expression']['reaction'],'none')

    def test_cancelled_prefixed_stream_keeps_previous_completed_repeat(self):
        voice,played=self.reaction_voice(self.reaction_asset())
        with patch.object(fish_tts,'urlopen',return_value=self.response()):voice.speak('First.')
        previous=voice._cache['path'];self.events.clear();self.started.clear()
        def cancel_after_prefix():
            self.assertTrue(self.started.wait(2));self.cancel.set()
        with patch.object(fish_tts,'urlopen',return_value=self.response(wait_after_prefix=cancel_after_prefix)):
            with self.assertRaises(TurnCancelled):voice.speak('Cancelled.')
        self.assertEqual(voice._cache['path'],previous)
        self.assertNotIn('ended',[kind for kind,_ in self.events])
        self.assertTrue(all(process.poll() is not None for process in self.processes))
        self.cancel.clear();self.started.clear()
        with patch.object(fish_tts,'urlopen',return_value=self.response()):voice.speak('Next.')
        self.assertEqual(self.events[-1][0],'ended')

    def test_bad_header_never_spawns_and_next_request_recovers(self):
        voice=self.voice();timing=[]
        voice.on_timing=lambda stage,code,**fields:timing.append((stage,code,fields))
        with patch.object(fish_tts,'urlopen',return_value=self.response(b'{"error":"not PCM"}')):
            with self.assertRaises(audio.AudioValidationError):voice.speak('Hello.')
        self.assertFalse(self.processes);self.assertFalse(voice.has_cache)
        first_failure=next((stage,code) for stage,code,fields in timing if fields.get('outcome')=='error')
        self.assertEqual(first_failure,('fish','invalid_wav'))
        self.assertEqual(self.events[-1][1]['code'],'invalid_wav')
        with patch.object(fish_tts,'urlopen',return_value=self.response()):voice.speak('Next.')
        self.assertEqual(self.events[-1][0],'ended')

    def test_truncated_download_keeps_previous_repeat_and_no_false_completion(self):
        voice=self.voice()
        with patch.object(fish_tts,'urlopen',return_value=self.response()):voice.speak('Good.')
        previous=voice._cache['path']
        self.events.clear()
        with patch.object(fish_tts,'urlopen',return_value=self.response(fixture()[:-2])):
            with self.assertRaises(audio.AudioValidationError):voice.speak('Truncated.')
        self.assertEqual(voice._cache['path'],previous)
        self.assertNotIn('ended',[kind for kind,_ in self.events])
        self.assertEqual(self.events[-1][0],'error')
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_cancel_stops_output_even_while_http_read_is_blocked(self):
        voice=self.voice()
        blocked,release=Event(),Event()
        errors=[]
        def hold_tail():
            self.assertTrue(self.started.wait(2))
            blocked.set();release.wait(3)
        def run():
            try:voice.speak('Cancelable.')
            except BaseException as error:errors.append(error)
        with patch.object(fish_tts,'urlopen',return_value=self.response(wait_after_prefix=hold_tail)):
            thread=Thread(target=run);thread.start()
            try:
                self.assertTrue(blocked.wait(2));self.cancel.set()
                deadline=time.monotonic()+1
                while self.processes[0].poll() is None and time.monotonic()<deadline:time.sleep(.01)
                self.assertIsNotNone(self.processes[0].poll())
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],TurnCancelled)
        self.assertFalse(voice.has_cache);self.assertEqual(self.events[-1][0],'cancelled')

    def test_cancel_interrupts_owned_http_socket_and_returns_without_late_downloader(self):
        voice=self.voice()
        reader,writer=socket.socketpair()
        self.addCleanup(reader.close);self.addCleanup(writer.close)
        errors=[]
        class Response:
            fp=SimpleNamespace(raw=SimpleNamespace(_sock=reader))
            def __enter__(self):return self
            def __exit__(self,*args):reader.close()
            def read(self,size):return self.read1(size)
            def read1(self,size):return reader.recv(size)
        # Valid prefix, then a transport that never sends EOF by itself.
        writer.sendall(fixture()[:13000])
        def run():
            try:voice.speak('Cancelable body.')
            except BaseException as error:errors.append(error)
        with patch.object(fish_tts,'urlopen',return_value=Response()):
            thread=Thread(target=run);thread.start()
            try:
                self.assertTrue(self.started.wait(2))
                self.cancel.set()
                thread.join(1)
                self.assertFalse(thread.is_alive(),'HTTP body still owns the canceled turn')
            finally:
                writer.close();thread.join(2)
        self.assertIsInstance(errors[0],TurnCancelled)
        self.assertEqual(reader.fileno(),-1)
        self.assertTrue(all(process.poll() is not None for process in self.processes))
        self.assertFalse(voice.has_cache)

    def test_session_progress_preserves_start_caption_and_arm_boundary(self):
        from bmo_ai.integration_session import IntegrationSession,SimulatedClient
        from bmo_ai.integration_profile import integration_config
        voice=self.voice()
        now=[100.];callbacks=[]
        session=IntegrationSession(integration_config(),SimulatedClient(),simulate=True,voice=voice,
            clock=lambda:now[0],on_playback_event=lambda *args:callbacks.append(args))
        self.addCleanup(session.close)
        session.set_subtitles('en')
        inspected=[]
        def inspect_prefix():
            deadline=time.monotonic()+2
            while session.snapshot()['playback']!='started' and time.monotonic()<deadline:time.sleep(.005)
            before=session.snapshot()
            self.assertEqual(before['playback'],'started')
            now[0]=100.5
            voice.emit('progress',source='fish',cues=[{'time':0.,'mouth':'open'},{'time':.8,'mouth':'closed'}])
            after=session.snapshot()
            inspected.append(after)
            self.assertEqual(after['position'],.5)
            self.assertEqual(after['caption'],before['caption'])
            self.assertEqual(after['utterance_id'],before['utterance_id'])
            self.assertEqual(after['cues'][0]['mouth'],'open')
        with patch.object(fish_tts,'urlopen',return_value=self.response(wait_after_prefix=inspect_prefix)):
            self.assertTrue(session.start_with_input(lambda check:'A greeting.'))
            deadline=time.monotonic()+3
            while session.busy and time.monotonic()<deadline:session.poll();time.sleep(.005)
        self.assertFalse(session.busy)
        self.assertTrue(inspected,str(list(session.events)))
        self.assertEqual(sum(event['kind']=='started' for event in session.events),1)
        self.assertFalse(any(args[0]=='progress' for args in callbacks))
        self.assertEqual(session.snapshot()['cues'],[])
        self.assertEqual(session.snapshot()['caption']['text'],'')

    def test_short_response_under_prefill_and_muted_output(self):
        voice=self.voice();voice.volume.set(muted=True)
        with patch.object(fish_tts,'urlopen',return_value=self.response(fixture(.05))):voice.speak('Short.')
        self.assertEqual(self.capture.read_bytes(),b'\0\0'*800)
        self.assertEqual([kind for kind,_ in self.events],['prepared','started','ended'])

    def test_blocked_owned_consumer_obeys_deadline_and_is_reaped(self):
        clock=[0.]
        def stalled(command,**kwargs):
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],**kwargs)
            self.processes.append(child)
            return child
        player=StreamingPCMPlayer(device=None,level=.25,check=lambda:None,
            prepare=lambda cues:None,started=lambda:None,progress=lambda cues:None,env={},
            process_factory=stalled,clock=lambda:clock[0])
        try:
            player.feed(fixture(2.))
            deadline=time.monotonic()+2
            while not self.processes and time.monotonic()<deadline:time.sleep(.005)
            self.assertTrue(self.processes)
            clock[0]=191.
            self.assertTrue(player.done.wait(1))
            self.assertEqual(player.error.code,'audio_timeout')
            self.assertIsNotNone(self.processes[0].poll())
        finally:player.close()


if __name__=='__main__':unittest.main()
