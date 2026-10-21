#!/usr/bin/env python3
"""Headless checks against the unmodified approved HTML, never audio playback.

Run from software/: ../.venv/bin/python checks/check_integration_browser.py
--face ~/Downloads/BMO_Screen_v13_2.html. Browser/device/API checks are separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bmo_ai.integration_face import FACE_SHA256, FaceServer, bridge, envelope, expand_cues, wav_cues


def run(face_path, chromium):
    from playwright.sync_api import sync_playwright

    before = hashlib.sha256(Path(face_path).read_bytes()).hexdigest()
    assert before == FACE_SHA256
    idle = dict(session_id="s", turn_id="0", utterance_id="", sequence=0,
                phase="idle", emotion="neutral", playback="idle", position=0, cues=[], source="fixture")
    checks = []
    with FaceServer(face_path, snapshot=lambda: idle) as server, sync_playwright() as playwright:
        clean_env = {key: value for key, value in os.environ.items()
                     if not any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "CREDENTIAL", "PASSWORD", "AUTH"))}
        browser = playwright.chromium.launch(executable_path=chromium, headless=True,
                                            args=["--no-sandbox", "--disable-gpu", "--mute-audio", "--disable-audio-output"],
                                            env=clean_env)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 600})
            page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(server.url)
                       else route.abort())
            page.add_init_script("""window.__mediaPlayCalls = 0;
              HTMLMediaElement.prototype.play = function() {
                window.__mediaPlayCalls++;
                throw new Error('Browser audio is forbidden in integration');
              };""")
            page.goto(server.url)
            page.wait_for_function("document.querySelector('#face').contentWindow.BMOFace?.getState().version === '13.2'")
            page.evaluate("""() => {
              window.BMOIntegration.close();
              window.adapter = new BMOFaceAdapter(document.querySelector('#face'), {poll:false});
              window.face = document.querySelector('#face').contentWindow.BMOFace;
              window.faceCalls = [];
              for (const method of ['setEmotion','setActivity','setSpeaking','stop','setMouth','playAudio','playBlob']) {
                const original=face[method];
                face[method]=function(...args) { faceCalls.push([method,...args]); return original.apply(this,args); };
              }
              window.seq=0;
              window.state=(changes={}) => ({session_id:'s',turn_id:'t1',utterance_id:'',sequence:++seq,
                phase:'idle',emotion:'neutral',playback:'idle',position:0,cues:[],...changes});
              window.apply=changes => adapter.applySnapshot(state(changes));
            }""")
            version = page.evaluate("({state:face.getState().version,residual:face.version})")
            assert version == {"state": "13.2", "residual": "13.0"}
            checks.append("approved iframe v13.2 / residual v13.0 preserved")

            for emotion in ("crying", "happy", "surprised"):
                for phase in ("listening", "thinking"):
                    state = page.evaluate("""([emotion,phase]) => {
                      apply({emotion,phase});
                      return face.getState();
                    }""", [emotion, phase])
                    assert state["selectedEmotion"] == emotion and state["activity"] == phase, state
                    assert emotion in state["eyeHref"], state
                    assert ("hmm" in state["mouthHref"]) if phase == "thinking" else True
            checks.append("crying/happy/surprised emotion and SVG references survive listening/thinking")
            calls = page.evaluate("""() => {
              faceCalls.length=0;
              apply({emotion:'crying',phase:'thinking'});
              const changed=faceCalls.map(call=>call[0]);
              faceCalls.length=0;
              apply({emotion:'crying',phase:'thinking'});
              return {changed,repeated:faceCalls};
            }""")
            assert calls == {"changed": ["setEmotion", "setActivity"], "repeated": []}, calls

            state = page.evaluate("""() => {
              apply({emotion:'happy',phase:'thinking',playback:'prepared',utterance_id:'u1',cues:[{time:0,mouth:'wide'}]});
              return face.getState();
            }""")
            assert state["speech"] == "off" and state["activity"] == "thinking", state
            state = page.evaluate("""() => {
              apply({emotion:'happy',phase:'speaking',playback:'started',utterance_id:'u1',position:.25,
                cues:[{time:0,mouth:'low'},{time:.1,mouth:'wide'},{time:.2,mouth:'closed'},{time:2,mouth:'closed'}]});
              return face.getState();
            }""")
            assert state["speech"] == "external" and state["mouth"] == "closed", state
            speech = page.evaluate("""() => {
              faceCalls.length=0;
              apply({emotion:'crying',phase:'speaking',playback:'started',utterance_id:'u1',position:.1,
                cues:[{time:0,mouth:'wide'},{time:2,mouth:'closed'}]});
              return {state:face.getState(),calls:faceCalls.map(call=>call[0])};
            }""")
            state = speech["state"]
            assert state["speech"] == "external" and state["selectedEmotion"] == "crying" and state["mouth"] == "wide", state
            assert "setEmotion" in speech["calls"] and "setActivity" not in speech["calls"] and "stop" not in speech["calls"], speech
            for ending in ("ended", "cancelled", "error"):
                utterance = "u-" + ending
                page.evaluate("""utterance => apply({emotion:'crying',phase:'speaking',playback:'started',
                  utterance_id:utterance,cues:[{time:0,mouth:'wide'}]})""", utterance)
                result = page.evaluate("""([utterance,ending]) => {
                  apply({emotion:'crying',playback:ending,utterance_id:utterance,cues:[{time:0,mouth:'wide'}]});
                  const stopped=face.getState();
                  const cues=adapter.state.cues;
                  const late=apply({phase:'speaking',playback:'started',utterance_id:utterance,cues:[{time:0,mouth:'wide'}]});
                  const latePrepared=apply({phase:'thinking',playback:'prepared',utterance_id:utterance});
                  return {stopped,cues,late,latePrepared};
                }""", [utterance, ending])
                assert result["stopped"]["speech"] == "off" and result["stopped"]["mouth"] == "closed", result
                assert result["stopped"]["selectedEmotion"] == "crying" and result["late"] is False, result
                assert result["cues"] == [] and result["latePrepared"] is False, result
            checks.append("prepared does not speak; running silence external+closed; emotion-only speech; terminal off+closed; late cues rejected")

            result = page.evaluate("""() => {
              apply({turn_id:'t2',utterance_id:'u2',phase:'speaking',playback:'started',position:1.5,
                cues:[{time:0,mouth:'wide'},{time:1,mouth:'round'},{time:2,mouth:'closed'}]});
              const resumed=face.getState();
              const stale=adapter.applySnapshot({...state(),sequence:0,utterance_id:'u1',phase:'speaking',playback:'started'});
              const current=face.getState();
              adapter.applySnapshot({...state(),session_id:'new',turn_id:'new',utterance_id:'',phase:'idle'});
              const oldSession=adapter.applySnapshot({...state(),session_id:'s',phase:'speaking',playback:'started',utterance_id:'old'});
              const retiredPrepared=adapter.applySnapshot({...state(),session_id:'new',turn_id:'gone',utterance_id:'gone',phase:'thinking',playback:'prepared'});
              adapter.applySnapshot({...state(),session_id:'new',turn_id:'next',utterance_id:'next',phase:'thinking'});
              const retiredError=adapter.applySnapshot({...state(),session_id:'new',turn_id:'gone',utterance_id:'gone',phase:'idle',playback:'error'});
              const retiredStart=adapter.applySnapshot({...state(),session_id:'new',turn_id:'gone',utterance_id:'gone',phase:'speaking',playback:'started'});
              const malformed=adapter.applySnapshot({...state(),session_id:'new',cues:[null]});
              return {resumed,stale,current,oldSession,retiredPrepared,retiredError,retiredStart,malformed};
            }""")
            assert result["resumed"]["mouth"] == "round" and result["stale"] is False and result["oldSession"] is False, result
            assert result["retiredPrepared"] is True and result["retiredError"] is False and result["retiredStart"] is False and result["malformed"] is False, result
            checks.append("relative-position reconnect skips old cues; stale sequence/session rejected")

            # Compare the port against real original _test, not a fake BMOFace.
            ladder = ("closed", "parted", "small", "low", "mid", "body", "open", "high", "wide", "round", "tight")
            fixtures = [[a, b, t, duration] for a in ladder for b in ladder
                        for t in (0, 1 / 30, 2 / 30, .066667) for duration in (.104, .105, .3)]
            expected = page.evaluate("fixtures => fixtures.map(args => face._test.bridge(...args))", fixtures)
            assert [bridge(*args) for args in fixtures] == expected
            rate = 12000
            samples = [0] * 1800 + [int(21000 * math.sin(i * .15)) for i in range(4800)] + [0] * 2400
            channels = [[sample / 32768 for sample in samples]]
            original = page.evaluate("""({channels,rate}) => {
              const data=channels.map(channel=>new Float32Array(channel));
              return face._test.envelope({sampleRate:rate,numberOfChannels:data.length,length:data[0].length,
                duration:data[0].length/rate,getChannelData:i=>data[i]});
            }""", {"channels": channels, "rate": rate})
            ported = envelope(channels, rate)
            assert len(original) == len(ported)
            for left, right in zip(original, ported):
                assert left["mouth"] == right["mouth"]
                assert abs(left["time"] - right["time"]) < 1e-12
                assert abs(left["level"] - right["level"]) < 1e-12
            with tempfile.TemporaryDirectory(prefix="bmo-mouth-fixture-") as directory:
                wav_path = Path(directory) / "pcm.wav"
                with wave.open(str(wav_path), "wb") as wav:
                    wav.setparams((1, 2, rate, 0, "NONE", "NONE"))
                    wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
                cues = wav_cues(wav_path)
            for cue in cues:
                time = cue["time"]
                index = max(i for i, event in enumerate(original) if event["time"] <= time + 1e-7)
                event = original[index]
                previous = original[index - 1]["mouth"] if index else "closed"
                duration = (original[index + 1]["time"] if index + 1 < len(original) else len(samples) / rate) - event["time"]
                # Use a tiny epsilon inside each held frame to avoid FP boundary ambiguity.
                actual = page.evaluate("args => face._test.bridge(...args)", [previous, event["mouth"], time - event["time"] + 1e-10, duration])
                assert cue["mouth"] == actual, (cue, actual)
            checks.append(f"original _test.bridge matches {len(fixtures)} fixtures; PCM WAV envelope/cue expansion matches original")

            # Check literal frame sequence on the real face through external setMouth.
            rendered = page.evaluate("""() => {
              face.setSpeaking(true);
              return ['low','body','wide','tight','round'].map(mouth => {
                face.setMouth(mouth,.25);
                return face.getState().mouth;
              });
            }""")
            assert rendered == ["low", "body", "wide", "tight", "round"]
            for target, held in (("wide", ["low", "body", "wide"]), ("round", ["tight", "round", "round"])):
                timeline = expand_cues([{"time": 0, "mouth": "closed"},
                                       {"time": .2, "mouth": target},
                                       {"time": .5, "mouth": "closed"}], .5)
                rendered = page.evaluate("""({target,timeline}) => {
                  const accepted=adapter.applySnapshot({...state(),session_id:'new',turn_id:'bridge-'+target,
                    utterance_id:'bridge-'+target,phase:'speaking',playback:'started',position:.2,cues:timeline});
                  const held=[0,1/30,2/30].map(elapsed=>{
                    adapter.renderMouth(adapter.received+elapsed*1000+.00001);
                    return face.getState().mouth;
                  });
                  return {accepted,held};
                }""", {"target": target, "timeline": timeline})
                assert rendered == {"accepted": True, "held": held}, rendered
            assert page.evaluate("window.__mediaPlayCalls") == 0
            assert page.evaluate("faceCalls.filter(call=>['playAudio','playBlob'].includes(call[0])).length") == 0
            assert page.locator("#face").content_frame.locator("body").evaluate("() => window.__mediaPlayCalls") == 0
            checks.append("adapter renders real face low/body/wide and tight/round at 30fps; browser media play calls = 0")

            # A response issued before a hidden tab must not restart stale speech.
            visibility = page.evaluate("""async () => {
              adapter.close();
              const originalFetch=window.fetch;
              let release,requests=0;
              const stale=state({session_id:'visibility',turn_id:'old',utterance_id:'old',
                phase:'speaking',playback:'started',cues:[{time:0,mouth:'wide'}]});
              const current=state({session_id:'visibility',turn_id:'current',utterance_id:'current',
                phase:'speaking',playback:'started',position:1.5,
                cues:[{time:0,mouth:'wide'},{time:1,mouth:'round'},{time:5,mouth:'closed'}]});
              window.__hidden=false;
              Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.__hidden});
              window.fetch=() => ++requests===1 ? new Promise(resolve=>{release=resolve})
                : Promise.resolve({ok:true,json:async()=>current});
              adapter=new BMOFaceAdapter(document.querySelector('#face'));
              window.__hidden=true;
              document.dispatchEvent(new Event('visibilitychange'));
              // Deliberately ignore AbortSignal to model a response already queued.
              release({ok:true,json:async()=>stale});
              await new Promise(resolve=>setTimeout(resolve,0));
              const suspended={state:adapter.state,speech:face.getState().speech};
              window.__hidden=false;
              document.dispatchEvent(new Event('visibilitychange'));
              await new Promise(resolve=>setTimeout(resolve,0));
              const resumed={...face.getState(),utterance:adapter.state.utterance_id};
              window.fetch=originalFetch;
              delete document.hidden;
              return {suspended,resumed,requests};
            }""")
            assert visibility["suspended"] == {"state": None, "speech": "off"}, visibility
            assert visibility["resumed"]["mouth"] == "round" and visibility["resumed"]["utterance"] == "current", visibility
            assert visibility["requests"] == 2, visibility
            checks.append("visibility drops in-flight old HTTP response and resumes at current relative position")

            # Remove the actual iframe, checking owned RAF/network callbacks stop.
            removed = page.evaluate("""async () => {
              document.querySelector('#face').remove();
              await new Promise(resolve=>setTimeout(resolve,40));
              return {closed:adapter.closed,state:adapter.state,controller:adapter.controller===null};
            }""")
            assert removed["closed"] and removed["state"] is None and removed["controller"]
            page.goto(server.url)
            page.wait_for_function("window.BMOIntegration && document.querySelector('#face').contentWindow.BMOFace")
            unloaded = page.evaluate("""async () => {
              document.querySelector('#face').contentWindow.dispatchEvent(new PageTransitionEvent('pagehide'));
              return BMOIntegration.closed && BMOIntegration.state===null && BMOIntegration.controller===null;
            }""")
            assert unloaded
            page.goto(server.url)
            page.wait_for_function("window.BMOIntegration && document.querySelector('#face').contentWindow.BMOFace")
            hidden = page.evaluate("""() => {
              window.dispatchEvent(new PageTransitionEvent('pagehide'));
              return BMOIntegration.closed && BMOIntegration.state===null;
            }""")
            assert hidden
            checks.append("iframe removal/unload and pagehide clean up callbacks/state/network")
        finally:
            browser.close()
    after = hashlib.sha256(Path(face_path).read_bytes()).hexdigest()
    assert after == before
    return {"status": "PASS", "face_sha256_before": before, "face_sha256_after": after,
            "checks": checks, "not_run": ["real audio", "cloud", "microphone", "Pi LCD", "physical controls"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--face", default=str(Path.home() / "Downloads/BMO_Screen_v13_2.html"))
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    args = parser.parse_args()
    print(json.dumps(run(args.face, args.chromium), indent=2))


if __name__ == "__main__":
    main()
