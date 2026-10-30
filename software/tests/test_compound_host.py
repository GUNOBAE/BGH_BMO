"""Actual semantic/session/Host bounded action dispatch with injected IO only."""
import time
from threading import Event
import unittest
from unittest.mock import patch

from bmo_ai.features.contracts import HostResult
from bmo_ai.features.host import HostControl
from bmo_ai.features.reply import HostIntent
from test_compound_requests import compound, step
import test_full_batch_semantic_host as _host_helpers
from test_semantic_requests import envelope


class CompoundHostTests(unittest.TestCase):
    setUp = _host_helpers.FullBatchSemanticHostTests.setUp
    tearDown = _host_helpers.FullBatchSemanticHostTests.tearDown
    make_host = _host_helpers.FullBatchSemanticHostTests.make_host
    finish = _host_helpers.FullBatchSemanticHostTests.finish
    ask = _host_helpers.FullBatchSemanticHostTests.ask

    def drive(self, host, predicate):
        self.finish(host, predicate)
        return host.snapshot()

    def test_one_reply_stops_tracking_before_video_snapshot_and_gp8_back_keeps_it_off(self):
        host, provider, clock, played = self.make_host([
            compound(step('stop_face_tracking'), step('start_video', 'seconds_5'))],
            camera=True, video=True)
        host.execute(HostIntent('start_face_tracking'), request_id='menu_start_tracking')
        host.poll()
        self.assertTrue(host.snapshot()['camera']['tracking_requested'])
        dispatched=[]
        original=host.execute
        def observe(intent, **kwargs):
            if kwargs.get('request_id','').startswith('spoken_device_'):
                dispatched.append((intent,host.camera.snapshot()['tracking_requested'],kwargs))
            return original(intent,**kwargs)
        with patch.object(host,'execute',side_effect=observe):
            self.assertTrue(host.start_question('Stop following my face and record a five-second video.'))
            state=self.drive(host,lambda: host.snapshot()['mode']=='video' and bool(host.snapshot()['video_preview_token']))
            self.assertEqual([row[0].name for row in dispatched],['stop_face_tracking','start_video'])
            self.assertEqual([row[1] for row in dispatched],[True,False])
            self.assertNotEqual(dispatched[0][2]['request_id'],dispatched[1][2]['request_id'])
            self.assertFalse(state['camera']['tracking_requested'])
            self.assertEqual(host._video_restore[0],False)
            self.assertEqual(len(provider.requests),1)
            self.assertEqual(len(played),1)
            host.control(HostControl('video_preview_ready',dict(generation=state['mode_generation'],
                request_id=state['video_request_id'],preview_token=state['video_preview_token'])),'compound_visible_preview')
            clock.offset+=4.9
            host.poll()
            self.assertEqual(host.snapshot()['video_phase'],'countdown')
            host.control(HostControl('input',dict(key='TALK',pressed=True)),'compound_gp8_back')
            host.control(HostControl('input',dict(key='TALK',pressed=False)),'compound_gp8_back_release')
            state=self.drive(host,lambda: host.snapshot()['mode']=='conversation')
            self.assertFalse(state['camera']['tracking_requested'])
            self.assertFalse(state['camera']['tracking'])
            self.assertFalse(state['camera_video']['recording'])
            self.assertIsNone(host.compound.active)
            for _ in range(3):host.poll()
            self.assertEqual(len(dispatched),2)
            self.assertEqual(len(provider.requests),1)

    def test_first_failure_prevents_later_volume_change(self):
        host, provider, _, _ = self.make_host([
            compound(step('arm_gesture','wave'),step('set_volume','level_100'))])
        self.assertTrue(host.start_question('Wave your arms and turn the volume to one hundred.'))
        self.drive(host,lambda: host.compound.last is not None)
        plan=host.compound.snapshot()
        self.assertEqual(plan['status'],'failed')
        self.assertEqual(plan['code'],'capability_denied')
        self.assertEqual(len(plan['results']),1)
        self.assertEqual(host.volume.snapshot()['level'],.25)
        self.assertEqual(len(provider.requests),1)
        self.assertEqual(sum(key.startswith('spoken_device_') for key in host._result_history),1)

    def pending_plan(self, *, replies=None):
        host, provider, clock, played=self.make_host(replies or [
            compound(step('stop_arms'),step('set_volume','level_100'))],arms=True)
        # Exercise real model/session admission and B1 stop, but hold its
        # completion receipt as position-unverified so the tail must wait.
        self.assertTrue(host.start_question('Stop your arms and set volume to one hundred.'))
        self.drive(host,lambda:host.compound.active is not None and host.compound.active['request_id'] is not None)
        self.assertEqual(host.compound.active['index'],0)
        self.assertEqual(host.volume.snapshot()['level'],.25)
        return host,provider,clock,played

    def test_pending_receipt_waits_then_exact_completed_receipt_dispatches_tail_once(self):
        host,provider,_,_=self.pending_plan()
        request=host.compound.active['request_id']
        for _ in range(5):host.poll()
        self.assertEqual(host.compound.active['index'],0)
        host._remember(HostResult(request,'succeeded','arm_stop_requested',simulated=False,
            completed=True,completion='resources_stopped'))
        self.drive(host,lambda:host.compound.last is not None)
        plan=host.compound.snapshot()
        self.assertEqual(plan['status'],'succeeded')
        self.assertEqual(len(plan['results']),2)
        self.assertEqual(host.volume.snapshot()['level'],1)
        self.assertEqual(len(provider.requests),1)
        request_ids=[row['request_id'] for row in plan['results']]
        self.assertEqual(len(set(request_ids)),2)
        self.assertFalse(host.compound.begin(plan['parent_turn'],tuple([HostIntent('volume_down')]),
            generation=host.modes.generation))
        for _ in range(5):host.poll()
        self.assertEqual(host.volume.snapshot()['level'],1)

    def test_cancel_repeat_mode_change_and_deadline_retire_tail_even_after_late_success(self):
        for action in ('cancel','repeat','mode_change','deadline'):
            with self.subTest(action=action):
                host,provider,clock,_=self.pending_plan()
                request=host.compound.active['request_id']
                if action in ('cancel','repeat'):
                    host.control(HostControl(action),'compound_retire_'+action)
                elif action=='mode_change':
                    host.execute(HostIntent('open_game_menu'),request_id='compound_other_mode')
                else:
                    clock.offset+=181
                host.poll()
                self.assertIsNone(host.compound.active)
                host._remember(HostResult(request,'succeeded','arm_stop_requested',simulated=False,
                    completed=True,completion='resources_stopped'))
                self.drive(host,lambda:not host.session.busy)
                for _ in range(3):host.poll()
                self.assertEqual(host.volume.snapshot()['level'],.25)
                self.assertEqual(len(provider.requests),1)

    def test_new_question_retires_previous_pending_tail(self):
        host,provider,_,_=self.pending_plan(replies=[
            compound(step('stop_arms'),step('set_volume','level_100')),envelope()])
        request=host.compound.active['request_id']
        self.assertTrue(host.start_question('What is two plus two?'))
        self.drive(host,lambda:not host.session.busy and host.audio.owner is None)
        host._remember(HostResult(request,'succeeded','arm_stop_requested',simulated=False,
            completed=True,completion='resources_stopped'))
        for _ in range(4):host.poll()
        self.assertIsNone(host.compound.active)
        self.assertEqual(host.volume.snapshot()['level'],.25)
        self.assertEqual(len(provider.requests),2)

    def test_cancel_before_preparation_finishes_cannot_create_plan_from_late_voice_events(self):
        started,release=Event(),Event()
        self.addCleanup(release.set)
        def player(_command,*,on_start,cancel_check,**_kwargs):
            on_start();started.set()
            deadline=time.monotonic()+3
            while not release.wait(.002):
                cancel_check()
                if time.monotonic()>=deadline:raise AssertionError('injected player release deadline')
            cancel_check()
        host,provider,_,_=self.make_host([
            compound(step('stop_face_tracking'),step('start_video'))],camera=True,video=True,player=player)
        host.execute(HostIntent('start_face_tracking'),request_id='preparation_existing_tracking')
        self.assertTrue(host.start_question('Stop tracking and record a video.'))
        self.assertTrue(started.wait(2))
        host.poll()
        host.control(HostControl('cancel'),'compound_cancel_reply')
        release.set()
        self.drive(host,lambda:not host.session.busy and host.audio.owner is None)
        for _ in range(4):host.poll()
        self.assertIsNone(host.compound.active)
        self.assertEqual(host.snapshot()['mode'],'conversation')
        self.assertFalse(any(key.startswith('spoken_device_') for key in host._result_history))
        self.assertEqual(len(provider.requests),1)


if __name__=='__main__':unittest.main()
