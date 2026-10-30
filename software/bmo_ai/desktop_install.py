"""Review-first per-user desktop entries; installation is an explicit opt-in.

No process, desktop trust command, package tool, service or autostart is run.
Existing entries are never replaced. A failed multi-file publication rolls back
only this transaction's new files, retaining ambiguous data for manual review.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shlex
import stat


ICON_SHA256 = "140b9bbefeaabdc3d7a1c7718faf082c395ebd0caa553e62e74c584c283a73f1"
_CANDIDATE_LAUNCHER_SOURCE_SHA256 = "dc41fe16e510d2789ce5d2e3c252abfb7c0b2bf439eb705b19d2b5cce3547b19"
INSTALL_TARGETS = ("desktop-and-applications", "applications-only")
_ICON = Path(__file__).parent / "desktop_assets" / "bmo-generic.svg"
_MESSAGES = {
    "desktop_invalid_config": "Desktop installation configuration is invalid.",
    "desktop_unsafe_directory": "A desktop installation directory is unsafe.",
    "desktop_unsafe_file": "An existing desktop installation file is unsafe.",
    "desktop_entry_conflict": "An existing BMO desktop file must be reviewed manually.",
    "desktop_launcher_pending": "The BMO desktop launcher is not ready for installation.",
    "desktop_icon_invalid": "The generic BMO icon did not pass verification.",
    "desktop_install_busy": "Another BMO desktop installation is in progress.",
    "desktop_install_failed": "BMO desktop installation failed without replacing existing entries.",
    "desktop_install_cancelled": "BMO desktop installation was cancelled.",
    "desktop_install_rollback_pending": "BMO desktop installation needs manual recovery.",
}


class DesktopInstallError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else "desktop_invalid_config"
        self.safe_message = _MESSAGES[self.code]
        super().__init__(self.safe_message)


def _path(value):
    try:
        result = Path(value)
        if (not result.is_absolute() or ".." in result.parts
                or not all(char.isalnum() or char in "/._ -" for char in str(result))):
            raise DesktopInstallError("desktop_invalid_config")
        return result
    except (ValueError, TypeError):
        raise DesktopInstallError("desktop_invalid_config") from None


def _character_arguments(options, *, simulate, ptt_microphone):
    """Persist only explicit character opt-ins; never keys or arbitrary flags."""
    if options is None:
        return []
    booleans = ("character_life", "ptt_trace", "plain_voice", "idle_voice", "wake_word",
                "silent_start", "no_feedback_voice")
    choices = {"ptt_vad_profile": ("baseline", "balanced", "quick"),
               "ptt_vad_mode": ("rms", "webrtc"),
               "presence_mode": ("normal", "quiet", "sleep", "off")}
    paths = ("voice_bank_directory", "voice_pack_directory", "wake_model_manifest")
    numbers = ("wake_question_wait", "request_cooldown", "session_request_limit",
               "estimated_request_cost", "estimated_session_cost_limit")
    if (type(options) is not dict
            or set(options) - {*booleans, *choices, *paths, *numbers}
            or any(type(options[key]) is not bool for key in booleans if key in options)
            or any(type(options[key]) is not str or options[key] not in values
                   for key, values in choices.items() if key in options)
            or any(options[key] is None for key in numbers if key in options)
            or "wake_question_wait" in options and (type(options["wake_question_wait"]) not in (int,float)
                or not math.isfinite(options["wake_question_wait"]) or not 1 <= options["wake_question_wait"] <= 10
                or options.get("wake_word") is not True)
            or options.keys() - {"character_life"} and options.get("character_life") is not True
            or options.get("wake_word") is True and not (ptt_microphone or simulate)
            or "wake_model_manifest" in options and (options.get("wake_word") is not True or simulate)):
        raise DesktopInstallError("desktop_invalid_config")
    if any(key in options for key in numbers if key != 'wake_question_wait'):
        # Reuse the admission contract without consuming a request or probing
        # a provider. Optional coefficients remain estimates, never billing.
        from .request_budget import RequestBudget
        try:
            RequestBudget(cooldown=options.get('request_cooldown', 0),
                max_requests=options.get('session_request_limit'),
                cost_per_request=options.get('estimated_request_cost'),
                max_estimated_cost=options.get('estimated_session_cost_limit'))
        except Exception:
            raise DesktopInstallError('desktop_invalid_config') from None
    normalized_paths = {key: _path(options[key]) for key in paths if key in options}
    result = []
    # This stable order is independent of dictionary insertion order.
    for key in ("character_life", "ptt_trace", "ptt_vad_profile", "ptt_vad_mode", "plain_voice",
                "voice_bank_directory", "voice_pack_directory", "idle_voice", "presence_mode", "wake_word", "wake_model_manifest", "wake_question_wait",
                "silent_start", "no_feedback_voice", "request_cooldown", "session_request_limit",
                "estimated_request_cost", "estimated_session_cost_limit"):
        if key not in options:
            continue
        flag = "--" + key.replace("_", "-")
        if key in booleans:
            if options[key]: result.append(flag)
        else:
            result.extend((flag, str(normalized_paths[key]) if key in paths else str(options[key])))
    return result


def _identity(info):
    return info.st_dev, info.st_ino


def _directory(path, *, home=None, create=False, created=None):
    descriptor = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", flags)
        current = Path("/")
        for part in path.parts[1:]:
            current /= part
            try:
                following = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                    if created is not None:
                        info = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                        created.append((current, _identity(info)))
                except FileExistsError:
                    pass
                following = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
            if home is not None and (current == home or current.is_relative_to(home)):
                info = os.fstat(descriptor)
                if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                    raise DesktopInstallError("desktop_unsafe_directory")
        result, descriptor = descriptor, None
        return result
    except (OSError, ValueError, TypeError):
        raise DesktopInstallError("desktop_unsafe_directory") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _source(path, *, limit, executable=False):
    parent = descriptor = None
    try:
        parent = _directory(path.parent)
        if parent is None:
            raise FileNotFoundError()
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=parent)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0, os.geteuid())
                or before.st_nlink != 1 or before.st_mode & (0o6022 if executable else 0o6002)
                or before.st_size > limit
                or executable and not before.st_mode & 0o100):
            raise DesktopInstallError("desktop_launcher_pending" if executable else "desktop_icon_invalid")
        if executable:
            return True
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(descriptor)
        if (len(raw) > limit or (_identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (_identity(after), after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise DesktopInstallError("desktop_icon_invalid")
        return raw
    except (OSError, ValueError, TypeError):
        raise DesktopInstallError("desktop_launcher_pending" if executable else "desktop_icon_invalid") from None
    finally:
        for value in (descriptor, parent):
            if value is not None:
                os.close(value)


def _configuration(project_root, home, environ, simulate, ptt_microphone=False,
                   frontend="auto", usb_gamepad=False, music_directory=None,
                   music_supervised=False, startup_chime=True, camera_supervised=False,
                   photo_directory=None, camera_video_supervised=False, video_directory=None,
                   character_options=None, playable_games=False, install_target="desktop-and-applications"):
    if (any(type(value) is not bool for value in (simulate, ptt_microphone, usb_gamepad, music_supervised, startup_chime,
                                                       camera_supervised, camera_video_supervised, playable_games))
            or type(frontend) is not str or frontend not in ("auto", "native", "chromium")
            or type(install_target) is not str or install_target not in INSTALL_TARGETS
            or music_supervised and (simulate or music_directory is None)
            or camera_supervised and (simulate or photo_directory is None)
            or photo_directory is not None and not camera_supervised
            or camera_video_supervised and (not camera_supervised or video_directory is None)):
        raise DesktopInstallError("desktop_invalid_config")
    character_arguments = _character_arguments(character_options, simulate=simulate, ptt_microphone=ptt_microphone)
    source = os.environ if environ is None else environ
    project = _path(project_root if project_root is not None else Path(__file__).resolve().parents[2])
    home = _path(home if home is not None else source.get("HOME", Path.home()))
    desktop = _path(source.get("XDG_DESKTOP_DIR", home / "Desktop"))
    data = _path(source.get("XDG_DATA_HOME", home / ".local" / "share"))
    if any(path == home or not path.is_relative_to(home) for path in (desktop, data)):
        raise DesktopInstallError("desktop_invalid_config")
    if any(part.casefold() in (".config", ".ssh", "autostart", "systemd")
           for path in (desktop, data) for part in path.relative_to(home).parts):
        raise DesktopInstallError("desktop_invalid_config")
    home_fd = _directory(home, home=home)
    if home_fd is None:
        raise DesktopInstallError("desktop_unsafe_directory")
    os.close(home_fd)
    icon = _source(_ICON, limit=4096)
    if hashlib.sha256(icon).hexdigest() != ICON_SHA256:
        raise DesktopInstallError("desktop_icon_invalid")
    launcher = project / "launch-desktop"
    candidate = bool(character_options and character_options.get("character_life"))
    private_candidate = candidate and install_target == "applications-only"
    wrapper = None
    try:
        if private_candidate:
            # The reviewed source is read as data and hash-checked; this path
            # never executes or copies a group-writable source shell script.
            # Normal executable-source guards remain unchanged. The generated
            # private entry below selects the same interpreter/module/options.
            source = _source(launcher, limit=65536)
            launcher_verified = hashlib.sha256(source).hexdigest() == _CANDIDATE_LAUNCHER_SOURCE_SHA256
            launcher = data / "bmo-desktop" / "BMO-Candidate-launcher"
            text = ("#!/bin/sh\nset -eu\n"
                "bmo_candidate_dir=" + shlex.quote(str(project)) + "\n"
                'bmo_candidate_python=${BMO_PYTHON:-"$bmo_candidate_dir/.venv-device/bin/python"}\n'
                'if [ ! -x "$bmo_candidate_python" ]; then\n'
                "  printf '%s\\n' 'BMO Candidate needs its .venv-device/bin/python or explicit BMO_PYTHON.' >&2\n"
                "  exit 2\nfi\n"
                'cd "$bmo_candidate_dir/software"\n'
                'exec "$bmo_candidate_python" -B -m bmo_ai.desktop_app "$@"\n')
            wrapper = text.encode()
        else:
            launcher_verified = _source(launcher, limit=1024 * 1024, executable=True)
    except DesktopInstallError:
        launcher_verified = False
    entry_name = "BMO-Candidate.desktop" if candidate else "BMO.desktop"
    icon_path = data / "icons" / "hicolor" / "scalable" / "apps" / ("bmo-candidate.svg" if candidate else "bmo-desktop.svg")
    command = [str(launcher), "--simulate" if simulate else "--live"]
    if frontend != "auto":
        command.extend(("--frontend", frontend))
    if ptt_microphone:
        command.append("--ptt-microphone")
    if usb_gamepad:
        command.append("--usb-gamepad")
    if playable_games:
        command.append("--playable-games")
    if music_directory is not None:
        command.extend(("--music-directory", str(_path(music_directory))))
    if music_supervised:
        command.append("--music-supervised")
    if not startup_chime:
        command.append("--no-startup-chime")
    if camera_supervised:
        command.extend(("--camera-supervised", "--photo-directory", str(_path(photo_directory))))
    if video_directory is not None:
        command.extend(("--video-directory", str(_path(video_directory))))
    if camera_video_supervised:
        command.append("--camera-video-supervised")
    command.extend(character_arguments)
    template = ("[Desktop Entry]\nType=Application\nName=" + ("BMO Candidate" if candidate else "BMO") + "\n"
                "Comment=Start the BMO desktop app manually\n"
                "Exec=" + " ".join('"' + value + '"' for value in command) + "\n"
                "Path=" + str(project) + "\nIcon=" + str(icon_path) + "\nTerminal=false\n"
                "StartupNotify=false\nCategories=Game;Utility;\nX-GNOME-Autostart-enabled=false\n")
    targets = []
    if install_target == "desktop-and-applications":
        targets.append((desktop / entry_name, template.encode("utf-8"), 0o700))
    targets.extend(((data / "applications" / entry_name, template.encode("utf-8"), 0o600),
                    (icon_path, icon, 0o600)))
    if wrapper is not None:
        targets.append((launcher, wrapper, 0o700))
    targets = tuple(targets)
    if len({path for path, _, _ in targets}) != len(targets):
        raise DesktopInstallError("desktop_invalid_config")
    return project, home, data, command, template, targets, bool(launcher_verified)


def _existing(directory, name, raw, mode):
    descriptor = None
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                             dir_fd=directory)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != mode or before.st_size > 65536):
            raise DesktopInstallError("desktop_unsafe_file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            actual = source.read(65537)
        after = os.fstat(descriptor)
        if (_identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                _identity(after), after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise DesktopInstallError("desktop_unsafe_file")
        if actual != raw:
            raise DesktopInstallError("desktop_entry_conflict")
        return True
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError):
        raise DesktopInstallError("desktop_unsafe_file") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def desktop_install_plan(*, project_root=None, home=None, environ=None, simulate=False, ptt_microphone=False,
                         frontend="auto", usb_gamepad=False, music_directory=None,
                         music_supervised=False, startup_chime=True, camera_supervised=False,
                   photo_directory=None, camera_video_supervised=False, video_directory=None,
                   character_options=None, playable_games=False, install_target="desktop-and-applications"):
    _, user_home, _, command, template, targets, verified = _configuration(project_root, home, environ, simulate, ptt_microphone,
        frontend, usb_gamepad, music_directory, music_supervised, startup_chime,
        camera_supervised, photo_directory, camera_video_supervised, video_directory, character_options, playable_games,
        install_target)
    rows = []
    for path, raw, mode in targets:
        directory = _directory(path.parent, home=user_home)
        try:
            exists = directory is not None and _existing(directory, path.name, raw, mode)
            rows.append({"path": str(path), "mode": oct(mode), "status": "IDENTICAL" if exists else "ABSENT"})
        finally:
            if directory is not None:
                os.close(directory)
    return {"status": "DRY_RUN", "launcher_verified": verified, "command": command,
            "template": template, "targets": rows, "icon_sha256": ICON_SHA256,
            "files_written": 0, "autostart": False, "services_changed": False,
            "microphone_button_opt_in": ptt_microphone, "automatic_capture": False,
            "frontend": frontend, "usb_gamepad_opt_in": usb_gamepad,
            "playable_games_opt_in": playable_games,
            "install_target": install_target,
            "launcher_kind": "private_candidate_wrapper" if character_options and character_options.get("character_life") and install_target == "applications-only" else "repository_launcher",
            "music_playback_opt_in": music_supervised, "startup_chime": startup_chime,
            "camera_opt_in": camera_supervised, "camera_video_opt_in": camera_video_supervised,
            "activation": "NOT_RUN", "desktop_trust": "NOT_RUN"}


def _remove_owned(directory, name, identity):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if _identity(info) != identity or not stat.S_ISREG(info.st_mode):
        raise DesktopInstallError("desktop_install_rollback_pending")
    os.unlink(name, dir_fd=directory)


def install_desktop(*, project_root=None, home=None, environ=None, simulate=False, install=False, ptt_microphone=False,
                    frontend="auto", usb_gamepad=False, music_directory=None,
                    music_supervised=False, startup_chime=True, camera_supervised=False,
                   photo_directory=None, camera_video_supervised=False, video_directory=None,
                   character_options=None, playable_games=False, install_target="desktop-and-applications"):
    if type(install) is not bool:
        raise DesktopInstallError("desktop_invalid_config")
    if not install:
        return desktop_install_plan(project_root=project_root, home=home, environ=environ, simulate=simulate,
                                    ptt_microphone=ptt_microphone, frontend=frontend,
                                    usb_gamepad=usb_gamepad, music_directory=music_directory,
                                    music_supervised=music_supervised, startup_chime=startup_chime,
                                    camera_supervised=camera_supervised, photo_directory=photo_directory,
                                    camera_video_supervised=camera_video_supervised, video_directory=video_directory,
                                    character_options=character_options, playable_games=playable_games,
                                    install_target=install_target)
    _, user_home, data, command, _, targets, verified = _configuration(project_root, home, environ, simulate, ptt_microphone,
        frontend, usb_gamepad, music_directory, music_supervised, startup_chime,
        camera_supervised, photo_directory, camera_video_supervised, video_directory, character_options, playable_games,
        install_target)
    if not verified:
        raise DesktopInstallError("desktop_launcher_pending")
    created, descriptors, stages, published = [], [], [], []
    lock = lock_directory = None
    try:
        lock_directory = _directory(data / "bmo-desktop", home=user_home, create=True, created=created)
        lock = os.open(".install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                       0o600, dir_fd=lock_directory)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 0):
            raise DesktopInstallError("desktop_unsafe_file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DesktopInstallError("desktop_install_busy") from None
        pending = []
        for path, raw, mode in targets:
            directory = _directory(path.parent, home=user_home, create=True, created=created)
            descriptors.append(directory)
            if not _existing(directory, path.name, raw, mode):
                pending.append((directory, path.name, raw, mode))
        for directory, name, raw, mode in pending:
            for _ in range(10):
                stage = ".bmo-desktop-stage-" + secrets.token_hex(12)
                try:
                    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         mode, dir_fd=directory)
                    break
                except FileExistsError:
                    continue
            else:
                raise DesktopInstallError("desktop_install_busy")
            identity = _identity(os.fstat(descriptor))
            stages.append((directory, stage, identity))
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            # link creates a new name atomically and refuses any target that
            # appeared after the duplicate check. Existing bytes are untouched.
            published.append((directory, name, identity))
            os.link(stage, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            os.unlink(stage, dir_fd=directory)
            stages.pop()
            os.fsync(directory)
        return {"status": "INSTALLED" if published else "ALREADY_INSTALLED", "command": command,
                "files_written": len(published), "icon_sha256": ICON_SHA256,
                "autostart": False, "services_changed": False,
                "microphone_button_opt_in": ptt_microphone, "automatic_capture": False,
            "frontend": frontend, "usb_gamepad_opt_in": usb_gamepad,
            "playable_games_opt_in": playable_games,
            "install_target": install_target,
            "launcher_kind": "private_candidate_wrapper" if character_options and character_options.get("character_life") and install_target == "applications-only" else "repository_launcher",
            "music_playback_opt_in": music_supervised, "startup_chime": startup_chime,
            "camera_opt_in": camera_supervised, "camera_video_opt_in": camera_video_supervised,
                "activation": "NOT_RUN", "desktop_trust": "NOT_RUN"}
    except BaseException as error:
        rollback_failed = False
        for directory, name, identity in reversed(published + stages):
            try:
                _remove_owned(directory, name, identity)
                os.fsync(directory)
            except (OSError, DesktopInstallError):
                rollback_failed = True
        if rollback_failed:
            raise DesktopInstallError("desktop_install_rollback_pending") from None
        if isinstance(error, DesktopInstallError):
            raise
        raise DesktopInstallError("desktop_install_cancelled" if isinstance(error, KeyboardInterrupt)
                                  else "desktop_install_failed") from None
    finally:
        for descriptor in [*descriptors, lock, lock_directory]:
            if descriptor is not None:
                os.close(descriptor)
        # Empty directories created by this attempt may be removed. No recursive
        # removal, old backup deletion or existing directory mode change occurs.
        for path, identity in reversed(created):
            parent = None
            try:
                parent = _directory(path.parent, home=user_home)
                if parent is not None and _identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == identity:
                    os.rmdir(path.name, dir_fd=parent)
            except (OSError, DesktopInstallError):
                pass
            finally:
                if parent is not None:
                    os.close(parent)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-desktop", action="store_true")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--ptt-microphone", action="store_true", help="explicitly persist button-only finite microphone admission in the new icon")
    parser.add_argument("--frontend", choices=("auto", "native", "chromium"), default="auto")
    parser.add_argument("--usb-gamepad", action="store_true")
    parser.add_argument("--playable-games", action="store_true")
    parser.add_argument("--desktop-install-target", choices=INSTALL_TARGETS, default="desktop-and-applications")
    parser.add_argument("--music-directory", type=Path)
    parser.add_argument("--music-supervised", action="store_true")
    parser.add_argument("--no-startup-chime", action="store_true")
    parser.add_argument("--camera-supervised", action="store_true")
    parser.add_argument("--photo-directory", type=Path)
    parser.add_argument("--camera-video-supervised", action="store_true")
    parser.add_argument("--video-directory", type=Path)
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args(argv)
    try:
        report = install_desktop(project_root=args.project_root, simulate=args.simulate,
                                 install=args.install_desktop, ptt_microphone=args.ptt_microphone,
                                 frontend=args.frontend, usb_gamepad=args.usb_gamepad,
                                 playable_games=args.playable_games,
                                 install_target=args.desktop_install_target,
                                 music_directory=args.music_directory, music_supervised=args.music_supervised,
                                 startup_chime=not args.no_startup_chime,
                                 camera_supervised=args.camera_supervised, photo_directory=args.photo_directory,
                                 camera_video_supervised=args.camera_video_supervised, video_directory=args.video_directory)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except DesktopInstallError as error:
        print(json.dumps({"status": "REJECTED", "code": error.code, "message": error.safe_message}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
