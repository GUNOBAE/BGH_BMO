"""Silent, bounded MP4 recording from the existing camera's latest frame cache.

This adapter never opens a camera or microphone. Its single worker samples the
existing owner, streams frames to an installed encoder, and validates the saved
container with ffprobe before publishing it. Counters describe this consumer;
none of the configured rates are a claim about physical Pi frame rate.
"""
from dataclasses import asdict, dataclass
import errno
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import select
import signal
import stat
import struct
import subprocess
from threading import Event, RLock, Thread
import time
import uuid

from ..contracts import FeatureError, request_id_ok, valid_id
from ..media.assets import _open_root
from .codec import validate_png
from .jpeg_codec import validate_jpeg
from .models import Frame, PreviewFrame


CONTRACT = 'bmo_camera_compressed_video_v1'
_VIDEO_ID = re.compile(r'video_[0-9a-f]{32}\Z')
_MAX_JOURNAL = 1024 * 1024
_MAX_RECORDS = 512
_METRICS = frozenset({'target_fps', 'source_frames', 'frames_written', 'source_duplicates',
    'source_gaps', 'stale_dropped', 'queue_replaced', 'queue_capacity', 'first_source_at',
    'last_source_at', 'max_source_age_ms', 'max_write_lag_ms', 'source_span', 'source_effective_fps',
    'encoder_frames', 'encoder_dropped', 'encoded_duration', 'timing_drift_seconds', 'timing_status',
    'source_timestamp_basis', 'encoder_timestamp_basis', 'physical_fps', 'header_avg_frame_rate',
    'header_r_frame_rate'})
_TOOLS = {'ffmpeg': ('/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/bin/ffmpeg'),
          'ffprobe': ('/usr/bin/ffprobe', '/usr/local/bin/ffprobe', '/bin/ffprobe')}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _tool(name):
    for path in _TOOLS[name]:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _ebml_size(size):
    if type(size) is not int or not 0 <= size <= 4 * 1024 * 1024:
        raise FeatureError('invalid_video_frame')
    for count in range(1, 9):
        if size < (1 << (7 * count)) - 1:
            return (size | (1 << (7 * count))).to_bytes(count, 'big')
    raise FeatureError('invalid_video_frame')


def _ebml(identifier, value):
    key = identifier.to_bytes((identifier.bit_length() + 7) // 8, 'big')
    return key + _ebml_size(len(value)) + value


def _ebml_uint(identifier, value):
    return _ebml(identifier, value.to_bytes(max(1, (value.bit_length() + 7) // 8), 'big'))


def _matroska_header(frame, fps):
    """A single video track, unknown-length Segment, no audio or file paths.

    Every later Cluster carries source-derived PTS. This small packet wrapper
    avoids image2pipe's arrival-clock timestamps, which collapse queued startup
    frames into a burst before the decoder has begun consuming the pipe.
    """
    header = b''.join((_ebml_uint(0x4286, 1), _ebml_uint(0x42F7, 1),
        _ebml_uint(0x42F2, 4), _ebml_uint(0x42F3, 8), _ebml(0x4282, b'matroska'),
        _ebml_uint(0x4287, 4), _ebml_uint(0x4285, 2)))
    info = _ebml(0x1549A966, _ebml_uint(0x2AD7B1, 1_000_000)
                 + _ebml(0x4D80, b'BMO') + _ebml(0x5741, b'BMO'))
    codec = b'V_MJPEG' if frame.format == 'jpeg' else b'V_MS/VFW/FOURCC'
    private = (b'' if frame.format == 'jpeg' else _ebml(0x63A2, struct.pack('<IiiHH4sIiiII',
        40, frame.width, frame.height, 1, 24, b'MPNG', frame.width * frame.height * 3, 0, 0, 0, 0)))
    video = _ebml(0xE0, _ebml_uint(0xB0, frame.width) + _ebml_uint(0xBA, frame.height))
    track = b''.join((_ebml_uint(0xD7, 1), _ebml_uint(0x73C5, 1),
        _ebml_uint(0x83, 1), _ebml_uint(0x9C, 0), _ebml(0x86, codec), private,
        _ebml_uint(0x23E383, round(1_000_000_000 / fps)), video))
    return (_ebml(0x1A45DFA3, header) + b'\x18\x53\x80\x67\x01' + b'\xff' * 7
            + info + _ebml(0x1654AE6B, _ebml(0xAE, track)))


def _matroska_packet(payload, seconds):
    if not _finite(seconds) or not 0 <= seconds <= 62:
        raise FeatureError('video_timestamp_invalid')
    stamp = round(seconds * 1000)
    # Track one, signed relative timecode zero, independent/key JPEG/PNG frame.
    block = _ebml(0xA3, b'\x81\x00\x00\x80' + payload)
    return _ebml(0x1F43B675, _ebml_uint(0xE7, stamp) + block)


def probe_compressed_video():
    """Local encoder metadata only, no device access or provider request."""
    encoder, probe = _tool('ffmpeg'), _tool('ffprobe')
    code = 'video_encoder_missing' if not encoder else 'video_probe_missing' if not probe else 'video_encoder_ready'
    supported = False
    if encoder and probe:
        try:
            result = subprocess.run([encoder, '-hide_banner', '-encoders'], capture_output=True,
                                    timeout=3, check=False)
            supported = result.returncode == 0 and len(result.stdout) <= 256 * 1024 and b'libx264 ' in result.stdout
            code = 'video_encoder_ready' if supported else 'video_codec_unavailable'
        except (OSError, subprocess.TimeoutExpired):
            code = 'video_encoder_unavailable'
    return dict(available=supported, code=code, format='mp4', codec='h264',
                mime='video/mp4', audio_streams=0, target_fps=20,
                physical_fps='NOT_MEASURED', activation='explicit_camera_opt_in')


@dataclass(frozen=True, slots=True)
class CompressedVideoConfig:
    max_duration: float = 60.
    default_duration: float = 30.
    max_bytes: int = 96 * 1024 * 1024
    max_storage_bytes: int = 512 * 1024 * 1024
    min_free_bytes: int = 32 * 1024 * 1024
    fps: int = 20
    size: tuple[int, int] = (640, 360)
    max_source_age: float = .75
    finalize_timeout: float = 20.

    def __post_init__(self):
        if (not _finite(self.max_duration) or not .5 <= self.max_duration <= 60
                or not _finite(self.default_duration) or not .5 <= self.default_duration <= self.max_duration
                or type(self.max_bytes) is not int or not 64 * 1024 <= self.max_bytes <= 128 * 1024 * 1024
                or type(self.max_storage_bytes) is not int or not self.max_bytes <= self.max_storage_bytes <= 1024 ** 3
                or type(self.min_free_bytes) is not int or not 0 <= self.min_free_bytes <= 1024 ** 3
                or type(self.fps) is not int or not 1 <= self.fps <= 30
                or type(self.size) is not tuple or len(self.size) != 2
                or any(type(x) is not int or not 2 <= x <= 1280 or x % 2 for x in self.size)
                or not _finite(self.max_source_age) or not .05 <= self.max_source_age <= 2
                or not _finite(self.finalize_timeout) or not 1 <= self.finalize_timeout <= 30):
            raise FeatureError('invalid_video_config')

    @property
    def max_frames(self):
        return math.ceil(self.max_duration * self.fps) + 1

    @property
    def encoder_byte_limit(self):
        # Leave room for the last compressed packet and MP4 index. The child
        # also has an OS file-size ceiling, so staging cannot grow unbounded.
        return self.max_bytes - max(32768, min(1024 * 1024, self.max_bytes // 8))


def _safe_file(fd, limit, *, empty=False):
    item = os.fstat(fd)
    if (not stat.S_ISREG(item.st_mode) or item.st_uid != os.geteuid()
            or stat.S_IMODE(item.st_mode) != 0o600 or item.st_nlink != 1
            or not (0 if empty else 1) <= item.st_size <= limit):
        raise FeatureError('unsafe_video_file')
    return item


class CompressedVideoStore:
    """Private journal and streaming stage; no existing artifact is deleted."""
    def __init__(self, root, *, config=None):
        self.root, self.config = Path(root), config or CompressedVideoConfig()
        self._root = self._lock = self._output = None
        self._records = self._request = self._stage = None
        self._guard = RLock()

    def _open(self, create=True):
        if (not self.root.is_absolute() or '..' in self.root.parts or self.root == Path('/')):
            raise FeatureError('unsafe_video_root')
        parent = None
        try:
            parent = _open_root(self.root.parent)
            if create:
                try:
                    os.mkdir(self.root.name, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            self._root = os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=parent)
            info = os.fstat(self._root)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise FeatureError('unsafe_video_root')
        except (OSError, ValueError, TypeError):
            raise FeatureError('unsafe_video_root') from None
        finally:
            if parent is not None:
                os.close(parent)

    def _release(self):
        for name in ('_output', '_lock', '_root'):
            fd = getattr(self, name)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)
        self._request = self._stage = self._records = None

    def _read_json(self):
        try:
            fd = os.open('.compressed-video-journal.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=self._root)
        except FileNotFoundError:
            return {}
        try:
            info = _safe_file(fd, _MAX_JOURNAL)
            raw = os.read(fd, info.st_size + 1)
        finally:
            os.close(fd)
        try:
            def unique(pairs):
                output = {}
                for key, value in pairs:
                    if key in output:
                        raise ValueError()
                    output[key] = value
                return output
            def reject_constant(value):
                raise ValueError()
            data = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
            if (type(data) is not dict or set(data) != {'contract', 'records'}
                    or data['contract'] != CONTRACT or type(data['records']) is not dict
                    or len(data['records']) > _MAX_RECORDS):
                raise ValueError()
            for request, record in data['records'].items():
                if (not request_id_ok(request) or type(record) is not dict
                        or set(record) != {'fingerprint', 'result'}
                        or type(record['fingerprint']) is not str or not re.fullmatch('[0-9a-f]{64}', record['fingerprint'])
                        or type(record['result']) is not dict or record['result'].get('request_id') != request):
                    raise ValueError()
                result = record['result']
                required = {'contract', 'request_id', 'status', 'code', 'stop_reason', 'video_id', 'sha256',
                            'bytes', 'duration', 'audio_streams', 'simulated', 'executed', 'completed',
                            'playable', 'interrupted', 'codec', 'container'}
                if (not required <= set(result) or set(result) - required - {'metrics'}
                        or result['contract'] != CONTRACT
                        or result['status'] not in ('running', 'succeeded', 'simulated', 'failed', 'cancelled', 'rejected', 'unknown')
                        or not valid_id(result['code']) or not valid_id(result['stop_reason'])
                        or result['video_id'] is not None and (type(result['video_id']) is not str or not _VIDEO_ID.fullmatch(result['video_id']))
                        or result['sha256'] is not None and (type(result['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', result['sha256']))
                        or type(result['bytes']) is not int or not 0 <= result['bytes'] <= self.config.max_bytes
                        or not _finite(result['duration']) or not 0 <= result['duration'] <= 60.15
                        or result['audio_streams'] != 0 or type(result['audio_streams']) is not int
                        or any(type(result[x]) is not bool for x in ('simulated', 'executed', 'completed', 'playable', 'interrupted'))
                        or result['simulated'] and result['executed']
                        or result['codec'] != 'h264' or result['container'] != 'mp4'
                        or result['playable'] and (not result['video_id'] or not result['sha256'] or not result['bytes'] or not result['duration'])):
                    raise ValueError()
                metrics = result.get('metrics', {})
                if type(metrics) is not dict or set(metrics) - _METRICS:
                    raise ValueError()
                for key, value in metrics.items():
                    if key in ('header_avg_frame_rate', 'header_r_frame_rate'):
                        if value is not None and (type(value) is not str or not re.fullmatch(r'\d{1,12}/\d{1,12}', value)):
                            raise ValueError()
                    elif key in ('timing_status', 'source_timestamp_basis', 'encoder_timestamp_basis', 'physical_fps'):
                        if value not in ('NOT_MEASURED', 'within_bound', 'unconfirmed', 'camera_monotonic',
                                         'frame_arrival_wallclock', 'source_pts_milliseconds'):
                            raise ValueError()
                    elif value is not None and (not _finite(value) or value < 0):
                        raise ValueError()
            return data['records']
        except (ValueError, TypeError, KeyError):
            raise FeatureError('video_journal_invalid') from None

    def _write_json(self):
        raw = json.dumps({'contract': CONTRACT, 'records': self._records},
                         allow_nan=False, separators=(',', ':')).encode()
        if len(raw) > _MAX_JOURNAL:
            raise FeatureError('video_journal_full')
        name = '.compressed-journal-' + uuid.uuid4().hex + '.tmp'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self._root)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, '.compressed-video-journal.json', src_dir_fd=self._root, dst_dir_fd=self._root)
            os.fsync(self._root)
        finally:
            try:
                os.unlink(name, dir_fd=self._root)
            except FileNotFoundError:
                pass

    def budget(self, needed=0):
        fs = os.fstatvfs(self._root)
        if fs.f_bavail * fs.f_frsize < self.config.min_free_bytes + needed:
            raise FeatureError('video_disk_full')
        total = 0
        names = os.listdir(self._root)
        if len(names) > 2048:
            raise FeatureError('video_storage_limit')
        for name in names:
            info = os.stat(name, dir_fd=self._root, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise FeatureError('unsafe_video_root')
            total += info.st_size
        if total + needed > self.config.max_storage_bytes:
            raise FeatureError('video_storage_limit')

    def begin(self, request_id, fingerprint, running):
        with self._guard:
            if self._root is not None:
                raise FeatureError('video_busy')
            try:
                self._open()
                self._lock = os.open('.compressed-video.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                     0o600, dir_fd=self._root)
                _safe_file(self._lock, 0, empty=True)
                try:
                    fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise FeatureError('video_busy') from None
                self._records = self._read_json()
                previous = self._records.get(request_id)
                if previous is not None:
                    result = dict(previous['result'])
                    if previous['fingerprint'] != fingerprint:
                        raise FeatureError('request_id_conflict')
                    if result['status'] == 'running':
                        result.update(status='unknown', code='video_interrupted', interrupted=True)
                    self._release()
                    return result
                if len(self._records) >= _MAX_RECORDS:
                    raise FeatureError('video_journal_full')
                self.budget(self.config.max_bytes)
                self._request = request_id
                self._records[request_id] = dict(fingerprint=fingerprint, result=running)
                self._write_json()
                self._stage = '.video-stage-' + uuid.uuid4().hex + '.mp4.partial'
                self._output = os.open(self._stage, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                       0o600, dir_fd=self._root)
                return None
            except BaseException:
                self._release()
                raise

    def finish(self, result, *, publish=False):
        with self._guard:
            try:
                if self._request != result['request_id'] or self._records is None:
                    raise FeatureError('video_reservation_missing')
                if publish:
                    _safe_file(self._output, self.config.max_bytes)
                    os.fsync(self._output)
                    destination = result['video_id'] + '.mp4'
                    # Link is no-replace; a previous successful file is never overwritten.
                    os.link(self._stage, destination, src_dir_fd=self._root, dst_dir_fd=self._root,
                            follow_symlinks=False)
                    os.fsync(self._root)
                    os.unlink(self._stage, dir_fd=self._root)
                    self._stage = None
                self._records[self._request]['result'] = dict(result)
                self._write_json()
                return result
            finally:
                self._release()

    def open_video(self, video_id):
        if type(video_id) is not str or not _VIDEO_ID.fullmatch(video_id):
            raise FeatureError('invalid_video_id')
        with self._guard:
            if self._root is not None:
                raise FeatureError('video_busy')
            try:
                self._open(create=False)
                records = self._read_json()
                saved = next((x['result'] for x in records.values()
                              if x['result'].get('video_id') == video_id and x['result'].get('playable') is True), None)
                if saved is None:
                    raise FeatureError('video_unavailable')
                fd = os.open(video_id + '.mp4', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=self._root)
                try:
                    info = _safe_file(fd, self.config.max_bytes)
                    if info.st_size != saved.get('bytes'):
                        raise FeatureError('video_verification_failed')
                    digest = hashlib.sha256()
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        digest.update(chunk)
                    if digest.hexdigest() != saved.get('sha256'):
                        raise FeatureError('video_verification_failed')
                    os.lseek(fd, 0, os.SEEK_SET)
                    return fd, info.st_size, dict(saved)
                except BaseException:
                    os.close(fd)
                    raise
            except OSError:
                raise FeatureError('video_unavailable') from None
            finally:
                self._release()

    def read_video(self, video_id, byte_range=None):
        fd, total, meta = self.open_video(video_id)
        try:
            start, end, status = 0, total - 1, 200
            if byte_range is not None:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', byte_range) if type(byte_range) is str and len(byte_range) <= 128 else None
                if not match or not any(match.groups()):
                    raise FeatureError('invalid_video_range')
                left, right = match.groups()
                if not left:
                    suffix = int(right)
                    if suffix <= 0:
                        raise FeatureError('invalid_video_range')
                    start = max(0, total - suffix)
                else:
                    start = int(left)
                    end = min(total - 1, int(right)) if right else total - 1
                if start > end or start >= total:
                    raise FeatureError('video_range_unsatisfiable')
                status = 206
            os.lseek(fd, start, os.SEEK_SET)
            remaining, chunks = end - start + 1, []
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    raise FeatureError('video_verification_failed')
                chunks.append(chunk)
                remaining -= len(chunk)
            return dict(body=b''.join(chunks), mime='video/mp4', status=status,
                        start=start, end=end, total=total, sha256=meta['sha256'])
        finally:
            os.close(fd)

    def close(self):
        with self._guard:
            self._release()


class CompressedVideoRecorder:
    """One producer-cache consumer and one encoder; no extra camera owner.

    frame_source must be a bounded cache-only callable returning an immutable
    Frame/PreviewFrame. Host polls never drive recording rate. offer_frame is
    available for injected IO; its capacity-one slot replaces late frames.
    """
    def __init__(self, root, *, frame_source=None, clock=time.monotonic, config=None, simulated=False):
        if type(simulated) is not bool or frame_source is not None and not callable(frame_source):
            raise FeatureError('invalid_video_config')
        self.config, self.clock, self.simulated = config or CompressedVideoConfig(), clock, simulated
        self.store = CompressedVideoStore(root, config=self.config)
        self.frame_source = frame_source
        self._guard, self._stop = RLock(), Event()
        self._thread = self._process = None
        self._frame = self._result = self._request = self._started = None
        self._generation, self._duration = -1, self.config.default_duration
        self._reason, self._interrupted, self._closed = 'user_stop', False, False
        self._phase = 'idle'
        self._metrics = {}
        self._capability = None

    def _base(self, request_id, status, code, **values):
        result = dict(contract=CONTRACT, request_id=request_id, status=status, code=code,
                    stop_reason=self._reason if self._request else 'not_started',
                    video_id=None, sha256=None, bytes=0, duration=0., audio_streams=0,
                    simulated=self.simulated, executed=False,
                    completed=False, playable=False, interrupted=False, codec='h264',
                    container='mp4')
        result.update(values)
        return result

    def capability(self):
        if self._capability is None:
            available = bool(_tool('ffmpeg') and _tool('ffprobe'))
            return dict(available=available, code='video_encoder_unverified' if available else 'video_encoder_missing',
                        format='mp4', codec='h264', mime='video/mp4', audio_streams=0,
                        target_fps=self.config.fps, physical_fps='NOT_MEASURED')
        return dict(self._capability)

    def start(self, request_id, *, generation, consent=False, duration=None):
        if (not request_id_ok(request_id) or type(generation) is not int or generation < 0
                or type(consent) is not bool or duration is not None and not _finite(duration)):
            raise FeatureError('invalid_video_request')
        duration = self.config.default_duration if duration is None else duration
        if not .5 <= duration <= self.config.max_duration:
            return self._base(request_id, 'rejected', 'video_duration_out_of_range')
        with self._guard:
            if self._closed:
                return self._base(request_id, 'rejected', 'video_closed')
            if self._thread is not None and self._thread.is_alive():
                return dict(self._result) if self._request == request_id and generation == self._generation else self._base(request_id, 'rejected', 'video_busy')
            if consent is not True:
                return self._base(request_id, 'rejected', 'video_consent_required')
            if generation < self._generation:
                return self._base(request_id, 'rejected', 'stale_video_generation')
            self._duration = duration
            running = self._base(request_id, 'running', 'video_starting')
            self._request, self._generation = request_id, generation
            self._started, self._reason, self._interrupted = None, 'user_stop', False
            self._stop.clear()
            self._frame = None
            self._phase, self._result = 'starting', running
            self._metrics = dict(target_fps=self.config.fps, source_frames=0, frames_written=0,
                source_duplicates=0, source_gaps=0, stale_dropped=0, queue_replaced=0, queue_capacity=1,
                first_source_at=None, last_source_at=None, max_source_age_ms=0., max_write_lag_ms=0.,
                source_span=0., source_effective_fps=None, encoder_frames=None, encoder_dropped=None,
                encoded_duration=None, timing_drift_seconds=None, timing_status='NOT_MEASURED',
                source_timestamp_basis='camera_monotonic', encoder_timestamp_basis='source_pts_milliseconds',
                physical_fps='NOT_MEASURED')
            self._thread = Thread(target=self._prepare_run, name='bmo-compressed-video', daemon=True)
            self._thread.start()
            return dict(running)

    # Compatibility names make the explicit new implementation easy to select.
    start_video_recording = start

    def _prepare_run(self):
        reserved = False
        try:
            capability = probe_compressed_video()
            with self._guard:
                self._capability = capability
            if not capability['available']:
                raise FeatureError(capability['code'])
            fingerprint = hashlib.sha256(json.dumps({'config': asdict(self.config), 'duration': self._duration,
                                                     'simulated': self.simulated}, sort_keys=True).encode()).hexdigest()
            prior = self.store.begin(self._request, fingerprint, self._result)
            if prior is not None:
                with self._guard:
                    self._result, self._phase = prior, 'review' if prior.get('playable') else 'failed'
                return
            reserved = True
            if self._stop.is_set():
                result = self._base(self._request, 'cancelled', 'video_cancelled', interrupted=True)
                self.store.finish(result)
                with self._guard:
                    self._result, self._phase = result, 'failed'
                return
            self._run()
        except Exception as error:
            result = self._base(self._request, 'failed', error.code if isinstance(error, FeatureError) else 'video_start_failed')
            if reserved:
                try:
                    self.store.finish(result)
                except Exception:
                    result.update(status='unknown', code='video_journal_pending')
            with self._guard:
                self._result, self._phase = result, 'failed'

    def offer_frame(self, frame):
        if frame is not None and not isinstance(frame, (Frame, PreviewFrame)):
            raise FeatureError('invalid_video_frame')
        with self._guard:
            if self._phase not in ('starting', 'recording') or self._stop.is_set():
                return False
            if self._frame is not None:
                self._metrics['queue_replaced'] += 1
            self._frame = frame
            return True

    def request_stop(self, request_id, *, generation, reason='user_stop'):
        if not request_id_ok(request_id) or not valid_id(reason):
            raise FeatureError('invalid_video_request')
        with self._guard:
            if self._request != request_id:
                return self._base(request_id, 'rejected', 'video_request_mismatch')
            if generation != self._generation or type(generation) is not int:
                return self._base(request_id, 'rejected', 'stale_video_generation')
            if self._phase in ('starting', 'recording'):
                self._reason, self._phase = reason, 'finalizing'
                self._stop.set()
            return self.snapshot()

    def stop(self, request_id, *, generation, reason='user_stop', wait=True):
        self.request_stop(request_id, generation=generation, reason=reason)
        if wait and self._thread is not None:
            self._thread.join(self.config.finalize_timeout + 5)
        return self.snapshot()['result']

    stop_video_recording = stop

    def cancel(self, reason='video_app_exit'):
        if not valid_id(reason):
            raise FeatureError('invalid_video_stop_reason')
        with self._guard:
            if self._phase in ('starting', 'recording', 'finalizing'):
                self._reason, self._interrupted, self._phase = reason, True, 'finalizing'
                self._stop.set()
            return self.snapshot()

    def _take_frame(self):
        if self.frame_source is not None:
            return self.frame_source()
        with self._guard:
            frame, self._frame = self._frame, None
            return frame

    def _write_frame(self, process, payload):
        descriptor = process.stdin.fileno()
        deadline, offset = time.monotonic() + 1., 0
        while offset < len(payload):
            if process.poll() is not None:
                raise FeatureError(self._encoder_failure(process))
            if time.monotonic() >= deadline:
                raise FeatureError('video_encoder_timeout')
            _, writable, _ = select.select([], [descriptor], [], .05)
            if writable:
                try:
                    offset += os.write(descriptor, payload[offset:offset + 65536])
                except BlockingIOError:
                    pass
                except OSError as failure:
                    # A pipe may break before poll() observes SIGXFSZ. Keep it
                    # at the encoder boundary; final reap checks the stronger
                    # exit/file-size evidence without guessing from EPIPE.
                    raise FeatureError(self._encoder_failure(process, failure)) from None

    def _encoder_failure(self, process, failure=None):
        """Classify bounded encoder IO using local, specific limit evidence."""
        code = process.poll()
        if (isinstance(failure, OSError) and failure.errno == errno.EFBIG
                or code == -signal.SIGXFSZ):
            return 'video_byte_limit'
        try:
            size = os.fstat(self.store._output).st_size
        except (OSError, TypeError):
            size = None
        if size is not None and (size >= self.config.max_bytes
                or code == 0 and size >= self.config.encoder_byte_limit):
            return 'video_byte_limit'
        return 'video_encoder_failed'

    def _spawn(self, frame):
        output_fd = self.store._output
        command = [_tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-probesize', '32', '-analyzeduration', '0',
            '-f', 'matroska', '-threads', '1', '-i', 'pipe:0',
            '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency', '-crf', '28',
            '-threads', '2', '-filter_threads', '1', '-pix_fmt', 'yuv420p',
            '-vf', f'scale={self.config.size[0]}:{self.config.size[1]}',
            '-enc_time_base', '1:1000', '-fps_mode', 'vfr',
            '-video_track_timescale', '1000', '-t', str(self._duration), '-fs', str(self.config.encoder_byte_limit),
            '-movflags', '+faststart', '-f', 'mp4', f'/proc/self/fd/{output_fd}']
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, close_fds=True, pass_fds=(output_fd,))
        try:
            # Set before the first input frame. Unlike preexec_fn this is safe
            # when other app owner threads already exist. Never raise a limit
            # or alter the parent/OS/user configuration.
            resource.prlimit(process.pid, resource.RLIMIT_FSIZE, (self.config.max_bytes, self.config.max_bytes))
            os.set_blocking(process.stdin.fileno(), False)
        except (OSError, ValueError):
            process.stdin.close()
            process.kill()
            process.wait(timeout=2)
            process.stderr.close()
            raise FeatureError('video_encoder_limit_unavailable') from None
        # A fixed-size drain prevents stderr blocking without publishing paths,
        # frame contents or arbitrary SDK/encoder text to the UI/journal.
        def drain():
            try:
                while process.stderr.read(4096):
                    pass
            except (OSError, ValueError):
                pass
        drain_thread = Thread(target=drain, name='bmo-video-stderr', daemon=True)
        drain_thread.start()
        return process, drain_thread

    def _probe(self, fd):
        command = [_tool('ffprobe'), '-v', 'error', '-count_frames', '-show_entries',
            'stream=codec_name,codec_type,width,height,nb_read_frames,avg_frame_rate,r_frame_rate,duration:format=format_name,duration,size',
            '-of', 'json', f'/proc/self/fd/{fd}']
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=self.config.finalize_timeout, check=False, pass_fds=(fd,))
        if result.returncode != 0 or len(result.stdout) > 65536:
            raise FeatureError('video_verification_failed')
        try:
            data = json.loads(result.stdout)
            streams = data['streams']
            videos = [x for x in streams if x.get('codec_type') == 'video']
            duration = float(data['format']['duration'])
            frames = int(videos[0]['nb_read_frames'])
            if (len(streams) != 1 or len(videos) != 1 or videos[0]['codec_name'] != 'h264'
                    or (videos[0]['width'], videos[0]['height']) != self.config.size
                    or not _finite(duration) or not .01 <= duration <= self._duration + .15
                    or not 1 <= frames <= self._metrics['frames_written']
                    or int(data['format']['size']) != os.fstat(fd).st_size
                    or 'mp4' not in data['format']['format_name']):
                raise ValueError()
            return dict(duration=duration, frames=frames,
                        header_avg_frame_rate=videos[0].get('avg_frame_rate'),
                        header_r_frame_rate=videos[0].get('r_frame_rate'))
        except (ValueError, TypeError, KeyError, IndexError):
            raise FeatureError('video_verification_failed') from None

    def _run(self):
        process = drain_thread = None
        last_id = last_stamp = last_sensor = None
        first_format = None
        error = None
        next_at = time.monotonic()
        startup_at = last_valid_at = self.clock()
        try:
            while not self._stop.is_set():
                now = self.clock()
                if not _finite(now) or now < (self._started if self._started is not None else startup_at):
                    raise FeatureError('video_clock_invalid')
                if self._started is not None and now - self._started >= self._duration:
                    self._reason = 'video_duration_limit'
                    break
                try:
                    frame = self._take_frame()
                except FeatureError:
                    raise
                except Exception:
                    # Only cache/source IO failed. Encoder size/signal evidence
                    # must never relabel a device/cache failure as a byte cap.
                    raise FeatureError('video_source_unavailable') from None
                if frame is not None:
                    if not isinstance(frame, (Frame, PreviewFrame)):
                        raise FeatureError('invalid_video_frame')
                    identity = (frame.stream_generation, frame.seq)
                    stamp, sensor = frame.captured_at, getattr(frame, 'sensor_timestamp', None)
                    if now - stamp > self.config.max_source_age or stamp > now + .05:
                        self._metrics['stale_dropped'] += 1
                    elif (identity == last_id or last_stamp is not None and stamp <= last_stamp
                          or sensor is not None and last_sensor is not None and sensor <= last_sensor):
                        self._metrics['source_duplicates'] += 1
                    else:
                        if last_id is not None and frame.stream_generation != last_id[0]:
                            raise FeatureError('video_stream_changed')
                        geometry = validate_jpeg(frame.payload) if frame.format == 'jpeg' else validate_png(frame.payload)
                        if geometry != (frame.width, frame.height):
                            raise FeatureError('invalid_video_frame')
                        if first_format is not None and frame.format != first_format:
                            raise FeatureError('video_stream_changed')
                        first_source = self._metrics['first_source_at']
                        if first_source is not None and stamp - first_source >= self._duration - .0005:
                            self._reason = 'video_duration_limit'
                            break
                        if process is None:
                            process, drain_thread = self._spawn(frame)
                            self._process, first_format = process, frame.format
                            self._write_frame(process, _matroska_header(frame, self.config.fps))
                        if self._metrics['frames_written'] >= math.ceil(self._duration * self.config.fps) + 1:
                            self._reason = 'video_frame_limit'
                            break
                        if last_id is not None:
                            self._metrics['source_gaps'] += max(0, frame.seq - last_id[1] - 1)
                        self._metrics['source_frames'] += 1
                        self._metrics['first_source_at'] = self._metrics['first_source_at'] if last_stamp is not None else stamp
                        self._metrics['last_source_at'] = stamp
                        self._metrics['max_source_age_ms'] = max(self._metrics['max_source_age_ms'], max(0., now - stamp) * 1000)
                        relative = stamp - self._metrics['first_source_at']
                        self._write_frame(process, _matroska_packet(frame.payload, relative))
                        self._metrics['frames_written'] += 1
                        if self._started is None:
                            with self._guard:
                                self._started = self.clock()
                                if not self._stop.is_set():
                                    self._phase = 'recording'
                                    self._result = self._base(self._request, 'running', 'video_recording')
                        self._metrics['max_write_lag_ms'] = max(self._metrics['max_write_lag_ms'], max(0., self.clock() - stamp) * 1000)
                        last_id, last_stamp, last_sensor = identity, stamp, sensor
                        last_valid_at = now
                        size = os.fstat(self.store._output).st_size
                        if size >= self.config.encoder_byte_limit:
                            self._reason = 'video_byte_limit'
                            break
                        self.store.budget()
                if now - last_valid_at > 2.:
                    raise FeatureError('video_source_unavailable')
                next_at += 1 / self.config.fps
                delay = next_at - time.monotonic()
                if delay < -.1:
                    next_at = time.monotonic()
                    delay = 0
                self._stop.wait(max(0., delay))
        except Exception as failure:
            error = failure.code if isinstance(failure, FeatureError) else 'video_capture_failed'
            if error == 'video_byte_limit':
                self._reason, error = 'video_byte_limit', None
            else:
                self._interrupted = True
        finally:
            with self._guard:
                self._phase = 'finalizing'
            if process is not None:
                try:
                    try:
                        process.stdin.close()
                    except OSError as failure:
                        error = error or self._encoder_failure(process, failure)
                    if process.wait(timeout=self.config.finalize_timeout) != 0:
                        code = self._encoder_failure(process)
                        # Preserve an independent source/validation error, but
                        # upgrade the earlier pipe-race diagnosis once the
                        # child's exit and final stage size are available.
                        if error in (None, 'video_encoder_failed', 'video_encoder_timeout'):
                            error = code
                            if code == 'video_byte_limit':
                                self._reason = 'video_byte_limit'
                    elif error == 'video_encoder_failed' and self._encoder_failure(process) == 'video_byte_limit':
                        error = 'video_byte_limit'
                        self._reason = 'video_byte_limit'
                except subprocess.TimeoutExpired:
                    error = error or 'video_encoder_timeout'
                    process.kill()
                    process.wait(timeout=2)
                finally:
                    if drain_thread is not None:
                        drain_thread.join(1)
                    process.stderr.close()
            result = self._base(self._request, 'failed', error or 'video_empty',
                                executed=not self.simulated and bool(self._metrics['frames_written']))
            try:
                if process is not None and process.returncode == 0 and self._metrics['frames_written']:
                    meta = self._probe(self.store._output)
                    size = _safe_file(self.store._output, self.config.max_bytes).st_size
                    digest = hashlib.sha256()
                    os.lseek(self.store._output, 0, os.SEEK_SET)
                    while True:
                        chunk = os.read(self.store._output, 65536)
                        if not chunk:
                            break
                        digest.update(chunk)
                    span = max(0., self._metrics['last_source_at'] - self._metrics['first_source_at'])
                    expected_duration = span + 1 / self.config.fps
                    drift = abs(meta['duration'] - expected_duration)
                    timing_confirmed = drift <= max(.012, 1 / self.config.fps * .15)
                    self._metrics.update(source_span=span,
                        source_effective_fps=(self._metrics['source_frames'] - 1) / span if span else None,
                        encoder_frames=meta['frames'], encoded_duration=meta['duration'],
                        encoder_dropped=self._metrics['frames_written'] - meta['frames'],
                        timing_drift_seconds=drift, timing_status='within_bound' if timing_confirmed else 'unconfirmed',
                        header_avg_frame_rate=meta['header_avg_frame_rate'], header_r_frame_rate=meta['header_r_frame_rate'])
                    if not timing_confirmed:
                        error = error or 'video_timestamp_drift'
                    interrupted = self._interrupted or error is not None
                    result.update(status='cancelled' if interrupted else 'simulated' if self.simulated else 'succeeded',
                        code=error or ('video_interrupted' if interrupted else 'video_saved'),
                        stop_reason=self._reason, video_id='video_' + hashlib.sha256(self._request.encode()).hexdigest()[:32],
                        sha256=digest.hexdigest(), bytes=size, duration=meta['duration'], completed=True,
                        playable=True, interrupted=interrupted, metrics=dict(self._metrics))
                    try:
                        self.store.finish(result, publish=True)
                    except Exception:
                        # Keep both media and running recovery journal when publication
                        # acknowledgement is uncertain. Never automatically re-record.
                        result.update(status='unknown', code='video_saved_state_unknown', completed=False, playable=False)
                else:
                    self.store.finish(result)
            except Exception as failure:
                result = self._base(self._request, 'failed',
                    failure.code if isinstance(failure, FeatureError) else 'video_verification_failed')
                try:
                    self.store.finish(result)
                except Exception:
                    result.update(status='unknown', code='video_journal_pending')
            with self._guard:
                self._result, self._phase, self._process = result, 'review' if result.get('playable') else 'failed', None

    def snapshot(self):
        with self._guard:
            elapsed = max(0., min(self._duration, self.clock() - self._started)) if self._started is not None else 0.
            return dict(contract=CONTRACT, phase=self._phase, recording=self._phase == 'recording',
                finalizing=self._phase == 'finalizing', generation=max(0, self._generation), elapsed=elapsed,
                readiness='READY' if self._capability and self._capability['available'] else 'UNVERIFIED',
                code=self._result['code'] if self._result else 'video_ready', format='mp4', codec='h264',
                audio_streams=0, simulated=self.simulated, executed=not self.simulated and self._started is not None,
                requested_duration=self._duration, max_duration=self.config.max_duration,
                max_bytes=self.config.max_bytes, min_free_bytes=self.config.min_free_bytes,
                max_frames=math.ceil(self._duration * self.config.fps) + 1,
                target_fps=self.config.fps, size=list(self.config.size), metrics=dict(self._metrics),
                worker_alive=self._thread is not None and self._thread.is_alive(),
                result=dict(self._result) if self._result else None)

    def poll(self, *, generation):
        with self._guard:
            if self._phase in ('starting', 'recording') and generation != self._generation:
                self.cancel('stale_video_generation')
        return self.snapshot()

    def open_video(self, video_id):
        return self.store.open_video(video_id)

    def read_video(self, video_id, byte_range=None):
        return self.store.read_video(video_id, byte_range)

    def close(self):
        self.cancel('video_app_exit')
        thread = self._thread
        if thread is not None:
            thread.join(self.config.finalize_timeout + 5)
        if thread is not None and thread.is_alive():
            return False
        self._closed = True
        self.store.close()
        return True
