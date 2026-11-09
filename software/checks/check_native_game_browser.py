#!/usr/bin/env python3
"""Product SuperTux admission/return with an owned synthetic process and pad.

The game binary, X11 window observation and physical pad are injected boundaries.
This check does not install/run SuperTux, observe a Pi screen or approve controls.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks import check_desktop_app_browser as desktop
from checks.check_full_batch_browser import replace_once
from checks.check_playable_games_browser import PAD_BOUNDARY


TOY = """import os,pathlib,sys,time
root=pathlib.Path(sys.argv[1])
(root/'native-pid').write_text(str(os.getpid()))
while not (root/'native-end').exists() and not (root/'native-crash').exists():time.sleep(.01)
raise SystemExit(7 if (root/'native-crash').exists() else 0)
"""


def child_source(readiness='ready'):
    source = replace_once(desktop.CHILD, "real_synthesis=IntegrationVoice._synthesize",
        "native_children=[]\nnative_command=[sys.executable,'-c',"+repr('exec('+repr(TOY)+')')+",str(root)]\n"
        "counts.update(native_spawns=0,native_reaped=0,native_pads_opened=0,native_pads_closed=0,native_window_probes=0)\n"
        "real_synthesis=IntegrationVoice._synthesize")
    source = replace_once(source, "def spawn(command,**options):", """def spawn(command,**options):
    if tuple(command)==tuple(native_command):
        assert options['start_new_session'] is True and options['close_fds'] is True
        assert options['env']=={'PATH':os.defpath,'LANG':'C.UTF-8'}
        process=real_popen(command,**options);native_children.append(process);counts['native_spawns']+=1
        return process
""")
    setup = r'''
    from bmo_ai.features.games.native import NativeGameRunner,NativeGamesFeature
    from bmo_ai.features.contracts import CatalogItem,FeatureError
    from bmo_ai.features.runtime import RuntimePolicy
    class Pad:
        def __init__(self):self.closed=False;counts['native_pads_opened']+=1
        def poll(self):
            if (root/'pad-disconnect').exists():raise FeatureError('native_pad_disconnected')
            pending=root/'native-pad.json'
            if not pending.exists():return ()
            events=json.loads(pending.read_text());pending.unlink()
            return tuple(tuple(event) for event in events)
        def close(self):
            if not self.closed:self.closed=True;counts['native_pads_closed']+=1
    def window(pid):
        counts['native_window_probes']+=1
        observed=root/'native-pid'
        return (root/'native-window').exists() and observed.exists() and observed.read_text()==str(pid)
    item=CatalogItem('supertux','SuperTux',READINESS,CODE,('native_supertux','native_pad_exit','silent_game'),False)
    def runner(_):
        return NativeGameRunner(command=native_command,environment={'PATH':os.defpath,'LANG':'C.UTF-8'},
            pad_factory=Pad,window_probe=window,exit_buttons=(3,7),clock=clock,startup_timeout=15.)
    native=NativeGamesFeature(item=item,runner_factory=runner if item.readiness=='READY' else None,clock=clock)
    settings.update(runtime=RuntimePolicy(simulate=False,conversation_ptt_microphone=True,native_supertux=True),
        native_supertux=True,native_game_feature=native,demo_assets=False,playable_games=True,usb_gamepad=True,
        voice=IntegrationVoice(settings['config'],client,simulate=True,emit=lambda *_a,**_kw:None))
'''
    codes = {'ready': ('READY', 'native_ready_to_launch'),
             'missing': ('RUNTIME_MISSING', 'native_supertux_not_installed'),
             'setup': ('UNVERIFIED', 'native_pad_profile_required')}
    level, code = codes[readiness]
    setup = setup.replace('READINESS', repr(level)).replace('CODE', repr(code))
    source = replace_once(source, '    host=FeatureHost(**settings,client=client);hosts.append(host)',
                          setup+'\n    host=FeatureHost(**settings,client=client);hosts.append(host)')
    return replace_once(source, "counts['last_code']=apps[-1].startup_snapshot()['code'] if apps else None", """for process in native_children:
    assert process.poll() is not None,'Native synthetic process survived exit'
    try:os.waitpid(process.pid,os.WNOHANG)
    except ChildProcessError:counts['native_reaped']+=1
    else:raise AssertionError('Native synthetic process was not reaped')
counts['last_code']=apps[-1].startup_snapshot()['code'] if apps else None""")


def publish(directory, name, value):
    staged = Path(directory)/(name+'.stage')
    staged.write_text(json.dumps(value))
    os.replace(staged, Path(directory)/name)


def run(chromium=None):
    from playwright.sync_api import sync_playwright
    before = hashlib.sha256(desktop.FIXTURE.read_bytes()).hexdigest()
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium, headless=True,
            env={'PATH':os.defpath,'LC_ALL':'C.UTF-8'},
            args=['--no-sandbox','--disable-gpu','--mute-audio','--disable-audio-output',
                  '--disable-background-networking','--disable-component-update','--no-first-run'])
        try:
            for readiness in ('missing', 'setup', 'ready'):
                with tempfile.TemporaryDirectory(prefix='bmo-native-browser-') as directory:
                    with patch.object(desktop, 'CHILD', child_source(readiness)):
                        fixture = desktop.DesktopFixture(directory, scenario='character',
                            extra_args=('--character-life','--voice-bank-directory',str(Path(directory)/'character-bank')))
                    try:
                        page = browser.new_page(viewport={'width':800,'height':480})
                        page.set_default_timeout(10000)
                        errors, actions = [], []
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        page.on('request',lambda request:actions.append(request.post_data_json['payload'])
                            if request.method=='POST' and request.url.endswith('/api/control') else None)
                        page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                        page.add_init_script(PAD_BOUNDARY)
                        page.goto(fixture.url,wait_until='domcontentloaded')
                        page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'&&BMODesktop.features")
                        def settle(expression):
                            try:page.wait_for_function('()=>'+expression)
                            except Exception as error:
                                current=fixture.features()
                                error.add_note(json.dumps(dict(expected=expression,errors=errors,
                                    host={key:current.get(key) for key in ('mode','games','audio_owner','last_result','requests','test_fixture')},
                                    browser=page.evaluate("()=>({mode:BMODesktop.features?.mode,native:BMODesktop.features?.games?.native,notice:document.querySelector('#notice').textContent})"))))
                                raise
                        def menu():
                            page.keyboard.press('Escape');page.locator('#games').click()
                            page.locator('#game-menu').wait_for(state='visible')
                        menu()
                        button=page.locator('[data-game-id=supertux]')
                        assert page.locator('#game-list button').count()==4
                        if readiness!='ready':
                            assert button.is_disabled()
                            label='Not installed' if readiness=='missing' else 'Setup needed'
                            assert label in button.inner_text()
                            assert fixture.features()['test_fixture']['native_spawns']==0
                            checks.append('SuperTux '+readiness+' remains disabled beside the unchanged three web games')
                        else:
                            assert not button.is_disabled() and 'Ready to launch' in button.inner_text()
                            assert 'without game audio' in page.locator('#native-game-status').inner_text()
                            previous=fixture.features()['test_fixture']
                            button.click()
                            settle("BMODesktop.features.mode==='game'&&BMODesktop.features.games.native.state==='starting'")
                            assert fixture.features()['games']['native']['window_detected'] is False
                            assert 'Waiting for its window' in page.locator('#foreground-status').inner_text()
                            assert page.evaluate('()=>BMOPlayableGames.snapshot()') is None
                            assert fixture.features()['test_fixture']['native_spawns']==previous['native_spawns']+1
                            # The actual child exists, but readiness remains false
                            # until the injected PID-owned window observation.
                            publish(directory,'native-window',True)
                            settle("BMODesktop.features.games.native.state==='running'")
                            assert fixture.features()['games']['native']['physical_input_verified'] is False
                            assert fixture.features()['games']['native']['audio_scope']=='silent_native'
                            start=len(actions)
                            polls=page.evaluate('()=>window.__padCalls')
                            page.evaluate("()=>{window.__padButtons=[0,8,9];window.dispatchEvent(new Event('blur'));window.dispatchEvent(new Event('gamepaddisconnected'));}")
                            page.keyboard.down('Enter');page.keyboard.press('ArrowLeft');page.keyboard.press('Escape')
                            page.evaluate("()=>BMODesktop.control('gamepad',{key:'PAD_LOST',pressed:true,sequence:999999,generation:BMODesktop.features.mode_generation})")
                            page.wait_for_timeout(300)
                            assert page.evaluate('()=>window.__padCalls')==polls
                            assert not [a for a in actions[start:] if a['action'] in ('gamepad','game_key','game_ready','game_event','input','ptt','cancel')]
                            assert fixture.features()['games']['native']['state']=='running'
                            # These indices belong only to this synthetic pad;
                            # the production profile must come from Pi observation.
                            publish(directory,'native-pad.json',[[3,True,False],[7,True,False]])
                            settle("BMODesktop.features.mode==='conversation'&&BMODesktop.features.games.native.state==='exited'&&BMODesktop.features.audio_owner===null")
                            page.keyboard.up('Enter')
                            assert fixture.features()['test_fixture']['brain']==previous['brain']
                            page.locator('#face').focus()
                            page.keyboard.down('Enter');page.keyboard.up('Enter')
                            settle('BMODesktop.features.test_fixture.brain==='+str(previous['brain']+1)+'&&!BMODesktop.features.conversation_busy&&BMODesktop.features.audio_owner===null')
                            assert fixture.features()['test_fixture']['native_pads_opened']==fixture.features()['test_fixture']['native_pads_closed']==1
                            checks.append('spawn waits for PID-owned window observation; native ownership suppresses browser pad/blur/held keys; observed fake chord exits and next real Enter starts one PTT')
                            # A second owned process crashes; resources must still
                            # return to the same face and a fresh conversation.
                            page.evaluate('()=>{window.__padButtons=[];}')
                            menu();page.locator('[data-game-id=supertux]').click()
                            settle("BMODesktop.features.games.native.state==='running'")
                            publish(directory,'native-crash',True)
                            settle("BMODesktop.features.mode==='conversation'&&BMODesktop.features.games.native.state==='failed'&&BMODesktop.features.audio_owner===null")
                            previous=fixture.features()['test_fixture']['brain']
                            page.locator('#face').focus();page.keyboard.press('Enter')
                            settle('BMODesktop.features.test_fixture.brain==='+str(previous+1)+'&&!BMODesktop.features.conversation_busy')
                            menu();assert page.locator('#native-game-status').get_attribute('data-state')=='failed'
                            assert fixture.features()['test_fixture']['native_pads_opened']==fixture.features()['test_fixture']['native_pads_closed']==2
                            checks.append('native crash preserves failed outcome, closes its pad/process/audio lease and restores face plus the next GP8 conversation')
                        assert not errors,errors
                        assert page.evaluate('()=>window.__audioCalls')==0
                        code,counts,error=fixture.finish()
                        assert code==0 and counts['forbidden']==0,error
                        assert counts['native_spawns']==counts['native_reaped']
                        page.close()
                    finally:fixture.close()
        finally:browser.close()
    assert hashlib.sha256(desktop.FIXTURE.read_bytes()).hexdigest()==before
    return dict(status='PASS',checks=checks,not_run=['SuperTux binary','Pi window/pad readiness','physical controller mapping','game audio'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--chromium',nargs='?',const=None,default='/usr/bin/chromium')
    print(json.dumps(run(parser.parse_args().chromium),indent=2))
