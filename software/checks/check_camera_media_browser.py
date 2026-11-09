#!/usr/bin/env python3
"""Product camera/video/game/music routes against synthetic broker/backends only."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit

SOFTWARE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOFTWARE))
from checks.check_desktop_app_browser import DesktopFixture,FIXTURE
from bmo_ai.features.media.video_fixture import validate_avi_bytes
from bmo_ai.screen_profiles import SCREEN_PROFILES


def run(profile='ci-synthetic',chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    face=FIXTURE if profile=='ci-synthetic' else Path(SCREEN_PROFILES[profile]['default_path'])
    before=hashlib.sha256(face.read_bytes()).hexdigest()
    if profile!='ci-synthetic':assert before==SCREEN_PROFILES[profile]['sha256']
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-camera-media-browser-') as directory:
        root=Path(directory);fixture=DesktopFixture(root,profile=profile,face=face,scenario='media',
                                                   extra_args=('--usb-gamepad',))
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=chromium,headless=True,
                    env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                    args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                          '--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
                try:
                    page=browser.new_page(viewport={'width':800,'height':480},accept_downloads=True)
                    page.set_default_timeout(8000)
                    page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                    preview_requests={'latest':0,'legacy':0}
                    music_commands=[]
                    def music_requested(request):
                        if request.method=='POST' and request.url.endswith('/api/control'):
                            payload=request.post_data_json.get('payload',{})
                            if payload.get('action') in ('media_command','next'):music_commands.append(payload)
                    page.on('request',music_requested)
                    def preview_requested(request):
                        path=urlsplit(request.url).path
                        if path=='/api/preview/frame':preview_requests['latest']+=1
                        elif path=='/api/preview':preview_requests['legacy']+=1
                    page.on('request',preview_requested)
                    page.add_init_script("""window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){
                        window.__audioCalls++;throw Error('Browser audio forbidden')};
                        window.__padCalls=0;window.__padButtons=[];window.__padConnected=true;
                        Object.defineProperty(navigator,'getGamepads',{value:()=>{
                            window.__padCalls++;return window.__padConnected?[{index:0,connected:true,
                            mapping:'standard',buttons:Array.from({length:16},(_,i)=>({pressed:window.__padButtons.includes(i)})),axes:[0,0]}]:[];
                        }});""")
                    def wait_state(predicate,timeout=8):
                        deadline=time.monotonic()+timeout
                        while time.monotonic()<deadline:
                            value=fixture.features()
                            if predicate(value):return value
                            page.wait_for_timeout(25)
                        raise AssertionError('Synthetic media state timeout: '+json.dumps(dict(
                            {k:value.get(k) for k in ('mode','camera_video','last_result')},
                            browser=page.evaluate("()=>({input:BMODesktop.inputEvidence,mode:BMODesktop.features?.mode,active:document.activeElement.id,dialogs:[...document.querySelectorAll('dialog[open]')].map(e=>e.id)})"))))
                    def panel(button,dialog):
                        page.evaluate('()=>BMODesktop.menu()')
                        if button=='system-menu':
                            page.locator('#settings-menu').click()
                        elif button=='video-menu':
                            page.locator('#camera-menu').click()
                        page.locator('#'+button).click()
                        page.locator('#'+dialog).wait_for(state='visible')
                    @contextmanager
                    def frozen_features():
                        held=[]
                        def hold(route):held.append(route)
                        page.route('**/api/features',hold)
                        try:
                            wait_state(lambda _:bool(held))
                            yield
                        finally:
                            page.unroute('**/api/features',hold)
                            for route in held:route.continue_()
                    def stops():
                        return [item['value'] for item in music_commands if item.get('action')=='media_command'
                            and item.get('value',{}).get('action')=='stop']
                    def first_song():
                        panel('music-menu','music-dialog');page.locator('#music-list button').first.click()
                        wait_state(lambda s:s['mode']=='music' and s['media']['active_id']=='fixture_song_a')
                        page.wait_for_function("()=>BMODesktop.features?.mode==='music'&&BMODesktop.features.media.active_id==='fixture_song_a'")
                    page.goto(fixture.url,wait_until='domcontentloaded')
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                    initial=fixture.features()
                    assert initial['emotion']=='sad' and not initial['camera']['on']
                    assert not initial['camera_video']['recording'] and not (root/'video-content').exists()
                    assert not page.locator('dialog[open]').count()
                    assert not page.locator('#video-indicator').is_visible()
                    assert fixture.features()['test_fixture']['forbidden']==0
                    assert initial['usb_gamepad']['enabled'] and initial['usb_gamepad']['device_opens']==0
                    assert page.evaluate('()=>window.__padCalls')==0
                    checks.append('default screen remains the unchanged face; opening app creates no video store/camera/PCM/brain turn')

                    panel('video-menu','video-dialog')
                    assert page.locator('#video-start').is_disabled()
                    page.locator('#video-consent').check()
                    page.locator('#video-start').click()
                    wait_state(lambda s:s['camera_video']['recording'])
                    page.locator('#video-indicator').wait_for(state='visible')
                    assert fixture.features()['camera']['on']
                    assert page.locator('#video-start').is_disabled()
                    # Enter in a settings dialog is not a hidden PTT trigger.
                    page.locator('#video-result').focus()
                    page.keyboard.press('Enter')
                    page.evaluate("()=>BMODesktop.control('ptt')")
                    page.wait_for_timeout(80)
                    assert fixture.features()['test_fixture']['brain']==0
                    page.locator('#video-stop').click()
                    state=wait_state(lambda s:bool(s['camera_video']['result'] and s['camera_video']['result']['completed']))
                    result=state['camera_video']['result'];saved=root/'video-content'/(result['video_id']+'.avi')
                    payload=saved.read_bytes();meta=validate_avi_bytes(payload)
                    assert result['sha256']==hashlib.sha256(payload).hexdigest()
                    assert meta['audio_streams']==0 and result['executed'] is False
                    assert stat.S_IMODE(saved.stat().st_mode)==0o600
                    assert stat.S_IMODE(saved.parent.stat().st_mode)==0o700
                    page.locator('#video-download').wait_for(state='visible')
                    status,download=fixture.request('/api/video/'+result['video_id'])
                    assert status==200 and download==payload
                    assert fixture.request('/api/video/video_'+'a'*32)[0]==404
                    assert fixture.request('/api/video/%2e%2e')[0]==404
                    checks.append('explicit consent/start/stop uses one synthetic camera broker and saves validated private silent AVI; same-origin download is exact')
                    page.locator('#video-dialog .dialog-close').click()
                    page.locator('#video-indicator').wait_for(state='hidden')
                    assert fixture.features()['emotion']=='sad'

                    panel('games','game-menu')
                    page.locator('#game-list button').first.click()
                    wait_state(lambda s:s['mode']=='game')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='game'")
                    assert fixture.features()['audio_owner']=='game'
                    page.evaluate('()=>BMODesktop.menu()')
                    page.keyboard.press('Escape')
                    assert not page.locator('#menu').is_visible()
                    assert fixture.features()['mode']=='game'
                    page.locator('#face').focus();page.keyboard.press('Enter');page.keyboard.press('b')
                    assert fixture.features()['test_fixture']['brain']==0
                    page.keyboard.press('Escape')
                    wait_state(lambda s:s['mode']=='game_menu')
                    page.locator('#game-back').click()
                    wait_state(lambda s:s['mode']=='conversation')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'")
                    assert fixture.features()['emotion']=='sad' and fixture.features()['audio_owner'] is None
                    checks.append('fixture game launches and returns through the existing InputRouter; Enter/B do not open PTT and emotion/history persist')

                    # The real production Gamepad API reader is replaced only
                    # at navigator.getGamepads. Server mode/action routing and
                    # timing remain the application used on the Pi.
                    calls=page.evaluate('()=>window.__padCalls')
                    page.evaluate('()=>{window.__padButtons=[9];}')
                    page.wait_for_timeout(300)
                    assert page.evaluate('()=>window.__padCalls')==calls
                    assert fixture.features()['test_fixture']['brain']==0
                    panel('games','game-menu')
                    page.wait_for_function('()=>window.__padCalls>0')
                    page.wait_for_timeout(200)
                    assert fixture.features()['mode']=='game_menu'  # held at activation is suppressed
                    page.evaluate('()=>{window.__padButtons=[];}');page.wait_for_timeout(150)
                    page.evaluate('()=>{window.__padButtons=[0];}')
                    wait_state(lambda s:s['mode']=='game')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='game'")
                    page.evaluate('()=>{window.__padButtons=[];}');page.wait_for_timeout(150)
                    page.evaluate('()=>{window.__padButtons=[1];}');page.wait_for_timeout(150)
                    state=fixture.features()
                    assert state['usb_gamepad']['accepted_edges']>=2
                    assert state['mode']=='game' and state['test_fixture']['brain']==0
                    checks.append('opt-in standard browser pad is unpolled in conversation, suppresses held activation, launches a fixture and forwards B through game-only production routing')

                    page.evaluate('()=>{window.__padConnected=false;}')
                    wait_state(lambda s:s['mode']=='game_menu' and s['audio_owner'] is None)
                    assert fixture.features()['usb_gamepad']['held']==[]
                    page.evaluate('()=>{window.__padConnected=true;window.__padButtons=[];}')
                    page.wait_for_timeout(180)
                    page.evaluate('()=>{window.__padButtons=[0];}')
                    wait_state(lambda s:s['mode']=='game')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='game'")
                    page.evaluate('()=>{window.__padButtons=[];}');page.wait_for_timeout(150)
                    page.evaluate('()=>{window.__padButtons=[8,9];}')
                    wait_state(lambda s:s['mode']=='game_menu',timeout=4)
                    assert fixture.features()['usb_gamepad']['held']==[]
                    assert fixture.features()['test_fixture']['brain']==0
                    checks.append('pad disconnect releases held edges and game audio; Start+Select hold uses existing game exit and cannot trigger GP8 conversation')

                    page.evaluate('()=>{window.__padButtons=[];}');page.wait_for_timeout(180)
                    page.evaluate('()=>{window.__padButtons=[0];}')
                    wait_state(lambda s:s['mode']=='game')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='game'")
                    page.evaluate('()=>{window.__padButtons=[];}');page.wait_for_timeout(150)
                    page.evaluate('()=>{window.__padButtons=[14];}')
                    wait_state(lambda s:'LEFT' in s['usb_gamepad']['held'])
                    page.evaluate("()=>window.dispatchEvent(new Event('blur'))")
                    wait_state(lambda s:s['mode']=='game_menu')
                    assert fixture.features()['usb_gamepad']['held']==[]
                    page.evaluate('()=>{window.__padButtons=[];}')
                    page.locator('#game-back').click()
                    wait_state(lambda s:s['mode']=='conversation')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'")
                    calls=page.evaluate('()=>window.__padCalls')
                    page.wait_for_timeout(200)
                    assert page.evaluate('()=>window.__padCalls')==calls
                    assert fixture.features()['test_fixture']['brain']==0
                    floor=fixture.features()['usb_gamepad']['sequence_floor']
                    assert floor>5
                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                    panel('games','game-menu');page.wait_for_timeout(180)
                    page.evaluate('()=>{window.__padButtons=[0];}')
                    wait_state(lambda s:s['mode']=='game' and s['usb_gamepad']['sequence_floor']>floor)
                    page.wait_for_function("()=>BMODesktop.features?.mode==='game'")
                    page.keyboard.press('Escape');wait_state(lambda s:s['mode']=='game_menu')
                    page.locator('#game-back').click();wait_state(lambda s:s['mode']=='conversation')
                    assert fixture.features()['test_fixture']['brain']==0
                    checks.append('blur/mode return neutralize gamepad; browser reload resumes above server sequence floor without stale input or another greeting')

                    panel('music-menu','music-dialog')
                    page.locator('#music-list button').first.click()
                    wait_state(lambda s:s['mode']=='music')
                    assert fixture.features()['audio_owner']=='music'
                    assert fixture.features()['media']['cue_kind']=='none'
                    page.locator('#music-pause').click();wait_state(lambda s:s['media']['state']=='paused')
                    page.locator('#music-resume').click();wait_state(lambda s:s['media']['state']=='playing')
                    page.wait_for_function("()=>BMODesktop.features?.media?.active_id==='fixture_song_a'")
                    before_stops=len(stops())
                    old_generation=page.evaluate('()=>BMODesktop.features.media.playback_generation')
                    with frozen_features():
                        with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/api/control')
                                and response.request.post_data_json.get('payload',{}).get('action')=='next') as admitted:
                            page.locator('#music-next').click()
                        next_request=admitted.value.json()['request_id']
                        advanced=wait_state(lambda s:s['media']['active_id']=='fixture_song_b')
                        assert advanced['media']['playback_generation']>old_generation
                        assert page.evaluate('()=>BMODesktop.features.media.playback_generation')==old_generation
                        for control in ('music-next','music-pause','music-resume','music-toggle','music-seek','music-replay'):
                            assert page.locator('#'+control).is_disabled(),control
                        assert page.locator('#music-stop').is_enabled()
                        page.locator('#music-stop').click()
                        page.evaluate('()=>Promise.resolve()')
                        assert len(stops())==before_stops,'Stop used the previous rendered track generation'
                    wait_state(lambda s:s['mode']=='conversation')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'")
                    assert len(stops())==before_stops+1
                    assert stops()[-1]['generation']==advanced['media']['playback_generation']
                    assert any(row['request_id']==next_request and row['status'] in ('running','simulated','succeeded')
                        for row in advanced['requests'])
                    checks.append('actual Next receipt and frozen old track render cannot send stale Stop; one human Stop waits for the acknowledged new playback generation while other track controls are disabled')

                    # A generation change not initiated by this browser can
                    # still reject a Stop. Its later receipt must unlock the
                    # latch, with no automatic retry, for a new human request.
                    first_song();before_stops=len(stops())
                    with frozen_features():
                        fixture.request('/api/control',payload={'action':'next','value':None})
                        advanced=wait_state(lambda s:s['media']['active_id']=='fixture_song_b')
                        with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/api/control')
                                and response.request.post_data_json.get('payload',{}).get('action')=='media_command'
                                and response.request.post_data_json['payload']['value']['action']=='stop') as stop_admitted:
                            page.locator('#music-stop').click()
                        stop_request=stop_admitted.value.json()['request_id']
                        rejected=wait_state(lambda s:any(row['request_id']==stop_request and row['code']=='stale_mode' for row in s['requests']))
                        assert rejected['mode']=='music'
                    page.wait_for_function("()=>BMODesktop.features?.media?.active_id==='fixture_song_b'&&document.querySelector('#music-surface').dataset.stopping==='false'&&!document.querySelector('#music-back').disabled")
                    assert len(stops())==before_stops+1,'Rejected Stop was automatically retried'
                    page.locator('#music-surface').click(position={'x':5,'y':5})
                    page.locator('#music-back').click();wait_state(lambda s:s['mode']=='conversation')
                    page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'")
                    assert len(stops())==before_stops+2
                    assert stops()[-1]['generation']==advanced['media']['playback_generation']
                    checks.append('terminal stale Stop rejection clears the busy latch without retry; a new explicit Back uses the fresh generation and returns to conversation')

                    # A queued Stop must not survive a distinct activity that
                    # was started while this browser's features were delayed.
                    first_song();before_stops=len(stops())
                    original_mode=page.evaluate('()=>BMODesktop.features.mode_generation')
                    with frozen_features():
                        page.locator('#music-next').click();wait_state(lambda s:s['media']['active_id']=='fixture_song_b')
                        page.locator('#music-stop').click()
                        fixture.request('/api/intent',payload={'name':'return_to_face','target':None})
                        wait_state(lambda s:s['mode']=='conversation')
                        fixture.request('/api/intent',payload={'name':'play_song','target':'fixture_song_a'})
                        newer=wait_state(lambda s:s['mode']=='music' and s['mode_generation']>original_mode and s['media']['active_id']=='fixture_song_a')
                    page.wait_for_function('generation=>BMODesktop.features?.mode_generation===generation&&document.querySelector("#music-surface").dataset.stopping==="false"',arg=newer['mode_generation'])
                    assert len(stops())==before_stops,'Deferred Stop crossed into a new activity'
                    panel('music-menu','music-dialog');page.locator('#music-stop').click()
                    wait_state(lambda s:s['mode']=='conversation')
                    checks.append('a deferred Stop is retired on a newer activity generation; it cannot stop a newly started song')
                    assert fixture.features()['emotion']=='sad' and fixture.features()['audio_owner'] is None
                    assert fixture.features()['test_fixture']['brain']==0
                    page.locator('#music-dialog').wait_for(state='hidden')
                    checks.append('silent fake music pause/resume/next/stop returns to the same face without invented RMS mouth cues or STT')

                    panel('camera-menu','camera-dialog')
                    page.locator('#camera-preview').click();wait_state(lambda s:s['camera']['preview_available'])
                    page.locator('#camera-image').wait_for(state='visible')
                    page.wait_for_function("""()=>{const c=document.querySelector('#camera-image'),p=BMODesktop.previewEvidence;
                        return c instanceof HTMLCanvasElement&&c.dataset.frameId&&c.width>0&&c.height>0
                          &&p.displayed>0&&p.last_displayed_id===c.dataset.frameId
                          &&c.getContext('2d').getImageData(0,0,1,1).data[3]>0;}""")
                    assert preview_requests['latest']>0 and preview_requests['legacy']==0
                    assert page.evaluate('()=>BMODesktop.previewEvidence.max_pending')==1
                    page.locator('#camera-track').click()
                    wait_state(lambda s:s['camera']['tracking'] and s['gaze']['owner']=='camera')
                    page.locator('#camera-off').click();wait_state(lambda s:not s['camera']['on'])
                    page.wait_for_function('()=>!BMODesktop.previewEvidence.active&&BMODesktop.previewEvidence.pending===0')
                    page.locator('#photo-take').click();wait_state(lambda s:s['mode']=='photo')
                    countdown=wait_state(lambda s:s['photo_phase']=='countdown')
                    page.wait_for_function("""()=>{const c=document.querySelector('#photo-preview-image');
                        return !c.hidden&&c instanceof HTMLCanvasElement&&c.dataset.frameId
                          &&BMODesktop.previewEvidence.last_displayed_id===c.dataset.frameId
                          &&c.getContext('2d').getImageData(0,0,1,1).data[3]>0;}""")
                    assert countdown['photo_countdown']>4
                    state=wait_state(lambda s:bool(s['photo'] and s['photo']['completed']))
                    assert state['photo']['simulated'] and not state['photo']['executed']
                    status,png=fixture.request('/api/photo/'+state['photo']['photo_id'])
                    assert status==200 and png.startswith(b'\x89PNG')
                    state=wait_state(lambda s:s['photo_phase']=='photo_review' and not s['conversation_busy']
                                     and s['audio_owner'] is None and s['test_fixture']['synthesis']==1)
                    assert not state['camera']['on']
                    page.locator('#photo-result-image').wait_for(state='visible')
                    page.wait_for_function("()=>document.querySelector('#photo-result-image').naturalWidth>0&&!BMODesktop.previewEvidence.active&&BMODesktop.previewEvidence.pending===0")
                    page.locator('#foreground-back').click();wait_state(lambda s:s['mode']=='conversation')
                    assert fixture.features()['emotion']=='sad'
                    checks.append('cached binary latest frames are drawn to canvas with one pending fetch; visible paint gates five-second countdown, OFF/review stop the pipeline, saved PNG review and one feedback TTS then Back preserve emotion')

                    panel('system-menu','system-dialog')
                    assert '실행하지 않았어요' in page.locator('#system-readiness').inner_text()
                    assert fixture.features()['system_diagnostics']['telemetry']['power']['status']=='NOT_RUN'
                    assert fixture.features()['system_diagnostics']['buttons']['current_firmware_verified'] is False
                    page.locator('#system-dialog .dialog-close').click()
                    assert page.evaluate('()=>window.__audioCalls')==0
                    audit=fixture.features()['test_fixture']
                    assert audit['brain']==audit['stt']==0 and audit['synthesis']==1 and audit['greeting_calls']==1
                    checks.append('diagnostics distinguish unmeasured power/current-firmware status and never run telemetry, mic, paid API or browser audio')
                    code,counts,error=fixture.finish()
                    assert code==0,error
                    assert counts['players']==counts['reaped']==2
                    assert counts['browsers']==counts['browser_reaped']==1 and counts['forbidden']==0
                    assert saved.read_bytes()==payload
                    checks.append('normal app exit cleans owned mock processes while keeping the completed selected-directory video unchanged')
                finally:browser.close()
        finally:fixture.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest()==before
    return dict(status='PASS',screen_profile=profile,checks=checks,counts=counts,
        sha256_before=before,sha256_after=before,
        not_run=['actual camera/video codec/AF','owned game-pack assets and real music','devices,paid API,OS','VNC/LCD/power/long-soak'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--screen-profile',choices=('ci-synthetic','r10','v13.2'),default='ci-synthetic')
    p.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=p.parse_args();print(json.dumps(run(args.screen_profile,args.chromium),ensure_ascii=False,indent=2))
