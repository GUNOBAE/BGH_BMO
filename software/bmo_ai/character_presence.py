"""Quiet, optional local presence; no model, microphone or device ownership.

The owner supplies eligibility and an existing AudioFocus lease. Timers only
return proposals. Cached playback has its own voice and never writes a dialogue
history or last-answer/repeat cache.
"""
from collections import deque
import math
import random
from threading import Event, Lock
import time

from .conversation import TurnCancelled
from .voice_bank import VoiceBankError


class PresenceError(ValueError):
    def __init__(self, code="presence_configuration_invalid"):
        self.code = code
        super().__init__("The local presence configuration is invalid.")


def _now(clock):
    value = clock()
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise PresenceError()
    return value


def _delay(chooser, low, high):
    value = chooser(low, high)
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise PresenceError()
    return value


class PresenceScheduler:
    """Idle speech is opt-in, rare, and charged only after a heard completion."""
    MODES = ("normal", "quiet", "sleep", "off")
    WINDOW = 15 * 60

    def __init__(self, *, clock=time.monotonic, interval=None, mode="off"):
        self.clock, self.interval = clock, interval or random.SystemRandom().uniform
        self._heard, self._pending = deque(), False
        self._distress_until = 0.
        self._next_at = None
        self.set_mode(mode)

    def _schedule(self, now):
        self._next_at = now + _delay(self.interval, 180., 420.)

    def _prune(self, now):
        while self._heard and now - self._heard[0] >= self.WINDOW:
            self._heard.popleft()

    def set_mode(self, mode):
        if mode not in self.MODES:
            raise PresenceError()
        self.mode, self._pending = mode, False
        self._schedule(_now(self.clock))

    def note_distress(self):
        now = _now(self.clock)
        self._distress_until = max(self._distress_until, now + self.WINDOW)
        self.cancel()

    def cancel(self):
        """User input wins; never count cancellation or reuse a stale admission."""
        self._pending = False
        self._schedule(_now(self.clock))

    def due(self, *, emotion="neutral", phase="idle", activity_mode="conversation",
            conversation_busy=False, audio_busy=False, game_active=False,
            video_active=False, sensitive=False):
        values = (conversation_busy, audio_busy, game_active, video_active, sensitive)
        if any(type(value) is not bool for value in values):
            raise PresenceError()
        now = _now(self.clock)
        self._prune(now)
        if sensitive:
            self._distress_until = max(self._distress_until, now + self.WINDOW)
        blocked = (self.mode != "normal" or emotion in ("angry", "crying")
            or phase != "idle" or activity_mode != "conversation" or any(values)
            or now < self._distress_until)
        if blocked:
            self._schedule(now)
            return False
        if self._pending or now < self._next_at or len(self._heard) >= 2:
            return False
        self._pending = True
        return True

    def completed(self, heard):
        if type(heard) is not bool:
            raise PresenceError()
        now = _now(self.clock)
        self._prune(now)
        if self._pending and heard:
            self._heard.append(now)
        self._pending = False
        self._schedule(now)

    def snapshot(self):
        now = _now(self.clock)
        self._prune(now)
        return dict(mode=self.mode, enabled=self.mode == "normal", pending=self._pending,
                    heard_in_15_minutes=len(self._heard), limit_per_15_minutes=2,
                    next_in_seconds=max(0., self._next_at-now),
                    recent_distress=now < self._distress_until, automatic_generation=False)


class IdleGaze:
    """Small bounded proposals. Native/tracking/dialogue arbitration stays external."""
    TARGETS = ((-.08, -.025), (.08, -.025), (-.05, .035), (.05, .035), (0., 0.))

    def __init__(self, *, clock=time.monotonic, interval=None, chooser=None):
        self.clock = clock
        self.interval = interval or random.SystemRandom().uniform
        self.chooser = chooser or random.SystemRandom().choice
        self.reset()

    def reset(self):
        self._next_at = _now(self.clock) + _delay(self.interval, 8., 20.)

    def tick(self, *, eligible=True, higher_owner=False):
        if type(eligible) is not bool or type(higher_owner) is not bool:
            raise PresenceError()
        now = _now(self.clock)
        if not eligible or higher_owner:
            self.reset()
            return None
        if now < self._next_at:
            return None
        value = self.chooser(self.TARGETS)
        if value not in self.TARGETS:
            raise PresenceError()
        self.reset()
        return tuple(value)


class CachedPresence:
    """Greeting-compatible boot adapter plus a separate approved idle clip path.

    Callers hold AudioFocus and pass their current cancellation check. A corrupt
    bank clip can fall back only before playback actually starts. Once heard,
    an error never launches a second greeting. No runtime synthesis path exists.
    """
    def __init__(self, bank, bank_player, greeting_player):
        self.bank, self.bank_player, self.greeting_player = bank, bank_player, greeting_player
        self._playing, self._guard = Lock(), Lock()
        self._closed = Event()
        self._generation = 0

    def _enter(self, cancel_check):
        if not self._playing.acquire(blocking=False):
            raise PresenceError("presence_busy")
        with self._guard:
            generation = self._generation
        def check():
            with self._guard:
                cancelled = generation != self._generation
            if self._closed.is_set() or cancelled:
                raise TurnCancelled()
            if cancel_check is not None:
                cancel_check()
        try:
            check()
        except BaseException:
            self._playing.release()
            raise
        return check

    def play(self, *, emit, cancel_check=None, on_playback_start=None,
             allow_unapproved=False):
        check = self._enter(cancel_check)
        try:
            # Existing manual greeting audition remains an explicit separate
            # path; normal boot never opts out of listening approval.
            if allow_unapproved:
                return self.greeting_player.play(emit=emit, cancel_check=check,
                    on_playback_start=on_playback_start, allow_unapproved=True)
            check()
            try:
                clip = self.bank.select("boot", emotion="neutral", context="settled")
            except VoiceBankError:
                clip = None
            if clip is None:
                return self.greeting_player.play(emit=emit, cancel_check=check,
                    on_playback_start=on_playback_start)
            started = False
            def began():
                nonlocal started
                check(); started = True
                if on_playback_start is not None:
                    on_playback_start()
            # The existing owner implements a once-only local chime. The bank
            # must not duplicate it, or synthesize one when no cache is present.
            chime = getattr(self.greeting_player, "play_chime", None)
            if callable(chime):
                chime(cancel_check=check)
            check()
            try:
                return self.bank_player.play(clip, emit=emit, cancel_check=check,
                                             on_playback_start=began)
            except VoiceBankError:
                check()
                if started:
                    raise
                return self.greeting_player.play(emit=emit, cancel_check=check,
                    on_playback_start=on_playback_start)
        finally:
            self._playing.release()

    def play_idle(self, emotion="neutral", *, context="settled", emit,
                  cancel_check=None, on_playback_start=None):
        if emotion in ("angry", "crying") or context in ("distress", "quiet", "sleep", "off"):
            return None
        check = self._enter(cancel_check)
        try:
            check()
            try:
                clip = self.bank.select("idle", emotion=emotion, context=context)
                check()
                if clip is None:
                    return None
                return self.bank_player.play(clip, emit=emit, cancel_check=check,
                    on_playback_start=on_playback_start)
            except VoiceBankError:
                check()
                return None
        finally:
            self._playing.release()

    def cancel(self):
        with self._guard:
            self._generation += 1
        self.bank_player.cancel()
        self.greeting_player.cancel()

    def close(self):
        self._closed.set()
        self.cancel()
        bank_done = self.bank_player.close()
        greeting_done = self.greeting_player.close()
        return bank_done is True and greeting_done is True
