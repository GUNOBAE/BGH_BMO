"""Per-axis relative IMU reactions from the existing, owned B1 connection.

READY is a transport observation, never a sensor-health certificate. No norm,
attitude, altitude, integrated gyro angle, motor command or safety decision is
computed here. Missing/bad axes remain missing and only repeated real trials can
authorize a particular relative signal. Synthetic observations cannot react.
"""
from collections import deque
import math
import re
from statistics import median
import time

from ..contracts import FeatureEvent, request_id_ok


AXES = ('AX', 'AY', 'AZ', 'GX', 'GY', 'GZ')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,96}\Z')
_OK = ('READY', 'ok')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class RelativeIMUMonitor:
    """A bounded signal monitor, independent of user audio/face ownership.

    The owner must discard reactions while busy rather than queueing them for
    later. ``imu_still`` also retires an old episode on OFF/loss/restart, so the
    owner can restore only the expression it still owns. Directional gaze stays
    unavailable: there is no verified body-axis mapping in this implementation.
    """
    def __init__(self, *, clock=time.monotonic, profile=None, freshness=1.,
                 warmup_duration=1.2, min_samples=12, fixed_duration=2.,
                 event_duration=.15, stable_duration=.8, cooldown=30.,
                 max_events=2, event_window=300.):
        durations=(freshness,warmup_duration,fixed_duration,event_duration,
                   stable_duration,cooldown,event_window)
        if (any(not _finite(value) or value <= 0 for value in durations)
                or type(min_samples) is not int or not 3 <= min_samples <= 64
                or type(max_events) is not int or not 1 <= max_events <= 10):
            raise ValueError('invalid relative IMU configuration')
        self.clock=clock
        self.freshness,self.warmup_duration=freshness,warmup_duration
        self.min_samples,self.fixed_duration=min_samples,fixed_duration
        self.event_duration,self.stable_duration=event_duration,stable_duration
        self.cooldown,self.max_events,self.event_window=cooldown,max_events,event_window
        self.enabled=False
        self.profile=None
        self._binding=None
        self._closed=False
        self._boot=self._sequence=self._timestamp=None
        self._controller_origin=None
        self._last_sample=self._last_observation=self._last_now=None
        self._generation=0
        self._sensor_id=None
        self._status='ABSENT'
        self._source='not_received'
        self._code='imu_no_samples'
        self._window=deque(maxlen=128)
        self._baseline={}
        self._reference_after={axis:None for axis in AXES}
        self._axes={axis:self._empty_axis(axis) for axis in AXES}
        self._fixed={axis:(None,None) for axis in AXES}
        self._events=deque(maxlen=4)
        self._history=deque(maxlen=10)
        self._episode=None
        self._episode_generation=None
        self._burst_active=False
        self._episode_counter=0
        self._motion_since=self._stable_since=None
        self._last_event=None
        self._accepted=self._rejected=self._duplicates=self._restarts=0
        self._axis_exclusions=0
        self.set_profile(profile)

    @staticmethod
    def _empty_axis(axis):
        return dict(raw=None,value=None,unit='g' if axis.startswith('A') else 'dps',
                    valid=False,saturated=False,fixed=False,bias=None,
                    bias_status='unobserved',status='missing',exclusion_reason='no_samples',
                    relative_validated=False,relative_usable=False,threshold=None,delta=None)

    def _now(self):
        try:now=self.clock()
        except Exception:now=None
        if not _finite(now) or (self._last_now is not None and now < self._last_now):
            self._reset('imu_clock_invalid')
            return None
        self._last_now=now
        return now

    def bind(self, *, firmware_sha256, helper_sha256, sensor_sha256, boot_sha256,
             source_id, sensor_id=None):
        """Bind only to the actual verified shared-port install/USB identity."""
        value=dict(firmware_sha256=firmware_sha256,helper_sha256=helper_sha256,
                   sensor_sha256=sensor_sha256,boot_sha256=boot_sha256,source_id=source_id)
        if (not all(type(value[key]) is str and _SHA.fullmatch(value[key])
                    for key in ('firmware_sha256','helper_sha256','sensor_sha256','boot_sha256'))
                or type(source_id) is not str or not _IDENTITY.fullmatch(source_id)
                or sensor_id is not None and (type(sensor_id) is not str
                    or not _IDENTITY.fullmatch(sensor_id))):
            self._binding=None
            self._reset('imu_binding_invalid')
            return False
        if sensor_id is not None:value['sensor_id']=sensor_id
        if value != self._binding:
            self._binding=value
            self._reset('imu_binding_changed')
        return True

    def set_profile(self, profile):
        if profile is None:
            self.profile=None
        else:
            try:
                from ...imu_profile import validate_profile
                self.profile=validate_profile(profile)
            except (ValueError,TypeError,KeyError,OverflowError,RecursionError):
                self.profile=None
        self._reset('imu_profile_changed' if self.profile else 'imu_trials_required')

    def set_enabled(self, value):
        if type(value) is not bool:return False
        if self.enabled != value:
            self.enabled=value
            self._reset('imu_warming_up' if value else 'imu_reactions_off')
        return True

    def _event(self, kind, code, *, axes=(), episode_id=None, generation=None):
        self._events.append(FeatureEvent(kind,self._generation if generation is None else generation,code=code,
            data=dict(source=self._source,relative_signal_validated=self._source=='real_b1',
                      sensor_health_verified=False,usable_for_control=False,
                      relative_only=True,axes=list(axes),episode_id=episode_id,
                      age_seconds=0.)))

    def _retire_episode(self, code):
        if self._episode is not None:
            self._event('imu_still',code,episode_id=self._episode,generation=self._episode_generation)
        self._episode=None
        self._episode_generation=None
        self._motion_since=self._stable_since=None

    def _reset(self, code):
        self._retire_episode(code)
        self._burst_active=False
        self._window.clear()
        self._baseline={}
        self._reference_after={axis:None for axis in AXES}
        for record in self._axes.values():
            record.update(relative_usable=False,delta=None)
        self._fixed={axis:(None,None) for axis in AXES}
        self._code=code

    def _profile_code(self):
        if self.profile is None:return 'imu_trials_required'
        if self._binding is None:return 'imu_binding_required'
        wanted=self.profile['binding']
        if any(self._binding.get(key)!=wanted.get(key) for key in wanted if key!='sensor_id'):
            return 'imu_profile_binding_mismatch'
        if (self._sensor_id != wanted['sensor_id']
                or 'sensor_id' in self._binding and self._sensor_id != self._binding['sensor_id']):
            return 'imu_sensor_identity_mismatch'
        return None

    def _relative_axes(self):
        if self._profile_code() is not None or self._source!='real_b1':return ()
        return tuple(axis for axis in self.profile['validated_relative_axes']
                     if self._axes[axis]['valid'] and not self._axes[axis]['saturated']
                     and not self._axes[axis]['fixed'])

    def _recent(self, now, *, minimum_samples, minimum_duration):
        """Use measured arrivals, without assuming a nominal sensor cadence.

        Keep enough recent observations for BOTH criteria. A two-hertz owner
        needs a longer real interval than a ten-hertz owner for twelve samples;
        the fixed-size deque and the ingest freshness/gap checks still bound it.
        """
        recent=list(self._window)
        while (len(recent)>minimum_samples
                and now-recent[1][0]>=minimum_duration):
            recent.pop(0)
        return recent

    def _valid_context(self, value):
        return (type(value) is dict and request_id_ok(value.get('controller_boot'))
            and type(value.get('sequence')) is int and 0 <= value['sequence'] < 2**32
            and type(value.get('timestamp_ms')) is int and 0 <= value['timestamp_ms'] < 2**32
            and value.get('status') in ('READY','ok','WARMUP','ABSENT','BUS_ERROR','STALE')
            and type(value.get('sensor_id')) is str and _IDENTITY.fullmatch(value['sensor_id'])
            and value.get('accel_fs')==2 and value.get('gyro_fs')==250
            and type(value.get('axes')) is dict and set(value['axes'])==set(AXES))

    def ingest(self, observation, *, synthetic=False, mode_generation=0):
        now=self._now()
        if self._closed or now is None:return self._drain()
        if type(synthetic) is not bool or type(mode_generation) is not int or mode_generation < 0:
            self._rejected+=1;self._reset('imu_invalid_context');return self._drain()
        self._generation=mode_generation
        if not self._valid_context(observation):
            self._rejected+=1;self._reset('imu_invalid_observation');return self._drain()
        value=observation
        if 'received_at' in value and (not _finite(value['received_at'])
                or not -.05<=now-value['received_at']<=self.freshness):
            self._rejected+=1;self._reset('imu_stale_delivery');return self._drain()
        if value.get('source') not in (None,'b1_mpu6050_registers','real_b1','synthetic'):
            self._rejected+=1;self._reset('imu_invalid_source');return self._drain()
        source='synthetic' if synthetic or value.get('synthetic') is True or value.get('source')=='synthetic' else 'real_b1'
        changed=(self._boot is not None and (self._boot!=value['controller_boot']
                 or self._source!=source or self._sensor_id!=value['sensor_id']))
        if changed:
            self._restarts+=1
            self._reset('imu_source_reconnected')
            self._sequence=self._timestamp=self._last_sample=None
            self._controller_origin=None
        self._boot=value['controller_boot'];self._source=source;self._sensor_id=value['sensor_id']
        self._last_observation=now
        self._status=value['status']
        if self._status not in _OK:
            self._reset('imu_'+self._status.lower())
            for axis in AXES:
                self._axes[axis]=self._empty_axis(axis)
                self._axes[axis]['exclusion_reason']='sensor_'+self._status.lower()
            return self._drain()
        if self._sequence is not None:
            advance=(value['sequence']-self._sequence)&0xffffffff
            elapsed=(value['timestamp_ms']-self._timestamp)&0xffffffff
            if advance==0 and elapsed==0:
                self._duplicates+=1
                return self.poll()
            if not 0<advance<0x80000000 or not 0<elapsed<0x80000000:
                self._rejected+=1;self._reset('imu_reordered_observation');return self._drain()
            if elapsed>self.freshness*1000:
                self._reset('imu_sample_gap');self._controller_origin=None
        if self._last_sample is not None and now-self._last_sample>self.freshness:
            self._reset('imu_stale_recovery');self._controller_origin=None
        if self._controller_origin is None:self._controller_origin=(value['timestamp_ms'],now)
        origin_tick,origin_now=self._controller_origin
        drift=abs(((value['timestamp_ms']-origin_tick)&0xffffffff)/1000-(now-origin_now))
        self._sequence=value['sequence'];self._timestamp=value['timestamp_ms']
        if drift>self.freshness:
            self._rejected+=1;self._reset('imu_timestamp_mismatch');return self._drain()
        self._last_sample=now;self._accepted+=1
        physical={}
        for axis in AXES:
            raw=value['axes'][axis]
            record=self._empty_axis(axis)
            record['raw']=raw if type(raw) is int else None
            if type(raw) is not int or not -32768<=raw<=32767:
                self._fixed[axis]=(None,None)
                record.update(status='invalid' if raw is not None else 'missing',
                              exclusion_reason='invalid_axis' if raw is not None else 'missing_axis')
            else:
                scale=value['accel_fs'] if axis.startswith('A') else value['gyro_fs']
                number=raw/32768*scale
                old,since=self._fixed[axis]
                if old!=raw or since is None:since=now
                self._fixed[axis]=(raw,since)
                saturated=abs(raw)>=32768*.99
                fixed=now-since>=self.fixed_duration
                record.update(value=number,valid=True,saturated=saturated,fixed=fixed,
                    status='saturated' if saturated else 'fixed' if fixed else 'observed',
                    exclusion_reason='sensor_saturation' if saturated else 'fixed_axis' if fixed else None)
                if not saturated:physical[axis]=number
            self._axes[axis]=record
            if not record['valid'] or record['saturated'] or record['fixed']:
                # A recovered numeric value cannot borrow this axis's old
                # reference. Keep the remaining qualified axes in service;
                # this axis waits for the next fresh reference capture.
                self._baseline.pop(axis,None)
                self._reference_after[axis]=now
        self._window.append((now,physical))
        # Bias is a per-axis stationary warning; it is never subtracted to
        # manufacture an angle, gravity vector, or healthy sensor certificate.
        for axis in AXES:
            samples=[row[axis] for _,row in self._window if axis in row]
            if samples:
                bias=median(samples)
                limit=1.25 if axis.startswith('A') else 5.
                stable_axis=(len(samples)>=self.min_samples
                    and max(samples)-min(samples)<(.04 if axis.startswith('A') else 2.))
                self._axes[axis].update(bias=round(bias,6),
                    bias_status='large_stationary_bias' if stable_axis and abs(bias)>limit else 'unverified')
        profile_code=self._profile_code()
        usable=self._relative_axes()
        if not usable:
            self._code=profile_code or 'imu_no_validated_axes_available'
            self._retire_episode(self._code)
            self._baseline={}
            return self._drain()
        selected=self.profile['validated_relative_axes']
        for axis,entry in selected.items():
            self._axes[axis].update(relative_validated=True,threshold=entry['threshold'])
            if axis not in usable and self._axes[axis]['exclusion_reason'] is None:
                self._axes[axis]['exclusion_reason']='axis_not_currently_usable'
        for axis in AXES:
            if axis not in selected and self._axes[axis]['exclusion_reason'] is None:
                self._axes[axis]['exclusion_reason']=self.profile['excluded_axes'].get(axis,'relative_trials_not_validated')
        if not self._baseline:
            recent=self._recent(now,minimum_samples=self.min_samples,
                                minimum_duration=self.warmup_duration)
            if (len(recent)<self.min_samples
                    or now-recent[0][0]<self.warmup_duration):
                self._code='imu_warming_up';return self._drain()
            candidate_baseline={}
            for axis in usable:
                after=self._reference_after[axis]
                samples=[row[axis] for at,row in recent if axis in row
                         and (after is None or at>after)]
                entry=selected[axis]
                allowed=max(entry['threshold']*.4,entry['noise_peak']*3)
                if len(samples)>=self.min_samples and max(samples)-min(samples)<=allowed:
                    candidate_baseline[axis]=median(samples)
            if len(candidate_baseline)!=len(usable):
                self._code='imu_stationary_reference_required';return self._drain()
            self._baseline=candidate_baseline
        active=tuple(axis for axis in usable if axis in self._baseline)
        if not active:
            self._reset('imu_rebaseline_required');return self._drain()
        for axis in active:
            self._axes[axis].update(relative_usable=True,
                delta=round(abs(physical[axis]-self._baseline[axis]),6))
        for axis in usable:
            if axis not in active:
                self._axes[axis]['exclusion_reason']='axis_rebaseline_required'
        self._code='imu_relative_ready' if self.enabled else 'imu_reactions_off'
        self._prune(now)
        if not self.enabled:return self._drain()
        moving=tuple(axis for axis in active
            if abs(physical[axis]-self._baseline[axis])>=selected[axis]['threshold'])
        recent=self._recent(now,minimum_samples=3,minimum_duration=self.stable_duration)
        steady=(len(recent)>=3 and now-recent[0][0]>=self.stable_duration
            and all(len([row[axis] for _,row in recent if axis in row])==len(recent)
                and max(row[axis] for _,row in recent)-min(row[axis] for _,row in recent)
                    <=selected[axis]['threshold']*.35 for axis in active))
        if self._burst_active and steady:
            self._retire_episode('relative_stable')
            self._burst_active=False
            # A new steady resting orientation is a new *relative* reference.
            # This estimates neither gravity nor direction; gyro bias is never
            # integrated. The stability interval itself is observed on every
            # usable axis before the change, not a free-running drift subtractor.
            self._baseline={axis:median(row[axis] for _,row in recent) for axis in active}
        elif moving:
            self._stable_since=None
            if self._motion_since is None:self._motion_since=now
            if not self._burst_active and now-self._motion_since>=self.event_duration:
                # A rejected burst is consumed as well: quota expiry must never
                # replay a reaction to movement which began long ago.
                self._burst_active=True
                if (len(self._history)<self.max_events
                        and (self._last_event is None or now-self._last_event>=self.cooldown)):
                    self._episode_counter+=1
                    self._episode='imu_'+str(self._episode_counter)
                    self._episode_generation=self._generation
                    self._last_event=now;self._history.append(now)
                    self._event('imu_motion','validated_relative_motion',axes=moving,episode_id=self._episode)
        else:
            self._motion_since=None
        return self._drain()

    def _prune(self, now):
        while self._history and now-self._history[0]>=self.event_window:self._history.popleft()

    def _drain(self):
        result=tuple(self._events);self._events.clear();return result

    def poll(self):
        now=self._now()
        if now is not None and self._last_sample is not None and now-self._last_sample>self.freshness:
            self._reset('imu_stale_sample')
        return self._drain()

    def snapshot(self):
        # Snapshot does not consume an episode-retirement event needed by Host.
        now=self._now()
        if now is not None and self._last_sample is not None and now-self._last_sample>self.freshness:
            self._reset('imu_stale_sample')
        current=(now is not None and self._last_sample is not None
                 and now-self._last_sample<=self.freshness and self._status in _OK)
        if now is not None:self._prune(now)
        available=bool(current and any(axis in self._baseline for axis in self._relative_axes()))
        problems=any(axis['status'] in ('missing','invalid','saturated','fixed')
                     or axis['bias_status']=='large_stationary_bias' for axis in self._axes.values())
        return dict(enabled=self.enabled,source=self._source,synthetic=self._source=='synthetic',
            health='degraded' if problems else 'unverified',sensor_health_verified=False,
            physical_verified=False,usable_for_control=False,relative_only=True,
            readiness='READY' if available else 'UNVERIFIED',reason_code=self._code,
            current_capabilities=dict(relative_motion=available,directional_gaze=False,
                pose=False,motor_safety=False),reaction_ready=self.enabled and available,
            axes={key:dict(value) for key,value in self._axes.items()},
            sensor_id=self._sensor_id,status=self._status,
            sample_age=None if now is None or self._last_sample is None else max(0.,now-self._last_sample),
            accepted=self._accepted,rejected=self._rejected,duplicates=self._duplicates,
            controller_restarts=self._restarts,retained_samples=len(self._window),
            active_episode=self._episode,auto_events_in_window=len(self._history),
            cooldown_seconds=self.cooldown,event_window_seconds=self.event_window,
            max_events_per_window=self.max_events,
            next_in_seconds=max(0.,self.cooldown-(now-self._last_event))
                if now is not None and self._last_event is not None else 0.,
            closed=self._closed)

    def close(self):
        self._closed=True
        self._reset('imu_closed');self._events.clear()
