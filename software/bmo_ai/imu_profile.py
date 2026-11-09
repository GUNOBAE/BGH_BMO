"""Private, repeated real-input evidence for relative MPU6050 reactions.

This tool never opens a device or authorizes motor control. Numeric checks and
an operator's completed physical trials establish only an axis-specific change
signal, never sensor health, orientation, fall detection or a safe arm pose.
WHO_AM_I and I2C address identify a device type/address, not a unique sensor.
After a known sensor, wiring or mounting replacement, disable reactions/stop the
app, explicitly clear the profile and repeat the real trials before re-enabling.
"""
from collections import Counter
from copy import deepcopy
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import statistics
from threading import RLock

from .daily_preferences import _open_directory, _unique
from .private_credentials import CredentialError, _close, _private_file


AXES = ('AX', 'AY', 'AZ', 'GX', 'GY', 'GZ')
TRIAL_KINDS = ('stationary', 'move', 'still', 'fan', 'speaker', 'arms')
MAX_BYTES = 512 * 1024
MAX_PROFILE_BYTES = 16 * 1024
MAX_OBSERVATIONS = 4096
MAX_TRIALS = 48
PROFILE_FILE = 'relative-profile.json'
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9:_-]{1,128}\Z')
_REASONS = frozenset(('missing', 'saturated', 'fixed', 'acceleration_bias', 'insufficient_movement',
                      'vibration_not_separated', 'noise_too_large'))


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _binding(value):
    required={'firmware_sha256', 'helper_sha256', 'sensor_sha256', 'boot_sha256', 'source_id', 'sensor_id'}
    if type(value) is not dict or set(value)!=required:
        raise ValueError('invalid IMU binding')
    if any(type(value[k]) is not str or not _HASH.fullmatch(value[k])
           for k in ('firmware_sha256', 'helper_sha256', 'sensor_sha256', 'boot_sha256')):
        raise ValueError('invalid IMU source hash')
    if any(type(value[k]) is not str or not _ID.fullmatch(value[k]) for k in ('source_id', 'sensor_id')):
        raise ValueError('invalid IMU source identity')
    if any(token in value[k].lower() for k in ('source_id', 'sensor_id')
           for token in ('synthetic', 'simulation', 'demo')):
        raise ValueError('synthetic source is not physical evidence')
    return dict(value)


def _observation(value, binding):
    keys = {'controller_boot', 'sequence', 'timestamp_ms', 'status', 'axes', 'accel_fs', 'gyro_fs', 'sensor_id'}
    if type(value) is not dict or set(value) != keys:
        raise ValueError('invalid IMU observation schema')
    if (type(value['controller_boot']) is not str or not _ID.fullmatch(value['controller_boot'])
            or type(value['sequence']) is not int or not 0 <= value['sequence'] <= 0xffffffff
            or type(value['timestamp_ms']) is not int or not 0 <= value['timestamp_ms'] <= 0xffffffff
            or value['status'] not in ('ok', 'READY') or value['sensor_id'] != binding['sensor_id']
            or type(value['accel_fs']) is not int or value['accel_fs'] != 2
            or type(value['gyro_fs']) is not int or value['gyro_fs'] != 250
            or type(value['axes']) is not dict or set(value['axes']) != set(AXES)):
        raise ValueError('invalid or unavailable real IMU observation')
    if any(raw is not None and (type(raw) is not int or not -32768 <= raw <= 32767)
           for raw in value['axes'].values()):
        raise ValueError('invalid raw IMU axis')
    return deepcopy(value)


def _physical(raw, axis):
    return raw * (2.0 if axis.startswith('A') else 250.0) / 32768


def _dataset(value):
    if (type(value) is not dict or set(value) != {'version', 'source', 'binding', 'trials'}
            or type(value['version']) is not int or value['version'] != 1 or value['source'] != 'real_b1'
            or type(value['trials']) is not list or not 12 <= len(value['trials']) <= MAX_TRIALS):
        raise ValueError('completed real B1 trials required')
    binding = _binding(value['binding'])
    trials, identifiers, observations_seen, total = [], set(), set(), 0
    for trial in value['trials']:
        if (type(trial) is not dict or set(trial) != {'kind', 'trial_id', 'observations'}
                or trial['kind'] not in TRIAL_KINDS or type(trial['trial_id']) is not str
                or not _ID.fullmatch(trial['trial_id']) or trial['trial_id'] in identifiers
                or type(trial['observations']) is not list or not 12 <= len(trial['observations']) <= 512):
            raise ValueError('invalid IMU trial')
        identifiers.add(trial['trial_id'])
        total += len(trial['observations'])
        if total > MAX_OBSERVATIONS:
            raise ValueError('IMU evidence bound exceeded')
        samples = [_observation(item, binding) for item in trial['observations']]
        for sample in samples:
            identity=(sample['controller_boot'],sample['sequence'])
            if identity in observations_seen:
                raise ValueError('a physical observation cannot count as two trials')
            observations_seen.add(identity)
        elapsed = 0
        for previous, current in zip(samples, samples[1:]):
            delta = (current['timestamp_ms'] - previous['timestamp_ms']) & 0xffffffff
            advance = (current['sequence'] - previous['sequence']) & 0xffffffff
            if (current['controller_boot'] != previous['controller_boot'] or not 0 < delta <= 500
                    or not 0 < advance < 0x80000000 or advance > 8):
                raise ValueError('stale, reordered or restarted IMU trial')
            elapsed += delta
        if elapsed < 1000 or elapsed > 120000:
            raise ValueError('IMU trial duration not established')
        trials.append(dict(kind=trial['kind'], trial_id=trial['trial_id'], observations=samples))
    counts = Counter(item['kind'] for item in trials)
    if any(counts[kind] < 2 for kind in TRIAL_KINDS):
        raise ValueError('two real trials of each context required')
    return dict(version=1, source='real_b1', binding=binding, trials=trials)


def build_profile(dataset, *, physical_trials_confirmed=False):
    """Validate repeated relative separation after human physical-trial review.

    Metadata is a provenance receipt, not cryptographic proof of a physical
    capture. A file marked synthetic is always rejected; an operator must review
    the actual source/trials before explicitly confirming an install.
    """
    if physical_trials_confirmed is not True:
        raise ValueError('completed physical trial confirmation required')
    dataset = _dataset(dataset)
    raw = json.dumps(dataset, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('IMU evidence too large')
    usable, excluded = {}, {}
    for axis in AXES:
        all_raw = [sample['axes'][axis] for trial in dataset['trials'] for sample in trial['observations']]
        if any(value is None for value in all_raw):
            excluded[axis] = 'missing';continue
        if any(abs(value) >= .99 * 32768 for value in all_raw):
            excluded[axis] = 'saturated';continue
        if max(all_raw) == min(all_raw):
            excluded[axis] = 'fixed';continue
        peaks = {kind: [] for kind in TRIAL_KINDS}
        baselines = []
        for trial in dataset['trials']:
            values = [_physical(sample['axes'][axis], axis) for sample in trial['observations']]
            baseline = statistics.median(values[:3])
            peaks[trial['kind']].append(max(abs(value-baseline) for value in values))
            if trial['kind'] in ('stationary', 'still'):
                baselines.append(statistics.median(values))
        bias = statistics.median(baselines)
        if axis.startswith('A') and abs(bias) > 1.25:
            excluded[axis] = 'acceleration_bias';continue
        movement = min(peaks['move'])
        noise = max(peak for kind in TRIAL_KINDS if kind != 'move' for peak in peaks[kind])
        minimum = .08 if axis.startswith('A') else 8.
        if movement < minimum:
            excluded[axis] = 'insufficient_movement';continue
        if movement <= 4 * noise:
            excluded[axis] = 'vibration_not_separated';continue
        lower = max(minimum/2, noise*2)
        upper = movement/2
        if lower > upper:
            excluded[axis] = 'noise_too_large';continue
        usable[axis] = dict(threshold=round((lower+upper)/2, 8), noise_peak=round(noise, 8),
            movement_min_peak=round(movement, 8), bias=round(bias, 8),
            unit='g' if axis.startswith('A') else 'dps')
    result = dict(version=1, source='real_b1', binding=dataset['binding'],
        evidence_sha256=hashlib.sha256(raw).hexdigest(), validated_relative_axes=usable,
        excluded_axes=excluded)
    return validate_profile(result)


def validate_profile(value):
    keys = {'version', 'source', 'binding', 'evidence_sha256', 'validated_relative_axes', 'excluded_axes'}
    if (type(value) is not dict or set(value) != keys or type(value['version']) is not int
            or value['version'] != 1 or value['source'] != 'real_b1'
            or type(value['evidence_sha256']) is not str or not _HASH.fullmatch(value['evidence_sha256'])
            or type(value['validated_relative_axes']) is not dict or type(value['excluded_axes']) is not dict):
        raise ValueError('invalid relative IMU profile')
    _binding(value['binding'])
    usable, excluded = value['validated_relative_axes'], value['excluded_axes']
    if set(usable) & set(excluded) or set(usable) | set(excluded) != set(AXES):
        raise ValueError('all axis decisions must be explicit')
    for axis, record in usable.items():
        if (type(record) is not dict or set(record) != {'threshold', 'noise_peak', 'movement_min_peak', 'bias', 'unit'}
                or any(not _finite(record[k]) for k in ('threshold', 'noise_peak', 'movement_min_peak', 'bias'))
                or record['unit'] != ('g' if axis.startswith('A') else 'dps')
                or not 0 <= record['noise_peak'] < record['movement_min_peak']/4
                or not max(.04 if axis.startswith('A') else 4., record['noise_peak']*2) <= record['threshold'] <= record['movement_min_peak']/2
                or abs(record['bias']) > (1.25 if axis.startswith('A') else 250.)
                or record['movement_min_peak'] > (4 if axis.startswith('A') else 500.)):
            raise ValueError('invalid axis relative threshold')
    if any(type(reason) is not str or reason not in _REASONS for reason in excluded.values()):
        raise ValueError('invalid excluded axis reason')
    return deepcopy(value)


class CaptureRecorder:
    """Bounded real-owner samples; never accepts synthetic source tags."""
    def __init__(self, binding):
        self.binding = _binding(binding)
        self.trials, self.current = [], None
        self.total = 0

    def start_trial(self, kind, trial_id=None):
        if trial_id is None:trial_id=str(kind)+'_'+secrets.token_hex(8)
        if (self.current is not None or kind not in TRIAL_KINDS or type(trial_id) is not str
                or not _ID.fullmatch(trial_id) or len(self.trials) >= MAX_TRIALS
                or any(item['trial_id'] == trial_id for item in self.trials)):
            raise ValueError('invalid capture trial')
        self.current = dict(kind=kind, trial_id=trial_id, observations=[])
        return trial_id

    def append(self, observation, binding=None, *, source='real_b1'):
        if self.current is None:
            return False
        if source != 'real_b1' or binding is not None and _binding(binding) != self.binding:
            raise ValueError('capture source mismatch')
        value = _observation(observation, self.binding)
        if self.total >= MAX_OBSERVATIONS or len(self.current['observations']) >= 512:
            raise ValueError('capture bound exceeded')
        self.current['observations'].append(value)
        self.total += 1
        return True

    def finish_trial(self):
        if self.current is None:
            raise ValueError('no current capture trial')
        completed, self.current = self.current, None
        self.trials.append(completed)
        return deepcopy(completed)

    def cancel_trial(self):
        if self.current is None:return False
        self.total-=len(self.current['observations'])
        self.current=None
        return True

    def snapshot(self):
        return dict(recording=self.current is not None,source='real_b1',
            kind=self.current['kind'] if self.current else None,
            trial_id=self.current['trial_id'] if self.current else None,
            completed_trials=len(self.trials),total_observations=self.total,
            current_observations=len(self.current['observations']) if self.current else 0,
            max_observations=MAX_OBSERVATIONS,max_trials=MAX_TRIALS,
            relative_signal_validated=False,physical_health_verified=False,
            usable_for_control=False)

    def dataset(self):
        if self.current is not None:
            raise ValueError('finish capture before export')
        return dict(version=1, source='real_b1', binding=dict(self.binding), trials=deepcopy(self.trials))


def default_directory():
    base = os.environ.get('XDG_CONFIG_HOME')
    return (Path(base) if base else Path.home()/'.config')/'bmo'/'imu'


def _read(root, name=PROFILE_FILE, limit=MAX_PROFILE_BYTES):
    if root is None:return None, None
    fd = None
    try:
        fd = os.open(name, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC, dir_fd=root)
        info = os.fstat(fd);_private_file(info, size_limit=limit)
        raw = os.read(fd, limit+1)
        if len(raw) != info.st_size:raise ValueError('changed IMU file')
        value = json.loads(raw, object_pairs_hook=_unique)
        if name == PROFILE_FILE:value = validate_profile(value)
        return value, (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
    except FileNotFoundError:return None, None
    finally:_close(fd)


class IMUProfileStore:
    def __init__(self, directory=None):
        self._guard, self._value = RLock(), None
        self._enabled, self._code = False, 'imu_profile_unavailable'
        self.directory = None
        root = None
        try:
            path = Path(directory) if directory is not None else default_directory()
            if not path.is_absolute() or '..' in path.parts or '\0' in str(path):raise ValueError('invalid directory')
            self.directory = path
            root = _open_directory(path)
            self._value, identity = _read(root)
            self._enabled = True
            self._code = 'imu_profile_ready' if identity else 'imu_profile_missing'
        except (OSError, CredentialError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):pass
        finally:_close(root)

    def load(self):
        with self._guard:return deepcopy(self._value)

    def snapshot(self):
        with self._guard:
            return dict(enabled=self._enabled, code=self._code, relative_axes=sorted(
                self._value['validated_relative_axes']) if self._value else [], automatic_write=False,
                relative_signal_validated=bool(self._value and self._value['validated_relative_axes']),
                runtime_binding_checked=False,
                physical_health_verified=False, usable_for_control=False)

    def _write(self, value, *, name=PROFILE_FILE, limit=MAX_PROFILE_BYTES, replace_existing=True):
        if not self._enabled:raise ValueError('private IMU store unavailable')
        raw = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()
        if len(raw)>limit:raise ValueError('IMU file too large')
        root=lock=temporary=None
        try:
            root=_open_directory(self.directory, create=True)
            lock=os.open('.imu.lock', os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600,dir_fd=root)
            _private_file(os.fstat(lock),size_limit=0)
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            _, identity=_read(root,name,limit)
            if identity is not None and not replace_existing:
                raise ValueError('named IMU evidence already exists')
            temporary='.imu-'+secrets.token_hex(12)
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=root)
            with os.fdopen(fd,'wb') as output:
                output.write(raw);output.flush();os.fsync(output.fileno())
            _, latest=_read(root,name,limit)
            if latest!=identity:raise ValueError('IMU file changed')
            if identity is None:
                os.link(temporary,name,src_dir_fd=root,dst_dir_fd=root,follow_symlinks=False)
                os.unlink(temporary,dir_fd=root)
            else:os.replace(temporary,name,src_dir_fd=root,dst_dir_fd=root)
            temporary=None;os.fsync(root)
        finally:
            if temporary is not None:
                try:os.unlink(temporary,dir_fd=root)
                except OSError:pass
            _close(lock);_close(root)

    def install(self, dataset, *, physical_trials_confirmed=False):
        profile=build_profile(dataset,physical_trials_confirmed=physical_trials_confirmed)
        if not profile['validated_relative_axes']:raise ValueError('no validated relative axes')
        with self._guard:
            self._write(profile)
            self._value,self._code=profile,'imu_profile_ready'
            return deepcopy(profile)

    def clear(self):
        """Explicitly revoke the private profile; never delete recorded trials.

        A running monitor holds its loaded profile, so stop the app or disable
        IMU reactions before physical replacement, then restart after revocation.
        Malformed JSON may be revoked, but a link/unsafe file is never followed.
        """
        with self._guard:
            if self.directory is None:raise ValueError('private IMU directory unavailable')
            root=lock=target=None
            try:
                root=_open_directory(self.directory)
                if root is not None:
                    lock=os.open('.imu.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600,dir_fd=root)
                    _private_file(os.fstat(lock),size_limit=0)
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    try:
                        target=os.open(PROFILE_FILE,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=root)
                    except FileNotFoundError:pass
                    else:
                        info=os.fstat(target);_private_file(info,size_limit=MAX_PROFILE_BYTES)
                        latest=os.stat(PROFILE_FILE,dir_fd=root,follow_symlinks=False)
                        if (info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size)!=(latest.st_dev,latest.st_ino,latest.st_mtime_ns,latest.st_size):
                            raise ValueError('IMU profile changed')
                        os.unlink(PROFILE_FILE,dir_fd=root);os.fsync(root)
                self._value=None
                self._enabled,self._code=True,'imu_profile_missing'
                return True
            finally:_close(target);_close(lock);_close(root)

    def export_evidence(self, dataset, *, name='real-trials.json'):
        # Export may be incomplete so that diagnostics can preserve a failed
        # trial. Only strict bounded recorder shape is accepted here; approval
        # still requires the complete independent build_profile checks.
        if (type(name) is not str
                or re.fullmatch(r'real-trials(?:-[a-f0-9]{32})?\.json',name) is None):
            raise ValueError('bounded private evidence filename required')
        if (type(dataset) is not dict or set(dataset)!={'version','source','binding','trials'}
                or dataset['version']!=1 or dataset['source']!='real_b1'
                or type(dataset['trials']) is not list or len(dataset['trials'])>MAX_TRIALS):
            raise ValueError('invalid evidence export')
        binding=_binding(dataset['binding']);seen=set();total=0
        for trial in dataset['trials']:
            if (type(trial) is not dict or set(trial)!={'kind','trial_id','observations'}
                    or trial['kind'] not in TRIAL_KINDS or type(trial['trial_id']) is not str
                    or not _ID.fullmatch(trial['trial_id']) or trial['trial_id'] in seen
                    or type(trial['observations']) is not list or len(trial['observations'])>512):
                raise ValueError('invalid evidence trial')
            seen.add(trial['trial_id']);total+=len(trial['observations'])
            if total>MAX_OBSERVATIONS:raise ValueError('evidence bound exceeded')
            for observation in trial['observations']:_observation(observation,binding)
        with self._guard:
            self._write(dataset,name=name,limit=MAX_BYTES,replace_existing=name=='real-trials.json')
        return self.directory/name


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path)
    parser.add_argument('--evidence-file',type=Path)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--install',action='store_true')
    action.add_argument('--clear',action='store_true',help='revoke the private relative profile; preserve real-trial evidence')
    parser.add_argument('--physical-trials-confirmed',action='store_true')
    args=parser.parse_args(argv)
    try:
        store=IMUProfileStore(args.directory)
        if args.clear:
            if args.evidence_file is not None or args.physical_trials_confirmed:
                raise ValueError('profile revocation does not accept evidence or approval')
            store.clear()
        elif args.install:
            if args.evidence_file is None:raise ValueError('evidence required')
            path=args.evidence_file
            if not path.is_absolute() or '..' in path.parts:raise ValueError('private evidence path required')
            directory=_open_directory(path.parent)
            try:
                value,_=_read(directory,path.name,MAX_BYTES)
                if value is None:raise ValueError('evidence missing')
            finally:_close(directory)
            store.install(value,physical_trials_confirmed=args.physical_trials_confirmed)
        elif args.evidence_file is not None or args.physical_trials_confirmed:
            raise ValueError('install required')
        print(json.dumps(dict(status='CLEARED' if args.clear else 'INSTALLED' if args.install else 'READ_ONLY',**store.snapshot(),
            transport_opened=False,firmware_changed=False),sort_keys=True))
        return 0
    except (OSError,CredentialError,ValueError,TypeError,UnicodeError,OverflowError,RecursionError):
        print(json.dumps(dict(status='REJECTED',code='imu_profile_not_installed',
            transport_opened=False,firmware_changed=False)))
        return 2


if __name__=='__main__':raise SystemExit(main())
