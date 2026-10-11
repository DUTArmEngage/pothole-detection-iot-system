"""Epic 4 checks: map events, Dashboard state and shared assets.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone

from main_server.clients.pi_client import PiClient
from main_server.dashboard.server import build_server, gps_state
from main_server.gps import GpsService
from main_server.image_processing import EventLog
from shared import config
from shared.schemas import Detection, GpsFix, to_iso
from tests.fake_gps import FakeRoute


def det(t, severity="low", conf=0.5, lat=-29.85, lon=31.0):
    return Detection(ts=to_iso(t), bbox=[0, 0, 1, 1], severity=severity, confidence=conf, lat=lat, lon=lon)


class Events(unittest.TestCase):
    def test_frames_of_one_pothole_merge_and_keep_worst_severity(self):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        log = EventLog()
        for i, sev in enumerate(["low", "high", "medium"]):
            log.add(det(t + timedelta(seconds=i * 0.5), sev, 0.5 + i / 10))
        events = log.list()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["severity"], "high")
        self.assertAlmostEqual(events[0]["confidence"], 0.7)

    def test_a_later_pothole_is_a_new_event(self):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        log = EventLog()
        log.add(det(t))
        log.add(det(t + timedelta(seconds=config.STALE_AFTER_S + 5)))
        self.assertEqual(len(log.list()), 2)

    def test_detections_without_position_are_not_plotted(self):
        log = EventLog()
        log.add(det(datetime(2026, 1, 1, tzinfo=timezone.utc), lat=None, lon=None))
        self.assertEqual(log.list(), [])

    def test_internal_fields_are_not_exposed(self):
        log = EventLog()
        log.add(det(datetime(2026, 1, 1, tzinfo=timezone.utc)))
        self.assertNotIn("_t", log.list()[0])


class State(unittest.TestCase):
    def test_gps_state_fresh_stale_and_empty(self):
        self.assertTrue(gps_state(lambda: [])["stale"])
        now = datetime.now(timezone.utc)
        fresh = [GpsFix(1, 2, to_iso(now), speed_kmh=30, heading=90)]
        s = gps_state(lambda: fresh)
        self.assertFalse(s["stale"])
        self.assertEqual(s["trail"], [[1, 2]])
        old = [GpsFix(1, 2, to_iso(now - timedelta(seconds=config.STALE_AFTER_S + 1)))]
        self.assertTrue(gps_state(lambda: old)["stale"])


class Server(unittest.TestCase):
    def test_serves_page_assets_and_combined_state_without_detection(self):
        gps = GpsService(FakeRoute().read)
        gps.sample_once()
        pi = PiClient(fake=True)
        pi.imu.poll_once()
        server = build_server(pi, "127.0.0.1", 0, None, gps.history)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            for path, needle in [("/", b"Pothole Dashboard"), ("/map_view.js", b"drawMap"), ("/imu_chart.js", b"drawImu")]:
                with urllib.request.urlopen(base + path) as r:
                    self.assertIn(needle, r.read(), path)
            with urllib.request.urlopen(base + "/api/state") as r:
                st = json.load(r)
            self.assertFalse(st["gps"]["stale"])
            self.assertIsNone(st["video"])
            self.assertTrue(st["imu"]["samples"])
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/../../etc/passwd")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
