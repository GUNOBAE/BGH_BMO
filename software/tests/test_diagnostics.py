import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.diagnostics import hardware_inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, content):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def test_inventory_observes_kernel_metadata_without_opening_devices(self):
        self.write("/proc/device-tree/model", b"Raspberry Pi 5 Model B\x00")
        self.write("/etc/os-release", b'PRETTY_NAME="Debian GNU/Linux 13"\nID=debian\nSECRET=ignore\n')
        self.write("/proc/asound/cards", b"2 [MAX98357A] simple-card\n")
        self.write("/sys/class/sound/card2/id", b"MAX98357A\n")
        (self.root / "sys/class/tty/ttyACM0").mkdir(parents=True)
        self.write("/sys/class/video4linux/video0/name", b"ISP codec\n")
        (self.root / "dev/snd").mkdir(parents=True)
        (self.root / "dev/snd/controlC2").touch()
        (self.root / "dev/ttyACM0").touch()
        opened = []
        real_open = Path.open

        def guarded_open(path, *args, **kwargs):
            opened.append(str(path))
            if self.root / "dev" in path.parents:
                raise AssertionError("device node was opened")
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            with patch("subprocess.run", side_effect=AssertionError("tool executed")):
                report = hardware_inventory(root=self.root, which=lambda name: "/usr/bin/" + name)
        self.assertEqual(report["platform"]["model"]["value"], "Raspberry Pi 5 Model B")
        self.assertEqual(report["platform"]["os_release"]["fields"], {"PRETTY_NAME": "Debian GNU/Linux 13", "ID": "debian"})
        self.assertEqual(report["audio"]["sysfs"]["cards"][0]["id"]["value"], "MAX98357A")
        self.assertEqual(report["audio"]["device_nodes"]["entries"], ["controlC2"])
        self.assertFalse(report["controller"]["port_opened"])
        self.assertFalse(report["controller"]["identity_verified"])
        self.assertEqual(report["camera"]["capture_test"], "not_performed")
        self.assertTrue(opened)
        self.assertTrue(all(not tool["executed"] for tool in report["tools"].values()))
        json.dumps(report, allow_nan=False)

    def test_hidden_device_nodes_are_not_reported_as_missing_hardware(self):
        self.write("/sys/class/sound/card0/id", b"USB\n")
        (self.root / "sys/class/tty/ttyACM0").mkdir(parents=True)
        report = hardware_inventory(root=self.root, which=lambda name: None)
        for kind in ("audio", "controller"):
            self.assertEqual(report[kind]["sysfs"]["status"], "observed")
            self.assertEqual(report[kind]["device_nodes"]["status"], "unavailable_in_current_view")
            self.assertIn("Sandbox/container", report[kind]["device_nodes"]["note"])
        self.assertEqual(report["platform"]["model"]["status"], "unavailable")
        self.assertEqual(report["imu"]["health"], "unverified")
        self.assertEqual(report["arms"]["backend"], "denied")
        self.assertTrue(all(tool["status"] == "unavailable" for tool in report["tools"].values()))
        json.dumps(report, allow_nan=False)

    def test_fallback_model_truncation_and_permission_errors(self):
        self.write("/sys/firmware/devicetree/base/model", b"Fallback model\x00")
        self.write("/proc/asound/cards", b"a" * 20_000)
        report = hardware_inventory(root=self.root, which=lambda name: None)
        self.assertEqual(report["platform"]["model"]["value"], "Fallback model")
        self.assertTrue(report["audio"]["proc_cards"]["truncated"])
        self.assertLess(len(report["audio"]["proc_cards"]["value"]), 20_000)
        with patch.object(Path, "open", side_effect=PermissionError("not visible")):
            report = hardware_inventory(root=self.root, which=lambda name: None)
        self.assertEqual(report["platform"]["model"]["reason"], "PermissionError")
        self.assertNotIn("not visible", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
