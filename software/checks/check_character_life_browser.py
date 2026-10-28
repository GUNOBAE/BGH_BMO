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
                                 extra_args=("--character-life", "--ptt-trace"))
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
                    page.add_init_script("""window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){
                        window.__audioCalls++;throw Error('Browser audio forbidden')};""")
                    def wait(predicate):
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline:
                            value = fixture.features()
                            if predicate(value): return value
                            page.wait_for_timeout(20)
                        raise AssertionError("Character production pipeline did not reach boundary: " + json.dumps(
                            {"phase": value.get("phase"), "ptt": value.get("ptt"),
                             "input": page.evaluate("()=>BMODesktop.inputEvidence")}))
                    page.goto(fixture.url, wait_until="domcontentloaded")
                    page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")
                    assert fixture.features()["test_fixture"]["brain"] == 0
                    checks.append("cache-only boot starts no dialogue engine or real input")
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
