#!/bin/sh
# Create a separate BMO Python environment that can see distro Picamera2/libcamera.
# NO sudo, no camera open, no OS package modification; only new local venv.
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
target="$project/.venv-camera"
if [ -e "$target" ] || [ -L "$target" ]; then
  printf '%s\n' "CAMERA_ENV_EXISTS: $target (not replaced)"
  exit 2
fi
if [ "${1:-}" = "--dry-run" ]; then
  printf '%s\n' "python3 -m venv --system-site-packages $target"
  printf '%s\n' "$target/bin/python -m pip install -r $project/software/requirements-lock.txt"
  exit 0
fi
python3 -m venv --system-site-packages "$target"
"$target/bin/python" -m pip install -r "$project/software/requirements-lock.txt"
cd "$project"
PYTHONPATH=software "$target/bin/python" -B -c \
  'from bmo_ai.features.camera.live import probe_camera_capability; print(probe_camera_capability())'
printf '%s\n' "CAMERA_ENV_READY (module discovery only, physical sensor not opened)"
