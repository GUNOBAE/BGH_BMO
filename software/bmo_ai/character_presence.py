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
    """Idle speech is opt-in, rare, and charged only after actual playback."""
    MODES = ("normal", "quiet", "sleep", "off")
    WINDOW = 15 * 60

    def __init__(self, *, clock=time.monotonic, interval=None, mode="off"):
        self.clock, self.interval = clock, interval or random.SystemRandom().uniform
        self._heard, self._pending = deque(), False
        self._charged = False
        self._distress_until = 0.
        self._next_at = None
        self._last_context = {}
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
        """User input wins; unheard cancellation never consumes the quota."""
        self._pending = False
        self._schedule(_now(self.clock))

    def due(self, *, emotion="neutral", phase="idle", activity_mode="conversation",
            conversation_busy=False, audio_busy=False, game_active=False,
            video_active=False, sensitive=False, muted=False, settings_active=False):
        values = (conversation_busy, audio_busy, game_active, video_active, sensitive, muted, settings_active)
        if any(type(value) is not bool for value in values):
            raise PresenceError()
        now = _now(self.clock)
        self._prune(now)
        if sensitive:
            self._distress_until = max(self._distress_until, now + self.WINDOW)
        self._last_context = dict(emotion=emotion, phase=phase, activity_mode=activity_mode,
            conversation_busy=conversation_busy, audio_busy=audio_busy, game_active=game_active,
            video_active=video_active, sensitive=sensitive, muted=muted, settings_active=settings_active)
        if self._blocked_reason(now, **self._last_context):
            self._schedule(now)
            return False
        if self._pending or now < self._next_at or len(self._heard) >= 2:
            return False
        self._pending = True
        self._charged = False
        return True

    def started(self):
        """Owner confirms a real player start, including interrupted speech."""
        now = _now(self.clock)
        self._prune(now)
        if not self._charged:
            self._heard.append(now)
            self._charged = True

    def completed(self, heard):
        if type(heard) is not bool:
            raise PresenceError()
        now = _now(self.clock)
        self._prune(now)
        if self._pending and heard and not self._charged:
            self._heard.append(now)
        self._charged = True
        self._pending = False
        self._schedule(now)

    def _blocked_reason(self, now, *, emotion='neutral', phase='idle', activity_mode='conversation',
                        conversation_busy=False, audio_busy=False, game_active=False,
                        video_active=False, sensitive=False, muted=False, settings_active=False):
        if self.mode != 'normal': return 'mode_' + self.mode
        if muted: return 'muted'
        if settings_active: return 'settings_active'
        if sensitive or now < self._distress_until: return 'recent_distress'
        if emotion in ('angry', 'crying'): return 'strong_emotion'
        if phase != 'idle' or conversation_busy: return 'conversation_active'
        if game_active: return 'game_active'
        if video_active: return 'recording_active'
        if activity_mode != 'conversation': return 'foreground_activity'
        if audio_busy: return 'audio_busy'
        return None

    def snapshot(self, **context):
        now = _now(self.clock)
        self._prune(now)
        blocked = self._blocked_reason(now, **(context or self._last_context))
        next_at = max(self._next_at, self._heard[0]+self.WINDOW if len(self._heard) >= 2 else 0.)
        reason = (blocked or ('pending' if self._pending else 'quota' if len(self._heard) >= 2
                            else 'interval' if now < next_at else 'ready'))
        return dict(mode=self.mode, enabled=self.mode == "normal", pending=self._pending,
                    heard_in_15_minutes=len(self._heard), limit_per_15_minutes=2,
                    next_in_seconds=max(0., next_at-now), suppression_reason=reason,
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
    def __init__(self, bank, bank_player, greeting_player, *, clock=None):
        self.bank, self.bank_player, self.greeting_player = bank, bank_player, greeting_player
        self.clock = clock or getattr(bank, 'clock', time.monotonic)
        self._idle_started = {}
        self._idle_status_cached = None
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
            now = _now(self.clock)
            with self._guard:
                self._idle_started = {clip: when for clip, when in self._idle_started.items() if now-when < 1800.}
                recent = tuple(self._idle_started)
            pack = getattr(getattr(self.bank_player, 'voice', None), 'event_pack', None)
            if pack is not None:
                selection_context = dict(emotion=emotion, context=context, recent=recent)
                absent = False
                try:
                    available = pack.has_event('idle', **selection_context)
                except VoiceBankError as error:
                    available = False
                    absent = error.code == 'voice_pack_asset_pending'
                if available:
                    selected = None
                    def observed(kind, **fields):
                        nonlocal selected
                        asset = fields.get('caption_asset')
                        if kind == 'prepared' and type(asset) is dict:
                            selected = asset.get('clip_id')
                        if kind == 'started' and type(selected) is str:
                            # A cancelled/partial clip was still heard; don't
                            # immediately recycle it at the next idle chance.
                            with self._guard:
                                self._idle_started[selected] = _now(self.clock)
                                self._idle_status_cached = None
                        emit(kind, **fields)
                    return self.bank_player.play_event('idle', emit=observed, cancel_check=check,
                        on_playback_start=on_playback_start, selection_context=selection_context)
                # A configured pack owns its idle authorization, including
                # event OFF/full release withdrawal. Never bypass it with a
                # separately approved legacy clip.
                if not absent:
                    return None
            try:
                clip = self.bank.select("idle", emotion=emotion, context=context, recent=recent)
                check()
                if clip is None or clip in recent:
                    return None
                def began():
                    check()
                    with self._guard:
                        self._idle_started[clip] = _now(self.clock)
                        self._idle_status_cached = None
                    if on_playback_start is not None:
                        on_playback_start()
                return self.bank_player.play(clip, emit=emit, cancel_check=check,
                    on_playback_start=began)
            except VoiceBankError:
                check()
                return None
        finally:
            self._playing.release()

    def idle_status(self, emotion='neutral', *, context='settled'):
        """Read-only eligibility, separate from selecting or attempting a clip.

        UI polling is bounded to one metadata read per second. Actual requests
        still revalidate the selected asset and authorization in play_idle.
        """
        now = _now(self.clock)
        with self._guard:
            recent = tuple(clip for clip, when in self._idle_started.items() if now-when < 1800.)
            pack = getattr(getattr(self.bank_player, 'voice', None), 'event_pack', None)
            key = (id(pack), emotion, context, recent)
            cached = self._idle_status_cached
            if cached is not None and cached[0] == key and 0 <= now-cached[1] < 1.:
                return dict(cached[2])
        query = getattr(pack, 'event_diagnostics', None)
        if callable(query):
            result = query('idle', emotion=emotion, context=context, recent=recent)
            result = dict(result, eligible_candidate_count=result.get('eligible_count', 0))
        else:
            # Never call legacy select() to count: it may advance its chooser.
            result = dict(eligible_candidate_count=None, blocked_reason='legacy_count_unreported',
                          release_scope='legacy', automatic_generation=False)
        with self._guard:
            self._idle_status_cached = (key, now, result)
        return dict(result)

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
