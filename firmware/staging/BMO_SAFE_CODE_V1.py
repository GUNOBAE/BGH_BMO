"""STAGING CircuitPython code.py — NO automatic arm movement at boot.

DO NOT flash/replace the running RP2040 program until physically verified:
power, both servo directions, travel range, independent STOP and return.
This draft retains GP2..GP6/GP8 USB keyboard inputs; GP7 is EMERGENCY PWM
DISABLE (not the old unsafe 0-to-180 toggle). It requires boot.py CDC data.

No serial console/REPL commands, no self-reset and no automatic gestures.
"""
import board
import digitalio
import json
import os
import pwmio
import time
import usb_cdc
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

PORT = usb_cdc.data
try:
    BOOT_ID = "".join("%02x" % x for x in os.urandom(8))
except (AttributeError, OSError):
    # No reliable boot identity => refuse all real commands.
    BOOT_ID = None

KEYBOARD = Keyboard(usb_hid.devices)
BUTTONS = (
    (board.GP2, Keycode.UP_ARROW),
    (board.GP3, Keycode.LEFT_ARROW),
    (board.GP4, Keycode.RIGHT_ARROW),
    (board.GP5, Keycode.DOWN_ARROW),
    (board.GP6, Keycode.Z),
    (board.GP8, Keycode.ENTER),
)
INPUTS = []
for pin, key in BUTTONS:
    button = digitalio.DigitalInOut(pin)
    button.direction = digitalio.Direction.INPUT
    button.pull = digitalio.Pull.UP
    INPUTS.append((button, key, False, 0.0))
stop_button = digitalio.DigitalInOut(board.GP7)
stop_button.direction = digitalio.Direction.INPUT
stop_button.pull = digitalio.Pull.UP

# Servo PWM allocated with duty=0; no pulse is sent on startup.
PWM = {
    "right": pwmio.PWMOut(board.GP10, frequency=50, duty_cycle=0),
    "left": pwmio.PWMOut(board.GP11, frequency=50, duty_cycle=0),
}
LIMITS = None
try:
    with open("/arm_limits.json", "r") as file:
        settings = json.load(file)
    if (type(settings) is dict and settings.get("version") == 1
            and settings.get("operator_reviewed") is True):
        verified = {}
        for side in ("left", "right"):
            item = settings.get(side)
            if (type(item) is not dict or set(item) != {"minimum", "neutral", "maximum"}
                    or any(type(item[k]) is not int for k in item)
                    or not (0 <= item["minimum"] < item["neutral"] < item["maximum"] <= 180)
                    or item["maximum"] - item["minimum"] > 20):
                raise ValueError("invalid calibration")
            verified[side] = item
        LIMITS = verified
except (OSError, ValueError, KeyError, TypeError):
    LIMITS = None

armed = False
last_heartbeat = 0.0
last_angle = {"left": None, "right": None}
last_seq = -1
line = bytearray()


def stop_arms():
    global armed
    armed = False
    for item in PWM.values():
        item.duty_cycle = 0
    for side in last_angle:
        last_angle[side] = None


def response(seq, code, accepted=False):
    if PORT is None:
        return
    data = {"v": 1, "boot": BOOT_ID or "unavailable",
            "seq": seq, "code": code, "accepted": bool(accepted),
            "position_verified": False}
    try:
        PORT.write((json.dumps(data, separators=(",", ":")) + "\n").encode())
    except (OSError, RuntimeError):
        stop_arms()


def handle(raw):
    global armed, last_heartbeat, last_seq
    try:
        value = json.loads(raw.decode("ascii"))
        if (type(value) is not dict or value.get("v") != 1
                or type(value.get("seq")) is not int
                or not (0 <= value["seq"] <= 2147483647)
                or type(value.get("op")) is not str):
            return
        seq, op = value["seq"], value["op"]
        if op == "hello" and set(value) == {"v", "seq", "op"}:
            response(seq, "ready" if LIMITS and BOOT_ID and PORT else "calibration_missing",
                     bool(LIMITS and BOOT_ID and PORT))
            return
        if seq <= last_seq:
            response(seq, "stale_or_duplicate")
            return
        last_seq = seq
        if op == "stop" and set(value) == {"v", "seq", "op", "boot"}:
            stop_arms()
            response(seq, "stopped", True)
            return
        if (value.get("boot") != BOOT_ID or BOOT_ID is None
                or LIMITS is None or PORT is None or not stop_button.value):
            stop_arms()
            response(seq, "not_ready")
            return
        if op == "arm" and set(value) == {"v", "seq", "op", "boot", "operator_present"}:
            if value["operator_present"] is True:
                armed = True
                last_heartbeat = time.monotonic()
                response(seq, "armed", True)
                return
        if op == "heartbeat" and set(value) == {"v", "seq", "op", "boot"}:
            if armed:
                last_heartbeat = time.monotonic()
                response(seq, "live", True)
                return
        if op == "move" and set(value) == {"v", "seq", "op", "boot", "side", "angle"}:
            side, angle = value["side"], value["angle"]
            if (not armed or side not in LIMITS or type(angle) is not int
                    or not LIMITS[side]["minimum"] <= angle <= LIMITS[side]["maximum"]
                    or time.monotonic() - last_heartbeat >= .6):
                stop_arms()
                response(seq, "movement_denied")
                return
            old = last_angle[side]
            # Initial pulse only at independently reviewed physical neutral.
            if (old is None and angle != LIMITS[side]["neutral"]) or (
                    old is not None and abs(angle - old) > 3):
                response(seq, "step_limit")
                return
            pulse_us = 1000 + angle * 1000 // 180
            PWM[side].duty_cycle = int(pulse_us * 65535 // 20000)
            last_angle[side] = angle
            last_heartbeat = time.monotonic()
            response(seq, "accepted_position_unverified", True)
            return
        response(seq, "unsupported_command")
    except (ValueError, KeyError, TypeError, UnicodeError):
        stop_arms()


stop_arms()
while True:
    now = time.monotonic()
    if armed and now - last_heartbeat >= .6:
        stop_arms()
    if not stop_button.value:
        stop_arms()

    # Keyboard navigation remains independent of host-arm data.
    inputs_updated = []
    for button, key, pressed, changed in INPUTS:
        down = not button.value
        if down != pressed and now - changed >= .03:
            if down:
                KEYBOARD.press(key)
            else:
                KEYBOARD.release(key)
            pressed, changed = down, now
        inputs_updated.append((button, key, pressed, changed))
    INPUTS = inputs_updated

    if PORT is not None and PORT.in_waiting:
        try:
            chunk = PORT.read(min(64, PORT.in_waiting))
        except (OSError, RuntimeError):
            chunk = None
        if chunk:
            for ch in chunk:
                if ch == 10:
                    if 0 < len(line) <= 192:
                        handle(line)
                    line.clear()
                elif ch == 13 or ch > 127 or len(line) >= 192:
                    line.clear()
                    stop_arms()
                else:
                    line.append(ch)
    time.sleep(.01)
