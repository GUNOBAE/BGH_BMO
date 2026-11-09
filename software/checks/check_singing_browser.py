#!/usr/bin/env python3
"""Product TWO MOODS 06 with the real PCM subprocess and synthetic sink.

Default face is public toy SVG geometry, not approved art or a design approval.
--face accepts only the exact user-supplied R10 for a local integration check.
No physical output, provider calls or browser audio are permitted.
"""
import argparse
from array import array
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
import wave

SOFTWARE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOFTWARE))
from checks import check_pcm_process_browser as pcm
from checks import check_v10_media_browser as media
from checks.check_full_batch_browser import replace_once
from bmo_ai.features.media.assets import MediaAsset
from bmo_ai.features.media.singing_analysis import prepare_profile
from bmo_ai.screen_profiles import SCREEN_PROFILES

FIXTURE=Path(__file__).with_name('fixtures')/'bmo_singing_contract.html'

@contextmanager
def fixture_for(directory,face=None):
    media.prepare(directory)
    root=Path(directory)/'music';meta=json.loads((root/'fixture.json').read_text())
    rate=44100;duration=16.
    samples=array('h')
    for i in range(int(rate*duration)):
        t=i/rate;cycle=t%3.4
        level=0 if cycle<.35 else .11 if cycle<1 else .72 if cycle<2 else .24 if cycle<2.7 else .02
        samples.append(int(28000*level*math.sin(t*2*math.pi*220)))
    with wave.open(str(root/meta['path']),'wb') as output:
        output.setparams((1,2,rate,len(samples),'NONE','not compressed'));output.writeframes(samples.tobytes())
    meta.update(sha256=hashlib.sha256((root/meta['path']).read_bytes()).hexdigest(),duration=duration)
    name=meta['id']+'-'+meta['sha256'][:12]+'.wav';(root/meta['path']).rename(root/name);meta['path']=name
    (root/'fixture.json').write_text(json.dumps(meta))
    manifest=root/(meta['id']+'.song.json')
    manifest.write_text(json.dumps(dict(version=2,asset=meta,private_use_confirmed=True,
        provenance=dict(source='user_provided',public_distribution_rights='NOT_VERIFIED'))));manifest.chmod(0o600)
    prepare_profile(root,MediaAsset(**meta))
    source=pcm.child_source()
    source=replace_once(source,"fixture=Path(os.environ['PYTHONPATH'])/'checks'/'fixtures'/'bmo_face_contract.html'",
        "fixture=Path(arguments[arguments.index('--face-file')+1])")
    profile='r10' if face else 'ci-synthetic';face=Path(face) if face else FIXTURE
    if profile=='r10':assert hashlib.sha256(face.read_bytes()).hexdigest()==SCREEN_PROFILES['r10']['sha256']
    with patch.object(media.desktop,'CHILD',source):
        fixture=media.desktop.DesktopFixture(directory,profile=profile,face=face,scenario='character',
            extra_args=('--character-life','--voice-bank-directory',str(Path(directory)/'character-bank')))
    try:yield fixture,meta
    finally:fixture.close()


def run(chromium='/usr/bin/chromium',face=None,screenshots=None):
    from playwright.sync_api import sync_playwright
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-singing-browser-') as directory,fixture_for(directory,face) as (fixture,asset):
        root=Path(directory)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},args=['--no-sandbox','--disable-gpu',
                '--mute-audio','--disable-audio-output','--disable-background-networking','--disable-component-update','--no-first-run'])
            try:
                page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(12000)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                transport=[]
                def requested(request):
                    if request.method=='POST' and request.url.endswith('/api/control'):
                        value=request.post_data_json.get('payload',{})
                        if value.get('action')=='media_command':transport.append(value['value'])
                page.on('request',requested)
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                page.add_init_script("""window.__browserAudioCalls=0;const play=HTMLMediaElement.prototype.play;
                    HTMLMediaElement.prototype.play=function(){if(this.tagName==='VIDEO'&&this.muted)return play.call(this);
                    window.__browserAudioCalls++;throw Error('Browser audio forbidden');};""")
                def settled(expression):page.wait_for_function('()=>'+expression)
                def wait(predicate,timeout=10):
                    deadline=time.monotonic()+timeout
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if predicate(state):return state
                        page.wait_for_timeout(20)
                    raise AssertionError('Singing product state timeout '+json.dumps({k:state.get(k) for k in ('mode','media','last_result')}))
                def controls():
                    page.locator('#music-surface').click(position={'x':5,'y':5});page.locator('#music-toolbar').wait_for(state='visible')
                def start(mode='face'):
                    page.evaluate('()=>BMODesktop.menu()');page.locator('#music-menu').click()
                    page.locator('[data-song-id=space_potatoes] [data-media-mode='+mode+']').click()
                    state=wait(lambda s:s['mode']=='music' and s['media']['variant']==mode and s['media']['output_started'])
                    settled("BMODesktop.features?.mode==='music'&&BMODesktop.features.media.output_started")
                    return state
                def state_playback(expected):
                    state=wait(lambda s:s['mode']=='music' and s['media']['state']==expected)
                    page.wait_for_function("expected=>BMODesktop.features?.media?.state===expected&&document.querySelector('#music-toggle').textContent===(expected==='playing'?'Pause':'Play')&&!document.querySelector('#music-toggle').disabled",arg=expected)
                    return state
                def back():
                    controls();page.locator('#music-back').click()
                    wait(lambda s:s['mode']=='conversation' and s['audio_owner'] is None and not s['conversation_busy'])
                    settled("BMODesktop.features?.mode==='conversation'&&BMODesktop.singing?.active===false")
                def conversation():
                    before=fixture.features()['test_fixture']['brain']
                    page.locator('#face').focus();page.keyboard.press('Enter')
                    wait(lambda s:s['test_fixture']['brain']==before+1 and not s['conversation_busy'] and s['audio_owner'] is None)
                    settled("BMODesktop.singing?.active===false")
                page.goto(fixture.url,wait_until='domcontentloaded')
                settled("BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                wait(lambda s:not s['conversation_busy'] and s['audio_owner'] is None)
                started=start();settled("BMODesktop.singing?.active&&BMODesktop.singing.status==='two_moods_06'")
                evidence=page.evaluate('()=>BMODesktop.singing')
                assert evidence['audio_sha256']==asset['sha256'] and evidence['mood']=='joyful',evidence
                assert evidence['source']=='audio-event-plan' and evidence['clock']=='python_pcm_position_seconds'
                assert started['media']['producer']=='isolated_process' and started['test_fixture']['pcm_max_live']==1
                checks.append('actual Desktop music Face uses a WAV-hash-bound cached analysis and supplied TWO MOODS renderer/planner; PCM subprocess remains sole audio owner')
                # HTTP admission is not a completed seek. Freeze the actual
                # browser's feature updates while the real Host applies it.
                controls();before_seek=fixture.features()['media']['playback_generation'];held_features=[]
                def hold_features(route):held_features.append(route)
                page.route('**/api/features',hold_features);wait(lambda _:bool(held_features))
                page.locator('#music-seek').fill('1.2');page.locator('#music-seek').dispatch_event('change')
                wait(lambda s:s['media']['playback_generation']>before_seek)
                assert page.locator('#music-toggle').is_disabled() and page.locator('#music-replay').is_disabled()
                pauses=len([x for x in transport if x['action']=='pause'])
                page.locator('#face').focus();page.keyboard.press('Enter')
                assert len([x for x in transport if x['action']=='pause'])==pauses
                stops=len([x for x in transport if x['action']=='stop'])
                controls();page.locator('#music-back').click()
                assert len([x for x in transport if x['action']=='stop'])==stops
                page.unroute('**/api/features',hold_features)
                for route in held_features:route.continue_()
                wait(lambda s:s['mode']=='conversation' and s['audio_owner'] is None and not s['conversation_busy'])
                settled("BMODesktop.features?.mode==='conversation'&&!BMODesktop.singing.active")
                actual_stops=[x for x in transport if x['action']=='stop']
                assert len(actual_stops)==stops+1 and actual_stops[-1]['generation']>before_seek
                checks.append('delayed feature receipt: seek/replay controls and GP8 short cannot issue stale-generation commands; one queued Back waits for the applied seek generation')
                start();settled("BMODesktop.singing?.active&&BMODesktop.singing.status==='two_moods_06'")
                if screenshots:
                    Path(screenshots).mkdir(parents=True,exist_ok=True)
                    settled("BMODesktop.singing.mouth!=='closed'")
                    page.screenshot(path=str(Path(screenshots)/'joyful.png'))
                # Seek to a real prepared sound event, not a periodic note timer.
                note_time=page.evaluate("""async()=>{const profile=await (await fetch(BMODesktop.features.media.singing_profile.url)).json();
                    const plan=BMOSingingPlan.compile(profile,{mood:'joyful'});if(!plan.noteEvents.length)throw Error('Fixture has no sound-event notes');
                    return plan.noteEvents[0].birth+.25;}""")
                controls();page.locator('#music-seek').fill(str(round(note_time,1)));page.locator('#music-seek').dispatch_event('change')
                settled("BMODesktop.singing.active&&BMODesktop.singing.noteCount>0")
                assert page.locator('#face').content_frame.locator('#bmo-a02-notes').evaluate('node=>node.childElementCount')>0
                page.locator('#music-toggle').click();state_playback('paused')
                settled("BMODesktop.singing.paused===true&&BMODesktop.singing.noteCount===0&&BMODesktop.singing.mouth==='closed'")
                page.locator('#music-seek').fill('4.2');page.locator('#music-seek').dispatch_event('change')
                wait(lambda s:abs(s['media']['position_seconds']-4.2)<.01)
                settled("BMODesktop.singing.active&&BMODesktop.singing.paused&&Math.abs(BMODesktop.singing.time-4.2)<.01")
                paused=fixture.features();generation=paused['media']['playback_generation'];workers=paused['test_fixture']['pcm_started']
                page.locator('#music-mood').select_option('gentle')
                wait(lambda s:s['media']['singing_mood']=='gentle')
                settled("BMODesktop.singing.mood==='gentle'&&BMODesktop.singing.active")
                changed=fixture.features()
                assert changed['media']['playback_generation']==generation and changed['test_fixture']['pcm_started']==workers
                assert abs(changed['media']['position_seconds']-4.2)<.01
                page.locator('#music-toggle').click();state_playback('playing')
                settled("BMODesktop.singing.mood==='gentle'&&!BMODesktop.singing.paused&&BMODesktop.singing.mouthAsset.startsWith('#a02-legacy-mouth-')")
                if screenshots:page.screenshot(path=str(Path(screenshots)/'gentle.png'))
                controls();before=fixture.features();page.locator('#music-mood').select_option('joyful')
                wait(lambda s:s['media']['singing_mood']=='joyful')
                settled("BMODesktop.singing.mood==='joyful'&&BMODesktop.singing.active")
                after=fixture.features()
                assert after['media']['playback_generation']==before['media']['playback_generation']
                assert after['media']['worker_pid']==before['media']['worker_pid']
                assert after['media']['position_seconds']>=before['media']['position_seconds']
                checks.append('real click pause/seek/resume: paused mouth closes and notes clear; both mood changes retain sample position and existing output generation/PID')
                controls();page.locator('#music-replay').click()
                wait(lambda s:s['media']['playback_generation']>after['media']['playback_generation'] and s['media']['output_started'])
                settled("BMODesktop.singing.active&&BMODesktop.singing.time<2")
                page.locator('#face').focus();page.keyboard.down('Enter');page.wait_for_timeout(1550);page.keyboard.up('Enter')
                wait(lambda s:s['mode']=='conversation' and s['audio_owner'] is None and not s['conversation_busy'])
                settled("BMODesktop.features?.mode==='conversation'&&!BMODesktop.singing.active")
                assert page.locator('#face').content_frame.locator('#bmo-a02-notes').evaluate('node=>node.childElementCount')==0
                conversation();checks.append('replay adopts the new generation; actual GP8 hold clears singing layers and keyup does not start PTT; a new explicit GP8 conversation works')
                start('video');settled("document.querySelector('#music-video').readyState>=2")
                assert not page.evaluate('()=>BMODesktop.singing.active')
                assert page.locator('#music-mood-control').is_hidden()
                back();checks.append('Video uses its existing muted browser decoder with Python audio and no singing layers')
                start();controls();page.locator('#music-seek').fill('15.6');page.locator('#music-seek').dispatch_event('change')
                wait(lambda s:s['mode']=='conversation' and s['audio_owner'] is None and not s['conversation_busy'])
                settled("!BMODesktop.singing.active&&BMODesktop.features?.mode==='conversation'")
                conversation();checks.append('real PCM natural completion clears the layer and owner; next PTT completes')
                # A delayed old analysis response must not reanimate after Stop.
                page.reload(wait_until='domcontentloaded');settled("BMODesktop.features?.mode==='conversation'")
                held=[]
                def hold_profile(route):held.append((route,route.fetch()))
                page.route('**/api/music-singing/**',hold_profile)
                start();wait(lambda _:bool(held));back();conversation()
                page.unroute('**/api/music-singing/**',hold_profile)
                for route,response in held:
                    try:route.fulfill(response=response)
                    except Exception:pass  # Stop aborts the consumer; the response is still deliberately completed late.
                page.wait_for_timeout(200)
                assert not page.evaluate('()=>BMODesktop.singing.active')
                assert page.locator('#face').content_frame.locator('#eyes').evaluate("node=>node.style.visibility")!='hidden'
                checks.append('late profile fetch after Stop and a new real conversation cannot reclaim visual ownership')
                # Face and feature snapshots arrive independently. Reproduce a new
                # thinking snapshot admitted BEFORE a delayed profile claims the
                # face, while the last feature snapshot still describes music.
                page.reload(wait_until='domcontentloaded');settled("BMODesktop.features?.mode==='conversation'")
                held=[];page.route('**/api/music-singing/**',hold_profile)
                start();wait(lambda _:bool(held))
                page.evaluate("""()=>{const a=BMOIntegration;a.poll=async()=>{};clearTimeout(a.timer);a.controller?.abort();a.pollGeneration++;
                    const next={...a.state,session_id:'synthetic-next-conversation',turn_id:'synthetic-turn',utterance_id:'',
                    sequence:1,phase:'thinking',playback:'idle',source:'synthetic_new_question',emotion:'neutral',position:0,cues:[]};
                    if(!a.applySnapshot(next))throw Error('new snapshot not admitted');}""")
                page.unroute('**/api/music-singing/**',hold_profile)
                for route,response in held:route.fulfill(response=response)
                settled("BMODesktop.singing.active===false&&BMOIntegration.visualOwner===null")
                page.wait_for_timeout(150)
                assert page.locator('#face').content_frame.locator('#eyes').evaluate("node=>node.style.visibility")!='hidden'
                assert page.evaluate("()=>document.querySelector('#face').contentWindow.BMOFace.getState().activity")=='thinking'
                back();checks.append('independent snapshot race: a new thinking state admitted before delayed profile completion wins even when the feature snapshot still says music')
                # Missing cached analysis follows old verified mouth cues, not level=.6.
                page.reload(wait_until='domcontentloaded');settled("BMODesktop.features?.mode==='conversation'")
                def deny_profile(route):route.fulfill(status=503,body='profile unavailable')
                page.route('**/api/music-singing/**',deny_profile)
                start();settled("BMODesktop.singing.status==='analysis_invalid_legacy_cues'")
                assert not page.evaluate('()=>BMODesktop.singing.active')
                settled("document.querySelector('#face').contentWindow.BMOFace.getState().mouth!=='closed'")
                back();page.unroute('**/api/music-singing/**',deny_profile)
                checks.append('missing/failed analysis keeps existing verified legacy mouth cues, with no fabricated default music level')
                assert page.evaluate('()=>window.__browserAudioCalls')==0 and not errors,errors
                code,counts,error=fixture.finish()
                assert code==0 and counts['forbidden']==0 and counts['pcm_started']==counts['pcm_reaped'],error
                assert counts['pcm_max_live']==1 and counts['players']==counts['reaped']
                return dict(status='PASS',checks=checks,face='exact unmodified local R10' if face else 'public synthetic SVG contract',
                    not_run=['Pi display/ALSA/acoustic quality','user listening/visual approval','physical buttons','private song analysis'])
            finally:browser.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium');parser.add_argument('--face',type=Path)
    parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args();print(json.dumps(run(args.chromium,args.face,args.screenshots),ensure_ascii=False,indent=2))
