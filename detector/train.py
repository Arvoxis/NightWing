"""
KHOJ detector — fine-tune YOLO for aerial SAR person detection.

Fine-tunes a COCO-pretrained YOLO (nano by default, for Jetson real-time) on an
aerial search-and-rescue dataset. Single class: person.

Run in the `ml` conda env (has ultralytics + GPU torch):

    conda activate ml
    # SARD exported from Roboflow in YOLOv11 format gives you a data.yaml:
    python detector/train.py --data path/to/SARD/data.yaml

Typical first run finishes in ~1-3 h on a laptop GPU and early-stops if it
plateaus. Weights land in detector/runs/<name>/weights/best.pt
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="path to dataset data.yaml")
    ap.add_argument("--model", default="yolo11n.pt",
                    help="pretrained weights (yolo11n.pt / yolov8n.pt / yolo11s.pt)")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--patience", type=int, default=20, help="early-stop patience")
    ap.add_argument("--imgsz", type=int, default=640,
                    help="640 for the demo; 1280 for tiny high-altitude people (slower)")
    ap.add_argument("--batch", type=int, default=-1, help="-1 = auto-fit GPU memory")
    ap.add_argument("--device", default="0", help="'0' for GPU, 'cpu' to force CPU")
    ap.add_argument("--name", default="sard_n")
    ap.add_argument("--degraded", action="store_true",
                    help="extra augmentation for haze/low-light robustness (SAR conditions)")
    args = ap.parse_args()

    from ultralytics import YOLO

    if not Path(args.data).exists():
        raise SystemExit(f"data.yaml not found: {args.data}")

    model = YOLO(args.model)

    train_kwargs = dict(
        data=args.data,
        epochs=args.epochs,
        patience=args.patience,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project="detector/runs",
        name=args.name,
        # default ultralytics aug is already strong (mosaic, mixup, HSV, flips)
        pretrained=True,
    )

    if args.degraded:
        # push harder on photometric aug to survive smoke / low light / haze
        train_kwargs.update(
            hsv_h=0.02, hsv_s=0.8, hsv_v=0.6,   # colour/brightness jitter
            degrees=10.0, translate=0.15, scale=0.6,
            mosaic=1.0, mixup=0.15,
        )

    print(f"[train] model={args.model} data={args.data} imgsz={args.imgsz} "
          f"epochs={args.epochs} degraded={args.degraded}")
    model.train(**train_kwargs)

    # quick validation summary
    metrics = model.val()
    print(f"[done] mAP50={metrics.box.map50:.3f}  mAP50-95={metrics.box.map:.3f}")
    print(f"[weights] detector/runs/{args.name}/weights/best.pt")


if __name__ == "__main__":
    main()
