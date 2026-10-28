"""Optional local openWakeWord adapter; no bundled BMO model or device IO.

An upstream English pre-trained model is not a Hey BMO model. A separately
trained, licensed and SHA-256 pinned model plus its exact local frontend is
required. Import, construction and status inspection never import the optional
runtime, download assets, or access a microphone. Only ``load`` performs local
model loading, after the caller's explicit enable request.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import stat
from threading import RLock

from ..contracts import FeatureError


PHRASES = ("Hey BMO", "Hey Beemo", "헤이 비모")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ARTIFACTS = ("classifier", "melspectrogram", "embedding")


class WakeModelError(FeatureError):
    """Fixed codes only; never expose local asset paths or runtime exceptions."""


def _shape(value):
    return (type(value) is list and 1 <= len(value) <= 4
            and all(item is None or type(item) is int and 0 < item <= 100000 for item in value))


def _matches(actual, expected):
    return len(actual) == len(expected) and all(
        want is None or type(got) is int and got == want
        for got, want in zip(actual, expected))


class LocalWakeDetector:
    """Real ONNX inference when explicitly loaded with a verified custom model.

    ``READY`` means assets/runtime/shape validation succeeded, never that the
    three phrases have passed acoustic acceptance. In this project no approved
    custom BMO model is supplied, so normal opt-in reports ``MODEL_MISSING``.
    """
    def __init__(self, manifest_path=None, *, loader=None):
        self._path = Path(manifest_path) if manifest_path is not None else None
        self._loader = loader
        self._lock = RLock()
        self._model = self._numpy = self._manifest = None
        self._pending, self._hits = bytearray(), 0
        self._state, self._code = "DISABLED", "disabled"

    def snapshot(self):
        with self._lock:
            return dict(enabled=self._state == "READY", status=self._state,
                        reason_code=self._code, phrases=list(PHRASES),
                        backend="openwakeword_onnx", local_only=True,
                        acoustic_acceptance="NOT_RUN", real_wake_word="NOT_RUN",
                        model_installed=self._model is not None,
                        automatic_capture=False, microphone_open=False,
                        contains_audio=False)

    def _read_manifest(self):
        if self._path is None:
            raise WakeModelError("wake_model_missing")
        try:
            mode = self._path.lstat().st_mode
            if not stat.S_ISREG(mode) or self._path.stat().st_size > 32768:
                raise WakeModelError("wake_model_invalid")
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WakeModelError("wake_model_missing") from None
        except (OSError, ValueError, UnicodeError):
            raise WakeModelError("wake_model_invalid") from None
        if (type(data) is not dict or data.get("schema") != 1
                or data.get("backend") != "openwakeword_onnx"
                or data.get("custom_model") is not True
                or data.get("sample_rate") != 16000 or data.get("frame_samples") != 1280
                or data.get("frontend") != "openwakeword_melspectrogram_embedding_v1"
                or type(data.get("phrases")) is not list or set(data["phrases"]) != set(PHRASES)
                or type(data.get("license")) is not str or not 1 <= len(data["license"]) <= 200
                or type(data.get("artifacts")) is not dict):
            raise WakeModelError("wake_model_invalid")
        threshold, consecutive = data.get("threshold", .5), data.get("consecutive_frames", 2)
        if (type(threshold) not in (int, float) or not math.isfinite(threshold)
                or not 0 < threshold < 1 or type(consecutive) is not int or not 1 <= consecutive <= 5):
            raise WakeModelError("wake_model_invalid")
        paths = {}
        base = self._path.resolve().parent
        for name in _ARTIFACTS:
            item = data["artifacts"].get(name)
            if (type(item) is not dict or type(item.get("file")) is not str
                    or not item["file"] or Path(item["file"]).name != item["file"]
                    or not item["file"].endswith(".onnx")
                    or type(item.get("sha256")) is not str or not _HASH.fullmatch(item["sha256"])
                    or type(item.get("license")) is not str or not 1 <= len(item["license"]) <= 200
                    or not _shape(item.get("input_shape")) or not _shape(item.get("output_shape"))):
                raise WakeModelError("wake_model_invalid")
            asset = base / item["file"]
            try:
                metadata = asset.lstat()
                if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= 64 * 1024 * 1024:
                    raise WakeModelError("wake_model_invalid")
                digest = hashlib.sha256()
                with asset.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                if digest.hexdigest() != item["sha256"]:
                    raise WakeModelError("wake_model_hash_mismatch")
            except FileNotFoundError:
                raise WakeModelError("wake_model_missing") from None
            except OSError:
                raise WakeModelError("wake_model_invalid") from None
            paths[name] = asset
        return data, paths

    @staticmethod
    def _load_runtime(paths):
        # Explicit paths prevent openWakeWord's empty-model/pretrained fallback.
        numpy = importlib.import_module("numpy")
        importlib.import_module("onnxruntime")
        module = importlib.import_module("openwakeword.model")
        model = module.Model(wakeword_models=[str(paths["classifier"])],
            inference_framework="onnx", melspec_model_path=str(paths["melspectrogram"]),
            embedding_model_path=str(paths["embedding"]), sr=16000, ncpu=1, device="cpu")
        return model, numpy

    def load(self):
        with self._lock:
            self._drop()
            try:
                manifest, paths = self._read_manifest()
                try:
                    model, numpy = (self._loader or self._load_runtime)(paths)
                except (ImportError, ModuleNotFoundError):
                    raise WakeModelError("wake_runtime_missing") from None
                key = paths["classifier"].stem
                sessions = {"classifier": model.models[key],
                            "melspectrogram": model.preprocessor.melspec_model,
                            "embedding": model.preprocessor.embedding_model}
                for name, session in sessions.items():
                    shape = manifest["artifacts"][name]
                    if (len(session.get_inputs()) != 1 or len(session.get_outputs()) != 1
                            or not _matches(session.get_inputs()[0].shape, shape["input_shape"])
                            or not _matches(session.get_outputs()[0].shape, shape["output_shape"])):
                        raise WakeModelError("wake_model_shape_mismatch")
                self._model, self._numpy, self._manifest = model, numpy, manifest
                self._key = key
                self._state, self._code = "READY", "ok"
                return True
            except WakeModelError as error:
                self._state = {"wake_model_missing": "MODEL_MISSING", "wake_runtime_missing": "RUNTIME_MISSING"}.get(error.code, "MODEL_INVALID")
                self._code = error.code
            except Exception:
                self._state, self._code = "MODEL_INVALID", "wake_model_invalid"
            self._drop()
            return False

    def _drop(self):
        self._model = self._numpy = self._manifest = None
        self._pending.clear()
        self._hits = 0

    def feed(self, pcm):
        with self._lock:
            if self._state != "READY":
                return False
            try:
                if type(pcm) is not bytes or not pcm or len(pcm) % 2 or len(pcm) > 3200:
                    raise ValueError()
                self._pending.extend(pcm)
                activated = False
                while len(self._pending) >= 2560:
                    chunk = bytes(self._pending[:2560])
                    del self._pending[:2560]
                    prediction = self._model.predict(self._numpy.frombuffer(chunk, dtype="<i2").copy())
                    score = float(prediction[self._key])
                    if not math.isfinite(score) or not 0 <= score <= 1:
                        raise ValueError()
                    self._hits = self._hits + 1 if score >= self._manifest.get("threshold", .5) else 0
                    if self._hits >= self._manifest.get("consecutive_frames", 2):
                        activated = True
                        self._hits = 0
                return activated
            except Exception:
                self._drop()
                self._state, self._code = "MODEL_ERROR", "wake_detector_failed"
                return False

    def reset(self):
        with self._lock:
            self._pending.clear()
            self._hits = 0
            if self._model is not None:
                try:
                    self._model.reset()
                except Exception:
                    self._drop()
                    self._state, self._code = "MODEL_ERROR", "wake_detector_failed"

    def close(self):
        with self._lock:
            self._drop()
            self._state, self._code = "DISABLED", "disabled"
        return True
