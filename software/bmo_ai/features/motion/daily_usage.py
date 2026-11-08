"""User preferences and a historical B1 commissioning record, never a deadman.

A saved preference cannot manufacture a first physical test. An operator may
record that completed test separately; current board bytes, USB identity, HELLO
and STOP state are checked again by the existing adapter on the next request.
No constructor opens a port, writes firmware or releases a latched stop.
"""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from ..contracts import FeatureError
from .arm_serial_posix_v2 import DEVICE, MOUNT, OwnedCdcDataPort, verified_cdc_identity
from .legacy_host import LegacyArmError, LegacyArmLink, LegacyMotionFeature, validate_legacy_install

VERSION = 1
CALIBRATION = 'b1-gp10-gp11-600-2400-50hz-original-v1'
CHECKS = ('motion_observed', 'stop_verified', 'gp7_verified', 'watchdog_verified', 'physical_cutoff_verified')
_ROOT = Path(__file__).resolve().parents[4] / 'firmware/staging/bmo_arm_legacy_bridge_v1'
_HASH = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_ID = re.compile(r'[A-Za-z0-9_.:-]{1,96}\Z', re.ASCII)
_LIMIT = 8192


class ArmUsageError(FeatureError):
    pass


def default_directory():
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))
    if not base.is_absolute():
        raise ArmUsageError('arm_settings_path_invalid')
    return base/'bmo/arms'


def _private_path(directory, *, create=False):
    path = Path(directory).absolute()
    if '..' in path.parts or path.resolve() != path:
        raise ArmUsageError('arm_settings_path_invalid')
    if any((parent/'.git').exists() for parent in (path, *path.parents)):
        raise ArmUsageError('arm_settings_private_directory_required')
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ArmUsageError('arm_settings_private_directory_required')
    return path


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or not 0 < info.st_size <= _LIMIT):
            raise ArmUsageError('arm_settings_file_invalid')
        raw = os.read(fd, _LIMIT+1)
        if len(raw) != info.st_size:
            raise ArmUsageError('arm_settings_file_invalid')
        return raw
    finally:
        os.close(fd)


def _identity(value):
    keys = {'device', 'interface', 'label', 'product', 'vendor_id', 'product_id', 'serial'}
    if (type(value) is not dict or set(value) != keys or value.get('device') != str(DEVICE)
            or value.get('interface') != '02'
            or any(type(v) is not str or not 0 < len(v) <= 128 or any(ord(c) < 32 or ord(c) > 126 for c in v)
                   for v in value.values())
            or not value['label'].startswith('CircuitPython CDC2')
            or 'rp2040' not in value['product'].lower()
            or re.fullmatch('[a-fA-F0-9]{4}', value['vendor_id']) is None
            or re.fullmatch('[a-fA-F0-9]{4}', value['product_id']) is None):
        raise ArmUsageError('arm_device_evidence_invalid')
    return dict(value)


def current_device_identity():
    """Read only the already observed dedicated CDC2 interface; no discovery."""
    label = verified_cdc_identity()
    try:
        interface = (Path('/sys/class/tty')/DEVICE.name/'device').resolve(strict=True)
        usb = interface.parent
        def field(path):
            with path.open('rb') as stream:
                data = stream.read(129)
            if len(data) > 128:
                raise ArmUsageError('arm_device_evidence_invalid')
            return data.decode('ascii').strip()
        return _identity(dict(device=str(DEVICE), interface='02', label=label,
            product=field(usb/'product'), vendor_id=field(usb/'idVendor'),
            product_id=field(usb/'idProduct'), serial=field(usb/'serial')))
    except (OSError, UnicodeError, ValueError):
        raise ArmUsageError('arm_device_evidence_unavailable') from None


def _hash_files(mount):
    import hashlib
    hashes = {}
    for name in ('code.py', 'bmo_arm_bridge.py', 'boot.py'):
        path = Path(mount)/name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 128*1024:
                raise ArmUsageError('arm_firmware_evidence_invalid')
            data = os.read(fd, 128*1024+1)
            if len(data) != info.st_size:
                raise ArmUsageError('arm_firmware_evidence_invalid')
            hashes[name] = hashlib.sha256(data).hexdigest()
        finally:
            os.close(fd)
    return hashes


def _record(value):
    if value is None:
        return None
    keys = {'reviewed_code_sha256', 'board_files_sha256', 'device_identity', 'calibration',
            'checks', 'test_session_id', 'recorded_at', 'evidence'}
    if (type(value) is not dict or set(value) != keys or value.get('calibration') != CALIBRATION
            or value.get('evidence') != 'operator_recorded_historical'
            or type(value.get('reviewed_code_sha256')) is not str or not _HASH.fullmatch(value['reviewed_code_sha256'])
            or type(value.get('test_session_id')) is not str or not _ID.fullmatch(value['test_session_id'])
            or type(value.get('recorded_at')) is not int or value['recorded_at'] <= 0
            or value.get('checks') != list(CHECKS)):
        raise ArmUsageError('arm_commissioning_record_invalid')
    hashes = value.get('board_files_sha256')
    if (type(hashes) is not dict or set(hashes) != {'code.py','bmo_arm_bridge.py','boot.py'}
            or any(type(v) is not str or not _HASH.fullmatch(v) for v in hashes.values())
            or hashes['code.py'] != value['reviewed_code_sha256']):
        raise ArmUsageError('arm_commissioning_record_invalid')
    _identity(value['device_identity'])
    return dict(value)


class ArmUsageStore:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else default_directory()

    def load(self):
        directory = _private_path(self.directory)
        try:
            value = json.loads(_read(directory/'daily.json'))
        except FileNotFoundError:
            return dict(version=VERSION, enabled=False, conversation_gestures=False, commissioning=None)
        except (OSError, UnicodeError, ValueError):
            raise ArmUsageError('arm_settings_unavailable') from None
        if (type(value) is not dict or set(value) != {'version','enabled','conversation_gestures','commissioning'}
                or type(value.get('version')) is not int or value['version'] != VERSION
                or type(value.get('enabled')) is not bool or type(value.get('conversation_gestures')) is not bool):
            raise ArmUsageError('arm_settings_invalid')
        _record(value['commissioning'])
        return value

    def _save(self, value):
        directory = _private_path(self.directory, create=True)
        raw = (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()
        if len(raw) > _LIMIT:
            raise ArmUsageError('arm_settings_invalid')
        fd, name = tempfile.mkstemp(prefix='.daily-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            # Validate any existing target before replacement; never overwrite a
            # symlink/public/unrelated malformed file or a changed receipt.
            self.load()
            os.replace(name, directory/'daily.json')
            dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        finally:
            try: os.unlink(name)
            except FileNotFoundError: pass
        return value

    def preferences(self, value):
        if type(value) is not dict or set(value) != {'enabled', 'conversation_gestures'} or any(type(v) is not bool for v in value.values()):
            raise ArmUsageError('arm_preferences_invalid')
        current = self.load()
        current.update(value)
        return self._save(current)

    def commission(self, *, reviewed_sha, test_session_id, checks,
                   firmware_root=_ROOT, mount=MOUNT, identity_reader=current_device_identity):
        """Record a human-observed completed test; never run the test here."""
        if (type(checks) is not dict or set(checks) != set(CHECKS)
                or any(value is not True for value in checks.values())
                or type(test_session_id) is not str or not _ID.fullmatch(test_session_id)):
            raise ArmUsageError('arm_completed_supervised_test_required')
        validate_legacy_install(firmware_root, reviewed_sha, mount=mount)
        hashes = _hash_files(mount)
        if hashes['code.py'] != reviewed_sha:
            raise ArmUsageError('arm_firmware_changed')
        record = dict(reviewed_code_sha256=reviewed_sha, board_files_sha256=hashes,
            device_identity=_identity(identity_reader()), calibration=CALIBRATION, checks=list(CHECKS),
            test_session_id=test_session_id, recorded_at=int(time.time()), evidence='operator_recorded_historical')
        _record(record)
        current = self.load()
        current['commissioning'] = record
        self._save(current)
        return record


class _DailyPort:
    """One lazy admission attempt per app, no reconnect or latch release."""
    def __init__(self, record, *, port_factory=OwnedCdcDataPort,
                 identity_reader=current_device_identity, firmware_root=_ROOT, mount=MOUNT):
        self.record, self.port_factory = record, port_factory
        self.identity_reader, self.firmware_root, self.mount = identity_reader, firmware_root, mount
        self.port = None
        self.attempted = self.closed = False
        self.code = 'arm_connection_not_requested'

    def _open(self):
        if self.closed or self.attempted:
            raise LegacyArmError('daily_connection_unavailable')
        self.attempted = True
        try:
            validate_legacy_install(self.firmware_root, self.record['reviewed_code_sha256'], mount=self.mount)
            if _hash_files(self.mount) != self.record['board_files_sha256']:
                raise ArmUsageError('arm_firmware_changed')
            if self.identity_reader() != self.record['device_identity']:
                raise ArmUsageError('arm_device_changed')
            self.port = self.port_factory()
            self.port.open()
            self.code = 'arm_connection_open'
        except Exception as error:
            code = getattr(error, 'code', None)
            self.code = code if type(code) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,95}', code) else 'arm_connection_failed'
            if self.port is not None:
                try: self.port.close()
                except Exception: self.code = 'arm_cleanup_unverified'
            raise LegacyArmError('daily_admission_failed') from None

    def write(self, data):
        if self.port is None:
            self._open()
        if self.closed:
            raise LegacyArmError('daily_connection_unavailable')
        return self.port.write(data)

    def read_until(self, delimiter, bound):
        if self.port is None or self.closed:
            raise LegacyArmError('daily_connection_unavailable')
        return self.port.read_until(delimiter, bound)

    def close(self):
        if self.port is not None:
            self.port.close()
        self.closed = True


class DailyArmController:
    def __init__(self, directory=None, *, runtime_ready=None, clock=time.monotonic,
                 store=None, port_factory=OwnedCdcDataPort, identity_reader=current_device_identity,
                 firmware_root=_ROOT, mount=MOUNT):
        self.store = store
        self.ready = runtime_ready or (lambda: False)
        self._fault = None
        try:
            self.store = self.store or ArmUsageStore(directory)
            self._value = self.store.load()
        except (FeatureError, OSError, TypeError, ValueError):
            self._value = dict(enabled=False, conversation_gestures=False, commissioning=None)
            self._fault = 'arm_settings_unavailable'
        self.motion = self.port = None
        record = self._value.get('commissioning')
        if record is not None:
            self.port = _DailyPort(record, port_factory=port_factory, identity_reader=identity_reader,
                                   firmware_root=firmware_root, mount=mount)
            link = LegacyArmLink(self.port, motor_permission=self._permission,
                                 permission_code="daily_arm_permission_required")
            self.motion = LegacyMotionFeature(link, clock=clock)

    @property
    def enabled(self): return self._value['enabled']

    @property
    def conversation_gestures(self): return self._value['conversation_gestures']

    def _permission(self):
        return self.enabled and self._fault is None and self.ready() is True

    def configure(self, value):
        if self._fault:
            raise ArmUsageError(self._fault)
        if (type(value) is not dict or set(value) != {'enabled', 'conversation_gestures'}
                or any(type(v) is not bool for v in value.values())):
            raise ArmUsageError('arm_preferences_invalid')
        # OFF is immediate even when a full disk prevents persisting preferences.
        # A later ON never re-creates a link or clears a STOP/GP7/watchdog latch.
        if self.enabled and not value['enabled']:
            self._value = dict(self._value, enabled=False)
            if self.motion is not None:
                self.motion.stop()
        try:
            self._value = self.store.preferences(value)
        except (FeatureError, OSError, ValueError):
            raise ArmUsageError('arm_settings_write_failed') from None
        return self.snapshot()

    def prepare(self):
        """User-requested current-state check/recovery, never a preference side effect."""
        if self.motion is None:
            raise ArmUsageError('arm_first_test_required')
        if not self.enabled:
            raise ArmUsageError('arm_usage_off')
        if not self._permission():
            raise ArmUsageError('arm_runtime_not_ready')
        self.motion.prepare()
        return self.snapshot()

    def snapshot(self):
        state = self.motion.snapshot() if self.motion is not None else {}
        code = (self._fault or 'arm_first_test_required' if self.motion is None else
                'arm_usage_off' if not self.enabled else
                self.port.code if self.port.attempted and self.port.code != 'arm_connection_open' else
                'arm_stopped' if state.get('stopped') else
                'arm_transport_error' if state.get('operation') == 'fault' else
                'arm_ready_position_unverified' if state.get('transport', {}).get('usable') else
                'arm_connection_not_requested')
        display_state = ('off' if not self.enabled else 'setup_required' if self.motion is None else
                         'stopped' if state.get('stopped') else
                         'connection_problem' if state.get('operation') in ('closed', 'fault') or (
                             self.port.attempted and self.port.code != 'arm_connection_open') else
                         'ready' if state.get('transport', {}).get('usable') else 'connection_unverified')
        return dict(enabled=self.enabled, conversation_gestures=self.conversation_gestures,
            state=display_state, prepare_requires_explicit_user_action=True,
            prepare_available=self.motion is not None and self.enabled and not state.get('closed')
                and state.get('operation') not in ('active', 'queued')
                and not (state.get('transport', {}).get('status') == 'UNKNOWN'),
            prepare_code=state.get('prepare_code'),
            commissioned=self.motion is not None, requires_first_test=self.motion is None,
            code=code, commissioning_evidence='operator_recorded_historical' if self.motion is not None else None,
            current_connection_verified=state.get('transport', {}).get('usable') is True,
            position_verified=False, physical_verified=False,
            settings_scope='saved_preferences_and_reviewed_device_only',
            restart_clears_stop=False)
