"""Hardware-accelerated inference for the reference detector.

`auto` picks the fastest device available:
  1. an NVIDIA GPU, via PyTorch CUDA (needs a CUDA build of torch);
  2. an Intel GPU, via OpenVINO (the model is exported once and cached next to the .pt);
  3. otherwise the plain PyTorch CPU path.
Output is the same `Detection`/`InferenceResult` the reference `Detector` returns.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from ref.edge.detector import Detection, Detector


class AcceleratedDetector(Detector):
    """The reference Detector's interface, running a model on a chosen device."""

    def __init__(self, model: str, device: str, conf_threshold: float, iou_threshold: float, imgsz: int = 640):
        # Deliberately not calling Detector.__init__: it only accepts .pt/.tflite files,
        # and an OpenVINO model is a directory.
        from ultralytics import YOLO

        self.model_path, self.conf, self.iou, self.imgsz = Path(model), conf_threshold, iou_threshold, imgsz
        self.device = device
        self.backend = "torch"  # routes infer() to _infer_torch below
        self._model = YOLO(str(model), task="detect")
        names = getattr(self._model, "names", None)
        self.class_names = [names[i] for i in sorted(names)] if names else ["pothole"]

    def _infer_torch(self, frame) -> List[Detection]:
        res = self._model.predict(frame, conf=self.conf, iou=self.iou, imgsz=self.imgsz, device=self.device, verbose=False)[0]
        out: List[Detection] = []
        for b in (res.boxes or []):
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            cid = int(b.cls[0])
            out.append(Detection(x1, y1, x2, y2, float(b.conf[0]), cid,
                                 self.class_names[cid] if cid < len(self.class_names) else str(cid)))
        return out


def _openvino_gpu_available() -> bool:
    try:
        import openvino as ov

        return "GPU" in ov.Core().available_devices
    except Exception:
        return False


def _openvino_model(pt: Path, imgsz: int) -> Path:
    """Export `pt` to OpenVINO next to it, once, and return the model directory."""
    target = pt.with_name(pt.stem + "_openvino_model")
    if not target.exists():
        print(f"[detect] exporting {pt.name} to OpenVINO (one-time, about 30 s)...")
        from ultralytics import YOLO

        YOLO(str(pt)).export(format="openvino", imgsz=imgsz, half=False)
    return target


def make_detector(model: str, device: str, conf: float, iou: float, imgsz: int = 640) -> Tuple[Detector, str]:
    """Build a detector for `device` in {auto, cuda, gpu, cpu}. Returns (detector, description)."""
    pt = Path(model)

    def cpu() -> Tuple[Detector, str]:
        return Detector(str(pt), conf_threshold=conf, iou_threshold=iou, imgsz=imgsz), "PyTorch CPU"

    if device == "cpu" or pt.suffix.lower() != ".pt":
        return cpu()

    if device in ("auto", "cuda"):
        try:
            import torch

            if torch.cuda.is_available():
                return AcceleratedDetector(str(pt), "cuda:0", conf, iou, imgsz), f"CUDA ({torch.cuda.get_device_name(0)})"
        except Exception as exc:
            print(f"[detect] CUDA unavailable: {exc}")
        if device == "cuda":
            print("[detect] no CUDA GPU; falling back")

    if device in ("auto", "gpu", "cuda") and _openvino_gpu_available():
        try:
            det = AcceleratedDetector(str(_openvino_model(pt, imgsz)), "intel:gpu", conf, iou, imgsz)
            det.warmup(1)  # compiles for the GPU; fails here, not mid-demo
            return det, "OpenVINO Intel GPU"
        except Exception as exc:
            print(f"[detect] OpenVINO GPU failed ({exc}); falling back to CPU")
    elif device == "gpu":
        print("[detect] no Intel GPU/OpenVINO found (pip install openvino); falling back to CPU")
    return cpu()
