#!/usr/bin/env python3
"""800x480 actual launcher/shell smoke: public contract fixture or local pinned art.

Default uses a text-only synthetic BMOFace contract. A test child adds an isolated
ci-synthetic profile; production hashes and original HTML are never changed.
--screen-profile r10/v13.2 instead checks the untouched approved local file.
Synthetic 150ms stage holds and controlled clocks are observations, not latency
measurements. No real API, optional camera import, device or browser audio runs.
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

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bmo_ai.screen_profiles import SCREEN_PROFILES
from check_feature_bundle_browser import CHILD, wait
from check_integration_end_to_end import launcher_url

FIXTURE=Path(__file__).with_name('fixtures')/'bmo_face_contract.html'
SYNTHETIC_CHILD=r'''
import builtins,hashlib,sys
from pathlib import Path
real_import=builtins.__import__
def no_optional_devices(name,*args,**kwargs):
    if name.split('.')[0] in ('picamera2','libcamera','cv2','numpy'):
        raise AssertionError('Real optional device import forbidden')
    return real_import(name,*args,**kwargs)
builtins.__import__=no_optional_devices
if sys.argv[4]=='ci-synthetic':
    import bmo_ai.screen_profiles as registry
    import bmo_ai.integration_face as face
    fixture=Path(sys.argv[5]).resolve(strict=True)
    assert fixture==Path(sys.argv[1]).resolve(strict=True),'Only repository fixture may override a test pin'
    payload=fixture.read_bytes()
    assert b'data-synthetic-contract="bmo-face-v1"' in payload
    records=dict(registry.SCREEN_PROFILES)
    records['ci-synthetic']=dict(default_path=str(fixture),sha256=hashlib.sha256(payload).hexdigest(),
        label='Public synthetic text API fixture; no artwork',approval_status='synthetic_test_only',pi_live_verified=False)
    registry.SCREEN_PROFILES=records
    face.SCREEN_PROFILES=records
'''


def run(profile='ci-synthetic',face_path=None,chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    path=(FIXTURE if profile=='ci-synthetic' else Path(face_path or SCREEN_PROFILES[profile]['default_path'])).resolve(strict=True)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    if profile!='ci-synthetic':
        assert before==SCREEN_PROFILES[profile]['sha256'],'Selected original pin mismatch'
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-offline-shell-') as home:
        offset_path=Path(home)/'clock.txt';offset_path.write_text('0')
        environment={'PATH':os.defpath,'HOME':home,'LC_ALL':'C.UTF-8',
            'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONDONTWRITEBYTECODE':'1'}
        process=subprocess.Popen([sys.executable,'-u','-B','-c',SYNTHETIC_CHILD+CHILD,
            str(path),str(Path(home)/'launcher.lock'),str(offset_path),profile,str(FIXTURE.resolve())],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=environment)
        try:
            url=launcher_url(process)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=chromium,headless=True,timeout=8000,env=environment,
                    args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                          '--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
                try:
                    page=browser.new_page(viewport={'width':800,'height':480})
                    page.set_default_timeout(5000)
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url) else route.abort())
                    page.add_init_script("""window.__mediaPlayCalls=0;
                      HTMLMediaElement.prototype.play=function(){window.__mediaPlayCalls++;throw Error('Browser audio forbidden')};""")
                    def ready():
                        wait(page,"()=>window.BMOFeatures?.state&&document.querySelector('#face').contentWindow.BMOFace",'Actual shell/face did not load')
                        page.evaluate("""()=>{window.face=document.querySelector('#face').contentWindow.BMOFace;
                          window.observed=[];window.facePlayCalls=0;
                          for(const name of ['playAudio','playBlob'])face[name]=()=>{facePlayCalls++;throw Error('Browser audio forbidden')};
                          function sample(){const f=face.getState(),s=BMOFeatures.state;
                            observed.push({emotion:f.selectedEmotion,activity:f.activity,speech:f.speech,mouth:f.mouth,
                              phase:BMOIntegration.state?.phase,playback:BMOIntegration.state?.playback});
                            if(observed.length>2000)observed.shift();requestAnimationFrame(sample);}sample();}""")
                    page.goto(url);ready()
                    with urlopen(url+'face.html?kiosk=1',timeout=2) as response:assert response.read()==path.read_bytes()
                    with urlopen(url+'api/screen',timeout=2) as response:metadata=json.load(response)
                    assert metadata['version']==profile and metadata['sha256']==before and metadata['pi_live_verified'] is False
                    if profile=='ci-synthetic':
                        assert page.evaluate('()=>face.getState().fixture') is True
                        assert metadata['approval_status']=='synthetic_test_only'
                    else:assert metadata['approval_status']==SCREEN_PROFILES[profile]['approval_status']
                    assert page.locator('#face').get_attribute('src')=='/face.html?kiosk=1'
                    assert not page.evaluate('()=>BMOFeatures.state.camera.on')
                    page.locator('#controls-toggle').click()
                    bounds=page.locator('#face').bounding_box()
                    assert bounds==dict(x=0,y=0,width=800,height=480),bounds
                    assert page.evaluate('()=>document.documentElement.scrollHeight===480&&document.documentElement.scrollWidth===800')
                    assert not page.locator('#shell-header').is_visible() and not page.locator('#shell-footer').is_visible()
                    page.locator('#controls-toggle').click()
                    checks.append('actual private launcher, selected bytes/metadata and 800x480 full-viewport iframe; camera defaults off')
                    offset=0.0
                    def advance(amount):
                        nonlocal offset
                        assert 0<amount<=1.3
                        with urlopen(url+'api/features',timeout=2):pass
                        offset+=amount
                        temporary=offset_path.with_suffix('.next');temporary.write_text(str(offset));os.replace(temporary,offset_path)
                        with urlopen(url+'api/features',timeout=2):pass
                        wait(page,'n=>BMOFeatures.state.test_clock_offset>=n','Owner fixture clock not observed',arg=offset)
                    def control(action,value=None):
                        receipt=page.evaluate('([a,v])=>BMOFeatures.control(a,v)',[action,value])
                        wait(page,"id=>BMOFeatures.state.requests.some(r=>r.request_id===id&&!['accepted','running'].includes(r.status))",'Control did not finish',arg=receipt['request_id'])
                        result=page.evaluate('id=>BMOFeatures.state.requests.find(r=>r.request_id===id)',receipt['request_id'])
                        assert result['status'] not in ('failed','rejected','unknown'),result
                        return result
                    def intent(name,target=None):
                        return page.evaluate('([n,t])=>BMOFeatures.intent(n,t)',[name,target])
                    def turn(emotion,cancel=False):
                        page.evaluate('()=>observed.length=0');control('question','Synthetic browser question.')
                        for activity in ('listening','thinking'):
                            wait(page,'a=>observed.some(s=>s.activity===a&&s.speech==="off")','Missing staged native activity',arg=activity)
                        wait(page,'e=>observed.some(s=>s.speech==="external"&&s.emotion===e)','Selected emotion did not reach player start',arg=emotion)
                        if cancel:control('cancel')
                        wait(page,'e=>!BMOFeatures.state.conversation_busy&&BMOFeatures.state.audio_owner===null&&face.getState().speech==="off"&&face.getState().selectedEmotion===e','Turn did not end preserving emotion',arg=emotion)
                    turn('crying')
                    prepared=page.evaluate("()=>observed.filter(s=>s.playback==='prepared')")
                    assert prepared and all(s['speech']=='off' and s['activity']=='thinking' for s in prepared)
                    assert page.evaluate('()=>face.getState().mouth')=='closed'
                    checks.append('crying listening/thinking/prepared preserves native thinking mouth without external speech; one Python player start/end retains crying')

                    intent('open_game_menu');wait(page,"()=>BMOFeatures.state.mode==='game_menu'",'Game menu failed')
                    intent('launch_game','fixture_game_a');wait(page,"()=>BMOFeatures.state.mode==='game'",'Game start failed')
                    assert page.locator('#game-frame').get_attribute('sandbox')=='allow-scripts'
                    frame=page.locator('#game-frame').content_frame
                    assert frame.locator('body').evaluate("()=>{try{void parent.document;return false}catch(e){return e.name==='SecurityError'}}")
                    original=page.evaluate('()=>[BMOFeatures.state.mode_generation,BMOFeatures.state.requests.length]')
                    page.evaluate("()=>window.postMessage({version:1,type:'bmo-intent',intent:{name:'take_photo'}},'*')")
                    frame.locator('body').evaluate("()=>parent.postMessage({type:'bmo-control',action:'stop'},'*')")
                    page.wait_for_timeout(160)
                    assert page.evaluate('()=>[BMOFeatures.state.mode_generation,BMOFeatures.state.requests.length]')==original
                    control('input',{'key':'B','pressed':True});control('input',{'key':'B','pressed':False})
                    assert page.evaluate("()=>BMOFeatures.state.mode==='game'")
                    control('input',{'key':'ESC','pressed':True})
                    wait(page,"()=>BMOFeatures.state.mode==='game_menu'",'Game exit did not return menu')
                    intent('return_to_face');wait(page,"()=>BMOFeatures.state.mode==='conversation'&&face.getState().selectedEmotion==='crying'",'Game lost crying')
                    checks.append('game/menu/face retains emotion; B stays in game; sandbox rejects parent access and untrusted postMessage has no side effect')

                    intent('start_face_tracking');control('preview',True)
                    wait(page,'()=>BMOFeatures.state.camera.tracking&&document.querySelector("#preview").naturalWidth>0','Synthetic preview failed')
                    intent('take_photo');wait(page,"()=>BMOFeatures.state.mode==='photo'",'Photo mode failed')
                    wait(page,"()=>BMOFeatures.state.photo_phase==='countdown'&&document.querySelector('#preview').complete&&document.querySelector('#preview').naturalWidth>0",'Displayed preview did not start photo countdown')
                    assert page.evaluate('()=>BMOFeatures.state.photo_countdown')>4
                    for _ in range(5):advance(1.1)
                    wait(page,'()=>BMOFeatures.state.photo?.completed','Synthetic photo not saved')
                    photo=page.evaluate('()=>BMOFeatures.state.photo')
                    assert photo['simulated'] and not photo['executed'] and photo['completion']=='synthetic_saved'
                    wait(page,"()=>BMOFeatures.state.photo_phase==='photo_review'&&!BMOFeatures.state.conversation_busy&&BMOFeatures.state.audio_owner===null",'Photo feedback did not finish in review')
                    assert not page.evaluate('()=>BMOFeatures.state.camera.on')
                    intent('return_to_face');wait(page,"()=>BMOFeatures.state.mode==='conversation'&&BMOFeatures.state.camera.tracking&&BMOFeatures.state.camera.preview",'Photo tracking not restored')
                    intent('stop_face_tracking');wait(page,'()=>!BMOFeatures.state.camera.on','Camera off failed')
                    # Inject an old face snapshot after OFF; retired utterance cannot restart speech.
                    assert page.evaluate("""()=>{const s=BMOIntegration.state;return BMOIntegration.applySnapshot({...s,playback:'started',phase:'speaking',sequence:s.sequence+1,cues:[]})}""") is False
                    assert not page.evaluate('()=>BMOFeatures.state.camera.on')
                    checks.append('displayed preview starts five-second countdown; one synthetic PNG and success feedback stay in review, explicit return restores tracking, and OFF/late events cannot reactivate')

                    intent('play_song','fixture_song_a');wait(page,"()=>BMOFeatures.state.audio_owner==='music'",'Media focus failed')
                    control('pause');position=page.evaluate('()=>BMOFeatures.state.media.position');advance(1.1)
                    assert abs(page.evaluate('()=>BMOFeatures.state.media.position')-position)<.01
                    assert page.evaluate('()=>window.__mediaPlayCalls+window.facePlayCalls')==0
                    assert page.locator('#face').content_frame.locator('body').evaluate('()=>window.__mediaPlayCalls')==0
                    page.reload();ready()
                    wait(page,"()=>BMOFeatures.state.mode==='music'&&BMOFeatures.state.media.state==='paused'&&face.getState().selectedEmotion==='crying'",'Reload did not reconnect paused media/emotion')
                    assert not page.evaluate('()=>BMOFeatures.state.camera.on')
                    # visibility suspends face polling, while HTTP heartbeat is separate.
                    page.evaluate("""()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));}""")
                    with urlopen(url+'api/features',timeout=2):pass
                    page.evaluate("""()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));}""")
                    wait(page,"()=>BMOIntegration.state?.emotion==='crying'",'Visibility reconnect failed')
                    control('resume');intent('stop_media')
                    wait(page,"()=>BMOFeatures.state.mode==='conversation'&&BMOFeatures.state.audio_owner===null&&face.getState().selectedEmotion==='crying'",'Media stop lost face/audio focus')
                    checks.append('media pause freezes position; refresh/visibility reconnect preserves crying, paused state and camera OFF; stop returns focus')

                    turn('happy',cancel=True)
                    prior=page.evaluate("()=>observed.filter(s=>['listening','thinking'].includes(s.activity))")
                    assert prior and all(item['emotion']=='crying' for item in prior)
                    control('reset');wait(page,"()=>face.getState().selectedEmotion==='neutral'&&!BMOFeatures.state.camera.on",'Reset did not clear explicitly')
                    assert page.evaluate('()=>window.__mediaPlayCalls+window.facePlayCalls')==0
                    assert page.locator('#face').content_frame.locator('body').evaluate('()=>window.__mediaPlayCalls')==0
                    stdout,_=process.communicate('/quit\n',timeout=5)
                    assert process.returncode==0,'Owned launcher quit failed'
                    summaries=[json.loads(line.removeprefix('BUNDLE_RESULT ')) for line in stdout.splitlines() if line.startswith('BUNDLE_RESULT ')]
                    assert len(summaries)==1;counts=summaries[0]
                    assert counts['brain']==counts['listen']==2,counts
                    assert counts['synthesis']==counts['players']==counts['reaped']==3,counts
                    assert counts['photos']==1 and counts['camera_open']==counts['camera_close'] and counts['forbidden']==0,counts
                    try:urlopen(url+'api/features',timeout=.3)
                    except URLError as error:assert isinstance(error.reason,ConnectionRefusedError)
                    else:raise AssertionError('Owned server survived quit')
                    checks.append('next question keeps crying until happy start; cancel keeps happy; reset neutral; quit reaps owned children/server, browser/live audio=0')
                finally:browser.close()
        finally:
            if process.poll() is None:
                try:process.communicate('/quit\n',timeout=3)
                except (subprocess.TimeoutExpired,BrokenPipeError,ValueError):
                    process.terminate()
                    try:process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:process.kill();process.communicate()
    after=hashlib.sha256(path.read_bytes()).hexdigest();assert before==after
    return dict(status='PASS',screen_profile=profile,asset_kind='synthetic_api_contract' if profile=='ci-synthetic' else 'unmodified_approved_local_HTML',
        sha256_before=before,sha256_after=after,checks=checks,counts=counts,
        fixture_note='controlled clock, synthetic stage holds/PNG/PCM; no real performance measurement',
        not_run=['real APIs/audio/camera/servo/IMU/pad/serial','Pi LCD and physical acceptance'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen-profile',choices=('ci-synthetic','r10','v13.2'),default='ci-synthetic')
    parser.add_argument('--face',type=Path)
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium',
                        help='Chromium path; pass this flag without a value for Playwright bundled Chromium')
    args=parser.parse_args()
    if args.screen_profile=='ci-synthetic' and args.face is not None:
        parser.error('--face is only for pinned local r10/v13.2 profiles')
    print(json.dumps(run(args.screen_profile,args.face,args.chromium),indent=2))


if __name__=='__main__':main()
