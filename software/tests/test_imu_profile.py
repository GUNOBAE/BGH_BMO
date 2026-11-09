"""Relative capability evidence is axis-specific, private and explicitly real."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.imu_profile import (AXES, CaptureRecorder, IMUProfileStore, MAX_BYTES,
                                PROFILE_FILE, TRIAL_KINDS, build_profile, main,
                                validate_profile)


def evidence():
    binding=dict(firmware_sha256='a'*64,helper_sha256='b'*64,sensor_sha256='c'*64,boot_sha256='d'*64,
                 source_id='pico_123',sensor_id='mpu6050_gp0_gp1_68')
    result=dict(version=1,source='real_b1',binding=binding,trials=[])
    counter=0
    for kind in TRIAL_KINDS:
        for repeat in range(2):
            observations=[]
            for index in range(16):
                # AX is the documented broken axis; GY has large stationary
                # offset but repeated relative movement is independently clear.
                axes=dict(AX=32767,AY=10+index%2,AZ=16384+index%2,
                          GX=0,GY=3000+index%2,GZ=None)
                if kind=='move' and 5<=index<=10:
                    axes['AY']+=4000;axes['GY']+=4000
                counter+=1
                observations.append(dict(controller_boot='boot_1',sequence=counter,
                    timestamp_ms=counter*100,status='READY',axes=axes,accel_fs=2,
                    gyro_fs=250,sensor_id=binding['sensor_id']))
            result['trials'].append(dict(kind=kind,trial_id=f'{kind}_{repeat}',observations=observations))
    return result


class IMUProfileTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory=self.root/'imu'
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        self.enterContext(patch('subprocess.Popen',side_effect=AssertionError('hardware forbidden')))

    def build(self, value=None):
        return build_profile(value or evidence(),physical_trials_confirmed=True)

    def test_partial_axes_and_biased_gyro_are_relative_only_with_repeated_separation(self):
        profile=self.build()
        self.assertEqual(set(profile['validated_relative_axes']),{'AY','GY'})
        self.assertEqual(profile['excluded_axes'],dict(AX='saturated',AZ='insufficient_movement',
                                                      GX='fixed',GZ='missing'))
        gyro=profile['validated_relative_axes']['GY']
        self.assertGreater(gyro['bias'],5)
        self.assertGreater(gyro['threshold'],4)
        self.assertNotIn('healthy',json.dumps(profile))
        self.assertNotIn('orientation',profile)
        self.assertEqual(validate_profile(profile),profile)
        detached=validate_profile(profile);detached['binding']['source_id']='other'
        self.assertEqual(profile['binding']['source_id'],'pico_123')

    def test_confirmation_and_real_source_required(self):
        with self.assertRaises(ValueError):build_profile(evidence())
        for source in ('synthetic','demo','healthy',True,None):
            item=evidence();item['source']=source
            with self.subTest(source=source),self.assertRaises(ValueError):self.build(item)
        item=evidence();item['binding']['source_id']='synthetic_pico'
        with self.assertRaises(ValueError):self.build(item)

    def test_all_four_source_hashes_are_required_preserved_and_validated(self):
        item=evidence()
        profile=self.build(item)
        self.assertEqual(profile['binding'],item['binding'])
        item['binding']['sensor_sha256']='not-a-hash'
        with self.assertRaises(ValueError):self.build(item)
        for key in ('sensor_sha256','boot_sha256','firmware_sha256','helper_sha256'):
            item=evidence();item['binding'].pop(key)
            with self.subTest(key=key),self.assertRaises(ValueError):self.build(item)

    def test_every_negative_context_and_repeated_move_are_required(self):
        for kind in TRIAL_KINDS:
            item=evidence();item['trials']=[t for t in item['trials'] if t['trial_id']!=kind+'_1']
            with self.subTest(kind=kind),self.assertRaises(ValueError):self.build(item)

    def test_vibration_that_resembles_movement_excludes_the_axis(self):
        for kind in ('fan','speaker','arms','stationary','still'):
            item=evidence()
            for trial in item['trials']:
                if trial['kind']==kind:
                    trial['observations'][8]['axes']['AY']+=4000
            with self.subTest(kind=kind):
                profile=self.build(item)
                self.assertNotIn('AY',profile['validated_relative_axes'])
                self.assertEqual(profile['excluded_axes']['AY'],'vibration_not_separated')
                self.assertIn('GY',profile['validated_relative_axes'])

    def test_acceleration_bias_is_not_normalized_into_health(self):
        item=evidence()
        for trial in item['trials']:
            for observation in trial['observations']:observation['axes']['AY']+=24000
        profile=self.build(item)
        self.assertEqual(profile['excluded_axes']['AY'],'acceleration_bias')
        self.assertIn('GY',profile['validated_relative_axes'])

    def test_two_move_trials_must_both_show_separation(self):
        item=evidence()
        for trial in item['trials']:
            if trial['trial_id']=='move_1':
                for observation in trial['observations']:observation['axes']['AY']=10
        profile=self.build(item)
        self.assertEqual(profile['excluded_axes']['AY'],'insufficient_movement')

    def test_real_two_and_five_hertz_trials_keep_sample_and_elapsed_requirements(self):
        for period_ms in (200,500):
            item=evidence()
            for trial in item['trials']:
                for sample in trial['observations']:
                    sample['timestamp_ms']=sample['sequence']*period_ms
            profile=self.build(item)
            self.assertEqual(set(profile['validated_relative_axes']),{'AY','GY'})
        item=evidence()
        for trial in item['trials']:
            for sample in trial['observations']:
                sample['timestamp_ms']=sample['sequence']*501
        with self.assertRaises(ValueError):self.build(item)

    def test_trial_freshness_boot_sequence_duration_and_identity_are_checked(self):
        mutations=(
            lambda s:s[5].update(controller_boot='reboot'),
            lambda s:s[5].update(sequence=s[4]['sequence']),
            lambda s:s[5].update(timestamp_ms=s[4]['timestamp_ms']),
            lambda s:s[5].update(timestamp_ms=s[4]['timestamp_ms']+600),
            lambda s:s[5].update(sensor_id='other_sensor'),
            lambda s:s[5].update(status='BUS_ERROR'),
            lambda s:s[5].update(accel_fs=16),
            lambda s:s[5].update(gyro_fs=2000),
            lambda s:s[5].update(sequence=True),
            lambda s:s[5]['axes'].update(AY=float('nan')),
            lambda s:s[5]['axes'].update(AY=32768),
        )
        for index, mutate in enumerate(mutations):
            item=evidence();mutate(item['trials'][0]['observations'])
            with self.subTest(index=index),self.assertRaises(ValueError):self.build(item)
        item=evidence();item['trials'][0]['observations']=item['trials'][0]['observations'][:11]
        with self.assertRaises(ValueError):self.build(item)
        item=evidence()
        for trial in item['trials']:
            for index,sample in enumerate(trial['observations']):sample['timestamp_ms']=index*10
        with self.assertRaises(ValueError):self.build(item)

    def test_copied_observations_cannot_count_as_repeated_physical_trials(self):
        item=evidence()
        item['trials'][1]['observations']=deepcopy(item['trials'][0]['observations'])
        with self.assertRaises(ValueError):self.build(item)

    def test_profile_edits_cannot_grant_unproven_small_threshold_or_whole_sensor_health(self):
        profile=self.build()
        mutations=(
            lambda p:p.update(source='synthetic'),
            lambda p:p.update(healthy=True),
            lambda p:p['validated_relative_axes']['AY'].update(threshold=.0001),
            lambda p:p['validated_relative_axes']['AY'].update(noise_peak=1),
            lambda p:p['validated_relative_axes']['AY'].update(unit='degrees'),
            lambda p:p['validated_relative_axes']['AY'].update(bias=2),
            lambda p:p['excluded_axes'].pop('AX'),
            lambda p:p['excluded_axes'].update(AY='saturated'),
        )
        for index,mutate in enumerate(mutations):
            item=deepcopy(profile);mutate(item)
            with self.subTest(index=index),self.assertRaises(ValueError):validate_profile(item)

    def test_readonly_missing_and_private_install_restart(self):
        store=IMUProfileStore(self.directory)
        self.assertIsNone(store.load());self.assertFalse(self.directory.exists())
        self.assertEqual(store.snapshot()['code'],'imu_profile_missing')
        self.assertFalse(store.snapshot()['physical_health_verified'])
        result=store.install(evidence(),physical_trials_confirmed=True)
        self.assertEqual(IMUProfileStore(self.directory).load(),result)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE((self.directory/PROFILE_FILE).stat().st_mode),0o600)
        loaded=store.load();loaded['binding']['source_id']='other'
        self.assertEqual(store.load()['binding']['source_id'],'pico_123')

    def test_invalid_evidence_and_synthetic_never_replace_installed_profile(self):
        store=IMUProfileStore(self.directory)
        store.install(evidence(),physical_trials_confirmed=True)
        before=(self.directory/PROFILE_FILE).read_bytes()
        item=evidence();item['source']='synthetic'
        with self.assertRaises(ValueError):store.install(item,physical_trials_confirmed=True)
        self.assertEqual((self.directory/PROFILE_FILE).read_bytes(),before)
        with self.assertRaises(ValueError):store.install(evidence())
        self.assertEqual((self.directory/PROFILE_FILE).read_bytes(),before)

    def test_private_store_rejects_checkout_links_and_unsafe_modes(self):
        checkout=self.root/'checkout';checkout.mkdir();(checkout/'.git').write_text('gitdir: private')
        self.assertFalse(IMUProfileStore(checkout/'imu').snapshot()['enabled'])
        self.directory.mkdir(mode=0o755);self.directory.chmod(0o755)
        self.assertFalse(IMUProfileStore(self.directory).snapshot()['enabled'])
        real=self.root/'real';real.mkdir(mode=0o700)
        link=self.root/'link';link.symlink_to(real,target_is_directory=True)
        self.assertFalse(IMUProfileStore(link).snapshot()['enabled'])
        (real/PROFILE_FILE).symlink_to(self.root/'target')
        self.assertFalse(IMUProfileStore(real).snapshot()['enabled'])

    def test_corrupt_live_profile_is_not_overwritten(self):
        store=IMUProfileStore(self.directory);store.install(evidence(),physical_trials_confirmed=True)
        target=self.directory/PROFILE_FILE;target.write_text('broken external data')
        with self.assertRaises((ValueError,json.JSONDecodeError)):
            store.install(evidence(),physical_trials_confirmed=True)
        self.assertEqual(target.read_text(),'broken external data')

    def test_recorder_binds_actual_source_and_export_does_not_grant_capability(self):
        item=evidence();recorder=CaptureRecorder(item['binding'])
        with self.assertRaises(ValueError):recorder.start_trial('unknown','first')
        recorder.start_trial('stationary','first')
        with self.assertRaises(ValueError):recorder.start_trial('move','second')
        with self.assertRaises(ValueError):recorder.append(item['trials'][0]['observations'][0],source='synthetic')
        with self.assertRaises(ValueError):recorder.dataset()
        for sample in item['trials'][0]['observations']:
            self.assertTrue(recorder.append(sample,item['binding']))
        recorder.finish_trial()
        store=IMUProfileStore(self.directory)
        target=store.export_evidence(recorder.dataset())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o600)
        self.assertIsNone(store.load())
        with self.assertRaises(ValueError):store.install(recorder.dataset(),physical_trials_confirmed=True)
        self.assertEqual(len(json.loads(target.read_text())['trials']),1)

    def test_cli_status_never_opens_a_device_and_install_requires_confirmation(self):
        with patch('builtins.print'):
            self.assertEqual(main(['--directory',str(self.directory)]),0)
        store=IMUProfileStore(self.directory);target=store.export_evidence(evidence())
        with patch('builtins.print'):
            self.assertEqual(main(['--directory',str(self.directory),'--evidence-file',str(target),'--install']),2)
            self.assertEqual(main(['--directory',str(self.directory),'--evidence-file',str(target),'--install','--physical-trials-confirmed']),0)
        self.assertEqual(set(IMUProfileStore(self.directory).load()['validated_relative_axes']),{'AY','GY'})

    def test_unique_trial_exports_preserve_earlier_sessions_and_reject_overwrite(self):
        store=IMUProfileStore(self.directory)
        name='real-trials-'+'a'*32+'.json'
        first=store.export_evidence(evidence(),name=name)
        before=first.read_bytes()
        next_session=evidence();next_session['trials']=next_session['trials'][:1]
        second=store.export_evidence(next_session,name='real-trials-'+'b'*32+'.json')
        self.assertNotEqual(first,second)
        self.assertEqual(first.read_bytes(),before)
        self.assertEqual(len(json.loads(second.read_text())['trials']),1)
        with self.assertRaises(ValueError):store.export_evidence(next_session,name=name)
        self.assertEqual(first.read_bytes(),before)
        self.assertEqual(store.export_evidence(evidence()).name,'real-trials.json')

    def test_trial_export_name_cannot_escape_private_directory_or_use_arbitrary_files(self):
        store=IMUProfileStore(self.directory)
        invalid=('../real-trials.json','/tmp/real-trials.json','sub/real-trials.json',
            'relative-profile.json','real-trials-'+('a'*31)+'.json',
            'real-trials-'+('A'*32)+'.json','real-trials-'+('a'*33)+'.json',
            'real-trials.json\n',None,False)
        for name in invalid:
            with self.subTest(name=name),self.assertRaises(ValueError):
                store.export_evidence(evidence(),name=name)
        self.assertFalse(self.directory.exists())

    def test_recording_cancel_restores_capacity_without_approving_or_exporting(self):
        item=evidence();recorder=CaptureRecorder(item['binding'])
        trial_id=recorder.start_trial('fan')
        self.assertTrue(trial_id.startswith('fan_'))
        recorder.append(item['trials'][0]['observations'][0])
        self.assertTrue(recorder.snapshot()['recording'])
        self.assertEqual(recorder.snapshot()['total_observations'],1)
        self.assertFalse(recorder.snapshot()['relative_signal_validated'])
        self.assertTrue(recorder.cancel_trial())
        self.assertFalse(recorder.cancel_trial())
        self.assertEqual(recorder.dataset()['trials'],[])
        self.assertEqual(recorder.snapshot()['total_observations'],0)

    def test_explicit_profile_revocation_preserves_real_evidence_and_does_not_create_missing_store(self):
        missing=IMUProfileStore(self.directory)
        self.assertTrue(missing.clear())
        self.assertFalse(self.directory.exists())
        store=IMUProfileStore(self.directory)
        evidence_file=store.export_evidence(evidence())
        before=evidence_file.read_bytes()
        store.install(evidence(),physical_trials_confirmed=True)
        self.assertTrue(store.clear())
        self.assertIsNone(store.load())
        self.assertFalse((self.directory/PROFILE_FILE).exists())
        self.assertEqual(evidence_file.read_bytes(),before)
        self.assertIsNone(IMUProfileStore(self.directory).load())
        self.assertFalse(store.snapshot()['relative_signal_validated'])
        with patch('builtins.print'):
            self.assertEqual(main(['--directory',str(self.directory),'--clear']),0)
            self.assertEqual(main(['--directory',str(self.directory),'--clear','--physical-trials-confirmed']),2)

    def test_revocation_can_remove_corrupt_private_json_but_never_follows_a_link(self):
        store=IMUProfileStore(self.directory)
        store.install(evidence(),physical_trials_confirmed=True)
        target=self.directory/PROFILE_FILE
        target.write_text('corrupt historical profile')
        corrupted=IMUProfileStore(self.directory)
        self.assertFalse(corrupted.snapshot()['enabled'])
        self.assertTrue(corrupted.clear())
        self.assertFalse(target.exists())
        outside=self.root/'preserve';outside.write_text('preserve')
        target.symlink_to(outside)
        with self.assertRaises(OSError):store.clear()
        self.assertEqual(outside.read_text(),'preserve')
        self.assertTrue(target.is_symlink())


if __name__=='__main__':unittest.main()
