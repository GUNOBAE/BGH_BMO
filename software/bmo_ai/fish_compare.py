"""Compare Aaron BMO on Fish free S2.1 and paid Drama 3; safe preview default.

Run: python -m bmo_ai.fish_compare --mode plan|free|compare --sample hello|all
Never invokes GPT/STT, microphone, speaker, or an unselected paid model.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import audio, fish_tts

SAMPLES = {
    "hello": ("Friendly", "Hi, I'm BMO! Do you want to play a game with me today?"),
    "happy": ("Excited", "[laughing happily] Yay! We did it! [playfully] You're my best friend!"),
    "sad": ("Sad", "[soft, a little sad] Oh, no... I feel lonely today. Will you stay with me?"),
    "angry": ("Angry", "[annoyed] Hey! That's not fair! [frustrated] Give BMO's controller back!"),
}
FREE_UNTIL = date(2026, 11, 30)
DRAMA_DISCOUNT_UNTIL = date(2026, 11, 12)


def today_kst():
    return datetime.now(timezone(timedelta(hours=9))).date()


def drama_rate(today):
    return 10.0 if today <= DRAMA_DISCOUNT_UNTIL else 50.0


def cost_usd(text, model, today):
    # Official Drama 3 rate is per character; all built-in samples are ASCII.
    if model == "s2.1-pro-free":
        return 0.0
    if model != "drama-3":
        raise ValueError("Unsupported comparison model")
    return len(text) * drama_rate(today) / 1_000_000


def parser():
    p = argparse.ArgumentParser(description="Test Aaron BMO: Fish free S2.1 vs paid Drama 3")
    p.add_argument("--mode", choices=("plan", "free", "compare"), default="plan")
    p.add_argument("--sample", choices=(*SAMPLES, "all"), default="hello")
    p.add_argument("--confirm-paid", action="store_true",
                   help="Required to make a paid Drama 3 request")
    p.add_argument("--reviewed-free-terms", action="store_true",
                   help="Required after Fish's published free period")
    p.add_argument("--max-paid-usd", type=float, default=0.05,
                   help="Estimate limit, NOT a server-side spend cap")
    p.add_argument("--output-dir", default="fish_aaron_tests")
    return p


def write_report(folder, samples, results, day, mode):
    indexed = {(r["sample"], r["model"]): r for r in results}
    rows = []
    for name, (mood, words) in samples:
        cells = []
        for model in ("s2.1-pro-free", "drama-3"):
            result = indexed.get((name, model))
            if result is None:
                cells.append("<td>Not requested</td>")
                continue
            cells.append('<td><audio controls preload="none" src="' +
                         html.escape(result["file"], quote=True) + '"></audio><p>' +
                         str(result["duration_s"]) + 's WAV; request ' +
                         str(result["request_s"]) + 's</p></td>')
        rows.append("<tr><th>" + html.escape(mood) + "<p>" + html.escape(words) +
                    "</p></th>" + "".join(cells) + "</tr>")
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>BMO Aaron voice A/B</title><style>'
        'body{font:16px/1.5 system-ui;max-width:1150px;margin:30px auto;padding:0 16px}'
        'table{width:100%;border-collapse:collapse}th,td{border:1px solid #bbb;'
        'padding:14px;text-align:left;vertical-align:top}audio{max-width:95%}'
        '</style></head><body><h1>Aaron BMO — Fish Audio A/B comparison</h1>'
        '<p>Model A: free S2.1 Pro. Model B: paid Drama 3. '
        'Both use the same exact text and Aaron reference ID. Date (KST): ' +
        str(day) + '. Mode: ' + html.escape(mode) + '.</p>'
        '<table><thead><tr><th>Same script</th><th>Free S2.1</th>'
        '<th>Paid Drama 3</th></tr></thead><tbody>' + "".join(rows) +
        '</tbody></table><h2>Listen for</h2><p>1. Similarity to the original '
        'BMO voice 2. Emotion, not just pitch 3. Naturalness 4. Voice consistency '
        'across lines 5. Whether the paid improvement is worth the price.</p>'
        '<p>Request time is total synthesis download, not time-to-first-audio. '
        'No retries or auto-fallback are used.</p></body></html>'
    )
    report = folder / "report.html"
    report.write_text(page, encoding="utf-8")
    report.chmod(0o600)
    result_path = folder / "results.json"
    result_path.write_text(json.dumps({"day_kst": str(day), "results": results},
                                      indent=2, ensure_ascii=False), encoding="utf-8")
    result_path.chmod(0o600)


def run(argv=None, *, synthesis_fn=None, output_fn=print):
    args = parser().parse_args(argv)
    day = today_kst()
    samples = list(SAMPLES.items()) if args.sample == "all" else [(args.sample, SAMPLES[args.sample])]
    models = ("s2.1-pro-free",) if args.mode == "free" else ("s2.1-pro-free", "drama-3")
    estimated = sum(cost_usd(words, "drama-3", day) for _, (_, words) in samples)
    output_fn("Aaron BMO voice ID: " + fish_tts.AARON_BMO_VOICE_ID)
    output_fn("Scripts: " + ", ".join(name for name, _ in samples))
    output_fn("Models: " + ", ".join(models))
    output_fn("Drama 3 estimated total: USD {:.6f} (USD {:.0f}/million characters)"
              .format(estimated, drama_rate(day)))
    if args.mode == "plan":
        output_fn("PLAN ONLY: no API call. Choose --mode free first.")
        return 0
    if args.mode == "compare":
        if not args.confirm_paid:
            output_fn("BLOCKED: --confirm-paid is required for paid Drama 3.")
            return 2
        if not 0 < args.max_paid_usd <= 10 or estimated > args.max_paid_usd:
            output_fn("BLOCKED: estimated cost exceeds --max-paid-usd.")
            return 2
    if day > FREE_UNTIL and not args.reviewed_free_terms:
        output_fn("BLOCKED: verify free tier billing after November 30, 2026.")
        return 2
    key = os.environ.get("FISH_API_KEY", "").strip()
    if not key or key == "YOUR_KEY_HERE" or key.startswith("YOUR_FISH"):
        output_fn("BLOCKED: export FISH_API_KEY securely before requesting audio.")
        return 2
    root = Path(args.output_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="bmo-aaron-", dir=root))
    call = fish_tts.synthesize_wav if synthesis_fn is None else synthesis_fn
    results = []
    for name, (mood, words) in samples:
        for model in models:
            output_fn("Generating " + name + " using " + model)
            started = time.monotonic()
            temp = None
            try:
                temp = Path(call(words, model=model, reference_id=fish_tts.AARON_BMO_VOICE_ID))
                info = audio._wav_info(temp, allow_streaming_header=True)
                target = folder / (name + ("_free.wav" if model == "s2.1-pro-free" else "_drama3.wav"))
                with temp.open("rb") as stream, target.open("xb") as saved:
                    shutil.copyfileobj(stream, saved, length=65536)
                target.chmod(0o600)
                results.append({"sample": name, "mood": mood, "model": model,
                                "voice_id": fish_tts.AARON_BMO_VOICE_ID, "text": words,
                                "file": target.name,
                                "duration_s": round(info["duration_seconds"], 2),
                                "request_s": round(time.monotonic() - started, 2),
                                "cost_estimate_usd": round(cost_usd(words, model, day), 8)})
            except (audio.AudioError, OSError, ValueError) as error:
                output_fn("Stopped: " + str(error))
                write_report(folder, samples, results, day, args.mode)
                output_fn("No paid fallback/retries. Partial report: " + str(folder / "report.html"))
                return 1
            finally:
                if temp is not None:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        output_fn("Warning: cannot clean temporary WAV.")
    write_report(folder, samples, results, day, args.mode)
    output_fn("DONE: " + str(folder / "report.html"))
    output_fn("Saved " + str(len(results)) + " audio files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
