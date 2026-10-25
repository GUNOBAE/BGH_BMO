"""Versioned, explicitly requested *synthetic* silent video recording.

There is intentionally no live encoder/backend activation in v1. A caller must
select a local storage directory and explicitly consent to each start. The
existing CameraBroker owns every frame; the generated DIB AVI is a test artifact,
not a claim that Picamera2/video codecs or real camera hardware were verified.
No audio stream, network, face embedding, or microphone path exists here.
"""
from dataclasses import asdict, dataclass, replace
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from threading import RLock
import time
import uuid

from ..contracts import FeatureError, request_id_ok, valid_id
from ..media.assets import _open_root
from ..media.video_fixture import MAX_VIDEO_BYTES, synthetic_avi_bytes, validate_avi_bytes
from .codec import validate_png


CONTRACT = 'bmo_camera_video_v1'
MAX_RECORDS = 1024
MAX_JOURNAL_BYTES = 1024 * 1024


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class VideoConfig:
    max_duration: float = 12.0
    max_bytes: int = MAX_VIDEO_BYTES
    min_free_bytes: int = 16 * 1024 * 1024
    fps: int = 2

    def __post_init__(self):
        if (not _finite(self.max_duration) or not .5 <= self.max_duration <= 30
                or type(self.max_bytes) is not int or not 1024 <= self.max_bytes <= MAX_VIDEO_BYTES
                or type(self.min_free_bytes) is not int or not 0 <= self.min_free_bytes <= 1024 ** 3
                or type(self.fps) is not int or not 1 <= self.fps <= 4
                or not 1 <= self.max_duration * self.fps <= 120):
            raise FeatureError('invalid_video_config')

    @property
    def fingerprint(self):
        raw = json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()
        return hashlib.sha256(CONTRACT.encode() + raw).hexdigest()


@dataclass(frozen=True, slots=True)
class VideoResult:
    request_id: str
    status: str
    code: str
    stop_reason: str = 'not_started'
    video_id: str | None = None
    sha256: str | None = None
    bytes: int = 0
    duration: float = 0
    simulated: bool = True
    executed: bool = False
    completed: bool = False
    audio_streams: int = 0

    def __post_init__(self):
        if (not request_id_ok(self.request_id)
                or self.status not in ('running', 'simulated', 'failed', 'cancelled', 'rejected', 'unknown')
                or not valid_id(self.code) or not valid_id(self.stop_reason)
                or self.video_id is not None and (type(self.video_id) is not str
                    or re.fullmatch(r'video_[0-9a-f]{32}', self.video_id) is None)
                or self.sha256 is not None and (type(self.sha256) is not str
                    or re.fullmatch(r'[0-9a-f]{64}', self.sha256) is None)
                or type(self.bytes) is not int or not 0 <= self.bytes <= MAX_VIDEO_BYTES
                or not _finite(self.duration) or not 0 <= self.duration <= 30
                or any(type(value) is not bool for value in (self.simulated, self.executed, self.completed))
                or self.simulated is not True or self.executed is not False
                or type(self.audio_streams) is not int or self.audio_streams != 0
                or self.completed and (self.status != 'simulated' or self.video_id is None
                                      or self.sha256 is None or not self.bytes or not self.duration)):
            raise FeatureError('invalid_video_result')

    def public(self):
        return {'contract': CONTRACT, **asdict(self)}


def _file_ok(fd, *, max_size, allow_empty=False):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not (0 if allow_empty else 1) <= info.st_size <= max_size):
        raise FeatureError('unsafe_video_file')
    return info


class VideoStore:
    """Pinned private directory; metadata reservation survives a power loss.

    Creating this object does no IO. Only the chosen leaf directory may be
    created; its parent must already exist, with no symlink in its ancestry.
    User artifacts are never deleted on close/cancel/recovery. A persisted
    running request becomes UNKNOWN and is never silently recorded again.
    """
    # 기본 합성 계약은 그대로 두고, 별도 사용자 승인 실기 클래스만 이 hook을 바꾼다.
    result_type = VideoResult
    contract = CONTRACT
    validate_payload = staticmethod(validate_avi_bytes)
    saved_status = 'simulated'
    simulated = True
    def __init__(self, root, *, free_bytes=None):
        self.root = Path(root)
        self._free_bytes = free_bytes or (lambda fd: os.fstatvfs(fd).f_bavail * os.fstatvfs(fd).f_frsize)
        self._root_fd = self._lock_fd = None
        self._records = self._request = None
        self._guard = RLock()

    def _open(self, *, create=True):
        if (not self.root.is_absolute() or '..' in self.root.parts
                or self.root.name in ('', '.', '..') or self.root == Path('/')):
            raise FeatureError('unsafe_video_root')
        parent = None
        try:
            parent = _open_root(self.root.parent)
            if create:
                try:
                    os.mkdir(self.root.name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            root_fd = os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                              dir_fd=parent)
            info = os.fstat(root_fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                os.close(root_fd)
                raise FeatureError('unsafe_video_root')
            self._root_fd = root_fd
        except (OSError, TypeError, ValueError, FeatureError):
            raise FeatureError('unsafe_video_root') from None
        finally:
            if parent is not None:
                os.close(parent)

    def _space(self, needed, reserve):
        value = self._free_bytes(self._root_fd)
        if type(value) is not int or value < needed + reserve:
            raise FeatureError('video_disk_full')

    def _read(self, name, limit):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=self._root_fd)
        try:
            _file_ok(descriptor, max_size=limit)
            chunks, total = [], 0
            while total <= limit:
                chunk = os.read(descriptor, min(65536, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > limit:
                raise FeatureError('unsafe_video_file')
            return b''.join(chunks)
        finally:
            os.close(descriptor)

    def _load(self):
        try:
            raw = self._read('.video-journal.json', MAX_JOURNAL_BYTES)
        except FileNotFoundError:
            return {}
        try:
            def unique(pairs):
                output = {}
                for key, value in pairs:
                    if key in output:
                        raise ValueError()
                    output[key] = value
                return output
            payload = json.loads(raw, object_pairs_hook=unique)
            if (type(payload) is not dict or set(payload) != {'contract', 'records'}
                    or payload['contract'] != self.contract or type(payload['records']) is not dict
                    or len(payload['records']) > MAX_RECORDS):
                raise ValueError()
            for request, record in payload['records'].items():
                if (not request_id_ok(request) or type(record) is not dict
                        or set(record) != {'fingerprint', 'result'}
                        or type(record['fingerprint']) is not str
                        or re.fullmatch('[a-f0-9]{64}', record['fingerprint']) is None
                        or type(record['result']) is not dict):
                    raise ValueError()
                result = self.result_type(**record['result'])
                if result.request_id != request:
                    raise ValueError()
            return payload['records']
        except (ValueError, TypeError, FeatureError):
            raise FeatureError('video_journal_invalid') from None

    def _stage(self, prefix, raw):
        name = prefix + uuid.uuid4().hex + '.tmp'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode=0o600, dir_fd=self._root_fd)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            return name
        except BaseException:
            os.unlink(name, dir_fd=self._root_fd)
            raise

    def _write(self):
        raw = json.dumps({'contract': self.contract, 'records': self._records},
                         allow_nan=False, separators=(',', ':')).encode()
        if len(raw) > MAX_JOURNAL_BYTES:
            raise FeatureError('video_journal_full')
        # Refuse to replace a foreign/symlink/FIFO metadata entry.
        try:
            self._read('.video-journal.json', MAX_JOURNAL_BYTES)
        except FileNotFoundError:
            pass
        name = self._stage('.video-journal-', raw)
        try:
            os.replace(name, '.video-journal.json', src_dir_fd=self._root_fd, dst_dir_fd=self._root_fd)
            os.fsync(self._root_fd)
        finally:
            try:
                os.unlink(name, dir_fd=self._root_fd)
            except FileNotFoundError:
                pass

    def _release(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
        if self._root_fd is not None:
            os.close(self._root_fd)
        self._root_fd = self._lock_fd = None
        self._records = self._request = None

    def begin(self, request_id, *, config):
        if not request_id_ok(request_id) or type(config) is not VideoConfig:
            raise FeatureError('invalid_video_request')
        with self._guard:
            if self._root_fd is not None:
                return self.result_type(request_id, 'running', 'video_busy')
            try:
                self._open()
                self._lock_fd = os.open('.video.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                                        | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=self._root_fd)
                _file_ok(self._lock_fd, max_size=0, allow_empty=True)
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self._release()
                    return self.result_type(request_id, 'running', 'video_busy')
                self._records = self._load()
                previous = self._records.get(request_id)
                if previous is not None:
                    if previous['fingerprint'] != config.fingerprint:
                        result = self.result_type(request_id, 'rejected', 'request_id_conflict')
                    else:
                        result = self.result_type(**previous['result'])
                        if result.status == 'running':
                            result = replace(result, status='unknown', code='video_interrupted',
                                             stop_reason='power_loss_or_interruption')
                    self._release()
                    return result
                if len(self._records) >= MAX_RECORDS:
                    raise FeatureError('video_journal_full')
                self._space(config.max_bytes, config.min_free_bytes)
                running = self.result_type(request_id, 'running', 'video_recording')
                self._request = request_id
                self._records[request_id] = {'fingerprint': config.fingerprint, 'result': asdict(running)}
                self._write()
                return None
            except BaseException as error:
                self._release()
                if isinstance(error, FeatureError) or not isinstance(error, Exception):
                    raise
                raise FeatureError('video_storage_failed') from None

    def finish(self, result):
        with self._guard:
            try:
                if self._records is None or self._request != result.request_id:
                    raise FeatureError('video_reservation_missing')
                self._records[result.request_id]['result'] = asdict(result)
                self._write()
                return result
            finally:
                self._release()

    def publish(self, request_id, payload, *, config, stop_reason, cancel_check=lambda: None):
        with self._guard:
            if self._request != request_id or self._records is None:
                raise FeatureError('video_reservation_missing')
            meta = self.validate_payload(payload)
            if len(payload) > config.max_bytes or meta['duration'] > config.max_duration:
                raise FeatureError('video_limit_exceeded')
            self._space(len(payload), config.min_free_bytes)
            name = self._stage('.video-stage-', payload)
            video_id = 'video_' + hashlib.sha256(request_id.encode()).hexdigest()[:32]
            destination = video_id + '.avi'
            committed = False
            try:
                written = self._read(name, MAX_VIDEO_BYTES)
                if written != payload:
                    raise FeatureError('video_verification_failed')
                self.validate_payload(written)
                cancel_check()
                os.link(name, destination, src_dir_fd=self._root_fd,
                        dst_dir_fd=self._root_fd, follow_symlinks=False)
                committed = True
                os.fsync(self._root_fd)
                result = self.result_type(request_id, self.saved_status, 'video_saved', stop_reason,
                                     video_id, hashlib.sha256(written).hexdigest(), len(written),
                                     meta['duration'], completed=True, simulated=self.simulated,
                                     executed=not self.simulated)
            except BaseException as error:
                if committed:
                    raise FeatureError('video_saved_state_unknown') from None
                if isinstance(error, FileExistsError):
                    raise FeatureError('video_destination_exists') from None
                raise
            finally:
                try:
                    os.unlink(name, dir_fd=self._root_fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    # A committed artifact is durable even if its staging link
                    # cannot be removed. Never turn that into a clean failure
                    # or erase/repeat either retained file.
                    raise FeatureError('video_saved_state_unknown' if committed
                                       else 'video_storage_failed') from None
            try:
                return self.finish(result)
            except Exception:
                # Media was committed; never delete or repeat it on metadata IO
                # failure. Persisted running reservation blocks automatic retry.
                return replace(result, code='video_saved_journal_pending')

    def read(self, video_id):
        if type(video_id) is not str or re.fullmatch(r'video_[0-9a-f]{32}', video_id) is None:
            raise FeatureError('invalid_video_id')
        with self._guard:
            if self._root_fd is not None:
                raise FeatureError('video_busy')
            try:
                self._open(create=False)
                raw = self._read(video_id + '.avi', MAX_VIDEO_BYTES)
                self.validate_payload(raw)
                return raw
            except (OSError, ValueError, TypeError):
                raise FeatureError('video_unavailable') from None
            finally:
                self._release()

    def close(self):
        """Release a reservation; leave running recovery metadata and all media."""
        with self._guard:
            self._release()


class VideoRecorder:
    """Owner-polled bounded simulation; uses the one existing CameraBroker.

    Product live capability remains denied regardless of environment, model
    output or consent. Native camera codecs/timeouts are NOT_RUN, and a close
    failure is retained as UNKNOWN rather than claiming the device was stopped.
    """
    def __init__(self, broker, *, store=None, clock=time.monotonic,
                 simulated=True, config=None):
        if type(simulated) is not bool:
            raise FeatureError('invalid_video_config')
        self.broker, self.store, self.clock = broker, store, clock
        self.simulated, self.config = simulated, config or VideoConfig()
        if type(self.config) is not VideoConfig:
            raise FeatureError('invalid_video_config')
        self._guard = RLock()
        self._owner = object()
        self._generation = -1
        self._request = self._payload = self._started = self._last_frame = None
        self._frames = 0
        self._result = None
        self._closed = self._blocked = False

    def _make_result(self, *args, **kwargs):
        return VideoResult(*args, **kwargs)

    def _denied(self, request_id, code):
        return self._make_result(request_id, 'rejected', code, executed=False)

    def _mode_allowed(self):
        return self.simulated is True and self.broker.simulated is True

    def _active_check(self, generation):
        if (type(generation) is not int or generation < 0 or generation != self._generation):
            raise FeatureError('stale_video_generation')
        if self._request is None:
            raise FeatureError('video_not_recording')

    def start_video_recording(self, request_id, *, generation, consent=False):
        if not request_id_ok(request_id) or type(generation) is not int or generation < 0:
            raise FeatureError('invalid_video_request')
        with self._guard:
            if self._closed or self._blocked:
                return self._denied(request_id, 'video_closed')
            if not self._mode_allowed():
                return self._denied(request_id, 'camera_video_denied')
            if consent is not True:
                return self._denied(request_id, 'video_consent_required')
            if self.store is None:
                return self._denied(request_id, 'video_storage_not_selected')
            if generation < self._generation:
                return self._denied(request_id, 'stale_video_generation')
            if self._request is not None:
                if self._request == request_id and generation == self._generation:
                    return self._result
                return self._denied(request_id, 'video_busy')
            claimed = reserved = False
            try:
                self.broker.claim(self._owner)
                claimed = True
                prior = self.store.begin(request_id, config=self.config)
                if prior is not None:
                    self._result = prior
                    return prior
                reserved = True
                self._generation, self._request = generation, request_id
                started = self.clock()
                if not _finite(started) or started < 0:
                    raise FeatureError('video_clock_invalid')
                self._started, self._last_frame, self._frames = started, None, 0
                self._payload = None
                self.broker.open(owner=self._owner, simulated_only=self.simulated is True)
                if not self._mode_allowed():
                    raise FeatureError('camera_video_denied')
                self._result = self._make_result(request_id, 'running', 'video_recording')
                self._capture()
                return self._result
            except Exception as error:
                code = error.code if isinstance(error, FeatureError) else 'video_start_failed'
                result = self._make_result(request_id, 'failed', code, 'start_failed')
                if claimed:
                    if not self._release_camera():
                        result = replace(result, status='unknown', code='video_close_failed')
                    claimed = False
                if reserved:
                    try:
                        self.store.finish(result)
                    except Exception:
                        result = replace(result, status='unknown', code='video_journal_pending')
                self._request, self._payload = None, None
                self._result = result
                return result
            finally:
                if claimed and self._request is None:
                    self._release_camera()

    def _capture(self):
        frame = self.broker.read_frame(owner=self._owner)
        if frame is None:
            return
        now = self.clock()
        if not _finite(now) or now < self._started:
            raise FeatureError('video_clock_invalid')
        if frame.captured_at > now + .05 or now - frame.captured_at > 2:
            raise FeatureError('stale_video_frame')
        if validate_png(frame.payload) != (frame.width, frame.height):
            raise FeatureError('invalid_video_frame')
        # Deliberately generated low-resolution fixture, not a real PNG encoder.
        count = self._frames + 1
        if count > math.floor(self.config.max_duration * self.config.fps):
            self._complete('video_duration_limit')
            return
        payload = synthetic_avi_bytes(width=16, height=16, fps=self.config.fps, frames=count)
        if len(payload) > self.config.max_bytes:
            if self._frames:
                self._complete('video_byte_limit')
                return
            raise FeatureError('video_byte_limit')
        self._payload, self._frames, self._last_frame = payload, count, now

    def _release_camera(self):
        if not self.broker.close(owner=self._owner):
            self._blocked = True
            return False
        if not self.broker.release(self._owner):
            self._blocked = True
            return False
        return True

    def _finish_failure(self, code, *, cancelled=False):
        request = self._request
        if request is None:
            return self._result
        result = self._make_result(request, 'cancelled' if cancelled else 'failed', code, code)
        if not self._release_camera():
            result = replace(result, status='unknown', code='video_close_failed')
        try:
            result = self.store.finish(result)
        except Exception:
            result = replace(result, status='unknown', code='video_journal_pending')
        self._request, self._payload = None, None
        self._result = result
        return result

    def _complete(self, reason):
        request = self._request
        if not self._release_camera():
            result = self._make_result(request, 'unknown', 'video_close_failed', reason)
            try:
                self.store.finish(result)
            except Exception:
                pass
            self._request, self._payload, self._result = None, None, result
            return result
        try:
            if self._payload is None:
                raise FeatureError('video_empty')
            result = self.store.publish(request, self._payload, config=self.config, stop_reason=reason)
        except Exception as error:
            code = error.code if isinstance(error, FeatureError) else 'video_storage_failed'
            result = self._make_result(request, 'unknown' if code == 'video_saved_state_unknown' else 'failed', code, reason)
            try:
                self.store.finish(result)
            except Exception:
                result = replace(result, status='unknown', code='video_journal_pending')
        self._request, self._payload, self._result = None, None, result
        return result

    def poll(self, *, generation):
        with self._guard:
            if self._request is None:
                return self.snapshot()
            if generation != self._generation or type(generation) is not int:
                self._finish_failure('stale_video_generation', cancelled=True)
                return self.snapshot()
            try:
                now = self.clock()
                if not _finite(now) or now < self._started:
                    raise FeatureError('video_clock_invalid')
                if now - self._started >= self.config.max_duration:
                    self._complete('video_duration_limit')
                elif self._last_frame is None or now - self._last_frame >= 1 / self.config.fps:
                    self._capture()
            except Exception as error:
                self._finish_failure(error.code if isinstance(error, FeatureError) else 'video_capture_failed')
            return self.snapshot()

    def stop_video_recording(self, request_id, *, generation):
        if not request_id_ok(request_id):
            raise FeatureError('invalid_video_request')
        with self._guard:
            if self._request is None and self._result is not None and self._result.request_id == request_id:
                return self._result
            if self._request != request_id:
                return self._denied(request_id, 'video_request_mismatch')
            self._active_check(generation)
            return self._complete('user_stop')

    def cancel(self, reason='video_cancelled'):
        if not valid_id(reason):
            raise FeatureError('invalid_video_stop_reason')
        with self._guard:
            return self._finish_failure(reason, cancelled=True)

    def snapshot(self):
        with self._guard:
            readiness = ('UNSUPPORTED' if self.simulated is not True else 'UNVERIFIED' if self._blocked
                         else 'ASSET_PENDING' if self.store is None else 'READY')
            return {'contract': CONTRACT, 'recording': self._request is not None,
                    'generation': max(0, self._generation), 'readiness': readiness,
                    'code': 'camera_video_denied' if self.simulated is not True
                            else 'video_storage_not_selected' if self.store is None
                            else 'video_close_failed' if self._blocked else 'video_closed' if self._closed
                            else 'video_recording' if self._request is not None else 'video_ready',
                    'format': 'synthetic_uncompressed_dib_avi', 'audio_streams': 0,
                    'simulated': True, 'executed': False, 'actual_camera_verified': False,
                    'max_duration': self.config.max_duration, 'max_bytes': self.config.max_bytes,
                    'min_free_bytes': self.config.min_free_bytes,
                    'frames': self._frames, 'result': self._result.public() if self._result else None}

    def close(self):
        with self._guard:
            if self._request is not None:
                self._finish_failure('video_app_exit', cancelled=True)
            self._closed = True
            if self.store is not None:
                self.store.close()
            return not self._blocked
