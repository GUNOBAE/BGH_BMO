#!/usr/bin/env python3
"""Supervised first BMO arm bench: built-in finite 10us move from verified-up.

Default is read-only PLAN. --prepare creates a PRIVATE LOCAL legacy-verified candidate code.py,
NEVER copies to CIRCUITPY or opens USB. --probe sends HELLO only.
--one-step requires a prepared (manually deployed) exact-sha trial firmware,
and one physical servo connector or no servos, GP7 RED released, and
hands-on confirmation of a physical BMO power-off fallback. No model input.
"""
import argparse
import os
from pathlib import Path
import select
import stat
import sys
import termios
import time
import tty

ROOT = Path(__file__).resolve().parents[1]
SOFTWARE = ROOT / "software"
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))

from bmo_ai.features.motion.arm_host_v2 import ArmHostV2, ArmV2Error, decode_ack, encode_ping
from bmo_ai.features.motion.arm_host_v2 import BoundedCdcExchange
from bmo_ai.features.motion.gesture_rules_v2 import LEGACY_REVIEWED_LIMITS

FW = ROOT / "firmware/staging/bmo_arm_supervised_v1/code.py"
MOUNT = Path("/media/bgh/CIRCUITPY/code.py")
OUTPUT = Path.home() / "BMO_ARM_PREP/legacy_full_swing_v2_code.py"
PORT = Path("/dev/ttyACM1")

# Candidate pulse values derived from the previously used old code.py:
# arm_angle=180 -> left(180-180)=0 -> 600us,
#                  right(180)=180 -> 2400us.
# This has no positional encoder. Only a single inward 10us first step is allowed.
PROFILE = LEGACY_REVIEWED_LIMITS
STEPS = {"left": 610, "right": 2390}


def candidate_text():
    source = FW.read_text(encoding="utf-8")
    checks = (
        ("ARM_OUTPUT_BUILD_ENABLED = False", "ARM_OUTPUT_BUILD_ENABLED = True"),
        ("REVIEWED_CALIBRATION = None", "REVIEWED_CALIBRATION = " + repr(PROFILE)),
        ("controller.manual_toggle()", "controller.manual_toggle()"),
    )
    for original, replacement in checks:
        if source.count(original) != 1:
            raise RuntimeError("firmware_template_unreviewed")
        source = source.replace(original, replacement, 1)
    if "board.GP12" in source or "controller.manual_toggle()" not in source:
        raise RuntimeError("gp7_deadman_not_reviewed")
    compile(source, str(FW), "exec")
    return source


def prepare():
    import hashlib
    text = candidate_text().encode("utf-8")
    root = OUTPUT.parent
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise RuntimeError("output_directory_not_private")
    if OUTPUT.exists() or OUTPUT.is_symlink():
        if OUTPUT.read_bytes() != text:
            raise RuntimeError("existing_trial_code_differs_refuse_overwrite")
    else:
        fd = os.open(OUTPUT, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
    print("Prepared Pi-local review-only candidate:", OUTPUT)
    print("SHA256:", hashlib.sha256(text).hexdigest())
    print("Source NOT written to RP2040. Both servos must remain unplugged.")


def identify_data_port():
    sysdev = (Path("/sys/class/tty") / PORT.name / "device").resolve(strict=True)
    if not (sysdev / "bInterfaceNumber").is_file():
        raise RuntimeError("interface_number_unavailable")
    num = (sysdev / "bInterfaceNumber").read_text().strip()
    label = (sysdev / "interface").read_text().strip()
    product = (sysdev.parent / "product").read_text().strip()
    if not (num == "02" and label.startswith("CircuitPython CDC2")
            and "rp2040" in product.lower()):
        raise RuntimeError("dedicated_cdc_identity_unconfirmed")
    if not stat.S_ISCHR(PORT.stat().st_mode):
        raise RuntimeError("not_a_tty_character_device")
    return label, product


class RawCdcPort:
    """Explicit stdlib POSIX opener. Opens only after identity check and opt-in."""

    def __init__(self):
        self.fd = None
        self.previous = None
        self.timeout = .45
        self.write_timeout = .16
        self.is_open = False

    def __enter__(self):
        identify_data_port()
        fd = os.open(PORT, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
        try:
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
            self.fd, self.previous, self.is_open = fd, old, True
            return self
        except Exception:
            os.close(fd)
            raise

    def write(self, data):
        if not self.is_open or type(data) is not bytes or len(data) > 128:
            raise RuntimeError("not_open_or_invalid_frame")
        _, writable, _ = select.select([], [self.fd], [], self.write_timeout)
        if not writable:
            raise TimeoutError("write_timeout")
        return os.write(self.fd, data)  # No retry after partial write.

    def read_until(self, delim, max_bytes):
        if delim != b"\n" or max_bytes > 128:
            raise RuntimeError("invalid_ack_bounds")
        end = time.monotonic() + self.timeout
        chunks = bytearray()
        while time.monotonic() < end and b"\n" not in chunks:
            t = max(0, end - time.monotonic())
            rd, _, _ = select.select([self.fd], [], [], t)
            if not rd:
                break
            try:
                chunk = os.read(self.fd, max_bytes - len(chunks))
            except BlockingIOError:
                continue
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) >= max_bytes:
                break
        return bytes(chunks)

    def __exit__(self, *_):
        if self.fd is not None:
            if self.previous is not None:
                try: termios.tcsetattr(self.fd, termios.TCSANOW, self.previous)
                except OSError: pass
            os.close(self.fd)
        self.fd, self.is_open = None, False


def require_exact_up_trial():
    import hashlib
    expected = candidate_text().encode("utf-8")
    installed = MOUNT.read_bytes()
    if installed != expected:
        raise RuntimeError("installed_firmware_is_not_up_trial")
    return hashlib.sha256(installed).hexdigest()


def run_probe():
    label, product = identify_data_port()
    print("Dedicated port:", PORT.name, label, product)
    with RawCdcPort() as port:
        exchange = BoundedCdcExchange(port)
        raw = exchange(b"V2 HELLO up_probe\n")
        reply = decode_ack(raw)
        if reply.request_id != "up_probe":
            raise RuntimeError("request_mismatch")
        print("HELLO ACK:", reply.code, "boot:", reply.boot_id,
              "position_verified:", reply.position_verified)
        if reply.code not in ("UNCALIBRATED", "READY"):
            raise RuntimeError("controller_not_ready")


def run_button_status():
    """USB read of the GP7 manual-owner flag. Does not ARM or MOVE any motor."""
    identify_data_port()
    with RawCdcPort() as port:
        exchange = BoundedCdcExchange(port)
        hello = decode_ack(exchange(b"V2 HELLO check_button\\n"))
        if hello.request_id != "check_button" or hello.code != "READY":
            raise RuntimeError("arm_button_status_not_ready")
        reply = decode_ack(exchange(encode_ping("red_state", hello.boot_id, 1)))
        if reply.request_id != "red_state" or reply.boot_id != hello.boot_id:
            raise RuntimeError("arm_button_status_untrusted")
        if reply.code == "BUTTON_OVERRIDE":
            print("GP7 RED TOGGLE CONTROLLER OWNER VERIFIED (PWM pins only; position unverified)")
        elif reply.code == "ALIVE":
            print("GP7 manual toggle not active yet (press RED once with both servos unplugged)")
        else:
            raise RuntimeError("arm_button_status_unknown")


def run_one_step(side, layout):
    if layout not in ("none", side):
        raise RuntimeError("unapproved_servo_connector_layout")
    sha = require_exact_up_trial()
    print("Up-position-only test firmware:", sha)
    print("No automatic gestures, no music or model motor control.")
    print("ONE bounded step:", side, STEPS[side], "us from",
          PROFILE[side]["home_us"], "us")
    if layout == "none":
        expected = "BOTH_3PIN_DISCONNECTED"
    else:
        expected = "ONLY_" + side.upper() + "_3PIN_CONNECTED"
    if input("Confirm physical servo layout (type " + expected + "): ").strip() != expected:
        raise RuntimeError("servo_layout_not_confirmed")
    if input("BMO entire power can be cut immediately? type POWER_OFF_READY: ").strip() != "POWER_OFF_READY":
        raise RuntimeError("power_cutoff_not_confirmed")
    # The red GP7 keeps original both-arm toggle semantics; never press during USB trial.
    if input("Keep GP7 RED released during USB test? type GP7_RELEASED: ").strip() != "GP7_RELEASED":
        raise RuntimeError("gp7_conflict")
    with RawCdcPort() as port:
        exchange = BoundedCdcExchange(port)
        host = ArmHostV2(exchange, profile=PROFILE,
                        power_cutoff_confirmed=lambda: True,
                        operator_present=lambda: True)
        armed = False
        try:
            ack = host.hello("bench_hello")
            print("HELLO:", ack.code, "boot:", ack.boot_id)
            if ack.code != "READY":
                raise RuntimeError("no_real_calibration_ready")
            host.arm("bench_arm")
            armed = True
            print("ARM accepted; current position is NOT measured.")
            host.move("bench_move", side, STEPS[side])
            # 250ms observation window, ping every <=100ms. No blocking prompts.
            until = time.monotonic() + .25
            i = 0
            while time.monotonic() < until:
                time.sleep(.075)
                i += 1
                host.ping("bench_ping" + str(i))
            host.stop("bench_stop")
            armed = False
            print("STOP_LATCHED: software PWM disabled; release GP7 now.")
            print("Actual position not verified by a sensor.")
        finally:
            if armed:
                try:
                    # One best-effort software STOP only, not a guarantee of
                    # electrical motor power cutoff; release GP7 immediately.
                    if not host.unknown:
                        host.stop("bench_abort")
                except Exception:
                    pass
            print("If unexpected motion occurs, cut ALL BMO power. USB STOP latches until RP2040 reboots.")


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    action = cli.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--probe", action="store_true")
    action.add_argument("--button-status", action="store_true",
                        help="read manual GP7 controller ownership; no host ARM/MOVE")
    action.add_argument("--one-step", choices=("left", "right"))
    cli.add_argument("--servo-layout", choices=("none", "left", "right"))
    args = cli.parse_args(argv)
    try:
        if args.prepare:
            prepare()
        elif args.probe:
            run_probe()
        elif args.button_status:
            run_button_status()
        elif args.one_step:
            if args.servo_layout is None:
                cli.error("--one-step also needs --servo-layout")
            run_one_step(args.one_step, args.servo_layout)
        else:
            print("PLAN ONLY: old physically verified GP7 red toggles full 0/180 both-arm travel.")
            print("Full range: left 600..2400us, right 2400..600us, initial up 600/2400.")
            print("Execute --prepare, review, then install while both plugs detached.")
            print("USB HELLO: --probe; red-button owner readback: --button-status.")
            print("Motion disabled by default in normal BMO. No host, USB or GPIO touched.")
        return 0
    except (RuntimeError, OSError, ArmV2Error, ValueError) as error:
        print("BMO ARM BENCH STOP:", type(error).__name__,
              getattr(error, "code", str(error)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
