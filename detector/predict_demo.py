"""
KHOJ detector — live inference (demo tool + Jetson fallback).

Runs the trained model on a camera / IP stream / image and shows detections. Used
two ways:
  * ON THE JETSON with the .engine (real edge inference), or
  * ON THE LAPTOP with best.pt streaming the drone's phone camera (fallback).

Deliberately low confidence threshold: in KHOJ the detector is allowed to be
uncertain — the swarm confirms via re-observation — so we surface faint hits
rather than hide them.

    # laptop webcam:
    python detector/predict_demo.py --weights best.pt --source 0
    # phone IP-camera app:
    python detector/predict_demo.py --weights best.pt --source http://192.168.1.5:8080/video
    # a folder of aerial test images:
    python detector/predict_demo.py --weights best.pt --source path/to/imgs --no-show
"""

from __future__ import annotations

import argparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True, help="best.pt (laptop) or best.engine (Jetson)")
    ap.add_argument("--source", default="0",
                    help="webcam index (0), IP-cam URL, or image/dir path")
    ap.add_argument("--conf", type=float, default=0.25,
                    help="low on purpose — the swarm resolves uncertain hits")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--no-show", action="store_true", help="don't open a window")
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.weights)
    # webcam index comes in as a string; ultralytics wants an int for that case
    source = int(args.source) if args.source.isdigit() else args.source

    results = model.predict(
        source=source, conf=args.conf, imgsz=args.imgsz,
        stream=True, show=not args.no_show, verbose=False,
    )
    for r in results:
        n = 0 if r.boxes is None else len(r.boxes)
        if n:
            confs = [round(float(c), 2) for c in r.boxes.conf.tolist()]
            print(f"[det] {n} person(s)  conf={confs}")


if __name__ == "__main__":
    main()
