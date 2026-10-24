"""Staging frames only; no serial, firmware or real motion IO."""
import json
import unittest

from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.motion.protocol import Handshake, MotionAck, MotionCommand, FakeMotionTransport
from bmo_ai.features.motion.wire import encode_frame, decode_frame


class MotionWireTests(unittest.TestCase):
    def test_handshake_command_ack_roundtrip_with_and_without_crc(self):
        values = (Handshake(), MotionCommand("r1", "wave", "s1", "t1", 2, "boot1"), MotionAck("r1", "boot1"))
        for value in values:
            for crc in (False, True):
                with self.subTest(kind=type(value).__name__, crc=crc):
                    raw = encode_frame(value, crc=crc)
                    self.assertLessEqual(len(raw), 512)
                    self.assertEqual(decode_frame(raw, require_crc=crc), value)

    def test_corrupt_missing_crc_extra_fields_invalid_version_duplicates_and_bounds_rejected(self):
        raw = encode_frame(MotionAck("r1", "boot1"), crc=True)
        invalid = (raw.replace(b"boot1", b"boot2"), raw[:-1], b"\n"+raw,
                   raw.replace(b'"version":1', b'"version":true'), raw+b" "*513,
                   raw.replace(b'"kind":', b'"version":1,"kind":'),
                   raw.replace(b'"accepted":true', b'"accepted":"yes"'))
        for value in invalid:
            with self.subTest(value_length=len(value)), self.assertRaises(FeatureError):
                decode_frame(value)
        with self.assertRaises(FeatureError):
            decode_frame(encode_frame(MotionAck("r1", "boot1")), require_crc=True)
        for value in (Handshake(version=True), Handshake(channel="repl"),
                      Handshake(controller_dedup="yes"), MotionAck("../secret", "boot1")):
            with self.assertRaises(FeatureError):
                encode_frame(value)

    def test_crc_fake_delivery_is_deduplicated_and_reboot_never_replays(self):
        transport = FakeMotionTransport(wire_crc=True)
        command = MotionCommand("r1", "wave", "s1", "t1", 1, transport.handshake().controller_boot)
        transport.send(command); transport.send(command)
        self.assertEqual(transport.dispatch_count, 1)
        self.assertEqual(len(transport.poll()), 2)
        transport.reconnect(controller_boot="new_boot")
        with self.assertRaises(FeatureError):
            transport.send(command)
        self.assertEqual(transport.dispatch_count, 1)
        transport.close()


if __name__ == "__main__":
    unittest.main()
