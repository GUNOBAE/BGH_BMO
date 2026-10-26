"""Staged host wire contract for the supervised RP2040 arm firmware.

Pure JSON/LF framing. Import, encode and decode NEVER open a serial device,
reset CircuitPython, move PWM, or modify the currently running firmware.
"""
import json
import re


class ArmCommandError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Arm data contract rejected: " + code)


def _object(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ArmCommandError("duplicate_arm_key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, TypeError):
        raise ArmCommandError("invalid_arm_frame") from None


def encode_command(op, seq, *, boot=None, side=None, angle=None,
                   operator_present=False, reviewed_limits=None):
    if type(seq) is not int or not 0 <= seq <= 2147483647:
        raise ArmCommandError("invalid_arm_sequence")
    if type(op) is not str or op not in ("hello", "stop", "arm", "heartbeat", "move"):
        raise ArmCommandError("invalid_arm_operation")
    value = {"v": 1, "seq": seq, "op": op}
    if op != "hello":
        if not isinstance(boot, str) or not re.fullmatch("[0-9a-f]{16}", boot):
            raise ArmCommandError("arm_boot_unverified")
        value["boot"] = boot
    if op == "arm":
        if operator_present is not True or reviewed_limits is None:
            raise ArmCommandError("arm_operator_review_required")
        value["operator_present"] = True
    elif op == "move":
        if (operator_present is not True or type(side) is not str
                or side not in ("left", "right") or type(angle) is not int
                or reviewed_limits is None):
            raise ArmCommandError("arm_operator_review_required")
        limits = reviewed_limits.get(side) if type(reviewed_limits) is dict else None
        if (type(limits) is not dict or set(limits) != {"minimum", "neutral", "maximum"}
                or not all(type(v) is int for v in limits.values())
                or not limits["minimum"] <= angle <= limits["maximum"]):
            raise ArmCommandError("arm_calibration_unverified")
        value.update(side=side, angle=angle)
    try:
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
    except (ValueError, TypeError):
        raise ArmCommandError("invalid_arm_frame") from None
    if len(raw) > 192:
        raise ArmCommandError("arm_frame_too_large")
    return raw


def decode_reply(raw, *, expected_boot=None):
    if type(raw) is not bytes or not 1 <= len(raw) <= 256 or not raw.endswith(b"\n"):
        raise ArmCommandError("invalid_arm_frame")
    try:
        value = _object(raw[:-1].decode("ascii"))
    except UnicodeError:
        raise ArmCommandError("invalid_arm_frame") from None
    if (type(value) is not dict
            or set(value) != {"v", "boot", "seq", "code", "accepted", "position_verified"}
            or type(value["v"]) is not int or value["v"] != 1
            or type(value["seq"]) is not int or not 0 <= value["seq"] <= 2147483647
            or type(value["accepted"]) is not bool
            or value["position_verified"] is not False
            or type(value["boot"]) is not str
            or type(value["code"]) is not str):
        raise ArmCommandError("invalid_arm_reply")
    if expected_boot is not None and value["boot"] != expected_boot:
        raise ArmCommandError("controller_boot_changed")
    return value
