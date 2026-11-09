"""Pinned public Hey Beemo candidate preparation and saved-WAV CPU inspection.

No microphone, playback, model training, paid service, verifier pickle or
arbitrary model URL is used. Preparation requires an explicit download flag;
all ONNX files belong in a private cache outside any Git checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import secrets
import stat
import time
import urllib.request
import wave

from .features.input.wake_word import LocalWakeDetector, PHRASES


REVISION = '58eb9404df18ea1ca64b73ecc9614b1e6ee8f2d6'
REPOSITORY = 'amrmantawi/bmo-openwakeword'
MODEL_CARD = f'https://huggingface.co/{REPOSITORY}/blob/{REVISION}/README.md'
FILES = {
    'classifier': dict(file='hey_beemo.onnx', bytes=206276,
        sha256='df035b9be9357106b3398368b44dd4f65c4ee88f8cd20612e9b7d850c8b0c015',
        input_shape=[1, 16, 96], output_shape=[1, 1]),
    'melspectrogram': dict(file='melspectrogram.onnx', bytes=1087958,
        sha256='ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f',
        input_shape=[None, None], output_shape=[None, 1, None, 32]),
    'embedding': dict(file='embedding_model.onnx', bytes=1328103,
        sha256='ba754db3cd768a524c655ea90655ee5e6055a43b8dfd29366a11e93716ae9e51',
        input_shape=[None, 76, 32, 1], output_shape=[None, 1, 1, 96]),
}


class WakePreparationError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('The local wake model preparation could not proceed.')


def _private_root(directory):
    root = Path(directory)
    if not root.is_absolute() or '..' in root.parts or any((p/'.git').exists() for p in (root, *root.parents)):
        raise WakePreparationError('private_model_cache_outside_checkout_required')
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise WakePreparationError('unsafe_model_cache')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise WakePreparationError('unsafe_model_cache')
    return root


def _read_private(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= limit):
            raise WakePreparationError('unsafe_local_wake_file')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            return stream.read(limit+1)
    finally:
        if descriptor is not None: os.close(descriptor)


def _write_new(path, payload):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(payload);stream.flush();os.fsync(stream.fileno())


def candidate_manifest():
    artifacts = {name: dict(item, license='Apache-2.0 (publisher declaration)',
        source_url=f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{item["file"]}')
        for name, item in FILES.items()}
    return dict(schema=1, backend='openwakeword_onnx', custom_model=True,
        sample_rate=16000, frame_samples=1280,
        phrases=['Hey Beemo'], supported_phrases=['Hey Beemo'], requested_phrases=list(PHRASES),
        frontend='openwakeword_melspectrogram_embedding_v1',
        license='Apache-2.0 (publisher model-card declaration; training provenance not independently audited)',
        source_repository=REPOSITORY, source_revision=REVISION, model_card=MODEL_CARD,
        threshold=.5, consecutive_frames=2, acoustic_acceptance='NOT_RUN',
        korean_acceptance='UNVERIFIED', runtime_reference=dict(openwakeword='0.6.0', onnxruntime='1.31.1'),
        artifacts=artifacts)


def _fetch(url, limit):
    with urllib.request.urlopen(url, timeout=30) as stream:
        return stream.read(limit+1)


def prepare_candidate(directory, *, allow_download=False, fetcher=None):
    root = _private_root(directory)
    fetcher = fetcher or _fetch
    downloaded, reused = 0, 0
    for item in candidate_manifest()['artifacts'].values():
        path = root/item['file']
        try:
            payload = _read_private(path, 8*1024*1024);reused += 1
        except FileNotFoundError:
            if allow_download is not True:
                raise WakePreparationError('explicit_public_download_required') from None
            payload = fetcher(item['source_url'], 8*1024*1024)
            if (type(payload) is not bytes or len(payload) != item['bytes']
                    or hashlib.sha256(payload).hexdigest() != item['sha256']):
                raise WakePreparationError('public_model_hash_mismatch')
            _write_new(path, payload);downloaded += 1
        if len(payload) != item['bytes'] or hashlib.sha256(payload).hexdigest() != item['sha256']:
            raise WakePreparationError('local_model_hash_mismatch')
    manifest = json.dumps(candidate_manifest(), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    path = root/'manifest.json'
    try:
        existing = _read_private(path, 32768)
        if existing != manifest: raise WakePreparationError('existing_wake_manifest_preserved')
    except FileNotFoundError:
        _write_new(path, manifest)
    return dict(status='PINNED_ASSETS_READY', source_revision=REVISION, downloaded=downloaded,
        reused=reused, manifest_file='manifest.json', supported_phrases=['Hey Beemo'],
        requested_phrases=list(PHRASES), acoustic_acceptance='NOT_RUN', korean_acceptance='UNVERIFIED')


def inspect_saved_wavs(manifest, paths):
    """Actual CPU inference on <=3 existing PCM files, without acoustic labels."""
    if not isinstance(paths, (tuple, list)) or not 1 <= len(paths) <= 3:
        raise WakePreparationError('one_to_three_saved_wavs_required')
    detector = LocalWakeDetector(manifest)
    began = time.perf_counter()
    if not detector.load():
        return dict(status='BLOCKED', detector=detector.snapshot(), microphone_opens=0, playback_calls=0)
    report = dict(status='OPERATIONAL_INFERENCE_PASS', detector=detector.snapshot(),
        model_load_seconds=round(time.perf_counter()-began, 6),
        python=platform.python_version(), machine=platform.machine(),
        versions={name: importlib.metadata.version(name) for name in ('openwakeword', 'onnxruntime', 'numpy')},
        clips=[], positive_wake_accuracy='NOT_RUN_NO_LICENSED_POSITIVE_CLIP',
        human_pronunciation='NOT_RUN', korean_pronunciation='UNVERIFIED',
        microphone_opens=0, playback_calls=0, network_audio_uploads=0,
        warning='Unlabelled stored audio is not a false-positive or wake-accuracy benchmark.')
    try:
        for path in paths:
            path = Path(path)
            if not path.is_absolute() or path.suffix.lower() != '.wav':
                raise WakePreparationError('private_saved_wav_required')
            payload = _read_private(path, 1024*1024)
            import io
            with wave.open(io.BytesIO(payload), 'rb') as stream:
                if (stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getframerate() != 16000
                        or stream.getcomptype() != 'NONE' or not 0 < stream.getnframes() <= 16000*12):
                    raise WakePreparationError('saved_wav_format_unsupported')
                frames, count, windows = stream.getnframes(), 0, []
                detector.reset();peak, activations = 0., 0
                started = time.perf_counter()
                while True:
                    block = stream.readframes(1280)
                    if not block: break
                    activated = bool(detector.feed(block))
                    activations += activated;count += 1
                    state = detector.snapshot()
                    if state['status'] != 'READY': raise WakePreparationError('saved_wav_inference_failed')
                    peak = max(peak, state['last_score'] or 0.)
                    if len(block) == 2560:
                        windows.append(dict(at_seconds=round(count*.08, 4), score=round(state['last_score'] or 0., 8),
                                            activated=activated))
                report['clips'].append(dict(file_name=path.name, sha256=hashlib.sha256(payload).hexdigest(),
                    seconds=round(frames/16000, 4), chunks=count, activations=activations,
                    peak_score=round(peak, 8), cpu_inference_seconds=round(time.perf_counter()-started, 6),
                    windows=windows, processed_samples=(frames//1280)*1280, buffered_tail_samples=frames%1280,
                    label='UNLABELLED_EXISTING_PCM', accuracy_evaluated=False))
        report['detector_after'] = detector.snapshot()
        return report
    finally:
        detector.close()


def infer_wav(manifest, path):
    """One bounded saved-WAV inference summary, suitable for human review tools."""
    result = inspect_saved_wavs(manifest, [path])
    if result['status'] == 'BLOCKED':
        return result
    detector = result['detector']
    return dict(status=result['status'], model_sha256=detector['model_sha256'],
        execution_providers=detector['execution_providers'], supported_phrases=detector['supported_phrases'],
        requested_phrases=detector['requested_phrases'], unverified_phrases=detector['unverified_phrases'],
        **result['clips'][0], positive_wake_accuracy=result['positive_wake_accuracy'],
        human_pronunciation='NOT_RUN', korean_pronunciation='UNVERIFIED', microphone_opens=0,
        playback_calls=0, network_audio_uploads=0)


def save_private_report(directory, report):
    root = _private_root(directory)
    name = 'wake-inference-'+secrets.token_hex(12)+'.json'
    _write_new(root/name, json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--allow-download', action='store_true')
    parser.add_argument('--saved-wav', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        result = prepare_candidate(args.directory, allow_download=args.allow_download) if args.prepare else {}
        if args.saved_wav:
            inference = inspect_saved_wavs(Path(args.directory)/'manifest.json', args.saved_wav)
            result.update(inference=inference, private_report_file=save_private_report(args.directory, inference))
        if not result: raise WakePreparationError('explicit_preparation_or_saved_wav_required')
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0 if result.get('inference', {}).get('status') != 'BLOCKED' else 2
    except (WakePreparationError, OSError, ValueError) as error:
        print(json.dumps(dict(status='BLOCKED', code=getattr(error, 'code', 'wake_preparation_failed'))))
        return 2


if __name__ == '__main__': raise SystemExit(main())
