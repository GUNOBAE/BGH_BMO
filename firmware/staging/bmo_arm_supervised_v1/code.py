"""STAGING ONLY: NOT A DROP-IN REPLACEMENT FOR THE USER'S LIVE RP2040.

Never copy this code to CIRCUITPY without a supervised physical/firmware review.
Keeps GP8 Enter and directions as HID. GP7 is a hold-to-enable software
interlock for supervised bench work; release latches STOP. NOT a power cutoff.
There is intentionally NO servo PWM on boot and NO enabled motor command by default.
A separate physical servo-power cutoff remains mandatory.
"""
import os
import time

import board
import digitalio
import pwmio
import usb_cdc
import usb_hid

from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from safe_arm_core import SafeArmController, MAX_FRAME

# Requires code review, measured calibration and the EXISTING GP7 red button
# held continuously by a human for any eventual ARM/MOVE. Release latches STOP.
# A software stop is NOT an electrical power cutoff. Keep these OFF by default.
ARM_OUTPUT_BUILD_ENABLED = False
REVIEWED_CALIBRATION = None
# Explicit review must supply {'reviewed': True,
#  'left': {'min_us':..., 'home_us':..., 'max_us':...},
#  'right': {'min_us':..., 'home_us':..., 'max_us':...}}
# Correct angles and mechanical limits CANNOT be inferred from 50 Hz or 0/180.
# No GP12 input is assumed: the existing, known GP7 button is the bench deadman.

POLL_INTERVAL_S = 0.01

class Pins:
    def __init__(self):
        self.kbd = Keyboard(usb_hid.devices)
        self.buttons = []
        for pin, key in (
            (board.GP2, Keycode.UP_ARROW), (board.GP3, Keycode.LEFT_ARROW),
            (board.GP4, Keycode.RIGHT_ARROW), (board.GP5, Keycode.DOWN_ARROW),
            (board.GP6, Keycode.Z), (board.GP8, Keycode.ENTER)
        ):
            input_pin = digitalio.DigitalInOut(pin)
            input_pin.direction = digitalio.Direction.INPUT
            input_pin.pull = digitalio.Pull.UP
            self.buttons.append([input_pin, key, False, 0.0])
        self.red = digitalio.DigitalInOut(board.GP7)
        self.red.direction = digitalio.Direction.INPUT
        self.red.pull = digitalio.Pull.UP
        self.red_pressed = False

    def physical_enabled(self):
        return (ARM_OUTPUT_BUILD_ENABLED and REVIEWED_CALIBRATION is not None
                and self.red.value is False)

    def poll(self, controller):
        now = time.monotonic()
        for item in self.buttons:
            io, key, was_pressed, changed_at = item
            pressed = io.value is False
            if pressed != was_pressed and now - changed_at >= 0.03:
                item[2] = pressed
                item[3] = now
                try:
                    if pressed:
                        self.kbd.press(key)
                    else:
                        self.kbd.release(key)
                except Exception:
                    # Losing HID cannot be used as permission to operate arms.
                    controller.emergency_stop("hid_error")
        red = self.red.value is False
        if self.red_pressed and not red and controller.armed:
            # Release latches STOP only if motor outputs were actually armed;
            # a GP7 press/release while disabled must not poison a later session.
            controller.emergency_stop("gp7_hold_released")
        self.red_pressed = red

class ServoOutputs:
    """No PWM instance is allocated during construction or before real ARM."""
    def __init__(self):
        self.channels = {}

    def arm(self, homes):
        if not ARM_OUTPUT_BUILD_ENABLED or REVIEWED_CALIBRATION is None:
            raise RuntimeError("motor_activation_not_approved")
        self.stop()
        pending = {}
        try:
            for name, pin in (("left", board.GP11), ("right", board.GP10)):
                out = pwmio.PWMOut(pin, duty_cycle=0, frequency=50)
                pending[name] = out
            for name in ("left", "right"):
                pending[name].duty_cycle = self._duty(homes[name])
            self.channels = pending
        except Exception:
            for out in pending.values():
                try:out.deinit()
                except Exception:pass
            raise

    @staticmethod
    def _duty(pulse_us):
        if type(pulse_us) is not int or not 500 <= pulse_us <= 2500:
            raise ValueError("invalid pulse")
        return (pulse_us * 65535) // 20000  # 50Hz period is 20,000 microseconds.

    def set_pulse(self, side, pulse_us):
        if side not in ("left", "right") or side not in self.channels:
            raise RuntimeError("servo_channel_not_owned")
        self.channels[side].duty_cycle = self._duty(pulse_us)

    def stop(self):
        old, self.channels = self.channels, {}
        failure = False
        for channel in old.values():
            try:
                channel.duty_cycle = 0
            except Exception:
                failure = True
            try:
                channel.deinit()
            except Exception:
                failure = True
        if failure:
            raise RuntimeError("pwm_release_unverified")
        # PWM disabled != actual servo power removed; external cutoff required.

def main():
    pins = Pins()
    outputs = ServoOutputs()
    # A fresh boot identifier rejects queued old commands after a controller reset.
    try:
        boot_id = "b" + os.urandom(8).hex()
        boot_identity_verified = True
    except Exception:
        # Keep existing HID buttons alive, but do not ever unlock real arms
        # if a unique per-boot controller ID cannot be generated.
        boot_id = "boot_unverified"
        boot_identity_verified = False
    controller = SafeArmController(
        outputs, boot_id, settings=REVIEWED_CALIBRATION,
        physical_enabled=lambda: boot_identity_verified and pins.physical_enabled())
    stream = usb_cdc.data  # Dedicated interface; NEVER read usb_cdc.console / REPL.
    if stream is not None:
        stream.timeout = 0
        # Default is None (infinite wait). Blocked writes must not freeze
        # HID inputs or prevent the 350ms controller watchdog from running.
        stream.write_timeout = 0.05
    buffer = bytearray()
    discarding = False
    try:
        while True:
            pins.poll(controller)
            controller.tick()
            if stream is not None and stream.in_waiting:
                try:
                    chunk = stream.read(min(stream.in_waiting, 128))
                except Exception:
                    controller.emergency_stop("data_read_failed")
                    chunk = b""
                if chunk:
                    for octet in chunk:
                        if octet == 10:
                            if not discarding:
                                response = controller.handle(bytes(buffer) + b"\n")
                                try:
                                    if stream.write(response) != len(response):
                                        controller.emergency_stop("data_partial_write")
                                except Exception:
                                    controller.emergency_stop("data_write_failed")
                            buffer.clear()
                            discarding = False
                        elif octet == 13 or len(buffer) >= MAX_FRAME - 1:
                            buffer.clear()
                            discarding = True
                        elif not discarding:
                            buffer.append(octet)
            time.sleep(POLL_INTERVAL_S)
    finally:
        controller.emergency_stop("exit")
        try:pins.kbd.release_all()
        except Exception:pass

if __name__ == "__main__":
    main()
