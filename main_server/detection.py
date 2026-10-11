"""Command-line wiring for the detection pipeline, shared by the laptop entry points."""

from __future__ import annotations

import argparse
from typing import Callable, List, Optional

from main_server.clients.pi_client import PiClient
from main_server.image_processing import DetectionPipeline, FrameSource
from main_server.pinstore import PinStore
from shared import config
from shared.schemas import GpsFix

DEFAULT_MODEL = "ref/models/pothole.pt"


def add_detection_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--video", default=None, help=f"video source: a webcam index (0, 1, ...), a URL or a file. Default: the Pi's motion stream, http://<pi>:{config.MOTION_PORT}/")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto", choices=("auto", "cuda", "gpu", "cpu"), help="inference device: auto (fastest available: NVIDIA, then Intel GPU via OpenVINO, then CPU), cuda, gpu (Intel), cpu")
    ap.add_argument("--pin-confidence", type=float, default=config.PIN_CONFIDENCE, help="pin and save detections at or above this confidence (default %(default)s)")
    ap.add_argument("--pins-db", default=config.PINS_DB, help="SQLite file where pins are saved for later use (default %(default)s)")
    ap.add_argument("--no-save", action="store_true", help="show pins but do not save them")
    ap.add_argument("--no-imu", action="store_true", help="no IMU in this setup: skip polling and grade severity from box size and confidence")
    ap.add_argument("--no-detect", action="store_true", help="skip video and detection")


def make_pipeline(args, pi: PiClient, gps_history: Optional[Callable[[], List[GpsFix]]] = None) -> Optional[DetectionPipeline]:
    if args.no_detect:
        return None
    from main_server.accel import make_detector  # pulls in ultralytics; only when detection is on

    source = args.video or f"http://{config.PI_HOST}:{config.MOTION_PORT}/"
    detector, device_name = make_detector(args.model, args.device, config.DETECT_CONF, config.DETECT_IOU)
    detector.warmup()
    frames = FrameSource(source)
    store = None if args.no_save else PinStore(args.pins_db)
    pipeline = DetectionPipeline(frames, detector, pi.imu if pi.enabled else None, gps_history,
                                 store=store, pin_confidence=args.pin_confidence)
    frames.start()
    pipeline.start()
    print(f"[detect] {source} -> {args.model} on {device_name}")
    print(f"[pins] confidence >= {args.pin_confidence:.2f} -> " + ("not saved" if store is None else f"saved to {args.pins_db}"))
    return pipeline
