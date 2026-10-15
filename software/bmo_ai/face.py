"""Opt-in face rendering on a caller-owned surface; never opens a device.

The caller owns the UI thread, event pump, display updates and surface lifetime.
An ordinary pygame.Surface works without pygame.init(), a display or a mixer.
Importing this module does not even import pygame. The CLI still uses NullFace.
"""
from __future__ import annotations

import math
from pathlib import Path

from .hardware import FaceState


DEFAULT_ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets" / "faces"
ANIMATION_FPS = 10
FALLBACK_COLOR = (142, 214, 186)


class PygameFace:
    """FacePort adapter, driven by render(dt_seconds) on the owner's UI thread.

    pygame_module allows device-free fakes and remains optional for cloud users.
    PNGs retain alpha without convert_alpha(), which requires a live display.
    set_state only changes local animation state; it never renders or blocks on IO.
    """

    def __init__(self, surface, *, asset_root=None, pygame_module=None):
        if pygame_module is None:
            import pygame as pygame_module

        self._pygame = pygame_module
        self._surface = surface
        self._size = surface.get_size()
        self._frames = {}
        root = Path(asset_root) if asset_root is not None else DEFAULT_ASSET_ROOT
        for state in FaceState:
            frames = []
            for path in sorted((root / state.value).glob("*.png")):
                try:
                    image = self._pygame.image.load(str(path))
                    frames.append(self._pygame.transform.smoothscale(image, self._size))
                except (OSError, self._pygame.error):
                    # Keep the legacy skip-corrupt-frame / idle-fallback policy.
                    continue
            self._frames[state] = frames
        self.missing_states = tuple(state for state in FaceState if not self._frames[state])
        if not self._frames[FaceState.IDLE]:
            fallback = self._pygame.Surface(self._size)
            fallback.fill(FALLBACK_COLOR)
            self._frames[FaceState.IDLE] = [fallback]
        for state in FaceState:
            if not self._frames[state]:
                self._frames[state] = self._frames[FaceState.IDLE]
        self.state = FaceState.IDLE
        self._frame_index = 0
        self._frame_timer = 0.0
        self._closed = False

    def set_state(self, state: FaceState) -> None:
        if not isinstance(state, FaceState):
            raise TypeError("state must be a FaceState")
        if self._closed:
            return
        # Legacy speech transitions reset the index, retaining elapsed time.
        if state != self.state:
            self.state = state
            self._frame_index = 0

    def render(self, dt_seconds: float) -> None:
        """Blit one frame; advance at most once and discard excess legacy time.

        No event polling, clock sleeping, display flipping or audio is performed.
        The target size is fixed at construction; resize requires a new adapter.
        """
        if (
            type(dt_seconds) not in (int, float)
            or not math.isfinite(dt_seconds)
            or dt_seconds < 0
        ):
            raise ValueError("dt_seconds must be finite and nonnegative")
        if self._closed:
            return
        frames = self._frames[self.state]
        self._frame_timer += dt_seconds
        if self._frame_timer >= 1.0 / ANIMATION_FPS:
            self._frame_timer = 0.0
            self._frame_index = (self._frame_index + 1) % len(frames)
        self._surface.blit(frames[self._frame_index], (0, 0))

    def close(self) -> None:
        """Release this adapter's references, leaving caller-owned pygame intact."""
        self._closed = True
        self._frames.clear()
        self._surface = None
