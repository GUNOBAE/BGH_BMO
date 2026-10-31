"""Product video transaction on the existing camera and dialogue owners.

The recorder consumes the camera's bounded preview cache; this coordinator never
constructs another sensor or opens a microphone. File finalization stays in the
recorder worker and only its verified result may become a review.
"""
import math
from uuid import uuid4

from .contracts import FeatureError, HostResult


class ProductVideoFlow:
    @staticmethod
    def _video_busy(state):
        return (state.get('recording') is True or state.get('finalizing') is True
                or state.get('worker_alive') is True or state.get('phase') == 'starting')

    def _init_product_video(self, directory):
        self._video_flow_enabled = bool(self.character_life and directory is not None)
        self._video_phase = 'idle'
        self._video_pending = self._video_result = self._video_restore = None
        self._video_back_pending = False
        self._video_input_held = False
        self._video_feedback = None
        if self._video_flow_enabled:
            from .camera.compressed_video import CompressedVideoRecorder
            self.video = CompressedVideoRecorder(directory, frame_source=self.camera.cached_frame,
                clock=self.clock, simulated=self.runtime.simulate)

    def _enter_video(self, context):
        self._video_input_held = False
        self._video_restore = context['restore']
        self._video_result = self._video_feedback = None
        self._video_back_pending = False
        self._video_phase = 'preparing'
        self._video_pending = dict(request_id=context['request_id'],
            generation=self.modes.generation, duration=context['duration'],
            requested_seconds=context['requested_seconds'], duration_clamped=context['duration_clamped'],
            deadline=None, prepare_deadline=self.clock()+20., preview_token=None,
            camera_started=False)
        # A spoken request has already finished its preparation reply. Menu
        # requests use the same owned feedback route, without a second GPT call.
        if not context.get('prepared'):
            self._video_say('video_ready', "Hold still. Movie time!")

    def _video_say(self, event, text):
        if not self._start_owned_notice(event, text, {}, 'video_feedback'):
            return False
        self._video_feedback = dict(event=event, status='pending')
        return True

    def _start_video_flow(self, request, target=None):
        from .reply import video_request_duration
        self.runtime.require('camera_video')
        if not self._video_flow_enabled:
            raise FeatureError('video_storage_not_selected')
        if (self.modes.mode != 'conversation' or self.session.busy
                or self.audio.owner is not None or self._photo_job is not None):
            raise FeatureError('video_mode_busy')
        state = self.camera.snapshot()
        duration = video_request_duration(target)
        self._transition('video', dict(request_id=request,
            restore=(state.get('tracking_requested', state.get('tracking', False)), state.get('preview', False)),
            duration=duration['duration_seconds'], requested_seconds=duration['requested_seconds'],
            duration_clamped=duration['duration_clamped'],
            prepared=request.startswith('spoken_device_') and not self._cached_event_available('video_ready')))
        return HostResult(request, 'running', 'video_preparing', simulated=self.runtime.simulate,
                          executed=False, completed=False, completion='not_started')

    def _video_presented(self, value):
        pending = self._video_pending
        if (not pending or self.modes.mode != 'video' or self._video_phase != 'preparing'
                or value['generation'] != self.modes.generation
                or value['request_id'] != pending['request_id']
                or not pending['preview_token'] or value['preview_token'] != pending['preview_token']
                or not self.camera.snapshot()['preview_available']):
            raise FeatureError('stale_video_preview')
        pending['deadline'] = self.clock()+5.
        self._video_phase = 'countdown'

    def _video_stop_flow(self):
        pending = self._video_pending
        if not pending or self.modes.mode != 'video':
            raise FeatureError('video_not_recording')
        if self._video_phase in ('preparing', 'countdown'):
            return self._return_from_video()
        if self._video_phase in ('starting', 'recording'):
            self.video.request_stop(pending['request_id'], generation=pending['generation'])
            self._video_phase = 'finalizing'
        return True

    def _video_input(self, key, pressed, now):
        if key not in ('TALK', 'START', 'B', 'ESC'):
            return True
        if not pressed:
            self._video_input_held = False
            return True
        if self._video_input_held:
            return True
        self._video_input_held = True
        self._last_ptt_press = now
        if self._video_phase in ('starting', 'recording'):
            return self._video_stop_flow()
        if self._video_phase != 'finalizing':
            return self._return_from_video()
        return True

    def _return_from_video(self):
        if self.modes.mode != 'video':
            return False
        self._last_ptt_press = self.clock()
        self._video_back_pending = True
        if self.session.busy:
            self.cancel_conversation()
        state = self.video.snapshot()
        if self._video_busy(state):
            self.video.cancel('video_interrupted')
            self._video_phase = 'finalizing'
        elif not self.session.busy:
            self._transition('conversation')
        return True

    def _leave_video(self):
        state = self.video.snapshot()
        if self._video_busy(state):
            raise FeatureError('video_cleanup_pending')
        if self._video_pending and self._video_phase in ('preparing', 'countdown'):
            self._finish_result(HostResult(self._video_pending['request_id'], 'cancelled', 'video_cancelled'))
        self.camera.off()
        if self._transition_target == 'conversation' and self._video_restore:
            tracking, preview = self._video_restore
            self.camera.set_tracking(tracking, generation=self.modes.generation)
            self.camera.set_preview(preview, generation=self.modes.generation)
        self._video_restore = self._video_pending = None
        self._video_phase = 'idle'
        self._video_back_pending = False

    def _tick_video_flow(self):
        try:
            self._tick_video_flow_owned()
        except Exception as error:
            pending = self._video_pending
            if not pending:
                return
            code = error.code if isinstance(error, FeatureError) else 'video_operation_failed'
            if self._video_busy(self.video.snapshot()):
                self.video.cancel(code)
                self._video_phase = 'finalizing'
            else:
                self._video_finish(dict(request_id=pending['request_id'], status='failed',
                    code=code, simulated=self.runtime.simulate, executed=False, completed=False))

    def _tick_video_flow_owned(self):
        pending = self._video_pending
        if not pending or self.modes.mode != 'video':
            return
        if pending['generation'] != self.modes.generation:
            self._return_from_video()
            return
        camera = self.camera.snapshot()
        if self._video_phase == 'preparing' and not self.session.busy:
            if not pending['camera_started']:
                self.camera.suspend_tracking('video', generation=pending['generation'])
                self.camera.acquire_consumer('video', generation=pending['generation'])
                self.camera.set_preview(True, generation=pending['generation'])
                pending['camera_started'] = True
                camera = self.camera.snapshot()
            if camera.get('error') or self.clock() >= pending['prepare_deadline']:
                self._video_phase = 'failed'
                self._video_result = dict(status='failed', code='video_preview_unavailable',
                    completed=False, request_id=pending['request_id'])
                self._finish_result(HostResult(pending['request_id'], 'failed', 'video_preview_unavailable'))
                self.camera.off()
            elif camera.get('preview_available') and pending['preview_token'] is None:
                pending['preview_token'] = uuid4().hex
        elif self._video_phase == 'countdown' and self.clock() >= pending['deadline']:
            self._video_phase = 'starting'
            result = self.video.start(pending['request_id'], generation=pending['generation'],
                                     consent=True, duration=pending['duration'])
            self._active_video_request = pending['request_id']
            if result.get('status') in ('rejected', 'failed', 'unknown'):
                self._video_finish(result)
        if self._video_phase in ('starting', 'recording', 'finalizing'):
            state = self.video.poll(generation=pending['generation'])
            if state.get('finalizing'):
                self._video_phase = 'finalizing'
            elif state.get('recording') and self._video_phase != 'finalizing':
                self._video_phase = 'recording'
            result = state.get('result')
            if result and result.get('status') not in ('running', 'accepted') and not self._video_busy(state):
                self._video_finish(result)
        if self._video_back_pending and not self.session.busy:
            state = self.video.snapshot()
            if not self._video_busy(state):
                self._transition('conversation')

    def _video_finish(self, result):
        self._video_result = dict(result)
        self._active_video_request = None
        saved = result.get('completed') is True and bool(result.get('video_id'))
        self._video_phase = 'review' if saved else 'failed'
        self.camera.off()
        self._finish_result(self._video_host_result(result))
        if saved and not self._video_back_pending and result.get('status') in ('succeeded', 'simulated'):
            self._video_say('video_saved', 'Ta-da! Our little movie is saved.')

    def _video_flow_snapshot(self):
        pending = self._video_pending
        state = self.video.snapshot()
        countdown = (max(0., pending['deadline']-self.clock())
                     if pending and pending['deadline'] is not None else None)
        elapsed = state.get('elapsed', state.get('elapsed_seconds', 0.))
        return dict(video_phase=self._video_phase, video=self._video_result or state,
            video_request_id=pending['request_id'] if pending else None,
            video_preview_token=pending['preview_token'] if pending else None,
            video_countdown=countdown, video_elapsed=elapsed,
            video_saved_id=(self._video_result or {}).get('video_id'),
            video_duration=pending['duration'] if pending else 30,
            video_duration_clamped=pending['duration_clamped'] if pending else False,
            video_feedback=self._video_feedback)
