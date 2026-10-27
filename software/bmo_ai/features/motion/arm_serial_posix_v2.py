"""One supervised Linux owner of the verified RP2040 *data* CDC interface.

Default import does nothing. No ttyACM0 console access, port discovery fallback,
serial DTR reset API, generic GPIO, shell execution, or auto firmware update.
Only after explicit live desktop --arms-supervised + reviewed hash validation may
the app open ttyACM1 (user-verified interface02) and reserve it exclusively.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import select
import stat
import termios
import time
import tty


class ArmPortUnavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


DEVICE = Path("/dev/ttyACM1")
MOUNT = Path("/media/bgh/CIRCUITPY")
TRIAL = Path.home() / "BMO_ARM_PREP/legacy_full_swing_v2_code.py"


def validate_reviewed_install(source_core, reviewed_sha):
    """Read-only exact byte verification. Never fix a mismatch or write MCU."""
    if (type(reviewed_sha) is not str or len(reviewed_sha) != 64
            or any(c not in "0123456789abcdef" for c in reviewed_sha)):
        raise ArmPortUnavailable("arm_reviewed_hash_required")
    expected = TRIAL.read_bytes()
    actual = (MOUNT / "code.py").read_bytes()
    if (hashlib.sha256(expected).hexdigest() != reviewed_sha
            or expected != actual):
        raise ArmPortUnavailable("arm_firmware_hash_mismatch")
    if source_core.read_bytes() != (MOUNT / "safe_arm_core.py").read_bytes():
        raise ArmPortUnavailable("arm_firmware_core_mismatch")
    boot = (MOUNT / "boot.py").read_text(encoding="utf-8")
    if "usb_cdc.enable(console=True, data=True)" not in boot:
        raise ArmPortUnavailable("arm_dedicated_data_disabled")
    # Reject accidentally reusing the prior GP7 deadman / narrow-range trial.
    text = actual.decode("utf-8")
    for evidence in ("ARM_OUTPUT_BUILD_ENABLED = True",
                     "controller.manual_toggle()", "'min_us': 600",
                     "'max_us': 2400", "board.GP10", "board.GP11"):
        if evidence not in text:
            # GP10/11 live in same code, not generated from a model.
            raise ArmPortUnavailable("arm_legacy_mapping_unverified")
    return reviewed_sha


def verified_cdc_identity(port=DEVICE):
    """Only the exact observed data interface. Never guess the console port."""
    if port != DEVICE or port.is_symlink():
        raise ArmPortUnavailable("arm_unapproved_serial_port")
    try:
        p = (Path("/sys/class/tty") / port.name / "device").resolve(strict=True)
        number = (p / "bInterfaceNumber").read_text().strip()
        label = (p / "interface").read_text().strip()
        product = (p.parent / "product").read_text().strip()
        if not (number == "02" and label.startswith("CircuitPython CDC2")
                and "rp2040" in product.lower() and stat.S_ISCHR(port.stat().st_mode)):
            raise ArmPortUnavailable("arm_data_port_identity_mismatch")
    except OSError:
        raise ArmPortUnavailable("arm_data_port_unavailable") from None
    return label


class OwnedCdcDataPort:
    timeout = .45
    write_timeout = .18

    def __init__(self, path=DEVICE):
        self.path = path
        self.fd = None
        self.original = None
        self.is_open = False

    def open(self):
        if self.is_open:
            raise ArmPortUnavailable("arm_port_already_open")
        verified_cdc_identity(self.path)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                original = termios.tcgetattr(fd)
                tty.setraw(fd)
            except Exception:
                os.close(fd)
                raise
        except Exception:
            raise ArmPortUnavailable("arm_data_port_busy_or_unavailable") from None
        self.fd, self.original, self.is_open = fd, original, True
        return self

    def write(self, message):
        if not self.is_open or type(message) is not bytes or len(message) > 128:
            raise ArmPortUnavailable("arm_port_closed_or_frame_invalid")
        _, writable, _ = select.select([], [self.fd], [], self.write_timeout)
        if not writable:
            raise ArmPortUnavailable("arm_cdc_write_timeout")
        return os.write(self.fd, message)  # no partial-write retry, ever

    def read_until(self, delimiter, bound):
        if not self.is_open or delimiter != b"\n" or bound != 128:
            raise ArmPortUnavailable("arm_invalid_ack_boundary")
        buffer = bytearray()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline and b"\n" not in buffer:
            readable, _, _ = select.select([self.fd], [], [],
                                           max(0, deadline - time.monotonic()))
            if not readable:
                break
            try:
                block = os.read(self.fd, bound - len(buffer))
            except BlockingIOError:
                continue
            if not block:
                break
            buffer.extend(block)
            if len(buffer) >= bound:
                break
        return bytes(buffer)

    def close(self):
        if self.fd is not None:
            try:
                if self.original is not None:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.original)
            finally:
                os.close(self.fd)
        self.fd = self.original = None
        self.is_open = False
