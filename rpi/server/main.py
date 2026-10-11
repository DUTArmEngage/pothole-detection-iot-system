"""Pi HTTP server: GET /imu/latest returns the last ~1 s of timestamped IMU samples.

    python3 -m rpi.server.main            # real MPU-6050
    python3 -m rpi.server.main --fake     # simulated samples, no hardware
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rpi.server.imu import ImuReader
from shared import config


def make_handler(reader: ImuReader):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/imu/latest":
                return self._send(404, {"error": "not found"})
            self._send(200, {"window_s": config.IMU_WINDOW_S, "samples": [s.to_dict() for s in reader.window()]})

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


def build_server(reader: ImuReader, host: str = "0.0.0.0", port: int = config.PI_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(reader))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="simulated IMU instead of the MPU-6050")
    ap.add_argument("--bus", type=int, default=3, help="I2C bus (3 = software bus on GPIO23/24)")
    ap.add_argument("--port", type=int, default=config.PI_PORT)
    args = ap.parse_args()

    if args.fake:
        from tests.fake_imu import FakeImu

        source = FakeImu().read
    else:
        from rpi.server.imu import Mpu6050Source

        source = Mpu6050Source(bus=args.bus)
    reader = ImuReader(source)
    reader.start()
    server = build_server(reader, port=args.port)
    print(f"[pi] GET http://0.0.0.0:{args.port}/imu/latest ({'fake' if args.fake else 'MPU-6050'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        reader.stop()


if __name__ == "__main__":
    main()
