#!/usr/bin/env python3
"""Field phrases through the production Desktop/FeatureClient/player-start/face API.

Only PCM, provider responses and owned player/browser child processes are test
boundaries. No actual microphone, speaker, credentials or paid requests run.
Use --profile r10 to render the unchanged local approved artwork.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

SOFTWARE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOFTWARE))
from checks import check_desktop_app_browser as desktop
from bmo_ai.screen_profiles import SCREEN_PROFILES


def field_child():
    child=desktop.CHILD
    provider="""    if scenario=='field_expression':
        def row(emotion='neutral',source='acting',reaction='none',delivery='calm'):
            return dict(version=3,speech='A field fixture answer.',emotion=emotion,
                gesture='none',intent=None,reaction=reaction,character=dict(
                delivery_id=delivery,intensity='medium',gaze_id='none',source=source))
        replies=[row(),row('angry',delivery='theatrical_angry'),row(),
                 row('happy','dialogue','chuckle','warm'),row('happy',delivery='excited'),
                 row(),row('angry',delivery='theatrical_angry'),row('neutral','dialogue')]
"""
    anchor="    client=Client(replies)"
    assert child.count(anchor)==1
    child=child.replace(anchor,provider+anchor)
    anchor="            counts['brain']+=1;time.sleep(.15)"
    assert child.count(anchor)==1
    child=child.replace(anchor,"""            counts['brain']+=1;time.sleep(.15)
            if scenario=='field_expression':
                deadline=time.monotonic()+8
                while (root/'hold-provider').exists():
                    if time.monotonic()>deadline:raise AssertionError('Field provider gate expired')
                    time.sleep(.01)""")
    child=child.replace("scenario in ('release','character'):","scenario in ('release','character','field_expression'):")
    child=child.replace("scenario in ('ptt','release','character'):time.sleep(.01)",
                        "scenario in ('ptt','release','character','field_expression'):time.sleep(.02)")
    child=child.replace("scenario in ('character','ptt_webrtc'):",
                        "scenario in ('character','ptt_webrtc','field_expression'):")
    return child


@contextmanager
def fixture_for(root,profile,face):
    # Change only this test process's generated child script. Shared existing
    # browser fixture/product files are never patched on disk.
    with patch.object(desktop,"CHILD",field_child()):
        fixture=desktop.DesktopFixture(root,profile=profile,face=face,scenario="field_expression",
            extra_args=("--character-life","--ptt-trace","--voice-bank-directory",str(Path(root)/"character-bank")))
    try:yield fixture
    finally:fixture.close()


def run(profile="ci-synthetic",chromium="/usr/bin/chromium"):
    from playwright.sync_api import sync_playwright
    face=desktop.FIXTURE if profile=="ci-synthetic" else Path(SCREEN_PROFILES[profile]["default_path"])
    before=hashlib.sha256(face.read_bytes()).hexdigest()
    if profile!="ci-synthetic":assert before==SCREEN_PROFILES[profile]["sha256"]
    checks=[];comparisons=[]
    with tempfile.TemporaryDirectory(prefix="bmo-field-expression-") as directory,fixture_for(directory,profile,face) as fixture:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=chromium,headless=True,
                env={"PATH":os.defpath,"HOME":directory,"LC_ALL":"C.UTF-8"},
                args=["--no-sandbox","--disable-gpu","--mute-audio","--disable-audio-output",
                      "--disable-background-networking","--disable-component-update","--disable-sync","--no-first-run"])
            try:
                page=browser.new_page(viewport={"width":800,"height":480});page.set_default_timeout(8000)
                page.route("**/*",lambda r:r.continue_() if r.request.url.startswith(fixture.url) else r.abort())
                page.add_init_script("""window.__audioCalls=0;HTMLMediaElement.prototype.play=function(){
                    window.__audioCalls++;throw Error('Browser audio forbidden')};""")
                page.goto(fixture.url,wait_until="domcontentloaded")
                page.wait_for_function("()=>BMODesktop.state?.code==='greeting_completed'")

                def wait(predicate):
                    deadline=time.monotonic()+8
                    while time.monotonic()<deadline:
                        state=fixture.features()
                        if predicate(state):return state
                        page.wait_for_timeout(20)
                    raise AssertionError("Field expression did not reach expected boundary: "+json.dumps(
                        {k:state.get(k) for k in ("phase","emotion","conversation_busy","last_result")}))

                def face_state():
                    return page.evaluate("()=>document.querySelector('#face').contentWindow.BMOFace.getState()")

                def question(text,expected,requested,model):
                    page.evaluate("q=>BMODesktop.control('question',q)",text)
                    played=wait(lambda s:s["phase"]=="speaking" and s["emotion"]==expected)
                    trace=played["character_life"]["expression_trace"]
                    assert trace["requested_emotion"]==requested and trace["model_emotion"]==model,trace
                    assert trace["resolved_emotion"]==trace["emitted_emotion"]==expected,trace
                    assert not trace["contains_text"] and trace["emission_status"]=="started"
                    page.wait_for_function("e=>BMOIntegration.state?.emotion===e&&document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion===e",arg=expected)
                    observed=face_state()
                    if profile!="ci-synthetic":
                        # R10 exposes the actually rendered emotion separately
                        # from its requested selection; assert both.
                        page.wait_for_function("e=>document.querySelector('#face').contentWindow.BMOFace.getState().emotion===e",arg=expected)
                        observed=face_state()
                    comparisons.append(dict(requested_emotion=requested,model_emotion=model,
                        resolved_emotion=trace["resolved_emotion"],emitted_emotion=trace["emitted_emotion"],
                        adapter_emotion=page.evaluate("()=>BMOIntegration.state.emotion"),
                        observed_face_emotion=observed["selectedEmotion"],
                        rendered_face_emotion=observed.get("emotion","NOT_IN_SYNTHETIC_CONTRACT")))
                    wait(lambda s:not s["conversation_busy"])
                    assert fixture.features()["emotion"]==expected
                    return played

                question("express a mad or angry?","angry","angry","neutral")
                checks.append("field English anger request traverses FeatureClient, CharacterPlan, session, owned Python player-start and original face API")
                audit=fixture.features()["test_fixture"]
                page.evaluate("()=>BMODesktop.control('repeat')")
                wait(lambda s:s["conversation_busy"]);wait(lambda s:not s["conversation_busy"])
                repeated=fixture.features()
                assert repeated["emotion"]=="angry" and repeated["character_life"]["expression_trace"]["replayed"]
                assert (audit["brain"],audit["synthesis"])==(repeated["test_fixture"]["brain"],repeated["test_fixture"]["synthesis"])
                page.wait_for_function("()=>document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion==='angry'")
                checks.append("repeat replays retained voice/expression without another provider/synthesis/gesture")

                question("What is two plus two?","neutral",None,"angry")
                checks.append("the next factual reply clears prior acting permission and reaches neutral in the unchanged face")
                question("슬픈척 해봐","sad","sad","neutral")
                assert fixture.features()["character_life"]["last_played_plan"]["delivery_id"]=="soft_sad"
                checks.append("field Korean sad acting reaches sad face/soft_sad delivery instead of neutral or crying")
                question("My mother died and I am grieving.","sad",None,"happy")
                plan=fixture.features()["character_life"]["last_played_plan"]
                assert plan["sensitive_context"] and plan["reaction_id"]==plan["gesture_id"]=="none"
                checks.append("serious comfort suppresses inappropriate model happiness/laughter/body flourish")
                question("I passed the exam!","happy",None,"happy")
                plan=fixture.features()["character_life"]["last_played_plan"]
                assert plan["source"]=="dialogue" and not plan["explicit_acting"] and plan["intensity"]=="low"
                checks.append("untrusted acting source label preserves restrained spontaneous happiness while removing theatrical permission")
                question("우는 연기를 해봐","crying","crying","neutral")

                hold=Path(directory)/"hold-provider";hold.write_text("public fixture gate")
                page.locator("#face").click(position={"x":400,"y":230});page.keyboard.press("Enter")
                listening=wait(lambda s:s["ptt"]["microphone_open"] and s["phase"]=="listening")
                assert listening["emotion"]=="crying"
                thinking=wait(lambda s:s["phase"]=="thinking" and s["test_fixture"]["brain"]==7)
                assert thinking["emotion"]=="crying"
                page.wait_for_function("()=>BMOIntegration.state?.phase==='thinking'&&document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion==='crying'")
                page.evaluate("()=>BMODesktop.control('cancel')")
                wait(lambda s:s["phase"]=="idle")
                hold.unlink()
                cancelled=wait(lambda s:not s["conversation_busy"])
                assert cancelled["emotion"]=="crying" and cancelled["test_fixture"]["synthesis"]==6
                page.wait_for_function("()=>document.querySelector('#face').contentWindow.BMOFace.getState().selectedEmotion==='crying'")
                checks.append("crying persists during real Host synthetic PTT listening/thinking and GP8-compatible cancel rejects the late model result")
                question("Please explain one simple fact.","neutral",None,"neutral")
                assert page.evaluate("()=>window.__audioCalls")==0
                code,counts,error=fixture.finish()
                assert code==0 and counts["forbidden"]==0,error
                assert counts["brain"]==8 and counts["stt"]==1 and counts["synthesis"]==7
                assert counts["players"]==counts["reaped"] and counts["max_players"]==1
                assert counts["mic_sources"]==counts["mic_closed"]==1 and counts["active_mics"]==0
                assert counts["b1_commands"]==0
                checks.append("all owned children/PCM close; no device/provider/network/browser audio or physical arm command")
            finally:browser.close()
    assert hashlib.sha256(face.read_bytes()).hexdigest()==before
    return dict(status="PASS",screen_profile=profile,screen_sha256=before,checks=checks,
                emotion_comparisons=comparisons,counts=counts,actual_paid_calls=0,
                human_speech_or_listening_acceptance="NOT_RUN",physical_device_acceptance="NOT_RUN")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",choices=("ci-synthetic",*SCREEN_PROFILES),default="ci-synthetic")
    parser.add_argument("--chromium",nargs="?",const=None,default="/usr/bin/chromium")
    args=parser.parse_args()
    print(json.dumps(run(args.profile,args.chromium),ensure_ascii=False,indent=2))
