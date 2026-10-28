#!/usr/bin/env python3
"""Production Desktop/director/session/adapter with synthetic IO boundaries."""
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
    face = FIXTURE if profile == "ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    before = hashlib.sha256(face.read_bytes()).hexdigest()
    checks = []
    with tempfile.TemporaryDirectory(prefix="bmo-character-browser-") as directory:
        fixture = DesktopFixture(directory, profile=profile, face=face, scenario="character",
            extra_args=("--character-life", "--ptt-trace", "--idle-voice", "--wake-word",
                        "--voice-bank-directory", str(Path(directory)/"character-bank")))
        try:
            status, initial_html = fixture.request("/")
            assert status == 200 and "<h1>Starting BMO…</h1>".encode() in initial_html
            assert 'id="loading-stage">Getting ready…'.encode() in initial_html
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=chromium, headless=True,
                    env={"PATH": os.defpath, "HOME": directory, "LC_ALL": "C.UTF-8"},
                    args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output",
                          "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run"])
                try:
                    page = browser.new_page(viewport={"width": 800, "height": 480})
                    page.set_default_timeout(8000)
                    page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                    page.add_init_script("""window.__audioCalls=0;window.__entrances=0;
                        const originalAnimate=Element.prototype.animate;
                        Element.prototype.animate=function(...args){if(this.tagName==='MAIN')window.__entrances++;
                          return originalAnimate.apply(this,args);};
                        HTMLMediaElement.prototype.play=function(){window.__audioCalls++;throw Error('Browser audio forbidden')};""")
                    def wait(predicate):
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline:
                            value = fixture.features()
                            if predicate(value): return value
                            page.wait_for_timeout(20)
                        raise AssertionError("Character production pipeline did not reach boundary: " + json.dumps(
                            {"phase": value.get("phase"), "ptt": value.get("ptt"),
                             "input": page.evaluate("()=>BMODesktop.inputEvidence")}))
                    held=[]
                    page.route("**/face.html?kiosk=1", lambda route: held.append(route))
                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='loading'")
                    assert page.locator("#splash h1").inner_text() == "Starting BMO…"
                    assert page.locator("#loading-stage").inner_text() == "Getting ready…"
                    assert 10 <= fixture.state()["progress_percent"] < 100
                    page.unroute("**/face.html?kiosk=1"); held.pop().continue_()
                    page.wait_for_function("()=>BMOIntegration.state?.source==='cached_greeting'&&BMOIntegration.state.playback==='started'")
                    assert page.evaluate("()=>window.__entrances") == 1
                    assert fixture.state()["face_presented"] and fixture.state()["progress_percent"] == 100
                    assert fixture.features()["test_fixture"]["brain"] == 0
                    checks.append("English achieved-checkpoint boot and once-only 550ms wrapper entrance precede cache-only greeting; original face untouched")
                    page.locator("#face").click(position={"x": 400, "y": 230})
                    page.keyboard.down("Enter"); page.keyboard.down("Enter"); page.keyboard.up("Enter")
                    wait(lambda s:s["ptt"]["microphone_open"])
                    first=wait(lambda s:not s["conversation_busy"] and s["ptt"]["reason_code"]=="transcribed")
                    assert first["test_fixture"]["brain"] == first["test_fixture"]["mic_sources"] == 1
                    assert first["test_fixture"]["max_players"] == 1
                    assert first["wake_word"]["status"] == "MODEL_MISSING"
                    checks.append("GP8 preempts boot for exactly one synthetic PTT turn; missing real wake model opens no persistent microphone")
                    page.reload(); page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMOIntegration.state?.phase==='idle'")
                    assert page.evaluate("()=>window.__entrances") == 0
                    assert fixture.state()["greeting_attempts"] == 1
                    checks.append("reload restores the same face/session without another entrance or greeting")
                    before_idle=fixture.features()["test_fixture"]
                    (Path(directory)/"presence-clock.txt").write_text("500")
                    page.wait_for_function("()=>BMOIntegration.state?.source==='cached_idle'&&BMOIntegration.state.playback==='started'")
                    page.locator("#face").click(position={"x": 400, "y": 230}); page.keyboard.press("Enter")
                    wait(lambda s:s["ptt"]["microphone_open"])
                    after_idle=wait(lambda s:not s["conversation_busy"] and s["test_fixture"]["brain"]==2)
                    assert after_idle["test_fixture"]["max_players"] == after_idle["test_fixture"]["max_mics"] == 1
                    assert after_idle["test_fixture"]["initial_history"] == 0
                    assert before_idle["history_messages"] <= after_idle["test_fixture"]["history_messages"]
                    assert fixture.state()["character_life"]["presence"]["heard_in_15_minutes"] == 0
                    checks.append("idle cached clip uses a separate voice/cache; GP8 cancels it before a single existing PTT turn, without charging heard quota")
                    page.evaluate("()=>BMODesktop.desktop('presence_mode','quiet')")
                    (Path(directory)/"presence-clock.txt").write_text("1200")
                    page.wait_for_function("()=>BMODesktop.state?.character_life?.presence?.mode==='quiet'")
                    assert fixture.state()["character_life"]["presence"]["enabled"] is False
                    checks.append("explicit quiet mode suppresses idle speech without changing conversation or OS sleep")
                    for question, emotion, delivery in (("우는 연기를 해봐", "crying", "theatrical_crying"),
                                                        ("장난스럽게 화내 봐", "angry", "theatrical_angry")):
                        page.evaluate("q=>BMODesktop.control('question',q)", question)
                        played = wait(lambda s:s["phase"] == "speaking" and s["emotion"] == emotion)
                        plan = played["character_life"]["last_played_plan"]
                        assert plan["delivery_id"] == delivery and plan["explicit_acting"]
                        page.wait_for_function("e=>BMOIntegration.state?.emotion===e&&document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion===e", arg=emotion)
                        wait(lambda s:not s["conversation_busy"])
                        assert fixture.features()["emotion"] == emotion
                    checks.append("explicit crying/angry acting traverses real Host, director, voice, session and unchanged face API")
                    audit = fixture.features()["test_fixture"]
                    page.evaluate("()=>BMODesktop.control('repeat')")
                    wait(lambda s:s["conversation_busy"]); wait(lambda s:not s["conversation_busy"])
                    repeated = fixture.features()["test_fixture"]
                    assert (audit["brain"], audit["synthesis"]) == (repeated["brain"], repeated["synthesis"])
                    assert fixture.features()["emotion"] == "angry"
                    checks.append("repeat retains expression with no provider or gesture rerun")
                    page.wait_for_function("()=>BMODesktop.state?.phase==='ready'&&BMODesktop.features?.ptt?.enabled")
                    page.locator("#face").click(position={"x": 400, "y": 230}); page.keyboard.press("Enter")
                    listening = wait(lambda s:s["ptt"]["microphone_open"])
                    assert listening["emotion"] == "angry"
                    wait(lambda s:not s["conversation_busy"] and s["ptt"]["reason_code"] == "transcribed")
                    diagnosis = fixture.features()["turn_diagnostics"]
                    assert diagnosis["terminal"] == "ok" and not diagnosis["contains_transcript"]
                    assert diagnosis["timings"]["host_to_first_frame_ms"]["status"] == "MEASURED"
                    assert diagnosis["timings"]["acoustic_first_sound_ms"]["status"] == "UNMEASURED"
                    assert diagnosis["timings"]["tts_to_first_byte_ms"]["status"] == "UNMEASURED"
                    assert fixture.features()["test_fixture"]["max_mics"] == 1
                    checks.append("PTT preserves prior emotion while listening; synthetic host/PCM timings are distinct from unmeasured Fish/acoustics")
                    assert page.evaluate("()=>window.__audioCalls") == 0
                    code, counts, error = fixture.finish()
                    assert code == 0 and counts["forbidden"] == 0, error
                    assert counts["players"] == counts["reaped"] and counts["max_players"] == 1
                    checks.append("no real devices/credentials/providers/browser audio; owned children reaped")
                finally: browser.close()
        finally: fixture.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest() == before
    return {"status": "PASS", "checks": checks, "screen_profile": profile,
            "screen_sha256": before, "physical_latency": "UNMEASURED", "listening_approval": "NOT_RUN"}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--profile", choices=("ci-synthetic", *SCREEN_PROFILES), default="ci-synthetic")
    p.add_argument("--chromium", nargs="?", const=None, default="/usr/bin/chromium")
    args = p.parse_args()
    print(json.dumps(run(args.profile, args.chromium), ensure_ascii=False, indent=2))
