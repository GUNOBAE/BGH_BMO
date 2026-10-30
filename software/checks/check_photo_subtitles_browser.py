#!/usr/bin/env python3
"""One product Desktop/Host with injected provider/player/camera boundaries.

Native review keys, stored PNGs and caption timing are product code. These
fixtures do not measure free-form model understanding, speech quality or Pi IO.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
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


def child_source():
    child="from types import SimpleNamespace\n"+desktop.CHILD
    child=child.replace("assert command==[sys.executable,'-c','import time; time.sleep(0.8)'],'Only synthetic player allowed'",
        "assert command in ([sys.executable,'-c','import time; time.sleep(0.8)'],[sys.executable,'-c','import time; time.sleep(4.0)']),'Only finite synthetic players allowed'")
    anchor="    counts['hosts']+=1"
    assert child.count(anchor)==1
    child=child.replace(anchor,anchor+"""
    photo_clock_origin=[None]
    def clock():
        try:offset=float((root/'photo-clock').read_text())
        except FileNotFoundError:return time.monotonic()
        if photo_clock_origin[0] is None:photo_clock_origin[0]=time.monotonic()
        return photo_clock_origin[0]+offset
    settings['clock']=clock
    counts.update(photo_captures=0,photo_saves=0,prepared_holds=0,english_speech=0,focus_refocus=0)
""")
    anchor="            counts['brain']+=1;time.sleep(.15)\n            return super().create(**request)"
    assert child.count(anchor)==1
    child=child.replace(anchor,r'''
            counts['brain']+=1;time.sleep(.15)
            assert request['model']=='gpt-6-luna' and request['reasoning']=={'effort':'low'}
            assert request['max_output_tokens']==320
            schema=request['text']['format']['schema']
            assert request['text']['format']['name']=='bmo_feature_reply_v5'
            token=schema['properties']['request']['properties']['turn_token']['enum'][0]
            message=request['input'][-1]['content']
            semantic=dict(turn_token=token,kind='none',disposition='none',emotion='none',
                subtitle_mode='unchanged',confirmation_token=None)
            speech='Numbers can be little friends.'
            captions=dict(en=speech,ko='숫자들도 작은 친구가 될 수 있어.')
            success=None;intent=None
            if message in ('Please, get angry.','Get angry.'):
                semantic.update(kind='acting',disposition='execute',emotion='angry')
                speech='Hey! My tiny castle is not your parking lot!'
                captions=dict(en=speech,ko='이봐! 내 작은 성은 네 주차장이 아니야!')
            elif message=='A translation may be unavailable.':
                captions=dict(en=speech,ko='')
            elif message=='자막 꺼줘':
                semantic.update(kind='subtitles',disposition='execute',subtitle_mode='off')
                speech='Just my voice, then.';captions=dict(en='',ko='')
            elif message=='Show Korean captions.':
                semantic.update(kind='subtitles',disposition='execute',subtitle_mode='ko')
                speech='My words have a little window now.'
                captions=dict(en=speech,ko='내 말에 작은 창문이 생겼네.')
            elif message=='Can you take a picture of my tiny spaceship for me, BMO?':
                semantic.update(kind='photo',disposition='execute')
                speech='Hold still, brave spaceship.'
                captions=dict(en=speech,ko='가만히 있어, 용감한 우주선아.')
                intent=dict(name='take_photo',target=None)
                success=dict(speech='Ta-da! Pretty good, huh?',
                    captions=dict(en='Ta-da! Pretty good, huh?',ko='짜잔! 꽤 괜찮지?'))
            elif message in ("Don't take a photo.",'Can you take photos?',
                    'Tomorrow, take a photo.','Translate "take a photo".'):
                # Deliberately forged execution interpretation exercises the
                # current-request denial guard, not a friendly model fixture.
                semantic.update(kind='photo',disposition='execute')
                intent=dict(name='take_photo',target=None)
                speech='We can talk about that.';captions=dict(en=speech,ko='그 이야기는 할 수 있어.')
            reply=dict(version=4,speech=speech,emotion='neutral',gesture='none',intent=intent,
                reaction='none',character=dict(delivery_id='calm',intensity='low',gaze_id='none',source='dialogue'),
                request=semantic,captions=captions,photo_success=success)
            return SimpleNamespace(status='completed',incomplete_details=None,error=None,output_text=json.dumps(reply))
''')
    # The real voice path emits prepared before reaching this player boundary;
    # only IO is held, so caption and face cannot advance early.
    anchor="    host=FeatureHost(**settings,client=client);hosts.append(host)"
    assert child.count(anchor)==1
    child=child.replace(anchor,"""
    from bmo_ai.features.camera import CameraFeature,DemoBackend,PhotoStore
    from bmo_ai.features.camera.models import PreviewFrame
    sys.path.insert(0,str(Path(os.environ['PYTHONPATH'])/'tests'))
    from test_feature_camera_adapters import jpeg_bytes
    class JpegPreviewBackend(DemoBackend):
        def __init__(self):
            super().__init__(clock=clock)
            self.jpeg=jpeg_bytes(640,360)
        def read_preview(self):
            frame=self.read_frame()
            return PreviewFrame(frame.stream_generation,frame.seq,frame.captured_at,
                640,360,self.jpeg,format='jpeg')
    settings['camera']=CameraFeature(backend_factory=JpegPreviewBackend,
        store=PhotoStore(root/'photos'),clock=clock,asynchronous_preview=True,preview_hz=20)
"""+anchor+"""
    from bmo_ai import audio
    def player(command,**options):
        deadline=time.monotonic()+8
        while (root/'hold-player').exists():
            counts['prepared_holds']+=1
            options['cancel_check']()
            if time.monotonic()>deadline:raise AssertionError('Player test gate expired')
            time.sleep(.01)
        if (root/'slow-player').exists():
            command=[sys.executable,'-c','import time; time.sleep(4.0)']
        return audio._run(command,**options)
    host.session.voice._player=player
    capture=host.camera.broker.capture_photo
    def capture_photo(**options):
        counts['photo_captures']+=1
        if (root/'fail-photo').exists():
            from bmo_ai.features.camera.diagnostics import CameraOperationError
            raise CameraOperationError('camera_timeout','focus')
        return capture(**options)
    host.camera.broker.capture_photo=capture_photo
    focus_snapshot=host.camera.focus_snapshot
    def focus_state():
        value=focus_snapshot()
        if (root/'focus-uncertain').exists():
            value=dict(value,status='unconfirmed',code='photo_focus_uncertain')
        return value
    host.camera.focus_snapshot=focus_state
    refocus=host.camera.refocus
    def user_refocus(*args,**options):
        counts['focus_refocus']+=1
        if not (root/'refocus-remains-uncertain').exists():
            (root/'focus-uncertain').unlink(missing_ok=True)
        return refocus(*args,**options)
    host.camera.refocus=user_refocus
    save=host.camera.store.save
    def save_photo(*args,**options):
        result=save(*args,**options)
        counts['photo_saves']+=bool(result.completed)
        return result
    host.camera.store.save=save_photo
""")
    anchor="    counts['synthesis']+=1;return real_synthesis(self,*args,**kwargs)"
    assert child.count(anchor)==1
    child=child.replace(anchor,"""    assert type(args[0]) is str and args[0].isascii(),'Caption translation reached voice'
    assert self.config.fish_gain==.25 and self.config.fish_name_mode=='beemo'
    assert self.config.fish_delivery=='soft'
    if args[0].startswith('Hey! My tiny castle'):
        from bmo_ai.character_director import VOICE_DIRECTIONS
        delivery=kwargs['delivery']
        assert delivery.emotion=='angry' and delivery.override_soft
        assert VOICE_DIRECTIONS['theatrical_angry'] in delivery.text
    counts['english_speech']+=1
    counts['synthesis']+=1;return real_synthesis(self,*args,**kwargs)""")
    return child


@contextmanager
def fixture_for(directory,profile,face):
    with patch.object(desktop,'CHILD',child_source()):
        fixture=desktop.DesktopFixture(directory,profile=profile,face=face,scenario='character',
            extra_args=('--character-life','--ptt-trace','--voice-bank-directory',str(Path(directory)/'character-bank')))
    try:yield fixture
    except Exception as error:
        if fixture.process.poll() is not None:
            output,stderr=fixture.process.communicate(timeout=3)
            error.add_note('Desktop fixture exited with code '+str(fixture.process.returncode)
                +'\n'+stderr+'\n'+''.join(fixture._lines)+output)
        raise
    finally:fixture.close()


def set_photo_clock(directory,value):
    """Publish a complete offset; the owner loop reads this concurrently."""
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError('Photo fixture clock offset must be finite')
    staged=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='ascii',dir=directory,
                prefix='.photo-clock-',delete=False) as output:
            staged=Path(output.name)
            output.write(repr(float(value)))
        os.replace(staged,Path(directory)/'photo-clock')
        staged=None
    finally:
        if staged is not None:staged.unlink(missing_ok=True)


def run(profile='ci-synthetic',chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    face=desktop.FIXTURE if profile=='ci-synthetic' else Path(SCREEN_PROFILES[profile]['default_path'])
    before=hashlib.sha256(face.read_bytes()).hexdigest()
    if profile!='ci-synthetic':assert before==SCREEN_PROFILES[profile]['sha256']
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-photo-subtitles-') as directory,fixture_for(directory,profile,face) as fixture:
        root=Path(directory)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                      '--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
            try:
                page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(8000)
                held_preview=[];hold_preview=[False]
                held_features=[];hold_features=[False]
                replay_preview=[False];sample_preview=[False];preview_sample=[None]
                preview_requests=[0]
                def route(request):
                    if not request.request.url.startswith(fixture.url):request.abort();return
                    if '/api/features' in request.request.url and hold_features[0]:held_features.append(request);return
                    if '/api/preview/frame?' in request.request.url:
                        preview_requests[0]+=1
                        if hold_preview[0]:held_preview.append(request);return
                        if replay_preview[0] and preview_sample[0] is not None:
                            request.fulfill(**preview_sample[0]);return
                        if sample_preview[0]:
                            response=request.fetch()
                            if response.status==200:
                                preview_sample[0]=dict(status=200,headers=response.headers,body=response.body())
                                sample_preview[0]=False;replay_preview[0]=True
                            request.fulfill(response=response);return
                    request.continue_()
                page.route('**/*',route)
                page.add_init_script("window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){window.__audioCalls++;throw Error('Browser audio forbidden')}")
                def wait(predicate):
                    deadline=time.monotonic()+8
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if predicate(state):return state
                        page.wait_for_timeout(20)
                    raise AssertionError('Photo/caption state did not settle: '+json.dumps({
                        k:state.get(k) for k in ('mode','phase','photo_phase','photo_countdown','photo','last_result','test_fixture')}))
                def finish():
                    state=wait(lambda s:not s['conversation_busy'] and s['audio_owner'] is None)
                    page.wait_for_function("()=>BMODesktop.features?.conversation_busy===false&&BMODesktop.features.audio_owner===null")
                    return state
                def question(text):
                    previous=fixture.features()['test_fixture']['brain']
                    page.evaluate('text=>BMODesktop.control("question",text)',text)
                    wait(lambda s:s['test_fixture']['brain']==previous+1)
                def subtitle_menu(mode):
                    page.evaluate('()=>BMODesktop.menu()')
                    page.locator('#subtitle-mode').select_option(mode)
                    wait(lambda s:s['subtitles']['mode']==mode)
                    page.locator('#resume').click()
                def advance_photo_countdown():
                    start=float((root/'photo-clock').read_text())
                    for elapsed in (1,2,3,4,5.1):
                        set_photo_clock(root,start+elapsed)
                        fixture.features()
                        page.wait_for_timeout(90)
                def uncertain_photo():
                    (root/'focus-uncertain').write_text('one test-owned completed uncertain focus observation')
                    page.evaluate('()=>BMODesktop.menu()');page.locator('#camera-menu').click()
                    page.locator('#photo-take').click()
                    wait(lambda s:s['photo_phase']=='countdown')
                    advance_photo_countdown()
                    value=wait(lambda s:s['photo_phase']=='focus_review')
                    page.wait_for_function("()=>BMODesktop.features?.photo_phase==='focus_review'&&document.querySelector('#photo-focus-actions').hidden===false")
                    assert page.locator('#foreground-status').inner_text()=='Focus uncertain — Take anyway, Refocus, or Back.'
                    return value
                page.goto(fixture.url,wait_until='domcontentloaded')
                page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.subtitles?.mode==='off'")
                assert not page.locator('#subtitles').is_visible()
                checks.append('default subtitles off; startup does not expose a dialogue caption')

                subtitle_menu('en')
                hold=root/'hold-player';hold.write_text('synthetic player-start gate')
                question('Get angry.')
                wait(lambda s:s['test_fixture']['prepared_holds']>0)
                assert not page.locator('#subtitles').is_visible()
                assert page.evaluate("()=>document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion")=='neutral'
                hold.unlink()
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent.includes('tiny castle')")
                assert page.evaluate("()=>document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion")=='angry'
                assert page.locator('#subtitles').inner_text()=='Hey! My tiny castle is not your parking lot!'
                trace=fixture.features()['character_life']['expression_trace']
                assert trace['requested_emotion']==trace['resolved_emotion']==trace['emitted_emotion']=='angry'
                assert trace['model_emotion']=='neutral' and trace['request_validation']=='validated'
                assert trace['character_life_enabled'] and trace['tts_override_soft'] is True
                assert trace['tts_backend']=='synthetic' and trace['turn_id'] and trace['degradation_reason']=='none'
                box=page.locator('#subtitles').bounding_box()
                assert box['y']>=390 and box['height']<=68 and box['x']>=0 and box['x']+box['width']<=800
                finish();page.locator('#subtitles').wait_for(state='hidden')
                assert page.locator('#turn-diagnostics').text_content()==''
                page.evaluate('()=>BMODesktop.menu()');page.locator('#system-menu').click()
                page.wait_for_function("()=>document.querySelector('#system-dialog').open&&document.querySelector('#turn-diagnostics').textContent.includes('expression_trace')")
                diagnostic=json.loads(page.locator('#turn-diagnostics').inner_text())
                assert diagnostic['enabled'] and isinstance(diagnostic['events'],list)
                assert diagnostic['expression_trace']['emitted_emotion']=='angry'
                assert diagnostic['expression_trace']['model_emotion']=='neutral'
                assert diagnostic['effective_config']['character_life'] is True
                assert diagnostic['effective_config']['chat_model']=='gpt-6-luna'
                assert diagnostic['effective_config']['reasoning_effort']=='low'
                assert diagnostic['effective_config']['fish_gain']==.25
                assert diagnostic['face_state']==dict(selected_emotion='angry',rendered_emotion=None)
                for private in ('Get angry.','tiny castle','이봐!','OPENAI_API_KEY','FISH_API_KEY',directory):
                    assert private not in page.locator('#turn-diagnostics').inner_text()
                page.locator('#system-dialog .dialog-close').click()
                page.wait_for_function("()=>!document.querySelector('#system-dialog').open&&document.querySelector('#turn-diagnostics').textContent===''")
                checks.append('explicit System panel keeps flat timing diagnostics and adds only safe expression/effective-config enums plus actual face API state, without speech/captions/STT or raw face dump')

                # Extend only the finite synthetic player, giving native menu
                # interactions time while ONE actual playback stays started.
                (root/'slow-player').write_text('four-second test-owned player')
                question('One ongoing answer for caption mode changes.')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='Numbers can be little friends.'")
                caption_utterance=page.evaluate('()=>BMOIntegration.state.utterance_id')
                page.evaluate('()=>BMODesktop.menu()')
                page.locator('#subtitle-mode').select_option('off')
                assert not page.locator('#subtitles').is_visible()
                page.wait_for_function("()=>BMODesktop.features?.subtitles.mode==='off'&&BMOIntegration.state.caption.mode==='off'")
                page.locator('#subtitle-mode').select_option('en')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='Numbers can be little friends.'")
                page.locator('#subtitle-mode').select_option('ko')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='숫자들도 작은 친구가 될 수 있어.'")
                assert page.evaluate('()=>BMOIntegration.state.utterance_id')==caption_utterance
                page.locator('#subtitle-mode').select_option('en')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='Numbers can be little friends.'")
                (root/'slow-player').unlink()
                finish();page.locator('#resume').click()
                checks.append('native subtitle off/en/ko menu changes apply after server mode acknowledgement to the same playing utterance; off clears immediately and on restores available caption without a new answer')

                question('Please, get angry.')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&BMOIntegration.state.emotion==='angry'")
                finish()
                checks.append('one semantic V4 response reaches angry player/face; prepared speech stays hidden, English captions start with the real player in a bounded bottom overlay')

                question('What is two plus two?')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&BMOIntegration.state.emotion==='neutral'&&document.querySelector('#subtitles').textContent==='Numbers can be little friends.'")
                baseline=finish()['test_fixture']
                page.evaluate("()=>BMODesktop.control('repeat')")
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='Numbers can be little friends.'")
                repeated=finish()['test_fixture']
                assert (baseline['brain'],baseline['synthesis'])==(repeated['brain'],repeated['synthesis'])
                checks.append('next fact returns neutral; repeat reuses only the last successful voice/caption with zero provider/synthesis/action repetition')

                subtitle_menu('ko')
                question('Tell me one small thing.')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='숫자들도 작은 친구가 될 수 있어.'")
                finish()
                question('A translation may be unavailable.')
                wait(lambda s:s['phase']=='speaking')
                assert not page.locator('#subtitles').is_visible()
                finish()
                checks.append('Korean translation captions accompany unchanged English synthesis; missing translation leaves voice working without invented captions')

                question('자막 꺼줘')
                wait(lambda s:s['subtitles']['mode']=='off');finish()
                assert not page.locator('#subtitles').is_visible()
                question('Show Korean captions.')
                wait(lambda s:s['subtitles']['mode']=='ko')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='내 말에 작은 창문이 생겼네.'")
                finish()
                hold.write_text('cancel before actual player')
                question('A cancelled answer must not become a caption.')
                wait(lambda s:s['test_fixture']['prepared_holds']>0 and s['phase']=='thinking')
                assert not page.locator('#subtitles').is_visible()
                page.evaluate("()=>BMODesktop.control('cancel')")
                finish();hold.unlink()
                assert not page.locator('#subtitles').is_visible()
                checks.append('semantic subtitle off/ko controls do not switch voice language; cancellation before player-start never flashes generated captions')

                # Transport faults use one genuine frame from the product
                # endpoint. Replaying that ID must never count HTTP polls as
                # new received/decoded/displayed camera frames.
                page.evaluate('()=>BMODesktop.menu()');page.locator('#camera-menu').click()
                page.locator('#camera-preview').click()
                page.wait_for_function("()=>BMODesktop.previewEvidence?.displayed>=3&&document.querySelector('#camera-image').dataset.frameId")
                sample_preview[0]=True
                deadline=time.monotonic()+4
                while preview_sample[0] is None and time.monotonic()<deadline:page.wait_for_timeout(20)
                assert preview_sample[0] is not None
                sampled_id=preview_sample[0]['headers']['x-bmo-frame-id']
                page.wait_for_function('id=>BMODesktop.previewEvidence.last_displayed_id===id',arg=sampled_id)
                repeated_frame=page.evaluate('()=>({...BMODesktop.previewEvidence})')
                page.wait_for_timeout(250)
                duplicate=page.evaluate('()=>({...BMODesktop.previewEvidence})')
                assert duplicate['requests']>repeated_frame['requests']
                assert all(duplicate[key]==repeated_frame[key] for key in ('received','decoded','displayed'))
                replay_preview[0]=False;hold_preview[0]=True
                deadline=time.monotonic()+4
                while not held_preview and time.monotonic()<deadline:page.wait_for_timeout(20)
                assert len(held_preview)==1
                slow=page.evaluate('()=>({...BMODesktop.previewEvidence})')
                page.wait_for_timeout(300)
                held=page.evaluate('()=>({...BMODesktop.previewEvidence})')
                assert held['requests']==slow['requests'] and held['pending']==held['max_pending']==1
                assert held['last_displayed_id']==slow['last_displayed_id']
                hold_preview[0]=False
                for pending in held_preview:pending.continue_()
                held_preview.clear()
                page.wait_for_function('previous=>BMODesktop.previewEvidence.displayed>previous',arg=held['displayed'])
                resumed=page.evaluate('()=>({...BMODesktop.previewEvidence})')
                assert resumed['dropped']>slow['dropped']
                assert resumed['received']>=resumed['decoded']>=resumed['displayed']>0
                assert all(type(resumed[key]) in (int,float) and math.isfinite(resumed[key]) and resumed[key]>=0
                    for key in ('last_frame_age_ms','last_display_age_ms'))
                page.evaluate("()=>BMODesktop.control('preview',false)")
                wait(lambda s:not s['camera']['on'])
                page.locator('#camera-dialog .dialog-close').click()
                page.wait_for_function('()=>BMODesktop.previewEvidence.active===false&&BMODesktop.previewEvidence.pending===0')
                stopped_preview=page.evaluate('()=>({...BMODesktop.previewEvidence})')
                page.wait_for_timeout(150)
                assert all(page.evaluate('()=>({...BMODesktop.previewEvidence})')[key]==stopped_preview[key]
                    for key in ('requests','displayed'))
                checks.append('independent latest-frame transport counts distinct received/decoded/displayed IDs and ages; duplicate HTTP responses do not inflate frames, slow consumer keeps one pending request and drops old frames, and preview close stops requests/draws')

                hold_preview[0]=True
                photo_decoded_before=page.evaluate('()=>BMODesktop.previewEvidence.decoded')
                # Freeze only Host/camera time for exact countdown boundaries;
                # the Desktop heartbeat and owned player stay on real clocks.
                set_photo_clock(root,0)
                photo_before=fixture.features()['test_fixture']
                question('Can you take a picture of my tiny spaceship for me, BMO?')
                wait(lambda s:s['photo_phase']=='preparing')
                page.wait_for_function("()=>BMODesktop.features?.photo_phase==='preparing'")
                deadline=time.monotonic()+4
                while not held_preview and time.monotonic()<deadline:page.wait_for_timeout(20)
                assert held_preview
                set_photo_clock(root,2)
                page.wait_for_timeout(150)
                preparing=fixture.features()
                assert preparing['photo_countdown'] is None and preparing['test_fixture']['photo_captures']==photo_before['photo_captures']
                page.evaluate('()=>BMODesktop.menu()')
                page.wait_for_function("()=>document.querySelector('#menu').open")
                hold_preview[0]=False
                for pending in held_preview:pending.continue_()
                held_preview.clear()
                page.wait_for_function('previous=>BMODesktop.previewEvidence.decoded>previous',arg=photo_decoded_before)
                page.wait_for_timeout(150)
                assert fixture.features()['photo_phase']=='preparing'
                assert fixture.features()['photo_countdown'] is None
                page.locator('#photo-view').evaluate("view=>view.style.display='none'")
                page.locator('#resume').click()
                page.wait_for_timeout(150)
                assert fixture.features()['photo_phase']=='preparing'
                assert fixture.features()['photo_countdown'] is None
                page.locator('#photo-view').evaluate("view=>view.style.removeProperty('display')")
                checks.append('a decoded preview behind an open menu or a zero-sized hidden ancestor cannot start the countdown; visible preview resumes the same request')
                page.wait_for_function("()=>BMODesktop.features?.photo_phase==='countdown'&&document.querySelector('#photo-countdown').textContent==='5'")
                assert page.locator('#photo-preview-image').is_visible()
                assert page.locator('#photo-preview-image').evaluate('canvas=>canvas.width>0&&canvas.height>0&&!!canvas.dataset.frameId')
                for digit in (4,3,2,1):
                    set_photo_clock(root,7-digit)
                    page.wait_for_function('digit=>document.querySelector("#photo-countdown").textContent===String(digit)',arg=digit)
                    assert fixture.features()['test_fixture']['photo_captures']==photo_before['photo_captures']
                set_photo_clock(root,7.1)
                saved=wait(lambda s:s['photo_phase']=='photo_review' and s['photo'] and s['photo']['completed'])
                photo_id=saved['photo']['photo_id']
                page.locator('#photo-result-image').wait_for(state='visible')
                page.wait_for_function("()=>{const image=document.querySelector('#photo-result-image');return image.complete&&image.naturalWidth>0}")
                assert '/api/photo/'+photo_id in page.locator('#photo-result-image').get_attribute('src')
                assert not page.locator('#photo-preview-image').is_visible()
                assert not saved['camera']['on']
                page.wait_for_function('()=>BMODesktop.previewEvidence.active===false&&BMODesktop.previewEvidence.pending===0')
                reviewed_preview=page.evaluate('()=>({...BMODesktop.previewEvidence})')
                page.wait_for_timeout(150)
                assert all(page.evaluate('()=>({...BMODesktop.previewEvidence})')[key]==reviewed_preview[key]
                    for key in ('requests','displayed'))
                status,png=fixture.request('/api/photo/'+photo_id)
                assert status==200 and png.startswith(b'\x89PNG')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='짜잔! 꽤 괜찮지?'")
                review=finish()
                assert review['photo_phase']=='photo_review'
                assert review['test_fixture']['brain']==photo_before['brain']+1
                assert review['test_fixture']['synthesis']==photo_before['synthesis']+2
                assert review['test_fixture']['photo_captures']==photo_before['photo_captures']+1
                page.wait_for_timeout(500)
                assert fixture.features()['photo_phase']=='photo_review'
                page.evaluate("()=>BMODesktop.control('repeat')")
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'&&document.querySelector('#subtitles').textContent==='짜잔! 꽤 괜찮지?'")
                after_repeat=finish()
                assert after_repeat['test_fixture']['synthesis']==review['test_fixture']['synthesis']
                assert after_repeat['test_fixture']['brain']==review['test_fixture']['brain']
                assert after_repeat['test_fixture']['photo_captures']==review['test_fixture']['photo_captures']
                checks.append('non-whitelist English photo request uses one GPT response; visible preview gates 5/4/3/2/1, stores one actual synthetic PNG, closes live preview and keeps review; success feedback adds one TTS only after save and repeat does not retake')

                captures=after_repeat['test_fixture']['mic_sources']
                ptt_requests=page.evaluate('()=>BMODesktop.inputEvidence.request_sent')
                page.locator('#foreground-back').focus();page.keyboard.down('Enter')
                wait(lambda s:s['mode']=='conversation')
                page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'")
                page.keyboard.down('Enter');page.keyboard.up('Enter')
                page.wait_for_timeout(300)
                assert fixture.features()['test_fixture']['mic_sources']==captures
                assert page.evaluate('()=>BMODesktop.inputEvidence.request_sent')==ptt_requests
                assert fixture.features()['test_fixture']['photo_captures']==review['test_fixture']['photo_captures']
                question('What is two plus two?');finish()
                checks.append('review GP8/held Enter/keyup only returns to BMO; the same press never starts PTT or another photo and the next conversation works')

                before_cancel=fixture.features()['test_fixture']
                page.evaluate('()=>BMODesktop.menu()');page.locator('#camera-menu').click()
                page.locator('#photo-take').click()
                wait(lambda s:s['photo_phase']=='countdown')
                page.wait_for_function("()=>BMODesktop.features?.photo_phase==='countdown'")
                page.keyboard.down('Escape');page.keyboard.up('Escape')
                cancelled=wait(lambda s:s['mode']=='conversation')
                assert cancelled['test_fixture']['photo_captures']==before_cancel['photo_captures']
                assert cancelled['test_fixture']['photo_saves']==before_cancel['photo_saves']
                assert cancelled['test_fixture']['synthesis']==before_cancel['synthesis']
                checks.append('native Escape cancels a live preview/countdown with no capture, save or success feedback')

                failure_before=fixture.features()['test_fixture']
                (root/'fail-photo').write_text('one injected camera boundary failure')
                question('Can you take a picture of my tiny spaceship for me, BMO?')
                wait(lambda s:s['photo_phase']=='countdown')
                for offset in (8.1,9.1,10.1,11.1,12.1):
                    set_photo_clock(root,offset);page.wait_for_timeout(90)
                    fixture.features()  # Current host heartbeat across controlled clock steps.
                failed=wait(lambda s:s['photo_phase']=='failed')
                assert failed['photo']['completed'] is False
                assert failed['photo']['code']=='camera_timeout' and failed['photo']['stage']=='focus'
                assert failed['photo_diagnostic']==dict(stage='focus',code='camera_timeout',status='failed',saved=False,automatic_retry=False)
                assert failed['test_fixture']['photo_captures']==failure_before['photo_captures']+1
                assert failed['test_fixture']['photo_saves']==failure_before['photo_saves']
                assert failed['test_fixture']['synthesis']==failure_before['synthesis']+1
                page.wait_for_function("()=>BMODesktop.features?.photo_phase==='failed'")
                page.wait_for_function("()=>document.querySelector('#foreground-status').textContent==='The camera could not finish focusing.'")
                assert not page.locator('#photo-result-image').is_visible()
                assert page.locator('#foreground-back').is_visible()
                page.locator('#foreground-back').click()
                wait(lambda s:s['mode']=='conversation')
                (root/'fail-photo').unlink()
                checks.append('injected focus timeout retains safe stage/code and a short specific UI reason, saves nothing, emits no success voice, does not retry, and Back to BMO restores conversation')

                before_uncertain=fixture.features()['test_fixture']
                uncertain=uncertain_photo()
                assert uncertain['test_fixture']['photo_captures']==before_uncertain['photo_captures']
                assert uncertain['test_fixture']['photo_saves']==before_uncertain['photo_saves']
                page.locator('#photo-take-anyway').click()
                unconfirmed=wait(lambda s:s['photo_phase']=='photo_review' and s['photo'] and s['photo']['completed'])
                assert unconfirmed['photo']['focus_status']=='unconfirmed'
                assert unconfirmed['test_fixture']['photo_captures']==before_uncertain['photo_captures']+1
                assert unconfirmed['test_fixture']['photo_saves']==before_uncertain['photo_saves']+1
                page.wait_for_function("()=>document.querySelector('#foreground-status').textContent==='Photo saved. Focus unconfirmed.'")
                page.wait_for_function("()=>{const image=document.querySelector('#photo-result-image');return !image.hidden&&image.complete&&image.naturalWidth>0}")
                status,png=fixture.request('/api/photo/'+unconfirmed['photo']['photo_id'])
                assert status==200 and png.startswith(b'\x89PNG')
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'")
                finished_unconfirmed=finish()
                assert finished_unconfirmed['test_fixture']['synthesis']==before_uncertain['synthesis']+1
                page.evaluate("()=>BMODesktop.control('repeat')")
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'");finish()
                assert fixture.features()['test_fixture']['photo_captures']==unconfirmed['test_fixture']['photo_captures']
                page.locator('#foreground-back').click();wait(lambda s:s['mode']=='conversation')
                (root/'focus-uncertain').unlink()
                checks.append('Focus uncertain retains the current transaction without still capture; native Take anyway stores one new actual synthetic still with unconfirmed focus and truthful persistent review, and repeat cannot retake')

                before_refocus=fixture.features()['test_fixture']
                refocus_request=uncertain_photo()
                duplicate_refocus=dict(generation=refocus_request['mode_generation'],
                    request_id=refocus_request['photo_request_id'],action='refocus')
                page.locator('#photo-refocus').click()
                page.evaluate('value=>BMODesktop.control("photo_focus",value)',duplicate_refocus)
                refocused=wait(lambda s:s['photo_phase']=='photo_review' and s['photo'] and s['photo']['completed'])
                assert refocused['photo']['focus_status']=='focused'
                assert refocused['test_fixture']['focus_refocus']==before_refocus['focus_refocus']+1
                assert refocused['test_fixture']['photo_captures']==before_refocus['photo_captures']+1
                assert refocused['test_fixture']['photo_saves']==before_refocus['photo_saves']+1
                page.wait_for_function("()=>BMOIntegration.state?.playback==='started'")
                finished_refocus=finish()
                assert finished_refocus['test_fixture']['synthesis']==before_refocus['synthesis']+1
                page.locator('#foreground-back').click();wait(lambda s:s['mode']=='conversation')
                checks.append('native Refocus runs once for the current uncertain transaction, observes the next focused state and saves one still; duplicate control cannot start another scan or capture')

                uncertain_photo()
                (root/'refocus-remains-uncertain').write_text('completed user refocus remains uncertain')
                hold_features[0]=True
                deadline=time.monotonic()+4
                while not held_features and time.monotonic()<deadline:page.wait_for_timeout(20)
                assert held_features
                before_failed_refocus=fixture.features()['test_fixture']['focus_refocus']
                page.locator('#photo-refocus').click()
                wait(lambda s:s['test_fixture']['focus_refocus']==before_failed_refocus+1)
                set_photo_clock(root,float((root/'photo-clock').read_text())+2.6)
                fixture.features()
                wait(lambda s:s['photo_phase']=='focus_review' and s['photo_refocus_used'])
                hold_features[0]=False
                for pending in held_features:pending.continue_()
                held_features.clear()
                page.wait_for_function("()=>BMODesktop.features?.photo_phase==='focus_review'&&BMODesktop.features.photo_refocus_used===true")
                assert page.locator('#photo-refocus').is_disabled()
                assert page.locator('#photo-take-anyway').is_enabled()
                before_focus_back=fixture.features()['test_fixture']
                before_focus_ptt=page.evaluate('()=>BMODesktop.inputEvidence.request_sent')
                page.locator('#photo-take-anyway').focus()
                page.keyboard.down('Enter');wait(lambda s:s['mode']=='conversation')
                page.keyboard.down('Enter');page.keyboard.up('Enter')
                page.wait_for_timeout(150)
                after_focus_back=fixture.features()['test_fixture']
                assert all(after_focus_back[key]==before_focus_back[key] for key in ('photo_captures','photo_saves','mic_sources','synthesis'))
                assert page.evaluate('()=>BMODesktop.inputEvidence.request_sent')==before_focus_ptt
                (root/'focus-uncertain').unlink()
                (root/'refocus-remains-uncertain').unlink()
                uncertain_photo()
                before_focus_repeat=fixture.features()['test_fixture']
                page.evaluate("()=>BMODesktop.control('repeat')")
                wait(lambda s:s['mode']=='conversation');finish()
                advance_photo_countdown()
                after_focus_repeat=fixture.features()['test_fixture']
                assert all(after_focus_repeat[key]==before_focus_repeat[key] for key in ('photo_captures','photo_saves','synthesis','focus_refocus'))
                (root/'focus-uncertain').unlink()
                checks.append('an uncertain Refocus whose intermediate phase is missed keeps Take anyway usable and Refocus disabled; GP8/Enter on that button only returns without capture/PTT, and repeat cannot revive the old photo')

                count=fixture.features()['test_fixture']['photo_captures']
                for text in ("Don't take a photo.",'Can you take photos?','Tomorrow, take a photo.','Translate "take a photo".'):
                    question(text);finish()
                    assert fixture.features()['mode']=='conversation'
                    assert fixture.features()['test_fixture']['photo_captures']==count
                checks.append('negation, capability, future and translated quotation cannot authorize a photo even with forged model execution metadata')
                assert page.evaluate('()=>window.__audioCalls')==0
                page.evaluate('()=>BMODesktop.menu()');page.locator('#camera-menu').click()
                page.locator('#camera-preview').click()
                page.wait_for_function("()=>BMODesktop.previewEvidence.active===true&&document.querySelector('#camera-image').dataset.frameId")
                page.goto('about:blank')
                navigated_requests=preview_requests[0]
                page.wait_for_timeout(200)
                assert preview_requests[0]==navigated_requests
                checks.append('native pagehide navigation ends the preview fetch pipeline; the departed product document sends no further frame requests')
                code,counts,error=fixture.finish()
                assert code==0 and counts['forbidden']==0,error
                assert counts['players']==counts['reaped'] and counts['max_players']==1
                assert counts['mic_sources']==counts['mic_closed'] and counts['active_mics']==0
                checks.append('all owned fixture processes close; zero physical microphone/camera/servo, paid-provider or browser audio IO')
            finally:browser.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest()==before
    return dict(status='PASS',screen_profile=profile,checks=checks,counts=counts,
        sha256_before=before,sha256_after=before,
        not_run=['actual free-form English understanding','Fish acting quality','Pi preview/capture quality','physical GP8/servo'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen-profile',choices=('ci-synthetic','r10','v13.2'),default='ci-synthetic')
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=parser.parse_args();print(json.dumps(run(args.screen_profile,args.chromium),ensure_ascii=False,indent=2))
