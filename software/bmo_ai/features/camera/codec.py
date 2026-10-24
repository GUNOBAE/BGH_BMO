"""Bounded standard-library PNG fixture/validation; no image or cloud backend."""
import struct
import zlib

from ..contracts import FeatureError
from .limits import MAX_DECODED_BYTES, MAX_IMAGE_SIDE, MAX_PNG_BYTES


def png_bytes(width=320, height=240, color=(42, 150, 112)):
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value))
    raw = b''.join(b'\x00' + bytes(color) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def validate_png(payload):
    """Check CRC, dimensions, compressed size and full RGB/RGBA scanline data."""
    if type(payload) is not bytes or len(payload) > MAX_PNG_BYTES or payload[:8] != b'\x89PNG\r\n\x1a\n':
        raise FeatureError('invalid_photo')
    offset, width, height, channels, compressed, ended = 8, None, None, None, bytearray(), False
    while offset + 12 <= len(payload):
        length = struct.unpack_from('!I', payload, offset)[0]
        kind = payload[offset + 4:offset + 8]
        end = offset + 8 + length
        if end + 4 > len(payload):
            raise FeatureError('invalid_photo')
        body = payload[offset + 8:end]
        crc = struct.unpack_from('!I', payload, end)[0]
        if zlib.crc32(kind + body) != crc:
            raise FeatureError('invalid_photo')
        if width is None:
            if kind != b'IHDR' or length != 13:
                raise FeatureError('invalid_photo')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('!2I5B', body)
            if not (1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE and depth == 8 and color in (2, 6)
                    and compression == filtering == interlace == 0):
                raise FeatureError('invalid_photo')
            channels = 3 if color == 2 else 4
        elif kind == b'IHDR':
            raise FeatureError('invalid_photo')
        elif kind == b'IDAT':
            compressed.extend(body)
        elif kind == b'IEND':
            if length or end + 4 != len(payload):
                raise FeatureError('invalid_photo')
            ended = True
            break
        offset = end + 4
    if not ended or not compressed:
        raise FeatureError('invalid_photo')
    expected = height * (1 + width * channels)
    if expected > MAX_DECODED_BYTES:
        raise FeatureError('invalid_photo')
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(bytes(compressed), expected + 1)
    except zlib.error:
        raise FeatureError('invalid_photo') from None
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise FeatureError('invalid_photo')
    stride = 1 + width * channels
    if any(raw[row * stride] > 4 for row in range(height)):
        raise FeatureError('invalid_photo')
    return width, height
