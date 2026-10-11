"""Run the Dashboard on its own (IMU panel only for now): python -m main_server.dashboard.main --fake-imu"""

import argparse

from main_server.clients.pi_client import PiClient
from main_server.dashboard.server import build_server
from shared import config


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake-imu", action="store_true", help="simulated IMU, Pi not needed")
    ap.add_argument("--pi", default=None, help="Pi base URL, default from shared.config")
    ap.add_argument("--port", type=int, default=config.DASHBOARD_PORT)
    args = ap.parse_args()

    pi = PiClient(args.pi, fake=args.fake_imu)
    pi.start()
    server = build_server(pi, "0.0.0.0", args.port)
    print(f"[dashboard] http://localhost:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pi.stop()


if __name__ == "__main__":
    main()
