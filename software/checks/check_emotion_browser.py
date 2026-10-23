#!/usr/bin/env python3
"""Approved-face regression and the actual automatic-expression mock launcher.

All delays are synthetic observation fixtures, not latency measurements. The
launcher gets an independent temporary lock; an existing BMO is never stopped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmo_ai.integration_face import FACE_SHA256
from check_integration_browser import run as baseline_browser
from check_integration_end_to_end import launcher_url, send


CHILD = r'''
from contextlib import ExitStack
import inspect, json, os, signal, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from bmo_ai.integration import main
from bmo_ai.integration_session import IntegrationSession, SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice
from bmo_ai import integration_voice as voice_module
from bmo_ai import integration_session as session_module

allowed={'PATH','HOME','LC_ALL','LANG','LC_CTYPE','PYTHONPATH','PYTHONDONTWRITEBYTECODE'}
assert not set(os.environ)-allowed, 'Unexpected child environment field'
counts=dict(listen=0,brain=0,structured=0,synthesis=0,gain=0,cues=0,players=0,reaped=0,forbidden=0)
processes=[]
emotions=['crying','happy','sad','surprised','angry','neutral','crying','surprised','happy']
real_popen,real_listen,real_speak,real_play=(subprocess.Popen,IntegrationVoice.listen,
                                         IntegrationVoice.speak,IntegrationVoice._play)
real_gain,real_cues=voice_module.attenuated_copy,voice_module.wav_cues
real_session_init=IntegrationSession.__init__
def session_init(self,*args,**options):
    # Advance idle policy time without waiting or altering provider/player time.
    options['clock']=lambda:time.monotonic()+float(Path(sys.argv[3]).read_text())
    return real_session_init(self,*args,**options)
def forbidden(*args,**kwargs):
    counts['forbidden']+=1
    raise AssertionError('Live IO forbidden')
def spawn(command,**options):
    assert command==[sys.executable,'-c','import time; time.sleep(0.8)'], 'Non-synthetic child forbidden'
    assert not set(options.get('env',{}))-allowed, 'Unexpected player environment field'
    process=real_popen(command,**options)
    processes.append(process)
    counts['players']+=1
    return process
def listen(self):
    assert self.simulate, 'Live input forbidden'
    counts['listen']+=1
    time.sleep(.15)
    return real_listen(self)
def create(self,**request):
    emotion=emotions[counts['brain']]
    counts['brain']+=1
    self.requests.append(request)
    time.sleep(.15)
    speech='A short synthetic response.'
    if request.get('text',{}).get('format',{}).get('type')=='json_schema':
        counts['structured']+=1
        speech=json.dumps(dict(speech=speech,emotion=emotion))
    return SimpleNamespace(status='completed',incomplete_details=None,error=None,output_text=speech)
def speak(self,text,**options):
    assert text=='A short synthetic response.', 'Envelope/tag reached voice boundary'
    counts['synthesis']+=1
    return real_speak(self,text,**options)
def gain(*args,**options):
    counts['gain']+=1
    return real_gain(*args,**options)
def cues(*args,**options):
    counts['cues']+=1
    return real_cues(*args,**options)
def play(self,*args,**kwargs):
    bound=inspect.signature(real_play).bind(self,*args,**kwargs)
    emit=bound.arguments['emit']
    def held(kind,**fields):
        emit(kind,**fields)
        if kind=='prepared':
            time.sleep(.15)  # Expose prepared before the player is spawned.
    bound.arguments['emit']=held
    return real_play(*bound.args,**bound.kwargs)
def interrupt(*args):
    raise KeyboardInterrupt()
signal.signal(signal.SIGTERM,interrupt)
with ExitStack() as guards:
    for target in ('socket.socket.connect','bmo_ai.cloud.create_client',
                   'bmo_ai.audio.record_wav','bmo_ai.audio.transcribe',
                   'bmo_ai.fish_tts.synthesize_wav','bmo_ai.local_piper.synthesize_wav'):
        guards.enter_context(patch(target,side_effect=forbidden))
    guards.enter_context(patch('subprocess.Popen',side_effect=spawn))
    guards.enter_context(patch.object(IntegrationVoice,'listen',listen))
    guards.enter_context(patch.object(IntegrationVoice,'speak',speak))
    guards.enter_context(patch.object(IntegrationVoice,'_play',play))
    guards.enter_context(patch.object(voice_module,'attenuated_copy',gain))
    guards.enter_context(patch.object(voice_module,'wav_cues',cues))
    guards.enter_context(patch.object(session_module,'wav_cues',cues))
    guards.enter_context(patch.object(SimulatedClient,'create',create))
    guards.enter_context(patch.object(IntegrationSession,'__init__',session_init))
    result=main(['--simulate','--emotion-mode','auto','--port','0','--face-file',sys.argv[1]],
                lock_path=sys.argv[2])
for process in processes:
    assert process.poll() is not None, 'Owned player survived exit'
    try:
        os.waitpid(process.pid,os.WNOHANG)
    except ChildProcessError:
        counts['reaped']+=1
    else:
        raise AssertionError('Owned player was not reaped')
print('EMOTION_RESULT '+json.dumps(counts),flush=True)
raise SystemExit(result)
'''


INSTALL_OBSERVER = """() => {
  window.face=document.querySelector('#face').contentWindow.BMOFace;
  window.observed=[]; window.facePlayCalls=0;
  for(const method of ['playAudio','playBlob']) {
    face[method]=()=>{facePlayCalls++;throw new Error('Browser audio forbidden')};
  }
  function sample(){
    const s=face.getState(),v=BMOIntegration.state;
    if(v) observed.push({turn:v.turn_id,utterance:v.utterance_id,position:v.position,
      mode:v.emotion_mode,emotion:s.selectedEmotion,activity:s.activity,speech:s.speech,
      visible:s.emotion,mouth:s.mouth,eye:s.eyeHref,mouthHref:s.mouthHref,
      gaze:s.gaze,eyeOnly:s.eyeOnly,nod:s.nod,tears:s.tearCount,blink:s.blink,playback:v.playback});
    if(observed.length>1200) observed.shift();
    requestAnimationFrame(sample);
  }
  sample();
}"""


def observe(page, url):
    page.goto(url)
    page.wait_for_function("()=>document.querySelector('#face').contentWindow.BMOFace?.getState().version==='13.2'")
    page.evaluate(INSTALL_OBSERVER)


def media_calls(page):
    return page.evaluate("window.__mediaPlayCalls+window.facePlayCalls") + page.locator("#face").content_frame.locator("body").evaluate("()=>window.__mediaPlayCalls")


def native_motion_check(browser,url):
    """Run original RAF-driven gaze/nod/blink with a controlled browser clock."""
    page=browser.new_page()
    try:
        page.set_default_timeout(4500)
        page.clock.install()
        page.route("**/*",lambda route:route.continue_() if route.request.url.startswith(url) else route.abort())
        page.add_init_script("""window.__mediaPlayCalls=0;
          HTMLMediaElement.prototype.play=function(){window.__mediaPlayCalls++;
            throw new Error('Browser audio forbidden')};""")
        observe(page,url)
        page.evaluate("""() => {
          BMOIntegration.close();
          face.inspect({emotion:'crying',activity:'idle',time:0,cryStart:0,tears:true});
          face.setActivity('listening');
        }""")
        page.clock.run_for(1220)
        listening=page.evaluate("face.getState()")
        assert listening['selectedEmotion']==listening['emotion']=='crying'
        assert listening['activity']=='listening' and listening['mouth']=='closed'
        assert abs(listening['gaze']['x']-.95)<.01 and abs(listening['gaze']['y']+.8)<.01
        assert listening['nod']>10 and listening['tearCount']>0
        page.evaluate("face.setActivity('thinking')")
        page.clock.run_for(250)
        thinking=page.evaluate("face.getState()")
        assert thinking['selectedEmotion']==thinking['emotion']=='crying' and thinking['tearCount']>0
        assert thinking['mouthHref']=='#mouth-thinking-hmm'
        assert abs(thinking['gaze']['x']+.82)<.01 and abs(thinking['gaze']['y']+.7)<.01
        assert abs(thinking['eyeOnly']['x']+.16)<.01 and abs(thinking['eyeOnly']['y']+.08)<.01
        page.evaluate("""() => {
          face.inspect({emotion:'crying',activity:'idle',time:0,cryStart:0,tears:true});
          face.resume();
        }""")
        page.clock.run_for(3300)
        blink=page.evaluate("face.getState()")
        assert blink['blink']=='closed' and blink['eyeHref']=='#eye-crying-closed'
        page.clock.run_for(200)
        assert page.evaluate("face.getState().blink")=='open'
        aperture=page.evaluate("""() => ['closed','wide'].map(mouth=>{
          const s=face.inspect({emotion:'surprised',activity:'idle',time:0,mouth,blink:'open'});
          return {mouth:s.mouth,eye:s.eyeHref,scale:s.eyeScale};
        })""")
        assert aperture[0]['eye']=='#eye-surprised-open-closed' and aperture[1]['eye']=='#eye-surprised-open-wide'
        assert aperture[1]['scale']>aperture[0]['scale']
        assert media_calls(page)==0
    finally:
        page.close()


def run(face_path, chromium):
    from playwright.sync_api import sync_playwright

    face_path=Path(face_path).expanduser().resolve(strict=True)
    before=hashlib.sha256(face_path.read_bytes()).hexdigest()
    assert before==FACE_SHA256, "Approved face hash mismatch"
    baseline=baseline_browser(str(face_path),chromium)
    checks=[]
    with tempfile.TemporaryDirectory(prefix="bmo-emotion-browser-") as home:
        env={"PATH":os.defpath,"HOME":home,"LC_ALL":"C.UTF-8",
             "PYTHONPATH":str(Path(__file__).resolve().parents[1]),"PYTHONDONTWRITEBYTECODE":"1"}
        clock_path=Path(home)/'clock-offset.txt'
        clock_path.write_text('0')
        process=subprocess.Popen([sys.executable,"-u","-B","-c",CHILD,str(face_path),str(Path(home)/"launcher.lock"),str(clock_path)],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        try:
            url=launcher_url(process)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=chromium,headless=True,timeout=6000,env=env,
                    args=["--no-sandbox","--disable-gpu","--mute-audio","--disable-audio-output"])
                try:
                    page=browser.new_page(viewport={"width":1024,"height":600})
                    page.set_default_timeout(4500)
                    page.route("**/*",lambda route:route.continue_() if route.request.url.startswith(url) else route.abort())
                    page.add_init_script("""window.__mediaPlayCalls=0;
                      HTMLMediaElement.prototype.play=function(){window.__mediaPlayCalls++;
                        throw new Error('Browser audio forbidden')};""")
                    observe(page,url)
                    idle_offset=0

                    def long_idle(emotion):
                        nonlocal idle_offset
                        idle_offset+=3600
                        next_clock=clock_path.with_suffix('.next')
                        next_clock.write_text(str(idle_offset))
                        os.replace(next_clock,clock_path)
                        # This GET also refreshes browser liveness on the host's
                        # advanced clock before admitting another worker turn.
                        with urlopen(url+'api/state',timeout=2) as response:
                            snapshot=json.load(response)
                        assert snapshot['emotion']==emotion and snapshot['phase']=='idle'
                        page.wait_for_timeout(100)
                        assert page.evaluate('face.getState().selectedEmotion')==emotion

                    def turn(emotion, *, mode="auto", reconnect=False):
                        previous=page.evaluate("BMOIntegration.state?.turn_id")
                        expected=page.evaluate('face.getState().selectedEmotion')
                        page.evaluate("observed.length=0")
                        send(process,"")
                        for phase in ("listening","thinking"):
                            try:
                                page.wait_for_function("([old,phase])=>observed.some(s=>s.turn!==old&&s.activity===phase)",arg=[previous,phase])
                            except Exception:
                                metadata=page.evaluate("({recent:observed.slice(-5),state:{phase:BMOIntegration.state?.phase,playback:BMOIntegration.state?.playback,emotion:BMOIntegration.state?.emotion,mode:BMOIntegration.state?.emotion_mode}})")
                                raise AssertionError(f"Missing {phase} in synthetic {emotion} turn: {json.dumps(metadata)}") from None
                        page.wait_for_function("old=>observed.some(s=>s.turn!==old&&s.playback==='prepared')",arg=previous)
                        staged=page.evaluate("observed.filter(s=>s.playback!=='started'&&['listening','thinking'].includes(s.activity))")
                        assert staged and all(s["emotion"]==s['visible']==expected and s["speech"]=="off" for s in staged), "Displayed emotion changed before player start"
                        assert any(s['activity']=='listening' and s['gaze']['x']>0 and s['gaze']['y']<0 for s in staged)
                        assert any(s['activity']=='thinking' and s['gaze']['x']<0 and s['eyeOnly']['x']<0 and s['mouthHref']=='#mouth-thinking-hmm' for s in staged)
                        if expected=='crying':
                            assert any(s['tears']>0 and s['eye'].startswith('#eye-crying-') for s in staged)
                        page.wait_for_function("([old,emotion])=>observed.some(s=>s.turn!==old&&s.speech==='external'&&s.emotion===emotion&&s.mouth!=='closed')",arg=[previous,emotion])
                        active=page.evaluate("({turn:BMOIntegration.state.turn_id,utterance:BMOIntegration.state.utterance_id,position:BMOIntegration.state.position})")
                        assert page.evaluate("face.getState().eyeHref").startswith("#eye-"+emotion+"-"), "Wrong original SVG emotion reference"
                        if reconnect:
                            assert media_calls(page)==0
                            observe(page,url)
                            page.wait_for_function("([turn,emotion,position])=>{const v=BMOIntegration.state,s=face.getState();return v?.turn_id===turn&&v.position>=position&&s.speech==='external'&&s.selectedEmotion===emotion}",arg=[active["turn"],emotion,active["position"]])
                        page.wait_for_function("([turn,emotion])=>{const v=BMOIntegration.state,s=face.getState();return v?.turn_id===turn&&v.playback==='ended'&&s.speech==='off'&&s.mouth==='closed'&&s.selectedEmotion===emotion}",arg=[active["turn"],emotion])
                        # End is published before the worker's final cache/file
                        # cleanup; do not race its deliberate busy admission.
                        page.wait_for_timeout(100)
                        return active

                    for emotion in ("crying","happy","sad","surprised","angry","neutral","crying"):
                        last=turn(emotion,reconnect=emotion=="surprised")
                        long_idle(emotion)
                    checks.append("all six emotions survive ended, hour-long mock idle, next listening/thinking/prepared until new player start")
                    checks.append("browser reconnect resumes current utterance/position/emotion without restarting speech")

                    send(process,"/repeat")
                    page.wait_for_function("old=>{const v=BMOIntegration.state,s=face.getState();return v?.turn_id!==old&&s.speech==='external'&&s.selectedEmotion==='crying'}",arg=last["turn"])
                    stale=page.evaluate("structuredClone(BMOIntegration.state)")
                    send(process,"/cancel")
                    page.wait_for_function("()=>{const v=BMOIntegration.state,s=face.getState();return v?.playback==='cancelled'&&s.speech==='off'&&s.mouth==='closed'&&s.selectedEmotion==='crying'}")
                    rejected=page.evaluate("stale=>{stale.sequence=BMOIntegration.sequence+1000;return BMOIntegration.applySnapshot(stale)}",stale)
                    assert rejected is False, "Retired emotion snapshot was accepted"
                    page.wait_for_timeout(100)
                    checks.append("repeat preserves cached crying expression; cancel closes mouth while retaining crying and rejects stale utterance")

                    send(process,"/emotion sad")
                    page.wait_for_function("()=>BMOIntegration.state?.emotion_mode==='manual'&&face.getState().selectedEmotion==='sad'")
                    turn("sad",mode="manual")
                    long_idle('sad')
                    assert page.evaluate("face.getState().selectedEmotion")=="sad", "Auto expiry cleared manual override"
                    send(process,"/emotion auto")
                    page.wait_for_function("()=>BMOIntegration.state?.emotion_mode==='auto'&&face.getState().selectedEmotion==='sad'")
                    turn("happy")
                    send(process,"/repeat")
                    page.wait_for_function("()=>face.getState().speech==='external'&&face.getState().selectedEmotion==='happy'")
                    page.wait_for_function("()=>BMOIntegration.state?.playback==='ended'&&face.getState().speech==='off'")
                    send(process,"/reset")
                    page.wait_for_function("()=>BMOIntegration.state?.emotion_mode==='auto'&&face.getState().selectedEmotion==='neutral'")
                    checks.append("manual-to-auto keeps displayed sad until new happy playback; repeat retains happy; explicit reset returns neutral")
                    assert media_calls(page)==0
                    native_motion_check(browser,url)
                    checks.append("unmodified native listening gaze/nods, thinking gaze/hmm, crying tears, automatic blink and surprise aperture coupling survive controlled clock")
                    stdout,_=process.communicate("/quit\n",timeout=4)
                    assert process.returncode==0, "Auto launcher did not exit cleanly"
                    summaries=[json.loads(line.removeprefix("EMOTION_RESULT ")) for line in stdout.splitlines() if line.startswith("EMOTION_RESULT ")]
                    assert len(summaries)==1
                    counts=summaries[0]
                    assert counts["listen"]==counts["brain"]==counts["synthesis"]==counts["gain"]==counts["cues"]==9, counts
                    assert counts["structured"]==8 and counts["players"]==counts["reaped"]==11 and counts["forbidden"]==0, counts
                    try:
                        urlopen(url+"api/state",timeout=.3)
                    except URLError as error:
                        assert isinstance(error.reason,ConnectionRefusedError)
                    else:
                        raise AssertionError("Auto launcher left its loopback server running")
                    checks.append("one delegated request per normal turn; repeat synthesis/gain absent; browser/live IO=0; all children reaped")
                finally:
                    browser.close()
        finally:
            if process.poll() is None:
                try:
                    process.communicate("/quit\n",timeout=3)
                except (subprocess.TimeoutExpired,BrokenPipeError,ValueError):
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill();process.communicate()
    after=hashlib.sha256(face_path.read_bytes()).hexdigest()
    assert before==after
    return {"status":"PASS","face_sha256_before":before,"face_sha256_after":after,
            "baseline_checks":baseline["checks"],"auto_checks":checks,"counts":counts,
            "fixture_note":"150ms synthetic observation holds; no latency or semantic-quality measurement",
            "not_run":["live schema support/semantic quality","real audio/Piper/microphone","Pi LCD/physical controls"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--face",default=str(Path.home()/"Downloads/BMO_Screen_v13_2.html"))
    parser.add_argument("--chromium",default="/usr/bin/chromium")
    args=parser.parse_args()
    print(json.dumps(run(args.face,args.chromium),indent=2))


if __name__=="__main__":
    main()
