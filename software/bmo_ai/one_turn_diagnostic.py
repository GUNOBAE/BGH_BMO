"""Explicit, private capture of one conversation turn; never a general logger.

The caller supplies only the current transcript, its structured model reply and
allowlisted stage metadata. This module never reads history, credentials, audio
or devices, and never makes a network request.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import stat
from threading import RLock


class OneTurnDiagnosticError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('The private one-turn diagnostic destination is unavailable.')


_TURN = re.compile(r'[A-Za-z0-9:_-]{1,128}\Z')
_ENUMS = frozenset((
    'none', 'neutral', 'happy', 'sad', 'angry', 'crying', 'surprised',
    'dialogue', 'acting', 'boot', 'idle', 'host', 'manual', 'auto',
    'validated', 'degraded', 'legacy', 'unvalidated', 'notice',
    'invalid_character', 'invalid_emotion', 'invalid_request', 'stale_request',
    'no_current_user', 'quoted_request', 'informational_request', 'past_request',
    'future_request', 'capability_question', 'negated_request', 'confirmation_missing',
    'character_repaired', 'emotion_repaired', 'request_denied', 'camera_disabled',
    'photo', 'subtitles', 'execute', 'clarify', 'staged', 'waiting', 'prepared',
    'started', 'ended', 'cancelled', 'error', 'finished', 'ok', 'not_run',
    'text', 'voice', 'microphone', 'transcribed', 'empty_stt', 'no_speech',
    'synthetic', 'fish', 'piper', 'plain_fallback', 'fish_s2_allowlist_v1',
    'fish_character_s2_v1', 'fish_character_drama3_v1', 'calm', 'warm', 'excited',
    'curious', 'proud', 'playfully_annoyed', 'soft_sad', 'tearful',
    'theatrical_crying', 'theatrical_angry', 'empathetic', 'manual_override',
))
_ENUM_FIELDS = frozenset((
    'requested_emotion', 'model_emotion', 'resolved_emotion', 'emitted_emotion',
    'resolved_source', 'validation', 'degradation_reason', 'request_validation',
    'request_kind', 'semantic_emotion', 'emission_status', 'emotion_mode',
    'input_kind', 'tts_delivery', 'tts_mechanism', 'tts_backend',
    'tts_requested_emotion', 'voice_delivery', 'voice_style', 'delivery_id',
    'stt_status', 'stt_result', 'reason',
))
_BOOL_FIELDS = frozenset((
    'replayed', 'character_life', 'character_life_enabled', 'tts_override_soft',
    'override_soft', 'metadata_degraded', 'manual_override',
))
_INT_FIELDS = frozenset(('turn', 'speech_chars', 'stt_chars', 'schema_version', 'generation'))


def _path(value):
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError, OSError):
        raise OneTurnDiagnosticError('diagnostic_absolute_path_required') from None
    if not path.is_absolute() or '..' in path.parts or path.name in ('', '.', '..'):
        raise OneTurnDiagnosticError('diagnostic_absolute_path_required')
    return path


def validate_destination(value):
    """Read-only preflight, suitable before launching or resolving credentials."""
    path = _path(value)
    for parent in (path.parent, *path.parent.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise OneTurnDiagnosticError('diagnostic_destination_unavailable') from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OneTurnDiagnosticError('diagnostic_unsafe_parent')
        try:
            (parent / '.git').lstat()
        except FileNotFoundError:
            pass
        except OSError:
            raise OneTurnDiagnosticError('diagnostic_destination_unavailable') from None
        else:
            raise OneTurnDiagnosticError('diagnostic_outside_checkout_required')
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        raise OneTurnDiagnosticError('diagnostic_destination_unavailable') from None
    else:
        raise OneTurnDiagnosticError('diagnostic_destination_exists')
    try:
        parent = path.parent.lstat()
    except FileNotFoundError:
        return path
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise OneTurnDiagnosticError('diagnostic_private_parent_required')
    return path


def _trace(value):
    result = {}
    if type(value) is not dict:
        return result
    for name, item in value.items():
        if name in _ENUM_FIELDS and (item is None or type(item) is str and item in _ENUMS):
            result[name] = item
        elif name in _BOOL_FIELDS and type(item) is bool:
            result[name] = item
        elif name in _INT_FIELDS and type(item) is int and 0 <= item <= 1000000:
            result[name] = item
    result['contains_text'] = False
    return result


class OneTurnDiagnostic:
    def __init__(self, path):
        self._lock = RLock()
        self._armed, self._captured, self._complete = True, False, False
        self._error = None
        self._data = self._turn = None
        self._dirfd = self._filefd = None
        destination = validate_destination(path)
        # Create each new directory privately, including intermediate parents.
        missing, parent = [], destination.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        try:
            for parent in reversed(missing):
                parent.mkdir(mode=0o700)
            validate_destination(destination)
            self._dirfd = os.open(destination.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self._name = destination.name
            fd = os.open(self._name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=self._dirfd)
            try:
                info = os.fstat(fd)
                self._identity = (info.st_dev, info.st_ino)
                os.write(fd, b'{"version":1,"captured":false}\n')
                os.fsync(fd)
            except BaseException:
                os.close(fd)
                raise
            self._filefd = fd  # Keep the inode alive so replacement cannot reuse its identity.
        except (OSError, OneTurnDiagnosticError):
            self.close()
            raise OneTurnDiagnosticError('diagnostic_destination_unavailable') from None

    def snapshot(self):
        with self._lock:
            return dict(armed=self._armed, captured=self._captured,
                complete=self._complete, error_code=self._error)

    def _write(self):
        temporary = '.bmo-diagnostic-' + secrets.token_hex(12)
        descriptor = None
        try:
            parent = os.fstat(self._dirfd)
            if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
                raise OSError('parent permissions changed')
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=self._dirfd)
            encoded = (json.dumps(self._data, ensure_ascii=False, allow_nan=False) + '\n').encode()
            with os.fdopen(descriptor, 'wb') as output:
                descriptor = None
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
                new_info = os.fstat(output.fileno())
            current = os.stat(self._name, dir_fd=self._dirfd, follow_symlinks=False)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or current.st_uid != os.geteuid() or stat.S_IMODE(current.st_mode) != 0o600
                    or (current.st_dev, current.st_ino) != self._identity):
                raise OSError('destination replaced')
            os.replace(temporary, self._name, src_dir_fd=self._dirfd, dst_dir_fd=self._dirfd)
            published = os.open(self._name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._dirfd)
            published_info = os.fstat(published)
            if (published_info.st_dev, published_info.st_ino) != (new_info.st_dev, new_info.st_ino):
                os.close(published)
                raise OSError('destination replaced after publication')
            os.close(self._filefd)
            self._filefd = published
            self._identity = (new_info.st_dev, new_info.st_ino)
            os.fsync(self._dirfd)
            return True
        except (OSError, TypeError, ValueError):
            self._error = 'diagnostic_write_failed'
            self._armed = False
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=self._dirfd)
            except (OSError, TypeError):
                pass

    def record_model(self, turn_id, transcript, model_reply):
        """Consume this capture once. Invalid/failing input never arms another turn."""
        with self._lock:
            if not self._armed or self._dirfd is None:
                return False
            self._armed = False
            if (type(turn_id) is not str or not _TURN.fullmatch(turn_id)
                    or type(transcript) is not str or len(transcript) > 8000
                    or type(model_reply) is not str or len(model_reply) > 16384):
                self._error = 'diagnostic_invalid_record'
                return False
            self._turn = turn_id
            self._data = dict(version=1, turn_id=turn_id, transcript=transcript,
                model_reply=model_reply, trace={})
            self._captured = self._write()
            return self._captured

    def record_trace(self, turn_id, trace):
        with self._lock:
            if (not self._captured or self._dirfd is None or self._error
                    or turn_id != self._turn):
                return False
            self._data['trace'].update(_trace(trace))
            complete = self._data['trace'].get('emission_status') in ('ended', 'cancelled', 'error', 'finished')
            saved = self._write()
            if saved and complete:
                self._complete = True
            return saved

    def close(self):
        with self._lock:
            self._armed = False
            if self._filefd is not None:
                os.close(self._filefd)
                self._filefd = None
            if self._dirfd is not None:
                os.close(self._dirfd)
                self._dirfd = None
