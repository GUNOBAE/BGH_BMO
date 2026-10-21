"""Read-only BMO voice A/B report from privacy-safe JSONL timing events.

No APIs, audio, GPIO, Fish, Piper, action systems, package installs, or
modifications to the source logs. Only voice_playback_boundary events from
real Fish playback are compared. Times are process/player boundaries,
not proof of when a speaker becomes audible.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics


def _finite_ms(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value < 600_000


def read_voice_log(path: str, *, model: str, effort: str) -> dict:
    """Extract valid, non-fallback samples from a JSONL telemetry file."""
    samples = []
    tts = []
    ignored = 0
    fallback = 0
    invalid_lines = 0
    with Path(path).expanduser().open("r", encoding="utf-8") as source:
        for line in source:
            if len(line) > 20_000:
                invalid_lines += 1
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                invalid_lines += 1
                continue
            if not isinstance(event, dict) or event.get("event") != "voice_playback_boundary":
                continue
            if event.get("tts_source") != "fish":
                fallback += 1
                continue
            if event.get("chat_model") != model or event.get("reasoning_effort") != effort:
                ignored += 1
                continue
            record_ms = event.get("after_record_end_ms")
            tts_ms = event.get("tts_prepare_ms")
            if _finite_ms(record_ms) and _finite_ms(tts_ms):
                samples.append(record_ms)
                tts.append(tts_ms)
            else:
                ignored += 1
    return {
        "model": model,
        "effort": effort,
        "samples": len(samples),
        "median_capture_end_to_player_ms": (
            round(statistics.median(samples), 1) if samples else None
        ),
        "median_fish_prepare_ms": (
            round(statistics.median(tts), 1) if tts else None
        ),
        "ignored_events": ignored,
        "piper_or_other_tts_events": fallback,
        "malformed_lines": invalid_lines,
    }


def main(argv=None, *, output=print) -> int:
    parser = argparse.ArgumentParser(description="Compare BMO voice log boundaries, entirely offline")
    parser.add_argument("--baseline", required=True, help="GPT-4.1-mini Fish voice JSONL log")
    parser.add_argument("--luna", required=True, help="GPT-6 Luna low Fish voice JSONL log")
    args = parser.parse_args(argv)
    if Path(args.baseline).expanduser().resolve() == Path(args.luna).expanduser().resolve():
        output("Use separate baseline and Luna voice log files.")
        return 2
    try:
        baseline = read_voice_log(args.baseline, model="gpt-4.1-mini", effort="unset")
        luna = read_voice_log(args.luna, model="gpt-6-luna", effort="low")
    except OSError:
        output("Cannot read one of the voice logs; check file paths/permissions.")
        return 2
    for name, value in (("GPT-4.1-mini baseline", baseline), ("GPT-6 Luna low", luna)):
        output(name + ": " + str(value["samples"]) + " valid Fish samples; "
               + "median capture-stop→player-start "
               + str(value["median_capture_end_to_player_ms"]) + " ms; "
               + "Fish prepare " + str(value["median_fish_prepare_ms"]) + " ms")
        if value["piper_or_other_tts_events"] or value["ignored_events"]:
            output(f"  Excluded fallback/nonmatching events: {value['piper_or_other_tts_events']}/{value['ignored_events']}")
    if not baseline["samples"] or not luna["samples"]:
        output("Not enough Fish voice measurements to compare. No winner can be determined.")
        return 1
    difference = round(luna["median_capture_end_to_player_ms"] -
                       baseline["median_capture_end_to_player_ms"], 1)
    output(f"Luna low minus baseline: {difference:+.1f} ms (negative means faster)")
    if min(baseline["samples"], luna["samples"]) < 5:
        output("CAUTION: fewer than five samples per model; repeat matched utterances.")
    output("NOTE: audio-player start is not the first audible acoustic sample; "
           "5-second recording and actual speaking endpoint are not inferred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
