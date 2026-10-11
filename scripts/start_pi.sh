#!/usr/bin/env bash
# Launch the Pi services from the repo root: motion (webcam stream), the IMU server and the Dashscreen.
# Set LAPTOP_HOST to the laptop's address. Pass --fake to use a simulated IMU.
set -e
cd "$(dirname "$0")/.."
export LAPTOP_HOST="${LAPTOP_HOST:-127.0.0.1}"

IMU_ARGS=()
[ "$1" = "--fake" ] && IMU_ARGS=(--fake)

python3 -m rpi.server.main "${IMU_ARGS[@]}" &
IMU_PID=$!
if command -v motion >/dev/null; then
  motion -n -c rpi/motion.conf &
  MOTION_PID=$!
else
  echo "motion not installed (sudo apt install motion); video stream disabled" >&2
fi
trap 'kill $IMU_PID $MOTION_PID 2>/dev/null' EXIT
sleep 1
python3 -m rpi.dashscreen.main
