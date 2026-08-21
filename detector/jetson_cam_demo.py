"""
KHOJ — Jetson edge perception demo (agent #7, the real drone's eye).

Runs the TensorRT engine on the Jetson's own camera (or a folder / video) and
emits detections in the SAME `Detection` shape the swarm speaks — proving the
real drone produces the identical data the sim/HIL feeder does. Each detection
maps 1:1 onto the `usb_sensor_t` the drone's ESP32 board expects (see
khoj/firmware/lib/quorum_proto/quorum_proto.h): has_detection=1,
det_x=x, det_y=y, det_conf=round(conf*100).

Self-contained — only needs `ultralytics` (already on the Jetson). Copy this one
file over; no repo checkout required.

    # live camera:
    python3 jetson_cam_demo.py --engine ~/best.engine --source 0 --save
    # a folder of frames (no camera attached):
    python3 jetson_cam_demo.py --engine ~/best.engine --source ~/samples

The camera->world projection mirrors perception.py (nadir pinhole). Calibrate
--alt / --hfov to the real airframe; they place the hit, not its confidence.
"""

from __future__ import annotations

import argparse
import math
import time


def project(u, v, W, H, alt, hfov_deg, heading):
    """Pixel (u,v) -> world offset (dx,dy) from the drone, rotated by heading."""
    hfov = math.radians(hfov_deg)
    vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * (H / W))
    east_half = alt * math.tan(hfov / 2.0)
    north_half = alt * math.tan(vfov / 2.0)
    nx = (u - W / 2.0) / (W / 2.0)
    ny = (v - H / 2.0) / (H / 2.0)
    east = nx * east_half
    fwd = -ny * north_half
    dx = fwd * math.cos(heading) + east * math.sin(heading)
    dy = fwd * math.sin(heading) - east * math.cos(heading)
    return dx, dy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="best.engine")
    ap.add_argument("--source", default="0", help="camera index, image folder, or video path")
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--agent", type=int, default=6, help="this drone's agent id (7th agent = 6)")
    ap.add_argument("--x", type=float, default=16.0, help="drone world x (grid cells)")
    ap.add_argument("--y", type=float, default=16.0, help="drone world y (grid cells)")
    ap.add_argument("--heading", type=float, default=0.0, help="drone heading (degrees)")
    ap.add_argument("--alt", type=float, default=30.0, help="altitude (world units) for projection")
    ap.add_argument("--hfov", type=float, default=66.0, help="camera horizontal FOV (deg)")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--save", action="store_true", help="save annotated frames")
    a = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(a.engine)
    src = int(a.source) if a.source.isdigit() else a.source
    hd = math.radians(a.heading)
    print(f"agent{a.agent} @ ({a.x},{a.y}) hdg={a.heading:.0f}deg  engine={a.engine}\n")

    t0 = time.time()
    n_frames = 0
    for r in model.predict(source=src, conf=a.conf, imgsz=a.imgsz,
                           stream=True, verbose=False, save=a.save):
        n_frames += 1
        H, W = r.orig_shape
        ms = r.speed.get("inference", 0.0)
        t = round(time.time() - t0, 2)
        dets = []
        for i in range(len(r.boxes)):
            u = float(r.boxes.xywh[i][0]); v = float(r.boxes.xywh[i][1])
            c = float(r.boxes.conf[i])
            dx, dy = project(u, v, W, H, a.alt, a.hfov, hd)
            wx, wy = a.x + dx, a.y + dy
            brg = math.degrees(math.atan2(wy - a.y, wx - a.x)) % 360.0
            dets.append((wx, wy, c, brg))
        if dets:
            body = "  ".join(f"(x={x:5.1f} y={y:5.1f} conf={c:.2f} brg={b:3.0f}deg"
                             f" -> usb: det_conf={round(c*100)})"
                             for x, y, c, b in dets)
            print(f"[{t:6.1f}s | {ms:4.1f}ms] agent{a.agent}: {body}")
        else:
            print(f"[{t:6.1f}s | {ms:4.1f}ms] agent{a.agent}: no person")

    dt = time.time() - t0
    if n_frames:
        print(f"\n{n_frames} frames · {n_frames/max(dt,1e-6):.1f} FPS end-to-end")


if __name__ == "__main__":
    main()
