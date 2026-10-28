"""Fixed greeting playback; caller owns face-ready admission, worker and focus.

This adapter has no brain/history, microphone or synthesis route. Its separate
IntegrationVoice instance reuses the existing sole Python/ALSA player boundary,
without touching the dialogue voice's last-answer cache or reducing gain twice.
"""
from threading import Event

from .conversation import TurnCancelled
from .greeting_cache import GreetingCache, GreetingError
from .integration_voice import IntegrationVoice


class GreetingPlayer:
    def __init__(self, config, cache, *, simulate=False, player=None, startup_chime=False,
                 chime_home=None):
        if (not isinstance(cache, GreetingCache) or type(simulate) is not bool
                or type(startup_chime) is not bool):
            raise GreetingError('greeting_corrupt')
        self.cache, self.simulate = cache, simulate
        self.startup_chime = startup_chime
        self._chime_home = chime_home
        self._chime_attempted = False
        self._cancelled, self._closed = Event(), Event()
        self.voice = IntegrationVoice(config, None, emit=lambda *a, **k: None,
                                      simulate=simulate, player=player)

    def _play_chime(self, check):
        if self.startup_chime and not self._chime_attempted:
            self._chime_attempted = True
            from .startup_sound import startup_chime_asset
            with startup_chime_asset(home=self._chime_home) as (path, info):
                self.voice._play(path, 'local_startup_chime', info, (), check,
                    lambda *_a, **_kw: None, None,
                    dict(emotion='neutral', emotion_validation='none', notice=False))
            check()

    def play_chime(self, *, cancel_check=None):
        """Use the same once-only owned player before an approved bank greeting."""
        if self._closed.is_set():
            raise GreetingError('greeting_closed')
        if not self.voice._speaking.acquire(blocking=False):
            raise GreetingError('greeting_player_busy')
        self.voice._idle.clear()
        self._cancelled.clear()
        def check():
            if self._closed.is_set() or self._cancelled.is_set():
                raise TurnCancelled()
            self.voice._check(cancel_check)
        try:
            check()
            self._play_chime(check)
        finally:
            self.voice._speaking.release()
            self.voice._idle.set()

    def play(self, *, emit, cancel_check=None, on_playback_start=None, allow_unapproved=False):
        """Synchronous playback; root must hold its one AudioFocus lease.

        Only an explicit human audition may set allow_unapproved=True. Ordinary
        startup requires approval. Successful return means playback completed;
        errors/cancel use existing event shapes and never start another player.
        """
        if self._closed.is_set():
            raise GreetingError('greeting_closed')
        if type(allow_unapproved) is not bool:
            raise GreetingError('greeting_corrupt')
        if not self.voice._speaking.acquire(blocking=False):
            raise GreetingError('greeting_player_busy')
        self.voice._idle.clear()
        self._cancelled.clear()
        source = 'cached_greeting'
        def check():
            if self._closed.is_set() or self._cancelled.is_set():
                raise TurnCancelled()
            self.voice._check(cancel_check)
        try:
            check()
            self._play_chime(check)
            with self.cache.load(simulate=self.simulate, require_approved=not allow_unapproved,
                                 cancel_check=check) as asset:
                expression = dict(emotion='happy', emotion_validation='validated', notice=False)
                self.voice._play(asset.path, source, asset.info, asset.cues, check, emit,
                                 on_playback_start, expression)
                check()
                return asset.public() | {'completed': True}
        except (TurnCancelled, KeyboardInterrupt):
            emit('cancelled', source=source)
            raise
        except BaseException:
            emit('error', source=source)
            raise
        finally:
            self.voice._speaking.release()
            self.voice._idle.set()

    def cancel(self):
        self._cancelled.set()

    def close(self):
        self._closed.set()
        self.cancel()
        return self.voice.close()
