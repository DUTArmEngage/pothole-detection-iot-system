"""Laptop HTTP server. `python -m main_server.main --fake` serves a replayed route."""

import argparse

from main_server.gps import GpsService, serve
from shared import config
from tests.fake_gps import FakeRoute


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="replay a fake route instead of the OS location source")
    ap.add_argument("--speed-kmh", type=float, default=30.0, help="fake route speed")
    ap.add_argument("--port", type=int, default=config.LAPTOP_PORT)
    args = ap.parse_args()

    if not args.fake:
        raise SystemExit("Real OS location source is not implemented yet; run with --fake.")
    service = GpsService(FakeRoute(speed_kmh=args.speed_kmh).read)
    server = serve(service, port=args.port)
    print(f"[laptop] GET http://0.0.0.0:{args.port}/gps/current (fake route)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service.stop()


if __name__ == "__main__":
    main()
