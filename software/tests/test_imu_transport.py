import hashlib
import os
from pathlib import Path
import tempfile
from threading import Event,Thread
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.motion.imu_transport import (
    AXES,IMUTransportError,parse_imu_reply,validate_imu_install,imu_source_identity)
from bmo_ai.features.motion.legacy_host import LegacyArmLink,LegacyMotionFeature,LegacyArmError
from bmo_ai.features.motion.arm_serial_posix_v2 import OwnedCdcDataPort
from bmo_ai.features.motion.reviewed_boot import (
    CANONICAL_BOOT_SHA256,SUPERVISED_BOOT_SHA256)


def reply(rid,values='-32768 120 16300 4 n -5',status='READY',boot='board_1'):
    return f'B1 IMU {rid} {boot} 17 2000 {status} {values} A2 G250 I68A68\n'.encode()


class Port:
    timeout=.025
    def __init__(self):
        self.writes=[];self.closed=False;self.pending=[];self.imu_error=None
    def write(self,data):
        self.writes.append(data)
        p=data.decode().strip().split()
        code='READY' if p[1]=='HELLO' else 'ALIVE' if p[1]=='PING' else 'STOPPED'
        self.pending.append(f'B1 ACK {p[2]} board_1 {code} POSITION_UNVERIFIED\n'.encode())
        return len(data)
    def read_until(self,delimiter,bound):return self.pending.pop(0) if self.pending else b''
    def exchange_imu(self,data):
        self.writes.append(data)
        if self.imu_error is not None:return self.imu_error
        return reply(data.decode().strip().split()[2])
    def close(self):self.closed=True


class IMUTransportTests(unittest.TestCase):
    def test_raw_partial_axes_preserved_no_health_or_pose_inference(self):
        value=parse_imu_reply(reply('imu_1'),'imu_1')
        self.assertEqual(value['axes'],dict(zip(AXES,[-32768,120,16300,4,None,-5])))
        self.assertFalse(value['synthetic']);self.assertFalse(value['physical_verified'])
        self.assertEqual((value['who_am_i'],value['address']),(104,104))
        self.assertNotIn('healthy',value);self.assertNotIn('orientation',value)

    def test_bad_schema_identity_and_values_rejected(self):
        base=reply('imu_1')
        variants=[base.replace(b'imu_1',b'imu_old'),base.replace(b'120',b'nan'),
            base.replace(b'120',b'32768'),base.replace(b'I68A68',b'I00A68'),
            base.replace(b'A2',b'A16'),base.replace(b' 17 ',b' -1 '),
            base+b'junk',base.replace(b'\n',b'\r\n'),b'x'*257,
            base.replace(b'READY',b'HEALTHY')]
        for raw in variants:
            with self.subTest(raw=raw),self.assertRaises(IMUTransportError):
                parse_imu_reply(raw,'imu_1')

    def test_absence_is_not_zero_filled(self):
        raw=reply('imu_2','n n n n n n','ABSENT').replace(b'I68A68',b'In')
        value=parse_imu_reply(raw,'imu_2')
        self.assertTrue(all(v is None for v in value['axes'].values()))
        self.assertIsNone(value['who_am_i'])

    def test_readonly_sensor_after_stop_and_motor_unknown_does_not_release_latch(self):
        port=Port();link=LegacyArmLink(port,motor_permission=lambda:False)
        link.stopped=link.unknown=True;link.last_response_code='STOPPED'
        value=link.imu_snapshot()
        self.assertEqual(value['status'],'READY')
        self.assertTrue(link.stopped and link.unknown)
        self.assertEqual(link.last_response_code,'STOPPED')
        self.assertIsNone(link.boot)
        self.assertEqual(len(port.writes),1);self.assertTrue(port.writes[0].startswith(b'B1 IMU '))

    def test_invalid_sensor_reply_does_not_poison_arms(self):
        port=Port();link=LegacyArmLink(port,motor_permission=lambda:True)
        link.hello();port.imu_error=b'B1 IMU imu_1 corrupt\n'
        with self.assertRaises(IMUTransportError):link.imu_snapshot()
        self.assertFalse(link.unknown);self.assertFalse(link.stopped)
        link.ping();self.assertEqual(link.last_response_code,'ALIVE')

    def test_late_sensor_reply_cannot_replace_motor_ack(self):
        port=Port();link=LegacyArmLink(port,motor_permission=lambda:True)
        port.imu_error=b''
        with self.assertRaises(IMUTransportError):link.imu_snapshot()
        port.pending.append(reply('imu_1'))
        link.hello();link.ping()
        self.assertFalse(link.unknown);self.assertEqual(link.boot,'board_1')

    def test_no_unbounded_port_fallback(self):
        port=Port();port.exchange_imu=None
        with self.assertRaisesRegex(IMUTransportError,'imu_port_budget_unavailable'):
            LegacyArmLink(port).imu_snapshot()
        self.assertEqual(port.writes,[])

    def test_async_poll_does_not_wait_and_readonly_close_never_stops_board(self):
        port=Port();entered=Event();release=Event()
        original=port.exchange_imu
        def held(data):entered.set();release.wait(.1);return original(data)
        port.exchange_imu=held
        binding={key:'a'*64 for key in ('firmware_sha256','helper_sha256','sensor_sha256','boot_sha256','source_id')}
        motion=LegacyMotionFeature(LegacyArmLink(port),telemetry_only=True,imu_binding=binding)
        motion.configure_imu(True)
        try:
            started=time.monotonic();motion.poll_imu()
            self.assertLess(time.monotonic()-started,.025)
            self.assertTrue(entered.wait(.1))
            started=time.monotonic();motion.poll()
            self.assertLess(time.monotonic()-started,.025)
            release.set()
            deadline=time.monotonic()+.3
            while time.monotonic()<deadline and motion.imu_transport_snapshot()['received']<1:time.sleep(.002)
            values=motion.drain_imu()
            self.assertEqual(len(values),1)
            self.assertEqual(values[0]['source'],'b1_mpu6050_registers')
            self.assertIn('received_at',values[0])
            result=motion.submit('wave',request_id='do_1',session_id='s',turn_id='t',
                mode_generation=0,started_at=0,explicit=True)
            self.assertEqual(result.code,'imu_read_only_transport')
        finally:
            release.set();motion.close()
        self.assertTrue(port.closed)
        self.assertTrue(all(data.startswith(b'B1 IMU ') for data in port.writes))

    def test_partial_and_coalesced_lines_keep_frame_boundaries(self):
        reader,writer=os.pipe()
        port=OwnedCdcDataPort();port.fd=reader;port.is_open=True;port.timeout=.01
        try:
            os.write(writer,b'B1 IMU imu_1 partial')
            self.assertEqual(port._read_line(256,time.monotonic()+.001,preserve_partial=True),b'')
            os.write(writer,b'\nACK\n')
            self.assertEqual(port.read_until(b'\n',256),b'B1 IMU imu_1 partial\n')
            self.assertEqual(port.read_until(b'\n',128),b'ACK\n')
        finally:
            os.close(writer);os.close(reader);port.fd=None

    def test_sensor_io_has_fixed_budget_and_partial_write_stays_distinct(self):
        from bmo_ai.features.motion.arm_serial_posix_v2 import ArmPortUnavailable
        port=OwnedCdcDataPort();port.is_open=True;port.fd=123
        budgets=[]
        def writable(readers,writers,errors,timeout):
            budgets.append(timeout);return [],writers,[]
        with patch('bmo_ai.features.motion.arm_serial_posix_v2.select.select',side_effect=writable),patch(
                'bmo_ai.features.motion.arm_serial_posix_v2.os.write',return_value=3):
            with self.assertRaisesRegex(ArmPortUnavailable,'imu_partial_write'):
                port.exchange_imu(b'B1 IMU imu_1\n')
        self.assertTrue(all(0 <= timeout <= .025 for timeout in budgets))
        link=LegacyArmLink(Port());link.port.exchange_imu=lambda data:(_ for _ in ()).throw(
            ArmPortUnavailable('imu_partial_write'))
        with self.assertRaisesRegex(IMUTransportError,'imu_partial_write'):link.imu_snapshot()
        self.assertFalse(link.unknown);self.assertFalse(link.stopped)

    def test_source_identity_hash_changes_with_actual_serial(self):
        with patch('bmo_ai.features.motion.daily_usage.current_device_identity',return_value={'serial':'device-one'}):
            first=imu_source_identity()
        with patch('bmo_ai.features.motion.daily_usage.current_device_identity',return_value={'serial':'device-two'}):
            second=imu_source_identity()
        self.assertRegex(first,r'^[0-9a-f]{64}$');self.assertNotEqual(first,second)

    def test_readonly_daily_port_does_not_authorize_old_arm_receipt(self):
        from bmo_ai.features.motion.daily_usage import _DailyPort
        from bmo_ai.features.motion.legacy_host import LegacyArmError
        port=Port();port.open=lambda:port
        binding={key:'a'*64 for key in ('firmware_sha256','helper_sha256','sensor_sha256','boot_sha256')}
        old_record={'reviewed_code_sha256':'b'*64,'device_identity':{'serial':'old'},'board_files_sha256':{}}
        daily=_DailyPort(old_record,port_factory=lambda:port,identity_reader=lambda:{'serial':'current'})
        with patch('bmo_ai.features.motion.imu_transport.validate_imu_install',return_value=binding),patch(
                'bmo_ai.features.motion.daily_usage.validate_legacy_install',side_effect=LegacyArmError('old_receipt')):
            link=LegacyArmLink(daily,motor_permission=lambda:True)
            self.assertEqual(link.imu_snapshot()['status'],'READY')
            self.assertFalse(daily.motor_admitted)
            with self.assertRaises(LegacyArmError):link.hello()
            self.assertTrue(link.unknown)
            self.assertFalse(port.closed)
            self.assertEqual(link.imu_snapshot()['status'],'READY')
            self.assertTrue(all(data.startswith(b'B1 IMU ') for data in port.writes))
        daily.close();self.assertTrue(port.closed)

    def test_exact_sensor_helper_required_for_v4_intake(self):
        root=Path(__file__).resolve().parents[2]/'firmware/staging/bmo_arm_legacy_bridge_v4'
        if not (root/'bmo_imu.py').exists():self.skipTest('v4 candidate not yet available')
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)
            for name in ('code.py','bmo_arm_bridge.py','bmo_imu.py','boot.py'):
                (target/name).write_bytes((root/name).read_bytes())
            value=validate_imu_install(root,mount=target)
            self.assertEqual(value['sensor_sha256'],hashlib.sha256((root/'bmo_imu.py').read_bytes()).hexdigest())
            (target/'bmo_imu.py').write_text('unreviewed')
            with self.assertRaisesRegex(IMUTransportError,'imu_firmware_unverified'):
                validate_imu_install(root,mount=target)
            (target/'bmo_imu.py').unlink()
            (target/'bmo_imu.py').symlink_to(root/'bmo_imu.py')
            with self.assertRaisesRegex(IMUTransportError,'imu_firmware_unverified'):
                validate_imu_install(root,mount=target)

    def test_actual_known_boot_hash_survives_transport_capture_profile_and_monitor(self):
        from bmo_ai.imu_profile import CaptureRecorder,IMUProfileStore
        from bmo_ai.features.motion.imu_relative import RelativeIMUMonitor
        from test_imu_profile import evidence
        root=Path(__file__).resolve().parents[2]/'firmware/staging/bmo_arm_legacy_bridge_v4'
        for supervised in (False,True):
            for enabled in (False,True):
                with self.subTest(supervised=supervised,enabled=enabled),tempfile.TemporaryDirectory() as directory:
                    target=Path(directory)/'board';target.mkdir()
                    for name in ('code.py','bmo_arm_bridge.py','bmo_imu.py','boot.py'):
                        source=root/name
                        if supervised and name=='boot.py':
                            source=root.with_name('bmo_arm_supervised_v1')/name
                        raw=source.read_bytes()
                        if name=='code.py' and enabled:
                            raw=raw.replace(b'REMOTE_ENABLED = False',b'REMOTE_ENABLED = True',1)
                        (target/name).write_bytes(raw)
                    actual=validate_imu_install(root,mount=target)
                    expected=SUPERVISED_BOOT_SHA256 if supervised else CANONICAL_BOOT_SHA256
                    self.assertEqual(actual['boot_sha256'],expected)
                    item=evidence()
                    item['binding'].update(actual)
                    recorder=CaptureRecorder(item['binding'])
                    for trial in item['trials']:
                        recorder.start_trial(trial['kind'],trial['trial_id'])
                        for sample in trial['observations']:
                            recorder.append(sample,binding=item['binding'],source='real_b1')
                        recorder.finish_trial()
                    captured=recorder.dataset()
                    self.assertEqual(captured['binding']['boot_sha256'],expected)
                    store=IMUProfileStore(Path(directory)/'private')
                    profile=store.install(captured,physical_trials_confirmed=True)
                    self.assertEqual(store.load()['binding']['boot_sha256'],expected)
                    now=[0.]
                    monitor=RelativeIMUMonitor(clock=lambda:now[0],profile=profile)
                    monitor.bind(**profile['binding']);monitor.set_enabled(True)
                    for sample in item['trials'][0]['observations']:
                        now[0]+=.1;monitor.ingest(sample)
                    self.assertTrue(monitor.snapshot()['reaction_ready'])
                    alternate=CANONICAL_BOOT_SHA256 if supervised else SUPERVISED_BOOT_SHA256
                    monitor.bind(**dict(profile['binding'],boot_sha256=alternate))
                    for sample in item['trials'][1]['observations']:
                        now[0]+=.1;monitor.ingest(sample)
                    self.assertFalse(monitor.snapshot()['reaction_ready'])
                    self.assertEqual(monitor.snapshot()['reason_code'],'imu_profile_binding_mismatch')
                    # A documentation-only unknown modification is still rejected.
                    (target/'boot.py').write_bytes((target/'boot.py').read_bytes()+b'\n# not reviewed\n')
                    with self.assertRaisesRegex(IMUTransportError,'imu_firmware_unverified'):
                        validate_imu_install(root,mount=target)

    def test_v4_daily_receipt_pins_sensor_dependency_and_detects_change(self):
        from bmo_ai.features.motion.daily_usage import ArmUsageStore,DailyArmController,CHECKS,DEVICE
        root=Path(__file__).resolve().parents[2]/'firmware/staging/bmo_arm_legacy_bridge_v4'
        if not (root/'bmo_imu.py').exists():self.skipTest('v4 candidate not yet available')
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'board';target.mkdir()
            for name in ('code.py','bmo_arm_bridge.py','bmo_imu.py','boot.py'):
                raw=(root/name).read_bytes()
                if name=='code.py':raw=raw.replace(b'REMOTE_ENABLED = False',b'REMOTE_ENABLED = True',1)
                (target/name).write_bytes(raw)
            identity=dict(device=str(DEVICE),interface='02',label='CircuitPython CDC2 fixture',
                product='RP2040 fixture',vendor_id='239a',product_id='80f4',serial='synthetic_v4')
            store=ArmUsageStore(Path(directory)/'private')
            record=store.commission(reviewed_sha=hashlib.sha256((target/'code.py').read_bytes()).hexdigest(),
                test_session_id='synthetic_v4_test',checks={name:True for name in CHECKS},
                mount=target,identity_reader=lambda:identity)
            self.assertIn('bmo_imu.py',record['board_files_sha256'])
            self.assertEqual(store.load()['commissioning']['board_files_sha256'],record['board_files_sha256'])
            (target/'bmo_imu.py').write_text('changed dependency')
            port=Port();port.open=lambda:port
            controller=DailyArmController(store=store,runtime_ready=lambda:True,
                mount=target,identity_reader=lambda:identity,port_factory=lambda:port)
            with self.assertRaises(LegacyArmError):controller.motion.link.hello()
            self.assertEqual(port.writes,[])


if __name__=='__main__':unittest.main()
