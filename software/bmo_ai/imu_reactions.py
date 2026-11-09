"""Owner-thread IMU product bridge: relative evidence, optional cache, no AI.

The transport is borrowed from the current Legacy B1 owner, or opened read-only
when there is no arm owner. No sensor result authorizes a motor or a pose.
"""
from collections import deque
from uuid import uuid4

from .features.contracts import FeatureError


class IMUReactions:
    def __init__(self, host, *, monitor=None, transport_factory=None, profile_directory=None):
        from .features.motion.imu_relative import RelativeIMUMonitor
        from .imu_profile import IMUProfileStore
        self.host, self.clock = host, host.clock
        self.directory = profile_directory
        self.profile_code = 'imu_profile_unavailable'
        try:
            self.store = IMUProfileStore(profile_directory)
            profile = self.store.load()
            self.profile_code = 'imu_profile_loaded' if profile else 'imu_validation_required'
        except Exception:
            profile = None
        self.monitor = monitor or RelativeIMUMonitor(clock=self.clock, profile=profile)
        self.factory = transport_factory
        self.transport = None
        self.owned = False
        self.enabled = False
        self.presentation_allowed = True
        self.next_connect = 0.
        self.transport_code = 'not_opened'
        self.suppression = 'off'
        self.active = self.restore = None
        self.last_audio = self.last_arm = None
        self.started = deque(maxlen=3)
        self.episodes = deque(maxlen=32)
        self.recorder = None
        self.last_capture_sample = None
        self.observed_boot = None
        self.voice_available = False
        self.next_asset_check = 0.
        self.capture = dict(status='idle', label=None, samples=0, code='imu_trial_not_started')

    def trial(self, value):
        label, action = value['label'], value['action']
        if self.host.runtime.simulate:
            self.capture.update(status='blocked', code='imu_real_capture_required')
            raise FeatureError('imu_real_capture_required')
        if action == 'start':
            if self.capture['status'] == 'recording':raise FeatureError('imu_trial_busy')
            self.interrupt('imu_trial_active')
            self.capture = dict(status='recording', label=label, samples=0,
                code='imu_trial_waiting_for_sensor', trial_id=uuid4().hex)
        elif action == 'cancel':
            if self.recorder is not None:self.recorder.cancel_trial()
            self.capture.update(status='idle',code='imu_trial_cancelled')
            if not self.enabled:self.release_owned()
        else:
            if self.capture['status'] != 'recording' or self.capture['label'] != label:
                raise FeatureError('imu_trial_not_started')
            try:
                if self.recorder is None or self.capture['samples'] < 12:
                    if self.recorder is not None:self.recorder.cancel_trial()
                    raise ValueError('no_samples')
                self.recorder.finish_trial()
                name='real-trials-'+uuid4().hex+'.json'
                self.store.export_evidence(self.recorder.dataset(), name=name)
                self.capture.update(status='saved',code='imu_trial_saved',evidence_file=name)
            except Exception:
                self.capture.update(status='failed',code='imu_trial_insufficient_samples')
                raise FeatureError('imu_trial_insufficient_samples') from None
            finally:
                if not self.enabled:self.release_owned()

    def set_enabled(self, value, *, persist=True):
        if type(value) is not bool:
            raise FeatureError('invalid_imu_setting')
        self.enabled = value
        self.monitor.set_enabled(value)
        if not value:
            self.interrupt('off')
            if self.capture['status'] != 'recording':
                self.release_owned()
        if persist and self.host.preferences is not None:
            self.host.preferences.update(imu_reactions=value)

    def release_owned(self):
        if self.transport is not None:
            if self.owned:
                self.transport.close()  # Read-only close never sends STOP.
            else:
                self.transport.configure_imu(False)
        self.transport, self.owned = None, False

    def _connect(self):
        from .features.motion.legacy_host import LegacyMotionFeature
        current = self.host.motion
        if isinstance(current, LegacyMotionFeature):
            if self.transport is not current:
                self.release_owned()
                self.transport = current
        elif self.transport is None and self.clock() >= self.next_connect:
            self.next_connect = self.clock()+5.
            if self.factory is None:
                if self.host.runtime.simulate:
                    self.transport_code = 'synthetic_no_sensor'
                    return
                from .features.motion.imu_transport import build_imu_transport
                self.transport = build_imu_transport(clock=self.clock)
            else:
                self.transport = self.factory()
            self.owned = True
        if self.transport is not None:
            if self.transport.imu_binding is None:
                from .features.motion.imu_transport import validate_imu_install, imu_source_identity
                binding=dict(validate_imu_install(), source_id=imu_source_identity())
                self.transport.configure_imu(True,binding=binding)
            else:self.transport.configure_imu(True)
            binding=self.transport.imu_binding
            self.monitor.bind(**binding)
            self.transport_code = 'shared_b1' if not self.owned else 'read_only_b1'

    def interrupt(self, reason, *, cancel_audio=True):
        if self.active:
            self.restore = self.active
        self.active = None
        self.suppression = reason
        if (cancel_audio and self.host._local_event_active
                and self.host._local_event_active.get('scope') == 'imu'):
            self.host.cancel_conversation()

    def _blocked(self):
        host, now = self.host, self.clock()
        face = host.session.snapshot()
        owned = bool(host._local_event_active and host._local_event_active.get('scope') == 'imu')
        if not self.enabled:return 'off'
        if self.capture['status'] == 'recording':return 'imu_trial_active'
        if not self.presentation_allowed:return 'desktop_or_settings_active'
        if host.modes.mode != 'conversation' or host.modes.transitioning:return 'foreground_activity'
        if host.session.busy and not owned:return 'conversation_active'
        if host.audio.owner is not None and not owned:return 'audio_busy'
        if face['phase'] in ('listening', 'thinking') and not owned:return 'input_active'
        if host.volume.snapshot()['effective_level'] <= 0:return 'muted'
        plan = face.get('character') or {}
        if plan.get('sensitive_context') or face['emotion'] in ('angry','crying','sad'):return 'sensitive_context'
        motion = host.motion.snapshot()
        if motion.get('active'):
            self.last_arm = now
            return 'arm_vibration'
        if self.last_arm is not None and now-self.last_arm < 2.:return 'arm_settling'
        if self.last_audio is not None and now-self.last_audio < 2.:return 'audio_settling'
        return None

    def _event(self, event):
        now = self.clock()
        if event.kind == 'imu_still':
            if (self.active and self.active['episode_id'] == (event.data or {}).get('episode_id')
                    and self.active['generation'] == event.mode_generation):
                self.restore, self.active = self.active, None
            return
        if event.kind != 'imu_motion':return
        data = event.data or {}
        if (event.mode_generation != self.host.modes.generation
                or data.get('age_seconds', 1) > .5):
            self.suppression = 'stale_event';return
        blocked = self._blocked()
        if blocked:
            self.suppression = blocked;return
        episode = data.get('episode_id')
        if episode in self.episodes:return
        self.episodes.append(episode)
        while self.started and now-self.started[0] >= 300.:self.started.popleft()
        if self.started and (now-self.started[-1] < 30. or len(self.started) >= 2):
            self.suppression = 'reaction_cooldown';return
        self.started.append(now)
        face = self.host.session.snapshot()
        self.active = dict(deadline=now+2.5, baseline=face['emotion'], turn_id=face['turn_id'], cached=False,
            episode_id=episode, generation=event.mode_generation)
        # Existing individually approved/full-use English assets only. Lookup
        # never creates a selection; actual session playback commits the clip.
        for name in ('reaction_gasp', 'reaction_interesting'):
            if self.host._start_owned_notice(name, '', owner='imu', cache_only=True):
                self.active.update(turn_id=self.host.session.snapshot()['turn_id'], cached=True)
                break
        self.suppression = 'cached_reaction' if self.active['cached'] else 'face_only_assets_pending'

    def playback(self, kind, turn_id, at):
        if self.active and self.active['cached'] and self.active['turn_id']==turn_id:
            if kind == 'started':self.active['deadline']=at+2.5
            elif kind in ('ended','cancelled','error'):
                self.restore,self.active=self.active,None

    def poll(self):
        if not self.enabled and self.capture['status'] != 'recording':return
        try:
            self._connect()
            if self.transport is not None:
                self.transport.poll_imu()
                for sample in self.transport.drain_imu():
                    if self.clock()-sample.get('received_at', self.clock()) > .5:
                        self.suppression='stale_sample';continue
                    boot=sample.get('controller_boot')
                    if self.observed_boot is not None and boot != self.observed_boot and self.factory is None:
                        from .features.motion.imu_transport import validate_imu_install, imu_source_identity
                        binding=dict(validate_imu_install(), source_id=imu_source_identity())
                        self.transport.configure_imu(True,binding=binding)
                        self.monitor.bind(**self.transport.imu_binding)
                    self.observed_boot=boot
                    capture_key=(sample.get('controller_boot'),sample.get('sequence'),sample.get('timestamp_ms'))
                    if (self.capture['status'] == 'recording' and sample.get('status') == 'READY'
                            and capture_key != self.last_capture_sample):
                        self._capture_sample(sample, capture_key)
                    for event in self.monitor.ingest(sample, synthetic=False,
                            mode_generation=self.host.modes.generation):
                        self._event(event)
            for event in self.monitor.poll():self._event(event)
            if self.owned and self.transport is not None:
                status=self.transport.imu_transport_snapshot()
                if status.get('code') not in ('imu_not_requested','imu_snapshot_received'):
                    self.transport_code=status.get('code','imu_transport_error')
                    self.interrupt('imu_unavailable')
                    self.release_owned()
        except Exception as error:
            # Fixed safe enum, never raw sensor/serial/provider exception text.
            self.transport_code = getattr(error, 'code', 'imu_transport_unavailable')
            if not isinstance(self.transport_code, str) or len(self.transport_code) > 80:
                self.transport_code = 'imu_transport_unavailable'
            self.suppression = 'imu_unavailable'
            self.interrupt('imu_unavailable')
            if self.owned:
                try:self.release_owned()
                except Exception:pass
        blocked = self._blocked()
        own = bool(self.host._local_event_active and self.host._local_event_active.get('scope')=='imu')
        if self.host.audio.owner is not None and not own:self.last_audio = self.clock()
        if blocked and self.active:self.interrupt(blocked)
        if self.active and self.clock() >= self.active['deadline']:
            self.restore, self.active = self.active, None
        if not self.monitor.snapshot().get('current_capabilities', {}).get('relative_motion', False):
            if self.active:self.interrupt('signal_unavailable')

    def _capture_sample(self, sample, key):
        from .imu_profile import CaptureRecorder
        try:
            binding=dict(self.transport.imu_binding,sensor_id=sample['sensor_id'])
            if self.recorder is None:self.recorder=CaptureRecorder(binding)
            if self.capture['samples'] == 0:
                self.recorder.start_trial(self.capture['label'], self.capture['trial_id'])
            observation={name:sample[name] for name in ('controller_boot','sequence','timestamp_ms',
                'status','axes','accel_fs','gyro_fs','sensor_id')}
            self.recorder.append(observation,binding=binding,source='real_b1')
            self.last_capture_sample=key
            self.capture['samples']+=1
            self.capture['code']='imu_trial_recording'
        except Exception:
            if self.recorder is not None:self.recorder.cancel_trial()
            self.capture.update(status='failed',code='imu_trial_invalid_or_full')

    def project(self, value):
        # Projection preserves the session's mouth/caption/playback clock. No
        # new face geometry, direction guess or independent audio player.
        if value.get('emotion_mode') != 'auto':return value
        active = self.active
        if active and self.clock() < active['deadline']:
            if active['cached']:
                if value['turn_id'] != active['turn_id'] or value['playback'] != 'started':return value
            elif value['phase'] != 'idle':return value
            return dict(value, emotion='surprised')
        restore = self.restore
        if restore and value['turn_id'] == restore['turn_id']:
            return dict(value, emotion=restore['baseline'])
        return value

    def snapshot(self):
        value = self.monitor.snapshot()
        capability = value.get('current_capabilities', {})
        if self.enabled and self.clock() >= self.next_asset_check:
            self.next_asset_check = self.clock()+5.
            self.voice_available = any(self.host._cached_event_available(name)
                for name in ('reaction_gasp','reaction_interesting'))
        return dict(value, enabled=self.enabled,
            health=value.get('health', 'unverified'), reason_code=value.get('reason_code', self.profile_code),
            capabilities=dict(motion_reaction=bool(capability.get('relative_motion')), gaze=False,
                voice_reaction=self.voice_available),
            transport=dict(code=self.transport_code, shared_owner=not self.owned,
                motor_permission_granted=False), capture=dict(self.capture),
            diagnostic=dict(suppression=self.suppression, reactions_last_5_minutes=len(self.started),
                minimum_interval_seconds=30, maximum_per_5_minutes=2, directional_gaze_enabled=False))

    def close(self):
        self.interrupt('closed')
        self.release_owned()
        self.monitor.close()
