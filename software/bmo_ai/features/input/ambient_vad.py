"""Optional local speech evidence for PTT; never a speaker/wake recognizer.

The baseline RMS gate remains available. WebRTC's small CPU detector uses its
own adaptive sub-band model instead of treating all low-frequency room energy
as speech. It sees analysis chunks only: the retained/STT PCM stays unmodified.
No model downloads, device access or runtime installation occur here.
"""
from dataclasses import dataclass
import importlib
import importlib.metadata

from ..contracts import FeatureError


class VadError(FeatureError):
    pass


@dataclass(frozen=True, slots=True)
class VoicingEvidence:
    voiced: bool
    subframes: int
    positive_subframes: int
    positive_seconds: float


class WebRtcVoiceEvidence:
    """Mode1 balances speech retention and stationary-noise rejection.

    Native 40ms source frames are split into two supported20ms chunks. Other
    finite source sizes use20/10ms chunks plus a bounded remainder. A positive
    subframe preserves that source frame, then the existing minimum-speech,
    silence-tail and preroll rules decide the turn boundary. This heuristic is
    not proof that a person spoke: tones/music may also be positive.
    """
    def __init__(self, *, sample_rate=16000, aggressiveness=1, _module=None):
        if (sample_rate != 16000 or type(sample_rate) is not int
                or type(aggressiveness) is not int or not 0 <= aggressiveness <= 3):
            raise VadError("invalid_vad_configuration")
        try:
            module = _module or importlib.import_module("webrtcvad")
        except ImportError:
            raise VadError("vad_backend_missing") from None
        try:
            self._vad = module.Vad(aggressiveness)
        except Exception:
            raise VadError("vad_backend_failed") from None
        try:
            self.version = importlib.metadata.version("webrtcvad-wheels")
        except importlib.metadata.PackageNotFoundError:
            self.version = "UNVERIFIED_DISTRIBUTION"
        self.sample_rate, self.aggressiveness = sample_rate, aggressiveness
        self._pending = bytearray()
        self.frames = self.positive_frames = self.subframes = 0

    def classify(self, pcm):
        if type(pcm) is not bytes or not pcm or len(pcm) % 2 or len(pcm) > 3200:
            raise VadError("invalid_vad_frame")
        self._pending.extend(pcm)
        positives = count = 0
        positive_seconds = 0.
        try:
            while len(self._pending) >= 320:
                size = 640 if len(self._pending) >= 640 else 320
                chunk = bytes(self._pending[:size])
                del self._pending[:size]
                result = self._vad.is_speech(chunk, self.sample_rate)
                if type(result) is not bool:
                    raise ValueError()
                count += 1
                positives += result
                if result:
                    positive_seconds += size / (self.sample_rate * 2)
        except Exception:
            self._pending.clear()
            raise VadError("vad_backend_failed") from None
        self.frames += 1
        self.subframes += count
        self.positive_frames += positives > 0
        # An unusual25ms source can complete30ms of analysis with5ms carried
        # from its previous frame. Attribute evidence conservatively to this
        # frame rather than crediting more positive time than it contains.
        # Native40ms source frames have no remainder and are unchanged.
        positive_seconds = min(positive_seconds,len(pcm)/(self.sample_rate*2))
        return VoicingEvidence(positives > 0, count, positives, positive_seconds)

    def snapshot(self):
        return dict(backend="webrtc", aggressiveness=self.aggressiveness, version=self.version,
                    status="READY", frames=self.frames, positive_frames=self.positive_frames,
                    analysed_subframes=self.subframes, raw_pcm_modified=False,
                    human_accuracy="NOT_RUN", speaker_identity="NOT_IMPLEMENTED")

    def close(self):
        self._pending.clear()
