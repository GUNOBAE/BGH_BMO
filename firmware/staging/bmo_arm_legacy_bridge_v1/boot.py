"""Staging only. Extra USB CDC data interface; preserves HID and console.

Requires both servos disconnected for first deployment. Existing boot.py
must be reviewed and backed up: USB enumeration can change.
"""
import usb_cdc

usb_cdc.enable(console=True, data=True)
