"""Private fixed-greeting asset; imports and construction perform no IO.

One atomic bundle contains the already gained WAV and its exact derived cues.
Preparing, auditioning and approving are distinct explicit human operations.
No missing/corrupt cache triggers synthesis, credentials, Piper or playback.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets

from . import audio, fish_tts
from .conversation import TurnCancelled
from .integration_face import wav_cues
from .private_credentials import (_close, _directory_ok, _open_directory,
                                  _private_file, CredentialError)
from .pronunciation import spoken_bmo_name
from .safe_playback import attenuated_copy, validate_gain

GREETING_TEXT = '돌아왔구나! 비모가 기다렸어!'
MAGIC = b'BMO_GREETING_V1\n'
MAX_WAV_BYTES = 8 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_DURATION = 10.0
_FILE = 'greeting.cache'
_CODES = frozenset(('greeting_missing', 'greeting_corrupt', 'greeting_unsafe',
    'greeting_busy', 'greeting_unapproved', 'greeting_simulated_only',
    'greeting_spec_changed', 'greeting_prepare_denied', 'greeting_storage_failed',
    'greeting_synthesis_failed', 'greeting_playback_failed', 'greeting_closed',
    'greeting_player_busy'))


class GreetingError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in _CODES else 'greeting_corrupt'
        super().__init__('The fixed greeting is unavailable; inspect its safe status code.')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _check(callback):
    if callback is not None and callback() is True:
        raise TurnCancelled()


def default_greeting_directory(*, environ=None, home=None):
    """Resolve nonsecret location only; never create/read a credential store."""
    source = os.environ if environ is None else environ
    base = source.get('XDG_CACHE_HOME')
    root = Path(base) if base else Path(home).joinpath('.cache') if home is not None else Path.home()/'.cache'
    path = root/'bmo'/'greeting-welcome-v2'
    if not path.is_absolute() or '..' in path.parts:
        raise GreetingError('greeting_unsafe')
    return path


@dataclass(frozen=True, slots=True)
class GreetingSpec:
    fish_model: str
    fish_voice_id: str
    fish_name_mode: str
    fish_delivery: str
    gain: float

    @classmethod
    def from_config(cls, config):
        if config.tts_provider != 'fish':
            raise GreetingError('greeting_spec_changed')
        return cls(config.fish_model, config.fish_voice_id, config.fish_name_mode,
                   config.fish_delivery, validate_gain(config.fish_gain))

    @property
    def spoken(self):
        value = spoken_bmo_name(GREETING_TEXT, self.fish_name_mode)
        return fish_tts.SOFT_AARON_DIRECTION+value if self.fish_delivery == 'soft' else value

    def public(self):
        return dict(version=1, text=GREETING_TEXT, provider='fish', model=self.fish_model,
            voice_id=self.fish_voice_id, name_mode=self.fish_name_mode, delivery=self.fish_delivery,
            gain=self.gain, spoken=self.spoken, cue_algorithm='original_bridge_30fps_v1')

    @property
    def fingerprint(self):
        return hashlib.sha256(_json(self.public())).hexdigest()


@dataclass(slots=True, repr=False)
class GreetingAsset:
    """Caller owns this verified temporary snapshot; never publish its path."""
    path: Path
    sha256: str
    fingerprint: str
    approved: bool
    simulated: bool
    info: dict
    cues: tuple
    _closed: bool = field(default=False, init=False)

    def public(self):
        return dict(sha256=self.sha256, fingerprint=self.fingerprint, approved=self.approved,
                    simulated=self.simulated, duration_seconds=self.info['duration_seconds'])

    def close(self):
        if not self._closed:
            self._closed = True
            audio._discard(self.path, suppress=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class GreetingCache:
    def __init__(self, directory, spec):
        self.directory = Path(directory)
        if (not self.directory.is_absolute() or '..' in self.directory.parts
                or '\0' in str(self.directory) or not isinstance(spec, GreetingSpec)):
            raise GreetingError('greeting_unsafe')
        self.spec = spec

    def _directory(self, *, create=False):
        try:
            return _open_directory(self.directory, create=create)
        except CredentialError:
            raise GreetingError('greeting_unsafe') from None

    @contextmanager
    def _write_lock(self):
        directory = self._directory(create=True)
        lock = None
        try:
            lock = os.open('.greeting.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK,
                           0o600, dir_fd=directory)
            _private_file(os.fstat(lock), size_limit=0)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise GreetingError('greeting_busy') from None
            yield directory
        except (CredentialError, OSError):
            raise GreetingError('greeting_unsafe') from None
        finally:
            _close(lock);_close(directory)

    def _read(self, directory):
        descriptor = None
        try:
            if directory is None:
                raise GreetingError('greeting_missing')
            descriptor = os.open(_FILE, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK, dir_fd=directory)
            _private_file(os.fstat(descriptor), size_limit=MAX_WAV_BYTES+MAX_HEADER_BYTES+len(MAGIC)+1)
            with os.fdopen(descriptor, 'rb') as source:
                descriptor = None
                raw = source.read(MAX_WAV_BYTES+MAX_HEADER_BYTES+len(MAGIC)+2)
            if not raw.startswith(MAGIC):
                raise ValueError()
            header, separator, wav = raw[len(MAGIC):].partition(b'\n')
            if not separator or len(header)>MAX_HEADER_BYTES or not 44<=len(wav)<=MAX_WAV_BYTES:
                raise ValueError()
            value = json.loads(header, object_pairs_hook=_unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if (type(value) is not dict or set(value)!={'version','spec','fingerprint','sha256','cues','cues_sha256','approved','simulated'}
                    or type(value['version']) is not int or value['version']!=1
                    or type(value['approved']) is not bool or type(value['simulated']) is not bool):
                raise ValueError()
            if _json(value['spec'])!=_json(self.spec.public()) or value['fingerprint']!=self.spec.fingerprint:
                raise GreetingError('greeting_spec_changed')
            if (hashlib.sha256(wav).hexdigest()!=value['sha256']
                    or type(value['cues']) is not list or len(value['cues'])>1000
                    or hashlib.sha256(_json(value['cues'])).hexdigest()!=value['cues_sha256']):
                raise ValueError()
            return value, wav
        except FileNotFoundError:
            raise GreetingError('greeting_missing') from None
        except (CredentialError, OSError):
            raise GreetingError('greeting_unsafe') from None
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            raise GreetingError('greeting_corrupt') from None
        finally:
            _close(descriptor)

    def _snapshot(self, payload):
        path = audio._new_wav(audio.AudioSynthesisError)
        try:
            path.write_bytes(payload)
            info = audio._wav_info(path)
            if info['duration_seconds']>MAX_DURATION:
                raise ValueError()
            return path, info
        except BaseException:
            audio._discard(path, suppress=True)
            raise

    def load(self, *, simulate=False, require_approved=True, cancel_check=None):
        if type(simulate) is not bool or type(require_approved) is not bool:
            raise GreetingError('greeting_corrupt')
        _check(cancel_check)
        directory = self._directory()
        path = None
        try:
            record, payload = self._read(directory)
            if record['simulated'] and not simulate:
                raise GreetingError('greeting_simulated_only')
            if require_approved and not record['approved']:
                raise GreetingError('greeting_unapproved')
            path, info = self._snapshot(payload)
            cues = tuple(dict(cue) for cue in wav_cues(path))
            if list(cues)!=record['cues']:
                raise GreetingError('greeting_corrupt')
            _check(cancel_check)
            asset = GreetingAsset(path, record['sha256'], record['fingerprint'], record['approved'], record['simulated'], info, cues)
            path = None
            return asset
        except (audio.AudioError, OSError, ValueError, TypeError):
            raise GreetingError('greeting_corrupt') from None
        finally:
            _close(directory)
            if path is not None:
                audio._discard(path, suppress=True)

    def _commit(self, directory, record, wav, cancel_check):
        descriptor = temporary = None
        try:
            _directory_ok(directory)
            raw = MAGIC+_json(record)+b'\n'+wav
            if len(_json(record))>MAX_HEADER_BYTES or len(wav)>MAX_WAV_BYTES:
                raise ValueError()
            temporary = '.greeting.'+secrets.token_hex(16)+'.tmp'
            descriptor = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,
                                 0o600, dir_fd=directory)
            with os.fdopen(descriptor, 'wb') as output:
                descriptor = None
                output.write(raw);output.flush();os.fsync(output.fileno())
            _check(cancel_check)
            try:
                _private_file(os.stat(_FILE, dir_fd=directory, follow_symlinks=False),
                              size_limit=MAX_WAV_BYTES+MAX_HEADER_BYTES+len(MAGIC)+1)
            except FileNotFoundError:
                pass
            os.replace(temporary, _FILE, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
            os.fsync(directory)
        except (CredentialError, OSError, ValueError, TypeError):
            raise GreetingError('greeting_storage_failed') from None
        finally:
            _close(descriptor)
            if temporary is not None:
                try:os.unlink(temporary, dir_fd=directory)
                except OSError:pass

    def publish(self, raw_wav, *, approved=False, simulated=False, cancel_check=None):
        """Explicit import: caller retains raw_wav; only this gained copy is saved."""
        if type(approved) is not bool or type(simulated) is not bool:
            raise GreetingError('greeting_corrupt')
        source = gained = None
        descriptor = None
        try:
            _check(cancel_check)
            descriptor = os.open(raw_wav, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            _private_file(os.fstat(descriptor), size_limit=MAX_WAV_BYTES)
            with os.fdopen(descriptor, 'rb') as handle:
                descriptor = None
                payload = handle.read(MAX_WAV_BYTES+1)
            source, _ = self._snapshot(payload)
            gained = attenuated_copy(source, self.spec.gain)
            info = audio._wav_info(gained)
            if info['duration_seconds']>MAX_DURATION:
                raise ValueError()
            wav = gained.read_bytes()
            cues = wav_cues(gained)
            record = dict(version=1, spec=self.spec.public(), fingerprint=self.spec.fingerprint,
                sha256=hashlib.sha256(wav).hexdigest(), cues=cues,
                cues_sha256=hashlib.sha256(_json(cues)).hexdigest(), approved=approved, simulated=simulated)
            _check(cancel_check)
            with self._write_lock() as directory:
                self._commit(directory, record, wav, cancel_check)
            return dict(sha256=record['sha256'], fingerprint=record['fingerprint'], approved=approved,
                        simulated=simulated, duration_seconds=info['duration_seconds'])
        except (CredentialError, OSError, audio.AudioError, ValueError, TypeError):
            raise GreetingError('greeting_corrupt') from None
        finally:
            _close(descriptor)
            for path in (source, gained):
                if path is not None:audio._discard(path, suppress=True)

    def approve(self, expected_sha256):
        """Explicit human-listening acknowledgement, bound to the current WAV hash."""
        if type(expected_sha256) is not str or len(expected_sha256)!=64:
            raise GreetingError('greeting_corrupt')
        # Validate PCM/cues before acknowledging it, including synthetic assets.
        with self.load(simulate=True, require_approved=False) as asset:
            if asset.sha256!=expected_sha256:
                raise GreetingError('greeting_corrupt')
        with self._write_lock() as directory:
            record, wav = self._read(directory)
            if record['sha256']!=expected_sha256:
                raise GreetingError('greeting_corrupt')
            record['approved'] = True
            self._commit(directory, record, wav, None)
        return dict(sha256=expected_sha256, fingerprint=self.spec.fingerprint,
                    approved=True, simulated=record['simulated'])

    def prepare(self, *, live_opt_in=False, fish_synthesizer=None, api_key=None,
                timeout=30.0, cancel_check=None):
        """Only explicit live preparation may synthesize. Never plays or approves."""
        if live_opt_in is not True:
            raise GreetingError('greeting_prepare_denied')
        _check(cancel_check)
        raw = None
        try:
            synthesize = fish_synthesizer or fish_tts.synthesize_wav
            credentials = {} if api_key is None else {'api_key': api_key}
            raw = Path(synthesize(self.spec.spoken, model=self.spec.fish_model,
                reference_id=self.spec.fish_voice_id, timeout=timeout,
                cancel_check=lambda: _check(cancel_check), **credentials))
            _check(cancel_check)
            return self.publish(raw, approved=False, simulated=False, cancel_check=cancel_check)
        except (TurnCancelled, KeyboardInterrupt):
            raise
        except GreetingError:
            raise
        except Exception:
            raise GreetingError('greeting_synthesis_failed') from None
        finally:
            if raw is not None:audio._discard(raw, suppress=True)


def make_synthetic_greeting_cache(directory, config):
    """Explicit test/simulate helper. Caller supplies an owned temporary directory."""
    from .integration_voice import synthetic_wav
    cache = GreetingCache(directory, GreetingSpec.from_config(config))
    raw = synthetic_wav()
    try:
        cache.publish(raw, approved=True, simulated=True)
    finally:
        audio._discard(raw, suppress=True)
    return cache
