"""Pure/injected telemetry and documented wiring; no live command execution."""
import json
import unittest
from unittest.mock import Mock, patch

from bmo_ai.features.contracts import FeatureError
from bmo_ai.system_readiness import button_inventory, decode_throttled, parse_temperature, telemetry_snapshot


class SystemReadinessTests(unittest.TestCase):
    def test_historical_50000_is_not_current_power_failure(self):
        result = decode_throttled("throttled=0x50000\n")
        self.assertFalse(result["current_fault"])
        self.assertTrue(result["historical_fault"])
        self.assertTrue(result["historical"]["undervoltage"])
        self.assertTrue(result["historical"]["throttled"])
        self.assertFalse(result["historical_is_current"])
        self.assertEqual(result["unknown_bits_hex"], "0x0")

    def test_current_and_historical_flags_stay_independent(self):
        result = decode_throttled("0x8000b")
        self.assertTrue(result["current"]["undervoltage"])
        self.assertTrue(result["current"]["frequency_capped"])
        self.assertFalse(result["current"]["throttled"])
        self.assertTrue(result["current"]["soft_temperature_limit"])
        self.assertTrue(result["historical"]["soft_temperature_limit"])
        self.assertFalse(result["historical"]["undervoltage"])

    def test_zero_and_unknown_bits_do_not_overclaim_clear_power_health(self):
        clear = decode_throttled("0x0")
        self.assertFalse(clear["current_fault"] or clear["historical_fault"])
        self.assertTrue(clear["interpretation_complete"])
        unknown = decode_throttled("0x80000000")
        self.assertFalse(unknown["interpretation_complete"])
        self.assertEqual(unknown["unknown_bits_hex"], "0x80000000")

    def test_malformed_or_unbounded_power_evidence_is_rejected(self):
        for raw in (None, True, 0x50000, "0x100000000", "0x-1", "0x5;reboot", "secret " * 100,
                    "0x0\nprivate", "throttled=0x0\x00", "0x0\n\n"):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(FeatureError):
                decode_throttled(raw)

    def test_temperature_is_numeric_without_control_or_policy(self):
        result = parse_temperature("temp=51.25'C\n")
        self.assertEqual(result, {"status": "OBSERVED", "celsius": 51.25, "fan_control": "DISABLED"})
        for raw in ("temp=nan'C", "temp=999'C", "temp=1'C\nprivate", 1, "temp=20'C;halt"):
            with self.assertRaises(FeatureError):
                parse_temperature(raw)

    def test_default_snapshot_performs_no_io_or_injected_command(self):
        runner = Mock(side_effect=AssertionError("No hardware command"))
        with patch("subprocess.Popen", side_effect=AssertionError("No subprocess")):
            result = telemetry_snapshot(runner=runner)
        runner.assert_not_called()
        self.assertEqual(result["power"]["status"], "NOT_RUN")
        self.assertEqual(result["temperature"]["status"], "NOT_RUN")
        self.assertFalse(result["devices_opened"] or result["services_changed"])

    def test_explicit_injected_runner_receives_only_fixed_bounded_requests(self):
        runner = Mock(side_effect=["temp=48.0'C", "throttled=0x50000"])
        result = telemetry_snapshot(runner=runner, enabled=True)
        self.assertEqual(result["temperature"]["celsius"], 48)
        self.assertFalse(result["power"]["current_fault"])
        self.assertEqual([c.args[0] for c in runner.call_args_list],
                         [("vcgencmd", "measure_temp"), ("vcgencmd", "get_throttled")])
        self.assertTrue(all(c.kwargs == {"timeout": 1.0, "max_bytes": 64} for c in runner.call_args_list))

    def test_failure_never_leaks_raw_error_or_suppresses_independent_probe(self):
        runner = Mock(side_effect=[RuntimeError("SECRET path key value"), "throttled=0x0"])
        result = telemetry_snapshot(runner=runner, enabled=True)
        self.assertEqual(result["temperature"]["code"], "telemetry_unavailable")
        self.assertEqual(result["power"]["status"], "OBSERVED")
        self.assertNotIn("SECRET", json.dumps(result))
        with self.assertRaises(FeatureError):
            telemetry_snapshot(enabled=True)
        with self.assertRaises(FeatureError):
            telemetry_snapshot(enabled=1)

    def test_documented_gp7_is_preserved_and_mapping_not_falsely_verified(self):
        inventory = button_inventory()
        buttons = {item["pin"]: item for item in inventory["buttons"]}
        self.assertEqual(set(buttons), {f"GP{i}" for i in range(2, 9)})
        self.assertEqual(buttons["GP7"]["mapping_status"], "PRESERVED_ARM_TOGGLE")
        self.assertIsNone(buttons["GP7"]["documented_hid"])
        self.assertEqual(buttons["GP8"]["documented_hid"], "Enter")
        self.assertFalse(inventory["current_firmware_verified"])
        self.assertFalse(inventory["keyboard_device_distinguishable"])
        self.assertEqual(inventory["device_reads"], 0)
        self.assertTrue(all(not item["can_reassign"] for item in buttons.values()))


if __name__ == "__main__":
    unittest.main()
