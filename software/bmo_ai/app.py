"""One explicit, half-duplex conversational turn with optional face adapter."""

OFFLINE_NOTICE_EN = (
    "Beemo is here. The online service is unavailable, "
    "so I cannot understand new questions right now."
)
OFFLINE_KOREAN_UNSUPPORTED_EN = (
    "Beemo is still here. My local voice only supports English for now."
)
from pathlib import Path

from . import audio
from .hardware import FaceState, NullFace
from .telemetry import LOGGER, event, measure


class ConversationApp:
    def __init__(self, brain, client, config, *, face=None, muted=False, voice=None,
                 cancel_check=None):
        self.brain = brain
        self.client = client
        self.config = config
        self.face = face or NullFace()
        self.muted = muted
        # Optional injected voice owns listen() and speak(text, on_playback_start=).
        # Constructing the app never initializes a device or SDK client.
        self.voice = voice
        self.cancel_check = cancel_check

    def _check_cancelled(self):
        if self.cancel_check is not None:
            self.cancel_check()

    def _state(self, state):
        try:
            self.face.set_state(state)
        except Exception:
            event("face_unavailable")

    def answer(self, message):
        self._state(FaceState.THINKING)
        self._check_cancelled()
        with measure("chat"):
            answer = self.brain.ask(message)
        self._check_cancelled()
        return answer

    def _speak_local(self, text, *, on_playback_start):
        """Use Piper only if the user enabled local fallback."""
        from . import local_piper
        local_piper.speak(
            text, binary=self.config.local_piper_binary,
            model=self.config.local_piper_model,
            name_mode=self.config.fish_name_mode,
            device=self.config.speaker_device,
            gain=self.config.fish_gain,
            on_playback_start=on_playback_start,
            cancel_check=self.cancel_check,
        )

    def offline_notice(self):
        """Announce cloud unavailability, not an offline chat reply."""
        if self.muted or not self.config.offline_voice:
            return
        self._state(FaceState.THINKING)
        try:
            def started():
                self._check_cancelled()
                self._state(FaceState.SPEAKING)
            self._speak_local(OFFLINE_NOTICE_EN, on_playback_start=started)
            event("offline_voice_notice")
        finally:
            self.idle()

    def playback(self, answer):
        if self.muted or not answer:
            return
        self._state(FaceState.THINKING)
        try:
            self._check_cancelled()
            with measure("speech"):
                def started():
                    self._check_cancelled()
                    self._state(FaceState.SPEAKING)
                    self._check_cancelled()

                if self.voice is not None:
                    self.voice.speak(answer, on_playback_start=started)
                elif self.config.tts_provider == "fish":
                    from .fish_tts import speak as fish_speak
                    try:
                        fish_speak(answer, device=self.config.speaker_device,
                                   model=self.config.fish_model,
                                   reference_id=self.config.fish_voice_id,
                                   timeout=self.config.timeout,
                                   gain=self.config.fish_gain,
                                   name_mode=self.config.fish_name_mode,
                                   delivery=self.config.fish_delivery,
                                   on_playback_start=started,
                                   cancel_check=self.cancel_check)
                    except audio.AudioSynthesisError as fish_error:
                        if not self.config.offline_voice:
                            raise
                        event("fish_tts_local_fallback", code=fish_error.code)
                        from .local_piper import is_english_text
                        fallback_text = (
                            answer if is_english_text(answer) else OFFLINE_KOREAN_UNSUPPORTED_EN
                        )
                        self._speak_local(fallback_text, on_playback_start=started)
                else:
                    audio.speak(self.client, answer, voice=self.config.voice,
                                device=self.config.speaker_device, model=self.config.tts_model,
                                on_playback_start=started, cancel_check=self.cancel_check)
        finally:
            self.idle()

    def voice_input(self):
        self._state(FaceState.LISTENING)
        self._check_cancelled()
        if self.voice is not None:
            message = self.voice.listen()
            self._check_cancelled()
            return message
        with measure("record"):
            path = audio.record_wav(self.config.seconds, self.config.mic_device)
        primary_failure = False
        try:
            self._check_cancelled()
            with measure("transcribe"):
                language = "ko" if self.config.language == "ko" else None
                return audio.transcribe(self.client, path, model=self.config.stt_model,
                                        language=language)
        except BaseException:
            primary_failure = True
            raise
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("audio_temp_cleanup_failed")
                if not primary_failure:
                    raise audio.AudioCleanupError(
                        "Temporary audio file cleanup failed. Check temporary-directory permissions.",
                        "audio_cleanup_failed",
                    ) from None

    def idle(self):
        self._state(FaceState.IDLE)

    def failed(self):
        self._state(FaceState.ERROR)
