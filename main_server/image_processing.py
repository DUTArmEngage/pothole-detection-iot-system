"""Laptop image processing: video in, graded and annotated detections out.

Frame source time: the `motion` MJPEG stream carries no per-frame timestamp, so a
frame is stamped with the laptop's (NTP-synced) clock when it arrives, minus
`config.VIDEO_LATENCY_S` for capture and network delay. See interfaces.md.

Severity comes from `ref/edge/severity.py` (box size, confidence, IMU impact).
Impact is the peak |az - 1| g over IMU samples within +/- IMPACT_WINDOW_S of the
frame time. A strike lands after the pothole is seen, so a frame is held until
IMU data past `ts + IMPACT_WINDOW_S` exists (or a short timeout passes).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Tuple

import cv2
import numpy as np

from ref.edge.detector import Detection as RawDetection
from ref.edge.detector import Detector
from ref.edge.severity import SeverityClassifier
from shared import config
from shared.imu_poller import ImuPoller
from shared.schemas import Detection, GpsFix, parse_ts, to_iso

from datetime import datetime, timezone

COLOURS = {"low": (80, 200, 80), "medium": (0, 190, 255), "high": (60, 60, 255)}  # BGR
IMU_WAIT_TIMEOUT_S = 0.6


# --- frame source ---------------------------------------------------------------

@dataclass
class Frame:
    image: np.ndarray
    epoch: float  # source time, seconds since epoch (laptop clock minus VIDEO_LATENCY_S)


class FrameSource:
    """Reads a motion HTTP stream or a video file on a thread; keeps the newest frames.

    A file is paced at its own frame rate and looped, so it behaves like a live stream.
    """

    def __init__(self, source: str, loop: bool = True, backlog: int = 3):
        self.source = source
        self.is_camera = source.isdigit()  # "0", "1": a webcam attached to this machine
        self.is_file = not self.is_camera and not source.lower().startswith(("http://", "https://", "rtsp://"))
        self.loop = loop and self.is_file
        self._frames: Deque[Frame] = deque(maxlen=backlog)
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self.connected = False
        self.error: Optional[str] = None
        self.frames_read = 0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.is_camera:  # DirectShow opens webcams quickly and reliably on Windows
                backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
                cap = cv2.VideoCapture(int(self.source), backend)
            else:
                cap = cv2.VideoCapture(self.source)
            if not cap.isOpened():
                self.connected, self.error = False, f"cannot open {self.source}"
                self._stop.wait(1.0)  # the Pi may not be up yet; keep retrying
                continue
            self.connected, self.error = True, None
            fps = cap.get(cv2.CAP_PROP_FPS) if self.is_file else 0
            period = 1.0 / fps if fps and fps > 0 else 0.0
            next_t = time.perf_counter()
            while not self._stop.is_set():
                ok, image = cap.read()
                if not ok:
                    break
                if period:
                    next_t += period
                    time.sleep(max(0.0, next_t - time.perf_counter()))
                frame = Frame(image, time.time() - config.VIDEO_LATENCY_S)
                with self._cond:
                    self._frames.append(frame)
                    self.frames_read += 1
                    self._cond.notify_all()
            cap.release()
            self.connected = False
            if self.is_file and self.loop:
                continue
            if self.is_file:
                return
            self.error = "stream ended; reconnecting"
            self._stop.wait(1.0)

    def next(self, timeout: float = 1.0) -> Optional[Frame]:
        """Oldest frame not yet taken; None on timeout."""
        with self._cond:
            if not self._frames:
                self._cond.wait(timeout)
            return self._frames.popleft() if self._frames else None


# --- detection helpers ------------------------------------------------------------

def iou(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(dets: List[RawDetection], iou_thr: float = config.DETECT_IOU) -> List[RawDetection]:
    """Drop lower-confidence boxes that overlap a kept box by more than `iou_thr`."""
    kept: List[RawDetection] = []
    for d in sorted(dets, key=lambda d: d.confidence, reverse=True):
        if all(iou((d.x1, d.y1, d.x2, d.y2), (k.x1, k.y1, k.x2, k.y2)) <= iou_thr for k in kept):
            kept.append(d)
    return kept


def peak_impact(samples) -> Optional[float]:
    """Peak vertical deviation |az - 1| in g, or None with no samples."""
    return max((abs(s.az - 1.0) for s in samples), default=None)


def nearest_fix(fixes: List[GpsFix], epoch: float, max_gap_s: float = config.GPS_MATCH_S) -> Optional[GpsFix]:
    best = min(fixes, key=lambda f: abs(parse_ts(f.ts).timestamp() - epoch), default=None)
    if best and abs(parse_ts(best.ts).timestamp() - epoch) <= max_gap_s:
        return best
    return None


def annotate(image: np.ndarray, dets: List[Detection]) -> np.ndarray:
    out = image.copy()
    for d in dets:
        x1, y1, x2, y2 = (int(v) for v in d.bbox)
        colour = COLOURS[d.severity]
        cv2.rectangle(out, (x1, y1), (x2, y2), colour, 2)
        label = f"{d.severity.upper()} {d.confidence:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        top = max(y1 - th - 6, 0)
        cv2.rectangle(out, (x1, top), (x1 + tw + 6, top + th + 6), colour, -1)
        cv2.putText(out, label, (x1 + 3, top + th + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return out


# --- map events ---------------------------------------------------------------------

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
EVENT_MERGE_S = 2.0


class EventLog:
    """One map event per pothole encounter: detections within EVENT_MERGE_S merge, keeping the worst severity.

    The same pothole appears in many consecutive frames; plotting each would smear a pile of pins.
    Only detections that carry a position become events.
    """

    def __init__(self, keep: int = 200):
        self._events: Deque[dict] = deque(maxlen=keep)

    def add(self, d: Detection) -> None:
        if d.lat is None:
            return
        t = parse_ts(d.ts).timestamp()
        if self._events and t - self._events[-1]["_t"] <= EVENT_MERGE_S:
            e = self._events[-1]
            e["_t"] = t
            if SEVERITY_RANK[d.severity] > SEVERITY_RANK[e["severity"]]:
                e["severity"] = d.severity
            e["confidence"] = max(e["confidence"], d.confidence)
            return
        self._events.append({"_t": t, "ts": d.ts, "lat": d.lat, "lon": d.lon, "severity": d.severity, "confidence": d.confidence})

    def list(self) -> List[dict]:
        return [{k: v for k, v in e.items() if k != "_t"} for e in self._events]


# --- pipeline ---------------------------------------------------------------------

class DetectionPipeline:
    def __init__(
        self,
        frames: FrameSource,
        detector: Detector,
        imu: Optional[ImuPoller] = None,
        gps_history: Optional[Callable[[], List[GpsFix]]] = None,
        recent: int = 50,
    ):
        self.frames, self.detector, self.imu, self.gps_history = frames, detector, imu, gps_history
        self._classifier: Optional[SeverityClassifier] = None
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._seq = 0
        self._recent: Deque[Detection] = deque(maxlen=recent)
        self.events = EventLog()
        self._out_times: Deque[float] = deque()
        self._stop = threading.Event()
        self.fps = 0.0
        self.latency_ms = 0.0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _imu_ready(self, frame: Frame) -> bool:
        """True once IMU data reaches past the frame's impact window (or there is no IMU)."""
        if not self.imu:
            return True
        newest = self.imu.newest_epoch()
        return newest is not None and newest >= frame.epoch + config.IMPACT_WINDOW_S

    def detect(self, frame: Frame) -> List[RawDetection]:
        result = self.detector.infer(frame.image)
        self.latency_ms = result.latency_ms
        return dedupe(result.detections)

    def grade(self, frame: Frame, raw: List[RawDetection]) -> Tuple[List[Detection], np.ndarray]:
        """Attach severity (with IMU impact), position and timestamp; draw the annotation."""
        h, w = frame.image.shape[:2]
        if self._classifier is None or self._classifier.frame_area != w * h:
            self._classifier = SeverityClassifier(frame_area=w * h)
        impact = None
        if self.imu:
            impact = peak_impact(self.imu.between(frame.epoch - config.IMPACT_WINDOW_S, frame.epoch + config.IMPACT_WINDOW_S))
        fix = nearest_fix(self.gps_history(), frame.epoch) if self.gps_history else None
        ts = to_iso(datetime.fromtimestamp(frame.epoch, timezone.utc))

        dets = []
        for d in raw:
            sev = self._classifier.classify(d.area, d.confidence, impact)
            dets.append(Detection(
                ts=ts, bbox=[d.x1, d.y1, d.x2, d.y2], severity=sev.severity.value,
                confidence=round(d.confidence, 3),
                lat=fix.lat if fix else None, lon=fix.lon if fix else None,
            ))
        return dets, annotate(frame.image, dets)

    def process(self, frame: Frame) -> Tuple[List[Detection], np.ndarray]:
        """Detect, grade and annotate one frame (no waiting for IMU data)."""
        return self.grade(frame, self.detect(frame))

    def _publish(self, dets: List[Detection], image: np.ndarray) -> None:
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        now = time.perf_counter()
        self._out_times.append(now)
        while self._out_times and now - self._out_times[0] > 2.0:
            self._out_times.popleft()
        self.fps = len(self._out_times) / 2.0  # frames published in the last 2 s
        with self._lock:
            if ok:
                self._jpeg, self._seq = buf.tobytes(), self._seq + 1
            self._recent.extend(dets)
            for d in dets:
                self.events.add(d)

    def _run(self) -> None:
        # Inference runs on every frame straight away. Only the grading step waits for IMU
        # samples to cover the frame's impact window, so waiting never limits throughput.
        pending: Deque[Tuple[Frame, List[RawDetection], float]] = deque()
        while not self._stop.is_set():
            frame = self.frames.next(timeout=0.05 if pending else 1.0)
            if frame is not None:
                pending.append((frame, self.detect(frame), time.time() + IMU_WAIT_TIMEOUT_S))
            while pending and (self._imu_ready(pending[0][0]) or time.time() > pending[0][2]):
                f, raw, _ = pending.popleft()
                self._publish(*self.grade(f, raw))

    def latest_jpeg(self, after_seq: int = -1, timeout: float = 1.0) -> Tuple[Optional[bytes], int]:
        """Newest annotated JPEG newer than `after_seq`, waiting briefly for one."""
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if self._jpeg is not None and self._seq > after_seq:
                    return self._jpeg, self._seq
            time.sleep(0.02)
        return None, after_seq

    def state(self) -> dict:
        with self._lock:
            recent = [d.to_dict() for d in self._recent]
        return {
            "connected": self.frames.connected,
            "error": self.frames.error,
            "frames": self.frames.frames_read,
            "fps": round(self.fps, 1),
            "latency_ms": round(self.latency_ms, 1),
            "recent": recent[-10:],
            "events": self.events.list(),
        }
