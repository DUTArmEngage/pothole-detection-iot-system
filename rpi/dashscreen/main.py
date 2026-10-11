"""Start the Dashscreen: GPS polling plus the UI server.

Open http://localhost:<port>/ fullscreen on the Pi, e.g.
    chromium-browser --kiosk http://localhost:8080/
"""

import argparse

from rpi.clients.gps_client import GpsClient
from rpi.dashscreen.server import build_server
from shared import config


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=config.DASHSCREEN_PORT)
    ap.add_argument("--laptop", default=None, help="laptop base URL, default from shared.config")
    args = ap.parse_args()

    url = f"{args.laptop.rstrip('/')}/gps/current" if args.laptop else None
    gps = GpsClient(url)
    gps.start()
    server = build_server(gps, "0.0.0.0", args.port)
    print(f"[dashscreen] http://localhost:{args.port}/  polling {gps.url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        gps.stop()


if __name__ == "__main__":
    main()
