"""Audition BMO voices with Fish's FREE S2.1 only. No GPT or system audio.

The same scripted words are used for every voice. A gentle direction variant
can be tested without modifying pitch artificially or switching voices by mood.
Preview/plan makes NO API calls; generation is user initiated.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import tempfile
from pathlib import Path

from . import audio, fish_tts
from .fish_compare import FREE_UNTIL, today_kst

MODEL = "s2.1-pro-free"
VOICES = {
    "aaron": ("Aaron — original", "106f41ea392c4cc48c6eee76f69ff17e",
              "https://fish.audio/m/106f41ea392c4cc48c6eee76f69ff17e/"),
    "coille": ("Studio Coille — original", "323847d4c5394c678e5909c2206725f6",
               "https://fish.audio/m/323847d4c5394c678e5909c2206725f6/"),
    "nikko": ("Nikkooo — alternative", "1ac90ca23d4a45a4b78b0dcc780069c7",
              "https://fish.audio/m/1ac90ca23d4a45a4b78b0dcc780069c7/"),
    "zac": ("Zaccharie — alternative", "35da70940483488a8f95ade873bf5d20",
            "https://fish.audio/m/35da70940483488a8f95ade873bf5d20/"),
}
PROFILES = {
    "aaron_plain": ("aaron", ""),
    "aaron_soft": ("aaron", "[gentle, warm, slightly lower pitch, still lightly playful] "),
    "coille_plain": ("coille", ""),
    "coille_playful": ("coille", "[subtly playful, warm and expressive, not exaggerated] "),
    "nikko_plain": ("nikko", ""),
    "zac_plain": ("zac", ""),
}
SCRIPTS = {
    "normal": "Hi, I'm BMO. I was thinking about you today. What should we do together?",
    "wonder": "Sometimes I wonder if the stars can hear us... Do you think they get lonely?",
    "happy": "We did it! That was amazing. Can we try one more time?",
    "angry": "Hey... that's not fair. I was still playing. Give it back, please.",
}


def parser():
    p = argparse.ArgumentParser(description="Listen to alternative BMO voices on free Fish S2.1")
    p.add_argument("--mode", choices=("plan", "free"), default="plan")
    p.add_argument("--profile", choices=(*PROFILES, "all"), default="aaron_plain")
    p.add_argument("--sample", choices=SCRIPTS.keys(), default="normal")
    p.add_argument("--reviewed-free-terms", action="store_true",
                   help="Use only after verifying Fish's updated free API terms")
    p.add_argument("--output-dir", default="fish_voice_trials")
    return p


def _write_results(folder, entries, day, sample):
    heading = "BMO voice lab — Free Fish S2.1"
    rows = []
    for r in entries:
        rows.append(
            '<section><h2>' + html.escape(r["profile"]) + '</h2><p><a href="' +
            html.escape(r["voice_url"], quote=True) + '">Original Fish voice page</a></p>' +
            '<audio controls preload="none" src="' + html.escape(r["file"], quote=True) +
            '"></audio><p>' + html.escape(r["sent_text"]) + '</p></section>'
        )
    body = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + heading + '</title><style>body{font:16px/1.5 system-ui;max-width:860px;margin:30px auto;padding:0 16px}section{margin:18px 0;padding:18px;border:1px solid #aaa;border-radius:12px}audio{width:100%}</style></head><body><h1>' + heading + '</h1><p>Day (KST): ' + html.escape(str(day)) + ' · script: ' + html.escape(sample) + '</p><p>Same spoken words; optional directions may alter delivery. This is subjective, not a quality ranking. Files are local.</p>' + "".join(rows) + '</body></html>'
    page = folder / "listen.html"
    page.write_text(body, encoding="utf-8")
    page.chmod(0o600)
    output = folder / "results.json"
    output.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    output.chmod(0o600)


def run(argv=None, *, synth=None, output=print):
    args = parser().parse_args(argv)
    day = today_kst()
    names = list(PROFILES) if args.profile == "all" else [args.profile]
    spoken_text = SCRIPTS[args.sample]
    output("BMO voice lab: " + str(day) + " | Fish " + MODEL)
    for name in names:
        voice_name, direction = PROFILES[name]
        output(name + " — " + VOICES[voice_name][0] + (", direction tag" if direction else ", no tag"))
    if args.mode == "plan":
        output("PLAN ONLY: no API requests. Use --mode free with an exported FISH_API_KEY.")
        return 0
    if day > FREE_UNTIL and not args.reviewed_free_terms:
        output("Blocked: please check Fish free-tier terms before API calls.")
        return 2
    key = os.environ.get("FISH_API_KEY", "").strip()
    if not key or key == "YOUR_KEY_HERE" or key.startswith("YOUR_FISH"):
        output("Blocked: set FISH_API_KEY securely in your shell.")
        return 2
    folder_base = Path(args.output_dir).expanduser()
    folder_base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="bmo-voices-", dir=folder_base))
    generator = fish_tts.synthesize_wav if synth is None else synth
    results = []
    for name in names:
        speaker, direction = PROFILES[name]
        label, voice_id, voice_url = VOICES[speaker]
        temp = None
        try:
            output("Generating: " + name)
            text = direction + spoken_text
            temp = Path(generator(text, model=MODEL, reference_id=voice_id))
            audio._wav_info(temp, allow_streaming_header=True)
            dest = folder / (name + ".wav")
            with temp.open("rb") as src, dest.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=65536)
            dest.chmod(0o600)
            results.append({"profile": name, "voice": label, "voice_id": voice_id,
                            "voice_url": voice_url, "model": MODEL, "file": dest.name,
                            "spoken_text": spoken_text, "sent_text": text})
        except (OSError, ValueError, audio.AudioError) as exc:
            output("Stopped: " + str(exc) + ". No retries or paid fallback.")
            _write_results(folder, results, day, args.sample)
            output("Partial report: " + str(folder / "listen.html"))
            return 1
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    output("Warning: temp file cleanup failed.")
    _write_results(folder, results, day, args.sample)
    output("DONE: " + str(folder / "listen.html"))
    output("Saved " + str(len(results)) + " free-model audio candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
