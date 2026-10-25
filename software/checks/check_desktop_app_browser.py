#!/usr/bin/env python3
"""Desktop launcher/host/product browser with public fixture or pinned local art.

Only a test-owned harmless child stands in for the kiosk process. Playwright
renders the actual product URL headlessly; no desktop session, API or audio
device is opened. Production output never needs to disclose this test URL.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

SOFTWARE = Path(__file__).resolve().parents[1]
PROJECT = SOFTWARE.parent
FIXTURE = Path(__file__).with_name("fixtures") / "bmo_face_contract.html"
sys.path.insert(0, str(SOFTWARE))
from bmo_ai.screen_profiles import SCREEN_PROFILES


CHILD = r'''
import builtins,hashlib,json,os,signal,subprocess,sys,tempfile,time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

allowed={'PATH','HOME','LC_ALL','LANG','LC_CTYPE','PYTHONPATH','PYTHONDONTWRITEBYTECODE',
         'BMO_PYTHON','PWD','OLDPWD','BMO_TEST_PROFILE','BMO_TEST_SCENARIO','BMO_TEST_ROOT'}
assert not set(os.environ)-allowed,'Unexpected desktop fixture environment'
root=Path(os.environ['BMO_TEST_ROOT']);scenario=os.environ['BMO_TEST_SCENARIO']
profile=os.environ['BMO_TEST_PROFILE'];arguments=sys.argv[1:]
if arguments[:3]==['-B','-m','bmo_ai.desktop_app']:arguments=arguments[3:]
real_import=builtins.__import__
def no_devices(name,*args,**kwargs):
    if name.split('.')[0] in ('picamera2','libcamera','cv2','numpy','serial','gpiozero','RPi'):
        raise AssertionError('Real device import forbidden')
    return real_import(name,*args,**kwargs)
builtins.__import__=no_devices
if profile=='ci-synthetic':
    import bmo_ai.screen_profiles as registry
    fixture=Path(os.environ['PYTHONPATH'])/'checks'/'fixtures'/'bmo_face_contract.html'
    payload=fixture.read_bytes()
    assert b'data-synthetic-contract="bmo-face-v1"' in payload
    records=dict(registry.SCREEN_PROFILES)
    records[profile]=dict(default_path=str(fixture),sha256=hashlib.sha256(payload).hexdigest(),
        label='Public synthetic text API fixture; no artwork',approval_status='synthetic_test_only',pi_live_verified=False)
    registry.SCREEN_PROFILES=records

from bmo_ai.desktop_app import main
from bmo_ai.desktop_browser import BrowserError
from bmo_ai.features.host import FeatureHost,FeatureSimulatedClient
from bmo_ai.greeting import GreetingPlayer
from bmo_ai.greeting_cache import GreetingCache,GreetingSpec,make_synthetic_greeting_cache
from bmo_ai.integration_voice import IntegrationVoice

counts=dict(hosts=0,browsers=0,browser_reaped=0,greeting_calls=0,greeting_started=0,
    greeting_ended=0,cache_published=0,brain=0,synthesis=0,players=0,reaped=0,
    forbidden=0,max_players=0,initial_history=0,initial_dialogue_cache=False)
counts.update(mic_sources=0,mic_closed=0,stt=0,active_mics=0,max_mics=0)
hosts=[];players=[];browsers=[];apps=[]
real_popen,real_open=subprocess.Popen,os.open
real_synthesis=IntegrationVoice._synthesize
def forbidden(*args,**kwargs):
    counts['forbidden']+=1;raise AssertionError('Real IO forbidden')
def safe_open(path,*args,**kwargs):
    if isinstance(path,(str,bytes,os.PathLike)) and Path(os.fsdecode(path)).name in ('credentials.json','.credentials.lock'):
        forbidden()
    return real_open(path,*args,**kwargs)
def spawn(command,**options):
    assert command==[sys.executable,'-c','import time; time.sleep(0.8)'],'Only synthetic player allowed'
    assert not set(options.get('env',{}))-allowed,'Unexpected player environment'
    active=sum(process.poll() is None for process in players)
    assert active==0,'Two Python players overlapped'
    process=real_popen(command,**options);players.append(process);counts['players']+=1
    counts['max_players']=max(counts['max_players'],active+1)
    return process
def synthesize(self,*args,**kwargs):
    counts['synthesis']+=1;return real_synthesis(self,*args,**kwargs)
def clock():
    try:offset=float((root/'clock.txt').read_text())
    except FileNotFoundError:offset=0.
    return time.monotonic()+offset

class FakeBrowser:
    def __init__(self,url,*,windowed,startup_timeout,clock):
        assert url.startswith('http://127.0.0.1:')
        self.url,self.ready,self.process,self.closed=url,False,None,False
        self.directory=None;browsers.append(self)
    def start(self):
        if scenario=='browser_start_fail':raise BrowserError('browser_spawn_failed')
        self.directory=tempfile.TemporaryDirectory(prefix='fake-chromium-',dir=root)
        self.process=real_popen([sys.executable,'-c','import time; time.sleep(120)'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            start_new_session=True,env={'PATH':os.defpath,'HOME':self.directory.name,'LC_ALL':'C.UTF-8'})
        counts['browsers']+=1;return self
    def poll(self):
        marker=root/'close-browser'
        if marker.exists() and self.process.poll() is None:
            self.process.terminate();self.process.wait(timeout=2)
        if scenario=='browser_poll_failed':return {'state':'failed','code':'browser_start_failed'}
        return {'state':'exited' if self.process.poll() is not None else 'running' if self.ready else 'starting',
                'code':'browser_closed' if self.process.poll() is not None else 'browser_ready' if self.ready else 'browser_starting'}
    def mark_ready(self):
        if scenario=='browser_ready_race':
            self.process.terminate();self.process.wait(timeout=2);return False
        self.ready=True;return True
    def close(self):
        if self.closed:return
        self.closed=True
        if self.process is not None:
            if self.process.poll() is None:self.process.terminate()
            self.process.wait(timeout=2)
            try:os.waitpid(self.process.pid,os.WNOHANG)
            except ChildProcessError:counts['browser_reaped']+=1
            else:raise AssertionError('Fake browser not reaped')
        if self.directory is not None:self.directory.cleanup()

def host_factory(**settings):
    if scenario=='engine_fail':raise RuntimeError('Private provider fixture detail')
    counts['hosts']+=1
    class Client(FeatureSimulatedClient):
        def create(self,**request):
            counts['brain']+=1;time.sleep(.15)
            return super().create(**request)
    client=Client([dict(version=1,speech='A synthetic desktop response.',emotion='crying',gesture='none',intent=None)]*3)
    if scenario.startswith('ptt'):
        from bmo_ai.features.input.microphone import SyntheticPcmSource
        class Source(SyntheticPcmSource):
            def __init__(self):
                super().__init__(clock=clock)
                self.closed_once=False
            def frames(self,*args,**kwargs):
                counts['mic_sources']+=1;counts['active_mics']+=1
                counts['max_mics']=max(counts['max_mics'],counts['active_mics'])
                assert counts['active_mics']==1 and not any(p.poll() is None for p in players),'Input/output overlap'
                if (root/'fail-mic').exists():
                    from bmo_ai.features.input.microphone import MicError
                    raise MicError('microphone_tool_unavailable')
                for frame in super().frames(*args,**kwargs):
                    if scenario=='ptt_slow':time.sleep(.03)
                    elif scenario=='ptt':time.sleep(.01)
                    yield frame
            def close(self):
                if not self.closed_once:
                    counts['mic_closed']+=1;counts['active_mics']-=1;self.closed_once=True
                return super().close()
        def transcribe(_client,path,**kwargs):
            import stat,wave
            assert stat.S_IMODE(path.stat().st_mode)==0o600
            with wave.open(str(path),'rb') as source:
                assert source.getframerate()==16000 and source.getnchannels()==1 and source.getsampwidth()==2
            counts['stt']+=1
            return 'Synthetic button question.'
        settings.update(ptt_source_factory=Source,ptt_transcriber=transcribe)
    host=FeatureHost(**settings,client=client);hosts.append(host)
    if scenario.startswith('ptt'):
        original_poll=host.poll
        def poll():
            if (root/'hold-host').exists():return
            return original_poll()
        host.poll=poll
    original=host.snapshot
    def snapshot():
        return original()|{'test_fixture':dict(counts,history_messages=len(host.session.brain.history),
            dialogue_cache=host.session.voice.has_cache,brain_instances=1)}
    host.snapshot=snapshot
    if scenario=='engine_poll_fail':
        def fail_poll():raise RuntimeError('Private backend polling detail')
        host.poll=fail_poll
    return host

def greeting_factory(*,config,simulate):
    assert simulate
    cache_path=root/'greeting'
    if scenario=='greeting_missing':cache=GreetingCache(cache_path,GreetingSpec.from_config(config))
    elif not (cache_path/'greeting.cache').exists():
        cache=make_synthetic_greeting_cache(cache_path,config);counts['cache_published']+=1
    else:cache=GreetingCache(cache_path,GreetingSpec.from_config(config))
    if scenario=='greeting_corrupt':
        target=cache_path/'greeting.cache';raw=target.read_bytes();target.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    player=GreetingPlayer(config,cache,simulate=True)
    original=player.play
    def play(*,emit,cancel_check):
        counts['greeting_calls']+=1
        host=hosts[-1]
        if counts['greeting_calls']==1:
            counts['initial_history']=len(host.session.brain.history)
            counts['initial_dialogue_cache']=host.session.voice.has_cache
        def observed(kind,**fields):
            if kind=='started':counts['greeting_started']+=1
            if kind=='ended':counts['greeting_ended']+=1
            emit(kind,**fields)
            if kind=='prepared':time.sleep(.15)
        return original(emit=observed,cancel_check=cancel_check)
    player.play=play
    return player

def on_started(app):
    apps.append(app)
    print('DESKTOP_TEST_READY '+json.dumps({'url':app.server.url}),flush=True)

with ExitStack() as guards:
    for target in ('socket.socket.connect','socket.socket.connect_ex','bmo_ai.cloud.create_client',
        'bmo_ai.private_credentials.resolve_credentials','getpass.getpass','bmo_ai.audio.record_wav',
        'bmo_ai.audio.transcribe','bmo_ai.fish_tts.synthesize_wav','bmo_ai.local_piper.synthesize_wav',
        'bmo_ai.greeting_cache.default_greeting_directory'):
        guards.enter_context(patch(target,side_effect=forbidden))
    guards.enter_context(patch('subprocess.Popen',side_effect=spawn))
    guards.enter_context(patch('os.open',side_effect=safe_open))
    guards.enter_context(patch.object(IntegrationVoice,'_synthesize',synthesize))
    result=main(arguments,lock_path=root/'desktop.lock',host_factory=host_factory,
        browser_factory=FakeBrowser,greeting_factory=greeting_factory,clock=clock,on_started=on_started)
for process in players:
    assert process.poll() is not None,'Player survived desktop exit'
    try:os.waitpid(process.pid,os.WNOHANG)
    except ChildProcessError:counts['reaped']+=1
    else:raise AssertionError('Player not reaped')
for host in hosts:
    assert host._closed and host.audio.owner is None,'Host retained ownership'
    assert not Path(host._temporary.name).exists(),'Host fixtures survived'
for app in apps:assert app._closed,'Runtime survived'
counts['last_code']=apps[-1].startup_snapshot()['code'] if apps else None
counts['last_phase']=apps[-1].startup_snapshot()['phase'] if apps else None
print('DESKTOP_TEST_RESULT '+json.dumps(counts),flush=True)
raise SystemExit(result)
'''


class DesktopFixture:
    """Private launcher subprocess, reusable by unit E2E and headless checks."""
    def __init__(self, root, *, profile="ci-synthetic", face=None, scenario="normal", developer=False):
        self.root, self.profile = Path(root), profile
        self.face = Path(face or (FIXTURE if profile == "ci-synthetic" else SCREEN_PROFILES[profile]["default_path"]))
        from bmo_ai.desktop_install import desktop_install_plan
        self.plan = desktop_install_plan(project_root=PROJECT, home=self.root,
                                         environ={"HOME": str(self.root)}, simulate=True)
        assert self.plan["files_written"] == 0 and self.plan["launcher_verified"]
        entry_command = shlex.split(next(line.removeprefix("Exec=") for line in self.plan["template"].splitlines()
                                        if line.startswith("Exec=")))
        assert entry_command == self.plan["command"]
        script = self.root / "fixture-entry.py"
        script.write_text(CHILD)
        wrapper = self.root / "fixture-python"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B "
                           + shlex.quote(str(script)) + ' "$@"\n')
        wrapper.chmod(0o700)
        environment = {"PATH": os.defpath, "HOME": str(self.root), "LC_ALL": "C.UTF-8",
            "PYTHONPATH": str(SOFTWARE), "PYTHONDONTWRITEBYTECODE": "1", "BMO_PYTHON": str(wrapper),
            "BMO_TEST_ROOT": str(self.root), "BMO_TEST_PROFILE": profile, "BMO_TEST_SCENARIO": scenario}
        command = [*entry_command, "--windowed", "--demo-assets", "--screen-profile", profile,
                   "--face-file", str(self.face)]
        if developer:command.append("--developer")
        self.command, self.environment = command, environment
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=environment, cwd=self.root)
        self.url, self._lines = None, []
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.process.stdout], [], [], .05)
            if readable:
                line = self.process.stdout.readline()
                if line.startswith("DESKTOP_TEST_READY "):
                    self.url = json.loads(line.removeprefix("DESKTOP_TEST_READY "))["url"]
                    break
                self._lines.append(line)
                if not line and self.process.poll() is not None:break
        if self.url is None and scenario != "browser_start_fail":
            output, error = self.process.communicate(timeout=3)
            raise AssertionError("Desktop fixture did not start: " + error + output)

    def request(self, path, *, payload=None, headers=None, body=None):
        parsed = urlsplit(self.url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        try:
            if payload is not None:
                body = json.dumps(dict(version=1, nonce="fixture_" + str(time.monotonic_ns()), payload=payload)).encode()
            values = {"Host": parsed.netloc}
            if body is not None:
                values.update({"Origin": self.url.rstrip("/"), "X-BMO-Token": self.token,
                               "Content-Type": "application/json", "Content-Length": str(len(body))})
            if headers:
                for key, value in headers.items():
                    if value is None:values.pop(key, None)
                    else:values[key] = value
            connection.request("POST" if body is not None else "GET", path, body=body, headers=values)
            response = connection.getresponse()
            return response.status, response.read()
        finally:connection.close()

    @property
    def token(self):
        status, body = self.request("/")
        assert status == 200
        return re.search(rb'name="bmo-command-token" content="([^"]+)"', body)[1].decode()

    def state(self):
        status, payload = self.request("/api/desktop")
        assert status == 200
        return json.loads(payload)

    def features(self):
        status, payload = self.request("/api/features")
        assert status == 200
        return json.loads(payload)

    def ready(self):
        state = self.state()
        status, payload = self.request("/api/screen")
        assert status == 200
        response = self.request("/api/desktop", payload={"action": "face_ready", "value":
            {"run_id": state["run_id"], "screen_sha256": json.loads(payload)["sha256"],
             "frame_version": "synthetic-contract-v1"}})
        if response[0] == 202:
            self.wait(lambda value: value["progress_percent"] == 100 or value["phase"] == "error")
            if self.state()["phase"] != "error":
                return self.request("/api/desktop", payload={"action": "face_presented", "value":
                    {"run_id": state["run_id"]}})
        return response

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.state()
            if predicate(value):return value
            time.sleep(.01)
        raise AssertionError("Desktop state did not reach expected boundary: " + json.dumps(value))

    def set_clock(self, value):
        """Publish one complete offset while the launcher concurrently reads it."""
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Fixture clock offset must be finite")
        staged = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=self.root,
                                             prefix=".clock-", delete=False) as output:
                staged = Path(output.name)
                output.write(repr(float(value)))
            os.replace(staged, self.root / "clock.txt")
            staged = None
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

    def finish(self, *, request_exit=True):
        if self.process.poll() is None and request_exit and self.url is not None:
            try:self.request("/api/desktop", payload={"action": "exit", "value": None})
            except OSError:pass
        output, error = self.process.communicate(timeout=6)
        output = "".join(self._lines) + output
        rows = [json.loads(line.removeprefix("DESKTOP_TEST_RESULT ")) for line in output.splitlines()
                if line.startswith("DESKTOP_TEST_RESULT ")]
        assert len(rows) == 1, "Missing desktop fixture audit: " + error
        assert "Traceback" not in error, error
        return self.process.returncode, rows[0], error

    def close(self):
        if self.process.poll() is None:
            try:self.finish()
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:self.process.communicate(timeout=4)
                except subprocess.TimeoutExpired:self.process.kill();self.process.communicate()


def run(profile="ci-synthetic", face=None, chromium="/usr/bin/chromium"):
    from playwright.sync_api import sync_playwright
    path = Path(face or (FIXTURE if profile == "ci-synthetic" else SCREEN_PROFILES[profile]["default_path"])).resolve(strict=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if profile != "ci-synthetic":assert before == SCREEN_PROFILES[profile]["sha256"]
    checks = []
    with tempfile.TemporaryDirectory(prefix="bmo-desktop-browser-") as directory:
        fixture = DesktopFixture(directory, profile=profile, face=path)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=chromium, headless=True, timeout=8000,
                    env={"PATH": os.defpath, "HOME": directory, "LC_ALL": "C.UTF-8"},
                    args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output",
                          "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
                try:
                    page = browser.new_page(viewport={"width": 800, "height": 480})
                    page.set_default_timeout(6000)
                    held = []
                    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(fixture.url) else route.abort())
                    page.route("**/face.html?kiosk=1", lambda route: held.append(route))
                    page.add_init_script("""window.__mediaPlayCalls=0;window.__faceAudioCalls=0;let faceAPI;
                        Object.defineProperty(window,'BMOFace',{configurable:true,get:()=>faceAPI,set:value=>{
                          for(const name of ['playAudio','playBlob'])if(typeof value[name]==='function')value[name]=()=>{
                            window.__faceAudioCalls++;throw Error('Face browser audio forbidden');};faceAPI=value;}});
                        HTMLMediaElement.prototype.play=function(){window.__mediaPlayCalls++;throw Error('Browser audio forbidden')};""")
                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='loading'")
                    assert page.locator("#splash").is_visible()
                    assert page.locator("#splash").inner_text().startswith("BMO Loading...")
                    # User-requested percentage supersedes the old no-percent contract.
                    # It must reflect a verified backend checkpoint, not elapsed time.
                    loading_percent = int(page.locator("#progress-percent").inner_text().rstrip("%"))
                    assert 10 <= loading_percent < 100
                    assert page.locator("#progress-track").get_attribute("role") == "progressbar"
                    assert page.locator("#progress-track").get_attribute("aria-valuenow") == str(loading_percent)
                    assert page.locator("#progress-fill").evaluate("(el)=>el.style.width") == str(loading_percent) + "%"
                    assert fixture.features()["test_fixture"]["greeting_calls"] == 0
                    assert held
                    page.unroute("**/face.html?kiosk=1")
                    held.pop().continue_()
                    page.wait_for_function("()=>BMOIntegration.state?.source==='cached_greeting'&&document.querySelector('#face').contentWindow.BMOFace.getState().speech==='external'")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMODesktop.state.code==='greeting_completed'")
                    assert page.evaluate("()=>BMODesktop.state.progress_percent") == 100
                    assert page.evaluate("()=>BMODesktop.state.progress_stage") == "local_app_ready"
                    assert page.locator("#progress-track").get_attribute("aria-valuenow") == "100"
                    checks.append("rectangular percent bar tracks backend checkpoints; 100 only after face readiness")
                    audit = fixture.features()["test_fixture"]
                    assert audit["greeting_calls"] == audit["greeting_started"] == audit["greeting_ended"] == audit["players"] == 1
                    assert audit["brain"] == audit["synthesis"] == audit["history_messages"] == 0
                    assert not audit["dialogue_cache"]
                    checks.append("actual desktop entry wrapper starts real host/server and owned fake Chromium; splash waits for iframe before one cached greeting")
                    assert page.locator("#face").bounding_box() == dict(x=0, y=0, width=800, height=480)
                    assert page.evaluate("()=>document.documentElement.scrollWidth===800&&document.documentElement.scrollHeight===480")
                    for selector in ("#splash", "#menu", "#notice", "#game-menu", "#foreground"):
                        assert not page.locator(selector).is_visible(), selector
                    assert page.locator("#talk-button,#shell-header,#shell-footer,#controls-toggle").count() == 0
                    assert fixture.request("/developer")[0] == 404
                    assert fixture.request("/face.html?kiosk=1")[1] == path.read_bytes()
                    checks.append("800x480 product face fills viewport without scroll/developer controls; selected original bytes remain unmodified")

                    page.reload();page.wait_for_function("()=>BMODesktop.state?.phase==='ready'")
                    page.evaluate("""()=>{window.__frameLoads=0;const frame=document.querySelector('#face');
                        frame.addEventListener('load',()=>window.__frameLoads++);frame.contentWindow.location.reload();}""")
                    page.wait_for_function("()=>window.__frameLoads===1")
                    page.wait_for_function("()=>BMOIntegration&&!BMOIntegration.closed&&document.querySelector('#face').contentWindow.BMOFace")
                    page.wait_for_function("()=>BMOIntegration.state?.phase==='idle'")
                    assert fixture.features()["test_fixture"]["greeting_calls"] == 1
                    checks.append("whole-page refresh and iframe-only reconnect restore the adapter without replaying greeting")

                    page.keyboard.press("Escape")
                    assert page.locator("#menu").is_visible()
                    page.locator("#talk").click()
                    page.wait_for_function("()=>BMOIntegration.state?.phase==='speaking'&&document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion==='crying'")
                    page.wait_for_function("()=>BMOIntegration.state?.playback==='ended'")
                    audit = fixture.features()["test_fixture"]
                    assert audit["brain"] == audit["synthesis"] == 1 and audit["history_messages"] > 0 and audit["dialogue_cache"]
                    assert audit["greeting_calls"] == 1
                    page.evaluate("()=>BMODesktop.control('repeat')")
                    page.wait_for_function("()=>BMOIntegration.state?.playback==='started'")
                    page.wait_for_function("()=>BMOIntegration.state?.playback==='ended'")
                    audit = fixture.features()["test_fixture"]
                    assert audit["brain"] == audit["synthesis"] == 1 and audit["players"] == 3
                    assert page.locator("#face").content_frame.locator("body").evaluate("()=>window.__mediaPlayCalls") == 0
                    assert page.locator("#face").content_frame.locator("body").evaluate("()=>window.__faceAudioCalls") == 0
                    assert page.evaluate("()=>window.__mediaPlayCalls") == 0
                    checks.append("conversation keeps the same brain and separate answer cache; repeat causes no extra synthesis/API and browser audio remains zero")
                    page.evaluate("()=>BMODesktop.intent('open_game_menu')")
                    page.wait_for_function("()=>document.querySelector('#game-menu').open")
                    page.locator("#game-back").click()
                    page.wait_for_function("()=>!document.querySelector('#game-menu').open")
                    assert fixture.features()["test_fixture"]["greeting_calls"] == 1
                    checks.append("explicit hidden menu round trip retains conversation emotion and never repeats startup greeting")
                    page.evaluate("""()=>{window.__exitKeys=[];const log=(where,event)=>__exitKeys.push({where,type:event.type,
                        key:event.key,prevented:event.defaultPrevented,menu:document.querySelector('#menu').open});
                        window.addEventListener('keydown',e=>log('parent',e),true);
                        document.querySelector('#face').contentDocument.addEventListener('keydown',e=>log('frame',e),true);
                        document.querySelector('#menu').addEventListener('cancel',e=>log('cancel',e));}""")
                    page.keyboard.press("Escape")
                    assert page.locator("#menu").is_visible(), page.evaluate("""()=>({keys:__exitKeys,
                        focused:document.activeElement.tagName,gameMenu:document.querySelector('#game-menu').open,
                        mode:BMODesktop.state.phase})""")
                    page.locator("#exit").click()
                    code, counts, error = fixture.finish(request_exit=False)
                    assert code == 0, error
                    assert counts["players"] == counts["reaped"] == 3
                    assert counts["browsers"] == counts["browser_reaped"] == 1
                    assert counts["forbidden"] == 0 and counts["max_players"] == 1
                    checks.append("product Exit reaps only its owned fake browser and all Python player children and closes the real loopback server")
                finally:browser.close()
        finally:fixture.close()
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after
    return dict(status="PASS", screen_profile=profile, checks=checks, counts=counts,
                sha256_before=before, sha256_after=after,
                asset_kind="public_synthetic_api_contract" if profile == "ci-synthetic" else "unmodified_approved_local_HTML",
                not_run=["real desktop install/Wayland kiosk/VNC/LCD", "paid APIs and physical devices", "Korean Fish voice quality"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-profile", choices=("ci-synthetic", "r10", "v13.2"), default="ci-synthetic")
    parser.add_argument("--face", type=Path)
    parser.add_argument("--chromium", nargs="?", const=None, default="/usr/bin/chromium")
    args = parser.parse_args()
    if args.screen_profile == "ci-synthetic" and args.face is not None:parser.error("--face requires a pinned local profile")
    print(json.dumps(run(args.screen_profile, args.face, args.chromium), ensure_ascii=False, indent=2))


if __name__ == "__main__":main()
