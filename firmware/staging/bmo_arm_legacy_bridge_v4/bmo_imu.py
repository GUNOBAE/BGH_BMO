"""MPU6050 read-only snapshots on GP0 SDA / GP1 SCL.

The I2C engine is cooperative: no hardware-driver call or wait/sleep can hold
the HID/B1 loop. Each poll has both a monotonic-ns and a step budget. Missing or
clock-stretching sensors affect only these snapshots, never arm readiness.
READY means bytes/configuration were read, NOT that any physical axis is good.
"""
import time

POLL_BUDGET_NS = 1000000
POLL_STEPS = 192
HALF_BIT_NS = 5000
STRETCH_NS = 250000
TRANSFER_NS = 500000000
RETRY_NS = 1000000000
SAMPLE_NS = 50000000
STALE_NS = 1000000000
STATUSES = ("WARMUP", "READY", "ABSENT", "BUS_ERROR", "STALE")


class BusError(Exception):
    pass


class NoAck(BusError):
    pass


class CooperativeI2C:
    """Open-drain GPIO only; every generator step is finite and nonblocking.

    Lines must provide .value with True=release, False=drive low. External
    3.3V pullups are required. This is a fixed sensor register transport, not a
    general shared bus API. No pin beyond these two is read or changed.
    """
    def __init__(self, sda, scl, clock_ns=None):
        self.sda, self.scl = sda, scl
        self.clock_ns = clock_ns or time.monotonic_ns
        self.release()

    def release(self):
        self.sda.value = True
        self.scl.value = True

    def _delay(self):
        until = self.clock_ns() + HALF_BIT_NS
        while self.clock_ns() < until:
            yield

    def _high(self):
        self.scl.value = True
        start = self.clock_ns()
        while not self.scl.value:
            if self.clock_ns() - start >= STRETCH_NS:
                raise BusError("clock_stretch_timeout")
            yield
        yield from self._delay()

    def _start(self):
        self.sda.value = True
        yield from self._high()
        if not self.sda.value:
            raise BusError("data_stuck_low")
        self.sda.value = False
        yield from self._delay()
        self.scl.value = False

    def _stop(self):
        self.scl.value = False
        self.sda.value = False
        yield from self._delay()
        yield from self._high()
        self.sda.value = True
        yield from self._delay()

    def _write(self, value):
        for bit in range(7, -1, -1):
            self.scl.value = False
            self.sda.value = bool(value & (1 << bit))
            yield from self._delay()
            yield from self._high()
            self.scl.value = False
        self.sda.value = True
        yield from self._delay()
        yield from self._high()
        acknowledged = not self.sda.value
        self.scl.value = False
        if not acknowledged:
            raise NoAck("sensor_nack")

    def _read(self, acknowledge):
        self.sda.value = True
        value = 0
        for _ in range(8):
            yield from self._delay()
            yield from self._high()
            value = (value << 1) | bool(self.sda.value)
            self.scl.value = False
        self.sda.value = not acknowledge
        yield from self._delay()
        yield from self._high()
        self.scl.value = False
        self.sda.value = True
        return int(value)

    def recover(self):
        # Finite sensor-only bus recovery, no RP2040 reset and no PWM writes.
        self.sda.value = True
        for _ in range(9):
            self.scl.value = False
            yield from self._delay()
            yield from self._high()
        yield from self._stop()

    def transfer(self, address, register, *, count=0, value=None):
        if address not in (0x68, 0x69) or not 0 <= register <= 0x7f:
            raise BusError("invalid_sensor_transaction")
        if not 0 <= count <= 14 or (count == 0) == (value is None):
            raise BusError("invalid_sensor_transaction")
        yield from self._start()
        yield from self._write(address << 1)
        yield from self._write(register)
        if value is not None:
            yield from self._write(value)
            yield from self._stop()
            return b""
        yield from self._start()
        yield from self._write((address << 1) | 1)
        raw = bytearray()
        for i in range(count):
            raw.append((yield from self._read(i + 1 < count)))
        yield from self._stop()
        return bytes(raw)


def _signed(raw, offset):
    value = raw[offset] * 256 + raw[offset + 1]
    return value - 65536 if value & 32768 else value


class MPU6050:
    def __init__(self, bus, clock_ns=None):
        self.bus = bus
        self.clock_ns = clock_ns or time.monotonic_ns
        self.sequence = 0
        self.timestamp_ms = 0
        self.status = "WARMUP"
        self.axes = None
        self.sensor_id = "In"
        self.address = 0x68
        self.identity = None
        self._task = None
        self._task_started = None
        self._sample_at = None
        self._next_at = 0
        self._configured = False
        self.last_steps = 0
        self.errors = 0

    def _initialize(self):
        yield from self.bus.recover()
        found = False
        for address in (0x68, 0x69):
            try:
                raw = yield from self.bus.transfer(address, 0x75, count=1)
            except NoAck:
                yield from self.bus.recover()
                continue
            # WHO_AM_I does not contain a unit serial, health or axis quality.
            if raw and raw[0] & 0x7e == 0x68:
                self.address, self.identity = address, raw[0]
                self.sensor_id = "I%02XA%02X" % (raw[0], address)
                found = True
                break
        if not found:
            raise NoAck("sensor_absent_or_identity_mismatch")
        # Fixed +/-2g, +/-250 dps; no self-test, calibration or bias subtraction.
        for register, value in ((0x6b, 1), (0x19, 9), (0x1a, 3), (0x1b, 0), (0x1c, 0)):
            yield from self.bus.transfer(self.address, register, value=value)
        raw = yield from self.bus.transfer(self.address, 0x1b, count=2)
        if len(raw) != 2 or raw[0] & 0xf8 or raw[1] & 0xf8:
            raise BusError("range_readback_mismatch")
        self._configured = True
        self._next_at = self.clock_ns() + 100000000  # Wake settling; no sleep.

    def _sample(self):
        raw = yield from self.bus.transfer(self.address, 0x3b, count=14)
        if len(raw) != 14:
            raise BusError("short_sensor_read")
        # Temperature bytes6..7 are deliberately skipped, never mixed in axes.
        axes = tuple(_signed(raw, offset) for offset in (0, 2, 4, 8, 10, 12))
        now = self.clock_ns()
        self.axes = axes
        self.timestamp_ms = (now // 1000000) & 0xffffffff
        self.sequence = (self.sequence + 1) & 0xffffffff
        self.status = "READY"
        self._sample_at = now
        self._next_at = now + SAMPLE_NS

    def _error(self, status, now):
        self.status, self.axes = status, None
        self._configured = False
        self._task = self._task_started = None
        self._next_at = now + RETRY_NS
        self.errors += 1
        try:
            self.bus.release()
        except Exception:
            pass

    def poll(self):
        # This limit is independent of a clock that fails/stops advancing.
        self.last_steps = 0
        try:
            now = self.clock_ns()
            if self._task is None:
                if now < self._next_at:
                    return
                self._task = self._sample() if self._configured else self._initialize()
                self._task_started = now
            if now - self._task_started > TRANSFER_NS:
                self._error("BUS_ERROR", now)
                return
            deadline = now + POLL_BUDGET_NS
            for _ in range(POLL_STEPS):
                if self.clock_ns() >= deadline:
                    break
                self.last_steps += 1
                try:
                    next(self._task)
                except StopIteration:
                    self._task = self._task_started = None
                    break
        except NoAck:
            self._error("ABSENT", self.clock_ns())
        except Exception:
            # Sensor failure is deliberately not bridge.fault_remote().
            self._error("BUS_ERROR", self.clock_ns())

    def frame(self, rid, boot):
        status = self.status
        if status == "READY" and self._sample_at is not None and self.clock_ns() - self._sample_at > STALE_NS:
            status = "STALE"
        axes = self.axes if status == "READY" and self.axes is not None else (None,) * 6
        values = " ".join("n" if value is None else str(value) for value in axes)
        return ("B1 IMU %s %s %d %d %s %s A2 G250 %s\n" %
                (rid, boot, self.sequence, self.timestamp_ms, status, values, self.sensor_id)).encode("ascii")


class UnavailableIMU:
    """Initialization failure still permits read-only diagnostic snapshots."""
    def poll(self):
        pass

    def frame(self, rid, boot):
        return ("B1 IMU %s %s 0 0 BUS_ERROR n n n n n n A2 G250 In\n" % (rid, boot)).encode("ascii")
