#!/usr/bin/env python3
"""Actual product music decoder, transport keys, and explicit v10 review UI.

Only short synthetic PCM/MP4 are used. Providers, microphone, hardware and
browser audio remain forbidden; this does not approve the private user media.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks import check_desktop_app_browser as desktop
from checks.check_full_batch_browser import replace_once
from bmo_ai.screen_profiles import SCREEN_PROFILES


def child_source():
    source = desktop.CHILD
    source = replace_once(source, "    counts['hosts']+=1", r'''
    counts['hosts']+=1
    from bmo_ai.features.media import MediaFeature,FakeMediaBackend
    from bmo_ai.features.media.assets import MediaAsset
    media_root=root/'music';meta=json.loads((media_root/'fixture.json').read_text())
    def media_clock():
        try:return float((root/'media-clock.txt').read_text())
        except FileNotFoundError:return 0.
    asset=MediaAsset(**meta)
    settings['media']=MediaFeature(media_root,(asset,),backend=FakeMediaBackend(clock=media_clock,pausable=True),clock=clock)
    settings['voice_pack_directory']=root/'organized-pack'
    from bmo_ai.voice_pack import OrganizedVoicePack
    pack=OrganizedVoicePack(settings['voice_pack_directory'])
    archive=root/'synthetic-v10.zip'
    pack.import_zip(archive,expected_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
''')
    return source


def prepare(directory):
    """Local deterministic media generation, with no output device or network."""
    sys.path.insert(0,str(SOFTWARE/'tests'))
    from test_voice_pack_v10 import pcm
    from bmo_ai import fish_tts
    import zipfile
    root=Path(directory);music=root/'music';music.mkdir(mode=0o700)
    wav=pcm(8.,rate=44100);(music/'face.wav').write_bytes(wav)
    cues=json.dumps([dict(time=0.,mouth='closed'),dict(time=.1,mouth='low'),
                     dict(time=.3,mouth='wide'),dict(time=.7,mouth='low')]).encode()
    (music/'energy.json').write_bytes(cues)
    command=[shutil.which('ffmpeg'),'-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i',
        'testsrc2=size=160x90:rate=25','-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
        '-t','8','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(music/'video.mp4')]
    subprocess.run(command,check=True,timeout=20,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    subprocess.run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-loglevel','error','-i',str(music/'video.mp4'),
        '-vn','-acodec','pcm_s16le','-ac','1','-ar','44100',str(music/'video-audio.wav')],
        check=True,timeout=15,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    import wave
    with wave.open(str(music/'video-audio.wav'),'rb') as audio:video_duration=audio.getnframes()/audio.getframerate()
    sha=lambda name:hashlib.sha256((music/name).read_bytes()).hexdigest()
    meta=dict(id='space_potatoes',title='Synthetic two-mode song',source='synthetic_test_only',path='face.wav',
        sha256=sha('face.wav'),media_type='audio/wav',duration=8.,cue_path='energy.json',cue_sha256=sha('energy.json'),
        cue_kind='whole_mix_energy_approximation',video_path='video.mp4',video_sha256=sha('video.mp4'),
        video_audio_path='video-audio.wav',video_audio_sha256=sha('video-audio.wav'),video_audio_duration=video_duration)
    (music/'fixture.json').write_text(json.dumps(meta))
    rows=[];payloads={}
    for clip,seconds,text in [('RX01',.1,''),('wake_ack_01',1.76,"Yeah? What's up?"),
                              ('PH_COUNTDOWN_05S',5.,'Five. Four. Three. Two. One.')]:
        raw=pcm(seconds);path='audio/'+clip+'.wav';payloads[path]=raw
        rows.append(dict(clip_id=clip,spoken_text=text,file=path,sha256=hashlib.sha256(raw).hexdigest(),baked_gain=1.,
            requested_model='drama-3',voice_id=fish_tts.AARON_BMO_VOICE_ID,
            provenance=dict(requested_model='drama-3',server_model_attestation='NOT_AVAILABLE'),approvals=dict(listening_approved=False)))
    with zipfile.ZipFile(root/'synthetic-v10.zip','w') as archive:
        archive.writestr('catalogue.json',json.dumps(dict(schema='bmo.voice_asset_handoff.v10',not_runtime_compatible_manifest=True,entries=rows)))
        for name,raw in payloads.items():archive.writestr(name,raw)
    for file in root.rglob('*'):
        if file.is_file():file.chmod(0o600)


@contextmanager
def fixture_for(directory,profile,face):
    prepare(directory)
    with patch.object(desktop,'CHILD',child_source()):
        fixture=desktop.DesktopFixture(directory,profile=profile,face=face,scenario='character',extra_args=('--character-life','--voice-bank-directory',str(Path(directory)/'character-bank')))
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
    with tempfile.TemporaryDirectory(prefix='bmo-v10-music-browser-') as directory,fixture_for(directory,profile,face) as fixture:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},args=['--no-sandbox','--disable-gpu','--mute-audio',
                    '--disable-audio-output','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
            try:
                page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(12000)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                page.add_init_script("""window.__browserAudioCalls=0;window.__musicPlays=0;
                    const nativePlay=HTMLMediaElement.prototype.play;
                    HTMLMediaElement.prototype.play=function(){
                      if(this.tagName==='VIDEO'&&this.muted){window.__musicPlays++;
                        if(this.id==='music-video'&&window.__injectLateMusicRejection){
                          window.__injectLateMusicRejection=false;window.__musicLateRejectStarted=true;
                          nativePlay.call(this).catch(()=>{});
                          return new Promise((resolve,reject)=>setTimeout(()=>reject(Error('Synthetic retired play promise')),600));
                        }
                        return nativePlay.call(this);}
                      window.__browserAudioCalls++;throw Error('Browser audio forbidden');};""")
                def wait(predicate,timeout=12):
                    deadline=time.monotonic()+timeout
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if predicate(state):return state
                        page.wait_for_timeout(25)
                    raise AssertionError('Product state did not settle: '+json.dumps({k:state.get(k) for k in
                        ('mode','media','voice_pack','last_result','requests','test_fixture')}))
                def panel(button):
                    page.evaluate('()=>BMODesktop.menu()');page.locator('#'+button).click()
                def start(mode):
                    panel('music-menu');page.locator('[data-song-id=space_potatoes] [data-media-mode='+mode+']').click()
                    wait(lambda state:state['mode']=='music' and state['media']['variant']==mode)
                    page.wait_for_function('mode=>BMODesktop.features?.mode==="music"&&BMODesktop.features.media.variant===mode',arg=mode)
                page.goto(fixture.url,wait_until='domcontentloaded')
                try:
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                except Exception as error:
                    error.add_note(json.dumps(dict(desktop=fixture.state(),features=fixture.features(),browser_errors=errors),ensure_ascii=False))
                    raise
                panel('voice-menu')
                page.locator('[data-candidate=RX01]').wait_for(state='visible')
                assert page.locator('.voice-candidate').count()==3
                bank=fixture.features()['voice_pack']
                assert bank['version']==10 and bank['approved']==bank['listened']==bank['played']==0
                assert '등록됨' in page.locator('[data-candidate=RX01] .voice-status').inner_text()
                preview=page.locator('[data-candidate=RX01] [data-variant=raw]')
                assert preview.get_by_role('button',name='이 SHA 승인',exact=True).is_disabled()
                baseline=fixture.features()['test_fixture']
                preview.get_by_role('button',name='이 SHA 미리 듣기',exact=True).click()
                wait(lambda state:state['test_fixture']['players']==baseline['players']+1)
                wait(lambda state:not state['conversation_busy'] and state['audio_owner'] is None)
                after=fixture.features();assert after['voice_pack']['approved']==0
                assert after['test_fixture']['brain']==baseline['brain'] and after['test_fixture']['synthesis']==baseline['synthesis']
                page.locator('#voice-dialog .dialog-close').click()
                checks.append('v10 RX01 empty speech imports; explicit unapproved per-SHA review uses one cached player without approval or GPT/TTS')
                panel('music-menu')
                assert page.locator('[data-song-id=space_potatoes]').count()==1
                assert page.locator('[data-song-id=space_potatoes] button').count()==2
                page.locator('#music-dialog .dialog-close').click()
                start('video')
                player=page.locator('#music-video');player.wait_for(state='visible')
                page.wait_for_function("()=>document.querySelector('#music-video').readyState>=2")
                decoded=player.evaluate('v=>({w:v.videoWidth,h:v.videoHeight,duration:v.duration,muted:v.muted,volume:v.volume,error:v.error?.code||null})')
                assert decoded['w']==160 and decoded['h']==90 and decoded['duration']>7.9 and decoded['muted'] and decoded['volume']==0 and decoded['error'] is None,decoded
                media=fixture.features()['media'];assert media['variant']=='video' and fixture.features()['audio_owner']=='music'
                response=page.request.get(fixture.url.rstrip('/')+media['video_url'],headers={'Range':'bytes=0-31'})
                assert response.status==206 and response.headers['content-type']=='video/mp4' and len(response.body())==32
                assert b'ftyp' in response.body()
                checks.append('one stable song offers face/video; actual embedded-audio MP4 decodes through muted product video and verified Range route')
                baseline=fixture.features()['test_fixture']
                page.locator('#music-volume').fill('0');page.locator('#music-volume').dispatch_event('change')
                wait(lambda state:state['volume']['level']==0)
                page.locator('#music-mute').check();wait(lambda state:state['volume']['muted'])
                page.locator('#music-volume').fill('0.35');page.locator('#music-volume').dispatch_event('change')
                page.locator('#music-mute').uncheck();wait(lambda state:state['volume']['level']==.35 and not state['volume']['muted'])
                assert player.evaluate('v=>v.muted&&v.volume===0')
                page.locator('#music-toggle').focus()
                page.keyboard.down('Enter');page.keyboard.down('Enter')
                wait(lambda state:state['media']['state']=='paused')
                page.keyboard.up('Enter')
                page.wait_for_function("()=>document.querySelector('#music-video').paused")
                page.locator('#music-seek').fill('2');page.locator('#music-seek').dispatch_event('change')
                wait(lambda state:state['media']['state']=='paused' and abs(state['media']['position']-2)<.01)
                page.wait_for_function("()=>Math.abs(document.querySelector('#music-video').currentTime-2)<.15")
                page.locator('#music-replay').click()
                wait(lambda state:state['media']['position']<.01 and state['media']['state']=='playing')
                (Path(directory)/'media-clock.txt').write_text('.5')
                wait(lambda state:abs(state['media']['position']-.5)<.01)
                page.wait_for_function("()=>Math.abs(document.querySelector('#music-video').currentTime-.5)<.4")
                page.keyboard.down('Enter');wait(lambda state:state['media']['state']=='paused')
                page.wait_for_function("()=>document.querySelector('#music-video').paused")
                page.evaluate('()=>{window.__injectLateMusicRejection=true;}')
                page.locator('#music-toggle').click();wait(lambda state:state['media']['state']=='playing')
                page.wait_for_function('()=>window.__musicLateRejectStarted===true')
                page.keyboard.press('Escape');wait(lambda state:state['mode']=='conversation')
                page.keyboard.up('Enter');page.wait_for_timeout(750)
                assert page.locator('#notice').is_hidden(), 'A retired decoder play promise changed the new conversation'
                assert fixture.features()['test_fixture']['mic_sources']==baseline['mic_sources']
                assert player.evaluate("v=>v.paused&&!v.hasAttribute('src')")
                checks.append('real GP8 edges pause once; seek/replay follow owner clock; Escape detaches decoder, ignores its delayed play rejection, and held key release cannot start PTT')
                (Path(directory)/'media-clock.txt').write_text('0')
                start('face')
                assert page.locator('#music-video-view').is_hidden()
                assert player.evaluate("v=>!v.hasAttribute('src')")
                (Path(directory)/'media-clock.txt').write_text('.35')
                page.wait_for_function("()=>document.querySelector('#face').contentWindow.BMOFace.getState().mouth==='wide'")
                page.locator('#music-toggle').click();wait(lambda state:state['media']['state']=='paused')
                page.wait_for_function("()=>document.querySelector('#face').contentWindow.BMOFace.getState().mouth==='closed'")
                page.locator('#music-back').click();wait(lambda state:state['mode']=='conversation')
                page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'")
                page.locator('#face').click(position={'x':400,'y':200});page.keyboard.press('Enter')
                wait(lambda state:state['test_fixture']['brain']==baseline['brain']+1 and not state['conversation_busy'])
                assert page.evaluate('()=>window.__browserAudioCalls')==0
                assert not errors,errors
                checks.append('face mode samples supplied whole-mix mouth approximation; pause closes mouth, Back releases music and next actual GP8 dialogue works')
                code,counts,error=fixture.finish()
                assert code==0 and counts['forbidden']==0 and counts['players']==counts['reaped'] and counts['max_players']==1,error
            finally:browser.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest()==before
    return dict(status='PASS',checks=checks,not_run=['private song/voice playback','Pi speaker routing/listening','actual lyric lip sync'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',choices=('ci-synthetic',*SCREEN_PROFILES),default='ci-synthetic')
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.profile,args.chromium),ensure_ascii=False,indent=2))
