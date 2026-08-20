"""
KHOJ detector — export trained weights to a TensorRT engine for the Jetson.

Real-time inference on Jetson needs a TensorRT FP16 engine, and TensorRT engines
are DEVICE-SPECIFIC: you must run this ON THE JETSON, not the training laptop.

    # on the Jetson (JetPack + ultralytics installed):
    python detector/export_jetson.py --weights best.pt --imgsz 640

Fallback if Jetson bring-up fights you: skip this, run best.pt on the laptop GPU
with predict_demo.py and tag the result as the drone's. Same demo, no engine.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True, help="path to best.pt")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--format", default="engine",
                    help="engine=TensorRT (Jetson) | onnx (portable fallback)")
    ap.add_argument("--half", action="store_true", default=True,
                    help="FP16 — faster on Jetson, negligible accuracy loss")
    args = ap.parse_args()

    from ultralytics import YOLO

    if not Path(args.weights).exists():
        raise SystemExit(f"weights not found: {args.weights}")

    model = YOLO(args.weights)
    out = model.export(format=args.format, half=args.half, imgsz=args.imgsz)
    print(f"[export] wrote {out}")
    if args.format == "engine":
        print("[export] load this .engine on the Jetson with predict_demo.py")


if __name__ == "__main__":
    main()
