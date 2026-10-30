#!/usr/bin/env python3
"""One product Desktop: compressed-video decoder, tracking and input ownership.

The camera/provider/player are injected IO, but the Host, HTTP, commands, canvas,
saved MP4 and HTML video decoder are product code. No physical FPS is claimed.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

SOFTWARE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOFTWARE))
from checks import check_desktop_app_browser as desktop
from bmo_ai.screen_profiles import SCREEN_PROFILES


def replace_once(source,old,new):
    assert source.count(old)==1,old
    return source.replace(old,new)


def child_source():
    child=desktop.CHILD
    child=replace_once(child,"hosts=[];players=[];browsers=[];apps=[]",
        "hosts=[];players=[];browsers=[];apps=[];encoders=[]")
    child=replace_once(child,"def spawn(command,**options):\n",r'''
def spawn(command,**options):
    if type(command) is list and command and Path(command[0]).name in ('ffmpeg','ffprobe'):
        # Only the product's fixed local tool invocations. No network source,
        # shell, physical input or alternative encoder command is admitted.
        assert Path(command[0]).is_absolute() and not options.get('shell')
        assert not any(str(value).startswith(('http:','https:','rtsp:','/dev/')) for value in command)
        process=real_popen(command,**options);encoders.append(process);return process
''')
    child=replace_once(child,"    counts['hosts']+=1","""    counts['hosts']+=1
    counts.update(preview_frames=0,photo_captures=0)
    from bmo_ai.features.camera import CameraFeature,DemoBackend,FixtureDetector,PhotoStore
    from bmo_ai.features.camera.models import PreviewFrame
    sys.path.insert(0,str(Path(os.environ['PYTHONPATH'])/'tests'))
    from test_feature_camera_adapters import jpeg_bytes
    class PreviewBackend(DemoBackend):
        def __init__(self):
            super().__init__(clock=clock)
            self.jpeg=jpeg_bytes(640,360)
        def read_preview(self):
            frame=self.read_frame();counts['preview_frames']+=1
            return PreviewFrame(frame.stream_generation,frame.seq,frame.captured_at,
                640,360,self.jpeg,format='jpeg')
        def capture_photo(self,**options):
            counts['photo_captures']+=1
            return super().capture_photo(**options)
    settings['camera']=CameraFeature(backend_factory=PreviewBackend,detector=FixtureDetector(),
        store=PhotoStore(root/'photos'),clock=clock,asynchronous_preview=True,preview_hz=20,max_hz=4)
    # A tiny synthetic archive exercises import/selection/approval UI only.
    # It is not the user's private v8 archive and asserts no listening approval.
    import zipfile
    from test_organized_voice_pack import pcm
    from bmo_ai import fish_tts
    from bmo_ai.voice_pack import OrganizedVoicePack
    raw,preview=pcm(),pcm(2500)
    rows=[dict(candidate_id='PH_REVIEW_02_A',clip_id='PH_REVIEW_02',
        text='Synthetic saved-photo voice candidate.',source_model='Drama3',
        voice_id=fish_tts.AARON_BMO_VOICE_ID,
        files=[dict(path='RAW/review.wav',sha256=hashlib.sha256(raw).hexdigest(),baked_gain=1.),
            dict(path='LEGACY_PREVIEW_GAIN025/review.wav',sha256=hashlib.sha256(preview).hexdigest(),baked_gain=.25)])]
    archive=root/'synthetic-pack.zip'
    with zipfile.ZipFile(archive,'w') as output:
        output.writestr('catalogue.json',json.dumps(dict(candidates=rows)))
        output.writestr('RAW/review.wav',raw);output.writestr('LEGACY_PREVIEW_GAIN025/review.wav',preview)
    archive.chmod(0o600)
    settings['voice_pack_directory']=root/'organized-pack'
    OrganizedVoicePack(settings['voice_pack_directory']).import_zip(archive,
        expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
""")
    child=replace_once(child,"            counts['brain']+=1;time.sleep(.15)\n            return super().create(**request)",r'''
            counts['brain']+=1;time.sleep(.15)
            assert request['model']=='gpt-6-luna' and request['reasoning']=={'effort':'low'}
            assert request['max_output_tokens']==320
            schema=request['text']['format']['schema']
            token=schema['properties']['request']['properties']['turn_token']['enum'][0]
            semantic=dict(turn_token=token,kind='none',disposition='none',emotion='none',
                subtitle_mode='unchanged',confirmation_token=None)
            message=request['input'][-1]['content'];intent=None
            if message in ('BMO, could you follow my face?','내 얼굴을 따라 봐 줘.'):
                semantic.update(kind='tracking',disposition='execute')
                intent=dict(name='start_face_tracking',target=None)
            elif message=='Stop tracking me, please.':
                semantic.update(kind='tracking',disposition='execute')
                intent=dict(name='stop_face_tracking',target=None)
            elif message=='Would you record a little video for me?':
                semantic.update(kind='video',disposition='execute')
                intent=dict(name='start_video',target='seconds_10')
            elif message=='자막 켜줘':
                semantic.update(kind='subtitles',disposition='execute',subtitle_mode='ko')
            speech='All right. Let us try that.'
            reply=dict(version=4,speech=speech,emotion='neutral',gesture='none',intent=intent,
                reaction='none',character=dict(delivery_id='calm',intensity='low',gaze_id='none',source='dialogue'),
                request=semantic,captions=dict(en=speech,ko='좋아. 해 보자.'),photo_success=None)
            from types import SimpleNamespace
            return SimpleNamespace(status='completed',incomplete_details=None,error=None,output_text=json.dumps(reply))
''')
    child=replace_once(child,"    host=FeatureHost(**settings,client=client);hosts.append(host)",
        """    host=FeatureHost(**settings,client=client);hosts.append(host)
    host.camera.on_target=host._camera_target
    real_poll=host.poll
    def poll_with_error_boundary():
        flag=root/'inject-provider-failure'
        if flag.exists():
            flag.unlink();host.session.voice._provider_failure('fish_authentication')
        for filename,attribute,value in (
                ('inject-cloud-failure','_cloud_runtime_error','authentication'),
                ('inject-cloud-setup-failure','_cloud_setup_error','missing_key'),
                ('clear-cloud-setup-failure','_cloud_setup_error',None)):
            flag=root/filename
            if flag.exists():
                flag.unlink();setattr(host,attribute,value)
        return real_poll()
    host.poll=poll_with_error_boundary
""")
    child=replace_once(child,"for host in hosts:\n", "for process in encoders:\n    assert process.poll() is not None,'Encoder survived exit'\nfor host in hosts:\n")
    return child


@contextmanager
def fixture_for(directory,profile,face):
    with patch.object(desktop,'CHILD',child_source()):
        fixture=desktop.DesktopFixture(directory,profile=profile,face=face,scenario='character',
            extra_args=('--character-life','--ptt-trace','--video-directory',str(Path(directory)/'videos'),
                '--voice-bank-directory',str(Path(directory)/'character-bank')))
    try:yield fixture
    except Exception as error:
        if fixture.process.poll() is not None:
            output,stderr=fixture.process.communicate(timeout=3)
            error.add_note('Fixture exited '+str(fixture.process.returncode)+'\n'+stderr+'\n'+''.join(fixture._lines)+output)
        raise
    finally:fixture.close()


def run(profile='ci-synthetic',chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    face=desktop.FIXTURE if profile=='ci-synthetic' else Path(SCREEN_PROFILES[profile]['default_path'])
    before=hashlib.sha256(face.read_bytes()).hexdigest();checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-full-batch-browser-') as directory,fixture_for(directory,profile,face) as fixture:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                    '--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
            try:
                page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(12000)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                page.add_init_script("""window.__videoPlays=0;window.__audioCalls=0;
                    const nativePlay=HTMLMediaElement.prototype.play;
                    HTMLMediaElement.prototype.play=function(){
                      if(this.tagName==='VIDEO'&&this.muted){window.__videoPlays++;return nativePlay.call(this);}
                      window.__audioCalls++;throw Error('Browser audio forbidden');};""")
                def wait(predicate,timeout=12):
                    deadline=time.monotonic()+timeout
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if predicate(state):return state
                        page.wait_for_timeout(25)
                    raise AssertionError('Product state did not settle: '+json.dumps({key:state.get(key) for key in
                        ('mode','phase','video_phase','video','camera_video','camera','gaze','last_result','requests','test_fixture')}))
                def panel(button):
                    page.evaluate('()=>BMODesktop.menu()');page.locator('#'+button).click()
                def finish_voice():
                    return wait(lambda state:not state['conversation_busy'] and state['audio_owner'] is None)
                def question(text):
                    count=fixture.features()['test_fixture']['brain']
                    page.evaluate('text=>BMODesktop.control("question",text)',text)
                    wait(lambda state:state['test_fixture']['brain']==count+1)
                    finish_voice()
                def wait_ui_mode(mode):
                    page.wait_for_function('mode=>BMODesktop.features?.mode===mode',arg=mode)
                page.goto(fixture.url,wait_until='domcontentloaded')
                try:
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                except Exception as error:
                    error.add_note(json.dumps(dict(desktop=fixture.state(),features=fixture.features(),browser_errors=errors),ensure_ascii=False))
                    raise
                assert not errors,errors
                checks.append('one actual product Desktop starts with existing approved profile and no device/provider IO')

                wake_before=fixture.features()['test_fixture']
                page.evaluate('()=>BMODesktop.menu()')
                assert page.locator('#wake-on').is_disabled()
                assert '다시 실행' in page.locator('#wake-readiness').inner_text()
                receipt=page.evaluate('()=>BMODesktop.control("wake",true)')
                rejected=wait(lambda state:any(item['request_id']==receipt['request_id'] and item['status']=='rejected'
                    and item['code']=='wake_model_unavailable' for item in state['requests']))
                assert not rejected['wake_word']['armed'] and not rejected['wake_word']['requested']
                wake_after=rejected['test_fixture']
                assert (wake_before['mic_sources'],wake_before['stt'],wake_before['brain'],wake_before['synthesis'])==(wake_after['mic_sources'],wake_after['stt'],wake_after['brain'],wake_after['synthesis'])
                page.locator('#resume').click()
                checks.append('unconfigured wake ON is disabled; forced owner command cannot open mic, download a model or call STT/GPT/TTS')

                panel('system-menu')
                assert page.locator('#provider-readiness').inner_text()
                assert page.locator('#provider-recheck').is_disabled()
                page.locator('#system-dialog .dialog-close').click()
                panel('arms-menu')
                assert page.locator('#arms-status').inner_text()
                assert '위치' in page.locator('#arms-dialog').inner_text()
                page.locator('#arms-stop').click()
                page.locator('#arms-result').wait_for(state='visible')
                page.locator('#arms-dialog .dialog-close').click()
                checks.append('actual arm menu exposes named-gesture/state boundary and independent stop without granting real motion')

                before_provider=fixture.features()['test_fixture']
                (Path(directory)/'inject-provider-failure').write_text('synthetic authentication boundary only')
                wait(lambda state:state['providers']['voice']['blocked'])
                page.locator('#face').click(position={'x':400,'y':230});page.keyboard.press('Enter')
                page.wait_for_timeout(300)
                blocked=fixture.features()['test_fixture']
                assert (blocked['mic_sources'],blocked['brain'],blocked['synthesis'])==(before_provider['mic_sources'],before_provider['brain'],before_provider['synthesis'])
                panel('system-menu');page.wait_for_function("()=>document.querySelector('#provider-recheck').disabled===false")
                assert 'Fish 인증' in page.locator('#provider-readiness').inner_text()
                page.locator('#provider-recheck').click();wait(lambda state:not state['providers']['voice']['blocked'])
                page.locator('#system-dialog .dialog-close').click()
                checks.append('injected Fish authentication fault blocks duplicate mic/GPT/TTS; explicit menu reset recovers local admission without an API probe')

                before_cloud=fixture.features()['test_fixture']
                (Path(directory)/'inject-cloud-failure').write_text('synthetic OpenAI authentication boundary')
                wait(lambda state:state['providers']['cloud']['blocked'])
                page.locator('#face').click(position={'x':400,'y':230});page.keyboard.press('Enter');page.wait_for_timeout(250)
                cloud_blocked=fixture.features()['test_fixture']
                assert (cloud_blocked['mic_sources'],cloud_blocked['stt'],cloud_blocked['brain'],cloud_blocked['synthesis'])==(before_cloud['mic_sources'],before_cloud['stt'],before_cloud['brain'],before_cloud['synthesis'])
                panel('system-menu');page.wait_for_function("()=>document.querySelector('#provider-recheck').disabled===false")
                assert 'OpenAI 인증' in page.locator('#cloud-readiness').inner_text()
                page.locator('#provider-recheck').click()
                cleared=wait(lambda state:not state['providers']['cloud']['blocked'])
                assert cleared['providers']['cloud']['authentication_verified'] is False
                (Path(directory)/'inject-cloud-setup-failure').write_text('synthetic startup missing-key boundary')
                wait(lambda state:state['providers']['cloud']['recovery']=='restart_required')
                page.wait_for_function("()=>document.querySelector('#provider-recheck').disabled===true")
                assert '다시 실행' in page.locator('#cloud-readiness').inner_text()
                page.locator('#system-dialog .dialog-close').click()
                panel('camera-menu');page.locator('#camera-preview').click()
                page.wait_for_function("()=>document.querySelector('#camera-image').dataset.frameId")
                assert fixture.features()['providers']['cloud']['blocked']
                page.locator('#camera-off').click();wait(lambda state:not state['camera']['on'])
                page.locator('#camera-dialog .dialog-close').click()
                # End only this injected fault boundary. Actual startup setup
                # faults require a new configured process, as the UI states.
                (Path(directory)/'clear-cloud-setup-failure').write_text('end test-owned fault injection')
                wait(lambda state:not state['providers']['cloud']['blocked'])
                after_cloud=fixture.features()['test_fixture']
                assert (after_cloud['mic_sources'],after_cloud['stt'],after_cloud['brain'],after_cloud['synthesis'])==(before_cloud['mic_sources'],before_cloud['stt'],before_cloud['brain'],before_cloud['synthesis'])
                checks.append('OpenAI fault blocks PTT before mic/STT/GPT/TTS; explicit runtime reset is not authentication approval, startup missing-key asks restart while preview/menu remain usable')

                before_assets=fixture.features()['test_fixture']
                panel('voice-menu')
                candidate=page.locator('[data-candidate=PH_REVIEW_02_A]')
                candidate.wait_for(state='visible')
                assert 'drama-3' in candidate.inner_text()
                assert fixture.features()['voice_pack']['approved']==0
                candidate.locator('select').select_option('photo_saved')
                candidate.get_by_role('button',name='선택',exact=True).click()
                wait(lambda state:'photo_saved' in state['voice_pack']['inventory'][0]['selected_events'])
                assert fixture.features()['voice_pack']['approved']==0
                preview=candidate.locator('[data-variant=preview_gain025]')
                assert preview.get_by_role('button',name='이 SHA 승인').is_disabled()
                preview.locator('input[type=checkbox]').check()
                preview.get_by_role('button',name='이 SHA 승인').click()
                approved=wait(lambda state:state['voice_pack']['inventory'][0]['variants']['preview_gain025']['approved'])
                assert not approved['voice_pack']['inventory'][0]['variants']['raw']['approved']
                after_assets=approved['test_fixture']
                assert (before_assets['brain'],before_assets['synthesis'],before_assets['players'])==(after_assets['brain'],after_assets['synthesis'],after_assets['players'])
                candidate.get_by_role('button',name='승인된 선택 음성 재생').click()
                played=wait(lambda state:state['test_fixture']['players']==after_assets['players']+1)
                finish_voice()
                assert played['test_fixture']['brain']==after_assets['brain'] and played['test_fixture']['synthesis']==after_assets['synthesis']
                page.locator('#voice-dialog .dialog-close').click()
                checks.append('synthetic private-pack import reaches provenance/event selection UI; explicit per-SHA checkbox approves only one variant with no playback/generation or bulk approval')
                checks.append('manual selected approved event uses the existing owned cached player without GPT/TTS regeneration; no unapproved variant is played')

                initial=fixture.features()['test_fixture']
                page.evaluate('()=>BMODesktop.menu()')
                page.locator('#master-volume').fill('0');page.locator('#master-volume').dispatch_event('change')
                wait(lambda state:state['volume']['level']==0)
                page.locator('#master-mute').check();wait(lambda state:state['volume']['muted'])
                page.locator('#master-volume').fill('0.35');page.locator('#master-volume').dispatch_event('change')
                page.locator('#master-mute').uncheck();wait(lambda state:state['volume']['level']==.35 and not state['volume']['muted'])
                page.locator('#resume').click()
                volume_counts=fixture.features()['test_fixture']
                assert (initial['brain'],initial['synthesis'])==(volume_counts['brain'],volume_counts['synthesis'])
                checks.append('real range/mute controls accept zero and restore user volume without provider synthesis')

                panel('camera-menu');page.locator('#camera-preview').click()
                wait(lambda state:state['camera']['on'])
                page.wait_for_function("()=>document.querySelector('#camera-image').dataset.frameId")
                page.locator('#camera-track').click()
                tracked=wait(lambda state:state['camera']['tracking'] and state['gaze']['owner']=='camera')
                page.wait_for_function("()=>document.querySelector('#face').contentWindow.BMOFace.getState().look.y!==0")
                page.locator('#camera-track').click()
                stopped=wait(lambda state:not state['camera']['tracking'])
                assert stopped['camera']['on'] and stopped['camera']['preview']
                page.locator('#camera-off').click();wait(lambda state:not state['camera']['on'])
                page.locator('#camera-dialog .dialog-close').click()
                assert tracked['test_fixture']['photo_captures']==0
                checks.append('actual preview and tracker feed unchanged face API; stopping tracking preserves preview, camera-off releases all')

                question('BMO, could you follow my face?')
                wait(lambda state:state['camera']['tracking'])
                question('내 얼굴을 따라 봐 줘.')
                wait(lambda state:state['camera']['tracking'])
                checks.append('English and Korean current-request meaning reaches same camera/TargetTracker path in one GPT response')

                panel('video-menu');page.locator('#video-consent').check();page.locator('#video-start').click()
                countdown=wait(lambda state:state['mode']=='video' and state['video_phase']=='countdown')
                wait_ui_mode('video')
                page.wait_for_function("()=>document.querySelector('#video-preview-image').dataset.frameId&&document.querySelector('#video-countdown').hidden===false")
                assert countdown['camera']['tracking_requested'] and not countdown['camera']['tracking']
                assert page.locator('#video-countdown').inner_text() in ('5','4')
                baseline=fixture.features()['test_fixture']
                fixture.set_clock(5.2)
                recording=wait(lambda state:state['video_phase']=='recording')
                page.wait_for_function("()=>BMODesktop.features?.video_phase==='recording'")
                assert page.locator('#video-indicator').is_visible()
                page.wait_for_timeout(1250)
                assert fixture.features()['test_fixture']['mic_sources']==baseline['mic_sources']
                assert fixture.features()['test_fixture']['brain']==baseline['brain']
                assert fixture.features()['test_fixture']['synthesis']==baseline['synthesis']
                page.keyboard.down('Enter');page.keyboard.down('Enter')
                reviewed=wait(lambda state:state['video_phase']=='review')
                page.wait_for_function("()=>BMODesktop.features?.video_phase==='review'")
                page.keyboard.up('Enter');page.wait_for_timeout(200)
                assert fixture.features()['mode']=='video'
                assert fixture.features()['test_fixture']['mic_sources']==baseline['mic_sources']
                checks.append('visible JPEG ACK starts five-second countdown; REC suppresses voice input/output; held GP8 stops once and release does not Back/PTT')

                player=page.locator('#video-review-player');player.wait_for(state='visible')
                page.wait_for_function("()=>document.querySelector('#video-review-player').readyState>=2")
                decoded=player.evaluate("video=>({width:video.videoWidth,height:video.videoHeight,duration:video.duration,muted:video.muted,error:video.error?.code||null})")
                assert decoded['width']==640 and decoded['height']==360 and decoded['duration']>0 and decoded['muted'] and decoded['error'] is None,decoded
                page.locator('#video-play').click()
                page.wait_for_function("()=>document.querySelector('#video-review-player').currentTime>0.15")
                page.locator('#video-play').click();assert player.evaluate('video=>video.paused')
                page.locator('#video-replay').click();page.wait_for_function("()=>!document.querySelector('#video-review-player').paused")
                video_id=reviewed['video_saved_id']
                response=page.request.get(fixture.url+'api/video/'+video_id,headers={'Range':'bytes=0-31'})
                assert response.status==206 and len(response.body())==32
                assert response.headers['content-type']=='video/mp4' and response.headers['content-range'].startswith('bytes 0-31/')
                assert b'ftyp' in response.body()
                bad=page.request.get(fixture.url+'api/video/not_an_existing_video',headers={'Range':'bytes=0-31'})
                assert bad.status in (400,404)
                checks.append('saved H264 MP4 actually decodes in product HTML video; play/pause/replay and bounded HTTP Range serve stored file')

                page.keyboard.press('Enter');returned=wait(lambda state:state['mode']=='conversation')
                wait_ui_mode('conversation')
                assert returned['camera']['tracking_requested']
                assert player.evaluate("video=>video.paused&&!video.hasAttribute('src')")
                assert returned['test_fixture']['photo_captures']==0
                question('Stop tracking me, please.')
                wait(lambda state:not state['camera']['tracking'])
                checks.append('review GP8 returns once, detaches decoder, restores prior requested tracking and explicit stop disables only tracking')

                panel('video-menu');page.locator('#video-consent').check();page.locator('#video-start').click()
                wait(lambda state:state['video_phase']=='countdown')
                before_cancel=fixture.features()['test_fixture']
                page.locator('#foreground-back').click();wait(lambda state:state['mode']=='conversation')
                assert fixture.features()['test_fixture']['photo_captures']==before_cancel['photo_captures']
                page.evaluate('()=>BMODesktop.control("repeat")');finish_voice()
                assert fixture.features()['mode']=='conversation'
                checks.append('countdown Back cancels without recording; repeat replays voice without recording/photo/arm action')

                assert page.evaluate('()=>window.__audioCalls')==0
                assert page.evaluate('()=>window.__videoPlays')>=2
                assert not errors,errors
                code,counts,error=fixture.finish()
                assert code==0 and counts['forbidden']==0,error
                assert counts['players']==counts['reaped'] and counts['max_players']==1
                checks.append('normal app exit releases video/camera/player children; frozen face bytes and provider/device prohibition remain intact')
            finally:browser.close()
    checks.extend(run_arm_menu(profile,chromium))
    assert hashlib.sha256(face.read_bytes()).hexdigest()==before
    return dict(status='PASS',checks=checks,screen_profile=profile,screen_sha256=before,
        not_run=['physical Pi camera/video FPS','human face tracking quality','speaker listening approval','physical arm movement'])


def run_arm_menu(profile,chromium):
    """The real B1 adapter/firmware core, with a test-owned wire and motor sinks."""
    from playwright.sync_api import sync_playwright
    face=desktop.FIXTURE if profile=='ci-synthetic' else Path(SCREEN_PROFILES[profile]['default_path'])
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-full-arm-browser-') as directory:
        fixture=desktop.DesktopFixture(directory,profile=profile,face=face,scenario='release')
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=chromium,headless=True,
                    env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                    args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                        '--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
                try:
                    page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(10000)
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                    page.add_init_script("HTMLMediaElement.prototype.play=function(){throw Error('Browser audio forbidden')}")
                    page.goto(fixture.url,wait_until='domcontentloaded')
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                    page.evaluate('()=>BMODesktop.menu()');page.locator('#arms-menu').click()
                    page.wait_for_function("()=>document.querySelector('[data-gesture=raise_left_arm]')?.disabled===false")
                    assert page.locator('#arms-gestures button').count()==7
                    # Native disabled-button behavior suppresses a second
                    # click before a new poll; adapter busy/ledger gates remain
                    # behind it and are tested separately.
                    page.locator('[data-gesture=raise_left_arm]').evaluate('button=>{button.click();button.click()}')
                    deadline=time.monotonic()+8
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if state['test_fixture']['b1_commands']==1:break
                        page.wait_for_timeout(25)
                    else:raise AssertionError('Arm menu did not reach existing B1 adapter: '+json.dumps(state))
                    assert state['test_fixture']['b1_gestures']==['raise_left_arm']
                    assert state['motion']['physical_verified'] is False
                    page.locator('#arms-stop').click()
                    deadline=time.monotonic()+8
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if state['test_fixture']['b1_stops']==1:break
                        page.wait_for_timeout(25)
                    else:raise AssertionError('Arm stop did not reach B1')
                    page.locator('#arms-stop').click();page.wait_for_timeout(150)
                    stopped=fixture.features()
                    assert stopped['test_fixture']['b1_commands']==1 and stopped['test_fixture']['b1_stops']==1
                    assert stopped['motion']['stopped'] and stopped['test_fixture']['b1_latched']
                    page.locator('#arms-dialog .dialog-close').click()
                    page.locator('#face').click(position={'x':400,'y':230});page.keyboard.press('Enter')
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if state['test_fixture']['brain']==1 and not state['conversation_busy']:break
                        page.wait_for_timeout(25)
                    else:raise AssertionError('GP8 conversation did not recover after arm stop')
                    assert state['test_fixture']['b1_commands']==1
                    code,counts,error=fixture.finish()
                    assert code==0 and counts['forbidden']==0,error
                    assert counts['b1_closed']==1 and counts['players']==counts['reaped']
                    checks.append('real arm-menu clicks reach existing B1 named left-arm gesture once; independent STOP latches without claiming physical completion')
                    checks.append('duplicate arm click/STOP and later GP8 conversation do not replay movement; owned B1 transport closes on normal exit')
                finally:browser.close()
        finally:fixture.close()
    return checks


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',choices=('ci-synthetic',*SCREEN_PROFILES),default='ci-synthetic')
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.profile,args.chromium),ensure_ascii=False,indent=2))
