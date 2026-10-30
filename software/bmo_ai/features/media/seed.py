"""Explicit private Space Potatoes handoff adapter; never a runtime seed loader.

The supplied MEDIA_SEED.json is provenance, not an executable manifest. This
adapter recognizes the three reviewed source bytes by their independent SHA,
validates/decode-checks local streams, then publishes our normal library v2
entry. Archive Python/HTML is never run. Original inputs are never rewritten.
"""
from array import array
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import resource
import secrets
import stat
import subprocess
import sys
import tempfile
import wave
import zipfile

from ..contracts import FeatureError
from .assets import AssetHandle, MediaAsset, _digest, _verified_fd
from .library import _private_root, _root_path, _names, MAX_LIBRARY_ITEMS


SPACE_SEED_SHA256 = "5de21e50e275e37207f185247f7062f14bd58d46686eb92c8b7fd0f78ff2cde2"
SPACE_COMPONENTS = {
    "mp3": "3329f1d07a396d9baa9380b413870e3ac3e023e85e8df81f34ae7a7a8fded663",
    "face_wav": "e4b31cd56d3e795d721178e9d315c0438a6bdafba841bc69b9a76aacbb1750c6",
    "video": "c7b79e37712b41a66d96ee0331acd408aea3f6b7e2369e18c31bb7566b12038e",
}
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
MAX_MEDIA_SECONDS = 120.


def _sha(path):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 < before.st_size <= MAX_ARCHIVE_BYTES):
            raise FeatureError("unsafe_media_file")
        result, count = hashlib.sha256(), 0
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                count += len(block)
                if count > MAX_ARCHIVE_BYTES:
                    raise FeatureError("media_too_large")
                result.update(block)
        after = os.fstat(descriptor)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or count != before.st_size):
            raise FeatureError("media_changed")
        return result.hexdigest()
    except OSError:
        raise FeatureError("media_changed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _json(path, *, limit=2 * 1024 * 1024):
    if not 0 < path.stat().st_size <= limit:
        raise FeatureError("invalid_media_seed_metadata")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise FeatureError("invalid_media_seed_metadata")
            result[key] = value
        return result
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=unique)
    except (ValueError, UnicodeError):
        raise FeatureError("invalid_media_seed_metadata") from None


def _limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (48 * 1024 * 1024, 48 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))


def _run(command, *, timeout=20, pass_fds=()):
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=timeout, check=False, pass_fds=pass_fds,
            env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"}, preexec_fn=_limits)
    except (OSError, subprocess.TimeoutExpired):
        raise FeatureError("media_decode_unavailable") from None
    if result.returncode or len(result.stdout) > 65536:
        raise FeatureError("media_decode_failed")
    return result.stdout


def _probe(path, *, video):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        data = _run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-f", "mov" if video else "mp3",
            "-i", "/proc/self/fd/" + str(descriptor), "-show_entries",
            "stream=codec_type,codec_name,width,height,channels,sample_rate:format=duration", "-of", "json"], pass_fds=(descriptor,))
        payload = json.loads(data)
        streams, duration = payload["streams"], float(payload["format"]["duration"])
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        frames = [s for s in streams if s.get("codec_type") == "video"]
        if (not math.isfinite(duration) or not 0 < duration <= MAX_MEDIA_SECONDS or len(audio) != 1
                or int(audio[0]["channels"]) not in (1, 2) or not 8000 <= int(audio[0]["sample_rate"]) <= 96000):
            raise FeatureError("invalid_media_seed_stream")
        if video and (len(frames) != 1 or frames[0]["codec_name"] != "h264"
                or audio[0]["codec_name"] != "aac" or not 0 < frames[0]["width"] <= 1920
                or not 0 < frames[0]["height"] <= 1080):
            raise FeatureError("invalid_media_seed_stream")
        if not video and (frames or audio[0]["codec_name"] != "mp3"):
            raise FeatureError("invalid_media_seed_stream")
        return duration
    except (ValueError, KeyError, TypeError):
        raise FeatureError("invalid_media_seed_stream") from None
    finally:
        os.close(descriptor)


def _decode(path, output=None, *, video):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        args = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "2", "-protocol_whitelist", "file,pipe", "-f", "mov" if video else "mp3",
                "-i", "/proc/self/fd/" + str(descriptor)]
        if output is None:
            # Decode all the MP4, including its video stream, before importing.
            args += ["-t", "120", "-f", "null", "-"]
        else:
            args += ["-map", "0:a:0", "-vn", "-t", "120", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", "-f", "wav", "-n", str(output)]
        _run(args, pass_fds=(descriptor,))
    finally:
        os.close(descriptor)


def _wav(path):
    try:
        with wave.open(str(path), "rb") as source:
            channels, rate, frames = source.getnchannels(), source.getframerate(), source.getnframes()
            if (source.getcomptype() != "NONE" or source.getsampwidth() != 2 or channels not in (1, 2)
                    or not 8000 <= rate <= 96000 or not 0 < frames / rate <= MAX_MEDIA_SECONDS):
                raise FeatureError("invalid_media_wav")
            count = 0
            while block := source.readframes(4096):
                count += len(block)
            if count != frames * channels * 2:
                raise FeatureError("invalid_media_wav")
            return frames / rate, rate
    except (wave.Error, OSError, EOFError):
        raise FeatureError("invalid_media_wav") from None


def _energy_values(data, digest, duration):
    """Read only hash-bound sample-zero whole-mix data; never action cues."""
    if type(data) is not dict:
        return None
    source = data.get("source_wav_sha256", data.get("wav_sha256", data.get("audio_sha256")))
    if source is None and type(data.get("source")) is dict:
        source = data["source"].get("sha256")
    if source != digest:
        return None
    rows = data.get("frames", data.get("energy", data.get("envelope", data.get("values"))))
    step = data.get("step_seconds", data.get("hop_seconds", data.get("frame_seconds")))
    if step is None and type(data.get("window_ms")) in (int, float):
        step = data["window_ms"] / 1000
    if type(rows) is not list or not 1 <= len(rows) <= 10000:
        return None
    result = []
    for index, row in enumerate(rows):
        if type(row) is dict:
            when = row.get("time", row.get("t", row.get("time_seconds")))
            value = row.get("rms", row.get("energy", row.get("level")))
        elif type(row) in (int, float) and type(step) in (int, float) and 0 < step <= 1:
            when, value = index * step, row
        else:
            return None
        if (type(when) not in (int, float) or type(value) not in (int, float)
                or not math.isfinite(when) or not math.isfinite(value) or not 0 <= when <= duration
                or not 0 <= value <= 1 or index == 0 and when != 0
                or result and when <= result[-1][0]):
            return None
        result.append((float(when), float(value)))
    return result


def _measured_energy(path):
    result = []
    with wave.open(str(path), "rb") as wav:
        rate, channels, step = wav.getframerate(), wav.getnchannels(), max(1, round(wav.getframerate() * .02))
        frame = 0
        while raw := wav.readframes(step):
            samples = array("h", raw)
            if sys.byteorder != "little":
                samples.byteswap()
            rms = math.sqrt(sum(float(value) ** 2 for value in samples) / len(samples)) / 32768.
            result.append((frame / rate, rms))
            frame += len(samples) // channels
    return result


def _mouths(values, duration):
    from ...integration_face import LADDER
    ordered = sorted(value for _, value in values if value > .0004)
    scale = max(.012, ordered[min(len(ordered) - 1, int(len(ordered) * .9))] if ordered else .05)
    floor = max(.001, scale * .035)
    thresholds = (.06, .12, .21, .33, .47, .62, .78, .91)
    cues, level, previous, last = [{"time": 0., "mouth": "closed"}], 0., "closed", -1.
    for when, rms in values:
        target = max(0., min(1., (rms - floor) / (scale - floor)))
        level += (.42 if target > level else .24) * (target - level)
        mouth = LADDER[next((i for i, threshold in enumerate(thresholds) if level < threshold), 8)]
        if mouth != previous and (when - last >= .05 or mouth == "closed"):
            cues.append({"time": when, "mouth": mouth})
            previous, last = mouth, when
    cues.append({"time": duration, "mouth": "closed"})
    return cues


@dataclass(slots=True)
class MediaSeedReview:
    archive_sha256: str
    duration: float
    video_duration: float
    energy_status: str
    _files: dict = field(repr=False)
    _hashes: dict = field(repr=False)

    def public(self):
        return dict(status="AVAILABLE_FOR_REVIEW", media_id="space_potatoes", variants=["face", "video"],
                    archive_sha256=self.archive_sha256, face_duration=self.duration, video_duration=self.video_duration,
                    source="user_provided", public_distribution_rights="NOT_VERIFIED",
                    cue_kind="whole_mix_energy_approximation", energy_source=self.energy_status,
                    playback="NOT_RUN", runtime_enabled=False, audio_gain_modified=False)


@contextmanager
def review_media_seed(source, *, expected_sha256=SPACE_SEED_SHA256, component_hashes=None):
    """Validate locally, producing temporary snapshots only until context exit.

    component_hashes is a test/integration seam for separately reviewed input;
    the CLI always pins the published three component hashes. Nothing from
    untrusted handoff JSON can change expected hashes, IDs, commands or paths.
    """
    _digest(expected_sha256)
    components = dict(SPACE_COMPONENTS if component_hashes is None else component_hashes)
    if set(components) != {"mp3", "face_wav", "video"} or len(set(components.values())) != 3:
        raise FeatureError("invalid_media_seed")
    for value in components.values():
        _digest(value)
    path = _root_path(source)
    descriptor = _verified_fd(path.parent, path.name, expected_sha256, limit=MAX_ARCHIVE_BYTES)
    try:
        with tempfile.TemporaryDirectory(prefix="bmo-private-media-seed-") as folder:
            temporary = Path(folder)
            files, hashes, names, metadata, energy_paths = {}, {}, set(), {}, []
            with os.fdopen(os.dup(descriptor), "rb") as raw, zipfile.ZipFile(raw) as archive:
                members = archive.infolist()
                if not 1 <= len(members) <= 512:
                    raise FeatureError("invalid_media_seed")
                total = 0
                for index, member in enumerate(members):
                    name = member.filename
                    parts = PurePosixPath(name).parts
                    if (not name or name.startswith("/") or "\\" in name or "\x00" in name
                            or any(part in (".", "..", "") for part in name.rstrip("/").split("/"))
                            or name.casefold() in names
                            or stat.S_ISLNK(member.external_attr >> 16) or member.flag_bits & 1):
                        raise FeatureError("unsafe_media_seed_archive")
                    names.add(name.casefold())
                    if member.is_dir():
                        continue
                    total += member.file_size
                    if (not 0 <= member.file_size <= MAX_ARCHIVE_BYTES or total > MAX_EXTRACTED_BYTES
                            or member.file_size > max(1, member.compress_size) * 1000):
                        raise FeatureError("media_seed_size_limit")
                    destination = temporary / ("source-" + str(index))
                    sha, count = hashlib.sha256(), 0
                    with archive.open(member) as original, open(destination, "xb") as target:
                        os.chmod(destination, 0o600)
                        while block := original.read(65536):
                            count += len(block)
                            if count > member.file_size:
                                raise FeatureError("invalid_media_seed")
                            sha.update(block); target.write(block)
                    if count != member.file_size:
                        raise FeatureError("invalid_media_seed")
                    digest = sha.hexdigest()
                    for role, expected in components.items():
                        if digest == expected:
                            if role in files:
                                raise FeatureError("duplicate_media_seed_component")
                            files[role], hashes[role] = destination, digest
                    basename = parts[-1]
                    if basename in ("MEDIA_SEED.json", "FILES_SHA256.json"):
                        if basename in metadata:
                            raise FeatureError("invalid_media_seed_metadata")
                        metadata[basename] = destination
                        value = _json(destination)
                        if type(value) not in (dict, list):
                            raise FeatureError("invalid_media_seed_metadata")
                    elif basename.lower().endswith(".json") and "energy" in basename.lower():
                        energy_paths.append(destination)
            if set(files) != set(components) or set(metadata) != {"MEDIA_SEED.json", "FILES_SHA256.json"}:
                raise FeatureError("media_seed_component_missing")
            duration, _ = _wav(files["face_wav"])
            video_duration = _probe(files["video"], video=True)
            mp3_duration = _probe(files["mp3"], video=False)
            if abs(duration - video_duration) > .25 or abs(duration - mp3_duration) > .25:
                raise FeatureError("media_seed_duration_mismatch")
            _decode(files["video"], video=True)
            _decode(files["mp3"], video=False)
            decoded = temporary / "video-audio.wav"
            _decode(files["video"], decoded, video=True)
            audio_duration, _ = _wav(decoded)
            if abs(audio_duration - video_duration) > .25:
                raise FeatureError("media_seed_duration_mismatch")
            files["video_audio"], hashes["video_audio"] = decoded, _sha(decoded)
            values, energy_status = None, "MEASURED_FROM_VERIFIED_WHOLE_MIX_WAV"
            for candidate in energy_paths:
                data = _json(candidate)
                proposed = _energy_values(data, hashes["face_wav"], duration)
                if proposed is not None and values is None:
                    values, energy_status = proposed, "HASH_BOUND_PREPARED_WHOLE_MIX_ENERGY"
                # Preserve supplied candidate bytes even if its handoff layout
                # differs; it cannot become unverified timed executable cues.
                role = "energy_source_" + str(len([k for k in files if k.startswith("energy_source_")]))
                files[role], hashes[role] = candidate, _sha(candidate)
            values = _measured_energy(files["face_wav"]) if values is None else values
            cues = temporary / "mouth-cues.json"
            cues.write_text(json.dumps(_mouths(values, duration), separators=(",", ":")))
            cues.chmod(0o600)
            files["cues"], hashes["cues"] = cues, _sha(cues)
            for basename, value in metadata.items():
                role = "seed_metadata" if basename == "MEDIA_SEED.json" else "seed_checksums"
                files[role], hashes[role] = value, _sha(value)
            yield MediaSeedReview(expected_sha256, duration, audio_duration, energy_status, files, hashes)
    except FeatureError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, EOFError):
        raise FeatureError("media_seed_review_failed") from None
    finally:
        os.close(descriptor)


def install_media_seed(review, content_root, *, private_use_confirmed=False):
    """Publish exactly one song with two variants, no player or auto approval.

    private_use_confirmed is permission for local import, not an invented legal
    ownership or public redistribution licence. Existing entries are untouched.
    """
    if type(review) is not MediaSeedReview or private_use_confirmed is not True:
        raise FeatureError("media_private_use_confirmation_required")
    names = {}
    for role, source in review._files.items():
        if _sha(source) != review._hashes[role]:
            raise FeatureError("media_changed")
        suffix = ".wav" if role in ("face_wav", "video_audio") else ".mp4" if role == "video" else ".mp3" if role == "mp3" else ".json"
        prefix = "space_potatoes" if role == "face_wav" else "space_potatoes-" + role
        names[role] = prefix + "-" + review._hashes[role][:12] + suffix
    asset = MediaAsset("space_potatoes", "Space Potato Song (Fresh Potatoes)", "user_provided",
        names["face_wav"], review._hashes["face_wav"], "audio/wav", review.duration,
        names["cues"], review._hashes["cues"], "whole_mix_energy_approximation",
        names["video"], review._hashes["video"], names["video_audio"], review._hashes["video_audio"], review.video_duration)
    metadata = json.dumps(dict(version=2, asset=asdict(asset), private_use_confirmed=True, provenance=dict(
        source="user_provided", public_distribution_rights="NOT_VERIFIED", archive_sha256=review.archive_sha256,
        source_hashes=review._hashes, energy_source=review.energy_status,
        video_audio_source="verified_mp4_embedded_audio", exact_lyric_lipsync=False,
        gain_baked=1.0, original_inputs_modified=False)), sort_keys=True).encode()
    root = _private_root(content_root, create=True)
    lock, staged, published = None, [], []
    try:
        lock = os.open(".media-import.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=root)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise FeatureError("unsafe_media_library")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FeatureError("media_import_busy") from None
        if len(_names(root)) >= MAX_LIBRARY_ITEMS:
            raise FeatureError("media_library_capacity")
        all_names = list(names.values()) + ["space_potatoes.song.json"]
        for name in all_names:
            try:
                os.stat(name, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FeatureError("media_import_exists")
        need = sum(path.stat().st_size for path in review._files.values()) + len(metadata) + 1024 * 1024
        if os.fstatvfs(root).f_bavail * os.fstatvfs(root).f_frsize < need:
            raise FeatureError("media_disk_space_low")
        payloads = [(names[role], source) for role, source in review._files.items()]
        payloads.append(("space_potatoes.song.json", metadata))
        for name, source in payloads:
            stage = ".media-stage-" + secrets.token_hex(12)
            fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
            staged.append(stage)
            with os.fdopen(fd, "wb") as target:
                if type(source) is bytes:
                    target.write(source)
                else:
                    role = next(role for role, filename in names.items() if filename == name)
                    pinned = _verified_fd(source.parent, source.name, review._hashes[role], limit=MAX_ARCHIVE_BYTES)
                    with os.fdopen(pinned, "rb") as original:
                        for block in iter(lambda: original.read(65536), b""):
                            target.write(block)
                target.flush(); os.fsync(target.fileno())
            os.link(stage, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
            published.append((name, os.stat(name, dir_fd=root, follow_symlinks=False).st_ino))
            os.unlink(stage, dir_fd=root); staged.remove(stage)
        os.fsync(root)
        with AssetHandle(content_root, asset):
            pass
        with AssetHandle(content_root, asset.playback_asset("video")):
            pass
        return {**review.public(), "status": "IMPORTED_FOR_REVIEW", "rollback": "retire_manifest_preserve_media"}
    except BaseException as error:
        for name, inode in reversed(published):
            try:
                if os.stat(name, dir_fd=root, follow_symlinks=False).st_ino == inode:
                    os.unlink(name, dir_fd=root)
            except OSError:
                pass
        if isinstance(error, (FeatureError, KeyboardInterrupt, SystemExit)):
            raise
        raise FeatureError("media_import_failed") from None
    finally:
        for stage in staged:
            try:
                os.unlink(stage, dir_fd=root)
            except OSError:
                pass
        if lock is not None:
            os.close(lock)
        os.close(root)
