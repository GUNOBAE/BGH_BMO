"""Passive hardware inventory for deployments with restricted device visibility.

Reads only procfs/sysfs descriptions and executable presence. It never runs an
external tool, opens a device node, imports a camera driver, or alters settings.
"""
from __future__ import annotations

from pathlib import Path
import platform
import re
import shutil
from typing import Callable


_READ_LIMIT = 16_384


def _read_text(root: Path, path: str, *, limit: int = _READ_LIMIT) -> dict:
    target = root / path.lstrip("/")
    try:
        with target.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as error:
        return {
            "status": "unavailable",
            "path": path,
            "reason": type(error).__name__,
        }
    return {
        "status": "observed",
        "path": path,
        "value": data[:limit].decode("utf-8", errors="replace").rstrip("\x00\r\n"),
        "truncated": len(data) > limit,
    }


def _list_names(root: Path, path: str, pattern: str) -> dict:
    try:
        names = sorted(
            child.name
            for child in (root / path.lstrip("/")).iterdir()
            if re.fullmatch(pattern, child.name)
        )
    except OSError as error:
        return {
            "status": "unavailable",
            "path": path,
            "reason": type(error).__name__,
            "entries": [],
        }
    return {"status": "observed", "path": path, "entries": names}


def _node_visibility(observation: dict, *, kernel_count: int) -> dict:
    if not observation["entries"] and kernel_count:
        observation = dict(observation)
        observation["status"] = "unavailable_in_current_view"
        observation["note"] = (
            "Kernel entries exist but matching /dev nodes are not visible. "
            "Sandbox/container visibility or device policy may differ from the host."
        )
    return observation


def hardware_inventory(
    *, root: Path | str = "/", which: Callable[[str], str | None] = shutil.which
) -> dict:
    """Return a JSON-serializable observation, not proof that hardware operates.

    ``root`` and ``which`` allow fake-filesystem unit tests without any hardware.
    Missing paths describe this process's view, never absence of physical devices.
    Tool paths describe executables on PATH; no tool is executed.
    """
    root = Path(root)
    model = _read_text(root, "/proc/device-tree/model", limit=256)
    if model["status"] != "observed":
        model = _read_text(root, "/sys/firmware/devicetree/base/model", limit=256)

    os_release = _read_text(root, "/etc/os-release")
    if os_release["status"] == "observed":
        # Read descriptive fields only; do not source a shell file.
        selected = {}
        for line in os_release.pop("value").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"PRETTY_NAME", "ID", "VERSION_ID"}:
                selected[key] = value.strip().strip('"').strip("'")
        os_release["fields"] = selected

    sound = _list_names(root, "/sys/class/sound", r"card[0-9]+")
    sound["cards"] = [
        {"name": name, "id": _read_text(root, f"/sys/class/sound/{name}/id", limit=128)}
        for name in sound["entries"]
    ]
    audio_nodes = _node_visibility(
        _list_names(root, "/dev/snd", r"(?:controlC[0-9]+|pcmC[0-9]+D[0-9]+[pc])"),
        kernel_count=len(sound["entries"]),
    )

    controller = _list_names(root, "/sys/class/tty", r"tty(?:ACM|USB)[0-9]+")
    controller_nodes = _node_visibility(
        _list_names(root, "/dev", r"tty(?:ACM|USB)[0-9]+"),
        kernel_count=len(controller["entries"]),
    )
    camera = _list_names(root, "/sys/class/video4linux", r"video[0-9]+")
    camera["devices"] = [
        {
            "name": name,
            "description": _read_text(root, f"/sys/class/video4linux/{name}/name", limit=256),
        }
        for name in camera["entries"]
    ]
    camera_nodes = _node_visibility(
        _list_names(root, "/dev", r"video[0-9]+"),
        kernel_count=len(camera["entries"]),
    )

    tools = {}
    for name in ("arecord", "aplay", "rpicam-hello", "libcamera-hello", "v4l2-ctl", "lsusb"):
        executable = which(name)
        tools[name] = {
            "status": "available" if executable else "unavailable",
            "path": executable,
            "executed": False,
        }

    return {
        "schema_version": 1,
        "mode": "read_only_inventory",
        "platform": {
            "model": model,
            "os_release": os_release,
            "machine": platform.machine(),
            "kernel_release": platform.release(),
            "python": platform.python_version(),
        },
        "audio": {
            "proc_cards": _read_text(root, "/proc/asound/cards"),
            "sysfs": sound,
            "device_nodes": audio_nodes,
            "capture_test": "not_performed",
            "playback_test": "not_performed",
        },
        "controller": {
            "sysfs": controller,
            "device_nodes": controller_nodes,
            "port_opened": False,
            "identity_verified": False,
            "note": "A tty name alone does not identify a safe controller data port.",
        },
        "camera": {
            "sysfs": camera,
            "device_nodes": camera_nodes,
            "capture_test": "not_performed",
            "note": "V4L2 entries can be codecs or ISP nodes; they do not prove camera presence.",
        },
        "arms": {"backend": "denied", "movement_test": "not_performed"},
        "imu": {"sample_test": "not_performed", "health": "unverified"},
        "tools": tools,
        "limitations": [
            "Passive inventory cannot verify wiring, signal quality, device health, or permissions to operate.",
            "Missing paths and executables describe the current process view, not the whole host.",
            "No audio, serial, camera, IMU, GPIO, servo, or power operation was performed.",
        ],
    }
