#!/usr/bin/env python3
"""Untouched local v13.2/R10 files through the real server and external adapter.

R10 is an approved design per Issue #15. This check adds no viewport transform,
artwork, audio path or runtime approval; the default remains original v13.2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmo_ai.integration_face import FaceServer, bridge, expand_cues
from bmo_ai.screen_profiles import SCREEN_PROFILES


def install(page, url):
    page.set_default_timeout(4500)
    page.route("**/*", lambda route: route.continue_()
               if route.request.url.startswith(url) else route.abort())
    page.add_init_script("""window.__mediaPlayCalls=0;
      HTMLMediaElement.prototype.play=function(){window.__mediaPlayCalls++;
        throw new Error('Browser audio forbidden')};""")


def ready(page, url):
    page.goto(url)
    page.wait_for_function("()=>document.querySelector('#face').contentWindow.BMOFace?.getState().version==='13.2'")
    page.evaluate("""() => {
      window.face=document.querySelector('#face').contentWindow.BMOFace;
      window.facePlayCalls=0;
      for(const method of ['playAudio','playBlob']) {
        face[method]=()=>{facePlayCalls++;throw new Error('Browser audio forbidden')};
      }
    }""")


def assert_no_media(page):
    assert page.evaluate("window.__mediaPlayCalls+window.facePlayCalls")==0
    assert page.locator('#face').content_frame.locator('body').evaluate('()=>window.__mediaPlayCalls')==0


def viewport_state(page, profile):
    state=page.evaluate("""() => {
      const doc=document.querySelector('#face').contentDocument, win=doc.defaultView;
      return {viewport:win.BMOScalePreview?.getState()??null,
        owners:doc.querySelectorAll('#bmo-viewport-v1-style').length,
        viewBox:doc.querySelector('#face').getAttribute('viewBox')};
    }""")
    if profile=='r10':
        assert state['owners']==1 and state['viewport']['requestedScale']==1.6, state
        assert state['viewport']['layoutVersion']=='7inch-expression-review-r10', state
        assert 0<state['viewport']['effectiveScale']<=1.6, state
    else:
        assert state==dict(viewport=None,owners=0,viewBox='0 0 1000 700'), state
    return state


def check_profile(browser, profile, path):
    record=SCREEN_PROFILES[profile]
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert before==record['sha256'], 'Selected screen pin mismatch'
    state=dict(session_id='profile-'+profile,turn_id='',utterance_id='',sequence=0,
               phase='idle',emotion='neutral',emotion_mode='manual',playback='idle',position=0,cues=[])
    holder={'state':state}
    checks=[]
    with FaceServer(path,snapshot=lambda:holder['state'],screen_profile=profile) as server:
        with urlopen(server.url+'face.html?kiosk=1',timeout=2) as response:
            served=response.read()
        assert hashlib.sha256(served).hexdigest()==before and served==path.read_bytes()
        with urlopen(server.url+'api/screen',timeout=2) as response:
            metadata=json.load(response)
        assert metadata==dict(version=profile,sha256=before,approval_status=record['approval_status'],pi_live_verified=False)
        checks.append('selected-only metadata and served bytes exactly match pinned local file')
        page=browser.new_page(viewport={'width':1024,'height':600})
        try:
            install(page,server.url)
            ready(page,server.url)
            viewport_state(page,profile)
            checks.append('original viewport unchanged' if profile=='v13.2' else 'R10 native scale 1.6 has one owner; no external viewport added')

            def publish(**changes):
                holder['state']=holder['state']|changes|{'sequence':holder['state']['sequence']+1}
                sequence=holder['state']['sequence']
                page.wait_for_function('sequence=>BMOIntegration.state?.sequence===sequence',arg=sequence)

            timeline=expand_cues([dict(time=0,mouth='closed'),dict(time=.2,mouth='wide'),
                                 dict(time=.5,mouth='closed'),dict(time=.8,mouth='round'),
                                 dict(time=1.1,mouth='closed')],1.1)
            for index,emotion in enumerate(('neutral','happy','sad','surprised','angry','crying')):
                utterance='u'+str(index)
                publish(turn_id='t'+str(index),utterance_id=utterance,emotion=emotion,
                        phase='listening',playback='idle',position=0,cues=[])
                listening=page.evaluate('face.getState()')
                assert listening['selectedEmotion']==emotion and listening['activity']=='listening'
                assert listening['speech']=='off' and listening['mouth']=='closed'
                publish(phase='thinking',playback='prepared',cues=timeline)
                thinking=page.evaluate('face.getState()')
                assert thinking['selectedEmotion']==emotion and thinking['mouthHref']=='#mouth-thinking-hmm'
                assert thinking['speech']=='off' and page.evaluate('BMOIntegration.state.cues.length')==0
                publish(phase='speaking',playback='started')
                held=page.evaluate("""() => [.2,.2+1/30,.2+2/30,.8,.8+1/30].map(elapsed=>{
                  BMOIntegration.renderMouth(BMOIntegration.received+elapsed*1000+.00001);
                  const s=face.getState();return {mouth:s.mouth,emotion:s.selectedEmotion,speech:s.speech};
                })""")
                assert [s['mouth'] for s in held]==['low','body','wide','tight','round'], held
                assert all(s['emotion']==emotion and s['speech']=='external' for s in held)
                publish(position=1.11)
                silent=page.evaluate('face.getState()')
                assert silent['speech']=='external' and silent['mouth']=='closed'
                publish(phase='idle',playback=('ended','cancelled','error')[index%3])
                terminal=page.evaluate('face.getState()')
                expected='rest' if profile=='r10' and emotion=='surprised' else 'closed'
                assert terminal['speech']=='off' and terminal['mouth']==expected and terminal['selectedEmotion']==emotion
            checks.append('six emotions preserve listening/thinking, 30fps bridge cues, external silence and terminal state')

            ladder=('closed','parted','small','low','mid','body','open','high','wide','round','tight')
            fixtures=[[a,b,t,d] for a in ladder for b in ladder
                      for t in (0,1/30,2/30,.066667) for d in (.104,.105,.3)]
            expected=page.evaluate('items=>items.map(item=>face._test.bridge(...item))',fixtures)
            assert expected==[bridge(*item) for item in fixtures]
            checks.append('1452 original bridge fixtures match unchanged external cue algorithm')
            assert_no_media(page)

            publish(turn_id='reload',utterance_id='reload',emotion='happy',phase='speaking',
                    playback='started',position=.84,cues=timeline)
            ready(page,server.url)
            page.wait_for_function("()=>BMOIntegration.state?.utterance_id==='reload'&&face.getState().mouth==='round'")
            viewport_state(page,profile)
            for width,height in ((640,480),(1024,600)):
                page.set_viewport_size(dict(width=width,height=height))
                if profile=='r10':
                    page.wait_for_function('width=>Math.abs(document.querySelector("#face").contentWindow.BMOScalePreview.getState().width-width)<1',arg=width)
                viewport_state(page,profile)
            assert_no_media(page)
            checks.append('reload resumes current position; resize/reload do not add duplicate viewport owners')

            disposed=page.evaluate("""() => {
              const doc=document.querySelector('#face').contentDocument;
              doc.defaultView.dispatchEvent(new PageTransitionEvent('pagehide'));
              return {closed:BMOIntegration.closed,state:BMOIntegration.state,controller:BMOIntegration.controller,
                owners:doc.querySelectorAll('#bmo-viewport-v1-style').length,
                viewBox:doc.querySelector('#face').getAttribute('viewBox')};
            }""")
            assert disposed==dict(closed=True,state=None,controller=None,owners=0,viewBox='0 0 1000 700'), disposed
            ready(page,server.url)
            page.evaluate("document.querySelector('#face').remove()")
            page.wait_for_function('()=>BMOIntegration.closed&&BMOIntegration.state===null&&BMOIntegration.controller===null')
            checks.append('frame unload/removal closes adapter; native viewport disposes and restores its original viewBox')
        finally:
            page.close()
    assert server._thread is None
    after=hashlib.sha256(path.read_bytes()).hexdigest()
    assert after==before
    return dict(profile=profile,approval_status=record['approval_status'],sha256_before=before,
                sha256_after=after,bytes=len(served),checks=checks,browser_audio_calls=0)


def run(v13_face,r10_face,chromium):
    from playwright.sync_api import sync_playwright
    results=[]
    with tempfile.TemporaryDirectory(prefix='bmo-screen-browser-') as home, sync_playwright() as playwright:
        env={'PATH':os.defpath,'HOME':home,'LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
        browser=playwright.chromium.launch(executable_path=chromium,headless=True,timeout=6000,env=env,
            args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output'])
        try:
            for profile,path in (('v13.2',v13_face),('r10',r10_face)):
                results.append(check_profile(browser,profile,Path(path).expanduser().resolve(strict=True)))
        finally:
            browser.close()
    return dict(status='PASS',default_profile='v13.2',profiles=results,
                not_run=['real audio/devices/cloud APIs','Pi LCD visual acceptance'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--face',default=SCREEN_PROFILES['v13.2']['default_path'])
    parser.add_argument('--r10-face',default=SCREEN_PROFILES['r10']['default_path'])
    parser.add_argument('--chromium',default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.face,args.r10_face,args.chromium),indent=2))


if __name__=='__main__':
    main()
