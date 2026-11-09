"""Fixed greeting playback; caller owns face-ready admission, worker and focus.

This adapter has no brain/history, microphone or synthesis route. Its separate
IntegrationVoice instance reuses the existing sole Python/ALSA player boundary,
without touching the dialogue voice's last-answer cache or reducing gain twice.
"""
from threading import Event

from .conversation import TurnCancelled
from .greeting_cache import GreetingCache, GreetingError
from .integration_voice import IntegrationVoice


# Captions only: the existing approved startup recordings and their selection
# remain unchanged. A transcript change cannot inherit an unrelated Korean line.
_BOOT_CAPTIONS = {
    'cached_greeting': ("Hey! You're back! BMO missed you!", '안녕! 돌아왔구나! 비모가 보고 싶었어!'),
    'boot_01': ("Hey, you're back! How have you been?", '안녕, 돌아왔구나! 잘 지냈어?'),
    'boot_02': ("Oh! Hello, friend. What's our adventure today?", '오! 안녕, 친구. 오늘은 어떤 모험을 할까?'),
    'boot_03': ('There you are. Ready for a little adventure?', '여기 있었구나. 작은 모험을 떠날 준비 됐어?'),
    'boot_04': ('Hello again! What shall we do first?', '또 만나서 반가워! 뭐부터 할까?'),
    'boot_05': ('BMO is ready. How are you doing?', '비모는 준비됐어. 넌 어때?'),
    'boot_06': ('Well, hello! Got any good ideas today?', '안녕! 오늘 좋은 생각 있어?'),
    'boot_07': ("Welcome back. Let's make something nice.", '어서 와. 멋진 걸 만들어 보자.'),
    'boot_08': ('Hi, friend. We can take it easy today.', '안녕, 친구. 오늘은 느긋하게 보내도 돼.'),
}


def boot_captions(clip_id, text):
    from .captions import clean_caption
    row = _BOOT_CAPTIONS.get(clip_id)
    return dict(en=clean_caption(text), ko=row[1] if row is not None and text == row[0] else '')


class GreetingPlayer:
    def __init__(self, config, cache, *, simulate=False, player=None, startup_chime=False,
                 chime_home=None, allow_synthesized_chime=True):
        if (not isinstance(cache, GreetingCache) or type(simulate) is not bool
                or type(startup_chime) is not bool or type(allow_synthesized_chime) is not bool):
            raise GreetingError('greeting_corrupt')
        self.cache, self.simulate = cache, simulate
        self.startup_chime = startup_chime
        self.allow_synthesized_chime = allow_synthesized_chime
        self.startup_chime_status = 'NOT_ATTEMPTED' if startup_chime else 'DISABLED'
        self._chime_home = chime_home
        self._chime_attempted = False
        self._cancelled, self._closed = Event(), Event()
        self.voice = IntegrationVoice(config, None, emit=lambda *a, **k: None,
                                      simulate=simulate, player=player)

    def _play_chime(self, check):
        if self.startup_chime and not self._chime_attempted:
            self._chime_attempted = True
            from .startup_sound import StartupSoundError, startup_chime_asset
            try:
                with startup_chime_asset(home=self._chime_home,
                        allow_synthesized=self.allow_synthesized_chime) as (path, info):
                    self.voice._play(path, 'local_startup_chime', info, (), check,
                        lambda *_a, **_kw: None, None,
                        dict(emotion='neutral', emotion_validation='none', notice=False))
                self.startup_chime_status = 'PLAYED'
            except StartupSoundError as error:
                if error.code != 'startup_sound_missing' or self.allow_synthesized_chime:
                    self.startup_chime_status = 'INVALID'
                    raise
                self.startup_chime_status = 'ASSET_NOT_TRANSFERRED'
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
                if asset.fingerprint == self.cache.spec.fingerprint:
                    expression.update(caption_payload=boot_captions('cached_greeting', self.cache.spec.public()['text']),
                        caption_asset=dict(clip_id='cached_greeting',candidate_id='cached_greeting',sha256=asset.sha256))
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
