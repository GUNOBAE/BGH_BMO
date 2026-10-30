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
    p.add_argument("--frontend", choices=("auto", "native", "chromium"), default="auto",
                   help="auto selects optional system Qt when present; Chromium remains the explicit fallback")
    p.add_argument("--developer", action="store_true", help="enable /developer and hidden Ctrl+Alt+D")
    p.add_argument("--demo-assets", action="store_true", help="simulation-only generated fixtures")
    p.add_argument("--video-directory", type=Path, help="user-selected private local synthetic-video storage; live camera video remains denied")
    p.add_argument("--music-directory", type=Path, help="explicit reviewed private WAV library; live playback additionally needs --music-supervised")
    p.add_argument("--music-supervised", action="store_true", help="explicit opt-in to requested local WAV playback; requires --live and --music-directory")
    p.add_argument("--usb-gamepad", action="store_true", help="opt-in browser USB gamepad routing in game/menu modes only; never starts PTT or arms")
    p.add_argument("--no-startup-chime", action="store_true", help="skip the original offline boot sound; never changes the approved Aaron greeting")
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
    p.add_argument("--one-turn-diagnostic", type=Path,
                   help="explicit private one-turn transcript/model record outside Git; session-only, never a general log")
    p.add_argument("--ptt-trace", action="store_true", help="opt-in bounded stage/timing/RMS diagnostics; never PCM, transcript, keys or provider messages")
    p.add_argument("--ptt-ambient-profile", type=Path, help="explicit supervised calibration profile; no automatic calibration or idle listening")
    p.add_argument("--plain-voice", action="store_true", help="use plain Aaron voice instead of supported, allowlisted emotional direction")
    p.add_argument("--character-life", action="store_true", help="opt in to the compatible Character Life director and detailed safe timing")
    p.add_argument("--voice-bank-directory", type=Path, help="explicit private approved character clip library; never generates audio")
    p.add_argument("--idle-voice", action="store_true", help="opt in to rare approved local idle clips; no automatic provider calls")
    p.add_argument("--presence-mode", choices=("normal", "quiet", "sleep", "off"), default=None,
                   help="local character speech policy; quiet/sleep/off suppress idle clips")
    p.add_argument("--wake-word", action="store_true", help="explicit optional local microphone listening after startup; missing custom model keeps PTT only")
    p.add_argument("--wake-model-manifest", type=Path, help="reviewed local Hey BMO ONNX manifest; no automatic download")
    p.add_argument("--wake-question-wait", type=float, default=5.,
                   help="wake-only fresh question speech-start window, 1–10 seconds; GP8 timing unchanged")
    p.add_argument("--ptt-vad-profile", choices=("baseline", "balanced", "quick"), default="baseline",
                   help="explicit VAD comparison profile: baseline .9s, balanced .6s, quick .45s; no measured latency claim")
    p.add_argument("--ptt-vad-mode", choices=("rms", "webrtc"), default="rms",
                   help="explicit local speech classifier; rms preserves baseline, webrtc keeps silence .9s and raw PCM")
    p.add_argument("--arms-supervised", action="store_true",
                   help="EXPLICIT live opt-in, only after physical GP7/full-swing acceptance")
    p.add_argument("--emotion-arms", action="store_true",
                   help="opt-in emotional arm gestures; simulation only until supervised firmware works")
    p.add_argument("--arm-legacy-bridge", action="store_true",
                   help="separate B1 bridge derived from SHA-verified original GP7 firmware")
    p.add_argument("--arm-reviewed-code-sha", metavar="SHA256",
                   help="manually reviewed exact SHA of deployed full-range RP2040 code.py")
    p.add_argument("--arm-power-off-ready", action="store_true",
                   help="affirm user physically present with immediate entire-BMO power cut")
    p.add_argument("--camera-supervised", action="store_true", help="explicit user-present camera activation path; no camera opens until a requested action")
    p.add_argument("--photo-directory", type=Path, help="required private local destination for supervised camera photos")
    p.add_argument("--camera-video-supervised", action="store_true", help="also permit explicitly requested supervised camera video; requires camera and video directory")
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
        "frontend": args.frontend, "native_pi_verified": "NOT_RUN",
        "startup_sound": {"enabled": not args.no_startup_chime,
                          "source": "original_local_pcm", "automatic_synthesis": False},
        "greeting": {"text": GREETING_TEXT, "asset_version": "english-v3", "source": "approved_private_cache", "automatic_synthesis": False},
        "configuration": effective_config(config, emotion_mode="auto"),
        "credentials_accessed": False, "ports_opened": False, "processes_started": False,
        "desktop_installed": False, "autostart": False, "physical_features_enabled": False,
        "ptt": {"enabled": args.simulate or args.ptt_microphone,
                "source": "synthetic_pcm" if args.simulate else "explicit_microphone" if args.ptt_microphone else "disabled",
                "automatic_capture": False, "wake_word_enabled": False, "physical_verified": False},
        "one_turn_diagnostic": {"requested": args.one_turn_diagnostic is not None,
                                "one_turn_only": True, "file_created": False},
        "ptt_diagnostics": {"trace_enabled": args.ptt_trace,
                            "ambient_profile_selected": args.ptt_ambient_profile is not None,
                            "raw_audio_logged": False, "transcripts_logged": False},
        "voice_direction": {"requested": not args.plain_voice, "speaker_changed": False,
                            "listening_approval": "NOT_RUN"},
        "character_life": {"enabled": args.character_life, "ptt_vad_profile": args.ptt_vad_profile,
                           "ptt_vad_mode": args.ptt_vad_mode,
                           "actual_latency": "UNMEASURED", "audio_generated": False,
                           "presence_mode": args.presence_mode or ("normal" if args.idle_voice else "off"),
                           "voice_bank_selected": args.voice_bank_directory is not None,
                           "wake_requested": args.wake_word,
                           "wake_question_wait": args.wake_question_wait,
                           "wake_model": "NOT_CHECKED" if args.wake_model_manifest else "MODEL_MISSING",
                           "acoustic_wake_acceptance": "NOT_RUN"},
        "arm_legacy_bridge": {"requested": args.arm_legacy_bridge,
                              "default_disabled": not args.arms_supervised,
                              "physical_verified": False},
        "emotion_arms": {"requested": args.emotion_arms, "gesture_names_only": True,
                         "automatic_serial_or_firmware_changes": False},
        "camera": {"supervised_opt_in": args.camera_supervised,
                   "photo_storage_selected": args.photo_directory is not None,
                   "automatic_capture": False, "physical_verified": False},
        "camera_video": {"storage_selected": args.video_directory is not None,
                         "live_enabled": args.camera_video_supervised, "automatic_capture": False},
        "music_library": {"selected": args.music_directory is not None,
                          "live_playback_enabled": args.music_supervised, "automatic_import": False},
        "usb_gamepad": {"enabled": args.usb_gamepad, "physical_verified": False,
                        "conversation_input": False, "motor_input": False},
        "pi_kiosk_verified": "NOT_RUN"}


def main(argv=None, *, lock_path=None, host_factory=FeatureHost, browser_factory=None,
         greeting_factory=None, credential_resolver=None, client_factory=None,
         clock=time.monotonic, sleep=time.sleep, on_started=None):
    p = parser()
    args = p.parse_args(argv)
    for value in (args.startup_timeout, args.greeting_timeout):
        if not 0 < value <= 120:
            p.error("timeouts must be finite and from 0 to 120 seconds")
    if not 1 <= args.wake_question_wait <= 10:
        p.error("--wake-question-wait must be finite and from 1 to 10 seconds")
    if args.wake_question_wait != 5. and not args.wake_word:
        p.error("--wake-question-wait requires --wake-word")
    if args.one_turn_diagnostic is not None and not args.character_life:
        p.error("--one-turn-diagnostic requires --character-life")
    if args.one_turn_diagnostic is not None and (args.install_desktop or args.prepare_greeting
            or args.audition_greeting or args.approve_greeting is not None):
        p.error("--one-turn-diagnostic is session-only; do not install or combine with greeting review")
    if args.demo_assets and not args.simulate:
        p.error("--demo-assets requires --simulate")
    if args.music_directory is not None and args.demo_assets:
        p.error("--music-directory and --demo-assets are separate catalogs")
    if args.music_supervised and (not args.live or args.music_directory is None):
        p.error("--music-supervised requires --live and --music-directory")
    if args.ptt_ambient_profile is not None and not args.ptt_microphone:
        p.error("--ptt-ambient-profile requires --ptt-microphone")
    if args.ptt_vad_mode != "rms" and (not args.character_life or not (args.simulate or args.ptt_microphone)):
        p.error("--ptt-vad-mode webrtc requires --character-life and --ptt-microphone (or --simulate)")
    if (args.voice_bank_directory is not None or args.idle_voice or args.presence_mode is not None
            or args.wake_word or args.wake_model_manifest is not None) and not args.character_life:
        p.error("character bank, presence and wake options require --character-life")
    if args.wake_word and not (args.simulate or args.ptt_microphone):
        p.error("--wake-word requires --ptt-microphone (or isolated --simulate)")
    if args.wake_model_manifest is not None and not args.wake_word:
        p.error("--wake-model-manifest requires --wake-word")
    if args.simulate and args.wake_model_manifest is not None:
        p.error("real wake models are separate from synthetic desktop simulation")
    if args.install_desktop and (args.arms_supervised or args.emotion_arms
            or args.arm_legacy_bridge or args.arm_reviewed_code_sha is not None
            or args.arm_power_off_ready):
        # The installer has no arm configuration. Never silently discard these
        # options or turn a session's physical acknowledgements into a launcher.
        p.error("arm controls are session-only and cannot be used with --install-desktop; "
                "launch the supervised arm session directly")
    if args.arm_legacy_bridge and not args.arms_supervised:
        p.error("--arm-legacy-bridge requires an independently supervised physical arm session")
    if args.emotion_arms and not (args.simulate or args.arms_supervised):
        p.error("--emotion-arms requires --simulate or an independently approved --arms-supervised")
    if args.arms_supervised and (not args.live or not args.arm_reviewed_code_sha
                                or not args.arm_power_off_ready):
        p.error("--arms-supervised needs --live, --arm-reviewed-code-sha and --arm-power-off-ready")
    if not args.arms_supervised and (args.arm_reviewed_code_sha or args.arm_power_off_ready):
        p.error("arm-related confirmations require explicit --arms-supervised")
    if args.camera_supervised and (not args.live or args.photo_directory is None):
        p.error("--camera-supervised requires --live and --photo-directory")
    if args.photo_directory is not None and not args.camera_supervised:
        p.error("--photo-directory requires --camera-supervised")
    if args.camera_video_supervised and (not args.camera_supervised or args.video_directory is None):
        p.error("--camera-video-supervised requires --camera-supervised and --video-directory")
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
            install_settings = {"install": not args.dry_run, "simulate": args.simulate}
            if args.character_life:
                character_options = {"character_life": True, "ptt_trace": args.ptt_trace,
                    "ptt_vad_profile": args.ptt_vad_profile, "plain_voice": args.plain_voice,
                    "idle_voice": args.idle_voice, "wake_word": args.wake_word}
                if args.ptt_vad_mode != "rms":
                    character_options["ptt_vad_mode"] = args.ptt_vad_mode
                if args.wake_question_wait != 5.:
                    character_options["wake_question_wait"] = args.wake_question_wait
                for key in ("voice_bank_directory", "presence_mode", "wake_model_manifest"):
                    value = getattr(args, key)
                    if value is not None:
                        character_options[key] = value
                install_settings["character_options"] = character_options
            install_settings["frontend"] = args.frontend
            if args.ptt_microphone:
                install_settings["ptt_microphone"] = True
            if args.usb_gamepad:
                install_settings["usb_gamepad"] = True
            if args.music_directory is not None:
                install_settings["music_directory"] = args.music_directory
            if args.music_supervised:
                install_settings["music_supervised"] = True
            if args.no_startup_chime:
                install_settings["startup_chime"] = False
            if args.camera_supervised:
                install_settings.update(camera_supervised=True, photo_directory=args.photo_directory)
            if args.video_directory is not None:
                install_settings["video_directory"] = args.video_directory
            if args.camera_video_supervised:
                install_settings["camera_video_supervised"] = True
            result = install_desktop(**install_settings)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except Exception:
            print("BMO desktop installation could not complete safely.", file=sys.stderr)
            return 1
    if not (args.simulate or args.live):
        p.error("choose --simulate or explicit --live")
    if args.one_turn_diagnostic is not None:
        from .one_turn_diagnostic import validate_destination, OneTurnDiagnosticError
        try:
            validate_destination(args.one_turn_diagnostic)
        except OneTurnDiagnosticError as error:
            p.error(error.code)
    lock = client = app = temporary = arm_port = None
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
                             require_face_presented=True, character_life=args.character_life,
                             presence_mode=args.presence_mode or ("normal" if args.idle_voice else "off"))
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
        using_builtin_browser = browser_factory is None
        if using_builtin_browser:
            from .desktop_browser import DesktopBrowser
            browser_factory = DesktopBrowser
        stage = "browser_init"
        browser_settings = dict(windowed=args.windowed,
                                startup_timeout=args.startup_timeout, clock=clock)
        # Existing embedded/test factories retain their constructor contract;
        # default auto detection belongs to the built-in frontend owner only.
        if args.frontend != "chromium" and (using_builtin_browser or args.frontend != "auto"):
            browser_settings["frontend"] = args.frontend
        browser = browser_factory(server.url, **browser_settings)
        app.set_browser(browser)
        stage = "browser_start"
        browser.start()
        app.browser_started()
        try:
            stage = "engine_init"
            settings = {"config": config, "runtime": RuntimePolicy(simulate=args.simulate,
                        conversation_ptt_microphone=args.simulate or args.ptt_microphone,
                        arms_supervised=args.arms_supervised),
                        "demo_assets": args.demo_assets, "clock": clock,
                        "ptt_trace": args.ptt_trace, "ptt_ambient_profile": args.ptt_ambient_profile,
                        "ptt_speech_safe": True,
                        "expressive_voice": not args.plain_voice,
                        "emotion_arms": args.emotion_arms}
            if args.character_life:
                settings["character_life"] = True
            if args.one_turn_diagnostic is not None:
                settings["one_turn_diagnostic"] = args.one_turn_diagnostic
            if args.wake_word:
                settings.update(wake_word=True, wake_model_manifest=args.wake_model_manifest,
                                wake_question_wait=args.wake_question_wait)
            if args.ptt_vad_profile != "baseline":
                settings["ptt_vad_profile"] = args.ptt_vad_profile
            if args.ptt_vad_mode != "rms":
                settings["ptt_vad_mode"] = args.ptt_vad_mode
            if args.music_supervised:
                settings["music_supervised"] = True
            if args.usb_gamepad:
                settings["usb_gamepad"] = True
            if args.camera_supervised:
                settings.update(camera_supervised=True, photo_directory=args.photo_directory,
                                camera_video_supervised=args.camera_video_supervised)
            if args.video_directory is not None:
                settings["video_directory"] = args.video_directory
            if args.music_directory is not None:
                settings["music_directory"] = args.music_directory
            if args.live:
                from .private_credentials import resolve_credentials
                from .cloud import create_client
                credentials = (credential_resolver or (lambda: resolve_credentials(interactive=False)))()
                client = (client_factory or create_client)(config, api_key=credentials.openai_api_key)
                settings.update(client=client, fish_api_key=credentials.fish_api_key)
            if args.arms_supervised:
                from .features.motion.arm_serial_posix_v2 import OwnedCdcDataPort
                if args.arm_legacy_bridge:
                    from .features.motion.legacy_host import (
                        LegacyArmLink, LegacyMotionFeature, validate_legacy_install)
                    firmware_root = (Path(__file__).resolve().parents[2]
                                     / "firmware/staging/bmo_arm_legacy_bridge_v1")
                    validate_legacy_install(firmware_root, args.arm_reviewed_code_sha)
                    arm_port = OwnedCdcDataPort().open()
                    arm_host = LegacyArmLink(arm_port, motor_permission=lambda:
                        bool(args.arm_power_off_ready and app is not None and app._ready
                             and not app.exiting and not app.failed))
                    settings["motion"] = LegacyMotionFeature(arm_host, clock=clock)
                else:
                    from .features.motion.arm_serial_posix_v2 import validate_reviewed_install
                    from .features.motion.arm_host_v2 import ArmHostV2, BoundedCdcExchange
                    from .features.motion.gesture_rules_v2 import LEGACY_REVIEWED_LIMITS
                    from .features.motion.live_feature_v2 import LiveArmMotionFeatureV2
                    source_core = (Path(__file__).resolve().parents[2]
                                   / "firmware/staging/bmo_arm_supervised_v1/safe_arm_core.py")
                    validate_reviewed_install(source_core, args.arm_reviewed_code_sha)
                    arm_port = OwnedCdcDataPort().open()
                    arm_host = ArmHostV2(
                        BoundedCdcExchange(arm_port), profile=LEGACY_REVIEWED_LIMITS,
                        clock=clock, power_cutoff_confirmed=lambda: args.arm_power_off_ready,
                        operator_present=lambda: bool(app is not None and app._ready
                                                      and not app.exiting and not app.failed))
                    settings["motion"] = LiveArmMotionFeatureV2(arm_host, clock=clock)
            host = host_factory(**settings)
            app.set_host(host, None)  # Retain ownership even if greeting setup fails.
            stage = "greeting_init"
            if greeting_factory is not None:
                greeting = greeting_factory(config=config, simulate=args.simulate)
            elif args.simulate:
                # Never inspect real greeting/credential storage during simulation.
                temporary = TemporaryDirectory(prefix="bmo-desktop-synthetic-")
                cache = make_synthetic_greeting_cache(Path(temporary.name)/"greeting", config)
                greeting = GreetingPlayer(config, cache, simulate=True,
                    startup_chime=not args.no_startup_chime, chime_home=Path(temporary.name),
                    allow_synthesized_chime=not args.character_life)
            else:
                cache = GreetingCache(default_greeting_directory(), GreetingSpec.from_config(config))
                greeting = GreetingPlayer(config, cache, startup_chime=not args.no_startup_chime,
                                          allow_synthesized_chime=not args.character_life)
            if args.character_life:
                greeting.allow_synthesized_chime = False
                from .voice_bank import VoiceBank, BankPlayer, default_voice_bank_directory
                from .character_presence import CachedPresence
                if args.simulate and temporary is None:
                    temporary = TemporaryDirectory(prefix="bmo-character-synthetic-")
                bank_directory = (args.voice_bank_directory if args.voice_bank_directory is not None else
                    Path(temporary.name)/"voice-bank" if args.simulate else default_voice_bank_directory())
                bank = VoiceBank(bank_directory, config, simulate=args.simulate)
                greeting = CachedPresence(bank, BankPlayer(config, bank, simulate=args.simulate), greeting)
                host.session.voice.character_bank = bank
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
            except Exception as error:
                from .desktop_diagnostics import failure
                print(failure("cleanup", error), file=sys.stderr)
                outcome = 1
            print("BMO desktop cleanup: " + json.dumps(app.cleanup_snapshot(),
                  ensure_ascii=False, sort_keys=True), file=sys.stderr)
        elif client is not None:
            client.close()
        if arm_port is not None:
            try:
                arm_port.close()
            except Exception:
                print("BMO desktop arm CDC owner cleanup failed.", file=sys.stderr)
                outcome = 1
        if temporary is not None:
            try:
                temporary.cleanup()
            except Exception:
                print("BMO desktop failed: stage=cleanup code=synthetic_cache_cleanup_failed", file=sys.stderr)
                outcome = 1
        if lock is not None:
            try:
                lock.close()
            except Exception:
                print("BMO desktop failed: stage=cleanup code=lock_release_failed", file=sys.stderr)
                outcome = 1
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
