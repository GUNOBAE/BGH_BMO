"""Bounded private Host/PTT microphone acceptance, with every paid path denied.

No devices, configuration files or credentials are read on import/help/plan.
Only an explicit ``live_acceptance=True`` run starts one finite production
MicPcmSource. A tap records that same source's PCM; there is no second arecord.
Speaker loopback is coordinated separately by the operator, never by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import struct
from threading import Event, RLock
import time
from types import SimpleNamespace
from uuid import uuid4
import wave


class AcceptanceError(ValueError):
    def __init__(self, code="acceptance_failed"):
        self.code = code
        super().__init__("The bounded microphone acceptance could not complete.")


def _bounded(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def acceptance_plan(*, trial="normal", max_seconds=10., cancel_after=1., vad_mode="rms"):
    if trial not in ("normal", "cancel", "reopen") or not _bounded(max_seconds, .2, 10.):
        raise AcceptanceError("invalid_acceptance_bounds")
    if not _bounded(cancel_after, .1, max_seconds if trial == "cancel" else 10.):
        raise AcceptanceError("invalid_acceptance_bounds")
    if vad_mode not in ("rms", "webrtc"):
        raise AcceptanceError("invalid_vad_configuration")
    return dict(trial=trial, maximum_capture_seconds=float(max_seconds), vad_mode=vad_mode,
                cancel_after_seconds=float(cancel_after) if trial == "cancel" else None,
                maximum_source_opens=1, requested_stream_rate=16000, channels=1,
                sample_width=2, paid_api_policy="DENIED", speaker_policy="EXTERNAL_OPERATOR_ONLY",
                persistent_wake_policy="MODEL_MISSING_PTT_ONLY", real_human_speech="NOT_RUN")


def _private_directory(directory):
    if directory is None:
        raise AcceptanceError("private_directory_required")
    target = Path(directory).expanduser().absolute()
    repository = Path(__file__).resolve().parents[3]
    try:
        target.resolve().relative_to(repository)
    except ValueError:
        pass
    else:
        raise AcceptanceError("raw_audio_in_repository_forbidden")
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        raise AcceptanceError("private_directory_unavailable") from None
    try:
        info = target.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise AcceptanceError("private_directory_permissions")
    except OSError:
        raise AcceptanceError("private_directory_unavailable") from None
    return target


def _private_file(path):
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        return os.fdopen(os.open(path, flags, 0o600), "wb")
    except OSError:
        raise AcceptanceError("private_output_unavailable") from None


class PcmEvidence:
    """Only aggregate PCM statistics leave the private recording sink."""
    def __init__(self, *, button_at, frame_ms=40):
        self._lock = RLock()
        self.button_at, self.frame_ms = button_at, frame_ms
        self.frames = self.samples = self.zeros = self.nonzeros = self.clipped = 0
        self.squares = self.peak = 0
        self.sequence_errors = self.format_errors = self.timestamp_errors = self.large_gaps = 0
        self._sequence = self._timestamp = self._first = None
        self._first_bytes = None
        self._smallest_gap = self._largest_gap = None
        self._minimum_rms = self._maximum_rms = None

    def add(self, frame):
        pcm = frame.pcm
        if type(pcm) is not bytes or not pcm or len(pcm) % 2:
            raise AcceptanceError("invalid_tapped_pcm")
        with self._lock:
            if len(pcm) != 16000 * 2 * self.frame_ms // 1000:
                self.format_errors += 1
            if type(frame.sequence) is not int or frame.sequence != (0 if self._sequence is None else self._sequence + 1):
                self.sequence_errors += 1
            stamp = frame.timestamp
            valid = type(stamp) in (int, float) and math.isfinite(stamp) and stamp >= self.button_at
            if not valid:
                self.timestamp_errors += 1
            elif self._timestamp is not None:
                gap = (stamp - self._timestamp) * 1000
                if gap < 0:
                    self.timestamp_errors += 1
                else:
                    self._smallest_gap = gap if self._smallest_gap is None else min(self._smallest_gap, gap)
                    self._largest_gap = gap if self._largest_gap is None else max(self._largest_gap, gap)
                    self.large_gaps += gap > max(120., self.frame_ms * 3)
            if valid:
                self._timestamp = stamp
                if self._first is None:
                    self._first = stamp
                    self._first_bytes = len(pcm)
            self._sequence = frame.sequence
            values = [item[0] for item in struct.iter_unpack("<h", pcm)]
            squared = sum(value * value for value in values)
            rms = math.sqrt(squared / len(values)) / 32768
            self._minimum_rms = rms if self._minimum_rms is None else min(self._minimum_rms, rms)
            self._maximum_rms = rms if self._maximum_rms is None else max(self._maximum_rms, rms)
            zeros = values.count(0)
            self.zeros += zeros
            self.nonzeros += len(values) - zeros
            self.clipped += sum(abs(value) >= 32760 for value in values)
            self.peak = max(self.peak, max(abs(value) for value in values))
            self.squares += squared
            self.samples += len(values)
            self.frames += 1

    def snapshot(self):
        with self._lock:
            return dict(frames=self.frames, samples=self.samples, byte_count=self.samples * 2,
                stream_seconds=self.samples / 16000, zero_samples=self.zeros, nonzero_samples=self.nonzeros,
                all_zero=self.samples > 0 and self.nonzeros == 0, clipped_samples=self.clipped,
                clipping_fraction=self.clipped / self.samples if self.samples else None,
                peak_normalized=self.peak / 32768,
                rms=math.sqrt(self.squares / self.samples) / 32768 if self.samples else None,
                frame_rms_min=self._minimum_rms, frame_rms_max=self._maximum_rms,
                sequence_errors=self.sequence_errors, frame_length_errors=self.format_errors,
                timestamp_errors=self.timestamp_errors, host_delivery_large_gaps=self.large_gaps,
                host_delivery_gap_min_ms=self._smallest_gap, host_delivery_gap_max_ms=self._largest_gap,
                first_pcm_after_host_button_ms=(self._first-self.button_at)*1000 if self._first is not None else None,
                first_pcm_buffer_bytes=self._first_bytes,
                actual_adc_sample_rate="UNMEASURED", requested_stream_sample_rate=16000,
                driver_dropped_frames="UNMEASURED", xrun="UNMEASURED_STDERR_DISCARDED",
                gap_interpretation="HOST_RECEIPT_ONLY_NOT_PROOF_OF_DRIVER_DROP")


class _TapSource:
    sample_rate = 16000
    simulated = False
    def __init__(self, source, wav_path, evidence, ready_path=None, max_bytes=320000):
        self.source, self.wav_path, self.evidence = source, wav_path, evidence
        self.ready_path, self._ready_written = ready_path, False
        self.max_bytes = max_bytes
        self.writer = self._stream = None
        self._lock = RLock()
        self._closed = False

    def set_timing_observer(self, observer):
        setter = getattr(self.source, "set_timing_observer", None)
        if callable(setter):
            setter(observer)

    def frames(self, capture_id, *, generation, cancel_check=None):
        with self._lock:
            self._stream = _private_file(self.wav_path)
            self.writer = wave.open(self._stream, "wb")
            self.writer.setnchannels(1); self.writer.setsampwidth(2); self.writer.setframerate(16000)
        try:
            for frame in self.source.frames(capture_id, generation=generation, cancel_check=cancel_check):
                with self._lock:
                    if self.evidence.samples * 2 + len(frame.pcm) > self.max_bytes:
                        raise AcceptanceError("acceptance_pcm_byte_limit")
                    self.evidence.add(frame)
                    self.writer.writeframesraw(frame.pcm)
                    if self.ready_path is not None and not self._ready_written:
                        with _private_file(self.ready_path) as ready:
                            ready.write(json.dumps(dict(version=1, event="first_pcm_received",
                                pid=os.getpid(), source_timestamp=frame.timestamp,
                                sample_rate=16000, channels=1, frame_bytes=len(frame.pcm)),
                                allow_nan=False).encode("utf-8"))
                        self._ready_written = True
                yield frame
        finally:
            self._finish_wav()

    def _finish_wav(self):
        with self._lock:
            if self.writer is not None:
                self.writer.close()
                self.writer = None
            if self._stream is not None:
                self._stream.close()
                self._stream = None

    def close(self):
        with self._lock:
            if self._closed:
                return True
            clean = self.source.close() is not False
            self._finish_wav()
            self._closed = clean
            return clean


class _DeniedClient:
    def __init__(self):
        self.attempts = 0
        self.responses = self
        self.audio = SimpleNamespace(transcriptions=self, speech=self)

    def create(self, *args, **kwargs):
        self.attempts += 1
        raise AcceptanceError("paid_api_forbidden")

    def close(self):
        return True


def run_microphone_acceptance(output_directory=None, *, live_acceptance=False,
        trial="normal", max_seconds=10., cancel_after=1., mic_device="plughw:CARD=Device,DEV=0",
        stop_requested=None, ready_file=None, _source_factory=None, _clock=time.monotonic, _sleep=time.sleep,
        vad_mode="rms"):
    """One bounded production Host/PTT trial; default is a pure dry-run plan.

    The private WAV includes all raw source frames, even discarded no-speech or
    cancelled audio. A local empty-transcript sink prevents all cloud calls.
    ``reopen`` labels a separately coordinated trial; it does not manufacture
    evidence that a previous owner was closed or a speaker loopback was heard.
    """
    plan = acceptance_plan(trial=trial, max_seconds=max_seconds, cancel_after=cancel_after, vad_mode=vad_mode)
    if type(live_acceptance) is not bool:
        raise AcceptanceError("live_acceptance_must_be_explicit")
    if not live_acceptance:
        return dict(status="NOT_RUN", plan=plan, device_opened=False)
    directory = _private_directory(output_directory)
    ready_path = None
    if ready_file is not None:
        ready_path = Path(ready_file).expanduser().absolute()
        if ready_path.parent.resolve() != directory.resolve() or ready_path.exists() or ready_path.is_symlink():
            raise AcceptanceError("private_ready_output_invalid")
    token = "mic-" + trial + "-" + uuid4().hex
    wav_path, report_path = directory / (token + ".wav"), directory / (token + ".json")
    from ..features.host import FeatureHost
    from ..features.input.microphone import MicPcmSource
    from ..features.runtime import RuntimePolicy
    from ..integration_profile import integration_config
    from ..integration_voice import IntegrationVoice
    config = integration_config(mic_device=mic_device)
    client = _DeniedClient()
    stopped = stop_requested or Event()
    began = _clock()
    evidence = PcmEvidence(button_at=began)
    taps, local_stt, voice_attempts, capture_outcomes = [], [], [], []
    def forbidden_voice(*args, **kwargs):
        voice_attempts.append(True)
        raise AcceptanceError("paid_or_speaker_path_forbidden")
    def blocked_stt(*args, **kwargs):
        local_stt.append(True)
        return ""  # No transcript means the existing worker never starts GPT.
    def source_factory():
        if taps:
            raise AcceptanceError("source_retry_forbidden")
        source = (_source_factory() if _source_factory is not None else
                  MicPcmSource(mic_device, live_opt_in=True, max_duration=max_seconds, clock=_clock))
        tap = _TapSource(source, wav_path, evidence, ready_path, round(16000*2*max_seconds))
        taps.append(tap)
        return tap
    voice = IntegrationVoice(config, client, simulate=False, character_life=True,
        emit=lambda *args, **kwargs: None, fish_synthesizer=forbidden_voice,
        piper_synthesizer=forbidden_voice, player=forbidden_voice)
    host = None
    cancelled = timed_out = cleanup_ok = False
    cleanup = {}
    code = "not_started"
    diagnostics = {}
    wake_off_before_ptt = None
    try:
        host = FeatureHost(config=config, client=client, voice=voice, clock=_clock,
            runtime=RuntimePolicy(simulate=False, conversation_ptt_microphone=True),
            ptt_source_factory=source_factory, ptt_transcriber=blocked_stt,
            character_life=True, ptt_speech_safe=True, wake_word=True,
            **({"ptt_vad_mode": vad_mode} if vad_mode != "rms" else {}))
        # This intentionally verifies the actual Host broker's model-missing
        # PTT fallback. No manifest is supplied; persistent listening is denied.
        if host.enable_wake_word() is not False:
            raise AcceptanceError("persistent_wake_forbidden")
        if trial == "reopen":
            clean = host.disable_wake_word()
            before = host.snapshot()["microphone_broker"]
            wake_off_before_ptt = dict(cleanup_verified=clean,
                microphone_open=before["microphone_open"], reader_alive=before["reader_alive"],
                wake_enabled=before["wake_enabled"], state=before["state"],
                detector_status=host.snapshot()["wake_word"]["status"],
                persistent_reader_acoustic_off="NOT_RUN_MODEL_MISSING")
            if not clean or before["microphone_open"] or before["reader_alive"] or before["wake_enabled"]:
                raise AcceptanceError("wake_off_before_ptt_unverified")
        original_capture = host.microphone.capture
        def capture_with_outcome(*args, **kwargs):
            try:
                result = original_capture(*args, **kwargs)
                capture_outcomes.append(dict(status=result.status, code=result.code))
                return result
            except Exception as error:
                from ..conversation import TurnCancelled
                safe_codes = {"pcm_truncated", "pcm_duration_limit", "microphone_stream_closed",
                              "microphone_read_failed", "microphone_start_failed", "microphone_tool_unavailable",
                              "pcm_cleanup_failed", "invalid_pcm_frame", "audio_focus_busy"}
                capture_outcomes.append(dict(status="cancelled" if isinstance(error, TurnCancelled) else "failed",
                    code="user_cancelled" if isinstance(error, TurnCancelled) else
                         error.code if getattr(error, "code", None) in safe_codes else "capture_failed"))
                raise
        host.microphone.capture = capture_with_outcome
        button_at = _clock()
        evidence.button_at = button_at
        if not host.ptt_button():
            raise AcceptanceError("ptt_admission_failed")
        deadline = button_at + max_seconds
        while host.session.busy:
            now = _clock()
            cancel_due = trial == "cancel" and now - button_at >= cancel_after
            if not cancelled and (cancel_due or stopped.is_set() or now >= deadline):
                timed_out = now >= deadline
                cancelled = True
                host.cancel_conversation()
            if now >= deadline + 2.:
                timed_out = True
                code = "acceptance_hard_timeout"
                break
            host.poll()
            _sleep(.01)
        host.poll()
        diagnostics = host.turn_diagnostics.snapshot()
        if code != "acceptance_hard_timeout":
            state = host.microphone.snapshot()
            code = state["reason_code"]
    except Exception as error:
        code = (error.code if isinstance(error, AcceptanceError) else "acceptance_trial_failed")
    finally:
        if host is not None:
            try:
                host.close()
            except Exception:
                pass
            cleanup = host.cleanup_snapshot()
            cleanup_ok = cleanup.get("resources_cleaned") is True
        else:
            voice.close(); client.close()
        for tap in taps:
            try:
                cleanup_ok = tap.close() is not False and cleanup_ok
            except Exception:
                cleanup_ok = False
    pcm = evidence.snapshot()
    wav = dict(saved=False, decoded=False)
    if wav_path.exists():
        try:
            with wave.open(str(wav_path), "rb") as recorded:
                payload_bytes = len(recorded.readframes(recorded.getnframes()))
                wav = dict(saved=True, decoded=True, frames=recorded.getnframes(),
                    sample_rate=recorded.getframerate(), channels=recorded.getnchannels(),
                    sample_width=recorded.getsampwidth(), file=wav_path.name,
                    sha256=hashlib.sha256(wav_path.read_bytes()).hexdigest())
                wav["decoded"] = (payload_bytes == wav["frames"] * wav["channels"] * wav["sample_width"]
                    and payload_bytes == pcm["byte_count"] and wav["sample_rate"] == 16000
                    and wav["channels"] == 1 and wav["sample_width"] == 2)
        except (OSError, EOFError, wave.Error):
            wav = dict(saved=True, decoded=False)
    valid = (pcm["frames"] > 0 and not pcm["all_zero"] and wav["decoded"] and cleanup_ok
             and pcm["sequence_errors"] == pcm["frame_length_errors"] == pcm["timestamp_errors"] == 0
             and client.attempts == len(voice_attempts) == 0 and len(taps) == 1)
    status = ("MIC_DEVICE_PASS" if valid else "MIC_ALL_ZERO_NEEDS_DIAGNOSIS" if pcm["all_zero"]
              else "MIC_DEVICE_NOT_VERIFIED")
    if _source_factory is not None:
        status = {"MIC_DEVICE_PASS": "OFFLINE_CAPTURE_PASS",
                  "MIC_ALL_ZERO_NEEDS_DIAGNOSIS": "OFFLINE_ALL_ZERO_NEEDS_DIAGNOSIS",
                  "MIC_DEVICE_NOT_VERIFIED": "OFFLINE_CAPTURE_NOT_VERIFIED"}[status]
    source_status = {}
    if taps:
        snapshot = getattr(taps[0].source, "snapshot", None)
        if callable(snapshot):
            raw = snapshot()
            source_status = {key: raw[key] for key in ("state", "reason_code", "microphone_open", "captured_bytes",
                "max_bytes", "planned_end", "discarded_partial_bytes") if key in raw}
    capture_outcome = capture_outcomes[-1] if capture_outcomes else dict(status="unknown", code="not_observed")
    capture_code = capture_outcome["code"]
    ptt_outcome = {"max_duration": "PLANNED_MAX_DURATION", "no_speech": "VAD_NO_SPEECH",
                   "silence": "VAD_SILENCE_END", "user_cancelled": "EXPLICIT_CANCELLED",
                   "ptt_released": "MANUAL_RELEASE", "max_buffer": "PCM_BUFFER_LIMIT"}.get(capture_code, "CAPTURE_ERROR")
    if ptt_outcome == "EXPLICIT_CANCELLED" and cancelled and timed_out:
        ptt_outcome = "BOUNDED_DEADLINE_CANCELLED"
    result = dict(version=1, status=status, trial=trial, code=code, plan=plan,
        source_kind="INJECTED_TEST_BOUNDARY" if _source_factory is not None else "PRODUCTION_MIC_SOURCE",
        physical_microphone="NOT_RUN" if _source_factory is not None else "LOCAL_PCM_OBSERVED" if pcm["frames"] else "NOT_VERIFIED",
        elapsed_seconds=max(0., _clock()-began), cancelled=cancelled, deadline_reached=timed_out,
        source_opens=len(taps), pcm=pcm, wav=wav, resources_cleaned=cleanup_ok,
        wake_off_before_ptt=wake_off_before_ptt,
        source_status=source_status, ptt_status=dict(outcome=ptt_outcome, capture=capture_outcome,
            human_speech="NOT_RUN", local_transcript_sink_only=True,
            harness_cancellation="BOUNDED_DEADLINE_CANCELLED" if cancelled and timed_out else
                "EXPLICIT_CANCELLED" if cancelled else "NOT_REQUESTED"),
        microphone_closed=cleanup.get("microphone_closed", not taps),
        broker=cleanup.get("microphone_broker"), timings=diagnostics.get("timings", {}),
        local_empty_transcript_sink_calls=len(local_stt), paid_api_attempts=client.attempts,
        voice_or_speaker_attempts=len(voice_attempts), real_human_speech="NOT_RUN",
        human_vad_accuracy="NOT_RUN", loopback="NOT_EVALUATED_BY_MIC_HELPER",
        reopen="SEPARATE_TRIAL_COMPARE_PREVIOUS_CLEANUP" if trial == "reopen" else "NOT_APPLICABLE")
    with _private_file(report_path) as report:
        report.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="One bounded private microphone Host/PTT acceptance trial.")
    parser.add_argument("--live-acceptance", action="store_true")
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--trial", choices=("normal", "cancel", "reopen"), default="normal")
    parser.add_argument("--max-seconds", type=float, default=10.)
    parser.add_argument("--cancel-after", type=float, default=1.)
    parser.add_argument("--mic-device", default="plughw:CARD=Device,DEV=0")
    parser.add_argument("--vad-mode", choices=("rms", "webrtc"), default="rms")
    parser.add_argument("--ready-file", type=Path, help="private first-PCM metadata IPC; must be inside output directory")
    args = parser.parse_args(argv)
    stopped = Event()
    previous = {}
    if args.live_acceptance:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda *unused: stopped.set())
    try:
        result = run_microphone_acceptance(args.output_directory, live_acceptance=args.live_acceptance,
            trial=args.trial, max_seconds=args.max_seconds, cancel_after=args.cancel_after,
            mic_device=args.mic_device, stop_requested=stopped, ready_file=args.ready_file,
            vad_mode=args.vad_mode)
        if not args.live_acceptance:
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        else:
            print(json.dumps({name: result[name] for name in (
                "status", "trial", "code", "elapsed_seconds", "cancelled", "deadline_reached",
                "source_opens", "resources_cleaned", "paid_api_attempts", "voice_or_speaker_attempts",
                "ptt_status", "source_status", "wake_off_before_ptt")},
                ensure_ascii=False, allow_nan=False))
        return 0 if result["status"] in ("NOT_RUN", "MIC_DEVICE_PASS") else 2
    except AcceptanceError as error:
        print(json.dumps(dict(status="NOT_RUN", code=error.code)))
        return 2
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    raise SystemExit(main())
