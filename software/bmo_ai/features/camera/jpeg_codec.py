"""Bounded JPEG structure/geometry check without decoding every preview frame."""
import struct

from ..contracts import FeatureError
from .limits import MAX_DECODED_BYTES, MAX_IMAGE_SIDE

MAX_PREVIEW_BYTES = 2 * 1024 * 1024
_SOF = frozenset((0xC0, 0xC1, 0xC2))


def validate_jpeg(payload):
    """Validate marker bounds and geometry; browser/OpenCV performs actual decode."""
    if (type(payload) is not bytes or not 16 <= len(payload) <= MAX_PREVIEW_BYTES
            or not payload.startswith(b'\xff\xd8') or not payload.endswith(b'\xff\xd9')):
        raise FeatureError('invalid_camera_preview')
    offset, dimensions = 2, None
    while offset + 4 <= len(payload):
        if payload[offset] != 0xFF:
            break
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            break
        marker = payload[offset]
        offset += 1
        if marker in (0x00, 0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            break
        if offset + 2 > len(payload):
            break
        length = struct.unpack_from('!H', payload, offset)[0]
        if length < 2 or offset + length > len(payload) - 2:
            break
        if marker in _SOF:
            if dimensions is not None or length < 8 or payload[offset + 2] != 8:
                break
            height, width = struct.unpack_from('!HH', payload, offset + 3)
            channels = payload[offset + 7]
            if not (1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE
                    and channels in (1, 3) and width * height * channels <= MAX_DECODED_BYTES
                    and length == 8 + channels * 3):
                break
            dimensions = width, height
        if marker == 0xDA:
            if dimensions is not None and length >= 6 and offset + length < len(payload) - 2:
                return dimensions
            break
        offset += length
    raise FeatureError('invalid_camera_preview')
