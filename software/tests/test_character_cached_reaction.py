"""Real WAV composition and owned playback with synthetic private fixtures.

The injected synthesizer returns local PCM. No provider, microphone, ALSA,
speaker, firmware or listening approval operation occurs in these tests.
"""
from pathlib import Path
import struct
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import wave
from dataclasses import replace

from bmo_ai import audio
from bmo_ai.character_director import CharacterDirector
from bmo_ai.conversation import TurnCancelled
from bmo_ai.integration_face import wav_cues
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice
from bmo_ai.voice_bank import VoiceBank


class CharacterCachedReactionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.now = 10000.
        self.config = integration_config()
        self.voices, self.paths = [], []
        for target in ("socket.socket.connect", "subprocess.Popen", "bmo_ai.cloud.create_client",
                "bmo_ai.private_credentials.resolve_credentials", "bmo_ai.fish_tts.synthesize_wav",
                "bmo_ai.local_piper.synthesize_wav"):
            self.enterContext(patch(target, side_effect=AssertionError("Real IO forbidden")))

    def tearDown(self):
        for voice in self.voices: voice.close()
        for path in self.paths: audio._discard(path, suppress=True)

    def pcm(self, amplitude=12000, *, rate=16000):
        path = audio._new_wav(audio.AudioSynthesisError);self.paths.append(path)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate)
            output.writeframes(struct.pack("<h", amplitude)*round(.2*rate))
        return path

    def bank(self, clips=(), *, approved=True):
        bank = VoiceBank(self.root/str(len(self.voices))/"bank", self.config,
                         clock=lambda: self.now, chooser=lambda values: values[0], simulate=True)
        for clip in clips:
            result = bank.publish(clip, self.pcm(), simulated=True)
            if approved: bank.approve(clip, result["sha256"], listening_approved=True)
        return bank

    def voice(self, bank, *, rate=16000, cancel=None):
        seen, events, played = [], [], []
        def player(_command, *, on_start, cancel_check, **_settings):
            cancel_check();on_start()
            if cancel is not None and cancel.is_set(): raise TurnCancelled()
            cancel_check()
        voice = IntegrationVoice(self.config, None, simulate=True, expressive=True, character_life=True,
            player=player, emit=lambda kind, **fields: events.append((kind, fields)))
        # Explicit local provider boundary: keeps actual WAV/gain/cue/player
        # integration while exercising the logical Fish success/quota branch.
        def synth(text, check, **settings):
            check();seen.append(settings["delivery"])
            return self.pcm(8000, rate=rate), "fish", False
        voice._synthesize = synth;voice.character_bank = bank
        self.voices.append(voice)
        return voice, seen, events

    def plan(self, voice, reaction="chuckle"):
        emotion = {"chuckle": "happy", "gasp": "surprised", "playful_puff": "angry", "sob": "crying"}[reaction]
        message = "Please pretend to cry." if reaction == "sob" else "Playfully act angry." if reaction == "playful_puff" else ""
        plan = CharacterDirector().resolve(emotion, reaction,
            metadata=dict(intensity="medium"), message=message)
        voice.expression_metadata = dict(emotion=plan.face_emotion, emotion_validation="validated",
            reaction=plan.reaction_id, character_plan=plan.public())

    def test_all_four_reaction_mappings_use_one_final_wav_and_single_start(self):
        for reaction, clip, cue in (("chuckle", "reaction_03", "[chuckling]"),
                ("gasp", "reaction_05", "[gasping]"),
                ("playful_puff", "reaction_07", "[sighing, brief playful puff]"),
                ("sob", "reaction_11", "[sobbing, one short breath]")):
            with self.subTest(reaction=reaction):
                bank = self.bank([clip]);voice, seen, events = self.voice(bank);self.plan(voice, reaction)
                starts = []
                with patch.object(bank, "completed", wraps=bank.completed) as completed:
                    voice.speak("A clean short response.", on_playback_start=lambda: starts.append(1))
                    self.assertEqual([kind for kind, _ in events], ["prepared", "started", "ended"])
                    self.assertEqual(len(starts), 1);self.assertEqual(len(seen), 1)
                    self.assertNotIn(cue, seen[0].text)
                    metadata = voice._cache["expression"]
                    self.assertEqual((metadata["reaction"], metadata["cached_reaction_id"]), (reaction, clip))
                    self.assertTrue(metadata["cached_reaction"])
                    self.assertAlmostEqual(voice._cache["info"]["duration_seconds"], .4)
                    completed.assert_called_once_with(clip)

    def test_prefix_and_answer_are_gained_exactly_once_with_cues_for_final_pcm(self):
        voice, _seen, _events = self.voice(self.bank(["reaction_03"]));self.plan(voice)
        voice.speak("A short answer.")
        cache = voice._cache
        with wave.open(str(cache["path"]), "rb") as source:
            samples = struct.unpack("<"+"h"*source.getnframes(), source.readframes(source.getnframes()))
        self.assertEqual(set(samples[:3200]), {3000})
        self.assertEqual(set(samples[3200:]), {2000})
        self.assertEqual(list(cache["cues"]), wav_cues(cache["path"]))

    def test_repeat_reuses_combined_wav_without_new_selection_synthesis_or_history_charge(self):
        bank = self.bank(["reaction_03"]);voice, seen, _events = self.voice(bank);self.plan(voice)
        with patch.object(bank, "select", wraps=bank.select) as select, \
             patch.object(bank, "completed", wraps=bank.completed) as completed:
            voice.speak("A short answer.");path = voice._cache["path"];payload = path.read_bytes()
            self.assertTrue(voice.repeat())
            self.assertEqual(path.read_bytes(), payload);self.assertEqual(voice._cache["path"], path)
            self.assertEqual(len(seen), 1);self.assertEqual(select.call_count, 1);self.assertEqual(completed.call_count, 1)

    def test_missing_unapproved_or_corrupt_cache_keeps_original_live_reaction_direction(self):
        for status in ("missing", "unapproved", "corrupt"):
            with self.subTest(status=status):
                bank = self.bank([] if status == "missing" else ["reaction_03"], approved=status != "unapproved")
                if status == "corrupt":
                    bank.inventory()
                    (bank.directory/self.config.fish_model/"reaction_03"/"greeting.cache").write_bytes(b"invalid private fixture")
                voice, seen, _events = self.voice(bank);self.plan(voice)
                with patch.object(bank, "completed", wraps=bank.completed) as completed:
                    voice.speak("A short answer.")
                    self.assertIn("[chuckling]", seen[0].text)
                    self.assertFalse(voice._cache["expression"]["cached_reaction"])
                    self.assertIsNone(voice._cache["expression"]["cached_reaction_id"])
                    self.assertEqual(voice._cache["expression"]["reaction"], "chuckle")
                    completed.assert_not_called()

    def test_pcm_mismatch_keeps_emotion_without_claiming_reaction_or_second_synthesis(self):
        bank = self.bank(["reaction_03"]);voice, seen, _events = self.voice(bank, rate=24000);self.plan(voice)
        with patch.object(bank, "completed", wraps=bank.completed) as completed:
            voice.speak("A short answer.")
            self.assertEqual(len(seen), 1);self.assertNotIn("[chuckling]", seen[0].text)
            metadata = voice._cache["expression"]
            self.assertEqual((metadata["voice_emotion"], metadata["reaction"]), ("happy", "none"))
            self.assertFalse(metadata["cached_reaction"]);self.assertIsNone(metadata["cached_reaction_id"])
            self.assertAlmostEqual(voice._cache["info"]["duration_seconds"], .2)
            completed.assert_not_called()

    def test_mismatching_bank_gain_or_identity_never_reuses_a_louder_or_different_clip(self):
        for changes in (dict(fish_gain=.1), dict(fish_voice_id="0"*32),
                        dict(fish_name_mode="literal"), dict(fish_delivery="plain")):
            with self.subTest(changes=changes):
                bank = self.bank(["reaction_03"]);voice, seen, _events = self.voice(bank)
                voice.config = replace(self.config, **changes);self.plan(voice)
                with patch.object(bank, "select", wraps=bank.select) as select:
                    voice.speak("A short answer.")
                    self.assertFalse(voice._cache["expression"]["cached_reaction"])
                    self.assertIn("[chuckling]", seen[0].text)
                    select.assert_not_called()

    def test_existing_quota_cooldown_and_explicit_acting_authorization_remain_active(self):
        bank = self.bank(["reaction_03", "reaction_11"]);voice, seen, _events = self.voice(bank)
        self.plan(voice);voice.speak("A short answer.")
        self.plan(voice);voice.speak("Another short answer.")
        self.assertFalse(voice._cache["expression"]["cached_reaction"])
        self.assertEqual(voice._cache["expression"]["reaction"], "none")
        self.plan(voice, "sob");voice.speak("A requested little acting scene.")
        self.assertEqual(voice._cache["expression"]["cached_reaction_id"], "reaction_11")
        self.plan(voice, "sob");voice.speak("A second explicitly requested scene.")
        self.assertFalse(voice._cache["expression"]["cached_reaction"])
        self.assertIn("[sobbing, one short breath]", seen[-1].text)  # Cache cooldown; live requested acting remains.
        self.now += 301
        self.plan(voice, "sob");voice.speak("A later explicitly requested scene.")
        self.assertTrue(voice._cache["expression"]["cached_reaction"])

    def test_cancelled_combined_playback_keeps_prior_answer_and_does_not_charge_bank(self):
        bank = self.bank(["reaction_03"]);cancel = Event();voice, _seen, events = self.voice(bank, cancel=cancel)
        voice.expression_metadata = dict(emotion="neutral", emotion_validation="validated")
        voice.speak("Prior answer.");prior = dict(voice._cache)
        self.plan(voice);cancel.set()
        with patch.object(bank, "completed", wraps=bank.completed) as completed:
            with self.assertRaises(TurnCancelled): voice.speak("A cancelled answer.")
            completed.assert_not_called()
        self.assertEqual(voice._cache, prior);self.assertTrue(prior["path"].is_file())
        cancelled_path = Path(next(fields["path"] for kind, fields in reversed(events) if kind == "prepared"))
        self.assertFalse(cancelled_path.exists())


if __name__ == "__main__": unittest.main()
