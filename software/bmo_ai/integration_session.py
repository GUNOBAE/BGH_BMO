"""One persistent conversation and a bounded, relative-time face snapshot.

The existing ConversationWorker owns turn admission, queue pressure, cooperative
cancellation and late results. This adapter never creates hardware at import.
"""
from collections import deque
from threading import Lock, Thread
from types import SimpleNamespace
import time
from uuid import uuid4

from .app import ConversationApp
from .conversation import ConversationWorker, TurnCancelled
from .cloud import CloudError
from .hardware import FaceState
from .integration_face import wav_cues
from .integration_profile import policy_brain
from .integration_voice import IntegrationVoice

EMOTIONS = frozenset({"neutral", "happy", "angry", "sad", "crying", "surprised"})


class SimulatedClient:
    """Synthetic responses, including real BMOBrain history handling, no SDK."""
    def __init__(self):
        self.requests = []
        self.responses = self

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(status="completed", incomplete_details=None,
                               error=None, output_text="We can have a little adventure together.")

    def close(self):
        pass


class IntegrationApp(ConversationApp):
    """Keep existing fixed unavailability notices on the same owned output."""
    def _notice(self, error):
        if self.config.offline_voice and error.code in ("connection", "timeout", "server", "rate_limit"):
            try:
                self.voice.offline_notice(on_playback_start=lambda: self._state(FaceState.SPEAKING))
            except TurnCancelled:
                raise
            except Exception:
                pass  # Preserve the primary public cloud error, never raw text.

    def voice_input(self):
        try:
            return super().voice_input()
        except CloudError as error:
            self._notice(error)
            raise

    def answer(self, message):
        previous = [dict(item) for item in self.brain.history]
        try:
            return super().answer(message)
        except TurnCancelled:
            # A synchronous provider may return after cancel. Its answer must
            # enter neither playback nor the next turn's retained context.
            self.brain.history = previous
            raise
        except CloudError as error:
            self._notice(error)
            raise


class IntegrationSession:
    def __init__(self, config, client, *, simulate=False, voice=None, brain=None,
                 clock=time.monotonic, timeout=90.0, disconnect_timeout=3.0):
        self.config, self.client, self.simulate = config, client, simulate
        self.brain = brain if brain is not None else policy_brain(client, config)
        self.clock, self.disconnect_timeout = clock, disconnect_timeout
        self.session_id = uuid4().hex
        self._lock = Lock()
        self._closed = self._active = self._pending_reset = False
        self._started = self._browser_seen = None
        self._turn = 0
        self._data = dict(session_id=self.session_id, turn_id=None,
                          utterance_id=None, sequence=0, phase="idle",
                          emotion="neutral", playback="idle", position=0.0,
                          cues=[], source=None)
        self.events = deque(maxlen=128)
        self.voice = voice if voice is not None else IntegrationVoice(
            config, client, simulate=simulate, emit=lambda *a, **kw: None,
            cancel_check=lambda: None)
        self.worker = ConversationWorker(self._factory, timeout=timeout,
                                         queue_size=32, clock=clock, synthetic=simulate)

    def _record(self, kind):
        self._data["sequence"] += 1
        self.events.append({key: self._data[key] for key in
                            ("session_id", "turn_id", "utterance_id", "sequence",
                             "phase", "emotion", "playback", "source")} | {"kind": kind})

    def _idle(self, playback):
        self._data.update(phase="idle", playback=playback, cues=[], position=0.0,
                          utterance_id=None)
        self._started = None

    @property
    def busy(self):
        return self.worker.busy

    def snapshot(self):
        with self._lock:
            value = dict(self._data)
            value["cues"] = [dict(cue) for cue in self._data["cues"]]
            if self._started is not None and self._data["playback"] == "started":
                value["position"] = max(0.0, self.clock() - self._started)
            return value

    def browser_snapshot(self):
        # Called only by the allowlisted HTTP state endpoint, never an audio API.
        with self._lock:
            self._browser_seen = self.clock()
        return self.snapshot()

    def set_emotion(self, emotion):
        if emotion not in EMOTIONS:
            raise ValueError("unknown emotion")
        with self._lock:
            self._data["emotion"] = emotion
            self._record("emotion")

    def _factory(self, queue_face, cancellation):
        turn = self.worker.turn

        def check():
            from .conversation import TurnCancelled
            if cancellation.is_set() or self._closed:
                raise TurnCancelled()

        session = self

        class SessionFace:
            def set_state(self, state):
                queue_face.set_state(state)
                with session._lock:
                    if not session._active or turn != session._turn or cancellation.is_set():
                        return
                    # SPEAKING is committed by the owned player-start boundary,
                    # not by synthesis completion or an older queued UI event.
                    if state in (FaceState.LISTENING, FaceState.THINKING):
                        session._data["phase"] = state.value
                        session._record("phase")
                    elif state in (FaceState.IDLE, FaceState.ERROR):
                        if state == FaceState.ERROR:
                            session._idle("error")
                        elif session._data["playback"] == "started":
                            session._idle("ended")
                        else:
                            session._data["phase"] = "idle"
                        session._record("phase")

        def emit(kind, **fields):
            check()
            cues = wav_cues(fields["path"]) if kind == "prepared" else None
            check()
            with self._lock:
                if not self._active or turn != self._turn:
                    return
                if kind == "prepared":
                    self._data.update(playback="prepared", phase="thinking", cues=cues,
                                      source=fields.get("source"), position=0.0)
                elif kind == "started":
                    self._started = self.clock()
                    self._data.update(playback="started", phase="speaking",
                                      source=fields.get("source", self._data["source"]))
                elif kind in ("ended", "cancelled", "error"):
                    self._idle(kind)
                self._record(kind)

        self.voice.cancel_check, self.voice.emit = check, emit
        return IntegrationApp(self.brain, self.client, self.config,
                               face=SessionFace(), voice=self.voice, cancel_check=check)

    def start(self, message="A synthetic question"):
        if self._closed or self.worker.busy:
            return False
        with self._lock:
            self._turn = self.worker.turn + 1
            self._active = True
            self._started = None
            self._data.update(turn_id=f"{self.session_id}:{self._turn}",
                              utterance_id=f"{self.session_id}:{self._turn}:1",
                              phase="idle", playback="idle", position=0.0, cues=[])
            self._record("turn_started")
        self.voice.message = message
        return self.worker.start()

    def cancel(self):
        result = self.worker.cancel()
        with self._lock:
            self._active = False
            self._idle("cancelled")
            self._record("cancelled")
        return result

    def poll(self):
        items = self.worker.poll()
        if self._browser_seen is not None and self.busy and self.clock() - self._browser_seen > self.disconnect_timeout:
            self.cancel()
        for item in items:
            if item.kind == "cancel_requested":
                with self._lock:
                    self._active = False
                    self._idle("cancelled")
                    self._record(item.code or "cancelled")
            if item.kind == "finished":
                with self._lock:
                    self._active = False
                    status = self._data["playback"]
                    if item.code not in ("ok", "empty_stt", "cancelled"):
                        status = "error"
                    elif item.code == "cancelled":
                        status = "cancelled"
                    self._idle(status)
                    self._record("finished")
                if self._pending_reset:
                    self._reset_now()
        return items

    def _reset_now(self):
        self.brain.reset()
        if hasattr(self.voice, "clear_cache"):
            self.voice.clear_cache()
        self._pending_reset = False
        with self._lock:
            self._idle("idle")
            self._record("reset")

    def reset(self):
        if self.busy:
            self._pending_reset = True
            self.cancel()
        else:
            self._reset_now()

    def close(self):
        if self._closed:
            return
        self.cancel()
        self._closed = True
        self.worker.close()
        thread = self.worker._thread
        if thread is not None:
            thread.join(timeout=1.0)

        def cleanup():
            if thread is not None and thread.is_alive():
                thread.join()  # daemon cleanup only; never block the owner UI.
            self.voice.close()
            close = getattr(self.client, "close", None)
            if close is not None:
                close()

        if thread is not None and thread.is_alive():
            Thread(target=cleanup, daemon=True, name="bmo-owned-cleanup").start()
        else:
            cleanup()
