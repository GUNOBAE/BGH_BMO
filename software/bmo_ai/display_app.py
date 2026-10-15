"""Manual, display-only BMO app. Import and --help never initialize SDL.

Only an existing desktop session is supported. Audio, cloud and controller
adapters are not imported. Desktop fullscreen never changes the monitor mode.
"""
from __future__ import annotations

import argparse
import os

from .face import PygameFace
from .hardware import FaceState


class DisplayAppError(RuntimeError):
    """A fixed, actionable startup message, without environment values."""


def _desktop_window():
    from pygame._sdl2.video import Window

    return Window.from_display_module()


def run_display(options, pygame, *, window_factory=None, output_fn=print):
    """Own this process's display only; tests may supply an entirely fake SDL."""
    if pygame.display.get_init():
        raise DisplayAppError("Run the face app in a separate process with its own display.")
    renderer = None
    previous_cursor = None
    try:
        pygame.display.init()
        if pygame.display.get_driver() not in ("wayland", "x11", "dummy"):
            raise DisplayAppError("An existing Wayland or X11 desktop is required.")
        desktops = pygame.display.get_desktop_sizes()
        if not 0 <= options.display < len(desktops):
            raise DisplayAppError("The requested desktop display is unavailable.")
        size = (800, 480) if options.windowed else desktops[options.display]
        if min(size) <= 0:
            raise DisplayAppError("The desktop did not report a usable resolution.")
        surface = pygame.display.set_mode(size, display=options.display)
        pygame.display.set_caption("BMO")
        if not options.windowed:
            window = (window_factory or _desktop_window)()
            # SDL fullscreen-desktop is compositor-managed and performs no modeset.
            window.set_fullscreen(desktop=True)
        previous_cursor = pygame.mouse.get_visible()
        pygame.mouse.set_visible(False)
        renderer = PygameFace(surface, pygame_module=pygame)
        clock = pygame.time.Clock()
        keys = {
            pygame.K_1: FaceState.IDLE,
            pygame.K_2: FaceState.LISTENING,
            pygame.K_3: FaceState.THINKING,
            pygame.K_4: FaceState.SPEAKING,
            pygame.K_5: FaceState.ERROR,
        }
        output_fn("BMO display ready. ESC / Q: quit.")
        if options.dev:
            output_fn("1: Idle  2: Listening  3: Thinking  4: Speaking  5: Error")
        while True:
            dt = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return 0
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        return 0
                    if options.dev and event.key in keys:
                        renderer.set_state(keys[event.key])
            renderer.render(dt)
            pygame.display.flip()
    finally:
        try:
            if renderer is not None:
                renderer.close()
        finally:
            try:
                if previous_cursor is not None:
                    pygame.mouse.set_visible(previous_cursor)
            finally:
                pygame.display.quit()


def main(argv=None, *, pygame_module=None, window_factory=None, output_fn=print):
    parser = argparse.ArgumentParser(description="Manual BMO face display; no audio or cloud.")
    parser.add_argument("--dev", action="store_true", help="enable state keys 1 through 5")
    parser.add_argument("--windowed", action="store_true", help="use an 800x480 desktop window")
    parser.add_argument("--display", type=int, default=0, help="desktop display index (default: 0)")
    options = parser.parse_args(argv)
    if options.display < 0:
        parser.error("--display must be nonnegative")
    previous_driver = os.environ.get("SDL_VIDEODRIVER")
    driver = previous_driver or (
        "wayland" if os.environ.get("WAYLAND_DISPLAY") else
        "x11" if os.environ.get("DISPLAY") else None
    )
    if driver not in ("wayland", "x11", "dummy"):
        output_fn("An existing Wayland or X11 desktop is required; run from its terminal.")
        return 2
    # Process-local only: prevent SDL falling back to direct DRM/console access.
    os.environ["SDL_VIDEODRIVER"] = driver
    try:
        if pygame_module is None:
            import pygame as pygame_module
        return run_display(options, pygame_module, window_factory=window_factory,
                           output_fn=output_fn)
    except KeyboardInterrupt:
        return 0
    except ImportError:
        output_fn("Pygame desktop support is unavailable. Use the system Python or --windowed.")
        return 2
    except DisplayAppError as error:
        output_fn(str(error))
        return 2
    except Exception as error:
        output_fn(f"BMO display failed ({type(error).__name__}); check the desktop terminal.")
        return 1
    finally:
        if previous_driver is None:
            os.environ.pop("SDL_VIDEODRIVER", None)
        else:
            os.environ["SDL_VIDEODRIVER"] = previous_driver


if __name__ == "__main__":
    raise SystemExit(main())
