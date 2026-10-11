"""Dashboard server: serves the presentation page and its state as JSON."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from main_server.clients.pi_client import PiClient

HERE = os.path.dirname(__file__)
PAGE = os.path.join(HERE, "index.html")
CHART_JS = os.path.join(HERE, "..", "..", "shared", "web", "imu_chart.js")


def make_handler(pi: PiClient):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._file(PAGE, "text/html; charset=utf-8")
            if self.path == "/imu_chart.js":
                return self._file(CHART_JS, "application/javascript")
            if self.path == "/api/state":
                body = json.dumps({"imu": pi.imu.snapshot(), "fake_imu": pi.fake}).encode()
                return self._send(200, body, "application/json")
            self._send(404, b"not found", "text/plain")

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


def build_server(pi: PiClient, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(pi))
