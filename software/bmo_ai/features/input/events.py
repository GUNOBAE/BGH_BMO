"""Bounded synthetic event admission in front of the existing InputRouter.

No evdev, HID, CDC, GPIO or device-reader package is imported or opened here.
Start+Select timing and mode dispatch remain owned by GamesFeature/InputRouter.
"""
from collections import deque
from dataclasses import dataclass
import math
import re
from types import MappingProxyType
import time

from ..contracts import FeatureError, valid_id
from ..resources import LOGICAL_INPUTS


SOURCES = frozenset({"synthetic", "usb_pad", "rp2040_hid", "rp2040_cdc"})
_GLOBAL_EDGES = frozenset({"STOP", "TALK", "MENU"})


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _control(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value) is not None


@dataclass(frozen=True, slots=True)
class RawInputEvent:
    source: str
    control: str
    pressed: bool
    sequence: int
    timestamp: float

    def __post_init__(self):
        if (type(self.source) is not str or self.source not in SOURCES or not _control(self.control) or type(self.pressed) is not bool
                or type(self.sequence) is not int or self.sequence < 0 or not _number(self.timestamp)):
            raise FeatureError("invalid_input_event")


class VerifiedMapping:
    """An explicit host verification record, not an inference from button IDs.

    Synthetic provenance exercises code only; it is never physical acceptance.
    Literal GP7 pin bindings remain prohibited while the arm-toggle conflict is
    unresolved. This does not infer a HID key code for the current firmware.
    """
    def __init__(self, source, bindings, *, verified=False, provenance=""):
        if verified is not True or not valid_id(provenance):
            raise FeatureError("input_unverified")
        if (type(source) is not str or source not in SOURCES or type(bindings) is not dict or not 1 <= len(bindings) <= 64
                or any(not _control(key) or type(value) is not str or value not in LOGICAL_INPUTS
                       for key, value in bindings.items())
                or len(set(bindings.values())) != len(bindings)):
            raise FeatureError("invalid_input_mapping")
        if source.startswith("rp2040") and any(key.upper() in {"GP7", "GPIO7", "PIN_GP7"} for key in bindings):
            raise FeatureError("gp7_conflict")
        self.source, self.provenance = source, provenance
        self.bindings = MappingProxyType(dict(bindings))


class SyntheticEventSource:
    """A finite, caller-supplied trace. Empty does not mean device disconnect."""
    def __init__(self, events=(), *, clock=time.monotonic):
        if type(events) not in (tuple, list) or len(events) > 4096 or any(type(e) is not RawInputEvent for e in events):
            raise FeatureError("invalid_input_trace")
        if any(events[n].timestamp > events[n + 1].timestamp for n in range(len(events) - 1)):
            raise FeatureError("invalid_input_trace")
        self._events, self.clock, self.closed = deque(events), clock, False

    def poll(self, limit=32):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise FeatureError("invalid_input_limit")
        result = []
        now = self.clock()
        if not _number(now):
            raise FeatureError("invalid_input_clock")
        while not self.closed and self._events and len(result) < limit and self._events[0].timestamp <= now:
            result.append(self._events.popleft())
        return tuple(result)

    def close(self):
        self.closed = True
        self._events.clear()


@dataclass(frozen=True, slots=True)
class InputDispatch:
    logical: str | None
    pressed: bool
    sequence: int
    timestamp: float
    delivered: bool
    code: str

    def public(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class InputAdapter:
    def __init__(self, router, source, *, mapping=None, clock=time.monotonic,
                 debounce_seconds=.03, freshness_seconds=1.0, batch_size=32):
        if (mapping is not None and type(mapping) is not VerifiedMapping
                or type(batch_size) is not int or not 1 <= batch_size <= 64
                or not _number(debounce_seconds) or debounce_seconds > 1
                or not _number(freshness_seconds) or not 0 < freshness_seconds <= 5):
            raise FeatureError("invalid_input_adapter")
        self.router, self.source, self.mapping, self.clock = router, source, mapping, clock
        self.debounce_seconds, self.freshness_seconds, self.batch_size = debounce_seconds, freshness_seconds, batch_size
        self._pressed, self._released_at = set(), {}
        self._last_sequence, self._last_timestamp = -1, None
        self._closed, self._source_failed = False, False
        self._counts = dict(delivered=0, dropped=0)

    def snapshot(self):
        return {"enabled": self.mapping is not None and not self._closed and not self._source_failed,
                "source": self.mapping.source if self.mapping else None,
                "provenance": self.mapping.provenance if self.mapping else None,
                "reason_code": "input_unverified" if self.mapping is None else "input_closed" if self._closed else
                               "input_source_failed" if self._source_failed else "ok",
                "real_input": "NOT_RUN", "device_opens": 0, **self._counts}

    def _route(self, logical, pressed, sequence, at):
        try:
            self.router.route(logical, pressed, now=at)
            self._counts["delivered"] += 1
            return InputDispatch(logical, pressed, sequence, at, True, "delivered")
        except Exception as error:
            self._counts["dropped"] += 1
            code = error.code if isinstance(error, FeatureError) else "input_consumer_failed"
            return InputDispatch(logical, pressed, sequence, at, False, code)

    def _release(self, at):
        result = []
        for logical in sorted(self._pressed):
            if logical not in _GLOBAL_EDGES:
                result.append(self._route(logical, False, self._last_sequence, at))
            self._released_at[logical] = at
        self._pressed.clear()
        return result

    def disconnect(self):
        if self._closed or self._source_failed or self.mapping is None:
            return ()
        now = self.clock()
        if not _number(now):
            now = self._last_timestamp or 0.
        result = self._release(now)
        if not self._source_failed:
            result.append(self._route("PAD_LOST", True, self._last_sequence, now))
        self._source_failed = True
        return tuple(result)

    def poll(self):
        if self._closed or self._source_failed or self.mapping is None:
            return ()
        now = self.clock()
        if not _number(now):
            raise FeatureError("invalid_input_clock")
        try:
            batch = self.source.poll(self.batch_size)
        except Exception:
            return self.disconnect()
        if type(batch) not in (tuple, list) or len(batch) > self.batch_size:
            return self.disconnect()
        accepted, result = [], []
        for event in batch:
            code, logical = "ok", None
            if type(event) is not RawInputEvent:
                self._counts["dropped"] += 1
                continue
            if self.mapping is None or event.source != self.mapping.source:
                code = "input_unverified"
            elif event.sequence <= self._last_sequence:
                code = "duplicate_input"
            else:
                self._last_sequence = event.sequence
                if (event.timestamp > now + .05 or now - event.timestamp > self.freshness_seconds
                        or self._last_timestamp is not None and event.timestamp < self._last_timestamp):
                    code = "stale_input"
                else:
                    self._last_timestamp = event.timestamp
                    logical = self.mapping.bindings.get(event.control)
                    if logical is None:
                        code = "unmapped_input"
            if code != "ok":
                self._counts["dropped"] += 1
                result.append(InputDispatch(logical, event.pressed, event.sequence, event.timestamp, False, code))
            else:
                accepted.append((event, logical))
        generation = self.router.modes.generation
        stops = [(e, key) for e, key in accepted if key == "STOP" and e.pressed]
        if stops:
            event, _ = stops[0]
            if "STOP" not in self._pressed:
                result.append(self._route("STOP", True, event.sequence, event.timestamp))
            else:
                self._counts["dropped"] += 1
                result.append(InputDispatch("STOP", True, event.sequence, event.timestamp, False, "repeated_input"))
            result.extend(self._release(event.timestamp))
            self._pressed.add("STOP")
            # A source may deliver press and release in one poll. Preserve its
            # final STOP edge state without routing another global action.
            for stop_event, logical in accepted:
                if logical == "STOP" and stop_event.sequence >= event.sequence:
                    if stop_event.pressed:
                        self._pressed.add("STOP")
                    else:
                        self._pressed.discard("STOP")
                        self._released_at["STOP"] = stop_event.timestamp
            for skipped, logical in accepted:
                if skipped is not event:
                    self._counts["dropped"] += 1
                    result.append(InputDispatch(logical, skipped.pressed, skipped.sequence,
                                                skipped.timestamp, False, "stop_preempted"))
            return tuple(result)
        for event, logical in accepted:
            code = None
            if event.pressed:
                if self.router.modes.generation != generation:
                    code = "stale_mode"
                elif logical in self._pressed:
                    code = "repeated_input"
                elif event.timestamp - self._released_at.get(logical, -math.inf) < self.debounce_seconds:
                    code = "debounced_input"
                if code is None:
                    self._pressed.add(logical)
            else:
                if logical not in self._pressed:
                    code = "repeated_input"
                else:
                    self._pressed.discard(logical)
                    self._released_at[logical] = event.timestamp
                    if logical in _GLOBAL_EDGES:
                        code = "edge_only_release"
            if code is not None:
                self._counts["dropped"] += 1
                result.append(InputDispatch(logical, event.pressed, event.sequence, event.timestamp, False, code))
            else:
                # Releases are never delayed: a released Start/Select must not
                # keep the existing game's 1.2-second exit hold armed.
                result.append(self._route(logical, event.pressed, event.sequence, event.timestamp))
        return tuple(result)

    def close(self):
        if not self._closed:
            self._closed = True
            now = self.clock()
            self._release(now if _number(now) else self._last_timestamp or 0.)
            try:
                self.source.close()
            except Exception:
                self._source_failed = True
