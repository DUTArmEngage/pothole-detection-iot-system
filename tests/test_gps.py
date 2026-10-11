"""Epic 1 checks: payloads, speed/heading derivation, /gps/current, stale state.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import time
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
        self.service.stop()  # the background sampler must not refill the history under the test
        time.sleep(0.2)
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


class Steady(unittest.TestCase):
    """The real location source jitters by metres while parked; the held position must not wander."""

    def feed(self, raws, steady=True):
        it = iter(raws)
        svc = GpsService(lambda: next(it), steady=steady)
        return [svc.sample_once() for _ in raws]

    def raw(self, lat, lon, acc, t):
        return {"lat": lat, "lon": lon, "accuracy_m": acc, "ts": to_iso(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=t))}

    def test_jitter_inside_accuracy_does_not_move_the_position(self):
        # ~5-10 m wobble around one spot, accuracy 12 m
        raws = [self.raw(-29.85000 + d, 31.0 + e, 12.0, i) for i, (d, e) in enumerate(
            [(0, 0), (0.00005, 0.00003), (-0.00004, 0.00006), (0.00006, -0.00004), (-0.00005, -0.00005)])]
        out = self.feed(raws)
        for f in out:  # never more than a few metres from where the parked laptop first was
            self.assertLess(abs(f.lat + 29.85) * 111320, 4)
            self.assertLess(abs(f.lon - 31.0) * 111320 * 0.87, 4)
        self.assertTrue(all(f.speed_kmh in (None, 0.0) for f in out))

    def test_without_the_filter_the_same_jitter_wanders(self):
        raws = [self.raw(-29.85 + d, 31.0, 12.0, i) for i, d in enumerate([0, 0.00005, -0.00004])]
        self.assertGreater(len({(f.lat, f.lon) for f in self.feed(raws, steady=False)}), 1)

    def test_real_movement_beyond_the_noise_is_followed(self):
        raws = [self.raw(-29.85, 31.0, 12.0, 0), self.raw(-29.85, 31.0, 12.0, 1), self.raw(-29.8502, 31.0, 12.0, 2)]  # ~22 m
        out = self.feed(raws)
        self.assertAlmostEqual(out[1].lat, -29.85, places=6)  # 0 m: still inside the noise
        self.assertEqual(out[2].lat, -29.8502)  # ~22 m: beyond the noise, so it follows
        self.assertGreater(out[2].speed_kmh, 0)

    def test_the_held_position_settles_instead_of_wandering(self):
        # 40 fixes jittering +/-8 m around a point: the held position's step shrinks as samples accumulate
        import random

        rng = random.Random(3)
        raws = [self.raw(-29.85 + rng.uniform(-7e-5, 7e-5), 31.0 + rng.uniform(-7e-5, 7e-5), 12.0, i) for i in range(40)]
        out = self.feed(raws)
        steps = [abs(out[i].lat - out[i - 1].lat) * 111320 for i in range(1, 40)]  # metres
        self.assertLess(max(steps[:3]), 8)
        self.assertLess(max(steps[-10:]), 0.5)  # under half a metre by the end
        self.assertLess(abs(out[-1].lat + 29.85) * 111320, 5)  # settles near the true centre

    def test_a_precise_fix_outweighs_a_coarse_one(self):
        raws = [self.raw(-29.85000, 31.0, 55.0, 0), self.raw(-29.85020, 31.0, 12.0, 1)]  # 22 m apart
        out = self.feed(raws)
        self.assertAlmostEqual(out[1].lat, -29.85020, delta=2e-5)  # within ~2 m of the 12 m fix


if __name__ == "__main__":
    unittest.main()
