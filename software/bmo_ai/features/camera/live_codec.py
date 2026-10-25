"""실제 PNG 픽셀의 무손실 DIB AVI 포장. 합성 색/16×16 fixture를 사용하지 않는다."""
import struct
import zlib

from ..contracts import FeatureError
from ..media.video_fixture import MAX_VIDEO_BYTES, _chunk, _chunks
from .codec import validate_png


def png_to_dib(payload):
    width, height = validate_png(payload)
    if width > 640 or height > 480:
        raise FeatureError('camera_video_frame_limit')
    # BMO의 RGB888 capture_file PNG 경로에 한정한다. 임의 영상 포맷 변환기가 아니다.
    if payload[25] != 2:
        raise FeatureError('camera_video_rgb_required')
    offset, compressed = 8, bytearray()
    while offset < len(payload):
        length = struct.unpack_from('!I', payload, offset)[0]
        if payload[offset + 4:offset + 8] == b'IDAT':
            compressed.extend(payload[offset + 8:offset + 8 + length])
        offset += 12 + length
    scanlines = zlib.decompress(bytes(compressed))  # validate_png already bounded/checked CRC+inflate.
    stride = width * 3
    previous = bytearray(stride)
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        kind = scanlines[start]
        row = bytearray(scanlines[start + 1:start + stride + 1])
        for x in range(stride):
            left = row[x - 3] if x >= 3 else 0
            up = previous[x]
            upper_left = previous[x - 3] if x >= 3 else 0
            if kind == 1:
                predictor = left
            elif kind == 2:
                predictor = up
            elif kind == 3:
                predictor = (left + up) // 2
            elif kind == 4:
                base = left + up - upper_left
                distances = (abs(base - left), abs(base - up), abs(base - upper_left))
                predictor = (left, up, upper_left)[distances.index(min(distances))]
            else:
                predictor = 0
            row[x] = (row[x] + predictor) & 255
        bgr = bytearray(stride)
        bgr[0::3], bgr[1::3], bgr[2::3] = row[2::3], row[1::3], row[0::3]
        rows.append(bytes(bgr) + b'\0' * (-stride % 4))
        previous = row
    return width, height, b''.join(reversed(rows))


def rgb_avi_bytes(frames, *, width, height, fps):
    if (any(type(value) is not int for value in (width, height, fps))
            or not 1 <= width <= 640 or not 1 <= height <= 480 or not 1 <= fps <= 4
            or not isinstance(frames, (tuple, list)) or not 1 <= len(frames) <= 120):
        raise FeatureError('invalid_camera_video')
    count = len(frames)
    size = (width * 3 + 3) // 4 * 4 * height
    if any(type(frame) is not bytes or len(frame) != size for frame in frames):
        raise FeatureError('invalid_camera_video')
    if size * count + 8192 > MAX_VIDEO_BYTES:
        raise FeatureError('video_byte_limit')
    main = struct.pack('<14I', round(1000000 / fps), size * fps, 0, 0x10, count, 0,
                       1, size, width, height, 0, 0, 0, 0)
    track = struct.pack('<4s4sIHH8I4h', b'vids', b'DIB ', 0, 0, 0, 0, 1, fps, 0,
                        count, size, 0xffffffff, 0, 0, 0, width, height)
    bitmap = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0, size, 0, 0, 0, 0)
    header = _chunk(b'LIST', b'hdrl' + _chunk(b'avih', main)
                    + _chunk(b'LIST', b'strl' + _chunk(b'strh', track) + _chunk(b'strf', bitmap)))
    movi, indices, offset = [], [], 4
    for frame in frames:
        chunk = _chunk(b'00db', frame)
        movi.append(chunk)
        indices.append(struct.pack('<4sIII', b'00db', 0x10, offset, size))
        offset += len(chunk)
    body = b'AVI ' + header + _chunk(b'LIST', b'movi' + b''.join(movi)) + _chunk(b'idx1', b''.join(indices))
    return b'RIFF' + struct.pack('<I', len(body)) + body


def validate_rgb_avi(raw):
    """우리가 생성한 실제 픽셀 DIB subset의 전체 header·frame·index를 재검증한다."""
    try:
        if (type(raw) is not bytes or not 200 <= len(raw) <= MAX_VIDEO_BYTES
                or raw[:4] != b'RIFF' or raw[8:12] != b'AVI '
                or struct.unpack_from('<I', raw, 4)[0] + 8 != len(raw)):
            raise ValueError()
        top = _chunks(raw[12:])
        if len(top) != 3 or [tag for tag, _ in top] != [b'LIST', b'LIST', b'idx1']:
            raise ValueError()
        header = _chunks(top[0][1][4:])
        if top[0][1][:4] != b'hdrl' or top[1][1][:4] != b'movi':
            raise ValueError()
        if len(header) != 2 or [tag for tag, _ in header] != [b'avih', b'LIST'] or header[1][1][:4] != b'strl':
            raise ValueError()
        stream = _chunks(header[1][1][4:])
        if len(stream) != 2 or [tag for tag, _ in stream] != [b'strh', b'strf']:
            raise ValueError()
        main = struct.unpack('<14I', header[0][1])
        track = struct.unpack('<4s4sIHH8I4h', stream[0][1])
        width, height, count, fps = main[8], main[9], main[4], track[7]
        frames = _chunks(top[1][1][4:])
        if len(frames) != count or any(tag != b'00db' for tag, _ in frames):
            raise ValueError()
        expected = rgb_avi_bytes([frame for _, frame in frames], width=width, height=height, fps=fps)
        if expected != raw:
            raise ValueError()
        return {'width': width, 'height': height, 'frames': count, 'fps': fps,
                'duration': count / fps, 'audio_streams': 0, 'codec': 'uncompressed_dib_rgb'}
    except (ValueError, TypeError, struct.error, IndexError, FeatureError):
        raise FeatureError('invalid_camera_video') from None
