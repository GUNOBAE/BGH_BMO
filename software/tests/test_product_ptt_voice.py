"""Product PTT feedback and emotion-delivery options; offline and device-free."""
from pathlib import Path
import unittest

from bmo_ai import audio
from bmo_ai.desktop_app import parser, public_plan
from bmo_ai.features.host import FeatureHost
from bmo_ai.features.runtime import RuntimePolicy
from bmo_ai.integration_profile import integration_config
from bmo_ai.integration_voice import IntegrationVoice, synthetic_wav
from bmo_ai.voice_performance import fish_styled_text, EMOTION_DIRECTIONS


class ProductPttVoiceTests(unittest.TestCase):
    def test_fish_styles_only_for_opted_in_validated_expressions(self):
        plain = "Beemo is happy!"
        baseline = fish_styled_text(plain, emotion="happy", validation="validated")
        self.assertEqual(baseline, fish_styled_text(
            plain, emotion="happy", validation="validated", expressive=False))
        self.assertEqual(baseline, fish_styled_text(
            plain, emotion="crying", validation="degraded", expressive=True))
        self.assertEqual(baseline, fish_styled_text(
            plain, emotion="angry", validation="validated", expressive=True, notice=True))
        self.assertEqual(fish_styled_text(plain, soft=False), plain)
        for mood, instruction in EMOTION_DIRECTIONS.items():
            result = fish_styled_text(plain, emotion=mood,
                                      validation="validated", expressive=True)
            self.assertTrue(result.endswith(plain))
            self.assertIn(instruction, result)
            self.assertEqual(result.count(instruction), 1)
        self.assertNotIn("[", fish_styled_text(plain, expressive=True, soft=False,
                                               emotion="neutral", validation="validated"))
        self.assertIn("cute pout", fish_styled_text(
            plain, emotion="angry", validation="manual", expressive=True))
        with self.assertRaises(ValueError):
            fish_styled_text(plain, expressive=1)

    def test_real_synthesis_path_gets_one_allowlisted_style_without_extra_call(self):
        seen = []
        def fish(text, **kwargs):
            seen.append((text, kwargs))
            return synthetic_wav()

        config = integration_config()
        voice = IntegrationVoice(config, None, emit=lambda *a, **k: None,
                                 simulate=False, fish_synthesizer=fish,
                                 fish_api_key="fixture-only", expressive=True)
        voice.expression_metadata = {"emotion": "crying", "emotion_validation": "validated"}
        try:
            path, source, notice = voice._synthesize("BMO, I missed you.", lambda: None)
            try:
                self.assertEqual((source, notice), ("fish", False))
                self.assertEqual(len(seen), 1)
                self.assertIn("[softly tearful", seen[0][0])
                self.assertIn("Beemo", seen[0][0])
                self.assertEqual(seen[0][1]["reference_id"], config.fish_voice_id)
                self.assertEqual(seen[0][1]["model"], config.fish_model)
            finally:
                audio._discard(Path(path), suppress=True)
        finally:
            voice.close()

    def test_piper_input_remains_clean_even_if_expressive(self):
        received = []
        def local(text, **options):
            received.append(text)
            return synthetic_wav()
        config = integration_config()
        voice = IntegrationVoice(config, None, emit=lambda *a, **k: None,
                                 simulate=False, expressive=True, piper_synthesizer=local)
        voice.expression_metadata = {"emotion": "happy", "emotion_validation": "validated"}
        try:
            path, source, _ = voice._synthesize("Just a simple reply.",
                                                lambda: None, force_piper=True)
            audio._discard(Path(path), suppress=True)
            self.assertEqual(source, "piper")
            self.assertEqual(received, ["Just a simple reply."])
        finally:
            voice.close()

    def test_sensitive_vad_is_opt_in_and_never_opens_a_real_microphone(self):
        for profile in ("normal", "sensitive"):
            host = FeatureHost(runtime=RuntimePolicy(simulate=True,
                                      conversation_ptt_microphone=True),
                               ptt_vad_profile=profile, voice_expressive=True)
            try:
                self.assertFalse(host.microphone.snapshot()["microphone_open"])
                self.assertFalse(host.microphone.snapshot()["automatic_capture"])
                self.assertEqual(host.session.voice.expressive_voice, True)
                expected = .02 if profile == "normal" else .007
                self.assertEqual(host.microphone.settings["threshold"], expected)
                expected_timeout = 3.0 if profile == "normal" else 5.0
                self.assertEqual(host.microphone.settings["no_speech_timeout"], expected_timeout)
            finally:
                host.close()

    def test_desktop_options_and_no_speech_message_are_present(self):
        args = parser().parse_args(["--simulate", "--vad-profile", "sensitive",
                                    "--expressive-voice"])
        plan = public_plan(args)
        self.assertTrue(plan["expressive_voice"])
        self.assertEqual(plan["ptt"]["vad_profile"], "sensitive")
        browser = Path(__file__).resolve().parents[1] / "bmo_ai" / "desktop_web" / "desktop.js"
        content = browser.read_text(encoding="utf-8")
        self.assertIn("no_speech:", content)
        self.assertIn("calibration_noisy:", content)
        self.assertIn("reason_code", content)


if __name__ == "__main__":
    unittest.main()
