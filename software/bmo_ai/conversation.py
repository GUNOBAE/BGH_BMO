"""Bounded worker bridge for ConversationApp; no Pygame, client or device creation.

The owner thread calls start/poll/cancel/close. Only structured, bounded metadata
crosses from the worker. A stuck synchronous dependency cannot be killed safely:
cancel invalidates its output, keeps the slot busy, and close never waits forever.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from queue import Empty, Full, Queue
from threading import Event, Thread, get_ident
import time

from .audio import AudioError
from .cloud import CloudError
from .hardware import FaceState


class TurnCancelled(Exception):
    """Cooperative adapters may raise this after observing their cancel event."""


@dataclass(frozen=True)
class TurnEvent:
    turn: int
    kind: str
    state: FaceState | None = None
    code: str | None = None
    timings: tuple[tuple[str, float], ...] = ()
    synthetic: bool = False


_PUBLIC_CODES = frozenset({
    "authentication", "permission", "model", "rate_limit", "timeout", "connection",
    "server", "request", "cloud", "invalid_wav", "audio_timeout", "device_unavailable",
    "audio_cleanup_failed", "invalid_transcription_response", "audio_download_timeout",
    "speech_download_too_large", "invalid_speech_download", "tool_unavailable",
})


class ConversationWorker:
    """One turn and at most one daemon worker; duplicate inputs are rejected.

    factory(queue_face, cancellation_event) must return a ConversationApp. It
    executes on the worker. Adapters should bound their own blocking operations.
    The bridge timeout requests cancellation; it cannot interrupt arbitrary IO.
    No replacement is started until the previous thread has actually exited and
    its completion has been polled. Public methods belong to the creating thread.
    """

    def __init__(self, factory, *, timeout=30.0, queue_size=32,
                 clock=time.monotonic, synthetic=False):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(queue_size) is not int or not 1 <= queue_size <= 256:
            raise ValueError("queue_size must be from 1 to 256")
        self._factory = factory
        self._clock = clock
        self._timeout = timeout
        self._events = Queue(maxsize=queue_size)
        self._owner = get_ident()
        self._thread = None
        self._cancel = Event()
        self._closed = False
        self._pending = False
        self._result = None
        self._notice = None
        self._reason = None
        self._ended = None
        self.turn = 0
        self.synthetic = bool(synthetic)

    def _own(self):
        if get_ident() != self._owner:
            raise RuntimeError("ConversationWorker must be controlled by its owner thread")

    @property
    def worker_alive(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def busy(self):
        return self._pending or self.worker_alive

    @property
    def queued_events(self):
        return self._events.qsize()

    def start(self):
        self._own()
        if self._closed or self.busy:
            return False
        self._drain()
        self.turn += 1
        self._cancel = Event()
        self._result = self._notice = self._reason = None
        self._ended = None
        self._started = self._clock()
        self._pending = True
        self._thread = Thread(target=self._run, args=(self.turn, self._cancel),
                              name="bmo-conversation", daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._pending = False
            self._thread = None
            raise
        return True

    def cancel(self, *, reason="cancelled"):
        self._own()
        if reason not in ("cancelled", "timeout"):
            raise ValueError("unknown cancellation reason")
        if not self._pending or self._cancel.is_set():
            return False
        self._reason = reason
        self._cancel.set()
        self._notice = TurnEvent(self.turn, "cancel_requested", code=reason,
                                 state=FaceState.ERROR if reason == "timeout" else FaceState.IDLE,
                                 synthetic=self.synthetic)
        return True

    def _drain(self):
        items = []
        for _ in range(self._events.maxsize):
            try:
                items.append(self._events.get_nowait())
            except Empty:
                break
        return items

    def poll(self):
        """Bounded, nonblocking drain. Completion means the thread really exited."""
        self._own()
        if self._closed:
            self._drain()
            return ()
        end = self._ended if self._ended is not None else self._clock()
        if self._pending and end - self._started >= self._timeout:
            self.cancel(reason="timeout")
        items = [item for item in self._drain()
                 if item.turn == self.turn and not self._cancel.is_set()]
        if self._notice is not None:
            items.append(self._notice)
            self._notice = None
        if self._pending and not self.worker_alive and self._result is not None and self._events.empty():
            result = self._result
            if self._reason:
                result = TurnEvent(self.turn, "finished", code=self._reason,
                                   state=FaceState.IDLE, timings=result.timings,
                                   synthetic=self.synthetic)
            items.append(result)
            self._pending = False
            self._thread = None
            self._result = None
        return tuple(items)

    def close(self):
        """Request cancel and detach; return whether worker actually stopped.

        No join is performed on the UI thread. Repeated close may observe later
        completion. Daemon status prevents a stuck dependency blocking process exit.
        """
        self._own()
        self.cancel()
        self._closed = True
        self._drain()
        return not self.worker_alive

    def _run(self, turn, cancellation):
        timings = []
        speech_start = playback_start = None

        def check():
            if cancellation.is_set():
                raise TurnCancelled()

        def emit(state):
            nonlocal playback_start
            if state == FaceState.SPEAKING and playback_start is None:
                playback_start = self._clock()
            item = TurnEvent(turn, "state", state=state, synthetic=self.synthetic)
            # Backpressure retains transition ordering. Cancellation/close always
            # releases a full queue without requiring the UI to drain it.
            while not cancellation.is_set():
                try:
                    self._events.put(item, timeout=0.02)
                    return
                except Full:
                    pass

        class QueueFace:
            def set_state(self, state):
                if not isinstance(state, FaceState):
                    raise TypeError("state must be a FaceState")
                emit(state)

        def stage(name, operation):
            check()
            start = self._clock()
            try:
                return operation()
            finally:
                timings.append((name, max(0.0, self._clock() - start)))

        code = "ok"
        try:
            app = stage("prepare", lambda: self._factory(QueueFace(), cancellation))
            # State publication can block under backpressure. Check again inside
            # ConversationApp after each state and before starting its next IO.
            app.cancel_check = check
            message = stage("input", app.voice_input)
            check()
            if not isinstance(message, str) or not message.strip():
                code = "empty_stt"
            else:
                answer = stage("llm", lambda: app.answer(message))
                check()
                speech_start = self._clock()
                try:
                    app.playback(answer)
                finally:
                    end = self._clock()
                    boundary = end if playback_start is None else playback_start
                    timings.append(("tts", max(0.0, boundary - speech_start)))
                    if playback_start is not None:
                        timings.append(("playback", max(0.0, end - playback_start)))
                check()
        except TurnCancelled:
            code = "cancelled"
        except (AudioError, CloudError) as error:
            code = error.code if error.code in _PUBLIC_CODES else "operation_failed"
        except TimeoutError:
            code = "timeout"
        except BaseException:
            # Never forward raw exceptions, messages, prompts or credentials.
            code = "operation_failed"
        finally:
            if cancellation.is_set():
                code = "cancelled"
            self._result = TurnEvent(turn, "finished", code=code,
                                     state=FaceState.IDLE if code in ("ok", "empty_stt", "cancelled") else FaceState.ERROR,
                                     timings=tuple(timings), synthetic=self.synthetic)
            self._ended = self._clock()
