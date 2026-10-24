"""Generated uncompressed AVI + a silent fake timeline, never a real decoder.

Only the bounded DIB/24-bit AVI subset produced here is accepted. Real browser
codec output, user video playback and audible output remain unsupported/NOT_RUN.
"""
import hashlib
import os
import stat
import struct
import time

from ..contracts import FeatureError, valid_id
from .assets import MediaAsset, _open_root
from . import FakeMediaBackend


MAX_VIDEO_BYTES = 8 * 1024 * 1024


def _chunk(name, payload):
    return name + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def synthetic_avi_bytes(*, width=16, height=16, fps=2, frames=2):
    if (any(type(value) is not int for value in (width, height, fps, frames))
            or not 1 <= width <= 64 or not 1 <= height <= 64
            or not 1 <= fps <= 30 or not 1 <= frames <= 120):
        raise FeatureError("invalid_video_fixture")
    stride = (width * 3 + 3) // 4 * 4
    size = stride * height
    avih = struct.pack("<14I", round(1000000 / fps), size * fps, 0, 0x10, frames, 0,
                       1, size, width, height, 0, 0, 0, 0)
    strh = struct.pack("<4s4sIHH8I4h", b"vids", b"DIB ", 0, 0, 0,
                       0, 1, fps, 0, frames, size, 0xffffffff, 0, 0, 0, width, height)
    strf = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, size, 0, 0, 0, 0)
    header = _chunk(b"LIST", b"hdrl" + _chunk(b"avih", avih)
                    + _chunk(b"LIST", b"strl" + _chunk(b"strh", strh) + _chunk(b"strf", strf)))
    chunks, indices, offset = [], [], 4
    for index in range(frames):
        color = bytes((32 + index % 2 * 96, 96, 192 - index % 2 * 96))
        row = color * width + b"\0" * (stride - width * 3)
        frame = _chunk(b"00db", row * height)
        chunks.append(frame)
        indices.append(struct.pack("<4sIII", b"00db", 0x10, offset, size))
        offset += len(frame)
    body = b"AVI " + header + _chunk(b"LIST", b"movi" + b"".join(chunks)) + _chunk(b"idx1", b"".join(indices))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunks(raw):
    offset, result = 0, []
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise FeatureError("invalid_media_video")
        tag, size = raw[offset:offset + 4], struct.unpack_from("<I", raw, offset + 4)[0]
        end = offset + 8 + size
        if end + size % 2 > len(raw):
            raise FeatureError("invalid_media_video")
        result.append((tag, raw[offset + 8:end]))
        offset = end + size % 2
        if len(result) > 256:
            raise FeatureError("invalid_media_video")
    return result


def validate_avi_bytes(raw):
    try:
        if (type(raw) is not bytes or not 200 <= len(raw) <= MAX_VIDEO_BYTES
                or raw[:4] != b"RIFF" or raw[8:12] != b"AVI "
                or struct.unpack_from("<I", raw, 4)[0] + 8 != len(raw)):
            raise FeatureError("invalid_media_video")
        top = _chunks(raw[12:])
        if len(top) != 3 or [tag for tag, _ in top] != [b"LIST", b"LIST", b"idx1"]:
            raise FeatureError("unsupported_video_codec")
        hdrl, movi, index = top[0][1], top[1][1], top[2][1]
        if hdrl[:4] != b"hdrl" or movi[:4] != b"movi":
            raise FeatureError("invalid_media_video")
        header = _chunks(hdrl[4:])
        if len(header) != 2 or [tag for tag, _ in header] != [b"avih", b"LIST"] or header[1][1][:4] != b"strl":
            raise FeatureError("invalid_media_video")
        stream = _chunks(header[1][1][4:])
        if len(stream) != 2 or [tag for tag, _ in stream] != [b"strh", b"strf"]:
            raise FeatureError("invalid_media_video")
        main = struct.unpack("<14I", header[0][1])
        track = struct.unpack("<4s4sIHH8I4h", stream[0][1])
        bitmap = struct.unpack("<IiiHHIIiiII", stream[1][1])
        width, height, count, fps = main[8], main[9], main[4], track[7]
        if (track[:2] != (b"vids", b"DIB ") or bitmap[0] != 40 or bitmap[3:6] != (1, 24, 0)
                or not 1 <= width <= 64 or not 1 <= height <= 64
                or not 1 <= count <= 120 or not 1 <= fps <= 30 or track[6] != 1
                or track[9] != count or main[6] != 1 or bitmap[1:3] != (width, height)
                or main[0] != round(1000000 / fps)):
            raise FeatureError("unsupported_video_codec")
        size = (width * 3 + 3) // 4 * 4 * height
        # Accept only the complete header semantics emitted by our generator.
        # In particular dwStart/initial frames, disabled flags, time scales,
        # palette fields and reserved rectangle values cannot alter the fake
        # timeline while still being described as our synthetic subset.
        if (main != (round(1000000 / fps), size * fps, 0, 0x10, count, 0,
                     1, size, width, height, 0, 0, 0, 0)
                or track != (b"vids", b"DIB ", 0, 0, 0, 0, 1, fps, 0, count,
                             size, 0xffffffff, 0, 0, 0, width, height)
                or bitmap != (40, width, height, 1, 24, 0, size, 0, 0, 0, 0)):
            raise FeatureError("unsupported_video_codec")
        frames = _chunks(movi[4:])
        if len(frames) != count or any(tag != b"00db" or len(frame) != size for tag, frame in frames):
            raise FeatureError("invalid_media_video")
        if len(index) != count * 16:
            raise FeatureError("invalid_media_video")
        offset = 4
        for number in range(count):
            if struct.unpack_from("<4sIII", index, number * 16) != (b"00db", 0x10, offset, size):
                raise FeatureError("invalid_media_video")
            offset += 8 + size + size % 2
        return {"width": width, "height": height, "frames": count, "fps": fps,
                "duration": count / fps, "audio_streams": 0, "codec": "synthetic_uncompressed_dib",
                "actual_decoder_verified": False}
    except (struct.error, OverflowError, TypeError, ValueError, IndexError):
        raise FeatureError("invalid_media_video") from None


def validate_avi_fd(fd):
    if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size > MAX_VIDEO_BYTES:
        raise FeatureError("invalid_media_video")
    os.lseek(fd, 0, os.SEEK_SET)
    raw = b""
    while len(raw) <= MAX_VIDEO_BYTES:
        chunk = os.read(fd, min(65536, MAX_VIDEO_BYTES + 1 - len(raw)))
        if not chunk:
            break
        raw += chunk
    os.lseek(fd, 0, os.SEEK_SET)
    return validate_avi_bytes(raw)


def make_video_fixture(content_root, *, asset_id="fixture_video", **settings):
    if not valid_id(asset_id):
        raise FeatureError("invalid_video_fixture")
    raw = synthetic_avi_bytes(**settings)
    meta = validate_avi_bytes(raw)
    root, created = _open_root(content_root), False
    name = asset_id + ".avi"
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=root)
        created = True
        with os.fdopen(fd, "wb") as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        return MediaAsset(asset_id, "Synthetic uncompressed video", "generated_video_fixture", name,
                          hashlib.sha256(raw).hexdigest(), "video/x-msvideo", meta["duration"])
    except (OSError, ValueError):
        if created:
            os.unlink(name, dir_fd=root)
        raise FeatureError("video_fixture_failed") from None
    finally:
        os.close(root)


class FakeVideoTimelineBackend(FakeMediaBackend):
    """Explicit silent simulation; never opens a codec, browser or sound device."""
    supported_types = frozenset({"video/x-msvideo"})

    def __init__(self, *, clock=time.monotonic, pausable=True):
        super().__init__(clock=clock, pausable=pausable)

    @property
    def capabilities(self):
        return (*super().capabilities, "synthetic_video_timeline")

    def start(self, handle):
        if handle.asset.media_type not in self.supported_types:
            raise FeatureError("unsupported_video_codec")
        validate_avi_fd(handle.fd)
        super().start(handle)
