"""
detector.py — Detection model wrapper.

Supports two backends so that the same interface serves both the development
machine (PyTorch .pt) and the Raspberry Pi (TFLite INT8). The backend is
selected automatically from the model file extension.

The wrapper reports its own inference latency so that timing measurements
require no external instrumentation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np


@dataclass
class Detection:
    """A single detection within one frame."""
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int
    class_name: str

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)


@dataclass
class InferenceResult:
    """Result of running the model over one frame."""
    detections: List[Detection]
    latency_ms: float
    frame_shape: tuple[int, int]


class Detector:
    """
    Unified detection interface.

    Parameters
    ----------
    model_path : str
        Path to a .pt (Ultralytics) or .tflite (quantised) model file.
    conf_threshold : float
        Minimum confidence for a detection to be returned.
    iou_threshold : float
        Non-maximum suppression IoU threshold.
    imgsz : int
        Square input resolution expected by the model.
    """

    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.45,
        imgsz: int = 640,
    ) -> None:
        self.model_path = Path(model_path)
        self.conf = conf_threshold
        self.iou = iou_threshold
        self.imgsz = imgsz
        self.class_names = ["pothole"]

        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Model not found: {model_path}\n"
                "Train a model first — see train/README.md"
            )

        suffix = self.model_path.suffix.lower()
        if suffix == ".pt":
            self.backend = "torch"
            self._init_torch()
        elif suffix == ".tflite":
            self.backend = "tflite"
            self._init_tflite()
        else:
            raise ValueError(f"Unsupported model format: {suffix}")

    # ------------------------------------------------------------------
    # Backend initialisation
    # ------------------------------------------------------------------
    def _init_torch(self) -> None:
        from ultralytics import YOLO
        self._model = YOLO(str(self.model_path))
        names = getattr(self._model, "names", None)
        if names:
            self.class_names = [names[i] for i in sorted(names)]

    def _init_tflite(self) -> None:
        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            from tensorflow.lite import Interpreter  # type: ignore

        self._interp = Interpreter(model_path=str(self.model_path), num_threads=4)
        self._interp.allocate_tensors()
        self._in = self._interp.get_input_details()[0]
        self._out = self._interp.get_output_details()[0]
        self._in_dtype = self._in["dtype"]
        self._in_quant = self._in.get("quantization", (0.0, 0))
        self._out_quant = self._out.get("quantization", (0.0, 0))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def infer(self, frame: np.ndarray) -> InferenceResult:
        """Run the model over one BGR frame and return detections with timing."""
        h, w = frame.shape[:2]
        t0 = time.perf_counter()

        if self.backend == "torch":
            dets = self._infer_torch(frame)
        else:
            dets = self._infer_tflite(frame, w, h)

        latency_ms = (time.perf_counter() - t0) * 1000.0
        return InferenceResult(dets, latency_ms, (w, h))

    def warmup(self, n: int = 3) -> None:
        """Run dummy inferences so first-call overhead is excluded from timing."""
        dummy = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
        for _ in range(n):
            self.infer(dummy)

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------
    def _infer_torch(self, frame: np.ndarray) -> List[Detection]:
        res = self._model.predict(
            frame, conf=self.conf, iou=self.iou,
            imgsz=self.imgsz, verbose=False,
        )[0]

        out: List[Detection] = []
        if res.boxes is None:
            return out
        for b in res.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            cid = int(b.cls[0])
            out.append(Detection(
                x1=x1, y1=y1, x2=x2, y2=y2,
                confidence=float(b.conf[0]),
                class_id=cid,
                class_name=self.class_names[cid] if cid < len(self.class_names) else str(cid),
            ))
        return out

    def _infer_tflite(self, frame: np.ndarray, ow: int, oh: int) -> List[Detection]:
        import cv2

        img = cv2.resize(frame, (self.imgsz, self.imgsz))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        if self._in_dtype == np.uint8:
            tensor = np.expand_dims(img, 0).astype(np.uint8)
        else:
            tensor = np.expand_dims(img.astype(np.float32) / 255.0, 0)

        self._interp.set_tensor(self._in["index"], tensor)
        self._interp.invoke()
        raw = self._interp.get_tensor(self._out["index"])

        scale, zero = self._out_quant
        if scale and scale > 0:
            raw = (raw.astype(np.float32) - zero) * scale

        return self._decode_yolo(raw, ow, oh)

    def _decode_yolo(self, raw: np.ndarray, ow: int, oh: int) -> List[Detection]:
        """
        Decode Ultralytics export output of shape (1, 4+nc, N) into detections.
        Boxes arrive as normalised cx, cy, w, h.
        """
        pred = np.squeeze(raw)
        if pred.ndim != 2:
            return []
        # Orient so that rows are attributes, columns are anchors
        if pred.shape[0] > pred.shape[1]:
            pred = pred.T

        boxes, scores, classes = [], [], []
        nc = pred.shape[0] - 4
        for i in range(pred.shape[1]):
            cls_scores = pred[4:4 + nc, i]
            cid = int(np.argmax(cls_scores))
            score = float(cls_scores[cid])
            if score < self.conf:
                continue
            cx, cy, bw, bh = pred[0:4, i]
            boxes.append([
                (cx - bw / 2) * ow, (cy - bh / 2) * oh,
                (cx + bw / 2) * ow, (cy + bh / 2) * oh,
            ])
            scores.append(score)
            classes.append(cid)

        if not boxes:
            return []

        keep = self._nms(np.array(boxes), np.array(scores), self.iou)
        out: List[Detection] = []
        for k in keep:
            x1, y1, x2, y2 = boxes[k]
            cid = classes[k]
            out.append(Detection(
                x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2),
                confidence=float(scores[k]), class_id=cid,
                class_name=self.class_names[cid] if cid < len(self.class_names) else str(cid),
            ))
        return out

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> List[int]:
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]
        keep: List[int] = []
        while order.size > 0:
            i = order[0]
            keep.append(int(i))
            if order.size == 1:
                break
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
            order = order[1:][iou <= thr]
        return keep
