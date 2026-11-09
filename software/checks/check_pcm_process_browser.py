#!/usr/bin/env python3
"""Actual Desktop/Host -> isolated PCM subprocess, with synthetic sink IO.

A real muted MP4 decoder and real key/click controls run. Only generated assets,
finite synthetic voice players and the explicit child test sink are allowed.
No ALSA, physical device, provider call or acoustic timing is exercised.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

SOFTWARE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOFTWARE))
from checks import check_v10_media_browser as media
from checks.check_full_batch_browser import replace_once
ORIGINAL_CHILD_SOURCE=media.child_source


def child_source():
    source=ORIGINAL_CHILD_SOURCE()
    source=replace_once(source,
        "    settings['media']=MediaFeature(media_root,(asset,),backend=FakeMediaBackend(clock=media_clock,pausable=True),clock=clock)",
        "    settings['music_directory']=media_root\n    settings['music_supervised']=True")
    source=replace_once(source,"real_synthesis=IntegrationVoice._synthesize",r'''
real_synthesis=IntegrationVoice._synthesize
pcm_children=[]
counts.update(pcm_started=0,pcm_reaped=0,pcm_max_live=0,host_pid=os.getpid(),parent_stalls=0)
''')
    source=replace_once(source,"    host=FeatureHost(**settings,client=client);hosts.append(host)",r'''
    from bmo_ai.features.runtime import RuntimePolicy
    from bmo_ai.features.media.pcm_clock import SampleClockWavBackend as ActualPCM
    settings.update(runtime=RuntimePolicy(simulate=False,conversation_ptt_microphone=True),
        demo_assets=False,voice=IntegrationVoice(settings['config'],client,simulate=True,emit=lambda *_a,**_kw:None))
    try:
        with patch('bmo_ai.features.media.SampleClockWavBackend',side_effect=lambda **options:ActualPCM(**options,_process_test_sink=True)):
            host=FeatureHost(**settings,client=client)
    except Exception:
        import traceback
        (root/'engine-failure.txt').write_text(traceback.format_exc())
        raise
    hosts.append(host)
''')
    source=replace_once(source,"def spawn(command,**options):",r'''
def spawn(command,**options):
    if command[:4]==[sys.executable,'-B','-m','bmo_ai.features.media.pcm_process']:
        counts['pcm_spawn_stage']='sink'
        assert command[command.index('--sink')+1]=='synthetic','Physical PCM sink forbidden'
        counts['pcm_spawn_stage']='env'
        assert not set(options.get('env',{}))-allowed,'Unexpected worker environment'
        counts['pcm_spawn_stage']='fd'
        assert len(options.get('pass_fds',()))==2,'Only verified WAV and bounded state descriptors are inherited'
        counts['pcm_spawn_stage']='ownership'
        assert not any(process.poll() is None for process in players),'Voice overlaps PCM'
        active=sum(process.poll() is None for process in pcm_children)
        assert active==0,'Two PCM owners overlapped'
        process=real_popen(command,**options);pcm_children.append(process)
        counts['pcm_spawn_stage']='started'
        counts['pcm_started']+=1;counts['pcm_max_live']=max(counts['pcm_max_live'],active+1)
        return process
    assert not any(process.poll() is None for process in pcm_children),'Voice overlaps PCM'
''')
    source=replace_once(source,"    def snapshot():\n        return original()|",r'''
    def snapshot():
        if (root/'stall-parent').exists():
            (root/'stall-parent').unlink()
            interval=sys.getswitchinterval()
            try:
                sys.setswitchinterval(.8)
                deadline=time.perf_counter()+.25
                while time.perf_counter()<deadline:pass
                counts['parent_stalls']+=1
            finally:sys.setswitchinterval(interval)
        return original()|''')
    source=replace_once(source,"brain_instances=1)",
        "brain_instances=1,voice_live=sum(process.poll() is None for process in players),pcm_live=sum(process.poll() is None for process in pcm_children),"
        "pcm_pids=[process.pid for process in pcm_children],"
        "pcm_child_status=dict(getattr(host.media.backend,'_process_snapshot',None) or {}))")
    source=replace_once(source,"for host in hosts:",r'''
for process in pcm_children:
    assert process.poll() is not None,'PCM worker survived desktop exit'
    try:os.waitpid(process.pid,os.WNOHANG)
    except ChildProcessError:counts['pcm_reaped']+=1
    else:raise AssertionError('PCM worker was not reaped')
for host in hosts:''')
    return source


@contextmanager
def fixture_for(directory):
    original_prepare=media.prepare
    def prepare(directory):
        original_prepare(directory)
        # The supervised path runs the actual private library/hash validator.
        # All files and metadata here are generated local test fixtures only.
        root=Path(directory)/'music';meta=json.loads((root/'fixture.json').read_text())
        name=meta['id']+'-'+meta['sha256'][:12]+'.wav'
        (root/meta['path']).rename(root/name);meta['path']=name
        manifest=root/(meta['id']+'.song.json')
        manifest.write_text(json.dumps(dict(version=2,asset=meta,private_use_confirmed=True,
            provenance=dict(source='user_provided',public_distribution_rights='NOT_VERIFIED'))))
        manifest.chmod(0o600)
    with patch.object(media,'child_source',child_source),patch.object(media,'prepare',prepare):
        with media.fixture_for(directory,'ci-synthetic',media.desktop.FIXTURE) as fixture:
            yield fixture


def run(chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-pcm-process-browser-') as directory,fixture_for(directory) as fixture:
        root=Path(directory)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                      '--disable-background-networking','--disable-component-update','--no-first-run'])
            try:
                page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(10000)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                page.add_init_script("""window.__browserAudioCalls=0;
                    const nativePlay=HTMLMediaElement.prototype.play;
                    HTMLMediaElement.prototype.play=function(){
                      if(this.tagName==='VIDEO'&&this.muted)return nativePlay.call(this);
                      window.__browserAudioCalls++;throw Error('Browser audio forbidden');};""")
                def settled(expression):page.wait_for_function('()=>'+expression)
                def wait(predicate,timeout=10):
                    deadline=time.monotonic()+timeout
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if predicate(state):return state
                        page.wait_for_timeout(20)
                    raise AssertionError('PCM product state did not settle: '+json.dumps({
                        k:state.get(k) for k in ('mode','media','audio_owner','last_result','test_fixture')}))
                def controls():
                    page.locator('#music-surface').click(position={'x':5,'y':5})
                    page.locator('#music-toolbar').wait_for(state='visible')
                def start(mode):
                    page.evaluate('()=>BMODesktop.menu()');page.locator('#music-menu').click()
                    page.locator('[data-song-id=space_potatoes] [data-media-mode='+mode+']').click()
                    state=wait(lambda s:s['mode']=='music' and s['media']['variant']==mode and s['media']['output_started'])
                    settled('BMODesktop.features?.mode==="music"&&BMODesktop.features.media.output_started===true')
                    output=state['media'];audit=state['test_fixture']
                    assert output['producer']=='isolated_process' and output['worker_pid']!=audit['host_pid'],output
                    assert audit['pcm_live']==1 and output['worker_pid'] in audit['pcm_pids'],audit
                    assert audit['pcm_child_status']['producer']=='isolated_process'
                    assert audit['pcm_child_status']['rate']>0 and audit['pcm_child_status']['submitted']>0
                    assert state['audio_owner']=='music'
                    return state
                def music_state(expected):
                    state=wait(lambda s:s['mode']=='music' and s['media']['state']==expected)
                    page.wait_for_function("expected=>BMODesktop.features?.media?.state===expected&&document.querySelector('#music-toggle').textContent===(expected==='playing'?'Pause':'Play')&&!document.querySelector('#music-toggle').disabled",arg=expected)
                    return state
                def back():
                    controls();page.locator('#music-back').click()
                    state=wait(lambda s:s['mode']=='conversation' and s['audio_owner'] is None and s['test_fixture']['pcm_live']==0)
                    settled('BMODesktop.features?.mode==="conversation"')
                    return state
                def conversation():
                    # The owner can finish before the browser renders its new
                    # input mode. Enter belongs to that rendered mode; wait for
                    # the actual return before asking for the next dialogue.
                    settled('BMODesktop.features?.mode==="conversation"&&BMODesktop.features.audio_owner===null&&document.querySelector("#music-surface").hidden')
                    previous=fixture.features()['test_fixture']['brain']
                    with page.expect_response(lambda response:response.request.method=='POST'
                            and response.url.endswith('/api/control')
                            and response.request.post_data_json.get('payload',{}).get('action')=='ptt') as admitted:
                        page.locator('#face').focus();page.keyboard.press('Enter')
                    receipt=admitted.value.json();request=receipt['request_id']
                    assert receipt['status']=='accepted',receipt
                    result=wait(lambda s:s['test_fixture']['brain']==previous+1 and not s['conversation_busy']
                        and s['audio_owner'] is None and any(item['request_id']==request
                            and item['code']=='voice_completed' for item in s['requests']))
                    assert result['test_fixture']['pcm_live']==0
                    return result
                page.goto(fixture.url,wait_until='domcontentloaded')
                try:settled("BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features?.mode==='conversation'")
                except Exception as error:
                    error.add_note('Desktop startup: '+json.dumps(fixture.state()))
                    error.add_note('Browser errors: '+repr(errors))
                    if (root/'engine-failure.txt').exists():error.add_note((root/'engine-failure.txt').read_text())
                    raise
                wait(lambda s:not s['conversation_busy'] and s['audio_owner'] is None and s['test_fixture']['voice_live']==0)
                for mode in ('video','face'):
                    state=start(mode)
                    if mode=='video':
                        settled("document.querySelector('#music-video').readyState>=2")
                        video=page.locator('#music-video').evaluate('v=>({width:v.videoWidth,height:v.videoHeight,muted:v.muted,volume:v.volume,error:v.error?.code||null})')
                        assert video==dict(width=160,height=90,muted=True,volume=0,error=None),video
                    else:
                        assert page.locator('#music-video-view').is_hidden()
                        settled("document.querySelector('#face').contentWindow.BMOFace.getState().mouth!=='closed'")
                    before=state['media']['position']
                    (root/'stall-parent').touch()
                    after=wait(lambda s:s['test_fixture']['parent_stalls']>=(1 if mode=='video' else 2)
                        and s['media']['position']>before+.10)
                    assert after['media']['state']=='playing' and after['media']['position']>before+.10,after['media']
                    assert after['test_fixture']['pcm_child_status']['error_code'] is None
                    checks.append(mode+': actual isolated producer PID/consumption clock continues through a 250ms parent GIL stall; actual video decode or existing face mouth stays on the product route')
                    controls();music_state('playing');page.locator('#music-toggle').click()
                    paused=music_state('paused');old_pid=paused['media']['worker_pid']
                    assert paused['test_fixture']['pcm_live']==0
                    page.locator('#music-seek').fill('2');page.locator('#music-seek').dispatch_event('change')
                    wait(lambda s:s['media']['state']=='paused' and abs(s['media']['position']-2)<.01)
                    settled('Math.abs(BMODesktop.features.media.position-2)<.01')
                    page.locator('#music-toggle').click();resumed=music_state('playing')
                    resumed=wait(lambda s:s['media']['output_started'] and s['media']['position']>2.05)
                    assert resumed['media']['worker_pid']!=old_pid and resumed['test_fixture']['pcm_live']==1
                    pid=resumed['media']['worker_pid'];started=resumed['test_fixture']['pcm_started']
                    page.locator('#music-volume').fill('0.5');page.locator('#music-volume').dispatch_event('change')
                    wait(lambda s:s['volume']['level']==.5)
                    page.locator('#music-mute').check()
                    muted=wait(lambda s:s['volume']['muted'] and s['test_fixture']['pcm_child_status'].get('synthetic_last_amplitude')==0)
                    assert muted['media']['worker_pid']==pid and muted['test_fixture']['pcm_started']==started
                    page.locator('#music-mute').uncheck()
                    wait(lambda s:not s['volume']['muted'] and s['test_fixture']['pcm_child_status'].get('synthetic_last_amplitude',0)!=0)
                    stopped=back()
                    assert stopped['media']['last_terminal']['outcome']=='cancelled'
                    assert stopped['media']['last_terminal']['producer']=='isolated_process'
                    assert stopped['media']['last_terminal']['resource_released']
                    conversation()
                    checks.append(mode+': pause reaps one producer, seek/resume starts only its replacement; real shared volume/mute reaches that process, Back reaps output and releases ownership')
                start('face')
                ended=wait(lambda s:s['mode']=='conversation' and s['audio_owner'] is None and s['test_fixture']['pcm_live']==0 and s['media']['last_terminal'] and s['media']['last_terminal']['outcome']=='ended',timeout=12)
                assert ended['media']['last_terminal']['error_code'] is None
                assert ended['media']['last_terminal']['worker_exit_code']==0
                checks.append('actual child-clock natural end closes/reaps the producer and returns to conversation, distinct from manual cancellation')
                answered=conversation()
                assert answered['test_fixture']['stt']==3 and answered['test_fixture']['pcm_live']==0
                assert page.evaluate('()=>window.__browserAudioCalls')==0
                assert not errors,errors
                code,counts,error=fixture.finish()
                assert code==0 and counts['forbidden']==0 and counts['players']==counts['reaped'],error
                assert counts['pcm_started']==counts['pcm_reaped'] and counts['pcm_max_live']==1
                checks.append('native Enter starts exactly one next PTT/answer after cleanup; all finite voices and PCM children are reaped, no browser audio or real IO')
                return dict(status='PASS',checks=checks,pcm_started=counts['pcm_started'],pcm_reaped=counts['pcm_reaped'],not_run=['ALSA/physical speaker','Pi CPU/performance','private song playback','paid STT/GPT/Fish','physical buttons'])
            finally:browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.chromium),ensure_ascii=False,indent=2))
