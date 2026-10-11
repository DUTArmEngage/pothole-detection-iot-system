"""Laptop GPS service: serves GET /gps/current.

A fix source (fake route now, the OS location API later) is sampled once per
GPS poll interval. Speed and heading are derived here from successive fixes.
A short history is retained for matching detections to positions (epic 3).
"""

from __future__ import annotations

import json
import math
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Deque, List, Optional, Tuple

from shared import config
from shared.schemas import GpsFix, parse_ts

FixSource = Callable[[], dict]  # returns {"lat", "lon", "ts", "accuracy_m"?}


def derive_motion(prev: GpsFix, cur: GpsFix) -> Tuple[Optional[float], Optional[float]]:
    """Speed (km/h) and heading (deg clockwise from north) between two fixes."""
    dt = (parse_ts(cur.ts) - parse_ts(prev.ts)).total_seconds()
    if dt <= 0:
        return None, None
    p1, p2 = math.radians(prev.lat), math.radians(cur.lat)
    dl = math.radians(cur.lon - prev.lon)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist = 2 * 6371000.0 * math.asin(math.sqrt(a))
    heading = None
    if dist > 0.5:  # below half a metre the bearing is noise
        y = math.sin(dl) * math.cos(p2)
        x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
        heading = (math.degrees(math.atan2(y, x)) + 360) % 360
    return dist / dt * 3.6, heading


class GpsService:
    def __init__(self, source: FixSource, history: int = 120):
        self._source = source
        self._history: Deque[GpsFix] = deque(maxlen=history)
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def sample_once(self) -> GpsFix:
        fix = GpsFix.from_dict(self._source())
        with self._lock:
            if self._history:
                prev = self._history[-1]
                fix.speed_kmh, fix.heading = derive_motion(prev, fix)
                if fix.heading is None:  # stationary: keep the last known heading
                    fix.heading = prev.heading
            self._history.append(fix)
        return fix

    def current(self) -> Optional[GpsFix]:
        with self._lock:
            return self._history[-1] if self._history else None

    def history(self) -> List[GpsFix]:
        with self._lock:
            return list(self._history)

    def run(self) -> None:
        last_error = None
        while not self._stop.is_set():
            try:
                self.sample_once()
                last_error = None
            except Exception as exc:  # a failed read must not kill the service
                if str(exc) != last_error:  # print each distinct error once
                    print(f"[gps] source error: {exc}")
                last_error = str(exc)
            self._stop.wait(config.GPS_POLL_S)

    def stop(self) -> None:
        self._stop.set()


def make_handler(service: GpsService):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/gps/current":
                return self._send(404, {"error": "not found"})
            fix = service.current()
            if fix is None:
                return self._send(503, {"error": "no fix yet"})
            self._send(200, fix.to_dict())

        def _send(self, code: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


def serve(service: GpsService, host: str = "0.0.0.0", port: int = config.LAPTOP_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    threading.Thread(target=service.run, daemon=True).start()
    return server
