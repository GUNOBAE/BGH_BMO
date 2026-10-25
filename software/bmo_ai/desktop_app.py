"""Manual BMO desktop program: existing Python engine + owned Chromium window."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys
from tempfile import TemporaryDirectory
import time

from .desktop_runtime import DesktopRuntime
from .features.host import FeatureHost
from .features.runtime import RuntimePolicy
from .features.server import FeatureServer
from .integration_profile import integration_config, effective_config
from .screen_profiles import SCREEN_PROFILES


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--live", action="store_true")
    p.add_argument("--windowed", action="store_true", help="explicit development window, default kiosk")
    p.add_argument("--frontend", choices=("chromium", "native"), default="chromium",
                   help="native uses a dedicated Qt BMO desktop window; chromium remains the verified fallback")
    p.add_argument("--developer", action="store_true", help="enable /developer and hidden Ctrl+Alt+D")
    p.add_argument("--demo-assets", action="store_true", help="simulation-only generated fixtures")
    p.add_argument("--video-directory", type=Path, help="user-selected private local synthetic-video storage; live camera video remains denied")
    p.add_argument("--music-directory", type=Path, help="explicit reviewed private WAV library; simulation uses silent backend and live music stays denied")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--diagnose-startup", action="store_true", help="read-only session metadata, original face SHA and lock check; no launch or repair")
    p.add_argument("--install-desktop", action="store_true", help="explicit user installation, never autostart")
    p.add_argument("--screen-profile", choices=tuple(SCREEN_PROFILES), default="r10")
    p.add_argument("--face-file", type=Path)
    p.add_argument("--baseline", action="store_true")
    p.add_argument("--offline-voice", action="store_true")
    p.add_argument("--mic-device", default="plughw:CARD=Device,DEV=0")
    p.add_argument("--ptt-microphone", action="store_true", help="explicit supervised opt-in: HID/keyboard Enter starts finite mic/VAD capture; no idle listening")
    p.add_argument("--camera-live", action="store_true",
                   help="supervised Picamera2 photo/preview/tracking opt-in; video and arms remain denied")
    p.add_argument("--photo-directory", type=Path,
                   help="explicit private photo storage location required for camera-live")
    p.add_argument("--expressive-voice", action="store_true",
                   help="opt-in Fish S2 emotional performance from validated BMO emotion; no extra API call")
    p.add_argument("--vad-profile", choices=("normal", "sensitive"), default="normal",
                   help="local RMS speech threshold; sensitive is an explicit test profile, not wake-word detection")
    p.add_argument("--speaker-device", default="plughw:CARD=MAX98357A,DEV=0")
    p.add_argument("--startup-timeout", type=float, default=15.)
    p.add_argument("--greeting-timeout", type=float, default=30.)
    greeting = p.add_mutually_exclusive_group()
    greeting.add_argument("--prepare-greeting", action="store_true", help="explicit live Fish synthesis once; no playback")
    greeting.add_argument("--audition-greeting", action="store_true", help="explicit cached greeting listening review")
    greeting.add_argument("--approve-greeting", metavar="WAV_SHA256", help="record the user's prior listening approval")
    return p


def _config(args):
    return integration_config(baseline=args.baseline, offline_voice=args.offline_voice,
                              mic_device=args.mic_device, speaker_device=args.speaker_device)


def public_plan(args):
    from .greeting_cache import GREETING_TEXT
    config = _config(args)
    return {"version": 1, "mode": "simulate" if args.simulate else "live" if args.live else "not_selected",
        "screen_profile": args.screen_profile, "screen_sha256": SCREEN_PROFILES[args.screen_profile]["sha256"],
        "window": "windowed" if args.windowed else "kiosk", "developer_enabled": args.developer,
        "frontend": args.frontend,
        "expressive_voice": args.expressive_voice,
        "greeting": {"text": GREETING_TEXT, "asset_version": "welcome-v2", "source": "approved_private_cache", "automatic_synthesis": False},
        "configuration": effective_config(config, emotion_mode="auto"),
        "credentials_accessed": False, "ports_opened": False, "processes_started": False,
        "desktop_installed": False, "autostart": False, "physical_features_enabled": False,
        "ptt": {"enabled": args.simulate or args.ptt_microphone, "vad_profile": args.vad_profile,
                "source": "synthetic_pcm" if args.simulate else "explicit_microphone" if args.ptt_microphone else "disabled",
                "automatic_capture": False, "wake_word_enabled": False, "physical_verified": False},
        "camera_live": {"enabled": args.camera_live, "storage_selected": args.photo_directory is not None,
                        "physical_verified": False, "automatic_capture": False},
        "camera_video": {"storage_selected": args.video_directory is not None,
                         "live_enabled": False, "automatic_capture": False},
        "music_library": {"selected": args.music_directory is not None,
                          "live_playback_enabled": False, "automatic_import": False},
        "pi_kiosk_verified": "NOT_RUN"}


def main(argv=None, *, lock_path=None, host_factory=FeatureHost, browser_factory=None,
         greeting_factory=None, credential_resolver=None, client_factory=None,
         clock=time.monotonic, sleep=time.sleep, on_started=None):
    p = parser()
    args = p.parse_args(argv)
    for value in (args.startup_timeout, args.greeting_timeout):
        if not 0 < value <= 120:
            p.error("timeouts must be finite and from 0 to 120 seconds")
    if args.demo_assets and not args.simulate:
        p.error("--demo-assets requires --simulate")
    if args.camera_live and (not args.live or args.photo_directory is None):
        p.error("--camera-live requires --live and an explicit --photo-directory")
    if args.photo_directory is not None and not args.camera_live:
        p.error("--photo-directory requires explicit --camera-live")
    if args.music_directory is not None and args.demo_assets:
        p.error("--music-directory and --demo-assets are separate catalogs")
    preparing = args.prepare_greeting or args.audition_greeting or args.approve_greeting is not None
    if preparing and not args.live:
        p.error("greeting preparation/review requires explicit --live")
    if args.install_desktop and preparing:
        p.error("installation and greeting review are separate operations")
    if args.diagnose_startup:
        from .desktop_diagnostics import startup_preflight
        print(json.dumps(startup_preflight(face_file=args.face_file,
            screen_profile=args.screen_profile, lock_path=lock_path), ensure_ascii=False, indent=2))
        return 0
    if args.status or (args.dry_run and not args.install_desktop):
        print(json.dumps(public_plan(args), ensure_ascii=False, indent=2))
        return 0
    if args.install_desktop:
        from .desktop_install import install_desktop
        try:
            options = {"install": not args.dry_run, "simulate": args.simulate}
            if args.frontend != "chromium":
                options["frontend"] = args.frontend
            if args.ptt_microphone:
                options["ptt_microphone"] = True
            if args.expressive_voice:
                options["expressive_voice"] = True
            if args.vad_profile != "normal":
                options["vad_profile"] = args.vad_profile
            result = install_desktop(**options)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except Exception:
            print("BMO desktop installation could not complete safely.", file=sys.stderr)
            return 1
    if not (args.simulate or args.live):
        p.error("choose --simulate or explicit --live")
    lock = client = app = temporary = None
    previous_handlers = {}
    outcome = 0
    stage = "lock"
    try:
        path = Path(lock_path) if lock_path is not None else Path(f"/tmp/bmo-integration-{os.getuid()}.lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            os.close(fd)
            print("BMO cannot acquire its private launcher lock.", file=sys.stderr)
            return 2
        lock = os.fdopen(fd, "r+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("BMO is already running for this user.", file=sys.stderr)
            return 2
        stage = "configuration"
        config = _config(args)
        from .greeting import GreetingPlayer
        from .greeting_cache import (GreetingCache, GreetingSpec, default_greeting_directory,
                                     make_synthetic_greeting_cache)
        if preparing:
            stage = "greeting_review"
            cache = GreetingCache(default_greeting_directory(), GreetingSpec.from_config(config))
            if args.prepare_greeting:
                from .private_credentials import resolve_credentials
                credentials = (credential_resolver or (lambda: resolve_credentials(interactive=False)))()
                result = cache.prepare(live_opt_in=True, api_key=credentials.fish_api_key)
                print(json.dumps(result, ensure_ascii=False))
            elif args.approve_greeting:
                result = cache.approve(args.approve_greeting)
                print(json.dumps(result, ensure_ascii=False))
            else:
                voice = GreetingPlayer(config, cache)
                try:
                    voice.play(emit=lambda *_a, **_kw: None, allow_unapproved=True)
                finally:
                    voice.close()
                print("Cached greeting audition finished. Approval remains a separate explicit step.")
            return 0
        app = DesktopRuntime(clock=clock, simulate=args.simulate, developer=args.developer,
                             startup_timeout=args.startup_timeout, greeting_timeout=args.greeting_timeout,
                             require_face_presented=True)
        for number in (signal.SIGTERM, signal.SIGINT):
            try:
                previous_handlers[number] = signal.signal(number, lambda *_: app._exit.set())
            except ValueError:
                pass  # Embedded test callers may have an owner thread outside main.
        face = args.face_file or Path(SCREEN_PROFILES[args.screen_profile]["default_path"])
        stage = "server_init"
        server = FeatureServer(app, face, screen_profile=args.screen_profile, port=0,
                               desktop=app, developer=args.developer)
        stage = "server_start"
        app.set_server(server.start())
        if browser_factory is None:
            from .desktop_browser import DesktopBrowser
            browser_factory = DesktopBrowser
        stage = "browser_init"
        browser_options = {"windowed": args.windowed,
                           "startup_timeout": args.startup_timeout, "clock": clock}
        if args.frontend != "chromium":
            browser_options["renderer"] = args.frontend
        browser = browser_factory(server.url, **browser_options)
        app.set_browser(browser)
        stage = "browser_start"
        browser.start()
        app.browser_started()
        try:
            stage = "engine_init"
            settings = {"config": config, "runtime": RuntimePolicy(simulate=args.simulate,
                        conversation_ptt_microphone=args.simulate or args.ptt_microphone,
                        camera_live=args.camera_live),
                        "demo_assets": args.demo_assets, "clock": clock}
            if args.vad_profile != "normal":
                settings["ptt_vad_profile"] = args.vad_profile
            if args.expressive_voice:
                settings["voice_expressive"] = True
            if args.video_directory is not None:
                settings["video_directory"] = args.video_directory
            if args.camera_live:
                settings["camera_photo_directory"] = args.photo_directory
            if args.music_directory is not None:
                settings["music_directory"] = args.music_directory
            if args.live:
                from .private_credentials import resolve_credentials
                from .cloud import create_client
                credentials = (credential_resolver or (lambda: resolve_credentials(interactive=False)))()
                client = (client_factory or create_client)(config, api_key=credentials.openai_api_key)
                settings.update(client=client, fish_api_key=credentials.fish_api_key)
            host = host_factory(**settings)
            app.set_host(host, None)  # Retain ownership even if greeting setup fails.
            stage = "greeting_init"
            if greeting_factory is not None:
                greeting = greeting_factory(config=config, simulate=args.simulate)
            elif args.simulate:
                # Never inspect real greeting/credential storage during simulation.
                temporary = TemporaryDirectory(prefix="bmo-desktop-synthetic-")
                cache = make_synthetic_greeting_cache(Path(temporary.name)/"greeting", config)
                greeting = GreetingPlayer(config, cache, simulate=True)
            else:
                cache = GreetingCache(default_greeting_directory(), GreetingSpec.from_config(config))
                greeting = GreetingPlayer(config, cache)
            app.greeting = greeting
        except Exception as error:
            code = getattr(error, "code", "engine_startup_failed")
            app.fail(code)
            from .desktop_diagnostics import failure
            print(failure(stage, error), file=sys.stderr)
            if client is not None and app.host is None:
                client.close(); client = None
        if on_started is not None:
            on_started(app)
        stage = "owner_loop"
        # No terminal, stdin, automatic capture or startup chat turn is needed.
        while not app.exiting:
            app.poll()
            sleep(.02)
        if app.failed:
            outcome = 1
    except KeyboardInterrupt:
        pass
    except Exception as error:
        from .desktop_diagnostics import failure
        print(failure(stage, error), file=sys.stderr)
        outcome = 1
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)
        if app is not None:
            try:
                app.close()
            except Exception:
                print("BMO owned cleanup needs review.", file=sys.stderr)
                outcome = 1
        elif client is not None:
            client.close()
        if temporary is not None:
            temporary.cleanup()
        if lock is not None:
            lock.close()
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
