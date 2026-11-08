#!/usr/bin/env python3
"""One offline release gate through the actual Desktop app, PTT and B1 code.

The app runs in its owned child with injected PCM/STT/GPT/voice/CDC boundaries.
Playwright renders the unchanged selected face. No devices or paid APIs run.
The optional media pass reuses the existing camera/game/music browser gate.
"""
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
from checks import check_desktop_app_browser as desktop
from checks.check_desktop_app_browser import DesktopFixture, FIXTURE
from bmo_ai.screen_profiles import SCREEN_PROFILES


def wait_features(fixture, predicate, *, pause=time.sleep, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fixture.features()
        if predicate(value):
            return value
        pause(.02)
    raise AssertionError("Release gate timed out: " + json.dumps({
        key: value.get(key) for key in ("sequence", "phase", "conversation_busy", "motion", "last_result", "requests")}))


def run(profile="ci-synthetic", chromium="/usr/bin/chromium", *, include_media=True):
    from playwright.sync_api import sync_playwright
    face = FIXTURE if profile == "ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    before = hashlib.sha256(face.read_bytes()).hexdigest()
    if profile != "ci-synthetic":
        assert before == SCREEN_PROFILES[profile]["sha256"]
    checks = []
    with tempfile.TemporaryDirectory(prefix="bmo-release-e2e-") as directory:
        # Acknowledgement makes the existing fixture poll gate deterministic:
        # never assume that writing hold-host already stopped an in-flight poll.
        gate="            if (root/'hold-host').exists():return"
        assert desktop.CHILD.count(gate)==1
        child=desktop.CHILD.replace(gate,"            if (root/'hold-host').exists():\n                (root/'hold-host-observed').touch();return")
        with patch.object(desktop,'CHILD',child):
            fixture = DesktopFixture(directory, profile=profile, face=face, scenario="release",
                                     extra_args=("--ptt-trace",))
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium, headless=True,
                    env={"PATH": os.defpath, "HOME": directory, "LC_ALL": "C.UTF-8"},
                    args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output",
                          "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
                try:
                    page = browser.new_page(viewport={"width": 800, "height": 480})
                    page.set_default_timeout(8000)
                    page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                    held = []
                    page.route("**/face.html?kiosk=1", lambda r: held.append(r))
                    page.add_init_script("""window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){
                        window.__audioCalls++;throw Error('Browser audio forbidden')};""")
                    pause = lambda seconds: page.wait_for_timeout(seconds * 1000)
                    wait = lambda predicate: wait_features(fixture, predicate, pause=pause)
                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='loading'")
                    assert 0 <= fixture.state()["progress_percent"] < 100
                    assert fixture.features()["test_fixture"]["b1_commands"] == 0
                    assert fixture.features()["test_fixture"]["greeting_calls"] == 0
                    assert held
                    checks.append("real Desktop entry waits for verified face; loading is checkpoint-based and idle sends zero DO")
                    page.unroute("**/face.html?kiosk=1")
                    held.pop().continue_()
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                    assert fixture.state()["progress_percent"] == 100
                    assert page.locator("#face").bounding_box() == dict(x=0, y=0, width=800, height=480)
                    assert fixture.request("/face.html?kiosk=1")[1] == face.read_bytes()
                    audit = fixture.features()["test_fixture"]
                    assert audit["greeting_calls"] == 1 and audit["brain"] == audit["stt"] == audit["b1_commands"] == 0
                    assert audit["players"] == 2 and audit["max_players"] == 1
                    assert (Path(directory) / ".cache/bmo/startup-chime-v1/boot.wav").is_file()
                    checks.append("original offline chime then one cache-only greeting, serialized in one audio owner; original face fullscreen, no startup brain/STT/DO")

                    def gp8():
                        page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMODesktop.features?.ptt?.enabled===true&&!BMODesktop.features.conversation_busy")
                        page.locator("#face").focus()
                        page.keyboard.press("Enter")

                    def control_finished(receipt, admitted_sequence):
                        # HTTP admission is not owner execution. Safety IDs are
                        # reused, and snapshots may still contain an earlier
                        # terminal result: require a publication AFTER admission.
                        def finished(snapshot):
                            record=next((row for row in snapshot['requests']
                                if row['request_id']==receipt['request_id']),None)
                            return (snapshot['sequence']>admitted_sequence and record
                                and record['status'] in ('succeeded','simulated') and record['completed']
                                and not snapshot['conversation_busy'] and snapshot['audio_owner'] is None
                                and not snapshot['motion']['active'])
                        snapshot=wait(finished)
                        page.wait_for_function("""expected=>{
                          const state=BMODesktop.features;
                          return state?.sequence>=expected.sequence&&state.mode==='conversation'
                            &&!state.conversation_busy&&state.audio_owner===null&&!state.motion.active
                            &&state.requests.some(row=>row.request_id===expected.request_id
                              &&row.completed&&['succeeded','simulated'].includes(row.status));
                        }""",arg=dict(sequence=snapshot['sequence'],request_id=receipt['request_id']))
                        return snapshot

                    def control_and_wait(action):
                        receipt=page.evaluate('action=>BMODesktop.control(action)',action)
                        admitted_sequence=fixture.features()['sequence']
                        return control_finished(receipt,admitted_sequence)

                    gp8()
                    state = wait(lambda s: s["test_fixture"]["b1_commands"] == 1 and s["emotion"] == "happy")
                    assert state["test_fixture"]["b1_gestures"] == ["wave"]
                    assert state["emotion"] == "happy"
                    assert state["test_fixture"]["stt"] == state["test_fixture"]["brain"] == 1
                    assert state["motion"]["controller"]["protocol"] == "bmo_legacy_b1"
                    wait(lambda s: s["test_fixture"]["b1_updates"] > 0)
                    wait(lambda s: not s["conversation_busy"])
                    assert fixture.features()["test_fixture"]["dialogue_cache"]
                    checks.append("GP8 browser edge -> finite synthetic PCM/VAD -> injected STT/GPT -> actual voice start -> one context wave over production B1")

                    page.evaluate("()=>BMODesktop.control('repeat')")
                    wait(lambda s: s["conversation_busy"])
                    state = wait(lambda s: not s["conversation_busy"])
                    assert state["test_fixture"]["b1_commands"] == 1
                    assert (state["test_fixture"]["stt"], state["test_fixture"]["brain"], state["test_fixture"]["synthesis"]) == (1, 1, 1)
                    assert state["emotion"] == "happy"
                    # Reproduce the old false-ready predicate deliberately:
                    # repeat has already ended/HOLDed before cancel is executed.
                    # Hold only this injected owner's poll, not product behavior.
                    wait(lambda s:not s['motion']['active'] and s['test_fixture']['b1_holds']>=1)
                    hold=Path(directory)/'hold-host'
                    hold.write_text('bounded synthetic owner-admission ordering gate')
                    try:
                        wait(lambda _s:(Path(directory)/'hold-host-observed').is_file())
                        cancel_receipt=page.evaluate("()=>BMODesktop.control('cancel')")
                        admitted_sequence=fixture.features()['sequence']
                        before_cancel=fixture.features()
                        assert not before_cancel['motion']['active'] and before_cancel['test_fixture']['b1_holds']>=1
                        with page.expect_response(lambda response:response.url.endswith('/api/control')
                                and response.request.method=='POST'
                                and response.request.post_data_json['payload']['action']=='ptt') as response:
                            gp8()
                        early_ptt=response.value.json()
                        assert early_ptt['status']=='accepted' and cancel_receipt['status']=='accepted'
                    finally:
                        hold.unlink(missing_ok=True)
                    state=wait(lambda s:any(row['request_id']==early_ptt['request_id']
                        and row['status']=='rejected' and row['code']=='stale_mode' for row in s['requests']))
                    assert (state['test_fixture']['mic_sources'],state['test_fixture']['stt'],
                            state['test_fixture']['brain'],state['test_fixture']['b1_commands'])==(1,1,1,1)
                    state=control_finished(cancel_receipt,admitted_sequence)
                    assert state['test_fixture']['b1_holds']>=1
                    checks.append("repeat preserves emotion/cache with zero recapture/provider/DO; controlled delayed cancel proves idle/HOLD alone is stale readiness, rejects early GP8 before capture, then exact completion and rendered state allow the next input")

                    # Stay below the three-second shell-liveness watchdog while
                    # advancing the two-second gesture cooldown deterministically.
                    fixture.set_clock(2)
                    gp8()
                    state = wait(lambda s: s["test_fixture"]["b1_commands"] == 2 and s["emotion"] == "surprised")
                    assert state["test_fixture"]["b1_gestures"] == ["wave", "shrug"]
                    assert state["emotion"] == "surprised"
                    state = control_and_wait('cancel')
                    assert state["test_fixture"]["b1_holds"] >= 2
                    pause(.25)
                    assert fixture.features()["test_fixture"]["b1_commands"] == 2
                    checks.append("next spoken emotion selects shrug; mid-playback cancel retires voice and motion with no delayed retry")

                    fixture.set_clock(4)
                    gp8()
                    state = wait(lambda s: s["test_fixture"]["b1_commands"] == 3)
                    assert state["test_fixture"]["b1_gestures"] == ["wave", "shrug", "celebrate"]
                    state = control_and_wait('stop')
                    assert state['motion']['stopped']
                    assert state["test_fixture"]["b1_commands"] == 3
                    assert state["test_fixture"]["b1_stops"] == 1
                    assert state["test_fixture"]["b1_latched"]
                    assert page.evaluate("()=>window.__audioCalls") == 0
                    checks.append("automatic happy emotion selects celebrate; STOP latches B1 once and never claims a measured servo position")

                    code, counts, error = fixture.finish()
                    assert code == 0, error
                    assert counts["players"] == counts["reaped"]
                    assert counts["browsers"] == counts["browser_reaped"] == counts["b1_closed"] == 1
                    assert counts["mic_sources"] == counts["mic_closed"] == 3
                    assert counts["active_mics"] == counts["forbidden"] == 0
                    assert counts["max_mics"] == counts["max_players"] == 1
                    checks.append("exit closes B1 ownership, PCM, voice worker, fake browser/player children and private temporary caches")
                finally:
                    browser.close()
        finally:
            fixture.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest() == before
    result = {"status": "PASS", "screen_profile": profile, "checks": checks, "counts": counts,
              "sha256_before": before, "sha256_after": before,
              "device_boundary": "in_process_test_double", "physical_validation": "NOT_RUN",
              "not_run": ["native Qt/Pi GUI", "paid STT/GPT/Fish", "actual microphone/camera/USB/servo", "installed third-party games"]}
    if include_media:
        from checks.check_camera_media_browser import run as media_run
        result["camera_game_music"] = media_run(profile, chromium)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-profile", choices=("ci-synthetic", "r10", "v13.2"), default="ci-synthetic")
    parser.add_argument("--chromium", nargs="?", const=None, default="/usr/bin/chromium")
    parser.add_argument("--skip-media", action="store_true", help="skip the separately reusable camera/game/music gate")
    args = parser.parse_args()
    print(json.dumps(run(args.screen_profile, args.chromium, include_media=not args.skip_media), ensure_ascii=False, indent=2))
