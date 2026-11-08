"""Product Host/Desktop retain music causes across real owner transitions.

Generated local PCM is consumed only by FakeMediaBackend. No speaker, camera,
microphone, paid provider, browser process or actual user diagnostic store opens.
"""
from unittest.mock import patch
import unittest

from bmo_ai.desktop_runtime import DesktopRuntime
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient, HostControl
from bmo_ai.features.reply import HostIntent
from bmo_ai.features.runtime import RuntimePolicy


REVISION = "a" * 40
RUN = "b" * 32


class MediaLifecycleHostTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.hosts, self.apps = [], []
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen",
                       "bmo_ai.audio.record_wav", "bmo_ai.audio.transcribe", "bmo_ai.audio._run",
                       "bmo_ai.fish_tts.synthesize_wav", "bmo_ai.cloud.create_client",
                       "bmo_ai.private_credentials.resolve_credentials",
                       "bmo_ai.media_diagnostics._open_directory"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO/store forbidden")))
        self.enterContext(patch("bmo_ai.completed_turn_diagnostics.source_revision", return_value=REVISION))

    def tearDown(self):
        for app in self.apps:
            app.close()
        for host in self.hosts:
            host.close()

    def host(self, *, bind=True):
        host = FeatureHost(demo_assets=True, clock=lambda: self.now,
                           runtime=RuntimePolicy(simulate=True), client=FeatureSimulatedClient())
        self.hosts.append(host)
        if bind:
            host.bind_runtime_identity(RUN)
        return host

    def start(self, host):
        result = host.execute(HostIntent("play_song", "fixture_song_a"), request_id="music_fixture")
        self.assertEqual(result.status, "simulated")
        self.assertEqual(host.media.snapshot()["state"], "playing")

    def command(self, host, action, source, value=None, *, generation=None):
        generation = host.media.snapshot()["playback_generation"] if generation is None else generation
        return host.control(HostControl("media_command", dict(action=action, source=source,
            generation=generation, value=value)), "ui_" + action + "_" + str(generation))

    def observation(self, host, event, *, hidden=False, focused=True, error=None, generation=None):
        generation = host.media.snapshot()["playback_generation"] if generation is None else generation
        return host.control(HostControl("media_observation", dict(event=event, generation=generation,
            hidden=hidden, focused=focused, error_code=error)), "observe_" + event)

    def events(self, host, event=None):
        values = host.media_diagnostics.snapshot()["events"]
        return [row for row in values if event is None or row["event"] == event]

    def test_explicit_pause_resume_seek_replay_keep_sources_and_playback_generation(self):
        host = self.host()
        self.start(host)
        initial = host.media.snapshot()["playback_generation"]
        self.assertTrue(self.command(host, "pause", "touch_pause").completed)
        pause = self.events(host, "pause")[-1]
        self.assertEqual((pause["runtime_revision"], pause["run_id"]), (REVISION, RUN))
        self.assertEqual((pause["catalog_id"], pause["variant"], pause["source"], pause["state"]),
                         ("fixture_song_a", "face", "touch_pause", "paused"))
        self.assertTrue(self.command(host, "seek", "touch_seek", .2).completed)
        current = host.media.snapshot()["playback_generation"]
        self.assertGreater(current, initial)
        # Mode generation stays put; a post-seek control belongs to the new
        # playback identity, while a late old video/key event must not act.
        self.assertNotEqual(current, host.modes.generation)
        stale = self.command(host, "resume", "gp8_short", generation=initial)
        self.assertEqual(stale.code, "stale_mode")
        self.assertEqual(host.media.snapshot()["state"], "paused")
        self.assertTrue(self.command(host, "resume", "gp8_short").completed)
        self.assertTrue(self.command(host, "replay", "touch_replay").completed)
        self.assertEqual([row["source"] for row in self.events(host)
                          if row["event"] in ("pause", "seek", "resume", "replay")],
                         ["touch_pause", "touch_seek", "gp8_short", "touch_replay"])
        self.assertEqual(host.audio.owner, "music")
        self.assertEqual(host.client.requests, [])

    def test_blur_hidden_are_observations_and_browser_failure_retains_original_code(self):
        host = self.host()
        self.start(host)
        for event, hidden, focused in (("blur", False, False), ("hidden", True, False),
                                       ("visible", False, True), ("focus", False, True)):
            self.observation(host, event, hidden=hidden, focused=focused)
            self.assertEqual(host.media.snapshot()["state"], "playing")
        self.observation(host, "video_error", error="decode")
        evidence = self.events(host, "lifecycle")[-1]
        self.assertEqual(evidence["error_code"], "video_decode_error")
        self.assertTrue(self.command(host, "stop", "video_error").completed)
        host.poll()
        terminal = self.events(host, "failed")
        self.assertEqual(len(terminal), 1)
        self.assertEqual((terminal[0]["source"], terminal[0]["error_code"]),
                         ("video_error", "video_decode_error"))
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)

    def test_backend_error_survives_cleanup_and_mode_transition_without_duplicate_terminal(self):
        host = self.host()
        self.start(host)
        backend = host.media.backend
        output = {"error_code": "media_output_underrun", "cleanup_error": None}
        original_stop = backend.stop

        def stop():
            original_stop()
            output["cleanup_error"] = "media_output_cleanup_failed"

        with patch.object(backend, "diagnostics", create=True, side_effect=lambda: dict(output)), \
                patch.object(backend, "stop", side_effect=stop):
            backend.fail()
            self.now += .1
            host.poll()
            host.poll()
        failures = self.events(host, "failed")
        self.assertEqual(len(failures), 1)
        self.assertEqual((failures[0]["error_code"], failures[0]["cleanup_error"]),
                         ("media_output_underrun", "media_output_cleanup_failed"))
        self.assertEqual(failures[0]["source"], "media_backend")
        self.assertAlmostEqual(failures[0]["position_seconds"], .1)
        self.assertEqual(failures[0]["duration_seconds"], .5)
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)
        self.assertIsNone(host.media.snapshot()["active_id"])

    def test_natural_completion_is_not_manual_stop_and_fires_once(self):
        host = self.host()
        self.start(host)
        self.now += .6
        host.poll()
        host.poll()
        completed = self.events(host, "completed")
        self.assertEqual(len(completed), 1)
        self.assertEqual((completed[0]["source"], completed[0]["state"]), ("media_backend", "ended"))
        self.assertIsNone(completed[0]["error_code"])
        self.assertEqual(self.events(host, "failed"), [])
        self.assertEqual(self.events(host, "cancelled"), [])
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)

    def test_unexpected_backend_exception_is_bounded_but_not_silently_classified_as_cancel(self):
        host = self.host()
        self.start(host)
        with patch.object(host.media.backend, "poll", side_effect=RuntimeError("private-path-and-token")):
            host.poll()
        failed = self.events(host, "failed")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["error_code"], "media_poll_failed")
        self.assertNotIn("private-path-and-token", repr(host.media_diagnostics.snapshot()))
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)

    def test_manual_back_retains_source_after_cancellation_and_owner_release(self):
        host = self.host()
        self.start(host)
        self.assertTrue(self.command(host, "stop", "touch_back").completed)
        host.poll()
        cancelled = self.events(host, "cancelled")
        self.assertEqual(len(cancelled), 1)
        self.assertEqual((cancelled[0]["source"], cancelled[0]["catalog_id"]),
                         ("touch_back", "fixture_song_a"))
        self.assertEqual(self.events(host, "completed"), [])
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)

    def test_shell_timeout_records_pre_cleanup_identity_and_actual_heartbeat_age(self):
        host = self.host()
        self.start(host)
        self.command(host, "pause", "touch_pause")  # Remains active beyond short fixture duration.
        host.heartbeat()
        self.now += 3.1
        host.poll()
        expired = self.events(host, "heartbeat_expired")
        self.assertEqual(len(expired), 1)
        receipt = expired[0]
        self.assertEqual((receipt["source"], receipt["catalog_id"], receipt["state"]),
                         ("shell_heartbeat_expired", "fixture_song_a", "paused"))
        self.assertFalse(receipt["lifecycle"]["connected"])
        self.assertAlmostEqual(receipt["lifecycle"]["heartbeat_age_seconds"], 3.1)
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)
        host.poll()
        self.assertEqual(len(self.events(host, "heartbeat_expired")), 1)

    def test_desktop_runtime_binds_run_and_records_disconnect_before_host_cleanup(self):
        host = self.host(bind=False)
        # Desktop owns one identity for this Host; bind it before music starts.
        app = DesktopRuntime(clock=lambda: self.now, simulate=True, silent_start=True,
                             startup_timeout=120., disconnect_timeout=2.)
        self.apps.append(app)
        app.set_host(host, None)
        self.start(host)
        self.command(host, "pause", "touch_pause")
        app.heartbeat()
        self.now += 2.1  # Desktop timeout precedes the Host's independent 3s bound.
        app.poll()
        expired = self.events(host, "heartbeat_expired")
        self.assertEqual(len(expired), 1)
        self.assertEqual((expired[0]["source"], expired[0]["run_id"], expired[0]["catalog_id"]),
                         ("browser_disconnected", app.run_id, "fixture_song_a"))
        self.assertFalse(expired[0]["lifecycle"]["connected"])
        self.assertAlmostEqual(expired[0]["lifecycle"]["heartbeat_age_seconds"], 2.1)
        self.assertEqual(app.startup_snapshot()["code"], "browser_disconnected")
        self.assertEqual(host.modes.mode, "conversation")
        self.assertIsNone(host.audio.owner)


if __name__ == "__main__":
    unittest.main()
