"""Single-output voice boundary for the integration launcher.

No devices, SDK clients, or processes are created on import/construction. Real
backends run only in explicit listen/speak calls; simulation creates a synthetic
PCM fixture and a harmless owned child instead of ALSA or cloud synthesis.
"""
from __future__ import annotations

import math
import os
from dataclasses import replace
from pathlib import Path
import struct
import shutil
import sys
import time
from threading import Event, Lock
import wave

from . import audio, fish_tts, local_piper
from .app import OFFLINE_KOREAN_UNSUPPORTED_EN, OFFLINE_NOTICE_EN
from .conversation import TurnCancelled
from .expression_contract import FACE_EMOTIONS
from .expressive_voice import ExpressivePolicy
from .integration_face import wav_cues
from .pronunciation import spoken_bmo_name
from .safe_playback import attenuated_copy, validate_gain
from .master_volume import MasterVolume


def _player_environment():
    # A child receives only audio/session plumbing, never arbitrary parent keys.
    allowed = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "HOME", "XDG_RUNTIME_DIR",
               "ALSA_CONFIG_PATH", "ALSA_CONFIG_DIR", "PULSE_SERVER"}
    return {name: value for name, value in os.environ.items()
            if name in allowed}


def synthetic_wav():
    """A finite voiced/silent PCM fixture; no recorded or model-generated audio."""
    path = audio._new_wav(audio.AudioSynthesisError)
    try:
        rate = 16000
        samples = []
        for index in range(int(rate * 0.8)):
            seconds = index / rate
            level = 12000 if 0.10 <= seconds < 0.35 else 7000 if 0.50 <= seconds < 0.70 else 0
            samples.append(round(level * math.sin(2 * math.pi * 220 * seconds)))
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        return path
    except BaseException:
        audio._discard(path, suppress=True)
        raise


class IntegrationVoice:
    """Persistent voice adapter; update emit/cancel_check before each worker turn.

    Injected synthesizers follow the existing synthesize_wav signatures and
    transfer ownership of their temporary result to this adapter. An injected
    player follows audio._run's signature. Private caption_payload is routed only
    to the session's UI snapshot/cache; event consumers must not log that text.
    """

    def __init__(self, config, client, *, emit, cancel_check=None, simulate=False,
                 fish_synthesizer=None, piper_synthesizer=None, player=None,
                 fish_api_key=None, expressive=False, character_life=False,
                 on_timing=None, timing_clock=time.monotonic, volume=None):
        self.config = config
        self.client = client
        self.emit = emit
        self.cancel_check = cancel_check
        self.simulate = bool(simulate)
        self.message = "Hello BMO."
        self._fish_synthesizer = fish_synthesizer
        self._piper_synthesizer = piper_synthesizer
        self._player = player
        self._fish_api_key = fish_api_key
        self._closed = Event()
        self._idle = Event()
        self._idle.set()
        self._speaking = Lock()
        self._cache_lock = Lock()
        self._cache = None
        self._cache_generation = 0
        self.expression_metadata = {"emotion": "neutral", "emotion_validation": "none"}
        self.expressive = bool(expressive)
        self.character_life = bool(character_life)
        self.character_bank = None
        self.on_timing, self.timing_clock = on_timing, timing_clock
        self._expressive_policy = ExpressivePolicy()
        self.volume = volume if isinstance(volume, MasterVolume) else MasterVolume(config.fish_gain)
        self._provider_guard = Lock()
        self._provider_code, self._provider_retry_at = None, None
        self.event_pack = None
        self._event_guard = Lock()
        self._queued_event = None

    def queue_event(self, event, request_id=None):
        """Queue one current host event for the next owned utterance only.

        The host remains responsible for actual-result admission. Pack absence
        or missing per-SHA approval falls back to that utterance's existing TTS,
        never to another clip/event or an automatic generation operation.
        """
        from .voice_pack import EVENT_CANDIDATES
        if event not in EVENT_CANDIDATES or request_id is not None and (type(request_id) is not str or not 0 < len(request_id) <= 128):
            raise audio.AudioValidationError('Choose a supported voice event.', 'voice_pack_event_invalid')
        with self._event_guard:
            self._queued_event = (event, request_id)

    def discard_queued_event(self):
        with self._event_guard:
            self._queued_event = None

    def _take_event_asset(self, check):
        from .voice_bank import VoiceBankError
        with self._event_guard:
            queued, self._queued_event = self._queued_event, None
        if queued is None or self.event_pack is None:
            return None
        try:
            return self.event_pack.load_event(queued[0], cancel_check=check)
        except VoiceBankError:
            return None

    def provider_snapshot(self):
        """Safe capability only; never validates or reveals a credential value."""
        with self._provider_guard:
            code, retry_at = self._provider_code, self._provider_retry_at
        remaining = max(0., retry_at-time.monotonic()) if retry_at is not None else None
        blocked = code in ('fish_authentication', 'fish_balance', 'fish_permission', 'fish_model') or bool(remaining)
        alsa_codes = ('audio_timeout', 'device_unavailable', 'tool_unavailable', 'audio_process_failed',
                      'audio_storage_failed', 'audio_cleanup_failed', 'audio_volume_failed', 'alsa_playback_failed')
        return dict(provider='alsa' if code in alsa_codes else 'fish', code=code or 'ready', blocked=blocked,
                    retry_after_seconds=round(remaining, 2) if remaining is not None else None,
                    recheck='explicit_reset_or_restart', automatic_retry=False)

    def reset_provider_error(self):
        """Explicit local re-admission after configuration/restart; no API call."""
        with self._provider_guard:
            self._provider_code, self._provider_retry_at = None, None
        return self.provider_snapshot()

    def _provider_failure(self, code, *, source='fish'):
        known = frozenset(('fish_authentication', 'fish_balance', 'fish_permission', 'fish_model',
            'fish_rate_limit', 'fish_connection', 'fish_timeout', 'fish_server', 'fish_request',
            'audio_timeout', 'audio_download_timeout', 'device_unavailable', 'tool_unavailable',
            'audio_process_failed', 'audio_storage_failed', 'audio_cleanup_failed', 'audio_volume_failed'))
        with self._provider_guard:
            self._provider_code = code if code in known else 'alsa_playback_failed' if source == 'alsa' else 'fish_request'
            self._provider_retry_at = time.monotonic()+30. if code == 'fish_rate_limit' else None

    def _timing(self, stage, code, **metrics):
        if self.on_timing is not None:
            try:
                self.on_timing(stage, code, at=self.timing_clock(), **metrics)
            except Exception:
                pass  # An observational sink cannot break owned playback.

    def _expression(self, *, notice=False, metadata=None):
        """Copy expression enums and the private UI caption pair, never TTS tags."""
        value = self.expression_metadata if metadata is None else metadata
        emotion = value.get("emotion") if type(value) is dict else None
        validation = value.get("emotion_validation") if type(value) is dict else None
        result = dict(emotion="neutral" if notice or emotion not in FACE_EMOTIONS else emotion,
                    emotion_validation="notice" if notice else validation if validation in
                    ("none", "manual", "validated", "degraded") else "none",
                    notice=bool(notice))
        if self.character_life and not notice and type(value) is dict and "character_plan" in value:
            from .character_director import CharacterPlan
            try:
                result["character_plan"] = CharacterPlan.from_public(value["character_plan"]).public()
            except ValueError:
                pass
        if not notice and type(value) is dict and type(value.get('caption_payload')) is dict:
            from .captions import clean_caption
            result['caption_payload'] = {lang: clean_caption(value['caption_payload'].get(lang)) for lang in ('en', 'ko')}
        return result

    @property
    def has_cache(self):
        """Whether this open session retains its last successful gained WAV."""
        with self._cache_lock:
            return not self._closed.is_set() and self._cache is not None

    def clear_cache(self):
        """Forget the sole private WAV and reject any in-flight cache promotion."""
        with self._cache_lock:
            previous, self._cache = self._cache, None
            self._cache_generation += 1
            with self._event_guard:
                self._queued_event = None
        if previous is not None:
            audio._discard(previous["path"], suppress=True)

    def invalidate_pending(self, cancel=None):
        """Invalidate a replacement without deleting the last success.

        The session can set its cancellation event under this same lock, so
        cancellation and successful cache promotion have one commit boundary.
        """
        with self._cache_lock:
            self._cache_generation += 1
            with self._event_guard:
                self._queued_event = None
            return cancel() if cancel is not None else None

    def _check(self, cancellation):
        if self._closed.is_set():
            raise TurnCancelled()
        if cancellation is not None:
            cancellation()

    def listen(self):
        cancellation = self.cancel_check
        self._check(cancellation)
        if self.simulate:
            return self.message
        path = audio.record_wav(self.config.seconds, self.config.mic_device,
                                cancel_check=lambda: self._check(cancellation),
                                env=_player_environment())
        try:
            self._check(cancellation)
            language = "ko" if self.config.language == "ko" else None
            message = audio.transcribe(self.client, path, model=self.config.stt_model,
                                       language=language)
            self._check(cancellation)
            return message
        finally:
            audio._discard(Path(path), suppress=True)

    def _synthesize(self, text, check, *, force_piper=False, notice=False, delivery=None):
        check()
        if self.simulate:
            return synthetic_wav(), "synthetic", notice
        if force_piper:
            synthesize = self._piper_synthesizer or local_piper.synthesize_wav
            return Path(synthesize(text, binary=self.config.local_piper_binary,
                                   model=self.config.local_piper_model,
                                   name_mode=self.config.fish_name_mode)), "piper", notice
        if self.config.tts_provider != "fish":
            raise audio.AudioValidationError(
                "The integration voice profile requires Fish Audio.", "invalid_tts_provider")
        readiness = self.provider_snapshot()
        if readiness['blocked']:
            raise audio.AudioSynthesisError('Fish Audio requires operator review before another request.', readiness['code'])
        spoken = spoken_bmo_name(text, self.config.fish_name_mode)
        if delivery is not None:
            spoken = delivery.text
        if self.config.fish_delivery == "soft" and not (delivery is not None and delivery.override_soft):
            spoken = fish_tts.SOFT_AARON_DIRECTION + spoken
        synthesize = self._fish_synthesizer or fish_tts.synthesize_wav
        credentials = {} if self._fish_api_key is None else {"api_key": self._fish_api_key}
        timing = ({"on_timing": self.on_timing, "clock": self.timing_clock}
                  if self.on_timing is not None and self._fish_synthesizer is None else {})
        try:
            result = Path(synthesize(spoken, model=self.config.fish_model,
                                   reference_id=self.config.fish_voice_id,
                                   timeout=self.config.timeout, cancel_check=check,
                                   **credentials, **timing))
            self.reset_provider_error()
            return result, "fish", notice
        except audio.AudioSynthesisError as error:
            self._provider_failure(error.code)
            check()
            if not self.config.offline_voice:
                raise
            english = local_piper.is_english_text(text)
            fallback = text if english else OFFLINE_KOREAN_UNSUPPORTED_EN
            return self._synthesize(fallback, check, force_piper=True, notice=notice or not english)

    def _cached_reaction(self, spoken, metadata, delivery, check):
        """A verified local prefix candidate; selection never generates audio.

        Keep the original live direction when selection/loading is unavailable.
        A selected prefix removes its one-shot Fish cue, without a second API
        call. If later PCM composition fails, speech keeps its emotion delivery
        and reports no short reaction rather than claiming an unheard prefix.
        """
        if (not self.character_life or self.character_bank is None or delivery is None
                or delivery.reaction == "none" or not delivery.mechanism.startswith("fish_character_")):
            return None, None, delivery
        from .character_director import CharacterPlan
        from .voice_bank import VoiceBank, VoiceBankError, catalog
        bank = self.character_bank
        if (not isinstance(bank, VoiceBank) or any(
                getattr(bank.config, key, None) != getattr(self.config, key, None)
                for key in ("fish_voice_id", "fish_name_mode", "fish_gain", "fish_delivery"))):
            return None, None, delivery
        asset = None
        try:
            plan = CharacterPlan.from_public(metadata.get("character_plan"))
            if (metadata.get("emotion_validation") != "validated" or plan.sensitive_context
                    or plan.source not in ("dialogue", "acting") or plan.reaction_id != delivery.reaction
                    or plan.face_emotion != delivery.emotion
                    or delivery.reaction in ("sob", "playful_puff") and not plan.explicit_acting):
                return None, None, delivery
            clip = {"chuckle": "reaction_03", "gasp": "reaction_05",
                    "playful_puff": "reaction_07", "sob": "reaction_11"}.get(delivery.reaction)
            if clip is None:
                return None, None, delivery
            context = ("acting" if delivery.reaction in ("sob", "playful_puff") else
                       "dialogue" if delivery.reaction == "gasp" else
                       "celebration" if plan.delivery_id in ("excited", "proud") else "playful")
            # select() remains the sole approval, context and cooldown gate.
            other = tuple(c for c, row in catalog().items() if row["category"] == "reaction" and c != clip)
            selected = bank.select("reaction", emotion=plan.face_emotion, context=context, recent=other)
            check()
            if selected != clip:
                return None, None, delivery
            asset = bank.load(clip, require_approved=True, simulate=self.simulate, cancel_check=check)
            quiet_metadata = dict(metadata, character_plan=replace(plan, reaction_id="none").public())
            plain = self._expressive_policy.plan(spoken, quiet_metadata, model=self.config.fish_model)
            if plain.reaction != "none" or plain.mechanism != delivery.mechanism:
                asset.close()
                return None, None, delivery
            return asset, clip, replace(delivery, text=plain.text)
        except (VoiceBankError, audio.AudioError, ValueError, TypeError, OSError):
            if asset is not None:
                asset.close()
            return None, None, delivery

    def _compose_reaction(self, asset, speech, check):
        """Join already gained PCM snapshots, with no resampling or extra gain."""
        output = None
        try:
            check()
            with wave.open(str(asset.path), "rb") as prefix, wave.open(str(speech), "rb") as answer:
                formats = lambda value: (value.getnchannels(), value.getsampwidth(),
                                          value.getframerate(), value.getcomptype())
                if (formats(prefix) != formats(answer) or prefix.getcomptype() != "NONE"
                        or prefix.getnframes()/prefix.getframerate() > 3.
                        or (prefix.getnframes()+answer.getnframes())/answer.getframerate() > 300.):
                    return None
                output = audio._new_wav(audio.AudioPlaybackError)
                with wave.open(str(output), "wb") as joined:
                    joined.setnchannels(answer.getnchannels());joined.setsampwidth(answer.getsampwidth())
                    joined.setframerate(answer.getframerate())
                    for source in (prefix, answer):
                        while True:
                            check()
                            block = source.readframes(4096)
                            if not block:
                                break
                            joined.writeframesraw(block)
            audio._wav_info(output)
            result, output = output, None
            return result
        except (audio.AudioError, OSError, EOFError, wave.Error, ValueError, ZeroDivisionError):
            return None
        finally:
            if output is not None:
                audio._discard(output, suppress=True)

    def play_event(self, event, *, emit=None, cancel_check=None, on_playback_start=None):
        """Approved-cache-only entry for idle/utility; never falls back to TTS."""
        previous_emit, previous_check = self.emit, self.cancel_check
        try:
            if emit is not None:
                self.emit = emit
            if cancel_check is not None:
                self.cancel_check = cancel_check
            self.queue_event(event)
            return self.speak('Local voice event.', on_playback_start=on_playback_start, _event_only=True) is True
        finally:
            self.emit, self.cancel_check = previous_emit, previous_check

    def speak(self, text, *, on_playback_start=None, _force_piper=False, _notice=False, _event_only=False):
        if not isinstance(text, str):
            raise audio.AudioValidationError("Speech input must be text.", "invalid_speech_text")
        if not text.strip():
            return
        if not self._speaking.acquire(blocking=False):
            raise audio.AudioPlaybackError("The voice player is busy.", "player_busy")
        self._idle.clear()
        with self._cache_lock:
            generation = self._cache_generation
        cancellation, emit = self.cancel_check, self.emit
        check = lambda: self._check(cancellation)
        raw = output = None
        reaction_asset = reaction_clip = None
        event_asset = None
        playback_baked_gain = self.config.fish_gain
        cached_reaction = False
        delivery = None
        metadata = dict(self.expression_metadata) if type(self.expression_metadata) is dict else {}
        source = "synthetic" if self.simulate else "piper" if _force_piper else "fish"
        try:
            check()
            validate_gain(self.config.fish_gain)
            event_asset = self._take_event_asset(check) if not _force_piper and not _notice else None
            if _event_only and event_asset is None:
                return False
            if event_asset is not None:
                # Exact source PCM, provenance and clip text; do not pretend a
                # Drama3 cache came from the live free synthesis configuration.
                output = audio._new_wav(audio.AudioPlaybackError)
                shutil.copyfile(event_asset.path, output)
                source, notice = 'voice_pack', False
                playback_baked_gain = event_asset.baked_gain
                metadata = dict(metadata, caption_payload={'en': event_asset.text, 'ko': ''})
                metadata.pop('character_plan', None)
            if event_asset is None and self.expressive and not _force_piper and not _notice:
                delivery = self._expressive_policy.plan(
                    spoken_bmo_name(text, self.config.fish_name_mode),
                    metadata, model=self.config.fish_model)
                reaction_asset, reaction_clip, delivery = self._cached_reaction(
                    spoken_bmo_name(text, self.config.fish_name_mode), metadata, delivery, check)
            if event_asset is None:
                settings = {} if delivery is None else {"delivery": delivery}
                raw, source, notice = self._synthesize(text, check, force_piper=_force_piper,
                                                      notice=_notice, **settings)
                # A synchronous provider may finish after cancellation. Discard
                # late results before any prepared/start event or player spawn.
                check()
                output = attenuated_copy(raw, self.config.fish_gain)
            if reaction_asset is not None:
                joined = (self._compose_reaction(reaction_asset, output, check)
                          if source in ("fish", "synthetic") and not notice else None)
                if joined is not None:
                    audio._discard(output, suppress=True)
                    output, cached_reaction = joined, True
                else:
                    delivery = replace(delivery, reaction="none")
            expression = self._expression(notice=notice, metadata=metadata)
            if delivery is not None:
                expression.update(
                    voice_emotion=delivery.emotion if source == "fish" else "neutral",
                    reaction=delivery.reaction if source == "fish" else "none",
                    voice_style=delivery.mechanism if source == "fish" else source,
                    tts_mechanism=delivery.mechanism, tts_override_soft=delivery.override_soft,
                    tts_backend=source, tts_requested_emotion=delivery.emotion)
            if self.character_life and self.character_bank is not None:
                expression.update(cached_reaction=cached_reaction,
                                  cached_reaction_id=reaction_clip if cached_reaction else None)
            info = audio._wav_info(output)
            cues = event_asset.cues if event_asset is not None else tuple(dict(cue) for cue in wav_cues(output))
            if event_asset is not None:
                expression.update(cache_source_model=event_asset.source_model,
                                  voice_pack_candidate=event_asset.candidate_id)
            if source == "fish":
                self._timing("fish", "audio_ready")
            self._play(output, source, info, cues, check, emit, on_playback_start, expression,
                       baked_gain=playback_baked_gain)
            check()
            if delivery is not None and source == "fish":
                self._expressive_policy.completed(delivery)
            if cached_reaction:
                try:
                    self.character_bank.completed(reaction_clip)
                except Exception:
                    pass  # Successful dialogue never fails on selection-history IO.
            # A failed/cancelled replacement keeps the previous successful WAV.
            # Promotion transfers the final gained file, without another copy.
            previous = None
            with self._cache_lock:
                check()
                if generation == self._cache_generation and not self._closed.is_set() and not _event_only:
                    previous, self._cache = self._cache, dict(
                        path=output, source=source, info=info, cues=cues, expression=expression,
                        baked_gain=playback_baked_gain)
                    output = None
            if previous is not None:
                audio._discard(previous["path"], suppress=True)
            if event_asset is not None:
                return True
        except (TurnCancelled, KeyboardInterrupt):
            emit("cancelled", source=source)
            raise
        except BaseException:
            emit("error", source=source)
            raise
        finally:
            if reaction_asset is not None:
                reaction_asset.close()
            if event_asset is not None:
                event_asset.close()
            for path in (output, raw):
                if path is not None:
                    audio._discard(Path(path), suppress=True)
            self._speaking.release()
            self._idle.set()

    def _play(self, output, source, info, cues, check, emit, on_playback_start, expression, *, baked_gain=None):
        """The only player/event path, shared by a new utterance and repeat."""
        check()
        emit("prepared", path=str(output), source=source,
             cues=[dict(cue) for cue in cues], **expression)
        check()

        def started():
            check()
            self._timing("playback", "playback_started")
            if on_playback_start is not None:
                on_playback_start()
            check()
            emit("started", path=str(output), source=source, **expression)

        with self.volume.prepare(output, baked_gain=self.config.fish_gain if baked_gain is None else baked_gain) as played:
            check()
            if self.simulate:
                command = [sys.executable, "-c", "import time; time.sleep(0.8)"]
            else:
                command = ["aplay", "-q", *audio._device_args(self.config.speaker_device), str(played)]
            player = self._player or audio._run
            try:
                player(command, timeout=info["duration_seconds"] + 10,
                       error_type=audio.AudioPlaybackError, on_start=started,
                       cancel_check=check, env=_player_environment())
            except audio.AudioPlaybackError as error:
                self._provider_failure(error.code, source='alsa')
                raise
        check()
        emit("ended", source=source)

    def repeat(self, *, on_playback_start=None):
        """Play the exact last success, without synthesis, gain or cue rebuild.

        Return False without spawning when empty/closed/busy. A failed or
        cancelled repeat retains the cache for the next explicit request.
        """
        if not self._speaking.acquire(blocking=False):
            return False
        self._idle.clear()
        cancellation, emit = self.cancel_check, self.emit
        check = lambda: self._check(cancellation)
        source = None
        try:
            with self._cache_lock:
                cached = self._cache
            if cached is None or self._closed.is_set():
                return False
            source = cached["source"]
            self._play(cached["path"], source, cached["info"], cached["cues"],
                       check, emit, on_playback_start, cached["expression"],
                       baked_gain=cached.get('baked_gain', self.config.fish_gain))
            return True
        except (TurnCancelled, KeyboardInterrupt):
            emit("cancelled", source=source)
            raise
        except BaseException:
            emit("error", source=source)
            raise
        finally:
            self._speaking.release()
            self._idle.set()

    def offline_notice(self, *, on_playback_start=None):
        """Existing fixed English notice; this is not an offline chat brain."""
        if self.config.offline_voice:
            self.speak(OFFLINE_NOTICE_EN, on_playback_start=on_playback_start,
                       _force_piper=True, _notice=True)

    def close(self):
        """Cancel owned playback; late synthesis cleans itself on return.

        Never wait indefinitely for synchronous cloud/Piper synthesis. Return
        whether the current operation has completed its cleanup.
        """
        self._closed.set()
        stopped = self._idle.wait(0.5)
        self.clear_cache()
        return stopped
