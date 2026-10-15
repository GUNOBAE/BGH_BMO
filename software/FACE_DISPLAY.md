# Manual BMO face display

Run from a terminal in an existing Raspberry Pi desktop session:

```bash
cd software
PYGAME_HIDE_SUPPORT_PROMPT=1 /usr/bin/python3 -B -m bmo_ai.display_app --dev
```

Use an existing system Python with Pygame 2.6.1 and SDL2 desktop support. The
cloud virtual environment does not include Pygame. This app does not require API
credentials, audio, a controller or a system service.

Keys 1–5 select Idle, Listening, Thinking, Speaking and Error. ESC, Q, window close
and terminal Ctrl-C exit. Omit `--dev` to keep Idle, or add `--windowed` for an
800×480 window. Fullscreen uses the current desktop resolution without changing
the monitor mode. The renderer preserves the original filename order and 10fps
animation; the event loop is capped at 60Hz.

The initial GitHub sync is **code only**. The original 13 face PNGs and their
provenance remain local pending confirmation of their separate license scope.
Without these assets the renderer shows its existing solid-color fallback; a
fresh public checkout cannot reproduce the original face artwork yet. Keep the
upstream code notice with the renderer; it does not establish a PNG license.

The default mock suite does not require the PNGs:

```bash
cd software
.venv/bin/python -B -m unittest discover -s tests -v
```

The optional `checks/check_face_offscreen.py` and
`checks/check_display_offscreen.py` require the original PNGs and provenance in
`software/assets/faces/`, plus an existing Pygame installation. The latter uses
SDL's dummy driver. These checks do not prove physical fullscreen output. No real
screen launch, audio or controller test was performed during implementation.
