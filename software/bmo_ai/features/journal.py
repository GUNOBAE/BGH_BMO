"""Private idempotency records; restart never replays an unfinished action."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from ..private_credentials import _open_directory, CredentialError
from .contracts import FeatureError, STATUSES, request_id_ok, valid_id


class ActionJournal:
    def __init__(self, directory=None):
        self._db = None
        self._directory = None
        if directory is None:
            self._db = sqlite3.connect(":memory:", timeout=.1)
        else:
            path = Path(directory)
            if not path.is_absolute():
                raise FeatureError("unsafe_journal")
            try:
                self._directory = _open_directory(path, create=True)
                fd = os.open("actions.sqlite", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                             0o600, dir_fd=self._directory)
            except (CredentialError, OSError):
                self.close()
                raise FeatureError("unsafe_journal") from None
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    raise FeatureError("unsafe_journal")
                self._db = sqlite3.connect(f"/proc/self/fd/{self._directory}/actions.sqlite", timeout=.1)
                current = os.stat("actions.sqlite", dir_fd=self._directory, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise FeatureError("unsafe_journal")
            except Exception:
                self.close()
                raise FeatureError("unsafe_journal") from None
            finally:
                os.close(fd)
        try:
            self._db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL, code TEXT NOT NULL)")
            # An ACK timeout or process death is unknown, never 'not executed'.
            self._db.execute("UPDATE requests SET state='unknown', code='restart_unknown' WHERE state IN ('accepted','running')")
            self._db.commit()
        except sqlite3.Error:
            self.close()
            raise FeatureError("journal_unavailable") from None

    def begin(self, request_id, intent):
        if not request_id_ok(request_id) or type(intent) is not dict or any(type(key) is not str for key in intent):
            raise FeatureError("invalid_request")
        try:
            raw = json.dumps(intent, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
            if len(raw) > 4096:
                raise ValueError()
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise FeatureError("invalid_request") from None
        if self._db is None:
            raise FeatureError("journal_closed")
        fingerprint = hashlib.sha256(raw).hexdigest()
        try:
            row = self._db.execute("SELECT fingerprint,state,code FROM requests WHERE id=?", (request_id,)).fetchone()
        except sqlite3.Error:
            raise FeatureError("journal_unavailable") from None
        if row:
            if row[0] != fingerprint:
                raise FeatureError("request_conflict")
            return {"status": row[1], "code": row[2], "duplicate": True}
        try:
            self._db.execute("INSERT INTO requests VALUES (?,?,'accepted','accepted')", (request_id, fingerprint))
            self._db.commit()
        except sqlite3.Error:
            raise FeatureError("journal_unavailable") from None
        return {"status": "accepted", "code": "accepted", "duplicate": False}

    def finish(self, request_id, status, code):
        if not request_id_ok(request_id) or status not in STATUSES or not valid_id(code):
            raise FeatureError("invalid_result")
        if self._db is None:
            raise FeatureError("journal_closed")
        try:
            cursor = self._db.execute("UPDATE requests SET state=?,code=? WHERE id=?", (status, code, request_id))
            if cursor.rowcount != 1:
                raise FeatureError("unknown_request")
            self._db.commit()
        except sqlite3.Error:
            raise FeatureError("journal_unavailable") from None

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._directory is not None:
            os.close(self._directory)
            self._directory = None
