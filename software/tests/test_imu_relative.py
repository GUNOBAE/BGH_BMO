"""Real-route provenance and per-axis relative signals, with injected IO only."""
from copy import deepcopy
import unittest

from bmo_ai.features.motion.imu_relative import AXES, RelativeIMUMonitor
from bmo_ai.imu_profile import build_profile
from test_imu_profile import evidence


class RelativeIMUMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now=0.
        self.sequence=0
        self.tick=0
        self.profile=build_profile(evidence(),physical_trials_confirmed=True)
        self.monitor=RelativeIMUMonitor(clock=lambda:self.now,profile=self.profile)
        self.assertTrue(self.monitor.bind(**self.profile['binding']))
        self.monitor.set_enabled(True)

    def sample(self, *, axes=None, synthetic=False, boot='boot_1', status='READY',
               mode_generation=7, period=.1, **changes):
        self.now+=period;self.sequence+=1;self.tick+=round(period*1000)
        values=dict(AX=32767,AY=10+self.sequence%2,AZ=16384+self.sequence%2,
                    GX=0,GY=3000+self.sequence%2,GZ=None)
        values.update(axes or {})
        value=dict(controller_boot=boot,sequence=self.sequence,timestamp_ms=self.tick,
                   status=status,axes=values,accel_fs=2,gyro_fs=250,
                   sensor_id=self.profile['binding']['sensor_id'])
        value.update(changes)
        return self.monitor.ingest(value,synthetic=synthetic,mode_generation=mode_generation)

    def warm(self):
        found=[]
        for _ in range(16):found.extend(self.sample())
        self.assertEqual(found,[])
        self.assertTrue(self.monitor.snapshot()['reaction_ready'])

    def move(self, count=4):
        result=[]
        for index in range(count):
            result.extend(self.sample(axes=dict(AY=4010+index%2,GY=7000+index%2)))
        return result

    def test_partial_axes_and_large_gyro_bias_never_mean_sensor_health(self):
        self.warm()
        state=self.monitor.snapshot()
        self.assertEqual(state['health'],'degraded')
        self.assertFalse(state['physical_verified'])
        self.assertFalse(state['sensor_health_verified'])
        self.assertFalse(state['usable_for_control'])
        self.assertEqual(state['current_capabilities'],dict(relative_motion=True,
            directional_gaze=False,pose=False,motor_safety=False))
        self.assertTrue(state['axes']['AX']['saturated'])
        self.assertIsNone(state['axes']['GZ']['value'])
        self.assertEqual(state['axes']['GZ']['exclusion_reason'],'missing_axis')
        self.assertEqual(state['axes']['GY']['bias_status'],'large_stationary_bias')
        self.assertTrue(state['axes']['GY']['relative_usable'])

    def test_actual_two_and_five_hertz_arrivals_warm_after_twelve_real_samples(self):
        for period in (.2,.5):
            self.monitor=RelativeIMUMonitor(clock=lambda:self.now,profile=self.profile)
            self.monitor.bind(**self.profile['binding']);self.monitor.set_enabled(True)
            for _ in range(11):
                self.assertEqual(self.sample(period=period),())
                self.assertFalse(self.monitor.snapshot()['reaction_ready'])
            self.assertEqual(self.sample(period=period),())
            self.assertTrue(self.monitor.snapshot()['reaction_ready'])
            events=[]
            for index in range(4):
                events+=self.sample(period=period,axes=dict(
                    AY=4010 if index%2 else -4010,GY=7000 if index%2 else -1000))
            self.assertEqual([item.kind for item in events],['imu_motion'])
            for _ in range(12):events+=self.sample(period=period)
            self.assertEqual([item.kind for item in events],['imu_motion','imu_still'])

    def test_slow_warmup_motion_requires_twelve_new_stable_samples(self):
        for period in (.2,.5):
            self.monitor=RelativeIMUMonitor(clock=lambda:self.now,profile=self.profile)
            self.monitor.bind(**self.profile['binding']);self.monitor.set_enabled(True)
            for index in range(24):
                self.assertEqual(self.sample(period=period,axes=dict(
                    AY=4010 if index%2 else -4010,GY=7000 if index%2 else -1000)),())
                self.assertFalse(self.monitor.snapshot()['reaction_ready'])
            # Stable GY alone must not authorize while the other currently
            # validated axis is still moving during initial reference capture.
            for index in range(12):
                self.assertEqual(self.sample(period=period,axes=dict(
                    AY=4010 if index%2 else -4010,GY=3000+index%2)),())
                self.assertFalse(self.monitor.snapshot()['reaction_ready'])
            for _ in range(11):
                self.assertEqual(self.sample(period=period),())
                self.assertFalse(self.monitor.snapshot()['reaction_ready'])
            self.assertEqual(self.sample(period=period),())
            self.assertTrue(self.monitor.snapshot()['reaction_ready'])

    def test_current_unqualified_data_stays_observable_but_cannot_react(self):
        self.monitor.set_profile(None)
        for _ in range(30):self.sample()
        self.assertEqual(self.monitor.snapshot()['axes']['GY']['bias_status'],'large_stationary_bias')
        self.assertEqual(self.move(),[])
        state=self.monitor.snapshot()
        self.assertEqual(state['reason_code'],'imu_trials_required')
        self.assertGreater(state['retained_samples'],0)
        self.assertEqual(state['axes']['GY']['bias_status'],'unverified')
        self.assertTrue(state['axes']['AX']['saturated'])
        self.assertFalse(state['reaction_ready'])

    def test_synthetic_cannot_borrow_a_real_validated_profile(self):
        for _ in range(16):self.sample(synthetic=True)
        self.assertEqual(self.move(),[])  # source change requires a fresh baseline
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        for _ in range(30):self.sample(synthetic=True,axes=dict(AY=4010,GY=7000))
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        self.assertEqual(self.monitor.snapshot()['source'],'synthetic')

    def test_single_fresh_motion_event_then_still_restores_same_episode(self):
        self.warm()
        events=self.move()
        self.assertEqual([event.kind for event in events],['imu_motion'])
        event=events[0]
        self.assertEqual(event.mode_generation,7)
        self.assertTrue(event.data['relative_signal_validated'])
        self.assertFalse(event.data['sensor_health_verified'])
        self.assertEqual(set(event.data['axes']),{'AY','GY'})
        self.assertEqual(event.data['age_seconds'],0)
        settled=[]
        for _ in range(14):settled.extend(self.sample())
        self.assertEqual([event.kind for event in settled],['imu_still'])
        self.assertEqual(settled[0].data['episode_id'],event.data['episode_id'])
        self.assertIsNone(self.monitor.snapshot()['active_episode'])

    def test_new_stable_resting_orientation_restores_without_claiming_direction(self):
        self.warm();events=self.move()
        for index in range(12):events+=self.sample(axes=dict(AY=4010+index%2,GY=7000+index%2))
        self.assertEqual([event.kind for event in events],['imu_motion','imu_still'])
        self.assertFalse(self.monitor.snapshot()['current_capabilities']['directional_gaze'])
        self.assertFalse(self.monitor.snapshot()['current_capabilities']['pose'])

    def test_continuous_movement_does_not_react_again_at_cooldown_expiry(self):
        self.warm();events=[]
        for index in range(400):
            values=dict(AY=4010 if index%2 else -4010,GY=7000 if index%2 else -1000)
            events+=self.sample(axes=values)
        self.assertEqual([event.kind for event in events],['imu_motion'])

    def test_quota_suppressed_burst_is_consumed_not_queued_until_expiry(self):
        self.warm();self.move()
        for _ in range(14):self.sample()
        # New movement begins before the thirty-second minimum interval.
        events=[]
        for index in range(400):
            events+=self.sample(axes=dict(AY=4010 if index%2 else -4010,
                                         GY=7000 if index%2 else -1000))
        self.assertEqual(events,[])
        for _ in range(14):self.sample()
        events=self.move()
        self.assertEqual([event.kind for event in events],['imu_motion'])

    def test_rolling_count_is_separate_from_episode_end(self):
        self.warm()
        # First and second bursts are separated by thirty seconds of actual
        # fresh stationary data, so a stale recovery cannot reset the quota.
        self.assertEqual(len(self.move()),1)
        for _ in range(305):self.sample()
        self.assertEqual(len(self.move()),1)
        for _ in range(305):self.sample()
        self.assertEqual(self.move(),[])
        self.assertEqual(self.monitor.snapshot()['auto_events_in_window'],2)

    def test_off_retires_expression_without_enabling_automatic_mode(self):
        self.warm();event=self.move()[0]
        self.monitor.set_enabled(False)
        events=self.monitor.poll()
        self.assertEqual([item.kind for item in events],['imu_still'])
        self.assertEqual(events[0].data['episode_id'],event.data['episode_id'])
        for _ in range(16):self.sample()
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        self.assertEqual(self.move(),[])

    def test_each_unusable_axis_is_excluded_without_invented_zero_or_norm(self):
        self.warm()
        for axis in ('AY','GY'):
            entry=dict(AY=None,GY=None);entry[axis]=4010 if axis=='AY' else 7000
            self.sample(axes=entry)
            state=self.monitor.snapshot()
            other='GY' if axis=='AY' else 'AY'
            self.assertIsNone(state['axes'][other]['value'])
            self.assertFalse(state['axes'][other]['relative_usable'])
            self.assertNotIn('norm',state)
            self.assertNotIn('attitude',state)

    def test_partial_axis_recovery_cannot_reuse_an_old_reference(self):
        for bad in (None,32767,True,'fixed'):
            self.monitor=RelativeIMUMonitor(clock=lambda:self.now,profile=self.profile)
            self.monitor.bind(**self.profile['binding']);self.monitor.set_enabled(True)
            self.warm()
            if bad=='fixed':
                for _ in range(25):self.sample(axes=dict(AY=10))
            else:self.sample(axes=dict(AY=bad))
            state=self.monitor.snapshot()
            self.assertFalse(state['axes']['AY']['relative_usable'])
            self.assertTrue(state['axes']['GY']['relative_usable'])
            self.assertTrue(state['reaction_ready'])
            events=[]
            # A changed stationary offset on the recovered axis must neither
            # become movement nor silently regain its pre-fault reference.
            for index in range(16):events+=self.sample(axes=dict(AY=9000+index%2))
            self.assertEqual(events,[])
            state=self.monitor.snapshot()
            self.assertFalse(state['axes']['AY']['relative_usable'])
            self.assertEqual(state['axes']['AY']['exclusion_reason'],'axis_rebaseline_required')
            self.assertTrue(state['axes']['GY']['relative_usable'])
            for index in range(4):
                events+=self.sample(axes=dict(AY=9000+index%2,GY=7000+index%2))
            self.assertEqual([event.kind for event in events],['imu_motion'])
            self.assertEqual(events[0].data['axes'],['GY'])

    def test_all_axis_reference_loss_requires_fresh_post_fault_samples(self):
        self.warm()
        self.sample(axes=dict(AY=None,GY=None))
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        for index in range(11):
            self.sample(period=.2,axes=dict(AY=9000+index%2,GY=12000+index%2))
            self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        self.sample(period=.2,axes=dict(AY=9001,GY=12001))
        self.assertTrue(self.monitor.snapshot()['reaction_ready'])

    def test_dropped_sample_and_duplicate_do_not_refresh_old_signal(self):
        self.warm();self.move()
        self.now+=1.1
        result=self.monitor.poll()
        self.assertEqual([item.kind for item in result],['imu_still'])
        state=self.monitor.snapshot();self.assertFalse(state['reaction_ready'])
        self.assertEqual(state['reason_code'],'imu_stale_sample')
        self.sample(sequence=self.sequence,timestamp_ms=self.tick-100)
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])

    def test_restart_and_changed_hashes_require_rebaseline_and_matching_trials(self):
        self.warm();old=self.move()[0]
        result=self.sample(boot='new_boot',mode_generation=8)
        self.assertEqual([item.kind for item in result],['imu_still'])
        self.assertEqual(result[0].mode_generation,7)
        self.assertEqual(result[0].data['episode_id'],old.data['episode_id'])
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        for _ in range(16):self.sample(boot='new_boot')
        self.assertTrue(self.monitor.snapshot()['reaction_ready'])
        for key in ('firmware_sha256','helper_sha256','sensor_sha256','boot_sha256','source_id'):
            binding=dict(self.profile['binding']);binding[key]='e'*64
            self.assertTrue(self.monitor.bind(**binding))
            for _ in range(16):self.sample(boot='new_boot')
            self.assertFalse(self.monitor.snapshot()['reaction_ready'])
            self.assertEqual(self.monitor.snapshot()['reason_code'],'imu_profile_binding_mismatch')

    def test_sensor_error_invalid_data_and_bad_clock_affect_only_monitor(self):
        for status in ('ABSENT','BUS_ERROR','STALE','WARMUP'):
            self.monitor=RelativeIMUMonitor(clock=lambda:self.now,profile=self.profile)
            self.monitor.bind(**self.profile['binding']);self.monitor.set_enabled(True)
            self.warm();self.move()
            events=self.sample(status=status)
            self.assertEqual([item.kind for item in events],['imu_still'])
            self.assertFalse(self.monitor.snapshot()['reaction_ready'])
            self.assertTrue(self.monitor.enabled)
            self.assertFalse(self.monitor.snapshot()['usable_for_control'])
            # Restore clean baseline with the same source, never mark synthetic.
            for _ in range(16):self.sample()
        self.now=-1.
        self.assertEqual(self.monitor.poll(),())
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])

    def test_whole_sample_reorder_and_range_change_do_not_authorize_reactions(self):
        self.warm();self.move()
        self.sample(sequence=1,timestamp_ms=100)
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        for _ in range(16):self.sample()
        self.sample(accel_fs=16)
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        self.assertEqual(self.monitor.snapshot()['reason_code'],'imu_invalid_observation')

    def test_transport_arrival_age_and_synthetic_metadata_cannot_be_dropped(self):
        self.warm();self.move()
        result=self.sample(received_at=self.now-2.)
        self.assertEqual([event.kind for event in result],['imu_still'])
        self.assertEqual(self.monitor.snapshot()['reason_code'],'imu_stale_delivery')
        for _ in range(16):self.sample()
        self.assertTrue(self.monitor.snapshot()['reaction_ready'])
        self.sample(source='synthetic')
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        self.assertEqual(self.monitor.snapshot()['source'],'synthetic')

    def test_close_is_local_and_drops_events(self):
        self.warm();self.move();self.monitor.close()
        self.assertEqual(self.monitor.poll(),())
        self.assertFalse(self.monitor.snapshot()['reaction_ready'])
        self.assertTrue(self.monitor.snapshot()['closed'])


if __name__=='__main__':unittest.main()
