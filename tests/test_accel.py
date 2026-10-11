"""Device selection and accelerated inference.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import os
import unittest

import cv2

from main_server.accel import _openvino_gpu_available, make_detector
from shared import config

ROOT = os.path.join(os.path.dirname(__file__), "..")
MODEL = os.path.join(ROOT, "ref", "models", "pothole.pt")
VIDEO = os.path.join(ROOT, "ref", "vid_potholes.mp4")


@unittest.skipUnless(os.path.exists(MODEL) and os.path.exists(VIDEO), "model or sample video missing")
class Devices(unittest.TestCase):
    def frame(self):
        cap = cv2.VideoCapture(VIDEO)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 100)
        ok, f = cap.read()
        cap.release()
        self.assertTrue(ok)
        return f

    def test_cpu_choice_is_the_plain_reference_detector(self):
        det, name = make_detector(MODEL, "cpu", config.DETECT_CONF, config.DETECT_IOU)
        self.assertEqual(name, "PyTorch CPU")
        self.assertTrue(det.infer(self.frame()).detections)

    @unittest.skipUnless(_openvino_gpu_available(), "no OpenVINO-visible GPU")
    def test_intel_gpu_finds_the_same_potholes_as_the_cpu(self):
        f = self.frame()
        cpu, _ = make_detector(MODEL, "cpu", config.DETECT_CONF, config.DETECT_IOU)
        gpu, name = make_detector(MODEL, "gpu", config.DETECT_CONF, config.DETECT_IOU)
        self.assertEqual(name, "OpenVINO Intel GPU")
        a, b = cpu.infer(f).detections, gpu.infer(f).detections
        self.assertEqual(len(a), len(b))
        for x, y in zip(sorted(a, key=lambda d: d.x1), sorted(b, key=lambda d: d.x1)):
            self.assertAlmostEqual(x.x1, y.x1, delta=8)  # pixels
            self.assertAlmostEqual(x.confidence, y.confidence, delta=0.05)

    def test_unavailable_gpu_falls_back_to_cpu(self):
        det, name = make_detector(MODEL, "cuda", config.DETECT_CONF, config.DETECT_IOU)  # no NVIDIA here
        self.assertTrue(det.infer(self.frame()).detections)


if __name__ == "__main__":
    unittest.main()
