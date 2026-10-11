"""Dashboard server: serves the presentation page, its state as JSON, and the annotated video."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional

from main_server.clients.pi_client import PiClient
from main_server.image_processing import DetectionPipeline
from shared import config
from shared.schemas import GpsFix, age_seconds

HERE = os.path.dirname(__file__)
PAGE = os.path.join(HERE, "index.html")

CRLF = bytes([13, 10])
WEB = os.path.join(HERE, "..", "..", "shared", "web")
SHARED_JS = ("imu_chart.js", "map_view.js")


def gps_state(history: Callable[[], List[GpsFix]]) -> dict:
    """Position, trail and staleness for the map and HUD."""
    fixes = history()
    fix = fixes[-1] if fixes else None
    age = age_seconds(fix.ts) if fix else None
    return {
        "fix": fix.to_dict() if fix else None,
        "trail": [[f.lat, f.lon] for f in fixes],
        "age_s": age,
        "stale": fix is None or age > config.STALE_AFTER_S,
    }


def make_handler(pi: PiClient, pipeline: Optional[DetectionPipeline] = None, gps_history: Optional[Callable[[], List[GpsFix]]] = None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._file(PAGE, "text/html; charset=utf-8")
            if self.path.lstrip("/") in SHARED_JS:
                return self._file(os.path.join(WEB, self.path.lstrip("/")), "application/javascript")
            if self.path == "/api/state":
                body = {"imu": pi.imu.snapshot(), "fake_imu": pi.fake, "video": pipeline.state() if pipeline else None,
                        "gps": gps_state(gps_history) if gps_history else None}
                return self._send(200, json.dumps(body).encode(), "application/json")
            if self.path == "/video.mjpg" and pipeline:
                return self._stream_video()
            self._send(404, b"not found", "text/plain")

        def _stream_video(self):
            """multipart/x-mixed-replace: the browser swaps the <img> for each new JPEG."""
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq = -1
            try:
                while True:
                    jpeg, seq = pipeline.latest_jpeg(seq, timeout=1.0)
                    if jpeg is None:
                        continue
                    head = b"--frame" + CRLF + b"Content-Type: image/jpeg" + CRLF
                    head += b"Content-Length: %d" % len(jpeg) + CRLF + CRLF
                    self.wfile.write(head + jpeg + CRLF)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # the viewer closed the page

        def _file(self, path: str, ctype: str):
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)

        def _send(self, code: int, data: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return Handler


def build_server(pi: PiClient, host: str, port: int, pipeline: Optional[DetectionPipeline] = None,
                 gps_history: Optional[Callable[[], List[GpsFix]]] = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(pi, pipeline, gps_history))
    server.daemon_threads = True  # open video streams must not block shutdown
    return server
