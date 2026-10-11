"""Start the Dashscreen: GPS polling plus the UI server.

Open http://localhost:<port>/ fullscreen on the Pi, e.g.
    chromium-browser --kiosk http://localhost:8080/
"""

import argparse

from rpi.clients.gps_client import GpsClient
from rpi.dashscreen.server import build_server
from shared import config
from shared.imu_poller import ImuPoller, http_fetcher


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=config.DASHSCREEN_PORT)
    ap.add_argument("--laptop", default=None, help="laptop base URL, default from shared.config")
    args = ap.parse_args()

    url = f"{args.laptop.rstrip('/')}/gps/current" if args.laptop else None
    gps = GpsClient(url)
    gps.start()
    imu = ImuPoller(http_fetcher(f"http://127.0.0.1:{config.PI_PORT}/imu/latest"))  # the Pi's own server
    imu.start()
    server = build_server(gps, imu, "0.0.0.0", args.port)
    print(f"[dashscreen] http://localhost:{args.port}/  polling {gps.url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        gps.stop()
        imu.stop()


if __name__ == "__main__":
    main()
