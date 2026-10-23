#!/usr/bin/env python3
"""Provided media cues through the real feature server, adapter and pinned face.

The owner thread advances a fake playback clock. Generated 0.5-second WAVs are
never played; no dialogue/model, real player, camera or hardware is exercised.
The default CLI re-executes this checker in a credential-free private child.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmo_ai.screen_profiles import SCREEN_PROFILES


PUBLIC_ENV = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONPATH",
                        "PYTHONDONTWRITEBYTECODE"})

GUARDS = r"""(() => {
  window.__cueAudioCalls=0;window.__cueBridgeAudioCalls=0;
  function blocked(){window.__cueAudioCalls++;throw new Error('Test forbids browser audio/capture');}
  HTMLMediaElement.prototype.play=blocked;
  for(const name of ['AudioBufferSourceNode','OscillatorNode']){
    if(window[name]?.prototype?.start)window[name].prototype.start=blocked;
  }
  if(window.speechSynthesis)window.speechSynthesis.speak=blocked;
  if(navigator.mediaDevices?.getUserMedia)navigator.mediaDevices.getUserMedia=blocked;
})();"""


def safe_browser_state(page):
    return page.evaluate("""() => {
      const f=document.querySelector('#face')?.contentWindow?.BMOFace?.getState?.(),
            s=window.BMOFeatures?.state,t=window.BMOIntegration?.state;
      return {mode:s?.mode,media:s?.media?.state,emotion:f?.selectedEmotion,mouth:f?.mouth,
              speech:f?.speech,phase:t?.phase,source:t?.source};
    }""")


def wait_for(page, host, predicate, label, *, timeout=5):
    """Pump production effects on their creating thread while Chromium polls."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        host.poll()
        if page.evaluate(predicate):
            return
        page.wait_for_timeout(20)  # UI polling only; playback time is controlled.
    raise AssertionError(label + ": " + json.dumps(safe_browser_state(page)))


def run(face_path, chromium, profile="v13.2"):
    import playwright
    from playwright.sync_api import sync_playwright
    from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient
    from bmo_ai.features.media import FakeMediaBackend, MediaFeature, make_fixture_media
    from bmo_ai.features.server import FeatureServer
    from bmo_ai.integration_profile import integration_config
    from bmo_ai.integration_voice import IntegrationVoice

    if set(os.environ) - PUBLIC_ENV:
        raise AssertionError("This check requires its credential-free child environment")
    path = Path(face_path).expanduser().resolve(strict=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == SCREEN_PROFILES[profile]["sha256"], "Selected screen hash mismatch"
    checks, observed_sequences = [], []
    counts = {"forbidden": 0, "driver_spawns": 0, "blocked_external_requests": 0,
              "browser_audio_calls": 0, "dialogue_requests": 0}
    now = [0.0]
    clock = lambda: now[0]
    real_popen = subprocess.Popen
    driver_root = Path(playwright.__file__).parent / "driver"
    driver_command = [str(driver_root / "node"), str(driver_root / "package" / "cli.js"), "run-driver"]

    def forbidden(*args, **kwargs):
        counts["forbidden"] += 1
        raise AssertionError("A real backend or unexpected subprocess was requested")

    def driver_only(command, *args, **kwargs):
        if list(command) != driver_command:
            return forbidden()
        assert not any("API_KEY" in key or "TOKEN" in key or "SECRET" in key for key in kwargs.get("env", {})), \
            "Unexpected credential field in browser driver environment"
        counts["driver_spawns"] += 1
        return real_popen(command, *args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="bmo-provided-cue-fixture-") as directory, ExitStack() as guards:
        root = Path(directory)
        environment = {"PATH": os.defpath, "HOME": directory, "LC_ALL": "C.UTF-8",
                       "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "bmo_ai.cloud.create_client",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe", "bmo_ai.audio._run",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.local_piper.synthesize_wav",
                       "bmo_ai.features.media.AlsaWavBackend.start",
                       "bmo_ai.features.camera.Picamera2Backend.open",
                       "bmo_ai.features.camera.OpenCVFaceDetector.detect",
                       "bmo_ai.private_credentials.resolve_credentials"):
            guards.enter_context(patch(target, side_effect=forbidden))
        guards.enter_context(patch("subprocess.Popen", side_effect=driver_only))
        items = make_fixture_media(root)
        cue_path = root / "provided_cues.json"
        cue_path.write_text(json.dumps([{"time": 0, "mouth": "closed"},
                                       {"time": .1, "mouth": "low"},
                                       {"time": .3, "mouth": "wide"}]))
        cue_path.chmod(0o600)
        items = (replace(items[0], cue_path=cue_path.name,
                         cue_sha256=hashlib.sha256(cue_path.read_bytes()).hexdigest()), items[1])
        media = MediaFeature(root, items, backend=FakeMediaBackend(clock=clock, pausable=True), clock=clock)
        client = FeatureSimulatedClient()
        client.responses.create = forbidden
        voice = IntegrationVoice(integration_config(), client, simulate=True, emit=forbidden, player=forbidden)
        voice.listen = voice.speak = forbidden
        host = FeatureHost(media=media, clock=clock, voice=voice, client=client)
        server = None
        try:
            host.session.set_emotion("crying")
            host.session.set_emotion("auto")
            host.poll()
            server = FeatureServer(host, path, screen_profile=profile, port=0).start()
            url = server.url
            with sync_playwright() as api:
                browser = api.chromium.launch(executable_path=chromium, headless=True, timeout=8000,
                    env=environment, args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output",
                        "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
                try:
                    page = browser.new_page(viewport={"width": 1024, "height": 768})
                    page.set_default_timeout(5000)

                    def route(request):
                        if request.request.url.startswith(url):
                            request.continue_()
                        else:
                            counts["blocked_external_requests"] += 1
                            request.abort()

                    page.route("**/*", route)
                    page.add_init_script(GUARDS)
                    page.goto(url)
                    wait_for(page, host, """() => window.BMOFeatures?.state && window.BMOIntegration?.state &&
                      document.querySelector('#face').contentWindow.BMOFace?.getState().version==='13.2' &&
                      document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion==='crying'""",
                      "Pinned face/crying initial state did not load")
                    page.evaluate("""() => {
                      const win=document.querySelector('#face').contentWindow;
                      window.cueFace=win.BMOFace;window.initialFaceWindow=win;
                      for(const name of ['playAudio','playBlob'])cueFace[name]=function(){
                        win.__cueBridgeAudioCalls++;throw new Error('Test forbids face audio');};
                    }""")
                    assert page.locator("#face").get_attribute("src").endswith("?kiosk=1")
                    checks.append("Pinned original iframe and production external adapter loaded with crying preserved")

                    page.locator("#songs").select_option("fixture_song_a")
                    page.locator("#song-play").click()
                    wait_for(page, host, """() => BMOFeatures.state.mode==='music' && !document.querySelector('#face-panel').hidden &&
                      !document.querySelector('#music-panel').hidden && BMOIntegration.state.source==='provided_media_cues' &&
                      cueFace.getState().speech==='external' && cueFace.getState().mouth==='closed'""",
                      "Media did not enter visible external closed-mouth playback")
                    first = page.evaluate("() => ({id:BMOIntegration.state.utterance_id,sequence:BMOIntegration.state.sequence})")
                    observed_sequences.append(first["sequence"])
                    now[0] = .15
                    wait_for(page, host, """() => BMOFeatures.state.media.position>=.15 && cueFace.getState().mouth==='low' &&
                      cueFace.getState().selectedEmotion==='crying' && cueFace.getState().speech==='external'""",
                      "Provided low cue at 0.15 did not reach the original face")
                    observed_sequences.append(page.evaluate("() => BMOIntegration.state.sequence"))
                    now[0] = .35
                    wait_for(page, host, """() => BMOFeatures.state.media.position>=.35 && cueFace.getState().mouth==='wide' &&
                      cueFace.getState().selectedEmotion==='crying'""",
                      "Provided wide cue at 0.35 did not reach the original face")
                    assert page.evaluate("() => BMOIntegration.state.position===0 && BMOIntegration.state.cues.length===1 && BMOIntegration.state.cues[0].time===0")
                    checks.append("Only explicit closed/low/wide cues sample the fake media clock and project one current zero-time cue")

                    page.locator('[data-control="pause"]').click()
                    wait_for(page, host, """() => BMOFeatures.state.media.state==='paused' && BMOIntegration.state.phase==='idle' &&
                      cueFace.getState().mouth==='closed' && cueFace.getState().speech==='off' &&
                      cueFace.getState().selectedEmotion==='crying'""", "Pause did not close/stop the mouth")
                    now[0] = .5
                    host.poll()
                    assert abs(host.media.cue_snapshot()["position"] - .35) < 1e-9
                    page.locator('[data-control="resume"]').click()
                    wait_for(page, host, """() => BMOFeatures.state.media.state==='playing' && BMOIntegration.state.phase==='speaking' &&
                      cueFace.getState().mouth==='wide' && cueFace.getState().speech==='external'""",
                      "Resume did not restore the held provided cue")
                    assert page.evaluate("() => BMOIntegration.state.utterance_id") == first["id"]
                    assert page.evaluate("() => document.querySelector('#face').contentWindow===initialFaceWindow")
                    checks.append("Pause closes speech; resume preserves the same media utterance, current cue and original iframe")

                    now[0] = .7
                    wait_for(page, host, """() => BMOFeatures.state.mode==='conversation' && BMOFeatures.state.audio_owner===null &&
                      BMOIntegration.state.source!=='provided_media_cues' && cueFace.getState().speech==='off' &&
                      cueFace.getState().mouth==='closed' && cueFace.getState().selectedEmotion==='crying'""",
                      "Media end did not release audio and return to crying conversation")
                    observed_sequences.append(page.evaluate("() => BMOIntegration.state.sequence"))
                    assert observed_sequences == sorted(observed_sequences) and observed_sequences[-1] > observed_sequences[0]
                    checks.append("End retires the media utterance, releases audio and returns to crying with monotonic transport sequence")

                    page.locator("#songs").select_option("fixture_song_b")
                    page.locator("#song-play").click()
                    wait_for(page, host, """() => BMOFeatures.state.mode==='music' && BMOFeatures.state.media.active_id==='fixture_song_b' &&
                      BMOIntegration.state.source!=='provided_media_cues' && cueFace.getState().speech==='off' &&
                      cueFace.getState().mouth==='closed'""", "Uncued media invented external speaking")
                    now[0] = .85
                    host.poll()
                    assert host.media.cue_snapshot() is None
                    assert page.evaluate("() => cueFace.getState().speech==='off' && cueFace.getState().selectedEmotion==='crying'")
                    now[0] = 1.3
                    wait_for(page, host, "() => BMOFeatures.state.mode==='conversation' && BMOFeatures.state.audio_owner===null",
                             "Uncued generated media did not finish")
                    checks.append("A non-silent generated WAV without a supplied cue never invents RMS singing/speaking")

                    counts["browser_audio_calls"] = sum(frame.evaluate(
                        "() => (window.__cueAudioCalls||0)+(window.__cueBridgeAudioCalls||0)") for frame in page.frames)
                    counts["dialogue_requests"] = len(client.requests)
                    assert counts["browser_audio_calls"] == counts["dialogue_requests"] == counts["forbidden"] == 0
                    checks.append("Browser audio/capture, dialogue, real synthesis, playback, camera and external backends were never called")
                finally:
                    browser.close()
        finally:
            if server is not None:
                server.close()
            host.close()
        assert host.media.snapshot()["active_id"] is None and host.audio.owner is None
        assert not Path(host._temporary.name).exists()
        checks.append("Owned host/server/media descriptors and generated host assets close cleanly")
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after, "Pinned original screen changed"
    return dict(status="PASS", screen_profile=profile, approval_status=SCREEN_PROFILES[profile]["approval_status"],
                sha256_before=before, sha256_after=after, checks=checks, counts=counts,
                fixture_note="Generated 0.5-second PCM, explicit cues and controlled clock; no real sound or latency measurement",
                not_run=["real API/audio/camera/serial/servo/IMU", "user songs or vocal alignment", "Pi LCD/audio acceptance"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-profile", choices=tuple(SCREEN_PROFILES), default="v13.2")
    parser.add_argument("--face", type=Path)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--child-run", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    face = args.face or Path(SCREEN_PROFILES[args.screen_profile]["default_path"])
    if args.child_run:
        print(json.dumps(run(face, args.chromium, args.screen_profile), indent=2))
        return 0
    with tempfile.TemporaryDirectory(prefix="bmo-cue-browser-child-") as home:
        environment = {"PATH": os.defpath, "HOME": home, "LC_ALL": "C.UTF-8",
                       "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--child-run",
            "--screen-profile", args.screen_profile, "--face", str(face), "--chromium", args.chromium],
            env=environment, text=True, capture_output=True, timeout=90)
        sys.stdout.write(result.stdout)
        if result.returncode:
            sys.stderr.write(result.stderr)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
