"""Epic 1 checks: payloads, speed/heading derivation, /gps/current, stale state.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone

from main_server.gps import GpsService, derive_motion, serve
from rpi.clients.gps_client import GpsClient
from shared import config
from shared.schemas import GpsFix, to_iso
from tests.fake_gps import FakeRoute


def fix_at(lat, lon, t):
    return GpsFix(lat=lat, lon=lon, ts=to_iso(t))


class DeriveMotion(unittest.TestCase):
    def test_due_east_one_second(self):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        speed, heading = derive_motion(fix_at(0, 0, t), fix_at(0, 0.0001, t + timedelta(seconds=1)))
        self.assertAlmostEqual(heading, 90, delta=0.5)
        self.assertAlmostEqual(speed, 40.0, delta=0.5)  # ~11.1 m in 1 s

    def test_stationary_has_no_heading(self):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        speed, heading = derive_motion(fix_at(1, 1, t), fix_at(1, 1, t + timedelta(seconds=1)))
        self.assertEqual(speed, 0.0)
        self.assertIsNone(heading)

    def test_non_increasing_time(self):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(derive_motion(fix_at(0, 0, t), fix_at(0, 1, t)), (None, None))


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.service = GpsService(FakeRoute(speed_kmh=36).read)
        self.server = serve(self.service, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        import threading
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.port}/gps/current"

    def tearDown(self):
        self.service.stop()
        self.server.shutdown()

    def test_503_before_first_fix_then_valid_json(self):
        self.service._history.clear()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url)
        self.assertEqual(cm.exception.code, 503)
        self.service.sample_once()
        data = json.load(urllib.request.urlopen(self.url))
        fix = GpsFix.from_dict(data)
        self.assertTrue(-90 <= fix.lat <= 90 and -180 <= fix.lon <= 180)

    def test_client_fresh_then_stale(self):
        self.service.sample_once()
        client = GpsClient(self.url)
        client.poll_once()
        self.assertFalse(client.snapshot()["stale"])
        self.service.stop()
        self.server.shutdown()
        # an old fix, as when the laptop stops updating
        old = datetime.now(timezone.utc) - timedelta(seconds=config.STALE_AFTER_S + 1)
        client._latest = fix_at(0, 0, old)
        snap = client.snapshot()
        self.assertTrue(snap["stale"])
        self.assertGreater(snap["age_s"], config.STALE_AFTER_S)

    def test_unreachable_laptop_is_stale(self):
        client = GpsClient("http://127.0.0.1:1/gps/current")
        client.poll_once()
        snap = client.snapshot()
        self.assertTrue(snap["stale"])
        self.assertIsNotNone(snap["error"])


if __name__ == "__main__":
    unittest.main()
