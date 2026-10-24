"""Render a review-only desktop plan. Never install/autostart or run anything."""
import argparse
import json
import os
from pathlib import Path
import re
import stat

from .features.contracts import FeatureError


def desktop_plan(*, project_root=None, mode="simulate", screen_profile="r10"):
    project = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    if (not project.is_absolute() or ".." in project.parts
            or re.fullmatch(r"[A-Za-z0-9_./ -]+", str(project)) is None
            or mode not in ("simulate", "live") or screen_profile not in ("r10", "v13.2")):
        raise FeatureError("invalid_desktop_plan")
    launcher = project / "launch-features"
    try:
        info = launcher.lstat()
        verified = (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and bool(info.st_mode & 0o100) and not info.st_mode & 0o022)
    except OSError:
        verified = False
    command = [str(launcher), "--" + mode, "--screen-profile", screen_profile]
    template = ("[Desktop Entry]\nType=Application\nName=BMO integrated robot\n"
                "Comment=Manual launch only; hardware remains separately denied\n"
                "Exec=" + " ".join('"' + value + '"' for value in command) + "\n"
                "Path=" + str(project) + "\nTerminal=true\nIcon=applications-games\n"
                "Categories=Game;Utility;\nX-GNOME-Autostart-enabled=false\n")
    return {"status": "DRY_RUN", "launcher_verified": verified, "command": command,
            "template": template, "files_written": False, "autostart": False,
            "systemd_changes": False, "activation": "NOT_RUN",
            "live_conversation": "NOT_RUN", "physical_features_enabled": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--mode", choices=("simulate", "live"), default="simulate")
    parser.add_argument("--screen-profile", choices=("r10", "v13.2"), default="r10")
    parser.add_argument("--print-template", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run:
        parser.error("only --dry-run is supported; nothing will be installed")
    try:
        result = desktop_plan(project_root=args.project_root, mode=args.mode, screen_profile=args.screen_profile)
        print(result["template"] if args.print_template else json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except FeatureError as error:
        print(json.dumps({"status": "REJECTED", "code": error.code, "files_written": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
