"""Manual Stage 1 launcher. --simulate shares the live session and face path."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import sys
import time

from .integration_profile import integration_config, effective_config


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--simulate", action="store_true", help="synthetic IO; no devices or speech APIs")
    mode.add_argument("--live", action="store_true", help="explicit live microphone/cloud/ALSA path")
    p.add_argument("--effective-config", action="store_true")
    p.add_argument("--baseline", action="store_true", help="GPT-4.1 mini; reasoning unset")
    p.add_argument("--offline-voice", action="store_true", help="opt in to original English Piper fallback")
    p.add_argument("--mic-device", default="plughw:CARD=Device,DEV=0")
    p.add_argument("--speaker-device", default="plughw:CARD=MAX98357A,DEV=0")
    p.add_argument("--face-file", type=Path, default=Path("/home/bgh/Downloads/BMO_Screen_v13_2.html"))
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-web", action="store_true", help="simulation-only CLI check")
    p.add_argument("--turns", type=int, default=0, help="run this many synthetic turns then exit")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        config = integration_config(baseline=args.baseline, offline_voice=args.offline_voice,
                                    mic_device=args.mic_device, speaker_device=args.speaker_device)
    except ValueError:
        p.error("invalid integration configuration")
    if args.effective_config:
        print(json.dumps(effective_config(config), indent=2, ensure_ascii=False))
        return 0
    if not (args.live or args.simulate):
        p.error("choose --simulate or --live")
    if args.no_web and not args.simulate:
        p.error("--no-web is only available with --simulate")
    if args.turns and (not args.simulate or not 1 <= args.turns <= 20):
        p.error("--turns requires --simulate and a value from 1 to 20")
    if not 0 <= args.port <= 65535:
        p.error("--port must be from 0 to 65535")

    from .cloud import CloudError, create_client
    from .integration_session import IntegrationSession, SimulatedClient
    from .integration_face import FaceServer
    from .integration_actions import MockActionHost, demo_requests

    # One manual launcher per user; no daemon/service or automatic start install.
    lock_path = Path(f"/tmp/bmo-integration-{os.getuid()}.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock = os.fdopen(descriptor, "w")
    except OSError:
        print("BMO cannot acquire its local launcher lock.", file=sys.stderr)
        return 2
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        print("BMO is already running for this user.", file=sys.stderr)
        return 2

    session = server = client = None
    try:
        client = SimulatedClient() if args.simulate else create_client(config)
        session = IntegrationSession(config, client, simulate=args.simulate)
        mock_actions = MockActionHost(simulate=True) if args.simulate else None
        if not args.no_web:
            server = FaceServer(args.face_file, snapshot=session.browser_snapshot, port=args.port)
            server.start()
            print("Face: " + server.url, flush=True)
        print("Commands: Enter=question, /cancel, /repeat, /reset, /emotion NAME, "
              "/actions-demo (simulate), /action JSON (simulate), /quit", flush=True)
        if args.turns:
            for index in range(args.turns):
                session.start(f"Synthetic question {index + 1}")
                while session.busy:
                    session.poll()
                    time.sleep(0.01)
                print(json.dumps({"turn": index + 1, "history_messages": len(session.brain.history),
                                  "playback": session.snapshot()["playback"]}), flush=True)
            return 0
        previous_sequence = -1
        while True:
            session.poll()
            state = session.snapshot()
            if state["sequence"] != previous_sequence:
                print(f"{state['phase']} / {state['playback']}", flush=True)
                previous_sequence = state["sequence"]
            readable, _, _ = select.select([sys.stdin], [], [], 0.02)
            if not readable:
                continue
            line = sys.stdin.readline()
            if not line:
                break
            command = line.strip()
            if command == "/quit":
                break
            if command == "/cancel":
                session.cancel()
            elif command == "/repeat":
                if not session.repeat():
                    print("No successful voice is cached, or a turn is still active.")
            elif command == "/reset":
                session.reset()
            elif command.startswith("/emotion "):
                try:
                    session.set_emotion(command.split(maxsplit=1)[1])
                except ValueError:
                    print("Emotion: neutral, happy, angry, sad, crying, surprised")
            elif command == "/actions-demo":
                if mock_actions is None:
                    print("Actions require --simulate; nothing was executed.")
                else:
                    for request in demo_requests():
                        print(json.dumps(mock_actions.dispatch_request(request),
                                         ensure_ascii=False, sort_keys=True), flush=True)
            elif command == "/action" or command.startswith("/action "):
                if mock_actions is None:
                    print("Actions require --simulate; nothing was executed.")
                else:
                    try:
                        request = json.loads(command.partition(" ")[2])
                    except json.JSONDecodeError:
                        print("Use /action followed by a structured JSON request.")
                    else:
                        print(json.dumps(mock_actions.dispatch_request(request),
                                         ensure_ascii=False, sort_keys=True), flush=True)
            elif command.startswith("/"):
                print("Unknown command.")
            elif not session.start(command or "A synthetic question"):
                print("A turn is still active.")
        return 0
    except KeyboardInterrupt:
        return 0
    except CloudError as error:
        print(error.safe_message, file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("BMO could not start; check the approved face file, local port and configuration.", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()
        elif client is not None:
            client.close()
        if server is not None:
            server.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
