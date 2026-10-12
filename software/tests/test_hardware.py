import json
import unittest
from unittest.mock import patch

from bmo_ai.hardware import (
    ArmMovementDisabledError,
    ButtonEvent,
    DeniedArmController,
    FaceState,
    Heartbeat,
    IMUSample,
    MAX_CHUNK_BYTES,
    MAX_FRAME_BYTES,
    NullFace,
    TelemetryDecoder,
    TelemetryError,
    TelemetryMonitor,
    parse_telemetry,
)


def frame(kind="heartbeat", sequence=1, **extra):
    value = {"v": 1, "type": kind, "seq": sequence, "timestamp_ms": 1234}
    value.update(extra)
    return json.dumps(value, separators=(",", ":")).encode()


class HardwareSafetyTests(unittest.TestCase):
    def test_face_states_do_not_touch_devices(self):
        with patch("builtins.open", side_effect=AssertionError("device opened")):
            face = NullFace()
            self.assertEqual(face.state, FaceState.IDLE)
            for state in FaceState:
                face.set_state(state)
                self.assertEqual(face.state, state)
            face.close()
        with self.assertRaises(TypeError):
            face.set_state("speaking")

    def test_arms_always_fail_closed_even_with_environment_opt_in(self):
        with patch.dict("os.environ", {"BMO_ALLOW_ARM_MOVEMENT": "1"}):
            with patch("builtins.open", side_effect=AssertionError("device opened")):
                arms = DeniedArmController()
                for values in ((0, 0, 1), (90, 90, 1), (float("nan"), 900, -1)):
                    with self.assertRaises(ArmMovementDisabledError):
                        arms.move_to(values[0], values[1], duration_seconds=values[2])
                arms.close()


class TelemetrySchemaTests(unittest.TestCase):
    def test_known_schema_is_typed_with_explicit_units(self):
        sample = parse_telemetry(
            frame("imu", accel_g=[0, 0, 1], gyro_dps=[1.5, -2, 0], temperature_c=24)
        )
        self.assertIsInstance(sample, IMUSample)
        self.assertEqual(sample.acceleration_g, (0.0, 0.0, 1.0))
        self.assertEqual(sample.gyroscope_dps, (1.5, -2.0, 0.0))
        button = parse_telemetry(frame("button", button_id=3, pressed=True))
        self.assertEqual(button, ButtonEvent(1, 1234, 3, True))
        self.assertEqual(parse_telemetry(frame()), Heartbeat(1, 1234))

    def test_unsupported_version_unknown_fields_and_commands_are_rejected(self):
        valid = json.loads(frame())
        payloads = [
            {**valid, "v": 2},
            {**valid, "v": True},
            {**valid, "v": 1.0},
            {**valid, "type": "move_arms"},
            {**valid, "type": []},
            {**valid, "command": "move_arms"},
            {key: value for key, value in valid.items() if key != "seq"},
            [],
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(TelemetryError):
                    parse_telemetry(json.dumps(payload).encode())

    def test_malformed_and_nonfinite_values_are_rejected(self):
        bad_frames = [
            b"",
            b"\xff",
            b"{",
            b'{"v":1,"type":"heartbeat","seq":1,"seq":2,"timestamp_ms":0}',
            b" " * (MAX_FRAME_BYTES + 1),
            frame("imu", accel_g=[float("nan"), 0, 0], gyro_dps=[0, 0, 0], temperature_c=24),
            frame("imu", accel_g=[float("inf"), 0, 0], gyro_dps=[0, 0, 0], temperature_c=24),
            frame("imu", accel_g=[True, 0, 0], gyro_dps=[0, 0, 0], temperature_c=24),
            frame("imu", accel_g=[0, 0], gyro_dps=[0, 0, 0], temperature_c=24),
            frame("imu", accel_g=[0, 0, 17], gyro_dps=[0, 0, 0], temperature_c=24),
            frame("imu", accel_g=[0, 0, 1], gyro_dps=[0, 0, 2001], temperature_c=24),
            frame("button", button_id=32, pressed=True),
            frame("button", button_id=False, pressed=True),
            frame("button", button_id=0, pressed="true"),
            frame(sequence=-1),
            frame(sequence=1 << 32),
            frame(sequence=True),
        ]
        for value in bad_frames:
            with self.subTest(value=value[:70]):
                with self.assertRaises(TelemetryError):
                    parse_telemetry(value)
        # JSON permits large exponents and integers; neither may escape as a crash.
        for value in (b"1e999", b"9" * 310):
            value = (
                b'{"v":1,"type":"imu","seq":1,"timestamp_ms":0,"accel_g":['
                + value
                + b',0,0],"gyro_dps":[0,0,0],"temperature_c":20}'
            )
            with self.assertRaises(TelemetryError):
                parse_telemetry(value)


class TelemetryFramingTests(unittest.TestCase):
    def test_fragmented_frames_crlf_and_malformed_recovery(self):
        decoder = TelemetryDecoder()
        first = frame(sequence=1)
        self.assertEqual(decoder.feed(first[:9]), ())
        events = decoder.feed(first[9:] + b"\r\nsecret-invalid-packet\n" + frame(sequence=2) + b"\n")
        self.assertEqual([event.sequence for event in events], [1, 2])
        self.assertEqual(decoder.accepted_frames, 2)
        self.assertEqual(decoder.rejected_frames, 1)
        self.assertEqual(decoder.buffered_bytes, 0)
        self.assertNotIn("secret", decoder.last_error)

    def test_oversized_frame_tail_is_never_accepted_and_buffer_stays_bounded(self):
        decoder = TelemetryDecoder()
        self.assertEqual(decoder.feed(b"x" * (MAX_FRAME_BYTES + 1)), ())
        self.assertLessEqual(decoder.buffered_bytes, MAX_FRAME_BYTES)
        self.assertEqual(decoder.feed(frame(sequence=55) + b"\n"), ())
        self.assertEqual(decoder.feed(frame(sequence=2) + b"\n"), (Heartbeat(2, 1234),))
        self.assertEqual(decoder.rejected_frames, 1)

    def test_maximum_frame_accepts_fragmented_crlf_without_growing_unbounded(self):
        payload = frame(sequence=8)
        payload += b" " * (MAX_FRAME_BYTES - len(payload))
        decoder = TelemetryDecoder()
        self.assertEqual(decoder.feed(payload), ())
        self.assertEqual(decoder.feed(b"\r"), ())
        self.assertEqual(decoder.buffered_bytes, MAX_FRAME_BYTES + 1)
        self.assertEqual(decoder.feed(b"\n"), (Heartbeat(8, 1234),))
        self.assertEqual(decoder.buffered_bytes, 0)
        self.assertEqual(decoder.rejected_frames, 0)
        # The extra byte is permitted only for CRLF framing; repeated CR or
        # another payload byte must drop the entire line through its newline.
        for tail in (b"\r\r\n", b"\rx\n", b"x\n"):
            decoder = TelemetryDecoder()
            self.assertEqual(decoder.feed(payload + tail), ())
            self.assertEqual(decoder.rejected_frames, 1)
            self.assertLessEqual(decoder.buffered_bytes, MAX_FRAME_BYTES + 1)
            self.assertEqual(decoder.feed(frame(sequence=9) + b"\n"), (Heartbeat(9, 1234),))

    def test_truncated_stream_is_dropped_before_next_connection(self):
        decoder = TelemetryDecoder()
        decoder.feed(frame()[:20])
        decoder.end_of_stream()
        self.assertEqual(decoder.buffered_bytes, 0)
        self.assertEqual(decoder.rejected_frames, 1)
        self.assertEqual(decoder.feed(frame(sequence=9) + b"\n"), (Heartbeat(9, 1234),))
        with self.assertRaises(TelemetryError):
            decoder.feed(b"x" * (MAX_CHUNK_BYTES + 1))
        with self.assertRaises(TypeError):
            decoder.feed("text")


class TelemetryFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.monitor = TelemetryMonitor(clock=lambda: self.now, max_age_seconds=0.5, button_queue_size=2)

    def test_freshness_uses_host_arrival_not_controller_clock_or_heartbeat(self):
        sample = IMUSample(1, (1 << 32) - 1, (0, 0, 1), (0, 0, 0), 23)
        self.assertTrue(self.monitor.ingest(sample))
        self.assertEqual(self.monitor.latest_imu(), sample)
        self.now += 0.6
        self.assertTrue(self.monitor.ingest(Heartbeat(2, 0)))
        self.assertIsNone(self.monitor.latest_imu())

    def test_duplicates_backwards_packets_and_wrap(self):
        high = (1 << 32) - 1
        self.assertTrue(self.monitor.ingest(Heartbeat(high, 0)))
        self.assertFalse(self.monitor.ingest(Heartbeat(high, 1)))
        self.assertFalse(self.monitor.ingest(Heartbeat(high - 1, 1)))
        self.assertTrue(self.monitor.ingest(Heartbeat(0, 2)))
        self.assertEqual(self.monitor.rejected_sequences, 2)
        with self.assertRaises(TelemetryError):
            self.monitor.ingest(Heartbeat(-1, 3))

    def test_buttons_are_bounded_consumed_once_and_expire(self):
        for sequence in range(1, 4):
            self.monitor.ingest(ButtonEvent(sequence, 0, sequence, True))
        self.assertEqual(self.monitor.dropped_buttons, 1)
        self.assertEqual([event.button_id for event in self.monitor.drain_buttons()], [2, 3])
        self.assertEqual(self.monitor.drain_buttons(), ())
        self.monitor.ingest(ButtonEvent(4, 0, 4, False))
        self.now += 1
        self.assertEqual(self.monitor.drain_buttons(), ())

    def test_invalid_configuration_and_clock_are_rejected(self):
        for value in (0, -1, float("inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                TelemetryMonitor(max_age_seconds=value)
        for value in (0, 1025, True):
            with self.assertRaises(ValueError):
                TelemetryMonitor(button_queue_size=value)
        self.monitor.ingest(Heartbeat(1, 0))
        self.now -= 1
        self.assertIsNone(self.monitor.latest_imu())
        with self.assertRaises(ValueError):
            self.monitor.ingest(Heartbeat(2, 0))
        self.now = float("nan")
        with self.assertRaises(ValueError):
            self.monitor.drain_buttons()


if __name__ == "__main__":
    unittest.main()
