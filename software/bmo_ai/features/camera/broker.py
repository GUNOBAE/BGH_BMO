"""One lazy capture owner shared by preview, tracking and synchronous photo."""
from threading import Lock, RLock

from ..contracts import FeatureError
from .models import Frame, PreviewFrame


class CameraBroker:
    def __init__(self, backend_factory):
        self.factory = backend_factory
        self._state, self._operation = RLock(), Lock()
        self._backend = None
        self._epoch = 0
        self._owner = None
        self._owned_close_uncertain = False
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

    @property
    def cleanup_uncertain(self):
        with self._state:
            return self._owned_close_uncertain

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
                if self._owned_close_uncertain or self._owner is not None or self._backend is not None:
                    raise FeatureError('camera_busy')
                self._owner = owner
                return True
        finally:
            self._operation.release()

    def release(self, owner):
        with self._state:
            if (owner is None or self._owner is not owner or self._backend is not None
                    or self._owned_close_uncertain):
                return False
            self._owner = None
            return True

    def _check_owner(self, owner):
        if self._owner is not owner or self._owned_close_uncertain:
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
                        released = backend.close() is not False
                    except Exception:
                        released = False
                    if owner is not None and not released:
                        with self._state:
                            self._owned_close_uncertain = True
                if not isinstance(error, Exception):
                    raise
                raise FeatureError('camera_open_failed') from None

    def _call(self, method, cancel_check=None, owner=None, **options):
        with self._operation:
            if cancel_check is not None:
                cancel_check()
            with self._state:
                self._check_owner(owner)
                backend, epoch = self._backend, self._epoch
            if backend is None:
                raise FeatureError('camera_off')
            try:
                callback = getattr(backend, method, None)
                if callback is None and method == 'read_preview':
                    callback = backend.read_frame
                if getattr(backend, 'cooperative_cancel', False) is True:
                    frame = callback(cancel_check=cancel_check, **options)
                else:
                    frame = callback(**options)
            except BaseException as error:
                if isinstance(error, FeatureError) and error.code == 'photo_focus_uncertain':
                    raise  # Healthy stream; only an explicit current choice may proceed.
                with self._state:
                    if backend is self._backend:
                        self.close(owner=owner)
                raise
            if cancel_check is not None:
                cancel_check()
            with self._state:
                if epoch != self._epoch or backend is not self._backend:
                    raise FeatureError('camera_cancelled')
                types = (Frame, PreviewFrame) if method == 'read_preview' else (Frame,)
                if not isinstance(frame, types) or frame.stream_generation != epoch:
                    raise FeatureError('invalid_camera_frame')
                if self._seq is not None and (frame.seq <= self._seq or frame.captured_at < self._stamp):
                    return None
                if method == 'read_preview' and self._stamp is not None and frame.captured_at == self._stamp:
                    return None
                self._seq, self._stamp = frame.seq, frame.captured_at
                return frame

    def read_frame(self, *, cancel_check=None, owner=None):
        return self._call('read_frame', cancel_check, owner)

    def read_preview(self, *, cancel_check=None, owner=None):
        frame = self._call('read_preview', cancel_check, owner)
        if isinstance(frame, Frame):
            return PreviewFrame(frame.stream_generation, frame.seq, frame.captured_at,
                frame.width, frame.height, frame.payload, format='png')
        return frame

    def focus_snapshot(self):
        with self._state:
            backend = self._backend
            if backend is None:
                return dict(status='device_fault', code='camera_off')
            callback = getattr(backend, 'focus_snapshot', None)
            if callback is not None:
                return callback()
            return dict(status='focused' if backend.simulated else 'unsupported',
                        code='synthetic_focus' if backend.simulated else 'af_unsupported')

    def refocus(self, *, cancel_check=None):
        with self._operation:
            if cancel_check is not None:
                cancel_check()
            with self._state:
                self._check_owner(None)
                backend = self._backend
            if backend is None:
                raise FeatureError('camera_off')
            callback = getattr(backend, 'refocus', None)
            if callback is not None:
                return callback(cancel_check=cancel_check)
            return self.focus_snapshot()

    def capture_photo(self, *, cancel_check=None, owner=None, allow_unconfirmed=False):
        options = {'allow_unconfirmed': True} if allow_unconfirmed else {}
        frame = self._call('capture_photo', cancel_check, owner, **options)
        if frame is None:
            raise FeatureError('stale_photo')
        return frame

    def close(self, *, owner=None):
        # Do not wait on a capture operation to invalidate its future result.
        with self._state:
            if self._owner is not owner or self._owned_close_uncertain:
                return False
            self._epoch += 1
            backend, self._backend = self._backend, None
            self._seq = self._stamp = None
        if backend is not None:
            try:
                released = backend.close() is not False
            except Exception:
                released = False
            if not released:
                if owner is not None:
                    with self._state:
                        self._owned_close_uncertain = True
                return False
        return True
