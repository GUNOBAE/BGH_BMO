"""Bounded local manifest validation using pinned no-follow file descriptors."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import unicodedata
import wave

from ..contracts import FeatureError, valid_id


MAX_ASSET_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_ITEMS = 128
MEDIA_TYPES = frozenset({"audio/wav", "audio/mpeg", "audio/ogg", "audio/flac", "video/mp4", "video/webm"})
_FIELDS = frozenset({"id", "title", "source", "path", "sha256", "media_type", "duration", "cue_path", "cue_sha256"})
_REQUIRED = frozenset({"id", "title", "source", "path", "sha256", "media_type"})


def _relative(value):
    if (type(value) is not str or not 1 <= len(value) <= 512 or value.startswith("/")
            or any(part in ("", ".", "..") for part in value.split("/"))
            or any(c in value for c in "\\:?#%;`$|&<>()")
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise FeatureError("invalid_media_path")
    return value


def _digest(value):
    if type(value) is not str or re.fullmatch("[a-f0-9]{64}", value) is None:
        raise FeatureError("invalid_media_hash")
    return value


@dataclass(frozen=True, slots=True)
class MediaAsset:
    id: str
    title: str
    source: str
    path: str
    sha256: str
    media_type: str
    duration: float | None = None
    cue_path: str | None = None
    cue_sha256: str | None = None

    def __post_init__(self):
        if (not valid_id(self.id) or type(self.title) is not str or not self.title.strip()
                or len(self.title) > 160 or type(self.source) is not str or not self.source.strip()
                or len(self.source) > 240 or "://" in self.source
                or any(unicodedata.category(c).startswith("C") for c in self.title + self.source)
                or self.media_type not in MEDIA_TYPES):
            raise FeatureError("invalid_media_manifest")
        _relative(self.path); _digest(self.sha256)
        if self.duration is not None and (type(self.duration) not in (int, float)
                or not math.isfinite(self.duration) or not 0 < self.duration <= 7200):
            raise FeatureError("invalid_media_duration")
        if (self.cue_path is None) != (self.cue_sha256 is None):
            raise FeatureError("invalid_media_cue")
        if self.cue_path is not None:
            _relative(self.cue_path); _digest(self.cue_sha256)


def _open_root(root):
    try:
        root = Path(root)
    except (TypeError, ValueError):
        raise FeatureError("invalid_content_root") from None
    if not root.is_absolute() or ".." in root.parts:
        raise FeatureError("invalid_content_root")
    fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open("/", flags)
        for part in root.parts[1:]:
            following = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd
    except OSError:
        if fd is not None:
            os.close(fd)
        raise FeatureError("unsafe_content_root") from None


def _verified_fd(root, path, digest, *, limit=MAX_ASSET_BYTES):
    _relative(path); _digest(digest)
    parent, result = _open_root(root), None
    try:
        parts = path.split("/")
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            os.close(parent)
            parent = following
        result = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(result)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
            raise FeatureError("unsafe_media_file")
        sha, total = hashlib.sha256(), 0
        while True:
            chunk = os.read(result, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise FeatureError("media_too_large")
            sha.update(chunk)
        after = os.fstat(result)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise FeatureError("media_changed")
        if sha.hexdigest() != digest:
            raise FeatureError("media_hash_mismatch")
        os.lseek(result, 0, os.SEEK_SET)
        value, result = result, None
        return value
    except FileNotFoundError:
        raise FeatureError("asset_pending") from None
    except OSError:
        raise FeatureError("unsafe_media_file") from None
    finally:
        os.close(parent)
        if result is not None:
            os.close(result)


class AssetHandle:
    """Own a verified descriptor until playback stops; never expose its path."""
    def __init__(self, root, asset):
        if type(asset) is not MediaAsset:
            raise FeatureError("invalid_media_manifest")
        self.asset, self.fd, self.cues = asset, None, ()
        self.duration = asset.duration
        try:
            self.fd = _verified_fd(root, asset.path, asset.sha256)
            if asset.media_type == "audio/wav":
                with os.fdopen(os.dup(self.fd), "rb") as stream, wave.open(stream, "rb") as wav:
                    if (wav.getcomptype() != "NONE" or wav.getnchannels() not in (1, 2)
                            or wav.getsampwidth() not in (1, 2, 3, 4) or wav.getframerate() <= 0
                            or wav.getnframes() <= 0):
                        raise FeatureError("invalid_media_wav")
                    actual = wav.getnframes() / wav.getframerate()
                    data = wav.readframes(wav.getnframes())
                    if len(data) != wav.getnframes() * wav.getnchannels() * wav.getsampwidth() or actual > 7200:
                        raise FeatureError("invalid_media_wav")
                    if self.duration is not None and abs(actual - self.duration) > .02:
                        raise FeatureError("media_duration_mismatch")
                    self.duration = actual
                os.lseek(self.fd, 0, os.SEEK_SET)
            if asset.cue_path is not None:
                cue_fd = _verified_fd(root, asset.cue_path, asset.cue_sha256, limit=1024 * 1024)
                with os.fdopen(cue_fd, "rb") as stream:
                    cues = json.load(stream)
                from ...integration_face import MOUTHS
                if type(cues) is not list or len(cues) > 10000:
                    raise FeatureError("invalid_media_cue")
                previous = 0
                for cue in cues:
                    if (type(cue) is not dict or set(cue) != {"time", "mouth"}
                            or type(cue["time"]) not in (int, float) or not math.isfinite(cue["time"])
                            or not previous <= cue["time"] <= (self.duration or 7200)
                            or type(cue["mouth"]) is not str or cue["mouth"] not in MOUTHS):
                        raise FeatureError("invalid_media_cue")
                    previous = cue["time"]
                self.cues = tuple(dict(cue) for cue in cues)
        except (OSError, ValueError, TypeError, EOFError, wave.Error):
            self.close()
            raise FeatureError("invalid_media_file") from None
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def import_manifest(payload, *, content_root):
    """Validate the whole manifest atomically; no file copy, URL or command."""
    if (type(payload) is not dict or set(payload) != {"version", "items"}
            or type(payload["version"]) is not int or payload["version"] != 1
            or type(payload["items"]) is not list or len(payload["items"]) > MAX_MANIFEST_ITEMS):
        raise FeatureError("invalid_media_manifest")
    assets = []
    for value in payload["items"]:
        if type(value) is not dict or not _REQUIRED <= set(value) <= _FIELDS:
            raise FeatureError("invalid_media_manifest")
        try:
            asset = MediaAsset(**value)
        except (TypeError, ValueError):
            raise FeatureError("invalid_media_manifest") from None
        if any(previous.id == asset.id for previous in assets):
            raise FeatureError("duplicate_media_id")
        with AssetHandle(content_root, asset):
            pass
        assets.append(asset)
    return tuple(assets)
