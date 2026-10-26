# STAGING ONLY. Install as CIRCUITPY/boot.py only after backing up
# existing firmware and disconnecting both servo power rails.
# A separate data CDC endpoint is required; the console remains intact.
import usb_cdc
usb_cdc.enable(console=True, data=True)
