"""One explicit, half-duplex conversational turn with optional face adapter."""
from pathlib import Path

from . import audio
from .hardware import FaceState, NullFace
from .telemetry import LOGGER, event, measure


class ConversationApp:
    def __init__(self, brain, client, config, *, face=None, muted=False):
        self.brain = brain
        self.client = client
        self.config = config
        self.face = face or NullFace()
        self.muted = muted

    def _state(self, state):
        try:
            self.face.set_state(state)
        except Exception:
            event("face_unavailable")

    def answer(self, message):
        self._state(FaceState.THINKING)
        with measure("chat"):
            return self.brain.ask(message)

    def playback(self, answer):
        if self.muted or not answer:
            return
        self._state(FaceState.SPEAKING)
        with measure("speech"):
            audio.speak(self.client, answer, voice=self.config.voice,
                        device=self.config.speaker_device, model=self.config.tts_model)

    def voice_input(self):
        self._state(FaceState.LISTENING)
        with measure("record"):
            path = audio.record_wav(self.config.seconds, self.config.mic_device)
        primary_failure = False
        try:
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
