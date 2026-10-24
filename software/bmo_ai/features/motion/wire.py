"""Bounded proposal-only JSON/LF codec, with optional negotiated CRC32.

This module has no serial reader/writer, DTR, REPL or firmware entry point.
CRC detects transmission corruption; it supplies no authentication or position
feedback. A live controller must implement/review this protocol separately.
"""
from dataclasses import asdict
import json
import re
import zlib

from ..contracts import FeatureError, request_id_ok
from .protocol import Handshake, MotionAck, MotionCommand


MAX_FRAME = 512
TYPES = {"hello": Handshake, "command": MotionCommand, "ack": MotionAck}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise FeatureError("invalid_motion_frame")
        value[key] = item
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _validate(kind, payload):
    if kind not in TYPES or type(payload) is not dict or set(payload) != set(TYPES[kind].__dataclass_fields__):
        raise FeatureError("invalid_motion_frame")
    result = TYPES[kind](**payload)
    if kind == "hello" and (type(result.version) is not int or result.version != 1
            or result.channel != "dedicated_data" or not request_id_ok(result.controller_boot)
            or type(result.controller_dedup) is not bool or type(result.position_feedback) is not bool):
        raise FeatureError("invalid_motion_frame")
    if kind == "ack" and (not request_id_ok(result.request_id) or not request_id_ok(result.controller_boot)
                           or type(result.accepted) is not bool):
        raise FeatureError("invalid_motion_frame")
    return result


def encode_frame(value, *, crc=False):
    try:
        if type(crc) is not bool:
            raise ValueError()
        kind = next((name for name, cls in TYPES.items() if type(value) is cls), None)
        if kind is None:
            raise ValueError()
        payload = asdict(value)
        _validate(kind, payload)
        frame = {"version": 1, "kind": kind, "payload": payload}
        if crc:
            frame["crc32"] = f"{zlib.crc32(_canonical(frame)):08x}"
        raw = _canonical(frame) + b"\n"
        if len(raw) > MAX_FRAME:
            raise ValueError()
        return raw
    except Exception:
        raise FeatureError("invalid_motion_frame") from None


def decode_frame(raw, *, require_crc=False):
    try:
        if type(raw) is not bytes or type(require_crc) is not bool or not 1 < len(raw) <= MAX_FRAME:
            raise ValueError()
        if not raw.endswith(b"\n") or b"\n" in raw[:-1] or b"\r" in raw:
            raise ValueError()
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict or set(value) not in ({"version", "kind", "payload"},
                                                        {"version", "kind", "payload", "crc32"}):
            raise ValueError()
        checksum = value.pop("crc32", None)
        if require_crc and checksum is None:
            raise ValueError()
        if checksum is not None and (type(checksum) is not str or not re.fullmatch(r"[0-9a-f]{8}", checksum)
                or checksum != f"{zlib.crc32(_canonical(value)):08x}"):
            raise ValueError()
        if type(value["version"]) is not int or value["version"] != 1 or type(value["kind"]) is not str:
            raise ValueError()
        return _validate(value["kind"], value["payload"])
    except Exception:
        raise FeatureError("invalid_motion_frame") from None
