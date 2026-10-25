"""Allowlisted read-only evidence, never a hardware activation or health claim.

The default path performs no IO. A caller must explicitly opt in and supply a
bounded command runner for telemetry. This module never changes power, fan,
services, permissions, firmware or input mappings.
"""
import math
import re

from .features.contracts import FeatureError


_POWER_BITS = ((0, "undervoltage"), (1, "frequency_capped"),
               (2, "throttled"), (3, "soft_temperature_limit"))
_KNOWN = sum((1 << bit) | (1 << (bit + 16)) for bit, _ in _POWER_BITS)


def decode_throttled(value):
    """Separate the four current flags from their latched historical flags.

    Accept the bounded `vcgencmd get_throttled` output or its hex value. Unknown
    bits are retained numerically and make interpretation incomplete.
    """
    if type(value) is not str or len(value) > 64:
        raise FeatureError("invalid_power_telemetry")
    match = re.fullmatch(r"(?:throttled=)?0x([0-9a-fA-F]{1,8})\s?", value)
    if match is None:
        raise FeatureError("invalid_power_telemetry")
    bits = int(match[1], 16)
    current = {name: bool(bits & (1 << bit)) for bit, name in _POWER_BITS}
    historical = {name: bool(bits & (1 << (bit + 16))) for bit, name in _POWER_BITS}
    unknown = bits & ~_KNOWN
    return {"status": "OBSERVED", "value_hex": f"0x{bits:x}",
            "current": current, "historical": historical,
            "current_fault": any(current.values()), "historical_fault": any(historical.values()),
            "unknown_bits_hex": f"0x{unknown:x}", "interpretation_complete": unknown == 0,
            "historical_is_current": False, "power_control": "DISABLED"}


def parse_temperature(value):
    if type(value) is not str or len(value) > 64:
        raise FeatureError("invalid_temperature_telemetry")
    match = re.fullmatch(r"temp=(-?[0-9]{1,3}(?:\.[0-9]{1,3})?)'C\s?", value)
    if match is None:
        raise FeatureError("invalid_temperature_telemetry")
    number = float(match[1])
    if not math.isfinite(number) or not -40 <= number <= 150:
        raise FeatureError("invalid_temperature_telemetry")
    return {"status": "OBSERVED", "celsius": number, "fan_control": "DISABLED"}


def telemetry_snapshot(*, runner=None, enabled=False):
    """Use an explicitly injected runner, with fixed commands and timeout.

    runner(argv, timeout=1.0, max_bytes=64) must return a bounded text string.
    There is intentionally no subprocess fallback and no automatic polling.
    Raw output and exception details are never returned.
    """
    if type(enabled) is not bool:
        raise FeatureError("invalid_telemetry_config")
    result = {"version": 1, "read_only": True,
              "temperature": {"status": "NOT_RUN"}, "power": {"status": "NOT_RUN"},
              "services_changed": False, "devices_opened": False}
    if not enabled:
        return result
    if not callable(runner):
        raise FeatureError("telemetry_runner_required")
    for key, command, parser in (("temperature", "measure_temp", parse_temperature),
                                 ("power", "get_throttled", decode_throttled)):
        try:
            result[key] = parser(runner(("vcgencmd", command), timeout=1.0, max_bytes=64))
        except Exception:
            result[key] = {"status": "UNAVAILABLE", "code": "telemetry_unavailable"}
    return result


def button_inventory():
    """Documented wiring is evidence, not verification of current firmware."""
    rows = [(2, "up", "ArrowUp"), (3, "left", "ArrowLeft"),
            (4, "right", "ArrowRight"), (5, "down", "ArrowDown"),
            (6, "triangle", "KeyZ"), (7, "red", None), (8, "green", "Enter")]
    return {"version": 1, "provenance": "user_documented_wiring",
            "current_firmware_verified": False, "device_reads": 0,
            "physical_test": "NOT_RUN", "servo": "DISABLED", "imu": "DISABLED",
            "keyboard_device_distinguishable": False,
            "buttons": [{"pin": f"GP{pin}", "role": role, "documented_hid": key,
                         "mapping_status": "PRESERVED_ARM_TOGGLE" if pin == 7 else "HUMAN_TEST_PENDING",
                         "can_reassign": False} for pin, role, key in rows]}
