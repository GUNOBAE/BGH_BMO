"""Private atomic PNG publication and injectable metadata-only dedup journal."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from threading import RLock
from typing import Protocol
import fcntl

from ..contracts import FeatureError, request_id_ok
from .codec import validate_png
from .models import PhotoResult


class PhotoLedger(Protocol):
    """begin returns a prior result, or None while reserving a new operation.

    A host journal adapter may implement this protocol instead of keeping two
    ledgers. An interrupted running entry must return unknown, never recapture.
    finish must release its reservation even when persistence fails.
    """
    def begin(self, request_id, fingerprint, *, simulated): ...
    def finish(self, result): ...


def private_root(root):
    root = Path(root).expanduser().absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise FeatureError('unsafe_photo_root')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not root.is_dir() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise FeatureError('unsafe_photo_root')
    return root


class JsonPhotoLedger:
    """Single private journal lease spans capture, including across processes."""
    def __init__(self, root):
        self.root = Path(root)
        self._lease = self._records = self._request = None
        self._lock = RLock()

    def _load(self):
        path = self.root / '.photo-journal.json'
        if not path.exists():
            return {}
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise FeatureError('photo_journal_invalid')
        records = json.loads(path.read_text())
        if type(records) is not dict or len(records) > 1024:
            raise FeatureError('photo_journal_invalid')
        return records

    def _write(self):
        descriptor, name = tempfile.mkstemp(prefix='.journal-', dir=self.root)
        try:
            with os.fdopen(descriptor, 'w') as output:
                json.dump(self._records, output, allow_nan=False, separators=(',', ':'))
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.root / '.photo-journal.json')
        finally:
            Path(name).unlink(missing_ok=True)

    def _release(self):
        if self._lease is not None:
            self._lease.close()
        self._lease = self._records = self._request = None

    def begin(self, request_id, fingerprint, *, simulated):
        with self._lock:
            if self._lease is not None:
                return PhotoResult(request_id, 'running', 'photo_busy', simulated=simulated)
            try:
                self.root = private_root(self.root)
                descriptor = os.open(self.root / '.photo-journal.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                self._lease = os.fdopen(descriptor, 'a')
                try:
                    fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self._release()
                    return PhotoResult(request_id, 'running', 'photo_busy', simulated=simulated)
                self._records = self._load()
                previous = self._records.get(request_id)
                if previous is not None:
                    if previous['fingerprint'] != fingerprint:
                        result = PhotoResult(request_id, 'rejected', 'request_id_conflict', simulated=simulated)
                    else:
                        result = PhotoResult(**previous['result'])
                        if result.status == 'running':
                            result = replace(result, status='unknown', code='photo_interrupted', completion='unknown')
                    self._release()
                    return result
                if len(self._records) >= 1024:
                    raise FeatureError('photo_journal_full')
                running = PhotoResult(request_id, 'running', 'photo_running', simulated=simulated)
                self._records[request_id] = dict(fingerprint=fingerprint, result=running.public())
                self._request = request_id
                self._write()
                return None
            except BaseException as error:
                self._release()
                if not isinstance(error, Exception):
                    raise
                raise FeatureError('photo_storage_failed') from None

    def finish(self, result):
        with self._lock:
            try:
                if self._request != result.request_id or self._records is None:
                    raise FeatureError('photo_reservation_missing')
                self._records[result.request_id]['result'] = result.public()
                self._write()
            finally:
                self._release()


class PhotoStore:
    def __init__(self, root, *, ledger=None):
        self.root = Path(root).expanduser().absolute()
        self.ledger = ledger if ledger is not None else JsonPhotoLedger(self.root)

    def begin(self, request_id, *, simulated=True, fingerprint='camera_png_v1'):
        if not request_id_ok(request_id) or type(fingerprint) is not str or not fingerprint or len(fingerprint) > 160:
            raise FeatureError('invalid_photo_request')
        return self.ledger.begin(request_id, fingerprint, simulated=simulated)

    def finish(self, result):
        self.ledger.finish(result)
        return result

    def save(self, request_id, frame, *, simulated, cancel_check):
        width, height = validate_png(frame.payload)
        if (width, height) != (frame.width, frame.height):
            raise FeatureError('invalid_photo')
        if not frame.focus_ok:
            raise FeatureError('photo_focus_failed')
        root = private_root(self.root)
        photo_id = 'photo_' + hashlib.sha256(request_id.encode()).hexdigest()[:32]
        destination = root / (photo_id + '.png')
        descriptor, name = tempfile.mkstemp(prefix='.capture-', suffix='.tmp', dir=root)
        committed = False
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(frame.payload)
                output.flush()
                os.fsync(output.fileno())
            written = Path(name).read_bytes()
            if written != frame.payload:
                raise FeatureError('photo_verification_failed')
            validate_png(written)
            cancel_check()
            # Atomic same-filesystem publication with no overwrite/symlink race.
            os.link(name, destination, follow_symlinks=False)
            committed = True
            result = PhotoResult(request_id, 'simulated' if simulated else 'succeeded', 'photo_saved',
                                 photo_id, hashlib.sha256(written).hexdigest(), len(written), simulated,
                                 not simulated, True, 'synthetic_saved' if simulated else 'saved')
            # Cancellation after publication cannot erase the committed image.
            try:
                self.ledger.finish(result)
            except Exception:
                return replace(result, code='photo_saved_journal_pending')
            return result
        except Exception:
            if committed:
                # Preserve the completed artifact even if metadata persistence
                # fails; a running journal entry prevents an automatic replay.
                raise FeatureError('photo_saved_state_unknown') from None
            raise
        finally:
            Path(name).unlink(missing_ok=True)

    def read(self, photo_id):
        if type(photo_id) is not str or re.fullmatch(r'photo_[0-9a-f]{32}', photo_id) is None:
            raise FeatureError('invalid_photo_id')
        try:
            path = private_root(self.root) / (photo_id + '.png')
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise FeatureError('invalid_photo')
                payload = handle.read(8 * 1024 * 1024 + 1)
            validate_png(payload)
            return payload
        except Exception:
            raise FeatureError('photo_unavailable') from None
