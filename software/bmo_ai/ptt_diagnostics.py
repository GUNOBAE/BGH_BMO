"""Explicit supervised ambient calibration; importing/--help opens no device.

Only bounded RMS statistics and a device fingerprint are saved, never PCM/WAV,
speech, credentials or device argv. This command never constructs an API client.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct

from .features.input.microphone import MicError, MicPcmSource


def _fingerprint(device):
    return hashlib.sha256(device.encode("utf-8")).hexdigest()


def load_ambient_profile(path, device):
    try:
        fd = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid() or info.st_size > 4096):
                raise ValueError()
            value = json.loads(source.read(4097))
        if (type(value) is not dict or set(value) != {
                "version", "device_fingerprint", "sample_rate", "channels", "rms", "samples", "supervised"}
                or value["version"] != 1 or value["device_fingerprint"] != _fingerprint(device)
                or value["sample_rate"] != 16000 or value["channels"] != 1
                or value["supervised"] is not True or type(value["samples"]) is not int
                or not 12800 <= value["samples"] <= 16000
                or type(value["rms"]) not in (int, float) or not math.isfinite(value["rms"])
                or not 0 <= value["rms"] <= .12):
            raise ValueError()
        return float(value["rms"])
    except (OSError, ValueError, TypeError, KeyError):
        raise MicError("ambient_profile_invalid") from None


def measure_ambient(source, *, device):
    """Caller must explicitly supply a supervised source. No opening on import."""
    samples = squares = 0
    frames = None
    try:
        frames = iter(source.frames("ambient:manual", generation=1, cancel_check=lambda: None))
        for frame in frames:
            if (type(frame.pcm) is not bytes or len(frame.pcm) % 2
                    or not 640 <= len(frame.pcm) <= 3200):
                raise MicError("pcm_audio_format_failed")
            values = tuple(s[0] for s in struct.iter_unpack("<h", frame.pcm))
            squares += sum(s * s for s in values)
            samples += len(values)
            if samples >= 12800:
                break
        if not 12800 <= samples <= 16000:
            raise MicError("ambient_insufficient_frames")
        rms = math.sqrt(squares / samples) / 32768
        if rms > .12:
            raise MicError("calibration_noisy")
        return dict(version=1, device_fingerprint=_fingerprint(device), sample_rate=16000,
                    channels=1, rms=round(rms, 8), samples=samples, supervised=True)
    finally:
        if frames is not None and hasattr(frames, "close"):
            frames.close()
        if source.close() is not True:
            raise MicError("pcm_cleanup_failed")


def save_ambient_profile(path, value):
    destination = Path(path).expanduser().absolute()
    # A new private directory/file only: never overwrite a prior profile.
    if not destination.parent.exists():
        destination.parent.mkdir(mode=0o700, parents=True)
    parent = destination.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) & 0o077):
        raise MicError("ambient_directory_private_required")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(value, output, allow_nan=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="버튼용 주변 소음 측정: 사용자 입회, 0.8초 조용히, PCM 저장 없음")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--supervised", action="store_true")
    parser.add_argument("--device", default="plughw:CARD=Device,DEV=0")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.calibrate or not args.supervised or args.output is None:
        parser.error("--calibrate --supervised --output PROFILE를 모두 명시해야 합니다")
    try:
        value = measure_ambient(MicPcmSource(args.device, live_opt_in=True, max_duration=2.), device=args.device)
        save_ambient_profile(args.output, value)
        print(json.dumps({"stage": "calibration", "code": "ambient_verified", "rms": value["rms"],
                          "sample_rate": 16000, "channels": 1, "pcm_saved": False}))
        return 0
    except Exception as error:
        code = error.code if isinstance(error, MicError) else "ambient_calibration_failed"
        print(json.dumps({"stage": "calibration", "code": code, "pcm_saved": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
