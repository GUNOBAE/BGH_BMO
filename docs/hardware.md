# Hardware interfaces and passive inventory

The conversational application can run without a display, camera, controller,
IMU, or servos. `software/bmo_ai/hardware.py` imports only the Python standard
library and does not open devices. `diagnostics.hardware_inventory()` inspects
kernel metadata and executable presence without operating hardware.

## Implemented boundaries

| Component | Interface or implementation | Verified behavior | Physical integration |
| --- | --- | --- | --- |
| Face | `FacePort`, `FaceState`, `NullFace` | Host state is one of idle/listening/thinking/speaking/error | No renderer installed; `NullFace` records state in memory |
| Camera | `CameraPort`, `CameraFrame` | Typed contract for a bounded capture and cleanup | Protocol only; no capture, driver import, or vision request |
| IMU | `IMUPort`, `IMUSample`, `TelemetryMonitor` | Typed observations, freshness expiry, replay rejection | No sensor access; existing MPU6050 readings are not trusted |
| Buttons | `ButtonPort`, `ButtonEvent`, `TelemetryMonitor` | Bounded queue, stale event removal, consumption once | Button ID-to-GPIO mapping is unverified |
| RP2040 | `parse_telemetry`, `TelemetryDecoder` | Strict proposed v1 JSON schema, bounded framing, malformed-data recovery | No serial transport and no firmware changes |
| Arms | `ArmPort`, `DeniedArmController` | Every movement request raises `ArmMovementDisabledError` | No servo backend; no override flag |

The host application drives face state locally. Cloud output remains conversational
text; it is never interpreted as JSON, evaluated, or forwarded as a controller
command. A future camera backend must capture only when explicitly requested and
must not transmit images automatically. A future motion implementation requires
a separate supervised hardware review; modifying an environment variable cannot
enable movement in this code.

## RP2040 telemetry contract

This is a **proposed host-side schema**, not a claim that the current RP2040
firmware produces it. No legacy message format is guessed, and no messages have
been exchanged with the controller during this development task.

One UTF-8 JSON object is framed by LF; CRLF is accepted by the streaming decoder.
Each example is an observation, never a command:

```json
{"v":1,"type":"heartbeat","seq":1,"timestamp_ms":1200}
{"v":1,"type":"imu","seq":2,"timestamp_ms":1210,"accel_g":[0,0,1],"gyro_dps":[0,0,0],"temperature_c":24.5}
{"v":1,"type":"button","seq":3,"timestamp_ms":1220,"button_id":0,"pressed":true}
```

Rules:

- `v` must be the integer `1`; boolean and floating-point versions are rejected.
- `seq` is a global unsigned 32-bit sequence across all message types.
- `timestamp_ms` is unsigned 32-bit controller uptime. It is informational and
  may wrap. Host freshness uses `time.monotonic()` at receipt, not this timestamp.
- `accel_g` and `gyro_dps` contain exactly three finite numbers in explicit units.
  Supported decoder bounds are ±16 g and ±2000 degrees/second. Temperature is
  bounded to −40 through 125 degrees Celsius as a defensive parser limit.
  These limits do not certify sensor health or appropriate operating temperature.
- `button_id` is an integer from 0 to 31. `pressed` is an actual JSON boolean.
  IDs do not imply any verified physical GPIO wiring or button label.
- Unknown types, missing/extra fields, duplicate keys, non-finite values, invalid
  UTF-8, and oversized frames are rejected. Error messages do not reproduce the
  untrusted frame.
- Frames are at most 512 bytes; `feed()` chunks are at most 4096 bytes. The
  framing buffer permits one additional CR byte only when followed by LF. A long
  line is discarded through LF so its tail cannot be accepted as another packet.
  `end_of_stream()` discards partial frames before a new stream is supplied.
- `TelemetryMonitor` rejects duplicate/backwards sequence numbers and accepts
  normal unsigned wrap. A forward gap of half the sequence space or larger is
  rejected. This protects ordering, not authenticity. After a supervised controller
  restart, replace the monitor; the host does not reset the controller itself.
- The default observation age limit is one second. Heartbeats do not refresh old
  IMU data. The default button queue holds 32 events, drops the oldest on overflow,
  and reports the dropped-event count.

A future transport must use a reviewed, dedicated data port, finite read timeouts,
backpressure, and a supervised reconnect policy. It must not send REPL control
characters, use a console port as a data port, trigger bootloader entry, or reset
the controller on errors. CircuitPython distinguishes the REPL console from its
optional data channel; typical host serial clients assert DTR when opening a
port. See the official [USB CDC documentation](https://docs.circuitpython.org/en/latest/shared-bindings/usb_cdc/)
and [workflow documentation](https://docs.circuitpython.org/en/latest/docs/workflows.html)
(checked 2026-10-13).

## Observed environment on 2026-10-13 KST

The passive snapshot is preserved in
[`reports/20261013-hardware-inventory.json`](reports/20261013-hardware-inventory.json).
Its single inventory run took 3.292 ms in the current process. This measures
metadata reads, not audio, serial, camera, or sensor latency.

| Observation | Result | Limit |
| --- | --- | --- |
| Device tree model | Raspberry Pi 5 Model B Rev 1.0 | Kernel description only |
| OS / architecture | Debian GNU/Linux 13 (trixie), aarch64 | Does not prove all Raspberry Pi OS packages are supported |
| Python | 3.13.5 | Application unit tests use this interpreter |
| Kernel | 6.18.50+rpt-rpi-2712 | Read-only platform information |
| ALSA cards | `vc4hdmi0`, `vc4hdmi1`, `MAX98357A`, `RP2040Zero`, `Device` | Registration does not verify microphone capture or speaker playback |
| Controller sysfs | `ttyACM0` | Port was not opened; identity and protocol were not verified |
| Video sysfs | RP1 camera frontend, Pi ISP and HEVC decoder nodes | Video nodes alone do not identify an attached Camera Module 3 |
| Tool presence | `arecord`, `aplay`, `rpicam-hello`, `v4l2-ctl`, `lsusb` | Inventory did not execute these tools |
| Device node visibility | Unavailable in passive sandbox snapshot; visible in later host doctor snapshot | Visibility differs by execution environment |

The later read-only host doctor report is preserved in
[`reports/20261013-doctor-host.json`](reports/20261013-doctor-host.json). ALSA
listing identified capture `hw:CARD=Device,DEV=0` (USB PnP Sound Device) and
playback `hw:CARD=MAX98357A,DEV=0`, alongside HDMI playback devices. Host audio,
video and `ttyACM0` nodes were visible. These observations resolve the sandbox
visibility limitation but do not verify recording, playback, camera capture,
controller communication or wiring. The doctor opened no audio streams or serial
port and changed no settings.

No audio defaults, ALSA/PipeWire settings, I2S overlays, boot configuration,
storage layout, controller firmware, power settings, or IMU diagnostic artifacts
were changed. No microphone capture, speaker playback, camera capture, sensor
poll, controller reset, REPL interaction, or arm movement was performed by this
hardware implementation and inventory.

The kernel lists the MAX98357A speaker separately from the RP2040 USB audio
device and the USB PnP Sound Device. Maintain the established speaker selection;
do not choose a device based only on its transient numeric card index. The
audio module provides its own discovery and explicit device configuration.

## Existing MPU6050 fault evidence

The pre-existing local report at
`/home/bgh/BMO_AI/imu_diagnostics/20261013_002849_KST/report.txt` was read without
re-running the diagnostic. It reports substantial stationary acceleration/gyro
bias and self-test failures; at ±2 g the X acceleration saturated. It does not
establish a healthy IMU or a proven physical cause.

That diagnostic left the RP2040 in a stopped REPL state and warns that restarting
the existing `code.py` can move the arms. Leave the controller in that state while
working unattended. The parser added here does not make the existing measurements
reliable, and this sensor must not drive orientation claims or movement. Further
power, wiring, soldering, replacement-module and cold-start verification needs a
supervised physical session. No calibration or offsets were applied to hide the
reported faults.

## Camera integration plan

For a Raspberry Pi camera, the maintained direction is libcamera with Picamera2,
not the legacy Picamera stack. Official installation guidance recommends the OS
package `python3-picamera2`; the upstream support statement targets Raspberry Pi
OS. This robot reports Debian 13, so package availability and successful Camera
Module 3 Wide operation remain unverified. See the official
[camera software documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html)
and [Picamera2 repository](https://github.com/raspberrypi/picamera2)
(checked 2026-10-13).

Implement a lazy `CameraPort` adapter only after that environment is verified.
Capture must have a finite timeout, return bounded encoded bytes, and close its
camera on every failed start/capture path. Unit tests should inject a fake camera;
a later supervised test can verify actual capture and autofocus. No camera
dependency or system package was installed by this hardware task.

## Offline verification

From the repository root, run:

```bash
PYTHONPATH=software python3 -m unittest discover -s software/tests -p 'test_hardware.py' -v
PYTHONPATH=software python3 -m unittest discover -s software/tests -p 'test_diagnostics.py' -v
```

These tests cover movement denial, typed face state, strict telemetry validation,
fragmentation, oversized-frame recovery, truncated streams, finite numeric bounds,
sequence replay/wrap, freshness expiry, bounded button queues, fake sysfs/proc
inventory, hidden device nodes, permission failures, and JSON serialization. They
do not exercise physical hardware.
