import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave

from bmo_ai.device_acceptance.microphone import AcceptanceError
from bmo_ai.device_acceptance.speaker import run_speaker_acceptance


class SpeakerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No actual player")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("No network")))
        self.enterContext(patch("bmo_ai.private_credentials.resolve_credentials", side_effect=AssertionError("No keys")))

    def test_default_is_plan_only_without_directory_or_player(self):
        result = run_speaker_acceptance()
        self.assertEqual(result["status"], "PLAN_ONLY")
        self.assertEqual((result["fixed_gain"], result["maximum_player_commands"]), (.25, 1))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_one_bounded_owned_player_and_gain_are_distinct_from_acoustic_pass(self):
        calls=[]
        def player(command, *, on_start, cancel_check, timeout, **_settings):
            cancel_check(); on_start();calls.append(command)
            self.assertLessEqual(timeout, 3.)
            with wave.open(command[-1]) as source:
                values=struct.unpack("<"+"h"*source.getnframes(), source.readframes(source.getnframes()))
                self.assertLessEqual(max(abs(v) for v in values), 656)
                self.assertEqual((source.getframerate(),source.getnchannels()),(16000,1))
        result=run_speaker_acceptance(self.root, live_acceptance=True, player=player)
        self.assertEqual(result["status"], "SPEAKER_COMMAND_PASS")
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],"/usr/bin/aplay")
        self.assertEqual(result["acoustic_result"], "NOT_MEASURED")
        self.assertTrue(result["player_reaped"] and result["voice_closed"] and result["audio_focus_released"])
        for path in self.root.iterdir():self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_existing_receipt_refuses_another_play(self):
        def player(*_a, on_start, **_kw):on_start()
        run_speaker_acceptance(self.root, live_acceptance=True, player=player)
        with self.assertRaises(AcceptanceError):
            run_speaker_acceptance(self.root, live_acceptance=True, player=player)

    def test_failure_is_not_retried_or_called_an_acoustic_success(self):
        calls=[]
        def player(*_a, **_kw):calls.append(1);raise OSError("untrusted detail")
        result=run_speaker_acceptance(self.root, live_acceptance=True, player=player)
        self.assertEqual(result["status"], "SPEAKER_COMMAND_FAILED")
        self.assertEqual(len(calls),1)
        self.assertNotIn("untrusted detail",json.dumps(result))
        self.assertEqual(result["acoustic_result"],"NOT_MEASURED")
        self.assertTrue(result["audio_focus_released"])


if __name__ == "__main__":unittest.main()
