import time
import board
import digitalio
import pwmio
import usb_hid
import usb_cdc
import os

from bmo_arm_bridge import LegacyBridge

# Remote motor commands are OFF in the staged install.
# Only a separately approved exact-hash firmware may flip this to True.
REMOTE_ENABLED = False

from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

# =========================
# USB HID Keyboard
# =========================

kbd = Keyboard(usb_hid.devices)

pins = [
    board.GP2,  # UP
    board.GP3,  # LEFT
    board.GP4,  # RIGHT
    board.GP5,  # DOWN
    board.GP6,  # Z
    board.GP7,  # RED BUTTON -> ARM TOGGLE
    board.GP8,  # ENTER
]

keys = [
    Keycode.UP_ARROW,
    Keycode.LEFT_ARROW,
    Keycode.RIGHT_ARROW,
    Keycode.DOWN_ARROW,
    Keycode.Z,
    None,
    Keycode.ENTER,
]

buttons = []

for p in pins:
    b = digitalio.DigitalInOut(p)
    b.direction = digitalio.Direction.INPUT
    b.pull = digitalio.Pull.UP
    buttons.append(b)

last_states = [True] * len(buttons)

# =========================
# Servo
# =========================

servo_init_failed = False
right_servo = left_servo = None
try:
    right_servo = pwmio.PWMOut(board.GP10, frequency=50)
except Exception:
    servo_init_failed = True
try:
    left_servo = pwmio.PWMOut(board.GP11, frequency=50)
except Exception:
    servo_init_failed = True

SERVO_MIN_US = 600
SERVO_MAX_US = 2400

def servo_angle(servo, angle):
    angle = max(0, min(180, angle))

    pulse_us = (
        SERVO_MIN_US
        + (SERVO_MAX_US - SERVO_MIN_US)
        * angle / 180
    )

    servo.duty_cycle = int((pulse_us / 20000) * 65535)

def set_arms(angle):
    # 실제 BMO 좌우 대칭 방향
    left_angle = 180 - angle
    right_angle = angle

    servo_angle(left_servo, left_angle)
    servo_angle(right_servo, right_angle)

    print("ARM =", angle)

# 부팅 시 팔 아래
arm_angle = 0
boot_servo_failed = servo_init_failed
try:
    set_arms(arm_angle)
except Exception:
    # A failed boot PWM write must not prevent the proven HID buttons loading.
    # Original mapping/endpoints remain in set_arms(); no new calibration here.
    boot_servo_failed = True

print("BMO CONTROLLER READY")

# Optional named-gesture host bridge. Original PWM and set_arms stay unchanged.
def set_one_arm(side, physical_angle):
    if side == "left":
        servo_angle(left_servo, 180 - physical_angle)
    elif side == "right":
        servo_angle(right_servo, physical_angle)

def set_both_from_host(angle):
    # Keep the original GP7 logical toggle in sync after explicit both_up/down.
    # servo_angle() and set_arms() remain byte-for-byte unchanged.
    # Automatic interpolation uses the exact same mapping without printing
    # every 20 ms to the console; GP7 still invokes original set_arms().
    global arm_angle
    set_one_arm("left", angle)
    set_one_arm("right", angle)
    if angle in (0, 180):
        arm_angle = angle

try:
    bridge_boot_id = "b" + os.urandom(6).hex()
except Exception:
    bridge_boot_id = "boot_disabled"
    REMOTE_ENABLED = False

data_port = None
cdc_init_failed = False
try:
    data_port = usb_cdc.data
    if data_port is not None:
        data_port.timeout = 0
        data_port.write_timeout = 0.03
except Exception:
    data_port = None
    cdc_init_failed = True
bridge = LegacyBridge(data_port, bridge_boot_id, set_one_arm,
                      set_both_from_host, lambda: arm_angle, enabled=REMOTE_ENABLED)
if boot_servo_failed:
    bridge.fault_remote("servo_fault")
elif cdc_init_failed:
    bridge.fault_remote("cdc_fault")

# =========================
# Main Loop
# =========================

while True:
    for i, b in enumerate(buttons):
        val = b.value

        if not val and last_states[i]:

            # 빨간 버튼
            if i == 5:
                bridge.manual_cancel()
                arm_angle = 180 if arm_angle == 0 else 0
                try:
                    set_arms(arm_angle)
                except Exception:
                    bridge.fault_remote("servo_fault")
                else:
                    bridge.manual_pose(arm_angle)

            # 기존 버튼
            elif keys[i] is not None:
                kbd.press(keys[i])

        elif val and not last_states[i]:
            if keys[i] is not None:
                kbd.release(keys[i])

        last_states[i] = val

    try:
        bridge.poll()
    except Exception:
        bridge.fault_remote("bridge_fault")
    time.sleep(0.01)
