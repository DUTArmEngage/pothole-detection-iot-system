"""
pipeline.py — The edge application.

Ties the components together into the sense-detect-geolocate-buffer-publish
pipeline described in Section 3.6. This is the software that runs on the
Raspberry Pi. It is agnostic to whether its input arrives from live sensors or
from the replay harness, which is the property that makes bench evaluation
meaningful: the code under test is the code that would be deployed.

Configuration selects between the three experimental arrangements of
Section 3.4.1:

  Configuration A  edge inference + fusion      (the proposed framework)
  Configuration B  cloud-only, no local buffer  (baseline control 1)
  Configuration C  vision only, no fusion       (baseline control 2)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from detector import Detector, Detection
from severity import SeverityClassifier, Severity
from fusion import IMUFusion, FusionMode, IMUSample
from buffer import EventBuffer, RoadEvent


class Configuration(str, Enum):
    EDGE_FUSION = "A"     # proposed framework
    CLOUD_ONLY = "B"      # remote inference, no buffer
    VISION_ONLY = "C"     # edge inference, fusion disabled


@dataclass
class PipelineConfig:
    device_id: str = "rpi-bench-01"
    model_path: str = "models/pothole.tflite"
    configuration: Configuration = Configuration.EDGE_FUSION

    conf_threshold: float = 0.35
    iou_threshold: float = 0.45
    imgsz: int = 640

    fusion_mode: FusionMode = FusionMode.GATE_AND_CORROBORATE
    imu_threshold_g: float = 0.45
    corroboration_window_s: float = 0.6

    db_path: str = "data/events.db"
    publish: bool = False
    broker_host: str = "localhost"
    broker_port: int = 1883
    topic_root: str = "roads/kzn"

    # Cloud-only baseline: simulated round-trip for remote inference
    cloud_rtt_ms: float = 850.0
    cloud_upload_kb_per_frame: float = 180.0

    save_frames: bool = False
    frame_dir: str = "data/frames"


@dataclass
class PipelineStats:
    frames_seen: int = 0
    frames_inferred: int = 0
    frames_gated_out: int = 0
    raw_detections: int = 0
    confirmed_events: int = 0
    suppressed_no_corroboration: int = 0
    inference_latency_ms: List[float] = field(default_factory=list)
    e2e_latency_ms: List[float] = field(default_factory=list)
    bytes_transmitted: int = 0
    by_severity: Dict[str, int] = field(default_factory=dict)
    wall_time_s: float = 0.0

    def summary(self) -> dict:
        lat = self.inference_latency_ms
        e2e = self.e2e_latency_ms
        infer_rate = (self.frames_inferred / self.wall_time_s
                      if self.wall_time_s else 0.0)
        return {
            "frames_seen": self.frames_seen,
            "frames_inferred": self.frames_inferred,
            "frames_gated_out": self.frames_gated_out,
            "gating_reduction_pct": round(
                100 * self.frames_gated_out / self.frames_seen, 2)
                if self.frames_seen else 0.0,
            "raw_detections": self.raw_detections,
            "confirmed_events": self.confirmed_events,
            "suppressed_no_corroboration": self.suppressed_no_corroboration,
            "inference_latency_ms": _stat(lat),
            "end_to_end_latency_ms": _stat(e2e),
            "throughput_fps": round(infer_rate, 2),
            "bytes_transmitted": self.bytes_transmitted,
            "kb_per_minute": round(
                self.bytes_transmitted / 1024 / (self.wall_time_s / 60), 2)
                if self.wall_time_s else 0.0,
            "by_severity": self.by_severity,
            "wall_time_s": round(self.wall_time_s, 2),
        }


def _stat(v: List[float]) -> Optional[dict]:
    if not v:
        return None
    a = np.array(v)
    return {
        "mean": round(float(a.mean()), 2),
        "median": round(float(np.median(a)), 2),
        "p95": round(float(np.percentile(a, 95)), 2),
        "min": round(float(a.min()), 2),
        "max": round(float(a.max()), 2),
        "std": round(float(a.std()), 2),
        "n": len(v),
    }


class Pipeline:
    """The edge processing pipeline."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self.stats = PipelineStats()

        self.detector = Detector(
            cfg.model_path, cfg.conf_threshold, cfg.iou_threshold, cfg.imgsz)
        self.detector.warmup()

        mode = (FusionMode.DISABLED
                if cfg.configuration is Configuration.VISION_ONLY
                else cfg.fusion_mode)
        self.fusion = IMUFusion(
            mode=mode,
            trigger_threshold_g=cfg.imu_threshold_g,
            corroboration_window_s=cfg.corroboration_window_s,
        )

        # Configuration B transmits raw frames and keeps no local store
        self.use_buffer = cfg.configuration is not Configuration.CLOUD_ONLY
        self.buffer = EventBuffer(cfg.db_path) if self.use_buffer else None

        self.severity: Optional[SeverityClassifier] = None
        self.publisher = None
        if cfg.publish:
            from publisher import MQTTPublisher
            self.publisher = MQTTPublisher(
                cfg.broker_host, cfg.broker_port,
                cfg.device_id, cfg.topic_root, self.buffer)
            self.publisher.connect()

        if cfg.save_frames:
            Path(cfg.frame_dir).mkdir(parents=True, exist_ok=True)

        self.link_up = True

    # ------------------------------------------------------------------
    def set_link(self, up: bool) -> None:
        """Simulate connectivity loss for the offline recovery test."""
        self.link_up = up
        if self.publisher:
            self.publisher.set_link(up)

    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray, t: float, gnss, imu_samples) -> List[RoadEvent]:
        """Process one synchronised instant. Returns confirmed events."""
        t_start = time.perf_counter()
        self.stats.frames_seen += 1

        for s in imu_samples:
            self.fusion.add_sample(IMUSample(t=s[0], ax=s[1], ay=s[2], az=s[3]))

        if not self.fusion.should_infer(t):
            self.stats.frames_gated_out += 1
            return []

        result = self.detector.infer(frame)
        self.stats.frames_inferred += 1
        self.stats.inference_latency_ms.append(result.latency_ms)
        self.stats.raw_detections += len(result.detections)

        if self.cfg.configuration is Configuration.CLOUD_ONLY:
            # Remote inference: the frame itself crosses the network and the
            # round trip is incurred before any result is available.
            self.stats.bytes_transmitted += int(
                self.cfg.cloud_upload_kb_per_frame * 1024)
            time.sleep(self.cfg.cloud_rtt_ms / 1000.0 * 0.001)  # scaled for bench

        if not result.detections:
            return []

        if self.severity is None:
            w, h = result.frame_shape
            self.severity = SeverityClassifier(frame_area=w * h)

        decision = self.fusion.evaluate(t)
        events: List[RoadEvent] = []

        for det in result.detections:
            if not decision.corroborated:
                self.stats.suppressed_no_corroboration += 1
                continue

            sev = self.severity.classify(
                box_area=det.area,
                confidence=det.confidence,
                peak_impact_g=decision.peak_g,
            )

            ev = RoadEvent.new(
                device_id=self.cfg.device_id,
                latitude=round(gnss.latitude, 7),
                longitude=round(gnss.longitude, 7),
                confidence=round(det.confidence, 4),
                severity=sev.severity.value,
                severity_score=sev.score,
                defect_class=det.class_name,
                box=[round(v, 1) for v in (det.x1, det.y1, det.x2, det.y2)],
                peak_impact_g=decision.peak_g,
                speed_kph=round(gnss.speed_kph, 1),
                heading_deg=round(gnss.heading_deg, 1),
            )

            if self.buffer:
                self.buffer.enqueue(ev)
            if self.publisher and self.link_up:
                self.publisher.publish(ev)
            self.stats.bytes_transmitted += ev.payload_bytes

            self.stats.confirmed_events += 1
            k = sev.severity.value
            self.stats.by_severity[k] = self.stats.by_severity.get(k, 0) + 1
            events.append(ev)

        self.stats.e2e_latency_ms.append((time.perf_counter() - t_start) * 1000.0)
        return events

    # ------------------------------------------------------------------
    def run(self, session, on_event: Optional[Callable] = None,
            outage: Optional[tuple] = None) -> PipelineStats:
        """
        Run a full replay session.

        outage : (start_s, end_s) optional
            Simulated connectivity interruption, used by the offline recovery
            test in Section 4.5.
        """
        t0 = time.perf_counter()
        for rf in session:
            if outage:
                self.set_link(not (outage[0] <= rf.t <= outage[1]))
            evs = self.process(rf.image, rf.t, rf.gnss, rf.imu)
            if on_event:
                for e in evs:
                    on_event(e, rf)
        self.stats.wall_time_s = time.perf_counter() - t0

        if self.publisher:
            self.set_link(True)
            self.publisher.drain()
        return self.stats

    def close(self) -> None:
        if self.publisher:
            self.publisher.disconnect()
