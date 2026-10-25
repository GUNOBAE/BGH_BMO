"""One lazy capture owner shared by preview, tracking and synchronous photo."""
from threading import Lock, RLock

from ..contracts import FeatureError
from .models import Frame


class CameraBroker:
    def __init__(self, backend_factory):
        self.factory = backend_factory
        self._state, self._operation = RLock(), Lock()
        self._backend = None
        self._epoch = 0
        self._owner = None
        self._seq = self._stamp = None
        self.simulated = getattr(backend_factory, 'simulated', True) is not False

    @property
    def opened(self):
        with self._state:
            return self._backend is not None

    @property
    def stream_generation(self):
        with self._state:
            return self._epoch

    def claim(self, owner):
        """Reserve this one broker for an explicit video session.

        Legacy preview/photo callers have owner=None. A reservation is only
        possible while off; callers cannot steal or close another lease.
        """
        if owner is None:
            raise FeatureError('invalid_camera_owner')
        if not self._operation.acquire(blocking=False):
            raise FeatureError('camera_busy')
        try:
            with self._state:
                if self._owner is owner:
                    return True
                if self._owner is not None or self._backend is not None:
                    raise FeatureError('camera_busy')
                self._owner = owner
                return True
        finally:
            self._operation.release()

    def release(self, owner):
        with self._state:
            if owner is None or self._owner is not owner or self._backend is not None:
                return False
            self._owner = None
            return True

    def _check_owner(self, owner):
        if self._owner is not owner:
            raise FeatureError('camera_busy')

    def open(self, *, cancel_check=None, owner=None, simulated_only=False):
        if cancel_check is not None:
            cancel_check()
        with self._state:
            self._check_owner(owner)
            if self._backend is not None:
                return
            expected = self._epoch
        with self._operation:
            if cancel_check is not None:
                cancel_check()
            with self._state:
                self._check_owner(owner)
                if expected != self._epoch:
                    raise FeatureError('camera_cancelled')
                if self._backend is not None:
                    return
                self._epoch += 1
                epoch = self._epoch
            backend = None
            try:
                backend = self.factory()
                if simulated_only and getattr(backend, 'simulated', None) is not True:
                    raise FeatureError('camera_real_disabled')
                if getattr(backend, 'cooperative_cancel', False) is True:
                    backend.open(epoch, cancel_check=cancel_check)
                else:
                    backend.open(epoch)
                if cancel_check is not None:
                    cancel_check()
                with self._state:
                    self._check_owner(owner)
                    if epoch != self._epoch:
                        raise FeatureError('camera_cancelled')
                    self._backend = backend
                    self.simulated = backend.simulated is not False
                    self._seq = self._stamp = None
            except BaseException as error:
                if backend is not None:
                    try:
                        backend.close()
                    except Exception:
                        pass
                if not isinstance(error, Exception):
                    raise
                raise FeatureError('camera_open_failed') from None

    def _call(self, method, cancel_check=None, owner=None):
        with self._operation:
            if cancel_check is not None:
                cancel_check()
            with self._state:
                self._check_owner(owner)
                backend, epoch = self._backend, self._epoch
            if backend is None:
                raise FeatureError('camera_off')
            try:
                if getattr(backend, 'cooperative_cancel', False) is True:
                    frame = getattr(backend, method)(cancel_check=cancel_check)
                else:
                    frame = getattr(backend, method)()
            except BaseException:
                with self._state:
                    if backend is self._backend:
                        self.close(owner=owner)
                raise
            if cancel_check is not None:
                cancel_check()
            with self._state:
                if epoch != self._epoch or backend is not self._backend:
                    raise FeatureError('camera_cancelled')
                if not isinstance(frame, Frame) or frame.stream_generation != epoch:
                    raise FeatureError('invalid_camera_frame')
                if self._seq is not None and (frame.seq <= self._seq or frame.captured_at < self._stamp):
                    return None
                self._seq, self._stamp = frame.seq, frame.captured_at
                return frame

    def read_frame(self, *, cancel_check=None, owner=None):
        return self._call('read_frame', cancel_check, owner)

    def capture_photo(self, *, cancel_check=None, owner=None):
        frame = self._call('capture_photo', cancel_check, owner)
        if frame is None:
            raise FeatureError('stale_photo')
        return frame

    def close(self, *, owner=None):
        # Do not wait on a capture operation to invalidate its future result.
        with self._state:
            if self._owner is not owner:
                return False
            self._epoch += 1
            backend, self._backend = self._backend, None
            self._seq = self._stamp = None
        if backend is not None:
            try:
                if backend.close() is False:
                    return False
            except Exception:
                return False
        return True
