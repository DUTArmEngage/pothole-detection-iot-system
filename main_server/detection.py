"""Command-line wiring for the detection pipeline, shared by the laptop entry points."""

from __future__ import annotations

import argparse
from typing import Callable, List, Optional

from main_server.clients.pi_client import PiClient
from main_server.image_processing import DetectionPipeline, FrameSource
from shared import config
from shared.schemas import GpsFix

DEFAULT_MODEL = "ref/models/pothole.pt"


def add_detection_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--video", default=None, help=f"video source: URL or file. Default: the Pi's motion stream, http://<pi>:{config.MOTION_PORT}/")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-detect", action="store_true", help="skip video and detection")


def make_pipeline(args, pi: PiClient, gps_history: Optional[Callable[[], List[GpsFix]]] = None) -> Optional[DetectionPipeline]:
    if args.no_detect:
        return None
    from ref.edge.detector import Detector  # pulls in ultralytics; only when detection is on

    source = args.video or f"http://{config.PI_HOST}:{config.MOTION_PORT}/"
    detector = Detector(args.model, conf_threshold=config.DETECT_CONF, iou_threshold=config.DETECT_IOU)
    detector.warmup()
    frames = FrameSource(source)
    pipeline = DetectionPipeline(frames, detector, pi.imu, gps_history)
    frames.start()
    pipeline.start()
    print(f"[detect] {source} -> {args.model}")
    return pipeline
