"""Offline numeric evidence for one local fixed-tone/microphone loopback.

Never plays, records, sends audio, changes gain or approves human listening.
The declared spectral thresholds are evidence gates, not a speech/wake model.
"""
import math
from pathlib import Path
import struct
import wave

from .microphone import AcceptanceError


def _read(path):
    path = Path(path)
    if path.stat().st_size > 321000:
        raise AcceptanceError("loopback_wav_limit")
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(),source.getnchannels(),source.getsampwidth(),source.getcomptype()) != (16000,1,2,"NONE"):
            raise AcceptanceError("loopback_format_invalid")
        payload = source.readframes(source.getnframes())
        if len(payload) != source.getnframes()*2:
            raise AcceptanceError("loopback_wav_incomplete")
    return [value[0]/32768 for value in struct.iter_unpack("<h",payload)]


def _windows(samples):
    size, hop, rate = 1024, 256, 16000
    weights = [.5-.5*math.cos(2*math.pi*i/(size-1)) for i in range(size)]
    scale = sum(weights)**2
    frequencies = (1180,1200,1220,900,1000,1400,1500)
    basis = {f: [(math.cos(2*math.pi*f*i/rate),math.sin(2*math.pi*f*i/rate))
                 for i in range(size)] for f in frequencies}
    values=[]
    for start in range(0,len(samples)-size+1,hop):
        block=[x*w for x,w in zip(samples[start:start+size],weights)]
        powers=[]
        for f in frequencies:
            real=sum(x*c for x,(c,s) in zip(block,basis[f]))
            imaginary=sum(x*s for x,(c,s) in zip(block,basis[f]))
            powers.append((real*real+imaginary*imaginary)/scale)
        values.append((max(powers[:3]),sum(powers[3:])/4))
    return values


def analyze_loopback(baseline_wav, recorded_wav):
    baseline, observed = _read(baseline_wav), _read(recorded_wav)
    result=dict(frequency_hz=1200, tone_tolerance_hz=20, window_samples=1024, hop_samples=256,
        minimum_baseline_increase_db=6.,minimum_neighbor_ratio_db=9.,minimum_consecutive_windows=3,
        speaker_command="SEPARATE_RESULT_REQUIRED", human_listening="NOT_APPROVED",
        acoustic_latency_ms="UNMEASURED",wake_model_inference="NOT_RUN",audio_uploaded=False)
    if not baseline or not observed or not any(baseline) or not any(observed):
        return dict(result,status="LOOPBACK_NOT_VERIFIED",code="empty_or_all_zero_pcm")
    left,right=_windows(baseline),_windows(observed)
    if not left or not right:
        return dict(result,status="LOOPBACK_NOT_VERIFIED",code="insufficient_pcm")
    target=sorted(x for x,_ in left)
    floor=max(1e-12,target[len(target)//2])
    streak=longest=0
    gains=[];ratios=[]
    for power,neighbors in right:
        gain=10*math.log10(max(power,1e-20)/floor)
        ratio=10*math.log10(max(power,1e-20)/max(neighbors,1e-12))
        gains.append(gain);ratios.append(ratio)
        streak=streak+1 if gain>=6. and ratio>=9. else 0
        longest=max(longest,streak)
    return dict(result,status="LOOPBACK_PASS" if longest>=3 else "LOOPBACK_NOT_VERIFIED",
        code="fixed_tone_detected" if longest>=3 else "tone_not_distinct_from_baseline",
        baseline_frames=len(baseline),recorded_frames=len(observed),
        maximum_target_increase_db=round(max(gains),3),maximum_neighbor_ratio_db=round(max(ratios),3),
        maximum_consecutive_windows=longest, baseline_all_zero=False, recorded_all_zero=False)
