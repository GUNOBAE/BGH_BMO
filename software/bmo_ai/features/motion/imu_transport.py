"""Read-only IMU snapshots on the existing, exclusively owned B1 CDC link.

Acquisition is a bounded low-priority transaction. It never grants a motor
permission, clears a latch, or creates another owner for an existing link.
"""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import Event, Lock, Thread
import time

AXES = ('AX', 'AY', 'AZ', 'GX', 'GY', 'GZ')
STATUSES = frozenset(('WARMUP', 'READY', 'ABSENT', 'BUS_ERROR', 'STALE'))
TOKEN = re.compile(r'[A-Za-z0-9_]{1,32}\Z', re.ASCII)
HEX = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
REPLY_BOUND = 256


class IMUTransportError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def parse_imu_reply(raw, request_id):
    """Strict fixed-range raw register data; READY does not mean healthy."""
    if (type(raw) is not bytes or not 1 <= len(raw) <= REPLY_BOUND or not raw.endswith(b'\n')
            or b'\r' in raw or b'\n' in raw[:-1]):
        raise IMUTransportError('imu_reply_incomplete')
    try:
        fields = raw[:-1].decode('ascii').split(' ')
        if (len(fields) != 16 or fields[:2] != ['B1','IMU'] or fields[2] != request_id
                or not TOKEN.fullmatch(fields[3]) or fields[6] not in STATUSES
                or fields[13:15] != ['A2','G250']):
            raise ValueError()
        seq, timestamp = (int(fields[n]) for n in (4,5))
        if any(not fields[n].isdigit() or len(fields[n]) > 10 for n in (4,5)):
            raise ValueError()
        if not 0 <= seq <= 0xffffffff or not 0 <= timestamp <= 0xffffffff:
            raise ValueError()
        axes = {}
        for name, value in zip(AXES, fields[7:13]):
            if value == 'n':axes[name] = None
            elif re.fullmatch(r'-?(0|[1-9][0-9]{0,4})',value) and -32768 <= int(value) <= 32767:
                axes[name] = int(value)
            else:raise ValueError()
        identity = fields[15]
        if identity == 'In':who = address = None
        elif re.fullmatch(r'I[0-9A-F]{2}A(68|69)', identity):
            who, address = int(identity[1:3],16),int(identity[4:],16)
        else:raise ValueError()
        if fields[6] == 'READY' and (who != 0x68 or address not in (0x68,0x69)):
            raise ValueError()
        return dict(controller_boot=fields[3],sequence=seq,timestamp_ms=timestamp,
            status=fields[6],axes=axes,accel_fs=2,gyro_fs=250,who_am_i=who,address=address,
            sensor_id=identity if identity != 'In' else 'unknown',
            synthetic=False,source='b1_mpu6050_registers',physical_verified=False)
    except (ValueError,UnicodeError,OverflowError):
        raise IMUTransportError('imu_reply_invalid') from None


def validated_binding(value):
    keys=('firmware_sha256','helper_sha256','sensor_sha256','boot_sha256','source_id')
    if (type(value) is not dict or not set(keys) <= set(value)
            or any(type(value[k]) is not str or not HEX.fullmatch(value[k])
                   for k in keys)):
        raise IMUTransportError('imu_binding_unverified')
    return {k:value[k] for k in keys}


def validate_imu_install(firmware_root=None, *, mount=None):
    """Verify exact v4 files, including sensor helper, with either motor flag."""
    from .arm_serial_posix_v2 import MOUNT
    root = (Path(firmware_root) if firmware_root is not None else
        Path(__file__).resolve().parents[4]/'firmware/staging/bmo_arm_legacy_bridge_v4')
    installed = Path(mount) if mount is not None else MOUNT
    def read(path):
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        try:
            info=os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 128*1024:raise ValueError()
            raw=os.read(descriptor,128*1024+1)
            if len(raw)!=info.st_size:raise ValueError()
            return raw
        finally:os.close(descriptor)
    try:
        template=read(root/'code.py')
        actual=read(installed/'code.py')
        if (template.count(b'\nREMOTE_ENABLED = False\n') != 1
                or actual not in (template,template.replace(b'REMOTE_ENABLED = False',b'REMOTE_ENABLED = True',1))):
            raise ValueError()
        hashes={'firmware_sha256':hashlib.sha256(actual).hexdigest()}
        for filename,key in (('bmo_arm_bridge.py','helper_sha256'),('bmo_imu.py','sensor_sha256'),
                             ('boot.py','boot_sha256')):
            expected=read(root/filename)
            if expected!=read(installed/filename):raise ValueError()
            hashes[key]=hashlib.sha256(expected).hexdigest()
        return hashes
    except (OSError,ValueError):
        raise IMUTransportError('imu_firmware_unverified') from None


def imu_source_identity():
    from .daily_usage import current_device_identity
    try:
        value=current_device_identity()
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()
    except Exception:
        raise IMUTransportError('imu_source_identity_unverified') from None


class IMUPoller:
    """One short background transaction, with no unbounded history or retry."""
    def __init__(self, link, *, clock=time.monotonic):
        self.link,self.clock=link,clock
        self._lock=Lock();self._wake=Event();self._closed=Event()
        self._observations=deque(maxlen=4);self._next=0.;self._pending=False
        self._code='imu_not_requested';self._last=None;self._count=0
        self._thread=Thread(target=self._run,name='bmo-imu-snapshot',daemon=True)
        self._thread.start()

    def request(self):
        with self._lock:
            now=self.clock()
            if self._closed.is_set() or self._pending or now < self._next:return
            self._pending=True;self._wake.set()

    def _run(self):
        while not self._closed.is_set():
            self._wake.wait(.1);self._wake.clear()
            with self._lock:
                pending=self._pending
            if not pending:continue
            try:
                sample=self.link.imu_snapshot()
                sample['received_at']=self.clock()
                with self._lock:
                    self._observations.append(sample);self._last=sample['received_at']
                    self._count+=1;self._code='imu_snapshot_received'
                delay=.1
            except Exception as error:
                code=getattr(error,'code',None)
                with self._lock:self._code=code if type(code) is str and code.startswith('imu_') else 'imu_transport_error'
                delay=1.
            with self._lock:
                self._next=self.clock()+delay;self._pending=False

    def drain(self):
        with self._lock:
            values=tuple(self._observations);self._observations.clear();return values

    def snapshot(self):
        with self._lock:
            return dict(enabled=not self._closed.is_set(),code=self._code,pending=self._pending,
                received=self._count,sample_age=None if self._last is None else max(0.,self.clock()-self._last),
                shared_b1_owner=True,synthetic=False)

    def close(self):
        self._closed.set();self._wake.set();self._thread.join(.25)
        if self._thread.is_alive():raise IMUTransportError('imu_worker_cleanup_pending')


def build_imu_transport(*, clock=time.monotonic, firmware_root=None, mount=None):
    """Explicit real-source factory; caller must already own the user setting."""
    from .arm_serial_posix_v2 import OwnedCdcDataPort
    from .legacy_host import LegacyArmLink,LegacyMotionFeature
    binding=dict(validate_imu_install(firmware_root,mount=mount))
    binding['source_id']=imu_source_identity()
    port=OwnedCdcDataPort()
    try:
        port.open()
        # Recheck file/device identity after reserving the one physical port.
        if (validate_imu_install(firmware_root,mount=mount)!=
                {k:v for k,v in binding.items() if k!='source_id'}
                or imu_source_identity()!=binding['source_id']):
            raise IMUTransportError('imu_binding_changed')
        motion=LegacyMotionFeature(LegacyArmLink(port,motor_permission=lambda:False),
            clock=clock,telemetry_only=True,imu_binding=binding)
        motion.configure_imu(True)
        return motion
    except Exception:
        port.close();raise
