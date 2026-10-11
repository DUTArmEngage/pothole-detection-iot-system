"""Epic 3 checks: frame time, de-duplication, impact-based severity, GPS match, annotated video.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import os
import time
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from main_server.clients.pi_client import PiClient
from main_server.image_processing import (
    DetectionPipeline, Frame, FrameSource, annotate, dedupe, iou, nearest_fix, peak_impact,
)
from ref.edge.detector import Detection as Raw
from ref.edge.detector import InferenceResult
from shared import config
from shared.imu_poller import ImuPoller
from shared.schemas import Detection, GpsFix, ImuSample, to_iso

VIDEO = os.path.join(os.path.dirname(__file__), "..", "ref", "vid_potholes.mp4")
MODEL = os.path.join(os.path.dirname(__file__), "..", "ref", "models", "pothole.pt")


def raw(x1, y1, x2, y2, conf):
    return Raw(x1, y1, x2, y2, conf, 0, "pothole")


class FakeDetector:
    def __init__(self, dets):
        self.dets = dets

    def infer(self, frame):
        return InferenceResult(list(self.dets), 1.0, (frame.shape[1], frame.shape[0]))


def imu_poller_with(epoch, azs):
    """Poller holding samples at 10 ms spacing from `epoch` with the given az values."""
    p = ImuPoller(lambda: [])
    base = datetime.fromtimestamp(epoch, timezone.utc)
    p._history.extend(
        ImuSample(ts=to_iso(base + timedelta(seconds=i * 0.01)), ax=0, ay=0, az=az, gx=0, gy=0, gz=0)
        for i, az in enumerate(azs)
    )
    from shared.schemas import parse_ts
    p._newest = parse_ts(p._history[-1].ts)
    return p


class Overlap(unittest.TestCase):
    def test_iou(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertEqual(iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)
        self.assertAlmostEqual(iou((0, 0, 10, 10), (5, 0, 15, 10)), 1 / 3, places=3)

    def test_dedupe_keeps_most_confident_of_overlapping_pair(self):
        out = dedupe([raw(0, 0, 100, 100, 0.5), raw(5, 5, 100, 100, 0.9), raw(300, 300, 400, 400, 0.4)])
        self.assertEqual(sorted(round(d.confidence, 1) for d in out), [0.4, 0.9])

    def test_dedupe_keeps_touching_boxes(self):
        self.assertEqual(len(dedupe([raw(0, 0, 100, 100, 0.8), raw(100, 0, 200, 100, 0.7)])), 2)


class Impact(unittest.TestCase):
    def test_peak_is_vertical_deviation(self):
        s = [ImuSample("2026-01-01T00:00:00.000Z", 0, 0, az, 0, 0, 0) for az in (1.0, 2.2, 0.9)]
        self.assertAlmostEqual(peak_impact(s), 1.2)
        self.assertIsNone(peak_impact([]))


class GpsMatch(unittest.TestCase):
    def test_nearest_within_gap_only(self):
        now = time.time()
        fixes = [GpsFix(1, 2, to_iso(datetime.fromtimestamp(now - 1.0, timezone.utc))),
                 GpsFix(3, 4, to_iso(datetime.fromtimestamp(now + 0.2, timezone.utc)))]
        self.assertEqual(nearest_fix(fixes, now).lat, 3)
        self.assertIsNone(nearest_fix(fixes, now + 60))
        self.assertIsNone(nearest_fix([], now))


class Pipeline(unittest.TestCase):
    def frame(self, epoch):
        return Frame(np.zeros((360, 640, 3), dtype=np.uint8), epoch)

    def test_impact_raises_severity_for_the_same_box(self):
        epoch = time.time()
        box = [raw(100, 100, 260, 220, 0.6)]
        calm = DetectionPipeline(None, FakeDetector(box), imu_poller_with(epoch - 0.5, [1.0] * 100))
        hard = DetectionPipeline(None, FakeDetector(box), imu_poller_with(epoch - 0.5, [1.0] * 40 + [4.5] * 5 + [1.0] * 55))
        d_calm, _ = calm.process(self.frame(epoch))
        d_hard, _ = hard.process(self.frame(epoch))
        order = ["low", "medium", "high"]
        self.assertGreater(order.index(d_hard[0].severity), order.index(d_calm[0].severity))

    def test_impact_outside_the_frame_window_is_ignored(self):
        epoch = time.time()
        # strike 2 s before the frame: outside +/- IMPACT_WINDOW_S
        imu = imu_poller_with(epoch - 3.0, [4.5] * 5 + [1.0] * 395)
        with_late = DetectionPipeline(None, FakeDetector([raw(100, 100, 260, 220, 0.6)]), imu)
        none = DetectionPipeline(None, FakeDetector([raw(100, 100, 260, 220, 0.6)]), imu_poller_with(epoch - 3.0, [1.0] * 400))
        a, _ = with_late.process(self.frame(epoch))
        b, _ = none.process(self.frame(epoch))
        self.assertEqual(a[0].severity, b[0].severity)

    def test_payload_fields_and_gps(self):
        epoch = time.time()
        fix = GpsFix(-29.85, 31.0, to_iso(datetime.fromtimestamp(epoch, timezone.utc)))
        p = DetectionPipeline(None, FakeDetector([raw(10, 10, 50, 50, 0.9)]), None, lambda: [fix])
        dets, img = p.process(self.frame(epoch))
        d = Detection.from_dict(dets[0].to_dict())  # round-trips through the shared payload
        self.assertEqual((d.lat, d.lon), (-29.85, 31.0))
        self.assertEqual(len(d.bbox), 4)
        self.assertEqual(img.shape, (360, 640, 3))
        self.assertTrue(img.any())  # something was drawn

    def test_no_gps_means_no_lat_lon(self):
        p = DetectionPipeline(None, FakeDetector([raw(10, 10, 50, 50, 0.9)]), None, lambda: [])
        dets, _ = p.process(self.frame(time.time()))
        self.assertIsNone(dets[0].lat)

    def test_overlapping_model_output_is_merged(self):
        p = DetectionPipeline(None, FakeDetector([raw(0, 0, 100, 100, 0.5), raw(4, 4, 100, 100, 0.8)]), None)
        dets, _ = p.process(self.frame(time.time()))
        self.assertEqual(len(dets), 1)
        self.assertEqual(dets[0].confidence, 0.8)

    def test_annotation_draws_nothing_without_detections(self):
        img = np.zeros((50, 50, 3), dtype=np.uint8)
        self.assertFalse(annotate(img, []).any())


class SourceKinds(unittest.TestCase):
    def test_source_kind_detection(self):
        self.assertTrue(FrameSource("0").is_camera)
        self.assertTrue(FrameSource("1").is_camera)
        self.assertFalse(FrameSource("0").is_file)
        self.assertFalse(FrameSource("http://pi:8081/").is_file)
        self.assertTrue(FrameSource("clip.mp4").is_file)

    def test_no_imu_pipeline_still_grades_from_size_and_confidence(self):
        p = DetectionPipeline(None, FakeDetector([raw(100, 100, 260, 220, 0.8)]), None)
        dets, _ = p.process(Frame(np.zeros((360, 640, 3), dtype=np.uint8), time.time()))
        self.assertEqual(len(dets), 1)
        self.assertIn(dets[0].severity, ("low", "medium", "high"))

    def test_disabled_pi_client_polls_nothing(self):
        pi = PiClient(enabled=False)
        pi.start()
        time.sleep(0.3)
        self.assertEqual(pi.imu.polls, 0)
        self.assertEqual(pi.imu.error, None)


@unittest.skipUnless(os.path.exists(VIDEO), "sample video missing")
class Source(unittest.TestCase):
    def test_file_frames_are_stamped_at_arrival(self):
        src = FrameSource(VIDEO)
        before = time.time()
        src.start()
        try:
            f = src.next(timeout=5)
            after = time.time()
            self.assertIsNotNone(f)
            self.assertTrue(before - config.VIDEO_LATENCY_S <= f.epoch <= after)
            self.assertEqual(f.image.shape[1::-1], (640, 360))
        finally:
            src.stop()

    def test_missing_source_reports_error_and_keeps_retrying(self):
        src = FrameSource("http://127.0.0.1:1/")
        src.start()
        end = time.time() + 15  # opening an unreachable URL can take a moment
        while src.error is None and time.time() < end:
            time.sleep(0.1)
        src.stop()
        self.assertFalse(src.connected)
        self.assertIn("cannot open", src.error)


@unittest.skipUnless(os.path.exists(VIDEO) and os.path.exists(MODEL), "sample video or model missing")
class EndToEnd(unittest.TestCase):
    def test_real_model_on_sample_video_produces_annotated_jpeg(self):
        from ref.edge.detector import Detector

        det = Detector(MODEL, conf_threshold=config.DETECT_CONF, iou_threshold=config.DETECT_IOU)
        src = FrameSource(VIDEO)
        pipe = DetectionPipeline(src, det)
        src.start()
        pipe.start()
        try:
            jpeg, seq = pipe.latest_jpeg(timeout=30)
            self.assertIsNotNone(jpeg)
            self.assertTrue(jpeg.startswith(b"\xff\xd8"))
            time.sleep(3)
            st = pipe.state()
            self.assertGreater(st["frames"], 0)
            self.assertTrue(st["recent"], "model found nothing in the sample video")
            Detection.from_dict(st["recent"][-1])
        finally:
            pipe.stop()
            src.stop()


if __name__ == "__main__":
    unittest.main()
