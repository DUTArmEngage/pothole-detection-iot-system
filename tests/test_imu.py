"""Epic 2 checks: IMU window, /imu/latest, poller de-dup and gap detection, fake mode.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone

from main_server.clients.pi_client import PiClient
from rpi.server.imu import ImuReader
from rpi.server.main import build_server
from shared import config
from shared.imu_poller import ImuPoller, http_fetcher
from shared.schemas import ImuSample, to_iso
from tests.fake_imu import FakeImu


def sample_at(base, offset_s, az=1.0):
    return ImuSample(ts=to_iso(base + timedelta(seconds=offset_s)), ax=0, ay=0, az=az, gx=0, gy=0, gz=0)


class Window(unittest.TestCase):
    def test_window_is_about_one_second_oldest_first(self):
        base = datetime.now(timezone.utc)
        samples = [sample_at(base, i * 0.01) for i in range(250)]  # 2.5 s at 100 Hz
        it = iter(samples)
        reader = ImuReader(lambda: next(it))
        for _ in samples:
            reader.sample_once()
        w = reader.window()
        span = (datetime.fromisoformat(w[-1].ts.replace("Z", "+00:00")) - datetime.fromisoformat(w[0].ts.replace("Z", "+00:00"))).total_seconds()
        self.assertAlmostEqual(span, config.IMU_WINDOW_S, delta=0.02)
        self.assertEqual([s.ts for s in w], sorted(s.ts for s in w))

    def test_empty_before_first_sample(self):
        self.assertEqual(ImuReader(FakeImu().read).window(), [])


class Poller(unittest.TestCase):
    def test_overlapping_windows_are_deduplicated(self):
        base = datetime.now(timezone.utc)
        stream = [sample_at(base, i * 0.01) for i in range(300)]
        # 1 s windows (100 samples) advancing 10 samples per poll => heavy overlap
        pos = {"n": 100}

        def fetch():
            n = pos["n"]
            pos["n"] += 10
            return [s.to_dict() for s in stream[n - 100 : n]]

        p = ImuPoller(fetch)
        for _ in range(10):
            p.poll_once()
        self.assertEqual(len(p.snapshot(seconds=60)["samples"]), 190)  # 100 + 9*10, no repeats
        self.assertEqual(p.gaps, 0)

    def test_gap_is_counted_when_window_starts_after_newest(self):
        base = datetime.now(timezone.utc)
        windows = [
            [sample_at(base, i * 0.01).to_dict() for i in range(100)],
            [sample_at(base, 5 + i * 0.01).to_dict() for i in range(100)],  # 5 s later: samples missed
        ]
        p = ImuPoller(lambda: windows.pop(0))
        p.poll_once()
        p.poll_once()
        self.assertEqual(p.gaps, 1)

    def test_impact_peak_survives_100ms_polling(self):
        """A 0.2 s impact must reach the poller even though polls are 100 ms apart."""
        imu = FakeImu(impact_every_s=100.0, impact_len_s=0.2, impact_g=1.5)  # one impact at t=0..0.2
        base = datetime.now(timezone.utc)
        t0 = time.monotonic()
        reader = ImuReader(lambda: imu.sample_at(time.monotonic() - t0, datetime.now(timezone.utc)))
        server = build_server(reader, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        reader.start()
        try:
            poller = ImuPoller(http_fetcher(f"http://127.0.0.1:{server.server_address[1]}/imu/latest"))
            end = time.time() + 1.0
            while time.time() < end:
                poller.poll_once()
                time.sleep(config.IMU_POLL_S)
            peak = max(abs(s["az"] - 1) for s in poller.snapshot(seconds=60)["samples"])
            self.assertGreater(peak, 1.0)
            self.assertEqual(poller.gaps, 0)
        finally:
            reader.stop()
            server.shutdown()
            server.server_close()


class Endpoint(unittest.TestCase):
    def test_imu_latest_json_shape(self):
        reader = ImuReader(FakeImu().read)
        for _ in range(20):
            reader.sample_once()
        server = build_server(reader, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/imu/latest") as r:
                body = json.load(r)
            self.assertEqual(body["window_s"], config.IMU_WINDOW_S)
            self.assertEqual(len(body["samples"]), 20)
            self.assertEqual(set(body["samples"][0]), {"ts", "ax", "ay", "az", "gx", "gy", "gz"})
            for d in body["samples"]:
                ImuSample.from_dict(d)
        finally:
            server.shutdown()
            server.server_close()


class FakeMode(unittest.TestCase):
    def test_laptop_runs_without_the_pi(self):
        pi = PiClient(fake=True)  # no Pi anywhere
        pi.imu.poll_once()
        snap = pi.imu.snapshot()
        self.assertGreater(len(snap["samples"]), 50)
        self.assertFalse(snap["stale"])
        self.assertIsNone(snap["error"])

    def test_unreachable_pi_reports_stale_and_error(self):
        pi = PiClient("http://127.0.0.1:1")
        pi.imu.poll_once()
        snap = pi.imu.snapshot()
        self.assertTrue(snap["stale"])
        self.assertIsNotNone(snap["error"])


if __name__ == "__main__":
    unittest.main()
