"""One media owner handles GP8 without microphone calls or held-key leakage."""
import time
import unittest

from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.features.reply import HostIntent
import test_music_directory_integration as _music_helpers


class MediaHostInputTests(unittest.TestCase):
    setUp = _music_helpers.MusicDirectoryIntegrationTests.setUp
    tearDown = _music_helpers.MusicDirectoryIntegrationTests.tearDown
    import_songs = _music_helpers.MusicDirectoryIntegrationTests.import_songs
    host = _music_helpers.MusicDirectoryIntegrationTests.host

    def start_music(self):
        self.import_songs(count=1)
        host=self.host()
        host.session.voice._player=lambda _cmd, *, on_start, cancel_check, **_kwargs:(cancel_check(),on_start(),cancel_check())
        host.execute(HostIntent('play_song','song_0'),request_id='input_music_start')
        self.assertEqual(host.media.snapshot()['state'],'playing')
        return host

    def test_direct_ptt_toggles_music_without_input_or_wake_and_talk_callback_is_valid(self):
        host=self.start_music()
        before=list(host.client.requests)
        self.assertFalse(host.ptt_button(_wake=True))
        self.assertEqual(host.media.snapshot()['state'],'playing')
        result=host.control(HostControl('ptt'),'input_direct_ptt')
        self.assertNotIn(result.status,('failed','rejected'))
        self.assertEqual(host.media.snapshot()['state'],'paused')
        self.assertEqual(host.audio.owner,'music')
        self.assertFalse(host.session.busy)
        self.clock+=.3
        self.assertTrue(host._talk_input())
        self.assertEqual(host.media.snapshot()['state'],'playing')
        self.assertEqual(host.client.requests,before)
        self.assertIsNone(host.microphone)

    def test_input_router_held_down_and_release_toggle_once_then_next_edge_can_resume(self):
        host=self.start_music()
        self.assertTrue(host.inputs.route('TALK',True,now=self.clock))
        self.assertEqual(host.media.snapshot()['state'],'paused')
        self.clock+=.7
        self.assertTrue(host.inputs.route('TALK',True,now=self.clock))
        self.assertEqual(host.media.snapshot()['state'],'paused')
        host.inputs.route('TALK',False,now=self.clock)
        self.assertEqual(host.media.snapshot()['state'],'paused')
        self.clock+=.3
        self.assertTrue(host.inputs.route('TALK',True,now=self.clock))
        self.assertEqual(host.media.snapshot()['state'],'playing')
        host.inputs.route('TALK',False,now=self.clock)
        self.assertEqual(host.client.requests,[])

    def test_media_back_and_late_keyup_do_not_start_ptt_but_fresh_question_works(self):
        host=self.start_music()
        host.inputs.route('TALK',True,now=self.clock)
        self.assertEqual(host.media.snapshot()['state'],'paused')
        host.execute(HostIntent('stop_media'),request_id='input_music_back')
        self.assertEqual(host.modes.mode,'conversation')
        self.assertIsNone(host.audio.owner)
        self.assertIsNone(host.media.snapshot()['active_id'])
        # A repeated down still belongs to the media press even after Back.
        host.control(HostControl('input',dict(key='TALK',pressed=True)),'input_late_music_held')
        self.assertFalse(host.session.busy)
        host.control(HostControl('input',dict(key='TALK',pressed=False)),'input_late_music_release')
        host.poll()
        self.assertFalse(host.session.busy)
        self.assertEqual(host.client.requests,[])
        self.assertTrue(host.start_question('A fresh explicit conversation after music.'))
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and host.session.busy:
            host.poll();time.sleep(.002)
        host.poll()
        self.assertFalse(host.session.busy)
        self.assertEqual(len(host.client.requests),1)
        self.assertEqual(host.modes.mode,'conversation')

    def test_control_input_held_after_back_cannot_open_configured_microphone(self):
        self.import_songs(count=1)
        opened=[]
        def source():
            opened.append(True)
            raise AssertionError('held media key must not open microphone')
        host=FeatureHost(runtime=RuntimePolicy(simulate=True,conversation_ptt_microphone=True),
            client=FeatureSimulatedClient(),clock=lambda:self.clock,
            music_directory=self.library,ptt_source_factory=source)
        self.hosts.append(host)
        host.execute(HostIntent('play_song','song_0'),request_id='input_live_mic_music')
        host.control(HostControl('input',dict(key='TALK',pressed=True)),'input_mic_music_pause')
        self.assertEqual(host.media.snapshot()['state'],'paused')
        host.execute(HostIntent('stop_media'),request_id='input_mic_music_back')
        self.clock+=.7
        host.control(HostControl('input',dict(key='TALK',pressed=True)),'input_mic_old_held')
        deadline=time.monotonic()+.3
        while time.monotonic()<deadline:
            host.poll();time.sleep(.002)
        self.assertEqual(opened,[])
        self.assertFalse(host.session.busy)
        self.assertEqual(host.client.requests,[])
        host.control(HostControl('input',dict(key='TALK',pressed=False)),'input_mic_old_release')


if __name__=='__main__':unittest.main()
