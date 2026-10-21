"""Focused free-tier Aaron BMO pronunciation + delivery audition.

We intentionally do NOT regenerate rejected community voices. Fish is the only
provider, no GPT/STT/hardware audio, and paid Drama 3 is not selectable.
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

AARON = fish_tts.AARON_BMO_VOICE_ID
FREE_MODEL = "s2.1-pro-free"
BASE = "Hi, I'm {name}! I was thinking about you today. What should we do together?"
SOFT = "[gentle, warm, slightly lower pitch, still lightly playful] "
LIGHTLY_PLAYFUL = "[warm, curious, lightly playful, natural conversational pace] "

# Keep the spoken words identical apart from the name's spelling in the first
# two cases; the third changes only the delivery direction, not the speaker.
CASES = {
    "soft_beemo": ("Aaron Soft — Beemo", SOFT + BASE.format(name="Beemo")),
    "soft_bee_mo": ("Aaron Soft — Bee-mo", SOFT + BASE.format(name="Bee-mo")),
    "playful_beemo": ("Aaron — gentle playful", LIGHTLY_PLAYFUL + BASE.format(name="Beemo")),
}


def parser():
    p = argparse.ArgumentParser(description="Audition BMO name Beemo vs Bee-mo, Aaron only")
    p.add_argument("--mode", choices=("plan", "free"), default="plan",
                   help="plan performs zero API calls; free explicitly uses S2.1 Pro Free")
    p.add_argument("--variant", choices=(*CASES, "all"), default="soft_beemo")
    p.add_argument("--reviewed-free-terms", action="store_true",
                   help="Required after the announced Fish free tier ends")
    p.add_argument("--output-dir", default="fish_name_trials")
    return p


def report(folder, results):
    blocks = []
    for item in results:
        blocks.append(
            '<section><h2>' + html.escape(item["title"]) + '</h2>'
            '<audio controls preload="none" src="' + html.escape(item["file"], quote=True) +
            '"></audio><p>' + html.escape(item["text"]) + '</p></section>'
        )
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Aaron BMO Name Audition</title><style>'
        'body{font:16px/1.5 system-ui;max-width:900px;margin:28px auto;padding:0 16px}'
        'section{border:1px solid #aaa;border-radius:10px;margin:12px 0;padding:16px}'
        'audio{width:100%}</style></head><body>'
        '<h1>Aaron BMO — Name and delivery audition</h1>'
        '<p>One Aaron reference voice, free S2.1 model. '
        'Compare whether Beemo sounds like the original spoken BMO name, '
        'then judge how naturally playful the last line is. '
        'These are real generated WAVs, not embedded demos.</p>' +
        ''.join(blocks) + '</body></html>'
    )
    webpage = folder / "listen.html"
    webpage.write_text(page, encoding="utf-8")
    webpage.chmod(0o600)
    index = folder / "results.json"
    index.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    index.chmod(0o600)


def run(argv=None, *, synth=None, output=print):
    args = parser().parse_args(argv)
    names = list(CASES) if args.variant == "all" else [args.variant]
    output("Aaron BMO pronunciation audition, Fish " + FREE_MODEL)
    output("Voice ID: " + AARON)
    for name in names:
        output(name + " : " + CASES[name][1])
    if args.mode == "plan":
        output("PLAN ONLY: no API calls or file creation.")
        return 0
    if today_kst() > FREE_UNTIL and not args.reviewed_free_terms:
        output("BLOCKED: confirm current Fish free-tier terms before generating audio.")
        return 2
    key = os.environ.get("FISH_API_KEY", "").strip()
    if not key or key == "YOUR_KEY_HERE" or key.startswith("YOUR_FISH"):
        output("BLOCKED: securely export FISH_API_KEY first.")
        return 2
    parent = Path(args.output_dir).expanduser()
    parent.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="aaron-name-", dir=parent))
    generate = fish_tts.synthesize_wav if synth is None else synth
    results = []
    for name in names:
        title, words = CASES[name]
        wav = None
        try:
            output("Generating: " + name)
            wav = Path(generate(words, model=FREE_MODEL, reference_id=AARON))
            info = audio._wav_info(wav, allow_streaming_header=True)
            target = folder / (name + ".wav")
            with wav.open("rb") as source, target.open("xb") as dest:
                shutil.copyfileobj(source, dest, length=65536)
            target.chmod(0o600)
            results.append({
                "variant": name, "title": title, "file": target.name,
                "text": words, "voice_id": AARON, "model": FREE_MODEL,
                "duration_seconds": round(info["duration_seconds"], 2),
            })
        except (audio.AudioError, OSError, ValueError) as exc:
            output("Stopped: " + str(exc) + ". No retry or paid fallback.")
            report(folder, results)
            output("Partial report: " + str(folder / "listen.html"))
            return 1
        finally:
            if wav is not None:
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    output("Warning: temporary audio cleanup failed.")
    report(folder, results)
    output("DONE: " + str(folder / "listen.html"))
    output("Saved " + str(len(results)) + " Aaron audition WAVs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
