"""Hosts, ports, poll intervals and thresholds shared by the Pi and the laptop."""

import os

# Override per device with environment variables. On a dedicated link, set
# LAPTOP_HOST on the Pi and PI_HOST on the laptop to the other device's address.
LAPTOP_HOST = os.environ.get("LAPTOP_HOST", "127.0.0.1")
LAPTOP_PORT = int(os.environ.get("LAPTOP_PORT", "8000"))
PI_HOST = os.environ.get("PI_HOST", "127.0.0.1")
PI_PORT = int(os.environ.get("PI_PORT", "8001"))
DASHSCREEN_PORT = int(os.environ.get("DASHSCREEN_PORT", "8080"))
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8090"))

GPS_POLL_S = 1.0
IMU_POLL_S = 0.1
IMU_WINDOW_S = 1.0
STALE_AFTER_S = 3.0
HTTP_TIMEOUT_S = 1.0
TRAIL_MAX_POINTS = 300
