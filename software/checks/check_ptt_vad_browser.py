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

SOFTWARE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOFTWARE))
from checks.check_desktop_app_browser import DesktopFixture, FIXTURE
from bmo_ai.screen_profiles import SCREEN_PROFILES


def run(profile="ci-synthetic", chromium="/usr/bin/chromium"):
    from playwright.sync_api import sync_playwright
    path = FIXTURE if profile == "ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if profile != "ci-synthetic": assert before == SCREEN_PROFILES[profile]["sha256"]
    checks = []
    with tempfile.TemporaryDirectory(prefix="bmo-ptt-browser-") as directory:
        fixture = DesktopFixture(directory, profile=profile, face=path, scenario="ptt_slow")
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
                            'busy':value.get('conversation_busy'),'ptt':value.get('ptt')}))
                    page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                    page.add_init_script("""window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){
                        window.__audioCalls++;throw Error('Browser audio forbidden')};""")
                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                    audit = fixture.features()["test_fixture"]
                    assert audit["mic_sources"] == audit["stt"] == audit["brain"] == 0
                    assert audit["greeting_calls"] == 1
                    checks.append("startup greeting is cache-only; idle opens zero PCM sources/STT/brain")

                    # Focus the unchanged face iframe; all input belongs to the parent adapter.
                    page.locator("#face").click(position={"x": 400, "y": 230})
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
                    page.wait_for_timeout(250)
                    assert fixture.features()["test_fixture"]["mic_sources"] == 1
                    page.evaluate("()=>BMODesktop.intent('return_to_face')")
                    page.wait_for_function("()=>!document.querySelector('#game-menu').open")
                    checks.append("game menu Enter stays game input and never starts PTT; face return preserves expression")

                    # New capture, then focus-loss cancellation before speech validation.
                    page.locator("#face").click(position={"x": 400, "y": 230})
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
                    assert audit['mic_sources']==2 and audit['stt']==1
                    checks.append('queued Enter survives idle snapshots until blur cancellation retires it; zero source opens after owner resumes')

                    # A fake disconnected recorder is visible and has a final
                    # failed request receipt, without raw tool/provider detail.
                    (Path(directory)/'fail-mic').write_text('synthetic missing input')
                    page.keyboard.press('Enter')
                    wait_features(lambda s:not s['conversation_busy'] and s['ptt']['state']=='failed')
                    page.locator('#ptt-state').wait_for(state='visible')
                    assert '녹음을 완료하지 못했어요' in page.locator('#ptt-state').inner_text()
                    assert fixture.features()['last_result']['code']=='voice_failed'
                    audit=fixture.features()['test_fixture']
                    assert (audit['mic_sources'],audit['mic_closed'],audit['stt'],audit['brain'])==(3,3,1,1)
                    checks.append('fake missing microphone gives fixed external error plus terminal failed receipt; no STT/brain/output')

                    # Reload never greets/records; physical USB mapping is still unverified.
                    page.reload(wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='ready'")
                    page.wait_for_timeout(200)
                    audit = fixture.features()["test_fixture"]
                    assert audit["greeting_calls"] == 1 and audit["mic_sources"] == 3
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
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    return dict(status="PASS",screen_profile=profile,checks=checks,counts=counts,
        sha256_before=before,sha256_after=before,
        not_run=["USB GP8 identity/firmware", "actual microphone/VAD tuning", "paid STT/GPT/Fish", "physical devices"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--screen-profile",choices=("ci-synthetic","r10","v13.2"),default="ci-synthetic")
    p.add_argument("--chromium",nargs="?",const=None,default="/usr/bin/chromium")
    args=p.parse_args()
    print(json.dumps(run(args.screen_profile,args.chromium),ensure_ascii=False,indent=2))
