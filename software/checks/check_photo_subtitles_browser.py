#!/usr/bin/env python3
"""One product Desktop/Host with injected provider/player/camera boundaries.

Native review keys, stored PNGs and caption timing are product code. These
fixtures do not measure free-form model understanding, speech quality or Pi IO.
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
    counts.update(photo_captures=0,photo_saves=0,prepared_holds=0,english_speech=0)
""")
    anchor="            counts['brain']+=1;time.sleep(.15)\n            return super().create(**request)"
    assert child.count(anchor)==1
    child=child.replace(anchor,r'''
            counts['brain']+=1;time.sleep(.15)
            assert request['model']=='gpt-6-luna' and request['reasoning']=={'effort':'low'}
            assert request['max_output_tokens']==320
            schema=request['text']['format']['schema']
            assert request['text']['format']['name']=='bmo_feature_reply_v4'
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
    child=child.replace(anchor,anchor+"""
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
            from bmo_ai.features.contracts import FeatureError
            raise FeatureError('fixture_capture_failed')
        return capture(**options)
    host.camera.broker.capture_photo=capture_photo
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
    finally:fixture.close()


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
                def route(request):
                    if not request.request.url.startswith(fixture.url):request.abort();return
                    if '/api/preview?photo=' in request.request.url and hold_preview[0]:held_preview.append(request);return
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

                hold_preview[0]=True
                # Freeze only Host/camera time for exact countdown boundaries;
                # the Desktop heartbeat and owned player stay on real clocks.
                (root/'photo-clock').write_text('0')
                photo_before=fixture.features()['test_fixture']
                question('Can you take a picture of my tiny spaceship for me, BMO?')
                wait(lambda s:s['photo_phase']=='preparing')
                page.wait_for_function("()=>BMODesktop.features?.photo_phase==='preparing'")
                deadline=time.monotonic()+4
                while not held_preview and time.monotonic()<deadline:page.wait_for_timeout(20)
                assert held_preview
                (root/'photo-clock').write_text('2')
                page.wait_for_timeout(150)
                preparing=fixture.features()
                assert preparing['photo_countdown'] is None and preparing['test_fixture']['photo_captures']==photo_before['photo_captures']
                page.evaluate('()=>BMODesktop.menu()')
                page.wait_for_function("()=>document.querySelector('#menu').open")
                hold_preview[0]=False
                for pending in held_preview:pending.continue_()
                held_preview.clear()
                page.wait_for_function("()=>{const image=document.querySelector('#photo-preview-image');return image.complete&&image.naturalWidth>0}")
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
                assert page.locator('#photo-preview-image').evaluate('image=>image.complete&&image.naturalWidth>0')
                for digit in (4,3,2,1):
                    (root/'photo-clock').write_text(str(7-digit))
                    page.wait_for_function('digit=>document.querySelector("#photo-countdown").textContent===String(digit)',arg=digit)
                    assert fixture.features()['test_fixture']['photo_captures']==photo_before['photo_captures']
                (root/'photo-clock').write_text('7.1')
                saved=wait(lambda s:s['photo_phase']=='photo_review' and s['photo'] and s['photo']['completed'])
                photo_id=saved['photo']['photo_id']
                page.locator('#photo-result-image').wait_for(state='visible')
                page.wait_for_function("()=>{const image=document.querySelector('#photo-result-image');return image.complete&&image.naturalWidth>0}")
                assert '/api/photo/'+photo_id in page.locator('#photo-result-image').get_attribute('src')
                assert not page.locator('#photo-preview-image').is_visible()
                assert not saved['camera']['on']
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
                    (root/'photo-clock').write_text(str(offset));page.wait_for_timeout(90)
                    fixture.features()  # Current host heartbeat across controlled clock steps.
                failed=wait(lambda s:s['photo_phase']=='failed')
                assert failed['photo']['completed'] is False
                assert failed['test_fixture']['photo_captures']==failure_before['photo_captures']+1
                assert failed['test_fixture']['photo_saves']==failure_before['photo_saves']
                assert failed['test_fixture']['synthesis']==failure_before['synthesis']+1
                page.wait_for_function("()=>BMODesktop.features?.photo_phase==='failed'")
                assert not page.locator('#photo-result-image').is_visible()
                assert page.locator('#foreground-back').is_visible()
                page.locator('#foreground-back').click()
                wait(lambda s:s['mode']=='conversation')
                (root/'fail-photo').unlink()
                checks.append('injected capture failure saves nothing, emits no success voice, does not retry, and Back to BMO restores conversation')

                count=fixture.features()['test_fixture']['photo_captures']
                for text in ("Don't take a photo.",'Can you take photos?','Tomorrow, take a photo.','Translate "take a photo".'):
                    question(text);finish()
                    assert fixture.features()['mode']=='conversation'
                    assert fixture.features()['test_fixture']['photo_captures']==count
                checks.append('negation, capability, future and translated quotation cannot authorize a photo even with forged model execution metadata')
                assert page.evaluate('()=>window.__audioCalls')==0
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
