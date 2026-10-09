"""
train.py — Model training and export.

Designed to run on Google Colab's free GPU tier. Training YOLOv8n on a few
thousand pothole images takes roughly 40–90 minutes there, which is the single
step that cannot be done on the Raspberry Pi or on a laptop CPU in reasonable
time.

The script trains, evaluates on a held-out split, and exports to TFLite INT8
for deployment. The INT8 export is not optional: full-precision inference on a
Raspberry Pi runs far below the frame rate needed at vehicle speed, which is
the finding the dissertation reports in Section 4.3.1.

Usage on Colab
--------------
    !pip install ultralytics
    !python train.py --data data.yaml --epochs 80 --model yolov8n.pt

Usage locally with a GPU
------------------------
    python train.py --data data.yaml --epochs 80 --device 0
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DATA_YAML_TEMPLATE = """# Dataset configuration for pothole detection
# Directory layout expected:
#   {root}/images/train/  {root}/labels/train/
#   {root}/images/val/    {root}/labels/val/
#   {root}/images/test/   {root}/labels/test/
#
# Labels are YOLO format: one .txt per image, one line per object:
#   <class_id> <cx> <cy> <w> <h>     all normalised 0-1

path: {root}
train: images/train
val: images/val
test: images/test

names:
  0: pothole
"""


def write_data_yaml(root: str, out: str = "data.yaml") -> str:
    Path(out).write_text(DATA_YAML_TEMPLATE.format(root=root), encoding="utf-8")
    print(f"Wrote {out}")
    return out


def train(args) -> Path:
    from ultralytics import YOLO

    print(f"\nTraining {args.model} for {args.epochs} epochs at {args.imgsz}px")
    model = YOLO(args.model)

    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        patience=args.patience,
        project=args.project,
        name=args.name,
        exist_ok=True,
        seed=args.seed,

        # Augmentation. Road imagery benefits from photometric variation because
        # lighting and surface tone vary enormously; it does not benefit from
        # vertical flipping, since roads are never upside down.
        hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,
        degrees=5.0, translate=0.1, scale=0.4,
        fliplr=0.5, flipud=0.0,
        mosaic=1.0, close_mosaic=10,

        optimizer="auto",
        lr0=0.01, lrf=0.01,
        cos_lr=True,
        warmup_epochs=3.0,
        val=True, plots=True,
    )

    best = Path(args.project) / args.name / "weights" / "best.pt"
    print(f"\nBest weights: {best}")
    return best


def evaluate(weights: Path, data: str, split: str = "test") -> dict:
    """Evaluate on the held-out split and return the Chapter 4 detection metrics."""
    from ultralytics import YOLO

    print(f"\nEvaluating on the {split} split")
    m = YOLO(str(weights))
    r = m.val(data=data, split=split, plots=True)

    b = r.box
    p, rc = float(b.mp), float(b.mr)
    metrics = {
        "precision": round(p, 4),
        "recall": round(rc, 4),
        "f1": round(2 * p * rc / (p + rc), 4) if (p + rc) else 0.0,
        "mAP50": round(float(b.map50), 4),
        "mAP50_95": round(float(b.map), 4),
        "split": split,
    }
    print(json.dumps(metrics, indent=2))
    return metrics


def export_tflite(weights: Path, imgsz: int, data: str) -> Path:
    """
    Export to INT8 TFLite.

    The data argument supplies the calibration set. Post-training integer
    quantisation needs representative images to determine activation ranges;
    calibrating on the wrong distribution costs accuracy unnecessarily.
    """
    from ultralytics import YOLO

    print("\nExporting to TFLite INT8")
    m = YOLO(str(weights))
    out = m.export(format="tflite", int8=True, imgsz=imgsz, data=data)
    print(f"Exported: {out}")
    return Path(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Train and export a pothole detector")
    ap.add_argument("--data", default="data.yaml", help="dataset YAML")
    ap.add_argument("--dataset-root", help="write data.yaml for this root, then train")
    ap.add_argument("--model", default="yolov8n.pt",
                    help="yolov8n.pt is the right default for Raspberry Pi")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="0", help="'0' for GPU, 'cpu' otherwise")
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument("--project", default="runs")
    ap.add_argument("--name", default="pothole")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--out-dir", default="models")
    args = ap.parse_args()

    if args.dataset_root:
        args.data = write_data_yaml(args.dataset_root)

    if not Path(args.data).exists():
        raise SystemExit(
            f"Dataset config not found: {args.data}\n"
            "Create it with --dataset-root /path/to/dataset, or see train/README.md"
        )

    best = train(args)
    metrics = evaluate(best, args.data, split="test")

    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(best, outdir / "pothole.pt")
    (outdir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    if not args.skip_export:
        tfl = export_tflite(best, args.imgsz, args.data)
        if tfl.exists():
            shutil.copy(tfl, outdir / "pothole.tflite")

    print("\n" + "=" * 60)
    print("  Training complete")
    print("=" * 60)
    print(f"  {outdir}/pothole.pt       PyTorch weights (development machine)")
    print(f"  {outdir}/pothole.tflite   INT8 quantised (Raspberry Pi)")
    print(f"  {outdir}/metrics.json     detection metrics for Table 4.2")
    print("\n  Copy both model files to the Pi, then run bench/run_experiment.py\n")


if __name__ == "__main__":
    main()
