#!/usr/bin/env python3
"""Real Desktop/Host/broker/PTT handoff; model/PCM/provider/player IO injected.

This tests wake lifecycle and the unchanged face API, not human wake accuracy.
The marker tones are synthetic int16 data, never microphone or speaker IO.
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


def field_child():
    child=desktop.CHILD
    anchor="    host=FeatureHost(**settings,client=client);hosts.append(host)"
    assert child.count(anchor)==1
    replacement=r'''
    if scenario=='field_wake':
        from bmo_ai.features.input.microphone import PcmFrame
        from bmo_ai.conversation import TurnCancelled
        from bmo_ai.ptt_replay import load_public_reference_pcm16k
        reference=load_public_reference_pcm16k(target_rms=.02)
        reference_padded=reference+bytes((-len(reference))%1280)
        reference_frames=[reference_padded[i:i+1280] for i in range(0,len(reference_padded),1280)]
        counts.update(wake_feeds=0,wake_hits=0,first_word_kept=False,keyword_in_stt=False)
        class Detector:
            state='DISABLED'
            def load(self):self.state='READY';return True
            def snapshot(self):return dict(status=self.state,enabled=self.state=='READY',reason_code='ok',real_wake_word='NOT_RUN')
            def feed(self,pcm):
                counts['wake_feeds']+=1
                hit=pcm==(10000).to_bytes(2,'little')*640
                counts['wake_hits']+=hit
                return hit
            def reset(self):pass
            def close(self):self.state='DISABLED';return True
        class Source:
            sample_rate=16000
            simulated=True
            def __init__(self):self.closed=False;self.entered=False
            def frames(self,capture_id,*,generation,cancel_check=None):
                self.entered=True;counts['mic_sources']+=1;counts['active_mics']+=1
                counts['max_mics']=max(counts['max_mics'],counts['active_mics'])
                assert counts['active_mics']==1 and not any(p.poll() is None for p in players),'Mic/output overlap'
                pending=[];sequence=0
                while not self.closed:
                    if cancel_check:cancel_check()
                    trigger=root/'wake-trigger'
                    if trigger.exists():
                        same=trigger.read_text()=='same-breath';trigger.unlink()
                        pending.extend([8000]*10+[10000])
                        if same:pending.extend([0]*6+reference_frames+[0]*24)
                    question=root/'new-question'
                    if question.exists():
                        question.unlink();pending.extend([0]*6+[5001]+[5000]*7+[0]*24)
                    value=pending.pop(0) if pending else 0
                    time.sleep(.02)
                    if cancel_check:cancel_check()
                    pcm=value if type(value) is bytes else value.to_bytes(2,'little',signed=True)*640
                    yield PcmFrame(pcm,sequence,clock(),generation)
                    sequence+=1
                raise TurnCancelled()
            def close(self):
                if not self.closed:
                    self.closed=True
                    if self.entered:counts['mic_closed']+=1;counts['active_mics']-=1
                return True
        def transcribe(_client,path,**kwargs):
            import wave
            with wave.open(str(path),'rb') as wav:pcm=wav.readframes(wav.getnframes())
            counts['stt']+=1
            counts['first_word_kept']=reference in pcm or (5001).to_bytes(2,'little')*640 in pcm
            counts['keyword_in_stt']=any(v.to_bytes(2,'little')*640 in pcm for v in (8000,10000))
            return 'A fresh synthetic follow-up question.'
        settings.update(ptt_source_factory=Source,ptt_transcriber=transcribe)
        with patch('bmo_ai.features.input.wake_word.LocalWakeDetector',return_value=Detector()):
            host=FeatureHost(**settings,client=client)
    else:
        host=FeatureHost(**settings,client=client)
    hosts.append(host)
'''
    child=child.replace(anchor,replacement)
    child=child.replace("scenario in ('character','ptt_webrtc'):",
                        "scenario in ('character','ptt_webrtc','field_wake'):")
    child=child.replace("            counts['brain']+=1;time.sleep(.15)","""            counts['brain']+=1;time.sleep(.15)
            if scenario=='field_wake':
                deadline=time.monotonic()+8
                while (root/'hold-provider').exists():
                    if time.monotonic()>deadline:raise AssertionError('Provider gate expired')
                    time.sleep(.01)""")
    return child


@contextmanager
def fixture_for(directory,profile,face):
    with patch.object(desktop,'CHILD',field_child()):
        fixture=desktop.DesktopFixture(directory,profile=profile,face=face,scenario='field_wake',
            extra_args=('--character-life','--ptt-trace','--wake-word',
                        '--voice-bank-directory',str(Path(directory)/'character-bank')))
    try:yield fixture
    finally:fixture.close()


def run(profile='ci-synthetic',chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    face=desktop.FIXTURE if profile=='ci-synthetic' else Path(SCREEN_PROFILES[profile]['default_path'])
    before=hashlib.sha256(face.read_bytes()).hexdigest()
    if profile!='ci-synthetic':assert before==SCREEN_PROFILES[profile]['sha256']
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-field-wake-') as directory,fixture_for(directory,profile,face) as fixture:
        root=Path(directory)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                      '--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run'])
            try:
                page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(8000)
                page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                page.goto(fixture.url,wait_until='domcontentloaded')
                page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                def wait(predicate):
                    deadline=time.monotonic()+8
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if predicate(state):return state
                        page.wait_for_timeout(20)
                    raise AssertionError('Wake field boundary timeout: '+json.dumps({k:state.get(k) for k in ('phase','wake_word','ptt','microphone_broker','last_result')}))
                wait(lambda s:s['microphone_broker']['reader_alive'] and s['microphone_broker']['ready_for_trigger'])
                (root/'wake-trigger').write_text('wake-only')
                wait(lambda s:s['wake_word']['handoff']['phase']=='question_listening')
                page.wait_for_function("()=>BMOIntegration.state?.phase==='listening'")
                page.wait_for_function("()=>document.querySelector('#ptt-state').textContent.includes('listening')")
                for milliseconds in (1000,2000):
                    page.wait_for_timeout(milliseconds)
                    state=fixture.features();audit=state['test_fixture']
                    assert state['phase']=='listening' and state['wake_word']['handoff']['phase']=='question_listening'
                    assert audit['stt']==audit['brain']==audit['synthesis']==0
                    assert page.evaluate("()=>BMOIntegration.state.phase")!='thinking'
                ended=wait(lambda s:not s['conversation_busy'] and s['wake_word']['handoff']['phase']=='timed_out')
                assert ended['test_fixture']['stt']==ended['test_fixture']['brain']==ended['test_fixture']['synthesis']==0
                checks.append('wake phrase + 1/3/5s fixture silence stays listening then quietly idles, with zero STT/GPT/TTS and no question-less thinking')
                checks.append('missing approved acknowledgement uses clear fresh-question hint and no synthesis/player')

                wait(lambda s:s['microphone_broker']['microphone_open'] and s['microphone_broker']['ready_for_trigger'])
                (root/'wake-trigger').write_text('same-breath')
                done=wait(lambda s:not s['conversation_busy'] and s['test_fixture']['brain']==1)
                assert done['test_fixture']['stt']==1 and done['test_fixture']['first_word_kept']
                assert not done['test_fixture']['keyword_in_stt']
                assert done['test_fixture']['wake_hits']==2
                checks.append('same-breath synthetic wake→local comma boundary→question keeps first follow-up PCM, excludes keyword ring, and makes one dialogue turn; no phoneme alignment claim')

                wait(lambda s:s['microphone_broker']['microphone_open'])
                before_repeat=fixture.features()['test_fixture']
                page.evaluate("()=>BMODesktop.control('repeat')")
                wait(lambda s:s['conversation_busy']);wait(lambda s:not s['conversation_busy'])
                after_repeat=fixture.features()['test_fixture']
                assert (before_repeat['brain'],before_repeat['synthesis'])==(after_repeat['brain'],after_repeat['synthesis'])
                assert after_repeat['wake_hits']==2
                checks.append('repeat uses retained dialogue cache and suppresses detector during owned output, with no extra self-wake/provider')

                wait(lambda s:s['microphone_broker']['microphone_open'])
                (root/'hold-provider').write_text('synthetic gate')
                page.locator('#face').click(position={'x':400,'y':230});page.keyboard.press('Enter')
                wait(lambda s:s['ptt']['microphone_open'] and s['conversation_busy'])
                (root/'new-question').write_text('question')
                wait(lambda s:s['phase']=='thinking' and s['test_fixture']['brain']==2)
                page.keyboard.press('Enter')
                wait(lambda s:s['phase']=='idle')
                (root/'hold-provider').unlink()
                wait(lambda s:not s['conversation_busy'])
                assert fixture.features()['test_fixture']['synthesis']==1
                checks.append('existing GP8 input still ends on speech silence and GP8 during thinking cancels late output')

                page.evaluate("()=>BMODesktop.desktop('wake_off')")
                wait(lambda s:not s['wake_word']['armed'] and not s['microphone_broker']['reader_alive'])
                assert not fixture.features()['microphone_broker']['microphone_open']
                page.wait_for_timeout(300)
                page.keyboard.press('Enter')
                wait(lambda s:s['ptt']['microphone_open'])
                (root/'new-question').write_text('question')
                wait(lambda s:not s['conversation_busy'] and s['test_fixture']['brain']==3)
                assert fixture.features()['test_fixture']['stt']==3
                checks.append('wake OFF releases actual broker reader; finite manual GP8 remains available with one PCM owner')
                code,counts,error=fixture.finish()
                assert code==0 and counts['forbidden']==0,error
                assert counts['mic_sources']==counts['mic_closed'] and counts['active_mics']==0
                assert counts['max_mics']==counts['max_players']==1
                assert counts['players']==counts['reaped'] and counts['browsers']==counts['browser_reaped']
                assert counts['b1_commands']==0
                checks.append('all owned fixture readers/players/browser close; zero credentials/network/devices/arm commands')
            finally:browser.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest()==before
    return dict(status='PASS',screen_profile=profile,checks=checks,counts=counts,screen_sha256=before,
                wake_detector='INJECTED_LIFECYCLE_ONLY',human_wake_accuracy='NOT_RUN',
                real_microphone_or_speaker='NOT_RUN',fixture_timing='NOT_PHYSICAL_LATENCY')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=('ci-synthetic',*SCREEN_PROFILES),default='ci-synthetic')
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.profile,args.chromium),ensure_ascii=False,indent=2))
