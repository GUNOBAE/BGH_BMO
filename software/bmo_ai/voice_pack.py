"""Private, hash-pinned voice-pack import and approval-gated event selection.

This module never generates audio, reads keys or touches the legacy bank. ZIPs
are inspected, not extracted. Only verified PCM snapshots and a canonical index
are staged; HTML/scripts and directions from the archive are never executed.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import argparse
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import wave
import zipfile

from . import audio, fish_tts
from .integration_face import wav_cues
from .private_credentials import _open_directory, _private_file, _close, CredentialError
from .voice_bank import VoiceBankError

V8_ARCHIVE_SHA256 = '9f5ca192a86dedd87a227418802108ee7e13e713b0faf8361e484cd39134cfa0'
MAX_ZIP = 512 * 1024 * 1024
MAX_WAV = 8 * 1024 * 1024
MAX_INDEX = 2 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,79}\Z')
_MODELS = frozenset(('drama-3', 's2.1-pro', 's2.1-pro-free'))
EVENT_CANDIDATES = {
    'photo_ready': ('PH_READY_03',), 'photo_saved': ('PH_REVIEW_02',),
    'photo_retake': ('PH_RETAKE_01',), 'photo_exit': ('PH_EXIT_02',),
    'photo_return_guide': ('PH_RETURN_GUIDE_01',),
    'music_random': ('M01',), 'music_selected': ('M04',), 'music_exit': ('M10',),
    'game_start': ('G01', 'G02'), 'game_exit': ('G03',), 'game_result': ('G04',),
    'error_network': ('E01',), 'error_service': ('E03',), 'error_camera': ('E07',),
    'error_storage': ('E08',), 'error_authentication': ('E10',),
    'error_rate_limit': ('E14',), 'error_playback': ('E15',), 'error_file': ('E16',),
    'reaction_chuckle': ('RX01',), 'reaction_gasp': ('RX02',),
    'reaction_playful_puff': ('RX03',), 'reaction_sob': ('RX04',),
    'goodbye': ('F04', 'F05'), 'utility': ('A01',),
    'idle': ('C02', 'C05', 'C08', 'C11', 'C13', 'C15'),
    # These are separate future assets, not aliases of unrelated existing WAVs.
    'video_ready': (), 'video_stopped': (), 'video_saved': (), 'video_failed': (),
    'tracking_started': (), 'tracking_stopped': (), 'tracking_failed': (),
    'volume_changed': (), 'focus_uncertain': (),
}


def default_voice_pack_directory(*, home=None):
    return (Path.home() if home is None else Path(home)) / '.local/share/bmo/voice-pack-v8'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VoiceBankError('voice_pack_manifest_invalid')
        result[key] = value
    return result


def _archive_name(name):
    if (type(name) is not str or not name or len(name) > 240 or '\\' in name or '\0' in name
            or ':' in name or name.startswith('/') or any(p in ('..', '') for p in name.rstrip('/').split('/'))):
        raise VoiceBankError('voice_pack_archive_unsafe')
    return str(PurePosixPath(name.rstrip('/')))


def _model(value):
    aliases = {'Drama3': 'drama-3', 'drama3': 'drama-3', 'Drama-3': 'drama-3'}
    value = aliases.get(value, value)
    if value not in _MODELS:
        raise VoiceBankError('voice_pack_provenance_required')
    return value


def _pcm(raw):
    if not 44 <= len(raw) <= MAX_WAV:
        raise VoiceBankError('voice_pack_audio_invalid')
    try:
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            rate, frames, width, channels = wav.getframerate(), wav.getnframes(), wav.getsampwidth(), wav.getnchannels()
            if (wav.getcomptype() != 'NONE' or width not in (1, 2, 3, 4)
                    or channels not in (1, 2) or not 8000 <= rate <= 96000
                    or not .05 <= frames/rate <= 30
                    or len(wav.readframes(frames+1)) != frames*width*channels):
                raise ValueError()
            return dict(sample_rate=rate, sample_width=width, channels=channels,
                        frames=frames, duration_seconds=frames/rate)
    except (wave.Error, EOFError, ValueError, ZeroDivisionError):
        raise VoiceBankError('voice_pack_audio_invalid') from None


@dataclass(slots=True)
class PackAsset:
    path: Path
    candidate_id: str
    clip_id: str
    text: str
    source_model: str
    baked_gain: float
    sha256: str
    info: dict
    cues: tuple

    def close(self):
        audio._discard(self.path, suppress=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class OrganizedVoicePack:
    def __init__(self, directory, *, chooser=None):
        self.directory = Path(directory)
        if not self.directory.is_absolute() or '..' in self.directory.parts or '\0' in str(self.directory):
            raise VoiceBankError('voice_pack_storage_unsafe')
        self.chooser = chooser or (lambda values: values[0])

    @contextmanager
    def _root(self, *, create=False, lock=False):
        root = fd = None
        try:
            root = _open_directory(self.directory, create=create)
            if root is None:
                raise VoiceBankError('voice_pack_asset_pending')
            if lock:
                fd = os.open('.pack.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600, dir_fd=root)
                _private_file(os.fstat(fd), size_limit=0)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    raise VoiceBankError('voice_pack_busy') from None
            yield root
        except (OSError, CredentialError):
            raise VoiceBankError('voice_pack_storage_unsafe') from None
        finally:
            _close(fd); _close(root)

    def _read(self, root, name, *, optional=False):
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC, dir_fd=root)
            _private_file(os.fstat(fd), size_limit=MAX_INDEX)
            raw = os.read(fd, MAX_INDEX+1)
            if len(raw) != os.fstat(fd).st_size:
                raise VoiceBankError('voice_pack_manifest_invalid')
            return json.loads(raw, object_pairs_hook=_unique)
        except FileNotFoundError:
            if optional:
                return None
            raise VoiceBankError('voice_pack_asset_pending') from None
        except (ValueError, UnicodeError):
            raise VoiceBankError('voice_pack_manifest_invalid') from None
        finally:
            _close(fd)

    def _write(self, root, name, raw, *, replace=False):
        stage = '.import-'+secrets.token_hex(12)
        try:
            fd = os.open(stage, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600, dir_fd=root)
            with os.fdopen(fd, 'wb') as out:
                out.write(raw);out.flush();os.fsync(out.fileno())
            if replace:
                os.replace(stage, name, src_dir_fd=root, dst_dir_fd=root)
            else:
                os.link(stage, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
            os.fsync(root)
        finally:
            try:
                os.unlink(stage, dir_fd=root)
            except FileNotFoundError:
                pass

    def import_zip(self, source, *, expected_sha256=V8_ARCHIVE_SHA256, source_model=None):
        """Hash-check a local archive, publish without overwriting another pack.

        Catalogue records support `clips`/`candidates` with a `files` list of
        {path,sha256,baked_gain}; explicit raw_path/raw_sha256 and
        preview_path/preview_sha256 records are also accepted. Missing source
        provenance is an actionable failure, never an assumed live model.
        """
        if type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
            raise VoiceBankError('voice_pack_hash_required')
        fd = None
        try:
            fd = os.open(source, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or before.st_nlink != 1 or not 0 < before.st_size <= MAX_ZIP):
                raise VoiceBankError('voice_pack_archive_unsafe')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != expected_sha256:
                raise VoiceBankError('voice_pack_hash_mismatch')
            with os.fdopen(os.dup(fd), 'rb') as stream, zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                names = {}
                if len(entries) > 1024 or sum(e.file_size for e in entries) > MAX_ZIP:
                    raise VoiceBankError('voice_pack_archive_limit')
                for entry in entries:
                    name = _archive_name(entry.filename)
                    if name in names or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                        raise VoiceBankError('voice_pack_archive_unsafe')
                    if entry.file_size > MAX_WAV and not entry.is_dir():
                        raise VoiceBankError('voice_pack_archive_limit')
                    names[name] = entry
                catalogue_names = [n for n in names if PurePosixPath(n).name == 'catalogue.json']
                if len(catalogue_names) != 1:
                    raise VoiceBankError('voice_pack_manifest_invalid')
                catalogue_path = catalogue_names[0]
                if names[catalogue_path].file_size > MAX_INDEX:
                    raise VoiceBankError('voice_pack_manifest_invalid')
                catalogue = json.loads(archive.read(names[catalogue_path]), object_pairs_hook=_unique)
                if type(catalogue) not in (list, dict):
                    raise VoiceBankError('voice_pack_manifest_invalid')
                rows = catalogue if type(catalogue) is list else catalogue.get('clips', catalogue.get('candidates', catalogue.get('items')))
                if type(rows) is not list or not 1 <= len(rows) <= 224:
                    raise VoiceBankError('voice_pack_manifest_invalid')
                defaults = catalogue if type(catalogue) is dict else {}
                normalized = []
                seen = set()
                with self._root(create=True, lock=True) as root:
                    existing = self._read(root, 'pack.json', optional=True)
                    if existing is not None:
                        if existing.get('archive_sha256') == digest:
                            return self.snapshot()
                        raise VoiceBankError('voice_pack_already_imported')
                    for row in rows:
                        if type(row) is not dict:
                            raise VoiceBankError('voice_pack_manifest_invalid')
                        clip_id = row.get('clip_id', row.get('id'))
                        candidate = row.get('candidate_id', row.get('id', clip_id))
                        take = row.get('take', row.get('variant'))
                        if candidate == clip_id and take in ('A', 'B'):
                            candidate += '_'+take
                        text = row.get('text', row.get('sentence', row.get('script')))
                        if (type(clip_id) is not str or not _ID.fullmatch(clip_id)
                                or type(candidate) is not str or not _ID.fullmatch(candidate) or candidate in seen
                                or type(text) is not str or not 0 < len(text) <= 1024):
                            raise VoiceBankError('voice_pack_manifest_invalid')
                        seen.add(candidate)
                        provenance = row.get('provenance', {})
                        if type(provenance) is not dict:
                            raise VoiceBankError('voice_pack_provenance_required')
                        model = _model(row.get('source_model', row.get('model', provenance.get('model', defaults.get('model', source_model)))))
                        voice_id = row.get('voice_id', provenance.get('voice_id', defaults.get('voice_id')))
                        if voice_id != fish_tts.AARON_BMO_VOICE_ID:
                            raise VoiceBankError('voice_pack_voice_mismatch')
                        files = row.get('files')
                        if type(files) is dict:
                            files = [dict(value, path=value.get('path', key)) for key, value in files.items() if type(value) is dict]
                        if files is None:
                            files = []
                            for prefix, gain in (('raw', 1.), ('preview', .25)):
                                path = row.get(prefix+'_path', row.get(prefix+'_file'))
                                if path is not None:
                                    files.append(dict(path=path, sha256=row.get(prefix+'_sha256'), baked_gain=gain))
                        if type(files) is not list or not 1 <= len(files) <= 2:
                            raise VoiceBankError('voice_pack_manifest_invalid')
                        variants = {}
                        for item in files:
                            if type(item) is not dict:
                                raise VoiceBankError('voice_pack_manifest_invalid')
                            path = _archive_name(item.get('path', item.get('filename')))
                            digest_wav = item.get('sha256')
                            gain = item.get('baked_gain')
                            if gain is None:
                                gain = .25 if 'LEGACY_PREVIEW_GAIN025' in PurePosixPath(path).parts else 1. if 'RAW' in PurePosixPath(path).parts else None
                            if (type(gain) not in (int, float) or gain not in (.25, 1.)
                                    or type(digest_wav) is not str or not _SHA.fullmatch(digest_wav)):
                                raise VoiceBankError('voice_pack_provenance_required')
                            if path not in names:
                                parent = str(PurePosixPath(catalogue_path).parent)
                                path = _archive_name(parent+'/'+path) if parent != '.' else path
                            if path not in names or not path.lower().endswith('.wav'):
                                raise VoiceBankError('voice_pack_audio_missing')
                            raw = archive.read(names[path])
                            if hashlib.sha256(raw).hexdigest() != digest_wav:
                                raise VoiceBankError('voice_pack_audio_hash_mismatch')
                            info = _pcm(raw)
                            variant = 'raw' if gain == 1. else 'preview_gain025'
                            if variant in variants:
                                raise VoiceBankError('voice_pack_manifest_invalid')
                            name = digest_wav+'.wav'
                            try:
                                self._write(root, name, raw)
                            except FileExistsError:
                                self._payload(root, name, digest_wav)
                            variants[variant] = dict(sha256=digest_wav, baked_gain=gain, file=name, **info)
                        explicit_events = row.get('events', [])
                        if (type(explicit_events) is not list or any(e not in EVENT_CANDIDATES for e in explicit_events)):
                            raise VoiceBankError('voice_pack_manifest_invalid')
                        normalized.append(dict(id=candidate, clip_id=clip_id, text=text, source_model=model,
                            voice_id=voice_id, name_mode='beemo', variants=variants, events=explicit_events,
                            job_id=str(row.get('job_id', ''))[:128], cue_review='UNRESOLVED'))
                    record = dict(version=8, archive_sha256=digest, candidates=normalized,
                                  individual_listening_approval=False, boot_override=False)
                    after = os.fstat(fd)
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise VoiceBankError('voice_pack_archive_changed')
                    self._write(root, 'pack.json', _json(record))
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise VoiceBankError('voice_pack_archive_changed')
            return self.snapshot()
        except VoiceBankError:
            raise
        except (OSError, zipfile.BadZipFile, ValueError, TypeError, KeyError, UnicodeError):
            raise VoiceBankError('voice_pack_import_failed') from None
        finally:
            _close(fd)

    def _index(self, root):
        record = self._read(root, 'pack.json')
        if (type(record) is not dict or record.get('version') != 8 or not _SHA.fullmatch(str(record.get('archive_sha256')))
                or type(record.get('candidates')) is not list or not 1 <= len(record['candidates']) <= 224):
            raise VoiceBankError('voice_pack_manifest_invalid')
        seen = set()
        for row in record['candidates']:
            if (type(row) is not dict or type(row.get('id')) is not str or not _ID.fullmatch(row['id'])
                    or row['id'] in seen or type(row.get('clip_id')) is not str or not _ID.fullmatch(row['clip_id'])
                    or type(row.get('text')) is not str or not 0 < len(row['text']) <= 1024
                    or row.get('source_model') not in _MODELS or row.get('voice_id') != fish_tts.AARON_BMO_VOICE_ID
                    or row.get('name_mode') != 'beemo' or row.get('cue_review') != 'UNRESOLVED'
                    or type(row.get('events')) is not list or any(e not in EVENT_CANDIDATES for e in row['events'])
                    or type(row.get('variants')) is not dict or not 1 <= len(row['variants']) <= 2):
                raise VoiceBankError('voice_pack_manifest_invalid')
            seen.add(row['id'])
            for name, variant in row['variants'].items():
                if (name not in ('raw', 'preview_gain025') or type(variant) is not dict
                        or type(variant.get('sha256')) is not str or not _SHA.fullmatch(variant['sha256'])
                        or variant.get('file') != variant['sha256']+'.wav'
                        or type(variant.get('baked_gain')) not in (int, float)
                        or variant['baked_gain'] != (1. if name == 'raw' else .25)
                        or type(variant.get('duration_seconds')) not in (int, float)
                        or not math.isfinite(variant['duration_seconds']) or not .05 <= variant['duration_seconds'] <= 30):
                    raise VoiceBankError('voice_pack_manifest_invalid')
        return record

    def _mapping(self, root, name):
        value = self._read(root, name, optional=True) or {}
        if (type(value) is not dict or len(value) > 448
                or any(type(k) is not str or len(k) > 100 or type(v) is not str or len(v) > 80 for k, v in value.items())):
            raise VoiceBankError('voice_pack_manifest_invalid')
        return value

    def _approvals(self, root):
        value = self._read(root, 'approvals.json', optional=True)
        value = {} if value is None else value
        if (type(value) is not dict or len(value) > 448
                or any(type(key) is not str or len(key) > 100 or type(item) is not dict
                    or set(item) != {'sha256', 'provenance'}
                    or any(type(v) is not str or not _SHA.fullmatch(v) for v in item.values())
                    for key, item in value.items())):
            raise VoiceBankError('voice_pack_manifest_invalid')
        return value

    @staticmethod
    def _provenance(row, variant, archive_sha256):
        value = row['variants'][variant]
        return hashlib.sha256(_json(dict(archive_sha256=archive_sha256,
            candidate_id=row['id'], clip_id=row['clip_id'], text=row['text'],
            source_model=row['source_model'], voice_id=row['voice_id'],
            name_mode=row['name_mode'], baked_gain=value['baked_gain'],
            sha256=value['sha256'], cue_algorithm='original_bridge_30fps_v1'))).hexdigest()

    def _approved(self, approvals, row, variant, archive_sha256):
        return approvals.get(row['id']+':'+variant) == dict(
            sha256=row['variants'][variant]['sha256'],
            provenance=self._provenance(row, variant, archive_sha256))

    def _payload(self, root, name, digest):
        if name != digest+'.wav' or not _SHA.fullmatch(digest):
            raise VoiceBankError('voice_pack_audio_invalid')
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC, dir_fd=root)
            _private_file(os.fstat(fd), size_limit=MAX_WAV)
            raw = os.read(fd, MAX_WAV+1)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise VoiceBankError('voice_pack_audio_hash_mismatch')
            _pcm(raw)
            return raw
        finally:
            _close(fd)

    def inventory(self):
        try:
            with self._root() as root:
                record = self._index(root)
                approvals = self._approvals(root)
                selection = self._mapping(root, 'selection.json')
                values = []
                for row in record['candidates']:
                    variants = {name: dict(sha256=value['sha256'], baked_gain=value['baked_gain'],
                        duration_seconds=value['duration_seconds'], approved=self._approved(approvals, row, name, record['archive_sha256']))
                        for name, value in row['variants'].items()}
                    values.append(dict(id=row['id'], clip_id=row['clip_id'], text=row['text'],
                        source_model=row['source_model'], variants=variants,
                        approved=any(v['approved'] for v in variants.values()), cue_review=row['cue_review'],
                        events=[event for event in EVENT_CANDIDATES if self._matches(row, event)],
                        selected_events=[event for event, identifier in selection.items() if identifier == row['id']]))
                return values
        except VoiceBankError as error:
            if error.code == 'voice_pack_asset_pending':
                return []
            raise

    def snapshot(self):
        values = self.inventory()
        digest = None
        selection = {}
        if values:
            with self._root() as root:
                digest = self._index(root)['archive_sha256']
                selection = self._mapping(root, 'selection.json')
        return dict(version=8, status='IMPORTED' if values else 'ASSET_PENDING', candidates=len(values),
                    approved=sum(v['approved'] for v in values), generated=False, automatic_approval=False,
                    live_model_changed=False, wake_ack_available=False, startup_override=False,
                    archive_sha256=digest, expected_v8_sha256=V8_ARCHIVE_SHA256,
                    event_names=list(EVENT_CANDIDATES), selected_events=selection)

    def approve(self, candidate_id, sha256, *, listening_approved=False):
        if listening_approved is not True or type(sha256) is not str or not _SHA.fullmatch(sha256):
            raise VoiceBankError('voice_pack_listening_approval_required')
        with self._root(lock=True) as root:
            record = self._index(root)
            rows = record['candidates']
            row = next((r for r in rows if r['id'] == candidate_id), None)
            if row is None:
                raise VoiceBankError('voice_pack_clip_invalid')
            variant = next((name for name, item in row['variants'].items() if item['sha256'] == sha256), None)
            if variant is None:
                raise VoiceBankError('voice_pack_sha_mismatch')
            self._payload(root, row['variants'][variant]['file'], sha256)
            approvals = self._approvals(root)
            approvals[candidate_id+':'+variant] = dict(sha256=sha256,
                provenance=self._provenance(row, variant, record['archive_sha256']))
            self._write(root, 'approvals.json', _json(approvals), replace=True)
        return dict(id=candidate_id, sha256=sha256, approved=True)

    def select_event(self, event, candidate_id=None):
        if event not in EVENT_CANDIDATES:
            raise VoiceBankError('voice_pack_event_invalid')
        with self._root() as root:
            record = self._index(root)
            approvals = self._approvals(root)
            chosen = self._mapping(root, 'selection.json')
            if candidate_id is not None:
                row = next((r for r in record['candidates'] if r['id'] == candidate_id), None)
                if row is None or not self._matches(row, event):
                    raise VoiceBankError('voice_pack_event_mismatch')
                # Selection is allowed before listening approval; playback is not.
                with self._root(lock=True) as locked:
                    chosen[event] = candidate_id
                    self._write(locked, 'selection.json', _json(chosen), replace=True)
            candidates = [row for row in record['candidates'] if self._matches(row, event)
                          and (event not in chosen or chosen[event] == row['id'])
                          and any(self._approved(approvals, row, name, record['archive_sha256']) for name in row['variants'])]
            return self.chooser([r['id'] for r in candidates]) if candidates else None

    @staticmethod
    def _matches(row, event):
        return event in row['events'] or any(row['clip_id'] == clip or row['clip_id'] in (clip+'_A', clip+'_B') for clip in EVENT_CANDIDATES[event])

    def load_event(self, event, *, cancel_check=None):
        if cancel_check is not None:
            cancel_check()
        candidate = self.select_event(event)
        if candidate is None:
            return None
        with self._root() as root:
            record = self._index(root)
            row = next(r for r in record['candidates'] if r['id'] == candidate)
            approvals = self._approvals(root)
            variant = next((row['variants'][name] for name in ('preview_gain025', 'raw')
                            if name in row['variants'] and self._approved(approvals, row, name, record['archive_sha256'])), None)
            if variant is None:
                raise VoiceBankError('voice_pack_listening_approval_required')
            raw = self._payload(root, variant['file'], variant['sha256'])
        output = audio._new_wav(audio.AudioPlaybackError)
        try:
            output.write_bytes(raw)
            info = audio._wav_info(output)
            cues = tuple(dict(cue) for cue in wav_cues(output))
            if cancel_check is not None:
                cancel_check()
            return PackAsset(output, candidate, row['clip_id'], row['text'], row['source_model'],
                             variant['baked_gain'], variant['sha256'], info, cues)
        except BaseException:
            audio._discard(output, suppress=True)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='Import/review private BMO voice assets; never generates audio.')
    parser.add_argument('--root', default=str(default_voice_pack_directory()))
    commands = parser.add_subparsers(dest='command', required=True)
    imp = commands.add_parser('import')
    imp.add_argument('archive');imp.add_argument('--sha256', default=V8_ARCHIVE_SHA256)
    imp.add_argument('--source-model', choices=sorted(_MODELS))
    commands.add_parser('list')
    approve = commands.add_parser('approve');approve.add_argument('candidate_id');approve.add_argument('sha256')
    approve.add_argument('--listening-approved', action='store_true')
    select = commands.add_parser('select');select.add_argument('event', choices=sorted(EVENT_CANDIDATES));select.add_argument('candidate_id')
    args = parser.parse_args(argv)
    pack = OrganizedVoicePack(args.root)
    try:
        if args.command == 'import':
            result = pack.import_zip(args.archive, expected_sha256=args.sha256, source_model=args.source_model)
        elif args.command == 'list':
            result = dict(snapshot=pack.snapshot(), inventory=pack.inventory())
        elif args.command == 'approve':
            result = pack.approve(args.candidate_id, args.sha256, listening_approved=args.listening_approved)
        else:
            result = dict(event=args.event, playable_candidate=pack.select_event(args.event, args.candidate_id))
        print(json.dumps(result, ensure_ascii=False));return 0
    except (VoiceBankError, audio.AudioError) as error:
        print(json.dumps(dict(status='BLOCKED', code=error.code)));return 2


if __name__ == '__main__':
    raise SystemExit(main())
