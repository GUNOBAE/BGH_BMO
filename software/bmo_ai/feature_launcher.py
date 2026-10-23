"""Manual, simulation-only feature shell. Live hardware remains disabled."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import sys
import time

from .features.host import FeatureHost, HostControl
from .features.reply import HostIntent
from .features.server import FeatureServer
from .integration_profile import integration_config, effective_config
from .screen_profiles import SCREEN_PROFILES


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--simulate", action="store_true")
    p.add_argument("--demo-assets", action="store_true", help="explicit generated game/song fixtures")
    p.add_argument("--screen-profile", choices=tuple(SCREEN_PROFILES), default="v13.2")
    p.add_argument("--face-file", type=Path)
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--no-web", action="store_true")
    p.add_argument("--turns", type=int, default=0)
    p.add_argument("--effective-config", action="store_true")
    return p


def main(argv=None, *, lock_path=None, clock=time.monotonic, host_factory=FeatureHost):
    p = parser()
    args = p.parse_args(argv)
    if args.effective_config:
        print(json.dumps(dict(effective_config(integration_config(), emotion_mode="auto"),
            feature_reply_version=1, simulation=True, real_features_enabled=False,
            screen_profile=args.screen_profile), ensure_ascii=False, indent=2))
        return 0
    if not args.simulate:
        p.error("feature execution requires --simulate; real features are not enabled")
    if not 0 <= args.port <= 65535 or not 0 <= args.turns <= 20:
        p.error("invalid port or turn count")
    path = Path(lock_path) if lock_path is not None else Path(f"/tmp/bmo-integration-{os.getuid()}.lock")
    lock = host = server = None
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock = os.fdopen(descriptor, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("BMO is already running for this user.", file=sys.stderr)
            return 2
        host = host_factory(demo_assets=args.demo_assets, clock=clock)
        if not args.no_web:
            face = args.face_file or Path(SCREEN_PROFILES[args.screen_profile]["default_path"])
            server = FeatureServer(host, face, screen_profile=args.screen_profile, port=args.port).start()
            print("Face: " + server.url, flush=True)
        print("SIMULATION: /games, /game ID, /face, /photo, /track, /camera-off, /song ID, "
              "/media pause|resume|next|stop, /gesture ID, /key KEY down|up, "
              "/emotion NAME, /cancel, /repeat, /reset, /stop, /quit", flush=True)
        if args.turns:
            for index in range(args.turns):
                host.start_question(f"Synthetic feature question {index + 1}.")
                while host.session.busy:
                    host.poll()
                    time.sleep(.01)
                host.poll()
                print(json.dumps({"turn": index + 1, "history_messages": len(host.session.brain.history),
                                  "simulation": True}), flush=True)
            return 0
        previous = None
        while True:
            host.poll()
            state = host.snapshot()
            summary = {key: state[key] for key in ("mode", "mode_generation", "emotion", "phase", "audio_owner")}
            if summary != previous:
                print(json.dumps(summary), flush=True)
                previous = summary
            readable, _, _ = select.select([sys.stdin], [], [], .02)
            if not readable:
                continue
            line = sys.stdin.readline()
            if not line or line.strip() == "/quit":
                break
            command, _, argument = line.strip().partition(" ")
            try:
                if command in ("/games", "/face", "/photo", "/track", "/camera-off"):
                    name = {"/games": "open_game_menu", "/face": "return_to_face", "/photo": "take_photo",
                            "/track": "start_face_tracking", "/camera-off": "stop_face_tracking"}[command]
                    host.execute(HostIntent(name))
                elif command in ("/game", "/song", "/gesture"):
                    host.execute(HostIntent({"/game": "launch_game", "/song": "play_song", "/gesture": "arm_gesture"}[command], argument))
                elif command == "/emotion":
                    host.control(HostControl("emotion", argument), "cli_" + str(time.monotonic_ns()))
                elif command == "/key":
                    key, _, edge = argument.partition(" ")
                    host.inputs.route(key.upper(), edge.lower() != "up", now=clock())
                elif command == "/media":
                    if argument == "stop":
                        host.execute(HostIntent("stop_media"))
                    else:
                        host.control(HostControl(argument), "cli_" + str(time.monotonic_ns()))
                elif command in ("/cancel", "/reset", "/repeat", "/stop"):
                    host.control(HostControl(command[1:]), "cli_" + str(time.monotonic_ns()))
                elif command.startswith("/"):
                    print("Unknown command.", flush=True)
                else:
                    host.start_question(line.strip() or "Synthetic feature question.")
            except Exception:
                print("The requested feature is unavailable.", flush=True)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print("The feature launcher could not complete its owned operation.", file=sys.stderr)
        return 1
    finally:
        cleanup_failed = False
        if server is not None:
            try:
                server.close()
            except Exception:
                cleanup_failed = True
        if host is not None:
            try:
                host.close()
            except Exception:
                cleanup_failed = True
        if lock is not None:
            lock.close()
        if cleanup_failed:
            print("Owned feature cleanup needs review.", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
