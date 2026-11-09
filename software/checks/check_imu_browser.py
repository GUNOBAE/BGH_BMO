#!/usr/bin/env python3
"""Product Settings/Host IMU preference and diagnostic presentation, injected IO.

No real sensor, audio, physical button, paid provider or axis approval is used.
Diagnostic overlays test presentation only and never enter the sensor monitor.
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
from checks import check_desktop_app_browser as desktop


def child_source():
    anchor="    host=FeatureHost(**settings,client=client);hosts.append(host)"
    assert desktop.CHILD.count(anchor)==1
    return desktop.CHILD.replace(anchor,anchor+'''
    # Only the rendered diagnostics are replaced. This is not sensor ingest,
    # physical evidence, a profile or a route to motor/audio authorization.
    actual_imu_snapshot=host.snapshot
    def imu_presentation():
        value=actual_imu_snapshot()
        target=root/'imu-presentation.json'
        if target.exists():
            try:overlay=json.loads(target.read_text())
            except (OSError,ValueError):return value
            overlay.pop('enabled',None)
            value=dict(value,imu=dict(value['imu'],**overlay))
        return value
    host.snapshot=imu_presentation
''')


@contextmanager
def fixture_for(directory, preferences):
    with patch.object(desktop,'CHILD',child_source()):
        fixture=desktop.DesktopFixture(directory,scenario='character',extra_args=(
            '--character-life','--ptt-trace','--preferences-directory',str(preferences),
            '--voice-bank-directory',str(Path(directory)/'character-bank')))
    try:yield fixture
    finally:fixture.close()


def publish(root,value):
    staged=root/'imu-presentation.pending'
    staged.write_text(json.dumps(value))
    os.replace(staged,root/'imu-presentation.json')


def run(chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    checks=[]
    with tempfile.TemporaryDirectory(prefix='bmo-imu-ui-') as directory:
        preferences=Path(directory)/'preferences'
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={'PATH':os.defpath,'HOME':directory,'LC_ALL':'C.UTF-8'},
                args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                      '--disable-background-networking','--disable-component-update','--no-first-run'])
            try:
                for restart in (False,True):
                    # Each launch gets fresh synthetic voice-bank fixtures;
                    # only the real private preference store is shared.
                    root=Path(directory)/('restarted' if restart else 'initial')
                    root.mkdir(mode=0o700)
                    with fixture_for(root,preferences) as fixture:
                        page=browser.new_page(viewport={'width':800,'height':480});page.set_default_timeout(8000)
                        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                        page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                        def settled(expression):page.wait_for_function('()=>'+expression)
                        def settings():
                            page.locator('#face').click(position={'x':400,'y':180})
                            page.locator('#menu').wait_for(state='visible')
                            page.locator('#settings-menu').click()
                            page.locator('#settings-dialog').wait_for(state='visible')
                        page.goto(fixture.url,wait_until='domcontentloaded')
                        try:settled("BMODesktop.state?.phase==='ready'&&BMODesktop.features?.imu!==undefined")
                        except Exception as error:
                            error.add_note('IMU browser startup state: '+json.dumps(fixture.state()))
                            error.add_note('Browser errors: '+repr(errors))
                            raise
                        settings()
                        expected='true' if restart else 'false'
                        settled('BMODesktop.features.imu.enabled==='+expected)
                        assert page.locator('#imu-reactions').is_checked()==restart
                        if restart:
                            page.locator('#imu-reactions').uncheck()
                            settled('BMODesktop.features.imu.enabled===false&&!document.querySelector("#imu-reactions").disabled')
                            saved=json.loads((preferences/'preferences.json').read_text())
                            assert saved['settings']['imu_reactions'] is False
                            checks.append('same launcher restart reloads IMU ON as a preference; OFF persists without granting sensor validation')
                        else:
                            assert '가능한 기능:' not in page.locator('#imu-capability').inner_text()
                            with page.expect_request(lambda request:request.method=='POST' and request.url.endswith('/api/control') and request.post_data_json['payload']=={'action':'imu_reactions','value':True}):
                                page.locator('#imu-reactions').check()
                            settled('BMODesktop.features.imu.enabled===true&&!document.querySelector("#imu-reactions").disabled')
                            assert json.loads((preferences/'preferences.json').read_text())['settings']['imu_reactions'] is True
                            assert '가능한 기능:' not in page.locator('#imu-capability').inner_text()
                            checks.append('native Settings checkbox sends the real Host control, persists the preference and leaves unverified sensor capability unavailable')
                            # Even an inconsistent presentation flag cannot
                            # advertise a synthetic source as a real feature.
                            publish(root,dict(source='synthetic',health='healthy',reason_code='synthetic_checks_pass',
                                capabilities=dict(motion_reaction=True,gaze=False),axes={}))
                            settled("BMODesktop.features.imu.source==='synthetic'&&document.querySelector('#imu-status').textContent.includes('합성 데모')")
                            assert '실물 검증이 아닌 합성 검사' in page.locator('#imu-status').inner_text()
                            assert '가능한 기능:' not in page.locator('#imu-capability').inner_text()
                            publish(root,dict(source='real',health='degraded',reason_code='profile_required',
                                capabilities=dict(motion_reaction=False,gaze=False),
                                axes=dict(AX=dict(valid=False,reason='saturated'),AY=dict(valid=False,reason='bias'),
                                          AZ=dict(valid=True,reason='unverified'),GX=dict(valid=False,reason='fixed'))))
                            settled("BMODesktop.features.imu.source==='real'&&document.querySelector('#imu-status').textContent.includes('실제 센서')")
                            assert '실제 축 테스트와 반응 검수가 필요' in page.locator('#imu-status').inner_text()
                            assert page.locator('#imu-readiness').inner_text()==''
                            page.locator('#system-menu').click()
                            settled("document.querySelector('#imu-readiness').textContent.includes('saturated')")
                            diagnostic=json.loads(page.locator('#imu-readiness').inner_text())
                            assert diagnostic['axes']['AX']['valid'] is False
                            assert diagnostic['axes']['AY']['reason']=='bias'
                            assert not diagnostic['capabilities']['motion_reaction']
                            # Presentation-only capability boundary: a degraded
                            # overall sensor may have a separately verified
                            # relative-motion feature, with audio independently
                            # unavailable. This never changes the real monitor.
                            relative=dict(source='real',health='degraded',reason_code='relative_motion_validated',
                                capabilities=dict(motion_reaction=True,voice_reaction=False,gaze=False),
                                axes=diagnostic['axes'])
                            publish(root,relative)
                            settled("document.querySelector('#imu-capability').textContent.includes('표정으로 반응')")
                            assert '검증한 상대 움직임' in page.locator('#imu-status').inner_text()
                            relative['capabilities']['voice_reaction']=True
                            publish(root,relative)
                            settled("document.querySelector('#imu-capability').textContent.includes('저장 음성')")
                            assert '다른 소리가 재생 중이면 끼어들지 않아요' in page.locator('#imu-capability').inner_text()
                            assert page.locator('#imu-trial-label option').count()==6
                            assert page.locator('#imu-trial-start').is_enabled()
                            assert page.locator('#imu-trial-finish').is_disabled()
                            assert page.locator('#imu-trial-cancel').is_disabled()
                            with page.expect_request(lambda request:request.method=='POST' and request.url.endswith('/api/control') and request.post_data_json['payload']=={'action':'imu_trial','value':{'label':'fan','action':'start'}}):
                                page.locator('#imu-trial-label').select_option('fan')
                                page.locator('#imu-trial-start').click()
                            # This is a simulated app, not a fake real-sensor
                            # commissioning route. Only inspect the dispatched
                            # command; real trial capture is covered separately.
                            settled('!document.querySelector("#imu-trial-start").disabled')
                            assert fixture.features()['test_fixture']['b1_commands']==0
                            page.locator('#system-dialog .dialog-close').click()
                            settled('!document.querySelector("#system-dialog").open&&document.querySelector("#imu-readiness").textContent===""')
                            assert page.locator('#imu-readiness').inner_text()==''
                            checks.append('synthetic health stays a labeled demo; real degraded axes stay unverified, readiness exposes axis reasons and labeled capture controls without moving arms')
                            (root/'imu-presentation.json').unlink()
                            page.locator('#face').focus();page.keyboard.press('Enter')
                            deadline=time.monotonic()+8
                            while time.monotonic()<deadline:
                                state=fixture.features()
                                if state['test_fixture']['brain']==1 and not state['conversation_busy'] and state['audio_owner'] is None:break
                                page.wait_for_timeout(20)
                            else:raise AssertionError('Next synthetic conversation did not complete')
                            assert state['test_fixture']['stt']==state['test_fixture']['synthesis']==1
                            assert state['test_fixture']['b1_commands']==0
                            assert page.locator('#menu').is_hidden()
                            checks.append('leaving IMU readiness preserves one native Enter PTT and completes the next dialogue with released audio ownership')
                        assert not errors,errors
                        code,counts,error=fixture.finish()
                        assert code==0 and counts['forbidden']==0 and counts['players']==counts['reaped'],error
                        page.close()
                return dict(status='PASS',checks=checks,not_run=['real sensor capture','axis capability approval','Pi physical reactions','physical HID/arms','paid API'])
            finally:browser.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    args=parser.parse_args()
    print(json.dumps(run(args.chromium),ensure_ascii=False,indent=2))
