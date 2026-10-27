"""STAGING ONLY. Do not copy to the user's RP2040 without a supervised backup.

Enabling the extra dedicated data CDC interface can exhaust USB endpoints or
change enumeration; the current USB HID must be verified before deployment.
This does not open a motor or change GP7 by itself. Runs ONLY as boot.py on MCU.
"""
import usb_cdc

# Keep the interactive console separate from the binary command port.
# If this fails from endpoint exhaustion, CircuitPython may enter safe mode.
usb_cdc.enable(console=True, data=True)
