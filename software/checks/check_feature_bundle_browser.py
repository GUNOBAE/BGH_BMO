#!/usr/bin/env python3
"""Actual simulation launcher + external shell + unmodified local screen.

Synthetic 150ms stage holds and eight-second PCM fixtures make UI transitions
observable; neither measures real latency, audio, camera or game performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmo_ai.screen_profiles import SCREEN_PROFILES
from check_integration_end_to_end import launcher_url


CHILD = r'''
from contextlib import ExitStack
from dataclasses import replace
import hashlib,inspect,json,os,signal,subprocess,sys,time,wave
from pathlib import Path
from unittest.mock import patch
from bmo_ai.feature_launcher import main
from bmo_ai.features.host import FeatureHost,FeatureSimulatedClient
from bmo_ai.features.camera import DemoBackend
from bmo_ai.features.games import FakeGameRunner
from bmo_ai.features import media
from bmo_ai.integration_voice import IntegrationVoice

allowed={'PATH','HOME','LC_ALL','LANG','LC_CTYPE','PYTHONPATH','PYTHONDONTWRITEBYTECODE'}
assert not set(os.environ)-allowed,'Unexpected child environment field'
counts=dict(brain=0,listen=0,synthesis=0,players=0,reaped=0,game_starts=0,game_b=0,
            camera_open=0,camera_close=0,photos=0,forbidden=0)
processes=[];hosts=[]
offset=lambda:float(Path(sys.argv[3]).read_text())
clock=lambda:time.monotonic()+offset()
real_popen=subprocess.Popen
real_listen,real_speak,real_play=IntegrationVoice.listen,IntegrationVoice.speak,IntegrationVoice._play
real_create=FeatureSimulatedClient.create
real_game_start,real_game_input=FakeGameRunner.start,FakeGameRunner.input
real_cam_open,real_cam_close,real_cam_photo=DemoBackend.open,DemoBackend.close,DemoBackend.capture_photo
real_media=media.make_fixture_media
def forbidden(*args,**kwargs):
    counts['forbidden']+=1
    raise AssertionError('Real IO forbidden')
def spawn(command,**options):
    assert command==[sys.executable,'-c','import time; time.sleep(0.8)'],'Non-synthetic child forbidden'
    assert not set(options.get('env',{}))-allowed,'Unexpected player environment field'
    process=real_popen(command,**options);processes.append(process);counts['players']+=1
    return process
def listen(self):
    assert self.simulate,'Live microphone forbidden'
    counts['listen']+=1;time.sleep(.15)
    return real_listen(self)
def speak(self,text,**options):
    assert text in ('A short synthetic feature response.','Ta-da! Pretty good, huh?'),'JSON/tag reached speech boundary'
    counts['synthesis']+=1
    return real_speak(self,text,**options)
def play(self,*args,**options):
    bound=inspect.signature(real_play).bind(self,*args,**options);emit=bound.arguments['emit']
    def held(kind,**fields):
        emit(kind,**fields)
        if kind=='prepared':time.sleep(.15)
    bound.arguments['emit']=held
    return real_play(*bound.args,**bound.kwargs)
def create(self,**request):
    assert request.get('text',{}).get('format',{}).get('name')=='bmo_feature_reply_v1','Wrong feature schema'
    counts['brain']+=1;time.sleep(.15)
    return real_create(self,**request)
def game_start(self,item):
    counts['game_starts']+=1
    return real_game_start(self,item)
def game_input(self,key,pressed):
    if key=='B':counts['game_b']+=1
    return real_game_input(self,key,pressed)
def cam_open(self,generation):
    result=real_cam_open(self,generation);counts['camera_open']+=1;return result
def cam_close(self):
    opened=self._opened;result=real_cam_close(self)
    if opened:counts['camera_close']+=1
    return result
def cam_photo(self):
    counts['photos']+=1
    return real_cam_photo(self)
def fixture_media(root):
    # Rebuild only generated, owned test WAVs; never a user's media asset.
    result=[]
    for asset in real_media(root):
        path=Path(root)/asset.path
        with wave.open(str(path),'wb') as output:
            output.setparams((1,2,8000,0,'NONE','NONE'));output.writeframes(bytes(8000*2*8))
        result.append(replace(asset,duration=8.0,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(result)
class CheckedHost(FeatureHost):
    def poll(self):
        super().poll();self.fixture_offset=offset()
    def snapshot(self):
        return super().snapshot()|{'test_clock_offset':getattr(self,'fixture_offset',0.0)}
def factory(**settings):
    reply=lambda emotion:dict(version=1,speech='A short synthetic feature response.',
                             emotion=emotion,gesture='none',intent=None)
    host=CheckedHost(**settings,client=FeatureSimulatedClient([reply('crying'),reply('happy')]))
    hosts.append(host);return host
def interrupt(*args):raise KeyboardInterrupt()
signal.signal(signal.SIGTERM,interrupt)
with ExitStack() as guards:
    for target in ('socket.socket.connect','bmo_ai.cloud.create_client','bmo_ai.audio.record_wav',
                   'bmo_ai.audio.transcribe','bmo_ai.fish_tts.synthesize_wav','bmo_ai.local_piper.synthesize_wav',
                   'bmo_ai.features.camera.Picamera2Backend.open','bmo_ai.features.camera.OpenCVFaceDetector.detect'):
        guards.enter_context(patch(target,side_effect=forbidden))
    guards.enter_context(patch('subprocess.Popen',side_effect=spawn))
    for owner,name,callback in ((IntegrationVoice,'listen',listen),(IntegrationVoice,'speak',speak),
        (IntegrationVoice,'_play',play),(FeatureSimulatedClient,'create',create),
        (FakeGameRunner,'start',game_start),(FakeGameRunner,'input',game_input),
        (DemoBackend,'open',cam_open),(DemoBackend,'close',cam_close),(DemoBackend,'capture_photo',cam_photo),
        (media,'make_fixture_media',fixture_media)):
        guards.enter_context(patch.object(owner,name,callback))
    result=main(['--simulate','--demo-assets','--port','0','--screen-profile',sys.argv[4],
                 '--face-file',sys.argv[1]],lock_path=sys.argv[2],clock=clock,host_factory=factory)
for process in processes:
    assert process.poll() is not None,'Owned child survived quit'
    try:os.waitpid(process.pid,os.WNOHANG)
    except ChildProcessError:counts['reaped']+=1
    else:raise AssertionError('Owned child not reaped')
for host in hosts:
    assert not host.camera.snapshot()['on'],'Camera remained on'
    assert host.games.snapshot()['active_id'] is None and host.media.snapshot()['active_id'] is None
    assert host.audio.owner is None,'Audio owner remained after quit'
    assert not Path(host._temporary.name).exists(),'Owned fixtures survived quit'
assert counts['camera_open']==counts['camera_close'],'Unbalanced camera ownership'
print('BUNDLE_RESULT '+json.dumps(counts),flush=True)
raise SystemExit(result)
'''


def wait(page,predicate,label,*,arg=None):
    try:
        page.wait_for_function(predicate,arg=arg)
    except Exception:
        metadata=page.evaluate("""() => {const s=BMOFeatures.state;return {mode:s?.mode,
          phase:s?.phase,emotion:s?.emotion,audio:s?.audio_owner,busy:s?.conversation_busy,
          result:s?.last_result,camera:s?.camera&&{on:s.camera.on,tracking:s.camera.tracking,preview:s.camera.preview}}}""")
        raise AssertionError(label+': '+json.dumps(metadata)) from None


def run(face_path,chromium,profile='v13.2'):
    from playwright.sync_api import sync_playwright
    path=Path(face_path).expanduser().resolve(strict=True)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert before==SCREEN_PROFILES[profile]['sha256'],'Selected screen pin mismatch'
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-bundle-browser-') as home:
        clock_path=Path(home)/'clock.txt';clock_path.write_text('0')
        environment={'PATH':os.defpath,'HOME':home,'LC_ALL':'C.UTF-8',
                     'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONDONTWRITEBYTECODE':'1'}
        process=subprocess.Popen([sys.executable,'-u','-B','-c',CHILD,str(path),str(Path(home)/'launcher.lock'),str(clock_path),profile],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=environment)
        try:
            url=launcher_url(process)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=chromium,headless=True,timeout=6000,env=environment,
                    args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output'])
                try:
                    page=browser.new_page(viewport={'width':1024,'height':768})
                    page.set_default_timeout(5000)
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url) else route.abort())
                    page.add_init_script("""window.__mediaPlayCalls=0;
                      HTMLMediaElement.prototype.play=function(){window.__mediaPlayCalls++;
                        throw new Error('Browser audio forbidden')};""")
                    page.goto(url)
                    wait(page,"()=>window.BMOFeatures?.state&&document.querySelector('#face').contentWindow.BMOFace?.getState().version==='13.2'",'Shell/approved face did not load')
                    page.evaluate("""() => {
                      window.face=document.querySelector('#face').contentWindow.BMOFace;
                      window.observed=[];window.facePlayCalls=0;window.nativeGazeOverrides=0;
                      for(const method of ['playAudio','playBlob'])face[method]=()=>{facePlayCalls++;throw new Error('Browser audio forbidden')};
                      for(const method of ['look','lookEyes']){
                        const original=face[method];face[method]=function(...args){
                          if(['listening','thinking'].includes(BMOIntegration.state?.phase)
                             ||['listening','thinking'].includes(face.getState().activity))nativeGazeOverrides++;
                          return original.apply(this,args);
                        };
                      }
                      function sample(){const s=BMOFeatures.state,f=face.getState(),v=BMOIntegration.state;
                        if(s)observed.push({mode:s.mode,phase:s.phase,emotion:f.selectedEmotion,activity:f.activity,
                          speech:f.speech,mouth:f.mouth,gaze:s.gaze.owner,playback:v?.playback});
                        if(observed.length>2000)observed.shift();requestAnimationFrame(sample);
                      }sample();
                    }""")
                    assert not page.evaluate('()=>BMOFeatures.state.camera.on'),'Camera default was on'
                    assert page.locator('#face').get_attribute('src').endswith('?kiosk=1')
                    with urlopen(url+'api/screen',timeout=2) as response:metadata=json.load(response)
                    assert metadata==dict(version=profile,sha256=before,approval_status=SCREEN_PROFILES[profile]['approval_status'],pi_live_verified=False)
                    checks.append('actual simulation launcher serves selected pinned original inside external kiosk shell')
                    offset=0.0

                    def advance(amount):
                        nonlocal offset
                        assert 0<amount<=1.3
                        with urlopen(url+'api/features',timeout=2):pass
                        offset+=amount
                        temporary=clock_path.with_suffix('.next');temporary.write_text(str(offset));os.replace(temporary,clock_path)
                        with urlopen(url+'api/features',timeout=2):pass
                        wait(page,'offset=>BMOFeatures.state?.test_clock_offset>=offset','Owner did not process fixture clock',arg=offset)

                    def control(action,value=None,*,terminal=True):
                        receipt=page.evaluate('([action,value])=>BMOFeatures.control(action,value)',[action,value])
                        if terminal:
                            wait(page,"id=>BMOFeatures.state.requests.some(r=>r.request_id===id&&!['accepted','running'].includes(r.status))",'Control did not complete',arg=receipt['request_id'])
                            result=page.evaluate('id=>BMOFeatures.state.requests.find(r=>r.request_id===id)',receipt['request_id'])
                            assert result['status'] not in ('failed','rejected','unknown'),result
                        return receipt

                    def conversation(emotion,*,cancel=False):
                        page.evaluate('()=>observed.length=0')
                        page.locator('#talk-button').click()
                        for phase in ('listening','thinking'):
                            wait(page,'phase=>observed.some(s=>s.activity===phase)','Missing native conversation activity',arg=phase)
                        wait(page,"emotion=>observed.some(s=>s.speech==='external'&&s.emotion===emotion)",'Player did not start selected emotion',arg=emotion)
                        if cancel:control('cancel')
                        wait(page,"emotion=>{const s=BMOFeatures.state,f=face.getState();return !s.conversation_busy&&s.audio_owner===null&&f.speech==='off'&&f.mouth==='closed'&&f.selectedEmotion===emotion}",'Conversation did not clean up',arg=emotion)

                    conversation('crying')
                    checks.append('crying reply reaches player start/end and remains through idle')
                    page.locator('#games-button').click()
                    wait(page,"()=>BMOFeatures.state.mode==='game_menu'",'Game menu did not open')
                    page.locator('#game-list [data-catalog-id="fixture_game_a"]').click()
                    wait(page,"()=>BMOFeatures.state.mode==='game'&&BMOFeatures.state.games.active_id==='fixture_game_a'",'First fixture game did not start')
                    assert page.locator('#game-frame').get_attribute('sandbox')=='allow-scripts'
                    frame=page.locator('#game-frame').content_frame
                    assert frame.locator('body').evaluate("()=>{try{void parent.document;return false}catch(error){return error.name==='SecurityError'}}")
                    assert frame.locator('body').evaluate('()=>window.__mediaPlayCalls')==0
                    control('input',{'key':'B','pressed':True});control('input',{'key':'B','pressed':False})
                    assert page.evaluate("()=>BMOFeatures.state.mode==='game'")
                    page.evaluate('()=>document.activeElement.blur()');page.keyboard.press('Escape')
                    wait(page,"()=>BMOFeatures.state.mode==='game_menu'",'Escape did not return to menu')
                    page.locator('#game-list [data-catalog-id="fixture_game_b"]').click()
                    wait(page,"()=>BMOFeatures.state.games.active_id==='fixture_game_b'",'Second fixture game did not start')
                    control('input',{'key':'START','pressed':True});control('input',{'key':'SELECT','pressed':True})
                    advance(.6);assert page.evaluate("()=>BMOFeatures.state.mode==='game'")
                    advance(.7)
                    wait(page,"()=>BMOFeatures.state.mode==='game_menu'",'Start+Select hold did not exit')
                    page.locator('#face-button').click()
                    wait(page,"()=>BMOFeatures.state.mode==='conversation'&&face.getState().selectedEmotion==='crying'",'Game return lost emotion')
                    checks.append('sandboxed two-game round trip keeps B in game, supports Escape/hold exit and preserves crying')

                    page.locator('#track-button').click()
                    wait(page,'()=>BMOFeatures.state.camera.tracking','Tracking did not enable')
                    page.locator('#preview-button').click()
                    wait(page,'()=>BMOFeatures.state.camera.preview&&document.querySelector("#preview").naturalWidth>0','Synthetic preview did not load')
                    page.locator('#photo-button').click()
                    wait(page,"()=>BMOFeatures.state.mode==='photo'&&BMOFeatures.state.photo_countdown!==null",'Photo countdown did not start')
                    for _ in range(5):advance(1.1)
                    wait(page,'()=>BMOFeatures.state.photo?.completed&&document.querySelector("#photo-result").naturalWidth>0','Synthetic photo was not verified/saved')
                    photo=page.evaluate('()=>BMOFeatures.state.photo')
                    assert photo['simulated'] and not photo['executed'] and photo['completion']=='synthetic_saved'
                    with urlopen(url+'api/photo/'+photo['photo_id'],timeout=2) as response:assert response.read(8)==b'\x89PNG\r\n\x1a\n'
                    wait(page,"()=>BMOFeatures.state.photo_phase==='photo_review'&&!BMOFeatures.state.conversation_busy&&BMOFeatures.state.audio_owner===null",'Photo feedback did not finish in review')
                    page.locator('#face-button').click()
                    wait(page,"()=>BMOFeatures.state.mode==='conversation'&&BMOFeatures.state.camera.tracking&&BMOFeatures.state.camera.preview",'Photo return did not restore prior camera options')
                    page.locator('#camera-off').click()
                    wait(page,'()=>!BMOFeatures.state.camera.on&&!BMOFeatures.state.camera.tracking&&!BMOFeatures.state.camera.preview','Camera off failed')
                    page.locator('#face-button').click()
                    wait(page,"()=>BMOFeatures.state.mode==='conversation'",'Face return failed')
                    assert not page.evaluate('()=>BMOFeatures.state.camera.on')
                    checks.append('single synthetic camera preview/photo/countdown saves verified PNG and restores options; explicit off stays off')

                    page.locator('#songs').select_option('fixture_song_a');page.locator('#song-play').click()
                    wait(page,"()=>BMOFeatures.state.mode==='music'&&BMOFeatures.state.audio_owner==='music'",'Media did not acquire sole audio owner')
                    control('pause');paused=page.evaluate('()=>BMOFeatures.state.media.position')
                    advance(1.1)
                    assert page.evaluate("()=>BMOFeatures.state.media.state==='paused'")
                    assert abs(page.evaluate('()=>BMOFeatures.state.media.position')-paused)<.01
                    control('resume');control('next')
                    wait(page,"()=>BMOFeatures.state.media.active_id==='fixture_song_b'",'Next fixture media did not start')
                    for _ in range(8):
                        advance(1.1)
                        if page.evaluate("()=>BMOFeatures.state.mode==='conversation'"):break
                    wait(page,"()=>BMOFeatures.state.mode==='conversation'&&BMOFeatures.state.audio_owner===null&&face.getState().selectedEmotion==='crying'",'Media completion did not release audio/return emotion')
                    assert not page.evaluate('()=>BMOFeatures.state.camera.on')
                    checks.append('silent generated media supports play/pause/resume/next/end with exclusive audio and emotion-preserving return')

                    page.locator('#track-button').click();wait(page,'()=>BMOFeatures.state.camera.tracking','Second tracking activation failed')
                    conversation('happy',cancel=True)
                    staged=page.evaluate("()=>observed.filter(s=>['listening','thinking'].includes(s.activity)&&s.speech==='off')")
                    assert staged and all(s['emotion']=='crying' for s in staged),'Next question discarded crying before player start'
                    assert any(s['gaze']=='native' for s in staged)
                    assert page.evaluate('()=>nativeGazeOverrides')==0,'Camera/shell gaze overrode native activity'
                    control('reset')
                    wait(page,"()=>face.getState().selectedEmotion==='neutral'&&!BMOFeatures.state.conversation_busy&&!BMOFeatures.state.camera.on",'Explicit reset failed')
                    assert page.evaluate('()=>window.__mediaPlayCalls+window.facePlayCalls')==0
                    assert page.locator('#face').content_frame.locator('body').evaluate('()=>window.__mediaPlayCalls')==0
                    checks.append('next native listening/thinking keeps crying until happy playback; cancel retains happy; explicit reset is neutral')
                    stdout,_=process.communicate('/quit\n',timeout=5)
                    assert process.returncode==0,'Owned feature launcher did not exit cleanly'
                    summaries=[json.loads(line.removeprefix('BUNDLE_RESULT ')) for line in stdout.splitlines() if line.startswith('BUNDLE_RESULT ')]
                    assert len(summaries)==1
                    counts=summaries[0]
                    assert counts['brain']==counts['listen']==2,counts
                    assert counts['synthesis']==counts['players']==counts['reaped']==3,counts
                    assert counts['game_starts']==2 and counts['game_b']>=2 and counts['photos']==1 and counts['forbidden']==0,counts
                    with_error=False
                    try:urlopen(url+'api/features',timeout=.3)
                    except URLError as error:with_error=isinstance(error.reason,ConnectionRefusedError)
                    assert with_error,'Feature loopback server survived quit'
                    checks.append('quit releases camera/game/media/audio/owned children/server and deletes only generated test assets; real/browser audio calls=0')
                finally:browser.close()
        finally:
            if process.poll() is None:
                try:process.communicate('/quit\n',timeout=3)
                except (subprocess.TimeoutExpired,BrokenPipeError,ValueError):
                    process.terminate()
                    try:process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.communicate()
    after=hashlib.sha256(path.read_bytes()).hexdigest();assert after==before
    return dict(status='PASS',screen_profile=profile,approval_status=SCREEN_PROFILES[profile]['approval_status'],
        sha256_before=before,sha256_after=after,checks=checks,counts=counts,
        fixture_note='150ms stage holds, generated 8-second PCM and controlled clock; no performance/real-media measurement',
        not_run=['real APIs/audio/camera/serial/servo/IMU','real game pack/pad/song content','Pi LCD/hardware acceptance'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen-profile',choices=tuple(SCREEN_PROFILES),default='v13.2')
    parser.add_argument('--face',type=Path)
    parser.add_argument('--chromium',default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.face or SCREEN_PROFILES[args.screen_profile]['default_path'],args.chromium,args.screen_profile),indent=2))


if __name__=='__main__':main()
