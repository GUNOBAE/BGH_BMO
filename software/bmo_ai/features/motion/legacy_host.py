"""BMO1 named-gesture host; no USB access on import or construction."""
import hashlib
import math
from pathlib import Path
import re
import time
from collections import deque
from threading import Event, RLock

from bmo_ai.features.contracts import FeatureError, FeatureEvent, HostResult, request_id_ok
from .arm_serial_posix_v2 import MOUNT

FRAME_MAX = 96
REPLY_MAX = 128
IDENT = re.compile(r"[A-Za-z0-9_]{1,32}\Z", re.ASCII)
NAMED_GESTURES = frozenset(("wave", "celebrate", "shrug", "raise_left_arm",
                            "raise_right_arm", "both_up", "both_down"))
MODEL_ONLY = frozenset(("wave", "celebrate", "shrug"))
RESPONSE_CODES = frozenset(("READY", "DISABLED", "STOPPED", "ALIVE", "HELD",
    "ACCEPTED", "REQUEST_CONFLICT", "INVALID", "STALE_BOOT", "INVALID_REQUEST_ID",
    "STALE_REQUEST", "BUSY", "DENIED", "RECOVERABLE", "FAULT_STOPPED", "PREPARED",
    "PERFORMANCE_READY", "PERFORM_ENTER", "PERFORM_HOLD", "PERFORM_RETURN", "PERFORM_IDLE",
    "POSE_UNSUITABLE", "STALE_PERFORMANCE"))
MAX_REQUEST_SEQUENCE = 99999999999999999999
# B1 has no position/completion ACK. Keep heartbeats through the firmware's
# 15-second hard gesture budget; the host finally HOLDs rather than abandoning
# a possibly unfinished gesture. This is not proof of a physical end position.
ACTIVE_BUDGET_S = 15.2
LEDGER_LIMIT = 4096
PERFORMANCE_PROFILES = {("wave", "greeting"): "greeting", ("shrug", "surprise"): "surprise",
    ("celebrate", "achievement"): "celebration", ("shrug", "playful"): "playful",
    ("shrug", "hesitation"): "playful"}
PERFORMANCE_PHASES = {"PERFORM_ENTER":"entering", "PERFORM_HOLD":"holding",
                      "PERFORM_RETURN":"returning", "PERFORM_IDLE":"idle"}


def _finite_time(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _clock_value(clock):
    try:
        return clock()
    except Exception:
        return None


class LegacyArmError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_legacy_install(firmware_root, reviewed_sha, mount=MOUNT):
    """Read-only exact-byte verification. Never changes RP2040 or calibration."""
    if not isinstance(reviewed_sha, str) or re.fullmatch(r"[a-f0-9]{64}", reviewed_sha) is None:
        raise LegacyArmError("reviewed_sha_required")
    root = Path(firmware_root)
    original = (root / "original_code.py").read_bytes()
    if hashlib.sha256(original).hexdigest() != "fdc65bebef17b78b945281549ddce80fb749e1a94262751d8d2f0146c394a01e":
        raise LegacyArmError("original_source_mismatch")
    template = (root / "code.py").read_bytes()
    # The daily receipt pins exact enabled bytes. A separately reviewed v2
    # receipt may select the recovery candidate; v1 bytes and receipts stay valid.
    expected_digest = hashlib.sha256(template.replace(
        b"REMOTE_ENABLED = False", b"REMOTE_ENABLED = True", 1)).hexdigest()
    if expected_digest != reviewed_sha and root.name == "bmo_arm_legacy_bridge_v1":
        for name in ("bmo_arm_legacy_bridge_v2", "bmo_arm_legacy_bridge_v3", "bmo_arm_legacy_bridge_v4"):
            candidate = root.with_name(name)
            if candidate.is_dir():
                candidate_bytes = (candidate/"code.py").read_bytes().replace(
                    b"REMOTE_ENABLED = False", b"REMOTE_ENABLED = True", 1)
                if hashlib.sha256(candidate_bytes).hexdigest() == reviewed_sha:
                    return validate_legacy_install(candidate, reviewed_sha, mount=mount)
    if template.count(b"\nREMOTE_ENABLED = False\n") != 1:
        raise LegacyArmError("invalid_reviewed_template")
    expected = template.replace(b"REMOTE_ENABLED = False", b"REMOTE_ENABLED = True", 1)
    if (hashlib.sha256(expected).hexdigest() != reviewed_sha
            or expected != (Path(mount) / "code.py").read_bytes()
            or (root / "bmo_arm_bridge.py").read_bytes() != (Path(mount) / "bmo_arm_bridge.py").read_bytes()):
        raise LegacyArmError("bridge_sha_or_dependency_mismatch")
    if root.name == 'bmo_arm_legacy_bridge_v4':
        if (root/'bmo_imu.py').read_bytes() != (Path(mount)/'bmo_imu.py').read_bytes():
            raise LegacyArmError('bridge_sha_or_dependency_mismatch')
    boot = (Path(mount) / "boot.py").read_text(encoding="utf-8")
    if "usb_cdc.enable(console=True, data=True)" not in boot:
        raise LegacyArmError("dedicated_data_disabled")
    return reviewed_sha


class LegacyArmLink:
    """One outstanding B1 request. Require exact ACK and fresh boot token."""
    def __init__(self, port, *, motor_permission=None, sequence_clock=None,
                 permission_code="physical_supervision_required"):
        if not callable(getattr(port, "write", None)) or not callable(getattr(port, "read_until", None)):
            raise LegacyArmError("owned_cdc_port_required")
        if permission_code not in ("physical_supervision_required", "daily_arm_permission_required"):
            raise LegacyArmError("invalid_permission_code")
        self.permission_code = permission_code
        self.port, self.boot = port, None
        self.motor_permission = motor_permission or (lambda: False)
        # Linux monotonic_ns is shared across processes during the same Pi boot,
        # unlike an instance counter restarted at 1 by each Desktop launch.
        # The firmware boot token/high-water still rejects an older Pi epoch;
        # neither endpoint silently resets the remote replay barrier.
        self.sequence_clock = sequence_clock or time.monotonic_ns
        self.counter = 0
        self.unknown = False
        self.stopped = False
        self.closed = False
        self.last_response_code = None
        self.last_request_id = None
        self.prepare_code = None
        self.performance_supported = None
        self._port_lock = RLock()
        self._motor_waiting = Event()
        self._imu_counter = 0
        self._imu_ids = deque(maxlen=8)

    def _read_arm_reply(self):
        # A timed-out sensor query can arrive after its 25ms budget. Route only
        # that exact known request away from a later arm ACK, never arbitrary
        # unknown serial traffic. Keep the original arm IO deadline overall.
        bound = 256 if self._imu_ids else REPLY_MAX
        deadline = time.monotonic()+getattr(self.port,'timeout',.45)
        for _ in range(5):
            bounded = getattr(self.port,'read_until_budget',None)
            raw = (bounded(b'\n',bound,deadline) if callable(bounded)
                   else self.port.read_until(b'\n',bound))
            fields = raw.split(b' ',3) if type(raw) is bytes else ()
            if (len(fields)==4 and fields[:2]==[b'B1',b'IMU']
                    and fields[2] in tuple(value.encode('ascii') for value in self._imu_ids)):
                continue
            return raw
        raise LegacyArmError('invalid_ack')

    def imu_snapshot(self):
        """Read cached sensor registers without changing any motor state."""
        from .imu_transport import IMUTransportError,parse_imu_reply
        if self.closed:raise IMUTransportError('imu_port_closed')
        if self._motor_waiting.is_set() or not self._port_lock.acquire(blocking=False):
            raise IMUTransportError('imu_arm_transaction_priority')
        try:
            exchange = getattr(self.port,'exchange_imu',None)
            if not callable(exchange):raise IMUTransportError('imu_port_budget_unavailable')
            self._imu_counter += 1
            rid='imu_'+str(self._imu_counter)
            self._imu_ids.append(rid)
            deadline=time.monotonic()+.025
            raw=exchange(('B1 IMU '+rid+'\n').encode('ascii'))
            for _ in range(4):
                fields=raw.split(b' ',3) if type(raw) is bytes else ()
                if (len(fields)==4 and fields[:2]==[b'B1',b'IMU']
                        and fields[2].decode('ascii','ignore') in self._imu_ids
                        and fields[2]!=rid.encode('ascii')):
                    read=getattr(self.port,'read_until_budget',None)
                    if not callable(read) or time.monotonic()>=deadline:
                        raise IMUTransportError('imu_reply_stale')
                    raw=read(b'\n',256,deadline)
                    continue
                return parse_imu_reply(raw,rid)
            raise IMUTransportError('imu_reply_stale')
        except IMUTransportError:
            raise
        except Exception as error:
            code=getattr(error,'code',None)
            raise IMUTransportError(code if code in ('imu_write_timeout','imu_partial_write',
                'imu_invalid_query','imu_port_closed') else 'imu_transport_error') from None
        finally:
            self._port_lock.release()

    def _request(self, command, *, is_hello=False, before_write=None, recovery=False):
        self._motor_waiting.set()
        try:
            with self._port_lock:
                return self._request_locked(command,is_hello=is_hello,before_write=before_write,recovery=recovery)
        finally:
            self._motor_waiting.clear()

    def _request_locked(self, command, *, is_hello=False, before_write=None, recovery=False):
        known_stop = self.stopped and self.last_response_code == "STOPPED"
        if self.closed or (self.unknown and not (recovery and known_stop)) or (self.stopped and not recovery):
            raise LegacyArmError("bridge_unavailable")
        try:
            sequence = self.sequence_clock()
        except Exception:
            sequence = None
        if type(sequence) is not int or sequence < 0:
            self.unknown = True
            raise LegacyArmError("request_clock_invalid")
        self.counter = max(self.counter + 1, sequence)
        if self.counter > MAX_REQUEST_SEQUENCE:
            self.unknown = True
            raise LegacyArmError("request_counter_exhausted")
        rid = "bmo_%d" % self.counter
        self.last_request_id = rid
        data = ("B1 " + command.format(rid=rid, boot=self.boot) + "\n").encode("ascii")
        if len(data) > FRAME_MAX:
            raise LegacyArmError("frame_too_long")
        # This boundary follows permission checks and ID creation, both of which
        # can take time. A local expiry has sent zero DO bytes and must not mark
        # an otherwise known transport UNKNOWN.
        if before_write is not None and before_write() is not True:
            raise LegacyArmError("stale_playback_start")
        self.last_response_code = None
        try:
            if self.port.write(data) != len(data):
                raise LegacyArmError("partial_write")
            raw = self._read_arm_reply()
            if (type(raw) is not bytes or not 1 <= len(raw) <= REPLY_MAX or
                    not raw.endswith(b"\n") or b"\r" in raw or b"\n" in raw[:-1]):
                raise LegacyArmError("ack_missing_or_incomplete")
            parts = raw[:-1].decode("ascii").split(" ")
            if (len(parts) != 6 or parts[0:2] != ["B1", "ACK"] or parts[2] != rid
                    or IDENT.fullmatch(parts[3]) is None
                    or parts[5] != "POSITION_UNVERIFIED"
                    or re.fullmatch(r"[A-Z_]{2,32}", parts[4]) is None):
                raise LegacyArmError("invalid_ack")
            if self.boot is not None and parts[3] != self.boot:
                raise LegacyArmError("boot_changed")
            self.last_response_code = parts[4] if parts[4] in RESPONSE_CODES else "UNRECOGNIZED"
            if parts[4] == "STOPPED":
                self.stopped = True
            if is_hello:
                self.boot = parts[3]
            return parts[4]
        except Exception as exc:
            self.unknown = True
            if isinstance(exc, LegacyArmError):
                raise
            raise LegacyArmError("link_unknown") from None

    def hello(self):
        if self.boot is not None:
            raise LegacyArmError("already_connected")
        code = self._request("HELLO {rid}", is_hello=True)
        if code != "READY":
            if code == "STOPPED":
                # A new Desktop/HELLO cannot clear the board's STOP, GP7 or
                # watchdog latch. Preserve this explicit remote observation
                # separately from an unverified transport failure.
                self.stopped = True
            self.unknown = True
            raise LegacyArmError("firmware_not_ready_" + code.lower())
        return code

    def prepare(self):
        """Only an explicit user action may call this. Never emits DO or PWM.

        A new HELLO observes the current latch, then v2's expiring STATUS token
        guards PREPARE against a later GP7/STOP edge. Unknown transport is not a
        recovery permission. A v1 STOP remains latched until firmware is reviewed.
        """
        try:
            permission = self.motor_permission()
        except Exception:
            permission = False
        if permission is not True:
            raise LegacyArmError(self.permission_code)
        if self.closed or (self.unknown and not (
                self.stopped and self.last_response_code == "STOPPED")):
            raise LegacyArmError("arm_prepare_connection_unverified")
        code = self._request("HELLO {rid}", is_hello=True, recovery=True)
        if code == "READY":
            self.stopped = self.unknown = False
            self.prepare_code = "arm_ready_position_unverified"
            return code
        if code != "STOPPED":
            self.prepare_code = "arm_prepare_board_unavailable"
            raise LegacyArmError(self.prepare_code)
        # A valid STOPPED ACK is a known stopped connection, not a timeout.
        self.unknown = False
        code = self._request("STATUS {rid} {boot}", recovery=True)
        token = self.last_request_id
        if code in ("STOPPED", "INVALID"):
            self.prepare_code = "arm_prepare_firmware_required"
            raise LegacyArmError(self.prepare_code)
        if code != "RECOVERABLE":
            self.prepare_code = "arm_prepare_fault_requires_review"
            raise LegacyArmError(self.prepare_code)
        code = self._request("PREPARE {rid} {boot} " + token, recovery=True)
        if code != "PREPARED":
            self.prepare_code = "arm_prepare_not_acknowledged"
            raise LegacyArmError(self.prepare_code)
        self.stopped = self.unknown = False
        self.prepare_code = "arm_prepared_position_unverified"
        return code

    def gesture(self, name, *, before_write=None):
        if self.boot is None or name not in NAMED_GESTURES:
            raise LegacyArmError("invalid_gesture_or_not_connected")
        try:
            permission = self.motor_permission()
        except Exception:
            permission = False
        if permission is not True:
            raise LegacyArmError(self.permission_code)
        code = self._request("DO {rid} {boot} " + name, before_write=before_write)
        if code != "ACCEPTED":
            raise LegacyArmError("gesture_" + code.lower())
        return code

    def performance_capability(self):
        if self.performance_supported is None:
            code = self._request("CAPS {rid} {boot}")
            self.performance_supported = code == "PERFORMANCE_READY"
        return self.performance_supported

    def performance_start(self, profile, *, before_write):
        if profile not in PERFORMANCE_PROFILES.values():
            raise LegacyArmError("invalid_performance")
        try:
            permission = self.motor_permission()
        except Exception:
            permission = False
        if permission is not True:
            raise LegacyArmError(self.permission_code)
        code = self._request("ACT {rid} {boot} " + profile, before_write=before_write)
        token = self.last_request_id
        if code != "PERFORM_ENTER":
            raise LegacyArmError("performance_" + code.lower())
        return token

    def performance_end(self, token):
        if IDENT.fullmatch(token or "") is None:
            raise LegacyArmError("invalid_performance_token")
        code = self._request("END {rid} {boot} " + token)
        if code not in PERFORMANCE_PHASES:
            raise LegacyArmError("performance_" + code.lower())
        return PERFORMANCE_PHASES[code]

    def performance_pulse(self, token):
        if IDENT.fullmatch(token or "") is None:
            raise LegacyArmError("invalid_performance_token")
        code = self._request("PULSE {rid} {boot} " + token)
        if code not in PERFORMANCE_PHASES:
            raise LegacyArmError("performance_" + code.lower())
        return PERFORMANCE_PHASES[code]

    def ping(self):
        if self.boot is None or self._request("PING {rid} {boot}") != "ALIVE":
            raise LegacyArmError("heartbeat_failed")

    def hold(self):
        if self.boot is not None and self._request("HOLD {rid} {boot}") != "HELD":
            raise LegacyArmError("hold_failed")

    def stop(self):
        if self.boot is not None and not self.stopped and not self.unknown:
            if self._request("STOP {rid} {boot}") != "STOPPED":
                self.unknown = True
                raise LegacyArmError("stop_unverified")
        self.stopped = True

    def close(self):
        if self.closed:
            return
        close = getattr(self.port, "close", None)
        if not callable(close):
            self.unknown = True
            raise LegacyArmError("port_close_unverified")
        try:
            close()
        except Exception:
            self.unknown = True
            raise LegacyArmError("port_close_failed") from None
        self.closed = True


class LegacyMotionFeature:
    """Plug into FeatureHost without altering model/GPT or approved R10."""
    def __init__(self, link, *, clock=None, ttl=1.0, cooldown=2.0,
                 telemetry_only=False, imu_binding=None):
        if not isinstance(link, LegacyArmLink):
            raise FeatureError("legacy_arm_link_required")
        if (not _finite_time(ttl) or not 0 < ttl <= 5
                or not _finite_time(cooldown) or cooldown > 60):
            raise FeatureError("invalid_motion_configuration")
        self.link = link
        self.clock = clock or time.monotonic
        self.ttl, self.cooldown = ttl, cooldown
        self.context = None
        self.generation = 0
        self.pending = None
        self.active_until = None
        self._active_pending = None
        self._inflight_pending = None
        self.last_gesture = float("-inf")
        self.next_ping = 0.
        self.seen = set()
        self._auto_contexts = set()
        self.events = []
        self.stopped = False
        self.closed = False
        self.fault = None
        self.last_error = None
        self.performance = None
        self.last_performance = None
        if type(telemetry_only) is not bool:raise FeatureError('invalid_motion_configuration')
        self.telemetry_only=telemetry_only
        self.imu_binding=imu_binding
        self._imu_poller=None

    def configure_imu(self, enabled=True, *, binding=None):
        from .imu_transport import IMUPoller,validated_binding
        if type(enabled) is not bool:raise FeatureError('invalid_imu_configuration')
        if binding is not None:self.imu_binding=validated_binding(binding)
        if enabled:
            self.imu_binding=validated_binding(self.imu_binding)
            if self.closed:raise FeatureError('feature_closed')
            if self._imu_poller is None:self._imu_poller=IMUPoller(self.link,clock=self.clock)
        elif self._imu_poller is not None:
            self._imu_poller.close();self._imu_poller=None

    def poll_imu(self):
        if self._imu_poller is None or self.closed:return
        if self.pending is not None:return
        if self.performance is not None:
            if (self.performance['phase']=='queued'
                    or self.performance['next_pulse']-self.clock()<.05):return
        if self.active_until is not None and self.next_ping-self.clock()<.05:return
        self._imu_poller.request()

    def drain_imu(self):
        return self._imu_poller.drain() if self._imu_poller is not None else ()

    def imu_transport_snapshot(self):
        return (self._imu_poller.snapshot() if self._imu_poller is not None else
                dict(enabled=False,code='imu_not_requested',shared_b1_owner=True,synthetic=False))

    def set_context(self, session_id, turn_id, mode_generation):
        if type(turn_id) is int and turn_id >= 0:
            turn_id = str(turn_id)
        if (not request_id_ok(session_id) or not request_id_ok(turn_id)
                or type(mode_generation) is not int or mode_generation < 0):
            raise FeatureError("invalid_motion_context")
        new_context = (session_id, turn_id, mode_generation)
        if new_context != self.context:
            self.cancel()
            self.context = new_context
            self.generation = mode_generation

    def submit(self, gesture_id, *, request_id, session_id, turn_id,
               mode_generation, started_at, explicit=False):
        if not request_id_ok(request_id):
            raise FeatureError("invalid_request_id")
        if self.telemetry_only:
            return HostResult(request_id,'rejected','imu_read_only_transport',simulated=False)
        if (self.closed or self.stopped or self.fault or self.pending is not None
                or self.active_until is not None or self.performance is not None or request_id in self.seen):
            return HostResult(request_id, "rejected", "arm_unavailable", simulated=False)
        if len(self.seen) >= LEDGER_LIMIT:
            return HostResult(request_id, "rejected", "motion_ledger_full", simulated=False)
        if self.context != (session_id, str(turn_id), mode_generation):
            return HostResult(request_id, "rejected", "stale_context", simulated=False)
        if type(explicit) is not bool or gesture_id not in (NAMED_GESTURES if explicit else MODEL_ONLY):
            return HostResult(request_id, "rejected", "invalid_gesture", simulated=False)
        if not explicit and self.context in self._auto_contexts:
            return HostResult(request_id, "rejected", "turn_gesture_limit", simulated=False)
        now = _clock_value(self.clock)
        if not _finite_time(now) or not explicit and (not _finite_time(started_at) or
                             not 0 <= now - started_at <= self.ttl):
            return HostResult(request_id, "rejected", "stale_playback_start", simulated=False)
        if now - self.last_gesture < self.cooldown:
            return HostResult(request_id, "rejected", "gesture_cooldown", simulated=False)
        self.seen.add(request_id)
        if not explicit:
            self._auto_contexts.add(self.context)
        self.pending = (request_id, gesture_id, started_at, self.context, explicit, now)
        return HostResult(request_id, "accepted", "gesture_queued", simulated=False)

    def start_performance(self, gesture_id, *, performance_context, request_id, session_id,
                          turn_id, mode_generation, started_at, explicit=False):
        """Queue a speech-linked performance, never wait for arrival.

        A validated current named request is separate from automatic decoration.
        It still needs a fresh player boundary, this same owner, the device
        cooldown, permission and the full token-bound stop/return lifecycle.
        """
        profile = PERFORMANCE_PROFILES.get((gesture_id, performance_context))
        if profile is None:
            return HostResult(request_id, "rejected", "invalid_performance_context", simulated=False)
        now = _clock_value(self.clock)
        if (not _finite_time(now) or not _finite_time(started_at)
                or not 0 <= now-started_at <= self.ttl):
            return HostResult(request_id, "rejected", "stale_playback_start", simulated=False)
        # Reuse all existing admission/dedup/current-turn restrictions, then
        # reserve this controller's one slot for the token-bound lifecycle.
        result = self.submit(gesture_id, request_id=request_id, session_id=session_id,
            turn_id=turn_id, mode_generation=mode_generation, started_at=started_at,
            explicit=explicit)
        if result.status != "accepted":
            return result
        pending, self.pending = self.pending, None
        self.performance = dict(pending=pending, profile=profile, phase="queued", token=None,
                                finish_reason=None, next_pulse=0., deadline=None)
        return HostResult(request_id, "accepted", "performance_queued", simulated=False)

    def finish_performance(self, turn_id=None, reason="playback_ended"):
        """Queue a normal bounded return. STOP/manual/fault can never enter here."""
        current = self.performance
        if current is None or (turn_id is not None and str(turn_id) != current['pending'][3][1]):
            return False
        if reason not in ('playback_ended', 'new_turn', 'cancel', 'listening', 'thinking', 'mode_change'):
            raise FeatureError('invalid_performance_finish_reason')
        if self.closed or self.stopped or self.fault or self.link.closed or self.link.stopped or self.link.unknown:
            self._retire_performance('performance_return_suppressed', 'cancelled')
            return False
        current['finish_reason'] = current['finish_reason'] or reason
        if current['token'] is None and current['phase'] == 'queued':
            self._retire_performance('performance_cancelled_before_start', 'cancelled')
        return True

    def _performance_fresh(self, current):
        pending = current['pending']; now = _clock_value(self.clock)
        return (self.performance is current and current['finish_reason'] is None
                and self.context == pending[3] and _finite_time(now)
                and 0 <= now-pending[2] <= self.ttl)

    def _retire_performance(self, code, status, *, executed=None):
        current, self.performance = self.performance, None
        if current is None:
            return
        self.last_performance = dict(profile=current['profile'], phase='ended' if status=='succeeded' else 'interrupted',
            code=code, finish_reason=current['finish_reason'], turn_id=current['pending'][3][1],
            position_verified=False)
        self._event(current['pending'], status, code,
                    executed=current['token'] is not None if executed is None else executed)

    def _poll_performance(self):
        current = self.performance
        if current is None:
            return
        try:
            now = _clock_value(self.clock)
            if not _finite_time(now):
                raise LegacyArmError('invalid_motion_clock')
            if current['phase'] == 'queued':
                if not self._performance_fresh(current):
                    self._retire_performance('stale_playback_start', 'rejected')
                    return
                if self.link.boot is None:
                    self.link.hello()
                if not self.link.performance_capability():
                    self._retire_performance('arm_performance_firmware_required', 'rejected')
                    return
                if not self._performance_fresh(current):
                    self._retire_performance('stale_playback_start', 'rejected')
                    return
                current['phase'] = 'starting'
                def before_write():
                    if not self._performance_fresh(current):
                        return False
                    current['token'] = self.link.last_request_id
                    return True
                token = self.link.performance_start(current['profile'], before_write=before_write)
                if self.performance is not current:
                    # A STOP while ACK was in flight already retired this token;
                    # never follow it with an automatic return or fresh ACT.
                    return
                current['token'] = token
                current['phase'] = 'entering'
                self.last_gesture = now
                current['deadline'] = now+4.2
                self._event(current['pending'], 'running', 'performance_ack_position_unverified', executed=True)
            if self.performance is not current or current['token'] is None:
                return
            if self.link.stopped or self.link.unknown:
                raise LegacyArmError('performance_stopped_or_unknown')
            if current['finish_reason'] and current['phase'] not in ('returning', 'idle'):
                current['phase'] = self.link.performance_end(current['token'])
                current['next_pulse'] = now+.14
            elif now >= current['next_pulse']:
                current['phase'] = self.link.performance_pulse(current['token'])
                current['next_pulse'] = now+.14
            if current['phase'] == 'idle':
                self._retire_performance('performance_return_commanded', 'succeeded')
            elif now >= current['deadline']:
                self.link.hold()
                self._retire_performance('performance_budget_held', 'unknown')
        except LegacyArmError as error:
            self.last_error = error.code
            if error.code in ('stale_playback_start', 'performance_pose_unsuitable', 'performance_busy'):
                self._retire_performance(error.code, 'rejected')
            else:
                # A missing ACK/STOP/GP7/fault never earns an automatic return.
                self.fault = 'arm_link_unknown'
                self._retire_performance(error.code, 'unknown')

    def _pending_fresh(self, expected=None):
        if self.pending is None or expected is not None and self.pending is not expected:
            return False
        _, _, started_at, context, explicit, queued_at = self.pending
        now = _clock_value(self.clock)
        return (_finite_time(now) and now >= queued_at and self.context == context
                and (explicit or _finite_time(started_at) and 0 <= now - started_at <= self.ttl))

    def _expire_pending(self):
        request_id = self.pending[0]
        self.pending = None
        self.events.append(FeatureEvent("motion_result", self.generation, request_id,
            "stale_playback_start", {"status": "rejected", "simulated": False,
            "executed": False, "completed": False, "completion": "not_started",
            "position_verified": False}))

    def _event(self, pending, status, code, *, executed=False):
        self.events.append(FeatureEvent("motion_result", pending[3][2], pending[0],
            code, {"status": status, "simulated": False, "executed": executed,
            "completed": False, "completion": "position_unverified" if executed or status == "unknown" else "not_started",
            "position_verified": False}))

    def _clear_active(self):
        self.active_until = None
        self._active_pending = None

    def poll(self):
        self.poll_imu()
        if self.closed or self.stopped or self.fault:
            result, self.events = tuple(self.events), []
            return result
        if self.performance is not None:
            self._poll_performance()
            result, self.events = tuple(self.events), []
            return result
        pending = self.pending
        attempted_pending = None
        dispatched_pending = None
        try:
            if pending is not None:
                if not self._pending_fresh(pending):
                    self._expire_pending()
                elif self.link.boot is None:
                    self.link.hello()
                if self.pending is pending:
                    if not self._pending_fresh(pending):
                        self._expire_pending()
                    else:
                        request_id, gesture = self.pending[:2]
                        attempted_pending = self._inflight_pending = pending
                        self.link.gesture(gesture, before_write=lambda: self._pending_fresh(pending))
                        dispatched_pending = pending
                        # Cancellation, a newer turn, or expiry can happen while
                        # the device ACK is in flight. The DO already reached
                        # the controller: never call this a zero-write expiry or
                        # resurrect the cancelled trajectory. HOLD once while
                        # the exact boot/link is still known; never retry DO.
                        if not self._pending_fresh(pending):
                            if self.pending is pending:
                                self.pending = None
                            self.link.hold()
                            self._event(pending, "cancelled", "obsolete_motion_held", executed=True)
                            result, self.events = tuple(self.events), []
                            return result
                        self.pending = None
                        now = _clock_value(self.clock)
                        if not _finite_time(now):
                            self.link.hold()
                            raise LegacyArmError("invalid_motion_clock")
                        self.last_gesture = now
                        self.active_until = now + ACTIVE_BUDGET_S
                        self._active_pending = pending
                        self.next_ping = now
                        self.events.append(FeatureEvent("motion_result", self.generation, request_id,
                            "ack_position_unverified", {"status": "running", "simulated": False,
                            "executed": True, "completed": False,
                            "completion": "position_unverified", "position_verified": False}))
            if self.active_until is not None:
                now = _clock_value(self.clock)
                if not _finite_time(now) or now < self.last_gesture:
                    self.link.hold()
                    raise LegacyArmError("invalid_motion_clock")
                if now >= self.active_until:
                    self.link.hold()
                    self._event(self._active_pending, "unknown", "gesture_budget_held", executed=True)
                    self._clear_active()
                elif now >= self.next_ping:
                    self.link.ping()
                    after_ping = _clock_value(self.clock)
                    if not _finite_time(after_ping) or after_ping < now:
                        self.link.hold()
                        raise LegacyArmError("invalid_motion_clock")
                    self.next_ping = after_ping + .14
        except LegacyArmError as error:
            if error.code == "stale_playback_start":
                if self.pending is pending:
                    self._expire_pending()
                elif attempted_pending is not None:
                    self._event(attempted_pending, "cancelled", "gesture_cancelled")
                result, self.events = tuple(self.events), []
                return result
            self.fault = "arm_link_unknown"
            self.last_error = error.code
            if dispatched_pending is not None:
                self._event(dispatched_pending, "unknown", "arm_link_unknown", executed=True)
                # A replacement turn can be queued by the owner while the old
                # ACK returns. It has sent no DO and must not inherit the old
                # command's receipt or survive this unknown transport.
                if self.pending is not None and self.pending is not dispatched_pending:
                    self._event(self.pending, "cancelled", "arm_link_unknown")
            elif attempted_pending is not None:
                # A partial write or missing ACK may already have started the
                # old command, including when cancel cleared the pending slot.
                self._event(attempted_pending, "unknown", "arm_link_unknown")
                if self.pending is not None and self.pending is not attempted_pending:
                    self._event(self.pending, "cancelled", "arm_link_unknown")
            elif self.pending is not None:
                self.events.append(FeatureEvent("motion_result", self.generation,
                    self.pending[0], "arm_link_unknown", {"status": "unknown",
                    "simulated": False, "executed": False, "completed": False,
                    "completion": "position_unverified", "position_verified": False}))
            elif self._active_pending is not None:
                self._event(self._active_pending, "unknown", "arm_link_unknown", executed=True)
            self.pending = None
            self._clear_active()
        finally:
            self._inflight_pending = None
        result, self.events = tuple(self.events), []
        return result

    def cancel(self, request_id=None):
        if self.performance is not None:
            if request_id in (None, self.performance['pending'][0]):
                self.finish_performance(reason='cancel')
            return
        if self.pending is not None and request_id not in (None, self.pending[0]):
            return
        if (self.pending is None and self._active_pending is not None
                and request_id not in (None, self._active_pending[0])):
            return
        pending, self.pending = self.pending, None
        if pending is not None and pending is not self._inflight_pending:
            self._event(pending, "cancelled", "gesture_cancelled")
        active = self._active_pending
        if self.active_until is not None and not self.link.unknown:
            try:
                self.link.hold()
            except LegacyArmError as error:
                self.fault = "arm_link_unknown"
                self.last_error = error.code
        if active is not None:
            self._event(active, "unknown" if self.fault else "cancelled",
                "arm_link_unknown" if self.fault else "gesture_cancelled", executed=True)
        self._clear_active()

    def stop(self):
        if self.telemetry_only:
            self.stopped=True
            return
        if self.stopped:
            return
        if self.performance is not None:
            self._retire_performance('performance_stopped', 'cancelled')
            # HOLD/STOP has priority over queued normal returns, including while
            # the start acknowledgement is in flight. No END is sent here.
            if self.link.boot is not None and not self.link.unknown and not self.link.stopped:
                try:
                    self.link.hold()
                except LegacyArmError as error:
                    self.fault = 'arm_link_unknown'; self.last_error = error.code
        self.cancel()
        self.stopped = True
        try:
            self.link.stop()
        except LegacyArmError as error:
            self.fault = "stop_unverified"
            self.last_error = error.code

    def prepare(self):
        if self.telemetry_only:raise FeatureError('imu_read_only_transport')
        if self.closed or self.pending is not None or self.active_until is not None or self._inflight_pending is not None or self.performance is not None:
            raise FeatureError("arm_prepare_busy_or_closed")
        try:
            self.link.prepare()
        except LegacyArmError as error:
            self.last_error = error.code
            raise FeatureError(error.code) from None
        # Request and turn ledgers survive preparation. No obsolete gesture is
        # resurrected and no new gesture is scheduled by this state transition.
        self.stopped = False
        self.fault = self.last_error = None
        self.context = None
        return self.snapshot()

    def close(self):
        if not self.closed:
            if self._imu_poller is not None:
                self._imu_poller.close();self._imu_poller=None
            self.stop()
            try:
                self.link.close()
            except LegacyArmError as error:
                self.fault = self.last_error = error.code
                raise FeatureError("arm_cleanup_failed") from None
            self.closed = True

    def cleanup_snapshot(self):
        clean = self.closed and self.link.closed
        return {"resources_cleaned": clean, "port_closed": self.link.closed,
                "code": "resources_cleaned" if clean else "arm_cleanup_pending",
                "physical_release_verified": False}

    def snapshot(self):
        closed = self.closed or self.link.closed
        stopped = self.stopped or self.link.stopped
        unknown = self.fault or self.link.unknown
        usable = not (closed or stopped or unknown) and self.link.boot is not None
        reason = ("arm_closed" if closed else "arm_stopped" if stopped else
                  self.fault or "arm_link_unknown" if unknown else
                  "position_unverified" if usable else "hello_required")
        transport = ("CLOSED" if self.link.closed else "STOPPED" if self.link.stopped else
                     "UNKNOWN" if self.link.unknown else
                     "ACKNOWLEDGED" if self.link.boot is not None else "NOT_CONNECTED")
        blocked = []
        if self.link.last_response_code == 'DISABLED':
            blocked.append('arm_board_disabled')
        if self.last_error == 'physical_supervision_required':
            blocked.append('arm_operator_condition_missing')
        elif self.last_error == 'daily_arm_permission_required':
            blocked.append('arm_runtime_not_ready')
        if closed:
            blocked.append('arm_closed')
        elif stopped:
            blocked.append('arm_stopped')
        if unknown and not blocked:
            blocked.append('arm_transport_error')
        operation = ('closed' if closed else 'stopped' if stopped else 'fault' if unknown
                     else 'queued' if self.pending is not None or self.performance is not None and self.performance['phase']=='queued'
                     else 'active' if self.active_until is not None or self.performance is not None else 'idle')
        return {"simulated": False, "real_enabled": True,
                "readiness": "BLOCKED" if closed or stopped else "READY" if usable else "UNVERIFIED",
                "reason_code": reason,
                "last_error_code": self.last_error,
                "queued": int(self.pending is not None),
                "active": self.pending is not None or self.active_until is not None or self.performance is not None,
                "stopped": stopped, "closed": closed,
                "supported_gestures": sorted(NAMED_GESTURES), "side_reference": "bmo_own",
                "blocked_reasons": blocked, "operation": operation,
                "cancel_semantics": "hold_current_commanded_position",
                "speech_cancel_semantics": "bounded_return_only_with_known_active_token",
                "performance": (dict(profile=self.performance['profile'], phase=self.performance['phase'],
                    finish_reason=self.performance['finish_reason'], turn_id=self.performance['pending'][3][1],
                    position_verified=False) if self.performance is not None else self.last_performance),
                "performance_supported": self.link.performance_supported,
                "stop_semantics": "board_latch_not_cleared_by_host_restart_not_power_cut",
                "position_verified": False, "physical_verified": False,
                "prepare_code": self.link.prepare_code,
                "prepare_requires_explicit_user_action": True,
                "preflight": {"status": "BLOCKED" if closed or stopped or unknown else "CONFIGURED",
                              "scope": "software_adapter",
                              "code": reason if closed or stopped or unknown else "supervised_launcher_required"},
                "transport": {"protocol": "bmo_legacy_b1", "status": transport,
                              "negotiated": self.link.boot is not None,
                              "last_response_code": self.link.last_response_code,
                              "usable": usable},
                "controller": {"protocol": "bmo_legacy_b1",
                               "negotiated": self.link.boot is not None,
                               "link_unknown": self.link.unknown}}
