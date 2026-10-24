"""Metadata-only offline readiness checks; no credential/device/port access."""
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from .features.games.assets import inventory_known_assets
from .screen_profiles import SCREEN_PROFILES


def _dependency(module, distribution):
    try:
        available = importlib.util.find_spec(module) is not None
    except Exception:
        available = False
    version = None
    if available:
        try:
            candidate = importlib.metadata.version(distribution)
            if re.fullmatch(r"[0-9][A-Za-z0-9.+_-]{0,79}", candidate):
                version = candidate
        except Exception:
            pass
    return {"available": available, "version": version, "imported": False, "executed": False}


def _screen(path, expected):
    descriptor = parent = None
    try:
        path = Path(path)
        if (not path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".html"
                or ".config" in path.parts):
            return {"status": "UNVERIFIED", "sha256_match": False, "bytes": None}
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent = os.open("/", flags)
        for part in path.parts[1:-1]:
            following = os.open(part, flags, dir_fd=parent)
            os.close(parent); parent = following
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= 2 * 1024 * 1024:
            return {"status": "UNVERIFIED", "sha256_match": False, "bytes": None}
        digest, total = hashlib.sha256(), 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > 2 * 1024 * 1024:
                return {"status": "UNVERIFIED", "sha256_match": False, "bytes": None}
            digest.update(chunk)
        after = os.fstat(descriptor)
        unchanged = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
                     after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        match = unchanged and digest.hexdigest() == expected
        return {"status": "MATCH" if match else "MISMATCH", "sha256_match": match, "bytes": total}
    except (OSError, TypeError, ValueError):
        return {"status": "ASSET_PENDING", "sha256_match": False, "bytes": None}
    finally:
        for value in (descriptor, parent):
            if value is not None:
                os.close(value)


def _git(project):
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "GIT_OPTIONAL_LOCKS": "0",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    prefix = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
              "-C", str(project)]
    try:
        outputs = []
        for arguments in (("rev-parse", "HEAD"), ("symbolic-ref", "--short", "HEAD"),
                          ("status", "--porcelain=v1", "--untracked-files=no")):
            result = subprocess.run([*prefix, *arguments], capture_output=True, text=True,
                                    timeout=3, check=True, env=environment)
            outputs.append(result.stdout.strip())
        head, branch, changes = outputs
        if re.fullmatch(r"[a-f0-9]{40,64}", head) is None or re.fullmatch(r"[A-Za-z0-9/_-]{1,128}", branch) is None:
            return {"status": "UNVERIFIED"}
        return {"status": "OBSERVED", "head": head, "branch": branch,
                "tracked_changes": bool(changes), "changed_tracked_count": len(changes.splitlines()) if changes else 0,
                "remote_verification": "NOT_RUN", "untracked_content_read": False}
    except (OSError, subprocess.SubprocessError):
        return {"status": "UNAVAILABLE", "remote_verification": "NOT_RUN"}


def offline_report(*, project_root=None, screen_paths=None, asset_paths=None,
                   dependency_probe=None, git_probe=None):
    project = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    dependency_probe = dependency_probe or _dependency
    git_probe = git_probe or _git
    required = {name: dependency_probe(module, distribution) for name, module, distribution in (
        ("openai", "openai", "openai"), ("httpx", "httpx", "httpx"))}
    optional = {name: dependency_probe(module, distribution) for name, module, distribution in (
        ("playwright", "playwright", "playwright"), ("picamera2", "picamera2", "picamera2"),
        ("opencv", "cv2", "opencv-python"), ("pillow", "PIL", "Pillow"))}
    screens = {}
    for name in ("r10", "v13.2"):
        profile = SCREEN_PROFILES[name]
        path = screen_paths.get(name, profile["default_path"]) if screen_paths else profile["default_path"]
        screens[name] = {**_screen(path, profile["sha256"]), "approval_status": profile["approval_status"],
                         "pi_live_verification": "NOT_RUN"}
    git = git_probe(project)
    ready = (sys.version_info >= (3, 11) and all(item.get("available") is True for item in required.values())
             and screens["r10"]["sha256_match"] and screens["r10"]["approval_status"] in ("approved", "approved_design")
             and git.get("status") == "OBSERVED")
    return {"version": 1, "mode": "offline_metadata_only",
            "readiness": "READY_FOR_HUMAN_TEST" if ready else "NOT_READY",
            "readiness_scope": "metadata_prerequisites_only",
            "python": {"version": ".".join(map(str, sys.version_info[:3])),
                       "supported": sys.version_info >= (3, 11)},
            "dependencies": {"required": required, "optional_disabled": optional},
            "tools_present_not_executed": {name: shutil.which(name, path=os.defpath) is not None
                for name in ("arecord", "aplay", "chromium", "retroarch")},
            "screens": screens, "git": git, "game_assets": inventory_known_assets(asset_paths),
            "user_media": "ASSET_PENDING", "tests": "NOT_RUN", "ci": "NOT_RUN",
            "live": {name: "NOT_RUN" for name in ("paid_api", "microphone", "speaker", "camera", "servo", "imu", "pad", "serial", "lcd")},
            "credentials_accessed": False, "ports_opened": False, "devices_opened": False,
            "services_changed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--r10-path", type=Path)
    parser.add_argument("--v13-path", type=Path)
    parser.add_argument("--asset", action="append", type=Path)
    args = parser.parse_args(argv)
    if not args.offline:
        parser.error("only explicit --offline metadata checks are supported")
    paths = {key: value for key, value in (("r10", args.r10_path), ("v13.2", args.v13_path)) if value is not None}
    print(json.dumps(offline_report(project_root=args.project_root, screen_paths=paths,
                                   asset_paths=args.asset), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
