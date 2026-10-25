"""Truthful Desktop loading checkpoints. All components are fake; no IO."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from bmo_ai.desktop_runtime import DesktopRuntime


class _Server:
    _bridge = SimpleNamespace(sha256="a" * 64)

    def close(self):
        pass


class _Browser:
    def __init__(self):
        self.ready_calls = 0

    def poll(self):
        return {"state": "starting"}

    def mark_ready(self):
        self.ready_calls += 1
        return True

    def close(self):
        pass


class _Host:
    def poll(self):
        pass

    def close(self):
        pass


class DesktopProgressTests(unittest.TestCase):
    def test_progress_only_tracks_real_completed_stages(self):
        app = DesktopRuntime(clock=lambda: 10.0, simulate=True)
        try:
            self.assertEqual((app.startup_snapshot()["progress_percent"],
                              app.startup_snapshot()["progress_stage"]), (10, "lock_acquired"))
            app.set_server(_Server())
            self.assertEqual(app.startup_snapshot()["progress_percent"], 25)
            browser = _Browser()
            app.set_browser(browser)
            self.assertEqual(app.startup_snapshot()["progress_percent"], 35)
            app.browser_started()
            self.assertEqual(app.startup_snapshot()["progress_percent"], 45)
            app.set_host(_Host(), None)
            self.assertEqual(app.startup_snapshot()["progress_percent"], 65)
            app.queue_desktop_control("progress_ready", {
                "action": "face_ready",
                "value": {"run_id": app.run_id, "screen_sha256": "a" * 64,
                          "frame_version": "13.2"},
            })
            self.assertEqual(app.startup_snapshot()["progress_percent"], 90)
            app._advance_progress(25, "stale_server_ready")
            self.assertEqual((app.startup_snapshot()["progress_percent"],
                              app.startup_snapshot()["progress_stage"]),
                             (90, "face_handshake_verified"))
            app.poll()
            self.assertEqual((app.startup_snapshot()["progress_percent"],
                              app.startup_snapshot()["progress_stage"]),
                             (100, "local_app_ready"))
            self.assertEqual(browser.ready_calls, 1)
        finally:
            app.close()

    def test_failure_freezes_last_verified_progress(self):
        app = DesktopRuntime(clock=lambda: 10.0, simulate=True)
        try:
            app.set_server(_Server())
            app.fail("face_ready_timeout")
            app.browser_started()
            app._advance_progress(100, "not_really_ready")
            snap = app.startup_snapshot()
            self.assertEqual(snap["phase"], "error")
            self.assertEqual(snap["progress_percent"], 25)
            self.assertEqual(snap["progress_stage"], "server_ready")
        finally:
            app.close()

    def test_visible_progress_is_stage_driven_and_accessible(self):
        root = Path(__file__).resolve().parents[1] / "bmo_ai" / "desktop_web"
        html = (root / "index.html").read_text(encoding="utf-8")
        js = (root / "desktop.js").read_text(encoding="utf-8")
        self.assertIn('id="progress-percent"', html)
        self.assertIn('role="progressbar"', html)
        self.assertIn('aria-valuenow="10"', html)
        self.assertIn("next.progress_percent", js)
        self.assertIn("next.progress_stage", js)
        self.assertIn("progress-fill", js)
        self.assertNotIn("setInterval", js)
        self.assertNotIn("progress+=1", js)


if __name__ == "__main__":
    unittest.main()
