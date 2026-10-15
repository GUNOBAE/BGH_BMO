"""Run regression tests behind side-effect guards; no packages are installed.

From software/: timeout 90s .venv/bin/python -B checks/check_conversation_safety.py
An audit hook remains active even when a test temporarily mocks a Python API.
Any attempted real network/device operation makes the final result fail, including
attempts whose exceptions the application catches. Only the existing synthetic
Python sleep-child timeout regression may create a process.
"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
violations = []


def audit(event, args):
    category = None
    if event in ("socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"):
        category = "network"
    elif event in ("subprocess.Popen", "os.posix_spawn"):
        executable, command = args[0], args[1]
        # Existing test_audio timeout child writes a synthetic PID in its test
        # temporary directory then sleeps. No other command is permitted.
        safe_child = (
            executable == sys.executable and isinstance(command, (list, tuple))
            and len(command) == 4 and command[1] == "-c"
            and command[2] == "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
            and Path(command[3]).name == "child.pid"
            and Path(command[3]).parent.is_relative_to(Path(os.getenv("TMPDIR", "/tmp")))
        )
        if not safe_child:
            category = "process"
    elif event in ("os.system", "os.exec"):
        category = "process"
    elif event in ("open", "os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.fsdecode(args[0])
        if path.startswith(("/dev/", "/sys/", "/media/")):
            category = "device"
        elif Path(path).name == ".env" or any(part in (".ssh", ".aws", ".git-credentials") for part in Path(path).parts):
            category = "private_file"
    if category:
        # Never retain an attempted path, command, host or payload.
        violations.append(category)
        raise PermissionError("Forbidden side effect during offline checks")


if __name__ == "__main__":
    sys.addaudithook(audit)
    # The SDK's mock-HTTP contract tests format a platform header. Stub this
    # metadata to avoid stdlib's optional `uname -p` process and /dev/null access.
    with patch("platform.platform", return_value="offline-test"):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"Side-effect guard violations: {len(violations)}")
    if violations:
        print("Violation categories: " + ", ".join(sorted(violations)))
    raise SystemExit(0 if result.wasSuccessful() and not violations else 1)
