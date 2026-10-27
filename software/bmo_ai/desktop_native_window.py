"""Native Qt window for the approved BMO face; invoked by owned backend process.

Only the optional main() imports PySide6. The native application never owns
credentials, API clients, microphones, speakers, motors or any BMO brain. It
embeds the trusted local face UI in a native Qt window without a Chromium
browser window, toolbar or URL box. The original R10 HTML remains unchanged.
"""
import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import urlsplit


def validated_loopback(value):
    try:
        url = urlsplit(value)
        if (type(value) is not str or len(value) > 160 or url.scheme != "http" or
            url.hostname != "127.0.0.1" or not url.port or url.path not in ("/", "/app", "/desktop") or
            url.query or url.fragment or url.username or url.password or
            value != "http://127.0.0.1:" + str(url.port) + url.path):
            raise ValueError()
        return url.port
    except (ValueError, AttributeError, TypeError):
        raise ValueError("invalid_native_url") from None


def native_python():
    """Use only the distribution's root-owned native interpreter, not a pip shim."""
    try:
        expected = Path("/usr/bin/python3")
        resolved = expected.resolve(strict=True)
        info = resolved.stat()
        if (resolved.parent != Path("/usr/bin") or
            re.fullmatch(r"python3\.[0-9]{1,2}", resolved.name) is None or
            not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
            (info.st_mode & 0o0022) or not (info.st_mode & 0o111)):
            raise ValueError()
        return str(resolved)
    except (OSError, ValueError):
        raise ValueError("native_python_unavailable") from None


def command(url, *, windowed=False):
    validated_loopback(url)
    if type(windowed) is not bool:
        raise ValueError("invalid_native_window_mode")
    script = Path(__file__).resolve(strict=True)
    if script.suffix != ".py" or script.name != "desktop_native_window.py":
        raise ValueError("native_launcher_unavailable")
    argv = [native_python(), "-I", "-B", str(script), "--url", url]
    if windowed:
        argv.append("--windowed")
    return argv


def available(environment):
    """Bounded optional-system-library probe, without constructing Qt or a GUI.

    The parent supplies its scrubbed profile/session environment. No package
    installation, credential access, device access or output forwarding occurs.
    This is capability detection, never a claim of a working Pi native window.
    """
    try:
        result = subprocess.run([native_python(), "-I", "-B", "-c",
            "from PySide6.QtWebEngineWidgets import QWebEngineView"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=environment, close_fds=True, timeout=2., check=False)
        return result.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def origin_allowed(scheme, host, port, expected_port, *, local_frame=False):
    """Shared request/navigation policy, testable without importing Qt."""
    return ((scheme == "http" and host == "127.0.0.1" and port == expected_port)
            or local_frame and scheme in ("about", "data", "blob"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--windowed", action="store_true")
    args = parser.parse_args(argv)
    try:
        port = validated_loopback(args.url)
    except ValueError:
        print("BMO native window: invalid_native_url", file=sys.stderr)
        return 2

    # An optional system package, not a dependency of the BMO cloud venv.
    try:
        from PySide6.QtCore import QUrl, Qt
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import (
            QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor,
            QWebEngineSettings,
        )
    except ImportError:
        print("BMO native window: qt_webengine_not_installed", file=sys.stderr)
        return 78

    class OnlyBmoOrigin(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            url = info.requestUrl()
            # The local wrapper needs about:blank and srcdoc for trusted frames.
            if not origin_allowed(url.scheme().lower(), url.host(), url.port(), port,
                                  local_frame=True):
                info.block(True)

    class LockedPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            return origin_allowed(url.scheme().lower(), url.host(), url.port(), port,
                                  local_frame=not is_main_frame)

        def createWindow(self, _type):
            return None  # No popup browser windows or remote navigation.

    # Qt runs as its own process. The original Python controller owns lifetime,
    # credentials and the only audio player through its parent process group.
    app = QApplication([])
    app.setApplicationName("BMO")
    view = QWebEngineView()
    profile = QWebEngineProfile(view)  # anonymous off-the-record Qt profile
    interceptor = OnlyBmoOrigin(profile)
    profile.setUrlRequestInterceptor(interceptor)
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
    page = LockedPage(profile, view)
    page.setAudioMuted(True)  # The existing Python owner is the only speaker.
    page.settings().setAttribute(
        QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
    page.settings().setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
    view.setPage(page)
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    # Default-deny permissions/downloads; the external Python adapters own all
    # physical capture. The WebEngine face never acquires camera/microphone.
    profile.downloadRequested.connect(lambda request: request.cancel())
    if hasattr(page, "permissionRequested"):
        page.permissionRequested.connect(lambda permission: permission.deny())
    view.setWindowTitle("BMO")
    view.resize(800, 480)
    if args.windowed:
        view.show()
    else:
        view.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        view.showFullScreen()
    view.setUrl(QUrl(args.url))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
