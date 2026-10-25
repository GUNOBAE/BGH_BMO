"""One persistent conversation and a bounded, relative-time face snapshot.

The existing ConversationWorker owns turn admission, queue pressure, cooperative
cancellation and late results. This adapter never creates hardware at import.
"""
from collections import deque
import json
from threading import Event, Lock, Thread
from types import SimpleNamespace
import time
from uuid import uuid4

from .app import ConversationApp
from .audio import AudioPlaybackError
from .conversation import ConversationWorker, TurnCancelled
from .cloud import CloudError
from .expression_contract import ExpressionClient
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
        text = "We can have a little adventure together."
        if request.get("text", {}).get("format", {}).get("type") == "json_schema":
            text = json.dumps({"speech": text, "emotion": "happy"})
        return SimpleNamespace(status="completed", incomplete_details=None,
                               error=None, output_text=text)

    def close(self):
        pass


class IntegrationApp(ConversationApp):
    """Keep existing fixed unavailability notices on the same owned output."""
    stage_reply = None
    input_provider = None
    trace_stage = None

    def _trace(self, stage, code="ok", outcome="ok"):
        if self.trace_stage is not None:
            self.trace_stage(stage, code, outcome=outcome)
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
            if self.input_provider is not None:
                self._record_end_monotonic = None
                self._state(FaceState.LISTENING)
                self._check_cancelled()
                message = self.input_provider(self._check_cancelled)
                self._check_cancelled()
                return message
            return super().voice_input()
        except CloudError as error:
            self._notice(error)
            raise

    def answer(self, message):
        previous = [dict(item) for item in self.brain.history]
        self._trace("ai", "started", "started")
        try:
            answer = super().answer(message)
            if self.stage_reply is not None:
                self.stage_reply(answer)
            self._check_cancelled()
            self._trace("ai")
            return answer
        except TurnCancelled:
            # A synchronous provider may return after cancel. Its answer must
            # enter neither playback nor the next turn's retained context.
            self.brain.history = previous
            raise
        except CloudError as error:
            self._trace("ai", error.code, "error")
            self._notice(error)
            raise
        except Exception as error:
            self._trace("ai", getattr(error, "code", "operation_failed"), "error")
            raise

    def playback(self, answer):
        self._trace("fish", "started", "started")
        try:
            return super().playback(answer)
        except TurnCancelled:
            self._trace("playback", "cancelled", "cancelled")
            raise
        except Exception as error:
            stage = "playback" if isinstance(error, AudioPlaybackError) else "fish"
            self._trace(stage, getattr(error, "code", "operation_failed"), "error")
            raise


class RepeatApp(ConversationApp):
    """Worker-compatible cached playback: no pretend listening or brain call."""
    def voice_input(self):
        self._check_cancelled()
        self._state(FaceState.THINKING)
        return "cached-audio"  # Internal worker stage token, never a transcript.

    def answer(self, message):
        self._check_cancelled()
        return message

    def playback(self, answer):
        try:
            self._check_cancelled()

            def started():
                self._check_cancelled()
                self._state(FaceState.SPEAKING)
                self._check_cancelled()

            if not self.voice.repeat(on_playback_start=started):
                raise AudioPlaybackError("No cached session audio is available.", "no_cached_audio")
        finally:
            self.idle()


class IntegrationSession:
    def __init__(self, config, client, *, simulate=False, voice=None, brain=None,
                 clock=time.monotonic, timeout=90.0, disconnect_timeout=3.0,
                 emotion_mode="manual", fish_api_key=None, reply_client=None,
                 on_reply=None, on_playback_event=None, diagnostics=None,
                 expressive_voice=False):
        if emotion_mode not in ("auto", "manual"):
            raise ValueError("unknown emotion mode")
        self.config, self.client, self.simulate = config, client, simulate
        self.expression_client = ExpressionClient(client) if reply_client is None else reply_client
        self._on_reply, self._on_playback_event = on_reply, on_playback_event
        self.diagnostics = diagnostics
        self.brain = brain if brain is not None else policy_brain(
            client, config, expression_client=self.expression_client if emotion_mode == "auto" else None)
        self.clock, self.disconnect_timeout = clock, disconnect_timeout
        self.session_id = uuid4().hex
        self._lock = Lock()
        self._closed = self._active = self._pending_reset = False
        self._cleanup_done, self._cleanup_failed = Event(), False
        self._repeat = False
        self._turn_input = None
        self._emotion_generation = 0
        self._turn_mode = emotion_mode
        self._turn_emotion_generation = 0
        self._staged_expression = self._notice_restore = None
        self._started = self._browser_seen = None
        self._turn = 0
        self._data = dict(session_id=self.session_id, turn_id=None,
                          utterance_id=None, sequence=0, phase="idle",
                          emotion="neutral", emotion_mode=emotion_mode,
                          emotion_validation="none", playback="idle", position=0.0,
                          cues=[], source=None)
        self.events = deque(maxlen=128)
        voice_credentials = {} if fish_api_key is None else {"fish_api_key": fish_api_key}
        self.voice = voice if voice is not None else IntegrationVoice(
            config, client, simulate=simulate, emit=lambda *a, **kw: None,
            cancel_check=lambda: None, **voice_credentials,
            **({"expressive": True} if expressive_voice else {}))
        self.worker = ConversationWorker(self._factory, timeout=timeout,
                                         queue_size=32, clock=clock, synthetic=simulate)

    def _record(self, kind):
        self._data["sequence"] += 1
        self.events.append({key: self._data[key] for key in
                            ("session_id", "turn_id", "utterance_id", "sequence",
                             "phase", "emotion", "emotion_mode", "emotion_validation",
                             "playback", "source")} | {"kind": kind})

    def _invalidate_expression(self, *, neutral=False):
        """Invalidate candidates without clearing the last displayed emotion.

        Continuity v2 keeps emotion across activities and ordinary terminal
        states. Only an explicit reset requests neutral here.
        """
        self._emotion_generation += 1
        self._staged_expression = self._notice_restore = None
        if neutral:
            self._data.update(emotion="neutral", emotion_validation="none")

    def _restore_notice_expression(self):
        """A fixed notice is neutral only while playing, including repeats.

        Completion, error or cancellation restores the prior dialogue display.
        A reset/manual override/mode change invalidates this scoped restoration,
        so its late terminal event cannot overwrite an explicit newer choice.
        """
        restore, self._notice_restore = self._notice_restore, None
        if (restore is not None and self._current_expression_turn()
                and restore[0] == self._emotion_generation and restore[1] == self._turn):
            self._data.update(emotion=restore[2], emotion_validation=restore[3])

    def _current_expression_turn(self):
        return (self._data["emotion_mode"] == "auto" and self._turn_mode == "auto"
                and self._turn_emotion_generation == self._emotion_generation)

    def _idle(self, playback):
        if playback in ("ended", "cancelled", "error"):
            self._restore_notice_expression()
        if playback in ("cancelled", "error"):
            self._invalidate_expression()
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
        if emotion not in EMOTIONS and emotion not in ("auto", "manual"):
            raise ValueError("unknown emotion")
        with self._lock:
            self._data["emotion_mode"] = "auto" if emotion == "auto" else "manual"
            self._invalidate_expression()
            if emotion in EMOTIONS:
                self._data["emotion"] = emotion
            if self._data["emotion_mode"] == "manual":
                self._data["emotion_validation"] = "manual"
            self._record("emotion")
        # A busy turn keeps its original brain-only view. Generation checks
        # discard its provisional metadata; the next turn chooses a new view.
        self.expression_client.reset()

    def _factory(self, queue_face, cancellation):
        turn = self.worker.turn
        mode, generation = self._turn_mode, self._turn_emotion_generation

        def check():
            from .conversation import TurnCancelled
            if cancellation.is_set() or self._closed:
                raise TurnCancelled()

        session = self

        def stage_reply(answer):
            check()
            if mode != "auto":
                return
            with self._lock:
                if (not self._active or turn != self._turn or cancellation.is_set()
                        or generation != self._emotion_generation
                        or self._data["emotion_mode"] != "auto"):
                    return
                reply = self.expression_client.last_reply
                if reply is None or reply.speech != answer:
                    # No raw envelope or unmatched provisional reply reaches TTS.
                    raise CloudError("expression_format", "The reply did not match the expression contract.")
                value = dict(emotion=reply.emotion,
                             emotion_validation="degraded" if reply.degraded else "validated")
                if hasattr(reply, "reaction"):
                    value["reaction"] = reply.reaction
                self._staged_expression = dict(turn=turn, generation=generation, **value)
                self.voice.expression_metadata = value
                if self._on_reply is not None:
                    # Feature host callbacks only enqueue bounded metadata. They
                    # never perform resource transitions on the provider thread.
                    self._on_reply(reply, self._data["turn_id"], self._data["utterance_id"])
            check()

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
            cues = None
            if kind == "prepared":
                cues = fields.get("cues")
                if cues is None:  # Compatibility for an injected voice adapter.
                    cues = wav_cues(fields["path"])
            check()
            if self.diagnostics is not None:
                if kind == "prepared":
                    self.diagnostics.record("fish", "prepared", turn=turn)
                elif kind in ("started", "ended", "error", "cancelled"):
                    self.diagnostics.record("playback", "playback_started" if kind == "started" else kind,
                        outcome="error" if kind == "error" else "cancelled" if kind == "cancelled" else "ok",
                        turn=turn)
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
                    if self._current_expression_turn():
                        notice = fields.get("notice") is True
                        self._notice_restore = (
                            self._emotion_generation, turn, self._data["emotion"],
                            self._data["emotion_validation"]) if notice else None
                        emotion = "neutral" if notice else fields.get("emotion", "neutral")
                        validation = "notice" if notice else fields.get("emotion_validation", "none")
                        if emotion not in EMOTIONS:
                            emotion, validation = "neutral", "none"
                        if validation not in ("none", "manual", "validated", "degraded", "notice"):
                            emotion, validation = "neutral", "none"
                        self._data.update(emotion=emotion, emotion_validation=validation)
                elif kind in ("ended", "cancelled", "error"):
                    if kind == "ended":
                        self._staged_expression = None
                    self._idle(kind)
                self._record(kind)
                if self._on_playback_event is not None:
                    self._on_playback_event(kind, self._data["turn_id"], self.clock())

        self.voice.cancel_check, self.voice.emit = check, emit
        with self._lock:
            self.voice.expression_metadata = dict(
                emotion=self._data["emotion"] if mode == "manual" else "neutral",
                emotion_validation="manual" if mode == "manual" else "none")
        app = RepeatApp if self._repeat else IntegrationApp
        instance = app(self.brain, self.client, self.config,
                       face=SessionFace(), voice=self.voice, cancel_check=check)
        if not self._repeat:
            instance.stage_reply = stage_reply
            instance.input_provider = self._turn_input
            if self.diagnostics is not None:
                instance.trace_stage = lambda stage, code="ok", **kw: self.diagnostics.record(
                    stage, code, turn=turn, **kw)
        return instance

    def start(self, message="A synthetic question"):
        return self._start_turn(message=message)

    def start_with_input(self, provider):
        """One request-local input provider in the SAME worker, brain and voice.

        Provider receives the current cancellation check. It may return an
        empty transcript; it must never call start() or create another engine.
        Ordinary start/repeat clear this provider, preventing stale reuse.
        """
        if not callable(provider):
            return False
        return self._start_turn(input_provider=provider)

    def repeat(self):
        """Admit one cached playback turn; empty/closed/busy returns False."""
        if not getattr(self.voice, "has_cache", False):
            return False
        return self._start_turn(repeat=True)

    def _start_turn(self, *, message=None, repeat=False, input_provider=None):
        if self._closed or self.worker.busy:
            return False
        self._repeat = repeat
        self._turn_input = input_provider
        with self._lock:
            self._invalidate_expression()
            self._turn_mode = self._data["emotion_mode"]
            self._turn_emotion_generation = self._emotion_generation
            self._turn = self.worker.turn + 1
            if self.diagnostics is not None:
                self.diagnostics.begin(self._turn)
            self._active = True
            self._started = None
            self._data.update(turn_id=f"{self.session_id}:{self._turn}",
                              utterance_id=f"{self.session_id}:{self._turn}:1",
                              phase="idle", playback="idle", position=0.0, cues=[])
            self._record("turn_started")
        self.expression_client.reset()
        self.brain.client = self.expression_client if self._turn_mode == "auto" else self.client
        if not repeat:
            self.voice.message = message
        return self.worker.start()

    def cancel(self):
        if hasattr(self.voice, "invalidate_pending"):
            result = self.voice.invalidate_pending(self.worker.cancel)
        else:
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
                if self.diagnostics is not None:
                    self.diagnostics.finish(item.code)
                self._turn_input = None
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
        self.expression_client.reset()
        if hasattr(self.voice, "clear_cache"):
            self.voice.clear_cache()
        self._pending_reset = False
        with self._lock:
            self._invalidate_expression(neutral=True)
            self._idle("idle")
            self._record("reset")

    def reset(self):
        with self._lock:
            self._invalidate_expression(neutral=True)
            self._record("expression_reset")
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
        if hasattr(self.voice, "clear_cache"):
            self.voice.clear_cache()
        self.worker.close()
        thread = self.worker._thread
        if thread is not None:
            thread.join(timeout=1.0)

        def cleanup():
            try:
                if thread is not None and thread.is_alive():
                    thread.join()  # daemon cleanup only; never block the owner UI.
                if self.voice.close() is False:
                    self._cleanup_failed = True
                close = getattr(self.client, "close", None)
                if close is not None and close() is False:
                    self._cleanup_failed = True
            except BaseException:
                self._cleanup_failed = True
                raise
            finally:
                self._cleanup_done.set()

        if thread is not None and thread.is_alive():
            Thread(target=cleanup, daemon=True, name="bmo-owned-cleanup").start()
        else:
            cleanup()

    def cleanup_snapshot(self):
        stopped = not self.worker.worker_alive
        complete = self._closed and stopped and self._cleanup_done.is_set() and not self._cleanup_failed
        return dict(resources_cleaned=complete, worker_stopped=stopped,
                    voice_client_closed=self._cleanup_done.is_set() and not self._cleanup_failed,
                    code="resources_cleaned" if complete else "session_cleanup_pending")
