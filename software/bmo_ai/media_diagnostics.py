"""Bounded local music-event evidence, separate from speech diagnostics.

The default is memory-only. A live Desktop must explicitly enable persistence
and bind its run identity before this module touches the user's state directory.
Only rebuilt identifiers, enums and numeric state are retained; never provider
payloads, media paths, titles, captions, transcripts, audio or credentials.
"""
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
from threading import RLock
import time

from .daily_preferences import _open_directory
from .features.contracts import valid_id, request_id_ok


CAPACITY = 32
MAX_BYTES = 131072
_FILE = "events.json"
_SHA = re.compile(r"[a-f0-9]{40}\Z")
_RUN = re.compile(r"[a-f0-9]{32}\Z")
EVENTS = frozenset(("start", "pause", "resume", "seek", "replay", "stop",
                    "completed", "failed", "cancelled", "lifecycle", "heartbeat_expired"))
SOURCES = frozenset(("touch_pause", "touch_resume", "touch_seek", "touch_replay", "touch_stop",
    "touch_back", "gp8_short", "gp8_hold", "escape", "video_play_rejected", "video_error",
    "blur", "focus", "hidden", "visible", "pagehide", "host_control", "mode_change",
    "browser_disconnected", "shell_heartbeat_expired", "app_close", "media_backend", "voice_command"))
STATES = frozenset(("idle", "preparing", "starting", "playing", "paused", "ended", "completed",
                    "failed", "cancelled", "stopped", "unknown"))
# These are product codes, not a regex that could preserve an arbitrary error
# message or a token. Unknown exceptions stay distinct from no error at all.
ERROR_CODES = frozenset((
    "unknown_error_code", "media_failed", "media_start_failed", "media_control_failed", "media_poll_failed",
    "media_alsa_unavailable", "media_output_unavailable", "media_output_format",
    "media_output_underrun", "media_output_failed", "media_output_clock_failed",
    "media_output_start_failed", "media_output_start_timeout", "media_output_stalled",
    "media_output_cleanup_failed", "cleanup_failed", "media_backend_unverified",
    "media_busy", "media_inactive", "media_video_inactive", "media_changed", "media_runtime_missing",
    "media_hash_mismatch", "media_too_large", "media_duration_mismatch", "media_variant_unavailable",
    "media_review_failed", "media_review_required", "media_import_codec_unsupported",
    "media_disk_space_low", "media_library_capacity", "asset_pending", "unsafe_media_file",
    "unsafe_media_library", "unsafe_content_root", "unsafe_media_metadata",
    "invalid_content_root", "invalid_media_path", "invalid_media_hash", "invalid_media_manifest",
    "invalid_media_duration", "invalid_media_cue", "invalid_media_variant", "invalid_media_wav",
    "invalid_media_file", "invalid_media_clock", "invalid_media_backend", "invalid_media_position",
    "invalid_media_library", "invalid_media_metadata", "video_play_rejected", "video_error",
    "video_aborted", "video_network_error", "video_decode_error", "video_src_not_supported",
    "not_allowed", "not_supported", "aborted", "network", "decode", "unsupported", "unknown",
    "play_rejected", "browser_disconnected", "shell_heartbeat_expired",
))


def default_directory():
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "bmo" / "media-diagnostics"


def _number(value, upper):
    return (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= upper)


def _error(value):
    if value is None:
        return None
    return value if type(value) is str and value in ERROR_CODES else "unknown_error_code"


def _choose(values, *names):
    for name in names:
        if name in values and values[name] is not None:
            return values[name]
    return None


def _receipt(event, source, media, lifecycle, *, sequence, at, revision, run_id, error_code):
    if (type(event) is not str or event not in EVENTS or type(source) is not str or source not in SOURCES
            or type(sequence) is not int or not 1 <= sequence <= 1000000000 or not _number(at, 1000000000000)):
        return None
    media = media if type(media) is dict else {}
    lifecycle = lifecycle if type(lifecycle) is dict else {}
    catalog_id = _choose(media, "catalog_id", "active_id")
    request_id = _choose(media, "request_id", "request")
    position = _choose(media, "position_seconds", "position")
    duration = _choose(media, "duration_seconds", "length", "duration")
    state = _choose(media, "state", "outcome")
    variant = _choose(media, "variant", "media_mode")
    result = dict(version=1, sequence=sequence, at_monotonic=round(at, 6),
        runtime_revision=revision if type(revision) is str and _SHA.fullmatch(revision) else None,
        run_id=run_id if type(run_id) is str and _RUN.fullmatch(run_id) else None,
        event=event, source=source, catalog_id=catalog_id if valid_id(catalog_id) else None,
        variant=variant if type(variant) is str and variant in ("face", "video") else None,
        position_seconds=round(position, 6) if _number(position, 3600) else None,
        duration_seconds=round(duration, 6) if _number(duration, 3600) else None,
        state=state if type(state) is str and state in STATES else None,
        request_id=request_id if request_id_ok(request_id) else None,
        error_code=_error(error_code if error_code is not None else _choose(media, "error_code", "original_error_code")),
        cleanup_error=_error(_choose(media, "cleanup_error", "cleanup_error_code")), lifecycle={})
    for name in ("mode_generation", "playback_generation"):
        value = media.get(name)
        result[name] = value if type(value) is int and 0 <= value <= 1000000000 else None
    for name, upper in (('error_frame', 691200000), ('error_submitted_frame', 691200000),
                        ('error_queued_frames', 691200000), ('error_rate', 96000),
                        ('error_at_monotonic', 1000000000000)):
        value = media.get(name)
        if _number(value, upper):
            result[name] = value
    producer=media.get('producer')
    if producer in ('isolated_process','injected_sink_thread'):
        result['producer']=producer
    pid=media.get('worker_pid')
    if type(pid) is int and 0 < pid <= 4194304:result['worker_pid']=pid
    code=media.get('worker_exit_code')
    if type(code) is int and -255 <= code <= 255:result['worker_exit_code']=code
    for name in ("hidden", "focused", "connected"):
        value = lifecycle.get(name)
        result["lifecycle"][name] = value if type(value) is bool else None
    age = lifecycle.get("heartbeat_age_seconds")
    result["lifecycle"]["heartbeat_age_seconds"] = round(age, 6) if _number(age, 3600) else None
    return result


def _stored(value):
    if type(value) is not dict or type(value.get("version")) is not int or value["version"] != 1:
        return None
    rebuilt = _receipt(value.get("event"), value.get("source"), value, value.get("lifecycle"),
        sequence=value.get("sequence"), at=value.get("at_monotonic"), revision=value.get("runtime_revision"),
        run_id=value.get("run_id"), error_code=value.get("error_code"))
    return rebuilt if rebuilt == value else None


def _private_file(info):
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size > MAX_BYTES):
        raise ValueError("private bounded media evidence required")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate evidence field")
        result[key] = value
    return result


def _read(root):
    if root is None:
        return [], None
    fd = None
    try:
        fd = os.open(_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root)
        info = os.fstat(fd)
        _private_file(info)
        raw = os.read(fd, MAX_BYTES + 1)
        if len(raw) != info.st_size:
            raise ValueError("changed evidence")
        value = json.loads(raw, object_pairs_hook=_unique)
        if (type(value) is not dict or set(value) != {"version", "events"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["events"]) is not list or len(value["events"]) > CAPACITY
                or any(_stored(row) is None for row in value["events"])):
            raise ValueError("invalid evidence")
        return value["events"], (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
    except FileNotFoundError:
        return [], None
    finally:
        if fd is not None:
            os.close(fd)


class MediaDiagnostics:
    def __init__(self, *, clock=time.monotonic, runtime_revision=None, run_id=None, directory=None, persist=False):
        self._lock = RLock()
        self.clock, self.runtime_revision = clock, runtime_revision
        self.run_id = None
        self._events, self._sequence = [], 0
        self._persist = persist is True
        self._directory = directory
        self._code = "memory_only" if not self._persist else "awaiting_runtime"
        self._storage_ready, self._persisted = False, False
        if run_id is not None:
            self.bind_runtime(run_id)

    def bind_runtime(self, run_id):
        with self._lock:
            if (type(run_id) is not str or _RUN.fullmatch(run_id) is None
                    or self.run_id not in (None, run_id)):
                return False
            if self.run_id == run_id:
                return True
            self.run_id = run_id
            if not self._persist:
                return True
            root = None
            try:
                path = Path(self._directory) if self._directory is not None else default_directory()
                if not path.is_absolute() or ".." in path.parts or "\0" in str(path):
                    raise ValueError("invalid evidence directory")
                self._directory = path
                root = _open_directory(path)
                self._events, identity = _read(root)
                self._sequence = max((event["sequence"] for event in self._events), default=0)
                self._storage_ready, self._persisted = True, identity is not None
                self._code = "ready"
            except (OSError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
                self._code = "media_diagnostic_storage_unavailable"
            finally:
                if root is not None:
                    os.close(root)
            return True

    def record(self, event, source, media=None, lifecycle=None, error_code=None):
        with self._lock:
            try:
                receipt = _receipt(event, source, media, lifecycle, sequence=self._sequence+1,
                    at=self.clock(), revision=self.runtime_revision, run_id=self.run_id, error_code=error_code)
            except Exception:
                receipt = None
            if receipt is None:
                return None
            self._sequence = receipt["sequence"]
            self._events = (self._events + [receipt])[-CAPACITY:]
            if self._storage_ready:
                self._save(receipt)
            return deepcopy(receipt)

    def _save(self, receipt):
        root = lock = None
        temporary = None
        try:
            root = _open_directory(self._directory, create=True)
            lock = os.open(".events.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                           | os.O_CLOEXEC, 0o600, dir_fd=root)
            _private_file(os.fstat(lock))
            if os.fstat(lock).st_size != 0:
                raise ValueError("invalid diagnostic lock")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current, identity = _read(root)
            receipt["sequence"] = max(receipt["sequence"], max((row["sequence"] for row in current), default=0)+1)
            events = (current + [receipt])[-CAPACITY:]
            raw = (json.dumps(dict(version=1, events=events), allow_nan=False, sort_keys=True,
                              separators=(",", ":")) + "\n").encode()
            if len(raw) > MAX_BYTES:
                raise ValueError("media evidence exceeds bound")
            temporary = ".events-" + secrets.token_hex(12)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600, dir_fd=root)
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            _, latest = _read(root)
            if latest != identity:
                raise ValueError("concurrent evidence change")
            if identity is None:
                os.link(temporary, _FILE, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
                os.unlink(temporary, dir_fd=root)
            else:
                os.replace(temporary, _FILE, src_dir_fd=root, dst_dir_fd=root)
            temporary = None
            os.fsync(root)
            self._events, self._sequence = events, receipt["sequence"]
            self._persisted, self._code = True, "saved"
        except (OSError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            # Keep the bounded in-memory receipt, including the original error.
            # A diagnostic failure never changes media or schedules a retry.
            self._storage_ready = False
            self._code = "media_diagnostic_write_failed"
        finally:
            if temporary is not None and root is not None:
                try:
                    os.unlink(temporary, dir_fd=root)
                except OSError:
                    pass
            if lock is not None:
                os.close(lock)
            if root is not None:
                os.close(root)

    def snapshot(self):
        with self._lock:
            return deepcopy(dict(enabled=True, persistent=self._storage_ready, persisted=self._persisted,
                store_code=self._code, retained=len(self._events), capacity=CAPACITY,
                latest=self._events[-1] if self._events else None, events=self._events,
                contains_audio=False, contains_transcript=False))

    def close(self):
        with self._lock:
            self._storage_ready = False
