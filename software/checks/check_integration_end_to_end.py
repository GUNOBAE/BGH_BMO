#!/usr/bin/env python3
"""Actual simulated launcher + loopback server + approved iframe, without live IO.

The 150 ms synthetic input/brain holds only make transitions observable to the
80 ms HTTP poll. They are fixtures, never measurements of provider latency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmo_ai.integration_face import FACE_SHA256


CHILD = r'''
from contextlib import ExitStack
from pathlib import Path
import json, os, signal, subprocess, sys, time
from unittest.mock import patch
from bmo_ai.integration import main
from bmo_ai.integration_session import SimulatedClient
from bmo_ai.integration_voice import IntegrationVoice

allowed = {'PATH','HOME','LC_ALL','LANG','LC_CTYPE','PYTHONPATH','PYTHONDONTWRITEBYTECODE'}
assert not set(os.environ) - allowed, 'Unexpected child environment field'
counts = dict(listen=0, brain=0, players=0, reaped=0, forbidden=0)
processes = []
real_popen, real_listen, real_create = subprocess.Popen, IntegrationVoice.listen, SimulatedClient.create
def forbidden(*args, **kwargs):
    counts['forbidden'] += 1
    raise AssertionError('Live IO forbidden')
def spawn(command, **options):
    assert command == [sys.executable,'-c','import time; time.sleep(0.8)'], 'Non-synthetic child forbidden'
    assert not set(options.get('env', {})) - allowed, 'Unexpected player environment field'
    process = real_popen(command, **options)
    processes.append(process)
    counts['players'] += 1
    return process
def listen(self):
    assert self.simulate, 'Live listen forbidden'
    counts['listen'] += 1
    time.sleep(.15)
    return real_listen(self)
def create(self, **request):
    counts['brain'] += 1
    time.sleep(.15)
    return real_create(self, **request)
def interrupt(*args):
    raise KeyboardInterrupt()
signal.signal(signal.SIGTERM, interrupt)
with ExitStack() as guards:
    for target in ('socket.socket.connect','bmo_ai.cloud.create_client',
                   'bmo_ai.audio.record_wav','bmo_ai.audio.transcribe',
                   'bmo_ai.fish_tts.synthesize_wav','bmo_ai.local_piper.synthesize_wav'):
        guards.enter_context(patch(target, side_effect=forbidden))
    guards.enter_context(patch('subprocess.Popen', side_effect=spawn))
    guards.enter_context(patch.object(IntegrationVoice,'listen',listen))
    guards.enter_context(patch.object(SimulatedClient,'create',create))
    result = main(['--simulate','--port','0','--face-file',sys.argv[1]],
                  lock_path=Path(os.environ['HOME'])/'launcher.lock')
for process in processes:
    assert process.poll() is not None, 'Owned player survived launcher exit'
    try:
        os.waitpid(process.pid, os.WNOHANG)
    except ChildProcessError:
        counts['reaped'] += 1
    else:
        raise AssertionError('Owned player was not reaped')
print('E2E_RESULT ' + json.dumps(counts), flush=True)
raise SystemExit(result)
'''


def send(process, command):
    process.stdin.write(command + "\n")
    process.stdin.flush()


def launcher_url(process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], .1)
        if ready:
            line = process.stdout.readline()
            if line.startswith("Face: http://127.0.0.1:"):
                return line.removeprefix("Face: ").strip()
            if not line and process.poll() is not None:
                break
    raise AssertionError("Actual launcher did not provide its loopback URL")


def run(face_path, chromium):
    from playwright.sync_api import sync_playwright

    face_path = Path(face_path).expanduser().resolve(strict=True)
    before = hashlib.sha256(face_path.read_bytes()).hexdigest()
    assert before == FACE_SHA256, "Approved face hash mismatch"
    checks = []
    with tempfile.TemporaryDirectory(prefix="bmo-e2e-home-") as home:
        environment = {"PATH": os.defpath, "HOME": home, "LC_ALL": "C.UTF-8",
                       "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        process = subprocess.Popen([sys.executable, "-u", "-B", "-c", CHILD, str(face_path)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=environment)
        try:
            url = launcher_url(process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=chromium, headless=True,
                    timeout=6000, env=environment,
                    args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output"])
                try:
                    page = browser.new_page(viewport={"width": 1024, "height": 600})
                    page.set_default_timeout(4000)
                    page.route("**/*", lambda route: route.continue_()
                               if route.request.url.startswith(url) else route.abort())
                    page.add_init_script("""window.__mediaPlayCalls=0;
                      HTMLMediaElement.prototype.play=function(){window.__mediaPlayCalls++;
                        throw new Error('Browser audio forbidden')};""")
                    page.goto(url)
                    page.wait_for_function("document.querySelector('#face').contentWindow.BMOFace?.getState().version==='13.2'")
                    page.evaluate("""() => {
                      window.observed=[]; window.facePlayCalls=0;
                      window.face=document.querySelector('#face').contentWindow.BMOFace;
                      for(const method of ['playAudio','playBlob']) {
                        face[method]=()=>{facePlayCalls++;throw new Error('Browser audio forbidden')};
                      }
                      function sample(){
                        const current=BMOIntegration.state, state=face.getState();
                        if(current) observed.push({turn:current.turn_id,utterance:current.utterance_id,
                          playback:current.playback,activity:state.activity,speech:state.speech,mouth:state.mouth});
                        if(observed.length>1000) observed.shift();
                        requestAnimationFrame(sample);
                      }
                      sample();
                    }""")
                    checks.append("actual launcher serves approved v13.2 iframe through its own FaceServer")

                    def complete_turn(previous=None):
                        send(process, "")
                        page.wait_for_function("old=>observed.some(s=>s.turn!==old&&s.activity==='listening')", arg=previous)
                        page.wait_for_function("old=>observed.some(s=>s.turn!==old&&s.activity==='thinking')", arg=previous)
                        page.wait_for_function("old=>observed.some(s=>s.turn!==old&&s.speech==='external')", arg=previous)
                        speaking = page.evaluate("old=>observed.find(s=>s.turn!==old&&s.speech==='external')", previous)
                        page.wait_for_function("turn=>{const s=face.getState(),v=BMOIntegration.state;return v?.turn_id===turn&&v.playback==='ended'&&s.activity==='idle'&&s.speech==='off'&&s.mouth==='closed'}", arg=speaking["turn"])
                        return speaking

                    first = complete_turn()
                    checks.append("same simulated turn reaches real listening/thinking/external speech/off+closed idle")
                    send(process, "/repeat")
                    page.wait_for_function("old=>{const s=face.getState(),v=BMOIntegration.state;return v?.turn_id!==old&&s.speech==='external'}", arg=first["turn"])
                    repeated = page.evaluate("({turn:BMOIntegration.state.turn_id,utterance:BMOIntegration.state.utterance_id})")
                    assert repeated["utterance"] != first["utterance"]
                    assert not page.evaluate("turn=>observed.some(s=>s.turn===turn&&s.activity==='listening')", repeated["turn"])
                    send(process, "/cancel")
                    page.wait_for_function("()=>{const s=face.getState(),v=BMOIntegration.state;return v?.playback==='cancelled'&&s.activity==='idle'&&s.speech==='off'&&s.mouth==='closed'}")
                    # The owned harmless child observes cancellation every 20ms.
                    page.wait_for_timeout(100)
                    page.evaluate("observed.length=0")
                    complete_turn(repeated["turn"])
                    checks.append("repeat has fresh IDs without listening; cancellation closes mouth and next turn recovers")

                    assert page.evaluate("window.__mediaPlayCalls+window.facePlayCalls") == 0
                    assert page.locator("#face").content_frame.locator("body").evaluate("()=>window.__mediaPlayCalls") == 0
                    stdout, _ = process.communicate("/quit\n", timeout=4)
                    assert process.returncode == 0, "Simulated launcher failed to exit cleanly"
                    summaries = [json.loads(line.removeprefix("E2E_RESULT ")) for line in stdout.splitlines()
                                 if line.startswith("E2E_RESULT ")]
                    assert summaries == [dict(listen=2, brain=2, players=3, reaped=3, forbidden=0)], summaries
                    try:
                        urlopen(url + "api/state", timeout=.3)
                    except URLError as error:
                        assert isinstance(error.reason, ConnectionRefusedError), "Loopback shutdown was not confirmed"
                    else:
                        raise AssertionError("Launcher left its FaceServer running")
                    checks.append("quit closes loopback server and reaps all three owned children; live/browser audio calls=0")
                finally:
                    browser.close()
        finally:
            if process.poll() is None:
                try:
                    process.communicate("/quit\n", timeout=3)
                except (subprocess.TimeoutExpired, BrokenPipeError, ValueError):
                    process.terminate()
                    try:
                        process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
    after = hashlib.sha256(face_path.read_bytes()).hexdigest()
    assert after == before, "Approved face changed during E2E check"
    return {"status": "PASS", "checks": checks, "counts": summaries[0],
            "face_sha256_before": before, "face_sha256_after": after,
            "fixture_note": "150ms synthetic stage holds only; no latency measurement",
            "not_run": ["real microphone/speaker/Piper", "cloud APIs", "Pi LCD", "physical controls"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--face", default=str(Path.home() / "Downloads/BMO_Screen_v13_2.html"))
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    print(json.dumps(run(args.face, args.chromium), indent=2))


if __name__ == "__main__":
    main()
