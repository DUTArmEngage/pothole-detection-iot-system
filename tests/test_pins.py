"""Pins: confidence gate, saving, reload across runs, GeoJSON export and Dashboard endpoints.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np

from main_server.clients.pi_client import PiClient
from main_server.dashboard.server import build_server
from main_server.image_processing import DetectionPipeline, EventLog, Frame
from main_server.pinstore import PinStore
from ref.edge.detector import Detection as Raw
from ref.edge.detector import InferenceResult
from shared.schemas import Detection, GpsFix, to_iso

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def det(t, conf, severity="medium", lat=-29.85, lon=31.0):
    return Detection(ts=to_iso(T0 + timedelta(seconds=t)), bbox=[0, 0, 1, 1], severity=severity, confidence=conf, lat=lat, lon=lon)


class Gate(unittest.TestCase):
    def test_only_confident_detections_become_events(self):
        log = EventLog(min_confidence=0.80)
        self.assertIsNone(log.add(det(0, 0.79)))
        self.assertIsNotNone(log.add(det(10, 0.80)))  # the threshold itself qualifies
        self.assertIsNotNone(log.add(det(20, 0.95)))
        self.assertEqual(len(log.list()), 2)

    def test_low_confidence_frame_does_not_extend_or_create_an_event(self):
        log = EventLog(min_confidence=0.80)
        self.assertEqual(log.add(det(0, 0.9))[1], True)
        self.assertIsNone(log.add(det(0.5, 0.6)))
        self.assertEqual(len(log.list()), 1)

    def test_merge_reports_not_new_and_whether_better(self):
        log = EventLog(min_confidence=0.80)
        e, new = log.add(det(0, 0.85))
        self.assertTrue(new)
        e, new = log.add(det(0.5, 0.95, "high"))
        self.assertFalse(new)
        self.assertEqual((e["severity"], e["confidence"], e["_better"]), ("high", 0.95, True))


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "p.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_pins_survive_a_restart_and_are_marked_current_only_for_this_run(self):
        a = PinStore(self.db)
        pid = a.add("2026-01-01T00:00:00.000Z", -29.85, 31.0, "high", 0.91, 16.0, b"\xff\xd8jpeg")
        a.close()
        time.sleep(0.01)
        b = PinStore(self.db)  # a later run
        b.add("2026-01-02T00:00:00.000Z", -29.86, 31.01, "low", 0.82)
        pins = b.all()
        self.assertEqual([p["current"] for p in pins], [False, True])
        self.assertEqual(pins[0]["image"], f"{pid}.jpg")
        self.assertTrue(b.image_path(pid))
        self.assertIsNone(b.image_path(999))
        b.close()

    def test_update_keeps_the_best_and_geojson_is_lon_lat(self):
        s = PinStore(self.db)
        pid = s.add("2026-01-01T00:00:00.000Z", -29.85, 31.0, "low", 0.8)
        s.update(pid, "high", 0.93)
        gj = s.geojson()
        f = gj["features"][0]
        self.assertEqual(f["geometry"]["coordinates"], [31.0, -29.85])
        self.assertEqual((f["properties"]["severity"], f["properties"]["confidence"]), ("high", 0.93))
        s.close()


class StubFrames:
    connected, error, frames_read = True, None, 0


class FakeDetector:
    def __init__(self, conf):
        self.conf = conf

    def infer(self, frame):
        return InferenceResult([Raw(100, 100, 260, 220, self.conf, 0, "pothole")], 1.0, (640, 360))


class PipelineSaving(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = PinStore(os.path.join(self.dir, "p.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_frames(self, conf, n=3):
        now = time.time()
        fix = GpsFix(-29.85, 31.0, to_iso(datetime.fromtimestamp(now, timezone.utc)), accuracy_m=16.0)
        p = DetectionPipeline(StubFrames(), FakeDetector(conf), None, lambda: [fix], store=self.store, pin_confidence=0.80)
        for i in range(n):
            f = Frame(np.zeros((360, 640, 3), dtype=np.uint8), now + i * 0.2)
            dets, img = p.process(f)
            p._publish(dets, img)
        return p

    def test_confident_pothole_is_saved_once_with_snapshot_and_laptop_position(self):
        p = self.run_frames(0.9)
        pins = self.store.all()
        self.assertEqual(len(pins), 1)  # three frames of one pothole merge into one pin
        self.assertEqual((pins[0]["lat"], pins[0]["lon"], pins[0]["accuracy_m"]), (-29.85, 31.0, 16.0))
        self.assertTrue(self.store.image_path(pins[0]["id"]))
        self.assertEqual(p.state()["events"][0]["id"], pins[0]["id"])
        self.assertTrue(p.state()["saving"])

    def test_unconfident_pothole_is_not_pinned_or_saved(self):
        p = self.run_frames(0.70)
        self.assertEqual(self.store.all(), [])
        self.assertEqual(p.state()["events"], [])

    def test_pipeline_without_a_store_still_pins_but_does_not_save(self):
        now = time.time()
        fix = GpsFix(-29.85, 31.0, to_iso(datetime.fromtimestamp(now, timezone.utc)))
        p = DetectionPipeline(StubFrames(), FakeDetector(0.9), None, lambda: [fix], store=None, pin_confidence=0.80)
        dets, img = p.process(Frame(np.zeros((360, 640, 3), dtype=np.uint8), now))
        p._publish(dets, img)
        self.assertEqual(len(p.state()["events"]), 1)
        self.assertFalse(p.state()["saving"])


class Endpoints(unittest.TestCase):
    def test_pins_geojson_and_snapshot_endpoints(self):
        d = tempfile.mkdtemp()
        store = PinStore(os.path.join(d, "p.db"))
        pid = store.add("2026-01-01T00:00:00.000Z", -29.85, 31.0, "high", 0.9, 16.0, b"\xff\xd8x\xff\xd9")
        pipe = DetectionPipeline(StubFrames(), FakeDetector(0.9), None, None, store=store)
        pi = PiClient(enabled=False)
        server = build_server(pi, "127.0.0.1", 0, pipe, None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            pins = json.load(urllib.request.urlopen(base + "/api/pins"))
            self.assertEqual(pins[0]["id"], pid)
            with urllib.request.urlopen(base + "/api/pins.geojson") as r:
                self.assertIn("attachment", r.headers["Content-Disposition"])
                self.assertEqual(json.load(r)["features"][0]["geometry"]["coordinates"], [31.0, -29.85])
            self.assertEqual(urllib.request.urlopen(f"{base}/pins/{pid}.jpg").read(), b"\xff\xd8x\xff\xd9")
            for bad in ("/pins/999.jpg", "/pins/../p.db", "/vendor/../../README.md"):
                with self.assertRaises(urllib.error.HTTPError, msg=bad):
                    urllib.request.urlopen(base + bad)
        finally:
            server.shutdown()
            server.server_close()
            store.close()
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
