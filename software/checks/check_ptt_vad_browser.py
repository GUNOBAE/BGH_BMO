#!/usr/bin/env python3
"""Real product browser + synthetic PCM/one-shot STT; no physical devices/APIs."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks.check_desktop_app_browser import DesktopFixture, FIXTURE
from checks import check_desktop_app_browser as desktop
from bmo_ai.screen_profiles import SCREEN_PROFILES


def run_focus_regression(profile="ci-synthetic", chromium="/usr/bin/chromium"):
    """Product routing with native keys; only cache/PCM/provider IO is injected."""
    from playwright.sync_api import sync_playwright
    face=FIXTURE if profile=="ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    # A genuinely missing optional cache stays in readiness, without covering the face.
    child=desktop.CHILD.replace("if scenario=='greeting_missing':",
        "if scenario in ('greeting_missing','ptt_slow'):")
    assert child!=desktop.CHILD
    anchor="            counts['brain']+=1;time.sleep(.15)"
    assert child.count(anchor)==1
    child=child.replace(anchor,anchor+"""
            deadline=time.monotonic()+8
            while (root/'hold-provider').exists():
                if time.monotonic()>deadline:raise AssertionError('Provider test gate expired')
                time.sleep(.01)""")
    child=child.replace("brain_instances=1)",
        "brain_instances=1,game_inputs=list(getattr(host.games._runner,'inputs',())))")
    # Delay one synthetic frame beyond the product's .25s freshness bound.
    # Its pre-pacing timestamp would be rejected; the fixture must stamp the
    # actual delivery. This exercises native input rather than weakening VAD.
    anchor="                    if scenario=='ptt_slow':time.sleep(.03)"
    assert child.count(anchor)==1
    child=child.replace(anchor,"""                    if scenario=='ptt_slow':
                        if counts['mic_sources']==1 and frame.sequence==7:
                            time.sleep(.30)
                            counts['synthetic_delayed_frame_age']=clock()-frame.timestamp
                            counts['synthetic_pacing_delays']=1
                        else:time.sleep(.03)""")
    checks=[]
    with tempfile.TemporaryDirectory(prefix="bmo-ptt-focus-") as directory:
        with patch.object(desktop,"CHILD",child):
            fixture=DesktopFixture(directory,profile=profile,face=face,scenario="ptt_slow")
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=chromium,headless=True,
                    env={"PATH":os.defpath,"HOME":directory,"LC_ALL":"C.UTF-8"},
                    args=["--no-sandbox","--disable-gpu","--mute-audio","--disable-audio-output",
                          "--disable-background-networking","--disable-component-update","--disable-sync","--no-first-run"])
                try:
                    page=browser.new_page(viewport={"width":800,"height":480});page.set_default_timeout(8000)
                    page.route("**/*",lambda r:r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                    def wait(predicate):
                        deadline=time.monotonic()+8
                        while time.monotonic()<deadline:
                            value=fixture.features()
                            if predicate(value):return value
                            page.wait_for_timeout(20)
                        raise AssertionError('Focus regression did not settle: '+json.dumps({
                            'mode':value['mode'],'phase':value['phase'],'ptt':value['ptt'],
                            'audio_owner':value['audio_owner'],'last_result':value['last_result'],
                            'requests':value['requests'][-3:],'audit':value['test_fixture'],
                            'browser':page.evaluate("()=>({input:BMODesktop.inputEvidence,active:document.activeElement.id,childActive:document.querySelector('#face').contentDocument.activeElement.tagName})")}))
                    def press_enter():
                        page.keyboard.down("Enter");page.keyboard.up("Enter")
                    def finish_turn(count):
                        result=wait(lambda s:not s['conversation_busy'] and s['audio_owner'] is None
                            and s['test_fixture']['mic_closed']==count)
                        page.wait_for_function("count=>BMODesktop.features?.conversation_busy===false&&BMODesktop.features.audio_owner===null&&BMODesktop.features.test_fixture.mic_closed===count",arg=count)
                        assert result['test_fixture']['stt']==result['test_fixture']['brain']==result['test_fixture']['synthesis']==count,{
                            'expected_completed_turns':count,'audit':result['test_fixture'],
                            'ptt':result['ptt'],'last_result':result['last_result'],
                            'requests':result['requests'][-3:],
                            'browser':page.evaluate("()=>({input:BMODesktop.inputEvidence,active:document.activeElement.id,childActive:document.querySelector('#face').contentDocument.activeElement.tagName})")}
                        assert not page.locator("#menu").evaluate("e=>e.open")
                        return result
                    page.goto(fixture.url,wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMODesktop.state.code==='greeting_missing'&&BMODesktop.features?.ptt?.enabled")
                    assert not page.locator("#notice").is_visible()
                    page.locator("#face").click(position={"x":400,"y":230})
                    page.wait_for_function("()=>document.querySelector('#menu').open")
                    page.locator("#settings-menu").click()
                    page.locator("#system-menu").click()
                    page.wait_for_function("()=>document.querySelector('#system-dialog').open")
                    page.locator("#greeting-retry").focus()
                    with page.expect_request(lambda request:request.url.endswith('/api/desktop')
                            and request.method=='POST' and request.post_data_json['payload']['action']=='retry_greeting'):
                        press_enter()
                    page.wait_for_function("()=>BMODesktop.state?.greeting_attempts===2&&BMODesktop.state.phase==='ready'&&BMODesktop.state.code==='greeting_missing'")
                    assert page.locator("#system-dialog").evaluate("e=>e.open")
                    assert fixture.features()['test_fixture']['mic_sources']==0
                    page.locator("#system-dialog .dialog-close").focus();press_enter()
                    page.wait_for_function("()=>!document.querySelector('#system-dialog').open&&document.activeElement.id==='face'")
                    assert not page.locator("#notice").is_visible()
                    checks.append("missing optional greeting leaves the face clear; readiness retry preserves native menu Enter without a dialogue PTT")

                    page.locator("#face-volume-trigger").wait_for(state="visible")
                    page.locator("#face-volume-trigger").focus()
                    assert page.evaluate("()=>document.activeElement.id")=="face-volume-trigger",page.evaluate("()=>document.activeElement.outerHTML")
                    press_enter()
                    audit=wait(lambda s:s['test_fixture']['mic_sources']>=1 or page.locator('#menu').evaluate('e=>e.open'))['test_fixture']
                    assert audit["mic_sources"]==1 and not page.locator("#menu").evaluate("e=>e.open"),{
                        "scenario":"face_control_residual_focus","mic_sources":audit["mic_sources"],
                        "menu_open":page.locator("#menu").evaluate("e=>e.open"),
                        "evidence":page.evaluate("()=>BMODesktop.inputEvidence")}
                    finish_turn(1)
                    assert not page.locator("#face-volume").is_visible()
                    checks.append("native Enter down/up from residual face-control focus starts one PTT without opening a menu or volume pane")

                    # Native Enter activation must still work INSIDE a menu.
                    # Closing it must not leave the opener's button as a trap.
                    page.locator("#face").click(position={"x":400,"y":230})
                    page.wait_for_function("()=>document.querySelector('#menu').open")
                    page.locator("#resume").focus();press_enter()
                    page.wait_for_function("()=>!document.querySelector('#menu').open&&document.activeElement.id==='face'")
                    assert fixture.features()['test_fixture']['mic_sources']==1
                    press_enter();finish_turn(2)
                    checks.append("menu resume uses its native Enter activation; keyup does not start PTT, focus returns to face, next Enter starts PTT without reopening menu")

                    # A held key must never briefly open then activate Talk in
                    # that new menu; a final mic count alone could hide this.
                    page.evaluate("""()=>{window.__focusMenuOpens=0;new MutationObserver(rows=>{
                        for(const row of rows)if(row.oldValue===null)window.__focusMenuOpens++;
                    }).observe(document.querySelector('#menu'),{attributes:true,attributeFilter:['open'],attributeOldValue:true});}""")
                    page.locator("#face-volume-trigger").wait_for(state="visible")
                    page.locator("#face-volume-trigger").focus()
                    page.keyboard.down("Enter");page.keyboard.down("Enter");page.keyboard.up("Enter")
                    finish_turn(3)
                    assert page.evaluate("()=>window.__focusMenuOpens")==0
                    assert page.evaluate("()=>BMODesktop.inputEvidence.repeats_ignored")>=1
                    checks.append("held native Enter/repeat on residual face-control focus opens zero menus and admits exactly one PTT")

                    page.locator("#face").focus();press_enter();finish_turn(4)
                    checks.append("same-origin face iframe native Enter down/up still reaches the single PTT owner")

                    page.locator('#face').click(position={'x':400,'y':230});page.locator('#conversation-menu').click();page.locator('#talk').focus();press_enter()
                    page.wait_for_function("()=>!document.querySelector('#menu').open&&document.activeElement.id==='face'")
                    finish_turn(5)
                    checks.append("native menu Talk Enter starts exactly one PTT; menu-close face focus restoration does not cancel that capture")

                    # Test native editing on the real product page, without
                    # exporting or replacing its private key() implementation.
                    page.evaluate("""()=>{const box=document.createElement('section');box.id='focus-editors';
                        box.style.cssText='position:fixed;top:0;left:0;z-index:20;background:white;color:black';
                        box.innerHTML='<input id="focus-input"><textarea id="focus-textarea"></textarea><div id="focus-editable" contenteditable="true"><span tabindex="0">editable text</span></div>';
                        document.body.append(box);}""")
                    for selector in ('#focus-input','#focus-textarea'):
                        page.locator(selector).focus();page.keyboard.type('typed');press_enter()
                    assert page.locator('#focus-input').input_value()=='typed'
                    assert page.locator('#focus-textarea').input_value()=='typed\n'
                    page.locator('#focus-editable span').first.focus();press_enter()
                    page.wait_for_timeout(200)
                    assert fixture.features()['test_fixture']['mic_sources']==5
                    assert not page.locator('#menu').evaluate('e=>e.open')
                    page.locator('#focus-editors').evaluate('e=>e.remove()')
                    checks.append("input/textarea and nested contenteditable retain native Enter editing without PTT")

                    page.locator('#face').click(position={'x':400,'y':230});page.locator('#games').click()
                    page.wait_for_function("()=>document.querySelector('#game-menu').open&&document.querySelector('#game-list button:not(:disabled)')")
                    page.locator('#game-list button:not(:disabled)').first.focus();press_enter()
                    wait(lambda s:s['mode']=='game')
                    page.wait_for_function("()=>!document.querySelector('#game-menu').open&&BMODesktop.features?.mode==='game'")
                    page.locator('#face').focus();press_enter()
                    game=wait(lambda s:['START',True] in s['test_fixture']['game_inputs'] and ['START',False] in s['test_fixture']['game_inputs'])
                    assert game['test_fixture']['mic_sources']==5
                    page.keyboard.press('Escape');wait(lambda s:s['mode']=='game_menu')
                    page.wait_for_function("()=>document.querySelector('#game-menu').open&&BMODesktop.features?.mode==='game_menu'")
                    page.locator('#game-back').focus();press_enter()
                    wait(lambda s:s['mode']=='conversation')
                    page.wait_for_function("()=>!document.querySelector('#game-menu').open&&document.activeElement.id==='face'")
                    assert fixture.features()['test_fixture']['mic_sources']==5
                    checks.append("open game menu native Enter launches a fixture game; gameplay Enter sends START down/up with zero PTT; game return restores face focus")

                    hold=Path(directory)/'hold-provider';hold.write_text('synthetic provider wait')
                    page.locator('#face-volume-trigger').wait_for(state='visible')
                    page.locator('#face-volume-trigger').focus();press_enter()
                    wait(lambda s:s['phase']=='thinking' and s['test_fixture']['brain']==6)
                    # Simulate the exact residual focus during thinking too.
                    page.locator('#face-volume-trigger').focus()
                    assert fixture.features()['phase']=='thinking'
                    press_enter()
                    wait(lambda s:s['phase']=='idle')
                    hold.unlink()
                    cancelled=wait(lambda s:not s['conversation_busy'])
                    assert cancelled['test_fixture']['mic_sources']==cancelled['test_fixture']['mic_closed']==6
                    assert cancelled['test_fixture']['synthesis']==5
                    assert not page.locator('#menu').evaluate('e=>e.open')
                    checks.append("thinking GP8/Enter from residual face-control focus cancels the actual pending provider without a menu or late playback")

                    code,counts,error=fixture.finish()
                    assert code==0,error
                    assert counts['forbidden']==0 and counts['mic_sources']==counts['mic_closed']==6
                    assert counts['synthetic_pacing_delays']==1
                    assert counts['synthetic_delayed_frame_age']>=.25
                    checks.append("one paced synthetic frame exceeds the old timestamp freshness limit but is stamped at actual delivery; all six native PTT admissions preserve production PCM validation")
                    assert counts['active_mics']==0 and counts['max_mics']==counts['max_players']==1
                    assert counts['players']==counts['reaped'] and counts['browsers']==counts['browser_reaped']==1
                    return {"status":"PASS","checks":checks,"counts":counts,
                        "physical_gp8_identity":"NOT_RUN","cache":"PRIVATE_SYNTHETIC_MISSING",
                        "native_os_focus_loss":"NOT_RUN_HEADLESS_DOCUMENT_FOCUS_ALWAYS_TRUE",
                        "input":"NATIVE_CHROMIUM_KEYDOWN_KEYUP"}
                finally:browser.close()
        finally:fixture.close()


def run_capture_boundaries(profile="ci-synthetic", chromium="/usr/bin/chromium"):
    """Native UI → product recorder: complete long input, hard cap, next PTT.

    Only synthetic PCM delivery is accelerated. This checks byte bounds and
    recovery, never claims hardware duration/latency or pronunciation accuracy.
    """
    from playwright.sync_api import sync_playwright
    path=FIXTURE if profile=="ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    child=desktop.CHILD
    anchor="                if scenario=='ptt_webrtc':"
    assert child.count(anchor)==1
    child=child.replace(anchor,"""                if scenario=='ptt_long_boundaries':
                    import struct
                    from bmo_ai.features.input.microphone import PcmFrame
                    if (root/'capture-cap').exists():patterns=((10000,760),)
                    elif (root/'capture-short').exists():patterns=((10000,8),(0,24))
                    else:patterns=((10000,200),(0,15),(12000,200),(0,24))
                    sequence=0
                    for amplitude,count in patterns:
                        for _ in range(count):
                            if kwargs.get('cancel_check'):kwargs['cancel_check']()
                            yield PcmFrame(struct.pack('<h',amplitude)*640,sequence,clock(),kwargs['generation'])
                            sequence+=1
                    return
"""+anchor)
    anchor="                assert source.getframerate()==16000 and source.getnchannels()==1 and source.getsampwidth()==2"
    assert child.count(anchor)==1
    child=child.replace(anchor,anchor+"\n                counts['last_stt_bytes']=source.getnframes()*2")
    with tempfile.TemporaryDirectory(prefix='bmo-long-input-browser-') as directory:
        with patch.object(desktop,'CHILD',child):
            fixture=DesktopFixture(directory,profile=profile,face=path,scenario='ptt_long_boundaries',
                extra_args=('--ptt-trace',))
        try:
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
                        raise AssertionError('Capture boundary did not settle: '+json.dumps({
                            'ptt':state.get('ptt'),'trace':state.get('turn_diagnostics'),
                            'counts':state.get('test_fixture')}))
                    def press():
                        page.locator('#face').focus();page.keyboard.down('Enter');page.keyboard.up('Enter')
                    page.locator('#face').click(position={'x':400,'y':230})
                    page.locator('#conversation-menu').click()
                    page.locator('#capture-limit').wait_for(state='visible')
                    assert 'up to 30 seconds' in page.locator('#capture-limit').inner_text()
                    assert fixture.features()['ptt']['max_seconds']==30
                    page.locator('#conversation-dialog .dialog-close').click()
                    page.wait_for_function("()=>!document.querySelector('dialog[open]')")
                    press()
                    long=wait(lambda s:s['test_fixture']['stt']==1 and not s['conversation_busy'])
                    assert long['turn_diagnostics']['terminal']=='ok'
                    assert long['test_fixture']['last_stt_bytes']>384000
                    assert long['test_fixture']['brain']==long['test_fixture']['synthesis']==1
                    assert long['audio_owner'] is None and not long['ptt']['microphone_open']
                    (Path(directory)/'capture-cap').write_text('synthetic 30-second PCM bound')
                    press()
                    limited=wait(lambda s:s['test_fixture']['mic_closed']==2 and not s['conversation_busy'])
                    assert limited['turn_diagnostics']['terminal']=='capture_limit_reached'
                    assert limited['ptt']['captured_bytes']==960000
                    assert limited['test_fixture']['stt']==limited['test_fixture']['brain']==limited['test_fixture']['synthesis']==1
                    page.wait_for_function("()=>BMODesktop.features?.turn_diagnostics?.terminal==='capture_limit_reached'")
                    page.locator('#ptt-state').wait_for(state='visible')
                    assert 'reached the recording limit' in page.locator('#ptt-state').inner_text()
                    assert limited['audio_owner'] is None and not limited['ptt']['microphone_open']
                    (Path(directory)/'capture-cap').unlink()
                    (Path(directory)/'capture-short').write_text('synthetic short input')
                    # Thirty seconds of PCM were accelerated above; let the
                    # independent .25s physical-button debounce window expire.
                    page.wait_for_timeout(300)
                    press()
                    recovered=wait(lambda s:s['test_fixture']['mic_closed']==3 and not s['conversation_busy'])
                    assert recovered['turn_diagnostics']['terminal']=='ok'
                    assert recovered['test_fixture']['stt']==recovered['test_fixture']['brain']==recovered['test_fixture']['synthesis']==2
                    assert recovered['test_fixture']['last_stt_bytes']<64000
                    page.wait_for_function("()=>BMODesktop.features?.turn_diagnostics?.terminal==='ok'&&document.querySelector('#ptt-state').hidden")
                    code,counts,error=fixture.finish();assert code==0,error
                    assert counts['forbidden']==0 and counts['mic_sources']==counts['mic_closed']==3
                    assert counts['max_players']==counts['max_mics']==1 and counts['active_mics']==0
                    return dict(status='PASS',capture_limit_seconds=30,counts=counts,
                        checks=['menu shows current 30-second source/coordinator bound',
                            'native Enter sends >12s complete synthetic question through one STT/brain/voice',
                            '30s PCM cap produces visible fixed retry message and zero extra STT/brain/voice',
                            'next native Enter short question restores normal face/input/audio ownership'],
                        physical_capture='NOT_RUN',latency='NOT_MEASURED_ACCELERATED_PCM')
                finally:browser.close()
        finally:fixture.close()


def run(profile="ci-synthetic", chromium="/usr/bin/chromium", vad_mode="rms"):
    from playwright.sync_api import sync_playwright
    path = FIXTURE if profile == "ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if profile != "ci-synthetic": assert before == SCREEN_PROFILES[profile]["sha256"]
    checks = []
    with tempfile.TemporaryDirectory(prefix="bmo-ptt-browser-") as directory:
        assert vad_mode in ("rms", "webrtc")
        args = ("--ptt-trace",)
        if vad_mode == "webrtc":
            args += ("--character-life", "--ptt-vad-mode", "webrtc",
                     "--voice-bank-directory", str(Path(directory)/"character-bank"))
        fixture = DesktopFixture(directory, profile=profile, face=path,
                                 scenario="ptt_webrtc" if vad_mode == "webrtc" else "ptt_slow",
                                 extra_args=args)
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium, headless=True,
                    env={"PATH": os.defpath, "HOME": directory, "LC_ALL": "C.UTF-8"},
                    args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output",
                          "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
                try:
                    page = browser.new_page(viewport={"width": 800, "height": 480})
                    page.set_default_timeout(8000)
                    def wait_features(predicate, timeout=8):
                        # wait_for_function treats an async predicate's Promise
                        # as truthy; explicitly poll completed HTTP snapshots.
                        deadline=time.monotonic()+timeout
                        while time.monotonic()<deadline:
                            value=fixture.features()
                            if predicate(value):return value
                            page.wait_for_timeout(25)
                        raise AssertionError('PTT fixture did not reach expected phase: '+json.dumps({
                            'busy':value.get('conversation_busy'),'ptt':value.get('ptt'),
                            'last_result':value.get('last_result'),'requests':value.get('requests',[])[-3:],
                            'browser':page.evaluate("()=>({input:BMODesktop.inputEvidence,mode:BMODesktop.features?.mode,desktop:BMODesktop.state?.phase,active:document.activeElement.id,dialogs:[...document.querySelectorAll('dialog[open]')].map(e=>e.id)})")}))
                    page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                    page.add_init_script("""window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){
                        window.__audioCalls++;throw Error('Browser audio forbidden')};""")
                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                    audit = fixture.features()["test_fixture"]
                    assert audit["mic_sources"] == audit["stt"] == audit["brain"] == 0
                    assert fixture.state()["greeting_attempts"] == 1
                    assert audit["greeting_calls"] == (0 if vad_mode == "webrtc" else 1)
                    checks.append("startup greeting is cache-only; idle opens zero PCM sources/STT/brain")

                    # Focus the unchanged face iframe; all input belongs to the parent adapter.
                    page.locator("#face").focus()
                    page.keyboard.down("Enter")
                    page.keyboard.down("Enter")  # Browser repeat must not create a second turn.
                    wait_features(lambda s:s['ptt']['microphone_open'] and s['phase']=='listening')
                    # Duplicate parent/iframe edges arrive at one parent latch.
                    page.evaluate("""()=>window.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter'}))""")
                    page.keyboard.up("Enter")
                    wait_features(lambda s:not s['conversation_busy'] and s['ptt']['reason_code']=='transcribed')
                    audit = fixture.features()["test_fixture"]
                    assert (audit["mic_sources"], audit["mic_closed"], audit["stt"], audit["brain"], audit["synthesis"]) == (1,1,1,1,1), audit
                    assert fixture.features()["emotion"] == "crying"
                    assert fixture.features()["audio_owner"] is None
                    assert not page.locator("#ptt-state").is_visible()
                    checks.append("iframe GP8/keyboard Enter + repeat/duplicate edge -> one VAD capture/STT/brain/voice; emotion persists")

                    evidence = page.evaluate("()=>({...BMODesktop.inputEvidence})")
                    assert evidence["physical_identity"] == "UNVERIFIED"
                    assert evidence["request_sent"] == evidence["request_accepted"] == 1
                    assert evidence["request_rejected"] == 0 and evidence["repeats_ignored"] >= 1
                    assert all(type(value) is int and 0 <= value <= 999999 for name, value in evidence.items()
                               if name != "physical_identity")
                    checks.append("bounded input evidence confirms one accepted request despite repeat/duplicate iframe edges; physical HID identity stays UNVERIFIED")

                    page.wait_for_function("()=>BMODesktop.features?.turn_diagnostics?.terminal==='ok'")
                    assert not page.locator("#system-dialog").is_visible()
                    page.evaluate("()=>BMODesktop.menu()")
                    page.locator("#settings-menu").click()
                    page.locator("#system-menu").click()
                    page.wait_for_function("()=>document.querySelector('#system-dialog').open")
                    diagnostic = json.loads(page.locator("#turn-diagnostics").inner_text())
                    assert diagnostic["enabled"] is True
                    assert diagnostic["contains_audio"] is diagnostic["contains_transcript"] is False
                    assert diagnostic["failure"] is None and 0 < len(diagnostic["events"]) <= 96
                    stages={event["stage"] for event in diagnostic["events"]}
                    assert {"microphone", "vad", "stt", "ai", "playback"} <= stages
                    # This fixture synthesizes locally. An unconditional Fish
                    # start marker used to fabricate a provider stage; preserve
                    # the product fix and verify the actual route in the UI.
                    assert "fish" not in stages
                    assert diagnostic['playback_path']=='synthetic'
                    assert not any(name.startswith('tts_') for name in diagnostic['markers_ms'])
                    assert diagnostic['timings']['tts_to_first_byte_ms']==dict(status='UNMEASURED',value_ms=None)
                    assert diagnostic['timings']['first_byte_to_player_ms']==dict(status='UNMEASURED',value_ms=None)
                    panel = page.locator("#system-dialog").inner_text()
                    for private in ("Synthetic button question.", "A synthetic desktop response.",
                                    "Private provider fixture detail", "OPENAI_API_KEY", "FISH_API_KEY", directory):
                        assert private not in panel
                    assert fixture.request("/api/control", payload={"action": "ptt", "value": None},
                                           headers={"X-BMO-Token": "INVALID_TEST_TOKEN"})[0] == 403
                    assert fixture.features()["test_fixture"]["mic_sources"] == 1
                    page.locator("#system-dialog .dialog-close").click()
                    checks.append("explicit --ptt-trace shows the actual synthetic playback route and only executed stages, without fabricated Fish markers/timings or transcript/PCM/keys/private paths; invalid token opens zero sources")

                    # Repeat only the retained gained response, never input/brain/synthesis.
                    page.evaluate("()=>BMODesktop.control('repeat')")
                    wait_features(lambda s:s['conversation_busy'])
                    wait_features(lambda s:not s['conversation_busy'])
                    audit = fixture.features()["test_fixture"]
                    assert (audit["mic_sources"],audit["stt"],audit["brain"],audit["synthesis"]) == (1,1,1,1)
                    assert fixture.features()["emotion"] == "crying"
                    checks.append("repeat retains the previous expression/audio and performs zero capture/STT/brain/synthesis")

                    page.evaluate("()=>BMODesktop.intent('open_game_menu')")
                    page.wait_for_function("()=>document.querySelector('#game-menu').open")
                    page.locator("#face").focus()
                    page.keyboard.press("Enter")
                    # Modal focus can legitimately stay on a native button.
                    # Also inject the parent adapter's explicit game-input
                    # edge so its evidence path is tested independently.
                    page.evaluate("""()=>{for(const type of ['keydown','keyup'])window.dispatchEvent(
                        new KeyboardEvent(type,{key:'Enter',code:'Enter'}));}""")
                    page.wait_for_timeout(250)
                    assert fixture.features()["test_fixture"]["mic_sources"] == 1
                    game_evidence = page.evaluate("()=>({...BMODesktop.inputEvidence})")
                    assert game_evidence["blocked_mode"] >= 1
                    assert game_evidence["request_sent"] == game_evidence["request_accepted"] == 1
                    page.evaluate("()=>BMODesktop.intent('return_to_face')")
                    # Enter can start the selected game and close its menu
                    # before return_to_face reaches the owner. Observe the
                    # rendered conversation transition, not just closed DOM.
                    page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'&&!document.querySelector('dialog[open]')")
                    checks.append("game menu Enter stays game input and never starts PTT; face return preserves expression")
                    checks.append("game Enter increments only the blocked-mode input evidence and preserves the single admitted PTT request")

                    # New capture, then focus-loss cancellation before speech validation.
                    page.locator("#face").focus()
                    page.keyboard.press("Enter")
                    wait_features(lambda s:s['ptt']['microphone_open'])
                    page.evaluate("()=>window.dispatchEvent(new Event('blur'))")
                    wait_features(lambda s:not s['conversation_busy'] and not s['ptt']['microphone_open'])
                    audit = fixture.features()["test_fixture"]
                    assert (audit["mic_sources"],audit["mic_closed"],audit["stt"],audit["brain"]) == (2,2,1,1)
                    assert fixture.features()["emotion"] == "crying"
                    checks.append("focus loss cancels and closes active PCM source, with no late STT/answer or expression reset")

                    # Freeze the owner BEFORE admission. Polling an old idle
                    # snapshot must not disarm cancellation of queued PTT.
                    hold = Path(directory) / 'hold-host'
                    hold.write_text('synthetic owner-poll gate')
                    page.keyboard.press('Enter')
                    page.wait_for_timeout(350)
                    assert fixture.features()['conversation_busy'] is False
                    page.evaluate("()=>window.dispatchEvent(new Event('blur'))")
                    page.wait_for_timeout(100)
                    hold.unlink()
                    page.wait_for_timeout(250)
                    audit=fixture.features()['test_fixture']
                    assert audit['mic_sources']==2 and audit['stt']==1, audit
                    checks.append('queued Enter survives idle snapshots until blur cancellation retires it; zero source opens after owner resumes')

                    # A fake disconnected recorder is visible and has a final
                    # failed request receipt, without raw tool/provider detail.
                    (Path(directory)/'fail-mic').write_text('synthetic missing input')
                    page.keyboard.press('Enter')
                    wait_features(lambda s:not s['conversation_busy'] and s['ptt']['state']=='failed')
                    # Host completion can precede the browser's next 100ms
                    # snapshot. Bind the visible failure check to that state.
                    page.wait_for_function("()=>BMODesktop.features?.ptt?.state==='failed'")
                    page.locator('#ptt-state').wait_for(state='visible')
                    message = page.locator('#ptt-state').inner_text()
                    assert '녹음을 완료하지 못했어요' in message, json.dumps({
                        'message': message,
                        'visible_diagnosis': page.evaluate('()=>BMODesktop.features?.turn_diagnostics'),
                        'latest_diagnosis': fixture.features().get('turn_diagnostics')}, ensure_ascii=False)
                    assert fixture.features()['last_result']['code']=='voice_failed'
                    audit=fixture.features()['test_fixture']
                    assert (audit['mic_sources'],audit['mic_closed'],audit['stt'],audit['brain'])==(3,3,1,1)
                    checks.append('fake missing microphone gives fixed external error plus terminal failed receipt; no STT/brain/output')

                    # Reload never greets/records; physical USB mapping is still unverified.
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='ready'")
                    page.wait_for_timeout(200)
                    audit = fixture.features()["test_fixture"]
                    assert fixture.state()["greeting_attempts"] == 1 and audit["mic_sources"] == 3
                    assert audit["greeting_calls"] == (0 if vad_mode == "webrtc" else 1)
                    assert fixture.features()["wake_word"]["enabled"] is False
                    assert fixture.features()["ptt"]["physical_verified"] is False
                    assert page.evaluate("()=>window.__audioCalls") == 0
                    checks.append("reload/wake-disabled preserve one greeting and zero automatic capture; browser audio remains forbidden")
                    code, counts, error = fixture.finish()
                    assert code == 0, error
                    assert counts["players"] == counts["reaped"]
                    assert counts["browsers"] == counts["browser_reaped"] == 1
                    assert counts["max_players"] == counts["max_mics"] == 1
                    assert counts["mic_sources"] == counts["mic_closed"] == 3 and counts["active_mics"] == 0
                    assert counts["forbidden"] == 0
                    checks.append("product exit reaps all owned synthetic players/browser and closes all PCM sources")
                finally: browser.close()
        finally: fixture.close()
    focus_regression=run_focus_regression(profile,chromium)
    capture_boundaries=run_capture_boundaries(profile,chromium) if vad_mode=="rms" else None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    return dict(status="PASS",screen_profile=profile,vad_mode=vad_mode,checks=checks,counts=counts,
        focus_regression=focus_regression,capture_boundaries=capture_boundaries,
        sha256_before=before,sha256_after=before,
        not_run=["USB GP8 identity/firmware", "actual microphone/VAD tuning", "paid STT/GPT/Fish", "physical devices"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--screen-profile",choices=("ci-synthetic","r10","v13.2"),default="ci-synthetic")
    p.add_argument("--chromium",nargs="?",const=None,default="/usr/bin/chromium")
    p.add_argument("--vad-mode",choices=("rms","webrtc"),default="rms")
    args=p.parse_args()
    print(json.dumps(run(args.screen_profile,args.chromium,args.vad_mode),ensure_ascii=False,indent=2))
