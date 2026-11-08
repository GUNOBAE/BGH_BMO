#!/usr/bin/env python3
"""Real Chromium visibility/freeze/disconnect boundaries with injected media IO.

Run under Xvfb in CI. No device, provider, private media or audio is used.
Observation during hidden/frozen intervals uses browser DOM only: HTTP GETs to
this product refresh its heartbeat and would invalidate the watchdog proof.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checks.check_v10_media_browser import fixture_for, desktop


def run(chromium=None):
    from playwright.sync_api import sync_playwright
    checks = []
    with tempfile.TemporaryDirectory(prefix='bmo-music-lifecycle-') as directory, fixture_for(
            directory, 'ci-synthetic', desktop.FIXTURE) as fixture, sync_playwright() as pw:
        env = {'PATH': os.defpath, 'HOME': directory, 'LC_ALL': 'C.UTF-8'}
        if os.environ.get('DISPLAY'):
            env['DISPLAY'] = os.environ['DISPLAY']
        profile = Path(directory)/'chromium-profile'
        profile.mkdir(mode=0o700)
        process = subprocess.Popen([chromium or pw.chromium.executable_path,
            '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
            '--user-data-dir='+str(profile), '--no-sandbox', '--disable-gpu',
            '--mute-audio', '--disable-audio-output', '--disable-background-networking',
            '--disable-component-update', '--disable-sync', '--no-first-run', 'about:blank'],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser = None
        try:
            deadline = time.monotonic()+10
            port_file = profile/'DevToolsActivePort'
            while not port_file.exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            port = int(port_file.read_text().splitlines()[0])
            # Playwright normally forces focus/visibility on every attached
            # page. Disable those overrides on the actual default context;
            # otherwise document.hidden stays false even for background tabs.
            browser = pw.chromium.connect_over_cdp('http://127.0.0.1:'+str(port), no_defaults=True)
            context = browser.contexts[0]
            page = context.pages[0]
            page.set_viewport_size({'width':800, 'height':480})
            page.set_default_timeout(12000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
            page.goto(fixture.url, wait_until='domcontentloaded')
            page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMODesktop.features?.mode==='conversation'")
            def start(variant):
                page.evaluate('()=>BMODesktop.menu()')
                page.locator('#music-menu').click()
                page.locator('[data-song-id=space_potatoes] [data-media-mode='+variant+']').click()
                page.wait_for_function("v=>BMODesktop.features?.mode==='music'&&BMODesktop.features.media.variant===v&&BMODesktop.features.media.state==='playing'", arg=variant)
                if variant == 'video':
                    page.wait_for_function("()=>document.querySelector('#music-video').readyState>=2&&!document.querySelector('#music-video').paused")
            def stop():
                page.keyboard.press('Escape')
                page.wait_for_function("()=>BMODesktop.features?.mode==='conversation'")
            for variant in ('video', 'face'):
                start(variant)
                assert page.evaluate('()=>BMODesktop.pollEvidence.owner') == 'dedicated_worker'
                background = context.new_page()
                background.goto('about:blank')
                background.bring_to_front()
                page.wait_for_function('()=>document.hidden===true', polling=100)
                # No product HTTP observations here. Exceed the unchanged 3s
                # shell timeout while genuine Chromium visibility is hidden.
                background.wait_for_timeout(4400)
                state = fixture.features()
                assert state['mode']=='music' and state['media']['state']=='playing', state['media']
                events = state['media_diagnostics']['events']
                assert any(e['source']=='hidden' and e['lifecycle']['hidden'] for e in events), events
                assert not any(e['event']=='heartbeat_expired' for e in events), events
                background.close()
                page.bring_to_front()
                page.wait_for_function('()=>!document.hidden', polling=100)
                page.wait_for_function("()=>BMODesktop.features?.media?.state==='playing'")
                stop()
                checks.append(variant+': genuine hidden >3s preserves music; foreground and explicit stop work')
            start('video')
            keeper = context.new_page()
            page.close()
            # Closing the document terminates its poll owner. No API polling
            # until after watchdog expiration, then inspect the released state.
            time.sleep(4.4)
            state = fixture.features()
            assert state['mode']=='conversation' and state['audio_owner']!='music', state['media']
            assert state['media']['last_terminal']['outcome']=='cancelled'
            checks.append('document close releases media through existing watchdog, without restart')
            page = keeper
            page.set_default_timeout(12000)
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
            page.goto(fixture.url, wait_until='domcontentloaded')
            page.bring_to_front()
            page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMODesktop.features?.mode==='conversation'")
            start('face')
            session = context.new_cdp_session(page)
            session.send('Page.setWebLifecycleState', {'state':'frozen'})
            # A worker awaiting the frozen parent's ACK cannot keep refreshing
            # /api/desktop forever; the existing watchdog must stop this owner.
            page.wait_for_timeout(4400)
            state = fixture.features()
            assert state['mode']=='conversation' and state['audio_owner']!='music', state['media']
            assert any(e['source']=='shell_heartbeat_expired' for e in state['media_diagnostics']['events'])
            session.send('Page.setWebLifecycleState', {'state':'active'})
            checks.append('frozen parent cannot be kept alive by detached worker; unchanged shell watchdog releases music')
            assert not errors, errors
            code, counts, error = fixture.finish()
            assert code==0 and counts['forbidden']==0 and counts['players']==counts['reaped'], error
        finally:
            if browser is not None:
                browser.close()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    return {'status':'PASS','checks':checks,'not_run':['Pi focus event origin','Pi media output/listening']}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--chromium', nargs='?', const=None, default='/usr/bin/chromium')
    print(json.dumps(run(parser.parse_args().chromium), ensure_ascii=False, indent=2))
