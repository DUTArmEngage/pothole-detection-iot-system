"""Dashscreen HTTP server: serves the 7" UI page and its state as JSON."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rpi.clients.gps_client import GpsClient

PAGE = os.path.join(os.path.dirname(__file__), "index.html")


def make_handler(gps: GpsClient):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, json.dumps({"gps": gps.snapshot()}).encode(), "application/json")
            self._send(404, b"not found", "text/plain")

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


def build_server(gps: GpsClient, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(gps))
