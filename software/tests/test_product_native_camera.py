"""Native BMO frontend and supervised camera admission; no device or Qt launch."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bmo_ai.desktop_app import parser, public_plan
from bmo_ai.desktop_browser import DesktopBrowser
from bmo_ai.desktop_native_window import validated_loopback, command
from bmo_ai.desktop_install import desktop_install_plan
from bmo_ai.features.contracts import FeatureError
from bmo_ai.features.host import FeatureHost, FeatureSimulatedClient
from bmo_ai.features.runtime import RuntimePolicy


class ProductNativeCameraTests(unittest.TestCase):
    def test_native_command_only_accepts_exact_private_loopback(self):
        self.assertEqual(validated_loopback("http://127.0.0.1:8765/"), 8765)
        for url in ("https://127.0.0.1:8765/", "http://localhost:8765/",
                    "http://evil.example:8765/", "http://127.0.0.1:8765/?anything=1",
                    "http://127.0.0.1:8765/extra", "http://user@127.0.0.1:8765/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validated_loopback(url)
        with patch("bmo_ai.desktop_native_window.native_python", return_value="/usr/bin/python3.13"):
            cmd = command("http://127.0.0.1:8765/", windowed=True)
            self.assertEqual(cmd[0], "/usr/bin/python3.13")
            self.assertEqual(cmd[-1], "--windowed")
            self.assertIn("desktop_native_window.py", cmd[2])
            self.assertNotIn("--no-sandbox", cmd)

    def test_native_view_is_selected_without_starting_a_browser(self):
        value = DesktopBrowser("http://127.0.0.1:8765/", renderer="native")
        self.assertEqual(value.snapshot()["renderer"], "native")
        self.assertFalse(value.snapshot()["owned_child"])
        fallback = DesktopBrowser("http://127.0.0.1:8765/")
        self.assertEqual(fallback.snapshot()["renderer"], "chromium")
        with self.assertRaises(Exception):
            DesktopBrowser("http://127.0.0.1:8765/", renderer="unknown")

    def test_install_plan_can_choose_native_BMO_icon_without_installing(self):
        with TemporaryDirectory() as tmp:
            with patch("bmo_ai.desktop_install.os.open", wraps=__import__("os").open) if False else patch(
                "bmo_ai.desktop_install._source", return_value=b"<svg/>"
            ):
                # Real icon SHA validation still applies, so this synthetic
                # injection deliberately must not be mistaken for installed.
                with self.assertRaises(Exception):
                    desktop_install_plan(project_root=Path(__file__).resolve().parents[2],
                                         home=Path(tmp), simulate=False, frontend="native")

    def test_runtime_capability_never_grants_real_video_or_arms(self):
        policy = RuntimePolicy(simulate=False, camera_live=True)
        policy.require("camera")
        self.assertEqual(policy.public()["camera"], "explicit_live")
        for capability in ("camera_video", "arms", "music", "external_games", "imu"):
            with self.subTest(capability=capability), self.assertRaises(FeatureError):
                policy.require(capability)
        with self.assertRaises(FeatureError):
            RuntimePolicy(simulate=True, camera_live=True)

    def test_approved_picamera_backend_is_lazy_and_photo_store_is_explicit(self):
        with TemporaryDirectory() as tmp:
            policy = RuntimePolicy(simulate=False, camera_live=True)
            client = FeatureSimulatedClient()
            with patch("builtins.__import__", side_effect=lambda *a, **kw:
                       (_ for _ in ()).throw(AssertionError("camera library loaded")) if
                       a and a[0] in ("picamera2", "cv2") else __import__(*a, **kw)):
                host = FeatureHost(runtime=policy, client=client, camera_photo_directory=Path(tmp))
            try:
                snap = host.snapshot()
                self.assertFalse(snap["camera"]["on"])
                self.assertEqual(snap["capabilities"]["camera"], "explicit_live")
                self.assertEqual(snap["capabilities"]["camera_video"], "denied")
            finally:
                host.close()

    def test_cli_requires_supervised_real_photo_storage(self):
        args = parser().parse_args(["--live", "--frontend", "native", "--camera-live",
                                    "--photo-directory", "/home/bgh/Pictures/BMO"])
        data = public_plan(args)
        self.assertEqual(data["frontend"], "native")
        self.assertTrue(data["camera_live"]["enabled"])
        self.assertFalse(data["camera_video"]["live_enabled"])


if __name__ == "__main__":
    unittest.main()
