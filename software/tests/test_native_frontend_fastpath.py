"""Qt native window is opt-in; no PySide6 required for contract tests."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bmo_ai.desktop_app import parser, public_plan
from bmo_ai.desktop_browser import DesktopBrowser, BrowserError
from bmo_ai.desktop_install import desktop_install_plan
from bmo_ai.desktop_native_window import validated_loopback


class NativeFastpathTests(unittest.TestCase):
    def test_explicit_frontend_is_reported_without_device_or_gui(self):
        opts = parser().parse_args(["--simulate", "--frontend", "native", "--status"])
        self.assertEqual(public_plan(opts)["frontend"], "native")
        self.assertEqual(parser().parse_args([]).frontend, "chromium")

    def test_native_window_allows_exact_bmo_loopback_only(self):
        self.assertEqual(validated_loopback("http://127.0.0.1:1234/"), 1234)
        for url in ("https://127.0.0.1:1234/", "http://localhost:1234/",
                    "http://127.0.0.1:1234/other", "http://example.com:1234/",
                    "http://127.0.0.1:1234/?token=private"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validated_loopback(url)

    def test_only_explicit_native_desktop_install_persists_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Desktop").mkdir(mode=0o700)
            (root / ".local").mkdir(mode=0o700)
            (root / ".local" / "share").mkdir(mode=0o700)
            env = {"HOME": str(root)}
            # Work only with the real repository's validated launcher.
            real = Path(__file__).resolve().parents[2]
            plain = desktop_install_plan(project_root=real, home=root, environ=env, simulate=True)
            native = desktop_install_plan(project_root=real, home=root, environ=env,
                                          simulate=True, frontend="native", ptt_microphone=True)
            self.assertNotIn("native", plain["command"])
            self.assertIn("native", native["command"])
            self.assertIn("--ptt-microphone", native["command"])
            self.assertEqual(native["files_written"], 0)

    def test_invalid_renderer_does_not_construct_browser(self):
        with self.assertRaises(BrowserError):
            DesktopBrowser("http://127.0.0.1:1111/", frontend="shell")

if __name__ == "__main__":
    unittest.main()
