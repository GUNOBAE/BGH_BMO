#!/usr/bin/env python3
"""Daily face/menu flow through the product browser with injected private IO.

Synthetic PCM/process output only; no hardware, external APIs or listening
approval. Pointer/key events and the Host/desktop ownership path remain real.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks import check_face_controls_browser as face_controls
from checks import check_v10_media_browser as media
from checks.check_full_batch_browser import replace_once
from bmo_ai.voice_pack_context import context_catalog


def run(chromium=None):
    from playwright.sync_api import sync_playwright
    source = replace_once(face_controls.child_source(),
        '    host=FeatureHost(**settings,client=client);hosts.append(host)',
        "    from bmo_ai.features.motion.daily_usage import DailyArmController\n"
        "    settings['arm_usage']=DailyArmController(root/'arm-usage',runtime_ready=lambda:True)\n"
        '    host=FeatureHost(**settings,client=client);hosts.append(host)')
    source=replace_once(source, "elif scenario in ('ptt','release','character'):time.sleep(.01)",
        "elif scenario in ('ptt','release','character'):time.sleep(.04 if (root/'slow-touch-capture').exists() else .01)")
    checks = []
    hashes = {}
    with tempfile.TemporaryDirectory(prefix='bmo-daily-browser-') as directory:
        original_prepare = media.prepare
        def prepare(root):
            original_prepare(root)
            hashes.update(face_controls.prepared_catalogue(root))
        with patch.object(media, 'prepare', prepare), patch.object(media, 'child_source', lambda: source), media.fixture_for(directory, 'ci-synthetic', media.desktop.FIXTURE) as fixture:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium, headless=True,
                    env={'PATH': os.defpath, 'HOME': directory, 'LC_ALL': 'C.UTF-8'},
                    args=['--no-sandbox', '--disable-gpu', '--mute-audio', '--disable-audio-output',
                          '--disable-background-networking', '--disable-component-update', '--no-first-run'])
                try:
                    page = browser.new_page(viewport={'width':800, 'height':480})
                    page.set_default_timeout(10000)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                    def settled(expression):page.wait_for_function('()=>'+expression)
                    def open_menu():
                        page.mouse.click(400, 200)
                        page.locator('#menu').wait_for(state='visible')
                    def close_menu():
                        page.locator('#resume').click()
                        page.locator('#menu').wait_for(state='hidden')
                        settled('BMODesktop.state.character_life.settings_active===false')
                    def manual_test():
                        open_menu()
                        button = page.locator('#presence-test')
                        box = button.bounding_box()
                        assert box and box['y'] >= 0 and box['y']+box['height'] <= 480, box
                        button.click()
                        page.locator('#menu').wait_for(state='hidden')
                    page.goto(fixture.url, wait_until='domcontentloaded')
                    settled("BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                    settled("BMODesktop.state.character_life.presence.mode==='off'")
                    assert page.locator('#notice').is_hidden()
                    assert page.locator('#face').bounding_box() == dict(x=0,y=0,width=800,height=480)
                    # The standalone R10 launcher hint may be inserted after
                    # iframe load. Hide only that exact integration hint, not
                    # approved expression elements or arbitrary Esc text.
                    page.frame_locator('#face').locator('body').evaluate("""body=>{
                      const wrapper=document.createElement('div');wrapper.id='face-art-wrapper';
                      wrapper.innerHTML='<svg id="kept-svg" width="12" height="12"><circle cx="6" cy="6" r="5"/></svg><canvas id="kept-canvas" width="12" height="12"></canvas><div id="kept-css-art" style="width:12px;height:12px;background:blue"></div><div id="hint-wrapper"><div id="standalone-return-hint">화면 돌아가기 <span>· Esc</span></div></div>';
                      body.append(wrapper);
                      const fallback=document.createElement('canvas');fallback.id='kept-fallback-canvas';
                      fallback.width=12;fallback.height=12;fallback.textContent='화면 돌아가기 · Esc';body.append(fallback);
                      const mixed=document.createElement('div');mixed.id='mixed-art-and-hint';
                      mixed.innerHTML='<canvas width="12" height="12"></canvas>화면 돌아가기 · Esc';body.append(mixed);
                      const keep=document.createElement('p');keep.id='unrelated-hint';
                      keep.textContent='Esc remains available in settings';body.append(keep);
                    }""")
                    page.frame_locator('#face').locator('#standalone-return-hint').wait_for(state='hidden')
                    assert page.frame_locator('#face').locator('#unrelated-hint').is_visible()
                    for kept in ('face-art-wrapper','kept-svg','kept-canvas','kept-css-art','mixed-art-and-hint','kept-fallback-canvas'):
                        assert page.frame_locator('#face').locator('#'+kept).is_visible(),kept
                    assert page.frame_locator('#face').locator('#hint-wrapper').evaluate("e=>getComputedStyle(e).display!=='none'")
                    # All six categories and shared controls fit the actual
                    # product viewport. GP6 is Z; no imaginary X/back button.
                    page.locator('#face').focus()
                    page.keyboard.press('z')
                    page.locator('#menu').wait_for(state='visible')
                    categories=page.locator('#menu-actions button')
                    assert categories.count()==6
                    for item in [categories.nth(i) for i in range(6)]+[page.locator('#presence-test'),page.locator('#resume')]:
                        box=item.bounding_box()
                        assert box and 0<=box['x'] and 0<=box['y'] and box['x']+box['width']<=800 and box['y']+box['height']<=480,box
                    assert page.locator('#conversation-menu').evaluate('e=>e===document.activeElement')
                    page.keyboard.press('ArrowRight')
                    assert page.locator('#music-menu').evaluate('e=>e===document.activeElement')
                    page.keyboard.press('ArrowDown')
                    assert page.locator('#arms-menu').evaluate('e=>e===document.activeElement')
                    assert page.locator('#arms-menu').evaluate('e=>getComputedStyle(e).outlineWidth')=='4px'
                    page.keyboard.press('z')
                    page.locator('#arms-dialog').wait_for(state='visible')
                    page.keyboard.down('z')
                    page.locator('#menu').wait_for(state='visible')
                    page.keyboard.up('z')
                    assert page.locator('#arms-dialog').is_hidden() and page.locator('#menu').is_visible()
                    page.keyboard.down('z')
                    page.locator('#menu').wait_for(state='hidden')
                    page.keyboard.up('z')
                    assert page.locator('#menu').is_hidden()
                    page.keyboard.press('z')
                    page.locator('#master-mute').focus();page.keyboard.press('z')
                    settled('BMODesktop.features.volume.muted===true')
                    page.keyboard.press('z')
                    settled('BMODesktop.features.volume.muted===false')
                    page.locator('#settings-menu').click()
                    page.locator('#subtitle-mode').focus();page.keyboard.press('z')
                    page.keyboard.press('ArrowDown');page.keyboard.press('z')
                    settled("BMODesktop.features.subtitles.mode==='en'")
                    page.locator('#settings-dialog .dialog-close').click()
                    checks.append('800x480 six touch categories fit with volume, manual idle and face return; actual arrows/GP6 Z select and long Back consume release; exact iframe launcher hint is hidden without changing source artwork')
                    # A drag and a cancelled touch must not become menu clicks.
                    page.mouse.move(320,200);page.mouse.down();page.mouse.move(390,200,steps=4);page.mouse.up()
                    assert page.locator('#menu').is_hidden()
                    page.frame_locator('#face').locator('body').dispatch_event('pointerdown',
                        dict(pointerId=3,isPrimary=True,button=0,clientX=320,clientY=200))
                    page.frame_locator('#face').locator('body').dispatch_event('pointercancel',dict(pointerId=3))
                    page.frame_locator('#face').locator('body').dispatch_event('pointerup',
                        dict(pointerId=3,isPrimary=True,button=0,clientX=320,clientY=200))
                    assert page.locator('#menu').is_hidden()
                    open_menu()
                    assert page.locator('#presence-test').is_visible() and page.locator('#arms-menu').is_visible()
                    page.locator('#arms-menu').click()
                    settled("BMODesktop.features.arm_usage.enabled===false")
                    buttons = page.locator('#arms-gestures button')
                    assert buttons.count()==7 and all(buttons.nth(i).is_disabled() for i in range(7))
                    assert page.locator('#arms-stop').is_visible() and not page.locator('#arms-stop').is_disabled()
                    assert page.locator('#arms-enabled').is_visible() and page.locator('#arms-conversation').is_visible()
                    assert '처음' in page.locator('#arms-status').inner_text()
                    page.locator('#arms-dialog .dialog-close').click()
                    open_menu();close_menu()
                    before = fixture.features()['test_fixture']
                    page.keyboard.down('Enter');page.keyboard.up('Enter')
                    settled('BMODesktop.features.test_fixture.brain==='+str(before['brain']+1)+'&&!BMODesktop.features.conversation_busy')
                    assert page.locator('#menu').is_hidden()
                    checks.append('800x480 full face single tap opens prominent daily controls; drag/cancel do not; all seven arm actions and Stop remain present while disabled; Back followed by Enter reaches PTT')
                    # Touch completion goes to the idempotent recorder-finish
                    # action, not generic GP8 (which could start/cancel a turn
                    # after a slow state snapshot).
                    before=fixture.features()['test_fixture']
                    (Path(directory)/'slow-touch-capture').touch()
                    (Path(directory)/'hold-next-player').touch()
                    page.locator('#face').focus();page.keyboard.press('Enter')
                    settled("BMODesktop.features.ptt.state==='speech'&&!document.querySelector('#ptt-finish').hidden")
                    page.locator('#ptt-finish').click()
                    settled("BMODesktop.features.phase==='speaking'&&BMODesktop.features.test_fixture.brain==="+str(before['brain']+1))
                    assert page.locator('#ptt-finish').is_hidden()
                    stale=page.evaluate("()=>BMODesktop.control('ptt_finish')")
                    settled("BMODesktop.features.requests.some(r=>r.request_id==='"+stale['request_id']+"'&&r.code==='capture_not_listening')")
                    assert fixture.features()['conversation_busy'] and fixture.features()['test_fixture']['mic_sources']==before['mic_sources']+1
                    page.evaluate("()=>BMODesktop.control('cancel')")
                    settled('!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                    (Path(directory)/'slow-touch-capture').unlink()
                    checks.append('actual Done speaking touch ends only the active capture; button hides while thinking/speaking, late finish does not start another microphone or cancel the owned answer')
                    page.evaluate("()=>BMODesktop.control('voice_full_accept',true)")
                    settled('BMODesktop.features.voice_pack.full_user_accepted===true')
                    page.evaluate("()=>BMODesktop.control('subtitles','ko')")
                    settled("BMODesktop.features.subtitles.mode==='ko'")
                    baseline = fixture.features()['test_fixture']
                    automatic = fixture.state()['character_life']['presence']['heard_in_15_minutes']
                    selected = []
                    for index in range(2):
                        before_manual=fixture.features()['test_fixture']
                        (Path(directory)/'hold-next-player').touch()
                        manual_test()
                        settled("BMODesktop.state.character_life.manual_test.state==='playing'&&BMOIntegration.state.source==='cached_idle'&&BMOIntegration.state.playback==='started'")
                        current = page.evaluate('()=>BMOIntegration.state')
                        asset, plan = current['caption_asset'], current['performance_plan']
                        selected.append(asset['clip_id'])
                        assert asset['sha256']==plan['sha256']==hashes[asset['clip_id']]
                        assert plan['clip_id']==asset['clip_id']
                        expected_caption=context_catalog()[asset['clip_id']]['ko']
                        page.wait_for_function("expected=>!document.querySelector('#subtitles').hidden&&document.querySelector('#subtitles').textContent===expected",arg=expected_caption)
                        assert page.locator('#subtitles').inner_text()==expected_caption
                        settled("document.querySelector('#face').contentWindow.BMOFace.getState().mouth!=='closed'")
                        face = page.evaluate("()=>document.querySelector('#face').contentWindow.BMOFace.getState()")
                        assert face['selectedEmotion']==plan['face_emotion'],(face,plan)
                        duplicate = page.evaluate("()=>BMODesktop.desktop('presence_test')")
                        assert duplicate['status']=='blocked' and duplicate['code']=='presence_test_busy',duplicate
                        assert fixture.features()['test_fixture']['players']==before_manual['players']+1
                        current_counts=fixture.features()['test_fixture']
                        assert (current_counts['brain'],current_counts['synthesis'],current_counts['history_messages'])==(before_manual['brain'],before_manual['synthesis'],before_manual['history_messages'])
                        if index==0:
                            page.locator('#face').focus();page.keyboard.down('Enter');page.keyboard.up('Enter')
                            settled('BMODesktop.features.test_fixture.brain==='+str(baseline['brain']+1)+'&&!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                            # Account for the single explicitly requested PTT answer.
                            baseline = fixture.features()['test_fixture']
                        else:
                            settled("BMODesktop.state.character_life.manual_test.state==='completed'&&BMODesktop.features.audio_owner===null")
                        settled('!BMOIntegration.state.performance_plan')
                        page.locator('#subtitles').wait_for(state='hidden')
                        assert fixture.state()['character_life']['presence']['mode']=='off'
                        assert fixture.state()['character_life']['presence']['heard_in_15_minutes']==automatic
                    assert len(set(selected))==2,selected
                    checks.append('actual menu test closes UI lease and uses exact selected PCM/SHA, local Korean captions, original expression and WAV mouth while automatic mode stays OFF; no quota charge, overlap or immediate repeated clip; GP8 cancels and starts one next question')
                    # Mute and a live dialogue are real admission failures, not silent successes.
                    open_menu();page.locator('#master-mute').check()
                    settled('BMODesktop.features.volume.muted===true')
                    previous = fixture.features()['test_fixture']
                    page.locator('#presence-test').click()
                    settled("BMODesktop.state.character_life.manual_test.code==='presence_test_muted'")
                    assert '음소거' in page.locator('#notice-message').inner_text()
                    assert fixture.features()['test_fixture']['players']==previous['players']
                    open_menu();page.locator('#master-mute').uncheck();close_menu()
                    settled('BMODesktop.features.volume.muted===false')
                    (Path(directory)/'hold-next-player').touch()
                    page.evaluate("()=>BMODesktop.control('question','A synthetic held answer.')")
                    settled("BMODesktop.features.phase==='speaking'")
                    previous = fixture.features()['test_fixture']
                    manual_test()
                    settled("BMODesktop.state.character_life.manual_test.code==='presence_test_conversation_active'")
                    assert fixture.features()['test_fixture']['players']==previous['players']
                    page.evaluate("()=>BMODesktop.control('cancel')")
                    settled('!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                    assert not errors,errors
                    checks.append('muted and busy manual test exposes a short reason without a new player; cancellation releases ownership')
                    code, counts, error = fixture.finish()
                    assert code==0 and counts['forbidden']==0 and counts['players']==counts['reaped'] and counts['max_players']==1,error
                    return dict(status='PASS',checks=checks,selected_manual_clips=selected,
                                not_run=['Pi touch display','private voice listening','physical arms or GP8'])
                finally:browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.chromium),ensure_ascii=False,indent=2))
