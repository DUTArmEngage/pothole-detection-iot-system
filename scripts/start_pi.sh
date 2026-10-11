#!/usr/bin/env bash
# Launch the Pi services from the repo root. Set LAPTOP_HOST to the laptop's address.
set -e
cd "$(dirname "$0")/.."
export LAPTOP_HOST="${LAPTOP_HOST:-127.0.0.1}"
exec python3 -m rpi.dashscreen.main "$@"
