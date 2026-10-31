"""Offline saved-PCM replay through the production PTT recorder and local VAD.

This command never opens devices, creates cloud clients or uploads audio. It
uses a PCM-duration clock, not invented USB receipt timestamps. Saved WAVs do
not contain the original per-frame delivery trace; results are segmentation
regressions, not new real-time latency or human speech-accuracy measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
from types import SimpleNamespace
import wave

from .features.input.microphone import MicError, PcmFrame, PttRecorder
from .features.resources import AudioFocus
from .turn_diagnostics import TurnDiagnostics


class ReplayError(ValueError):
    def __init__(self, code="invalid_replay_wav"):
        self.code = code
        super().__init__("The bounded local PCM replay could not complete.")


REFERENCE_SHA256 = "3dbb730b90e0266d78d4fe03f00aba37a83780a6a5240d9084b5c01266194c1e"


def reference_overlay(background_pcm, reference_raw, *, target_rms=.004):
    """Constructed test mixture, not a new human recording or calibration.

    The pinned upstream reference is8kHz monoS16; linear2x interpolation makes
    a16kHz analysis mixture. Original background samples are read locally and
    never uploaded or overwritten. The returned bytes remain an offline-only
    derived buffer; this helper never writes or plays an audio file.
    """
    if (type(background_pcm) is not bytes or len(background_pcm) % 2
            or type(reference_raw) is not bytes or sha256_bytes(reference_raw) != REFERENCE_SHA256
            or type(target_rms) not in (int,float) or not math.isfinite(target_rms)
            or not 0 < target_rms <= .03):
        raise ReplayError("invalid_reference_overlay")
    values = [item[0] for item in struct.iter_unpack("<h", reference_raw)]
    reference = []
    for index,value in enumerate(values):
        reference.extend((value,(value+values[min(index+1,len(values)-1)])/2))
    raw_rms = math.sqrt(sum(value*value for value in reference)/len(reference))/32768
    background = [item[0] for item in struct.iter_unpack("<h",background_pcm)]
    if len(background) < len(reference):
        raise ReplayError("reference_overlay_background_short")
    mixed = [max(-32768,min(32767,background[index]+round(reference[index]*target_rms/raw_rms)))
             if index < len(reference) else background[index] for index in range(len(background))]
    pcm = struct.pack("<"+"h"*len(mixed),*mixed)
    return pcm, dict(kind="CONSTRUCTED_REFERENCE_OVERLAY_NOT_HUMAN_ACCEPTANCE",
        reference_sha256=REFERENCE_SHA256, reference_source_rate=8000, analysis_rate=16000,
        interpolation="linear2x", reference_target_rms=target_rms,
        reference_samples=len(reference), reference_seconds=len(reference)/16000,
        human_acceptance="NOT_RUN", raw_audio_exported=False)


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def load_public_reference_pcm16k(*, target_rms=.004):
    """Checks-only pinned public reference; no trailing silence or device IO.

    The dev checkout vendors this8kHz fixture and its MIT/BSD notices under
    tests/fixtures. Callers explicitly append any synthetic silence needed for
    an offline browser/recorder scenario. Product startup never calls this.
    """
    fixture = Path(__file__).resolve().parents[1]/"tests"/"fixtures"/"webrtcvad_reference_8khz_s16le.raw"
    try:
        if fixture.stat().st_size != 14490:
            raise ReplayError("invalid_reference_overlay")
        raw = fixture.read_bytes()
    except OSError:
        raise ReplayError("reference_fixture_unavailable") from None
    pcm, _ = reference_overlay(bytes(len(raw)*2),raw,target_rms=target_rms)
    return pcm


def load_saved_pcm(path):
    try:
        source = Path(path)
        if source.stat().st_size > 450000:
            raise ReplayError()
        with wave.open(str(source), "rb") as recorded:
            count = recorded.getnframes()
            if ((recorded.getframerate(), recorded.getnchannels(), recorded.getsampwidth()) != (16000, 1, 2)
                    or count < 640 or count > 16000 * 12 or recorded.getcomptype() != "NONE"):
                raise ReplayError()
            pcm = recorded.readframes(count)
            if len(pcm) != count * 2:
                raise ReplayError()
        return pcm, hashlib.sha256(source.read_bytes()).hexdigest()
    except (OSError, EOFError, wave.Error):
        raise ReplayError() from None


class _Clock:
    now = 100.
    def __call__(self): return self.now


class _SavedSource:
    sample_rate = 16000
    simulated = True
    def __init__(self, pcm, clock, *, cancel_at=None, on_cancel=None):
        self.pcm, self.clock = pcm, clock
        self.cancel_at, self.on_cancel = cancel_at, on_cancel
        self.closed, self.frames_yielded = False, 0

    def frames(self, capture_id, *, generation, cancel_check=None):
        began = self.clock()
        for sequence, offset in enumerate(range(0, len(self.pcm), 1280)):
            frame = self.pcm[offset:offset+1280]
            if len(frame) != 1280:
                break  # Saved files may end at a partial analysis frame.
            if cancel_check is not None:
                cancel_check()
            self.clock.now += .04
            if self.cancel_at is not None and self.clock() - began >= self.cancel_at:
                self.on_cancel()
            self.frames_yielded += 1
            yield PcmFrame(frame, sequence, self.clock(), generation)
        # This is the known saved-recording boundary, not an unexpected new
        # device failure. Complete source frames are retained without padding.
        raise MicError("pcm_duration_limit")

    def close(self):
        self.closed = True
        return True


def replay_pcm(pcm, *, vad_mode="rms", threshold=.008, silence_timeout=.9,
               cancel_at=None, state_callback=None):
    if type(pcm) is not bytes or not 1280 <= len(pcm) <= 384000 or len(pcm) % 2:
        raise ReplayError("invalid_replay_pcm")
    if cancel_at is not None and (type(cancel_at) not in (int, float) or not math.isfinite(cancel_at)
                                  or not 0 < cancel_at <= 12):
        raise ReplayError("invalid_replay_cancel")
    clock = _Clock()
    source = _SavedSource(pcm, clock, cancel_at=cancel_at)
    trace = TurnDiagnostics(enabled=True, clock=clock)
    trace.mark_input(); trace.begin(1)
    recorder = PttRecorder(AudioFocus(), lambda: source, clock=clock,
        threshold=threshold, silence_timeout=silence_timeout, min_speech=.24,
        speech_safe=True, vad_mode=vad_mode, on_timing=trace.record, on_state=state_callback)
    source.on_cancel = recorder.cancel
    began = clock()
    try:
        try:
            capture = recorder.capture("saved-pcm-replay", generation=1)
        except MicError as error:
            if error.code != "pcm_duration_limit":
                raise
            capture = SimpleNamespace(status="INCOMPLETE_SAVED_SOURCE", code=error.code, pcm=b"")
        state = recorder.snapshot()
        return dict(vad_mode=vad_mode, capture_status=capture.status, capture_code=capture.code,
            retained_bytes=len(capture.pcm), retained_pcm_sha256=hashlib.sha256(capture.pcm).hexdigest() if capture.pcm else None,
            consumed_source_frames=source.frames_yielded, source_frames=len(pcm)//1280,
            replay_clock_seconds=round(clock()-began, 6),
            speech_marker_ms=trace.snapshot()["markers_ms"].get("speech_start"),
            vad_evidence=state["vad_evidence"], threshold=threshold, silence_timeout=silence_timeout,
            speech_entry_window=state["speech_entry_window"],
            clock="SAVED_PCM_DURATION_NOT_USB_RECEIPT_TIME", physical_device="NOT_RUN",
            human_speech_accuracy="NOT_RUN", source_boundary="KNOWN_SAVED_RECORDING_END",
            raw_pcm_modified=False)
    finally:
        recorder.close()


def pcm_summary(pcm):
    frame_rms, energy, dc_energy = [], 0, 0.
    for offset in range(0, len(pcm)-1279, 1280):
        values = [item[0] for item in struct.iter_unpack("<h", pcm[offset:offset+1280])]
        square = sum(value*value for value in values)
        energy += square
        dc_energy += sum(values)**2 / len(values)
        frame_rms.append(math.sqrt(square/len(values))/32768)
    return dict(frames=len(frame_rms), seconds=len(pcm)/32000,
        frame_rms_min=min(frame_rms), frame_rms_median=statistics.median(frame_rms),
        frame_rms_max=max(frame_rms), frames_at_or_above_baseline_008=sum(value >= .008 for value in frame_rms),
        dc_energy_fraction=dc_energy/energy if energy else 0.,
        semantic_labels="UNAVAILABLE", audio_exported=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline saved WAV comparison; no devices or cloud.")
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--vad-mode", choices=("rms", "webrtc", "compare"), default="compare")
    parser.add_argument("--private-report", type=Path)
    parser.add_argument("--reference-fixture", type=Path, help="optional pinned public8kHz regression reference; constructs local-only mixture")
    parser.add_argument("--overlay-rms", type=float, default=.004)
    args = parser.parse_args(argv)
    try:
        pcm, source_sha = load_saved_pcm(args.wav)
        overlay = None
        if args.reference_fixture is not None:
            try:
                if args.reference_fixture.stat().st_size != 14490:
                    raise ReplayError("invalid_reference_overlay")
                reference_raw = args.reference_fixture.read_bytes()
            except OSError:
                raise ReplayError("reference_fixture_unavailable") from None
            pcm, overlay = reference_overlay(pcm,reference_raw,target_rms=args.overlay_rms)
        modes = ("rms", "webrtc") if args.vad_mode == "compare" else (args.vad_mode,)
        report = dict(source_sha256=source_sha, format="16000Hz_MONO_S16_LE", summary=pcm_summary(pcm),
                      replays=[replay_pcm(pcm, vad_mode=mode) for mode in modes],
                      overlay=overlay,human_acceptance="NOT_RUN", private_audio_uploaded=False)
        if args.private_report is not None:
            from .device_acceptance.microphone import _private_directory, _private_file
            directory = _private_directory(args.private_report.parent)
            with _private_file(directory / args.private_report.name) as destination:
                destination.write(json.dumps(report, indent=2, allow_nan=False).encode("utf-8"))
        print(json.dumps(report, allow_nan=False))
        return 0
    except (ReplayError, MicError) as error:
        print(json.dumps(dict(status="NOT_RUN", code=error.code)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
