import math
from pathlib import Path
import random
import struct
import tempfile
import unittest
import wave

from bmo_ai.device_acceptance.loopback import analyze_loopback


class LoopbackEvidenceTests(unittest.TestCase):
    def setUp(self):self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
    def wav(self,name,*,tone=False,zero=False):
        randomizer=random.Random(82)
        samples=[]
        for i in range(16000):
            value=0 if zero else randomizer.randint(-40,40)
            if tone and 4000<=i<13600:value+=round(400*math.sin(2*math.pi*1200*i/16000))
            samples.append(value)
        path=self.root/name
        with wave.open(str(path),"wb") as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
            output.writeframes(struct.pack("<"+"h"*len(samples),*samples))
        return path
    def test_distinct_fixed_tone_is_evidence_only_not_latency_or_listening_approval(self):
        r=analyze_loopback(self.wav("baseline.wav"),self.wav("tone.wav",tone=True))
        self.assertEqual(r["status"],"LOOPBACK_PASS")
        self.assertEqual(r["acoustic_latency_ms"],"UNMEASURED")
        self.assertEqual(r["human_listening"],"NOT_APPROVED")
        self.assertEqual(r["wake_model_inference"],"NOT_RUN")
    def test_same_ambient_is_not_promoted_to_loopback_pass(self):
        r=analyze_loopback(self.wav("baseline.wav"),self.wav("ambient.wav"))
        self.assertEqual(r["status"],"LOOPBACK_NOT_VERIFIED")
    def test_zero_input_is_failure_not_quiet_room_success(self):
        r=analyze_loopback(self.wav("zero.wav",zero=True),self.wav("tone.wav",tone=True))
        self.assertEqual(r["status"],"LOOPBACK_NOT_VERIFIED")
        self.assertEqual(r["code"],"empty_or_all_zero_pcm")


if __name__=="__main__":unittest.main()
