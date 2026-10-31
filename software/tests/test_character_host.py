"""The production Host/session/director/voice path, with IO boundaries replaced."""
from threading import Event
import time
import unittest
from unittest.mock import patch

from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice


class CharacterHostTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.Popen", "socket.socket.connect", "socket.socket.connect_ex",
                       "bmo_ai.cloud.create_client", "bmo_ai.fish_tts.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))
        self.prepared, self.begin, self.started, self.release = Event(), Event(), Event(), Event()
        self.host = None

    def tearDown(self):
        self.begin.set(); self.release.set()
        if self.host is not None:
            self.host.close()

    def make_host(self, emotion="sad", reaction="none", gesture="none"):
        config = integration_config()
        client = FeatureSimulatedClient([dict(version=3, speech="A short synthetic response.",
            emotion=emotion, gesture=gesture, intent=None, reaction=reaction,
            character=dict(delivery_id="soft_sad" if emotion == "sad" else "warm",
                           intensity="medium", gaze_id="curious", source="dialogue"))])
        def player(_command, *, on_start, cancel_check, **_kwargs):
            self.prepared.set()
            deadline = time.monotonic() + 4
            while not self.begin.wait(.002):
                cancel_check()
                if time.monotonic() > deadline: raise AssertionError("Synthetic start gate timed out")
            on_start(); self.started.set()
            while not self.release.wait(.002):
                cancel_check()
                if time.monotonic() > deadline: raise AssertionError("Synthetic playback gate timed out")
            cancel_check()
        voice = IntegrationVoice(config, client, simulate=True, expressive=True,
                                  character_life=True, player=player, emit=lambda *_a, **_kw: None)
        self.host = FeatureHost(config=config, client=client, voice=voice,
                                character_life=True, expressive_voice=True)
        return client

    def pump(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            self.host.heartbeat(); self.host.poll(); time.sleep(.002)
        self.assertTrue(predicate(), "Production character pipeline did not reach its boundary")

    def test_acting_face_changes_at_player_start_and_repeat_has_no_new_provider_or_motion(self):
        client = self.make_host()
        self.host.session.set_emotion("crying"); self.host.session.set_emotion("auto")
        self.assertTrue(self.host.start_question("장난스럽게 화내 봐"))
        self.assertTrue(self.prepared.wait(2))
        self.assertEqual(self.host.session.snapshot()["emotion"], "crying")
        self.assertIsNone(self.host.snapshot()["character_life"]["last_played_plan"])
        self.begin.set(); self.assertTrue(self.started.wait(2))
        self.pump(lambda: self.host.snapshot()["character_life"]["last_played_plan"] is not None)
        plan = self.host.snapshot()["character_life"]["last_played_plan"]
        self.assertEqual((plan["face_emotion"], plan["delivery_id"]), ("angry", "theatrical_angry"))
        self.assertEqual(self.host.session.snapshot()["emotion"], "angry")
        self.assertEqual(self.host.motion.snapshot()["queued"], 0)
        self.release.set(); self.pump(lambda: not self.host.session.busy)
        self.host.control("repeat", request="repeatCharacter")
        self.pump(lambda: not self.host.session.busy)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(self.host.session.snapshot()["emotion"], "angry")
        self.assertEqual(self.host.snapshot()["character_life"]["last_played_plan"], plan)

    def test_real_distress_suppresses_comic_reaction_and_body_flourish(self):
        self.make_host("happy", "chuckle", "celebrate")
        self.assertTrue(self.host.start_question("My mother died and I am grieving."))
        self.assertTrue(self.prepared.wait(2)); self.begin.set(); self.assertTrue(self.started.wait(2))
        self.pump(lambda: self.host.snapshot()["character_life"]["last_played_plan"] is not None)
        plan = self.host.snapshot()["character_life"]["last_played_plan"]
        self.assertTrue(plan["sensitive_context"])
        self.assertEqual((plan["face_emotion"], plan["reaction_id"], plan["gesture_id"]),
                         ("sad", "none", "none"))
        self.assertFalse(self.host.camera.snapshot()["on"])
        self.assertFalse(self.host.motion.snapshot()["active"])

    def test_dialogue_gaze_precedes_camera_only_while_speaking_and_native_input_wins(self):
        self.make_host()
        self.assertTrue(self.host.start_question("우는 연기를 해봐"))
        self.assertTrue(self.prepared.wait(2))
        self.host.gaze.offer("camera", .7, .5, generation=self.host.modes.generation,
                             expires_at=time.monotonic() + 10)
        self.assertEqual(self.host.gaze.resolve(phase="thinking", generation=self.host.modes.generation)["owner"], "native")
        self.begin.set(); self.assertTrue(self.started.wait(2))
        self.pump(lambda: self.host.snapshot()["character_life"]["last_played_plan"] is not None)
        self.assertEqual(self.host.gaze.resolve(phase="speaking", generation=self.host.modes.generation)["owner"], "dialogue")
        self.assertEqual(self.host.gaze.resolve(phase="idle", generation=self.host.modes.generation)["owner"], "camera")


if __name__ == "__main__": unittest.main()
