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
MOTION_PORT = int(os.environ.get("MOTION_PORT", "8081"))

GPS_POLL_S = 1.0
IMU_POLL_S = 0.1
IMU_WINDOW_S = 1.0
STALE_AFTER_S = 3.0
HTTP_TIMEOUT_S = 1.0
TRAIL_MAX_POINTS = 300

# Video / detection
VIDEO_LATENCY_S = float(os.environ.get("VIDEO_LATENCY_S", "0.0"))  # capture+network delay subtracted from frame arrival time
IMPACT_WINDOW_S = 0.3   # IMU samples within +/- this of the frame time feed severity
DETECT_IOU = 0.5        # overlapping boxes above this IoU are merged
DETECT_CONF = 0.35
GPS_MATCH_S = 2.0       # max |frame ts - GPS fix ts| for a detection to get lat/lon

# Pins: detections at or above this confidence are pinned on the map and saved for later use.
# 0.80 by default; override with PIN_CONFIDENCE or --pin-confidence.
PIN_CONFIDENCE = float(os.environ.get("PIN_CONFIDENCE", "0.80"))
PINS_DB = os.environ.get("PINS_DB", "data/potholes.db")

# Steadiness filter for the laptop's OS location: fixes closer than this (or than the reported accuracy)
# to the held position are treated as noise, so a stationary laptop does not wander on the map.
STEADY_MIN_RADIUS_M = float(os.environ.get("STEADY_MIN_RADIUS_M", "15"))
