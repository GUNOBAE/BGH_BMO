#!/usr/bin/env python3
"""Approved local SVG through the real Desktop; no copied artwork/device IO.

Pause/seek the production WAAPI animation for deterministic start/mid/end PNGs.
The actual pinned R10 tree is used; the public text-only fixture cannot approve
an artwork animation. PNGs remain in an explicit private local evidence folder.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks.check_desktop_app_browser import DesktopFixture
from bmo_ai.screen_profiles import SCREEN_PROFILES


INSTRUMENTATION = """window.__entranceCount=0;window.__rootAnimations=0;window.__audioCalls=0;
    const original=Element.prototype.animate;
    Element.prototype.animate=function(...args){
      const animation=original.apply(this,args);
      if(this.matches('[data-bmo-expression-entrance]')){
        window.__entranceCount++;animation.pause();animation.currentTime=0;
      }
      if(['MAIN','IFRAME','SVG','BODY','HTML'].includes(this.tagName.toUpperCase()))window.__rootAnimations++;
      return animation;
    };
    HTMLMediaElement.prototype.play=function(){window.__audioCalls++;throw Error('Browser audio forbidden')};"""


def iframe_reload_ack_guard(browser, profile, face):
    """Hold the new iframe blank while a real old entrance gets cancelled."""
    with tempfile.TemporaryDirectory(prefix="bmo-entrance-reload-") as directory:
        fixture=DesktopFixture(directory,profile=profile,face=face,scenario='character',
            extra_args=('--character-life','--startup-timeout','60','--voice-bank-directory',
                        str(Path(directory)/'character-bank')))
        page=browser.new_page(viewport={'width':800,'height':480})
        held,acks,loads=[],[],[]
        try:
            page.set_default_timeout(8000)
            page.route('**/*',lambda route:route.continue_()
                if route.request.url.startswith(fixture.url) else route.abort())
            page.add_init_script(INSTRUMENTATION)
            def frame_route(route):
                loads.append(1)
                if len(loads)>1:held.append(route)
                else:route.continue_()
            def command_route(route):
                if route.request.method=='POST':
                    body=route.request.post_data_json
                    if body.get('payload',{}).get('action')=='face_presented':acks.append(1)
                route.continue_()
            page.route('**/face.html?kiosk=1',frame_route)
            page.route('**/api/desktop',command_route)
            page.goto(fixture.url,wait_until='domcontentloaded')
            page.wait_for_function("()=>BMOIntegration.entrance?.animation&&document.querySelector('#splash').hidden")
            assert acks==[] and fixture.state()['face_presented'] is False
            page.evaluate("()=>document.querySelector('#face').contentWindow.location.reload()")
            # Dispatch events while the NEW HTML remains unavailable. An old
            # animation's resolved promise must not acknowledge this blank face.
            deadline=time.monotonic()+8
            while not held and time.monotonic()<deadline:page.wait_for_timeout(20)
            assert held, 'Expected held iframe-only reload'
            page.wait_for_timeout(250)
            assert acks==[], 'Retired entrance acknowledged a new, unready iframe'
            assert fixture.state()['face_presented'] is False
            assert fixture.features()['test_fixture']['players']==0
            held.pop().continue_()
            page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
            assert len(acks)==1 and fixture.state()['face_presented'] is True
            assert page.evaluate("()=>document.querySelector('#face').contentWindow.__entranceCount") == 0
            assert page.evaluate("()=>BMOIntegration.entrance===null&&!BMOIntegration.closed")
            code,counts,error=fixture.finish()
            assert code==0 and counts['forbidden']==0,error
            assert counts['players']==counts['reaped']
            return dict(acks_before_fresh_frame=0,acks_after_fresh_handshake=1,
                        greeting_before_fresh_frame=0,repeat_entrance_on_reload=False)
        finally:
            for route in held:
                try:route.abort()
                except Exception:pass
            page.close();fixture.close()


def run(profile="r10", chromium="/usr/bin/chromium", evidence_directory=None):
    from playwright.sync_api import sync_playwright
    assert profile in SCREEN_PROFILES
    face = Path(SCREEN_PROFILES[profile]["default_path"])
    before = hashlib.sha256(face.read_bytes()).hexdigest()
    assert before == SCREEN_PROFILES[profile]["sha256"]
    if evidence_directory is None:
        evidence_directory = (Path.home()/".local/state/bmo/field-entrance"/
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    evidence_directory = Path(evidence_directory).expanduser().absolute()
    assert not evidence_directory.is_relative_to(SOFTWARE.parent)
    evidence_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    checks, snapshots, pngs = [], {}, {}
    with tempfile.TemporaryDirectory(prefix="bmo-expression-entrance-") as directory:
        fixture = DesktopFixture(directory, profile=profile, face=face, scenario="character",
            extra_args=("--character-life", "--startup-timeout", "60", "--voice-bank-directory",
                        str(Path(directory)/"character-bank")))
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium, headless=True,
                    env={"PATH": os.defpath, "HOME": directory, "LC_ALL": "C.UTF-8"},
                    args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output",
                          "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
                try:
                    page = browser.new_page(viewport={"width": 800, "height": 480})
                    page.set_default_timeout(8000)
                    page.route("**/*", lambda route: route.continue_()
                               if route.request.url.startswith(fixture.url) else route.abort())
                    page.add_init_script(INSTRUMENTATION)
                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMOIntegration.entrance?.animation&&document.querySelector('#splash').hidden")
                    assert fixture.state()["character_life"]["enabled"]
                    assert fixture.state()["progress_percent"] == 100
                    assert page.locator('#splash h1').inner_text() == 'Starting BMO…'
                    assert page.locator('#loading-stage').inner_text() == 'Ready!'
                    # Deterministic native eye/brow/tear/mouth coordinates. This
                    # uses the approved readback/inspection API, never redraws.
                    page.evaluate("""()=>document.querySelector('#face').contentWindow.BMOFace.inspect({
                        emotion:'crying',time:2,blink:'open',gaze:[.2,-.1],eyeOnly:[.1,.2],tears:true})""")

                    def snapshot(label, current_time=None):
                        if current_time is not None:
                            page.evaluate("t=>{BMOIntegration.entrance.animation.currentTime=t}", current_time)
                        page.wait_for_timeout(35)
                        value = page.evaluate("""()=>{
                          const frame=document.querySelector('#face'),doc=frame.contentDocument;
                          const svg=doc.getElementById('face'),root=doc.getElementById('lookRoot');
                          const rect=svg.querySelector(':scope > rect'),layer=root.closest('[data-bmo-expression-entrance]');
                          const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
                          return {main:box(document.querySelector('main')),frame:box(frame),svg:box(svg),background:box(rect),
                            background_fill:rect.getAttribute('fill'),view_box:svg.getAttribute('viewBox'),
                            layer_transform:layer?doc.defaultView.getComputedStyle(layer).transform:null,
                            root_transform:root.getAttribute('transform'),eye_transform:doc.getElementById('eyeOffset').getAttribute('transform'),
                            native_origin:frame.contentWindow.BMOFace.getState().origin,
                            native_eye_offset:frame.contentWindow.BMOFace.getState().eyeOffset,
                            expression_bounds:box(root),root_parent:root.parentElement.tagName,
                            expression_children:[...root.children].map(e=>e.id),
                            eye_brows_tears_together:!!layer&&['eyes','angryBrows','tears','mouth'].every(id=>layer.contains(doc.getElementById(id))),
                            entrance_status:BMOIntegration.entranceState.status,
                            root_animation_count:window.__rootAnimations+frame.contentWindow.__rootAnimations};
                        }""")
                        target=evidence_directory/(label+".png")
                        page.screenshot(path=str(target));target.chmod(0o600)
                        pngs[label] = dict(file=target.name, sha256=hashlib.sha256(target.read_bytes()).hexdigest())
                        snapshots[label] = value
                        return value

                    start=snapshot("start", 0)
                    middle=snapshot("middle", 275)
                    assert start['eye_brows_tears_together'] and middle['eye_brows_tears_together']
                    assert start['expression_bounds']['y'] > 480, start
                    assert 0 < middle['expression_bounds']['y'] < start['expression_bounds']['y'], middle
                    assert start['root_transform'] == start['native_origin']
                    assert middle['root_transform'] == middle['native_origin']
                    assert start['eye_transform'] == start['native_eye_offset']
                    assert middle['eye_transform'] == middle['native_eye_offset']
                    page.evaluate("()=>{const a=BMOIntegration.entrance.animation;a.currentTime=550;a.play()}")
                    page.wait_for_function("()=>BMOIntegration.entranceState.status==='completed'")
                    end=snapshot("end")
                    for field in ('main','frame','svg','background','background_fill','view_box'):
                        assert start[field] == middle[field] == end[field], (field, start[field], middle[field], end[field])
                    assert start['background_fill'].lower() == '#cbfad3'
                    assert all(item['root_animation_count'] == 0 for item in snapshots.values())
                    assert end['layer_transform'] is None and end['root_parent'] == 'svg'
                    assert start['expression_children'] == middle['expression_children'] == end['expression_children']
                    assert page.locator('#face').content_frame.locator('body').evaluate('()=>window.__entranceCount') == 1
                    checks.append('actual approved SVG background/main/iframe/viewBox stay fixed at start/middle/end while eyes/mouth/brows/tears rise together')
                    checks.append('temporary outer SVG transform preserves native lookRoot/eyeOffset gaze and restores exact expression parent/order')
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                    assert fixture.state()['face_presented']
                    assert fixture.features()['test_fixture']['brain'] == 0
                    checks.append('English achieved-checkpoint loading remains; entrance finishes before cache-only greeting, with zero brain/API calls')

                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                    page.wait_for_timeout(150)
                    assert page.locator('#face').content_frame.locator('body').evaluate('()=>window.__entranceCount') == 0
                    assert page.evaluate("()=>BMOIntegration.entrance===null")
                    assert page.evaluate("()=>document.querySelector('#face').contentDocument.querySelectorAll('[data-bmo-expression-entrance]').length") == 0
                    checks.append('reload restores existing face/session without another entrance or greeting')

                    failed=page.evaluate("""()=>{
                      const win=document.querySelector('#face').contentWindow,proto=win.SVGGraphicsElement.prototype;
                      const original=proto.getBBox;
                      proto.getBBox=function(){throw Error('Injected layout failure')};
                      try{return BMOIntegration.presentExpression()}
                      finally{proto.getBBox=original}
                    }""")
                    assert failed['status']=='animation_unavailable'
                    assert page.evaluate("()=>BMOIntegration.entrance===null")
                    assert page.evaluate("()=>document.querySelector('#face').contentDocument.getElementById('lookRoot').parentElement.id==='face'")
                    assert page.evaluate("()=>document.querySelector('#face').contentDocument.querySelectorAll('[data-bmo-expression-entrance]').length") == 0
                    checks.append('injected SVG layout failure restores exact original tree and returns fixed unavailable status without latching startup')

                    reduced=page.evaluate("()=>BMOIntegration.presentExpression({reducedMotion:true})")
                    assert reduced['status'] == 'reduced_motion'
                    assert page.evaluate("()=>BMOIntegration.entrance===null")
                    page.evaluate("()=>{window.__cancelPresentation=BMOIntegration.presentExpression();BMOIntegration.cancelEntrance('operator_cancelled')}")
                    cancelled=page.evaluate("()=>window.__cancelPresentation")
                    assert cancelled['status'] == 'operator_cancelled'
                    assert page.evaluate("()=>document.querySelector('#face').contentDocument.getElementById('lookRoot').parentElement.id==='face'")
                    page.evaluate("()=>{window.__closePresentation=BMOIntegration.presentExpression();BMOIntegration.close()}")
                    assert page.evaluate("()=>window.__closePresentation")["status"] == 'closed'
                    assert page.evaluate("()=>document.querySelector('#face').contentDocument.querySelectorAll('[data-bmo-expression-entrance]').length") == 0
                    checks.append('reduced motion skips movement; explicit cancellation/adapter exit cancel animation and remove temporary layer')
                    assert page.evaluate("()=>window.__audioCalls+document.querySelector('#face').contentWindow.__audioCalls") == 0
                    code,counts,error=fixture.finish()
                    assert code == 0 and counts['forbidden'] == 0, error
                    assert counts['players'] == counts['reaped'] and counts['browsers'] == counts['browser_reaped']
                    checks.append('all test-owned children cleaned; no hardware/paid API/browser audio or original artwork writes')
                    reload_ack=iframe_reload_ack_guard(browser,profile,face)
                    checks.append('iframe-only reload cancels old entrance; zero late face acknowledgements/greetings before fresh HTML and handshake, no repeated entrance')
                finally:browser.close()
        finally:fixture.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest() == before
    result=dict(status='PASS',screen_profile=profile,checks=checks,snapshots=snapshots,pngs=pngs,
                artwork_sha256_before=before,artwork_sha256_after=before,
                evidence_directory=str(evidence_directory),reload_ack=reload_ack,physical_display_acceptance='NOT_RUN')
    report=evidence_directory/'report.json';report.write_text(json.dumps(result,ensure_ascii=False,indent=2));report.chmod(0o600)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen-profile',choices=tuple(SCREEN_PROFILES),default='r10')
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    parser.add_argument('--evidence-directory',type=Path)
    args=parser.parse_args()
    print(json.dumps(run(args.screen_profile,args.chromium,args.evidence_directory),ensure_ascii=False,indent=2))
