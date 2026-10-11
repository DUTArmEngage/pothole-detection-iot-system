"""Laptop HTTP server. `python -m main_server.main --fake` serves a replayed route."""

import argparse

import threading

from main_server.clients.pi_client import PiClient
from main_server.dashboard.server import build_server as build_dashboard
from main_server.detection import add_detection_args, make_pipeline
from main_server.gps import GpsService, serve
from shared import config
from tests.fake_gps import FakeRoute


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="replay a fake route instead of the OS location source")
    ap.add_argument("--speed-kmh", type=float, default=30.0, help="fake route speed")
    ap.add_argument("--fake-imu", action="store_true", help="simulated IMU instead of polling the Pi")
    ap.add_argument("--pi", default=None, help="Pi base URL, default from shared.config")
    add_detection_args(ap)
    ap.add_argument("--port", type=int, default=config.LAPTOP_PORT)
    ap.add_argument("--open", action="store_true", help="open the Dashboard in the default browser")
    ap.add_argument("--dashboard-port", type=int, default=config.DASHBOARD_PORT)
    args = ap.parse_args()

    source = None
    if args.fake:
        read = FakeRoute(speed_kmh=args.speed_kmh).read
    else:
        from main_server.location import WindowsLocationSource

        source = WindowsLocationSource()
        read = source.read
    service = GpsService(read, history=900, steady=not args.fake)
    server = serve(service, port=args.port)
    pi = PiClient(args.pi, fake=args.fake_imu, enabled=not args.no_imu)
    pi.start()
    pipeline = make_pipeline(args, pi, service.history)
    dashboard = build_dashboard(pi, "0.0.0.0", args.dashboard_port, pipeline, service.history)
    threading.Thread(target=dashboard.serve_forever, daemon=True).start()
    print(f"[laptop] Dashboard http://localhost:{args.dashboard_port}/ ({'fake IMU' if args.fake_imu else 'Pi IMU'})")
    print(f"[laptop] GET http://0.0.0.0:{args.port}/gps/current ({'fake route' if args.fake else 'OS location'})")
    if args.open:
        import webbrowser

        webbrowser.open(f"http://localhost:{args.dashboard_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service.stop()
        pi.stop()
    finally:
        if source:
            source.close()


if __name__ == "__main__":
    main()
