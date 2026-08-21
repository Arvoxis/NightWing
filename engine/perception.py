"""
KHOJ — perception (v3).  The real detector, in the loop.

Turns a real aerial image + the drone's pose into `Detection` objects in the
*exact* schema the swarm already speaks (`protocol.Detection`). This is the piece
that makes the pipeline real: everything downstream — fusion, re-observation,
trust, the auction — consumes these identically to the v0 ground-truth stub, so
nothing in belief.py / swarm.py changes.

    image + pose  ->  YOLO(best.pt)  ->  boxes  ->  world-frame Detections

Where it runs:
    On the real drone / Jetson (agent's onboard camera) — or here, fed real SARD
    aerial frames, as the `perception_demo` proves. It is the drop-in for
    `world._sense()`: same output type, real perception instead of a stand-in.

Camera → world projection (nadir / top-down pinhole):
    A detection's pixel offset from image centre maps to a ground offset via the
    altitude and field-of-view, then rotates by the drone's heading into the
    world frame. The camera constants below are ASSUMPTIONS to calibrate against
    the actual airframe (altitude, lens FOV, image size) — they don't affect the
    detection *confidence* (that's the model), only where the hit is placed.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from engine.protocol import Detection, bearing_between


# ------------------------------------------------------------------ model path

# Trained weights (SARD, mAP50≈0.934). Gitignored + nested under runs/ by
# ultralytics, so resolve from a few likely spots rather than hard-coding one.
_WEIGHT_CANDIDATES = [
    "runs/detect/detector/runs/sard_n/weights/best.pt",
    "detector/runs/sard_n/weights/best.pt",
    "runs/detect/train/weights/best.pt",
    "best.pt",
]


def resolve_weights(path: str | None = None) -> str:
    if path and os.path.exists(path):
        return path
    for c in _WEIGHT_CANDIDATES:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(
        "Could not find trained weights (best.pt). Pass weights=... explicitly. "
        f"Looked in: {_WEIGHT_CANDIDATES}")


# ------------------------------------------------------------------ camera + pose

@dataclass
class CameraModel:
    """Nadir (straight-down) aerial camera. FOV + altitude set the ground
    footprint; calibrate these to the real airframe. Vertical FOV is derived from
    the image aspect ratio at inference time if not given."""
    altitude: float = 30.0        # metres above ground (world units)
    hfov_deg: float = 66.0        # horizontal field of view
    vfov_deg: float | None = None  # if None, derived from image aspect ratio

    def ground_half_extents(self, img_w: int, img_h: int) -> tuple[float, float]:
        """Half of the ground footprint the frame covers, in world units, at the
        image edges: (east_half from hfov, north_half from vfov)."""
        hfov = math.radians(self.hfov_deg)
        if self.vfov_deg is not None:
            vfov = math.radians(self.vfov_deg)
        else:
            # derive vertical FOV from horizontal FOV and the image aspect ratio
            vfov = 2.0 * math.atan(math.tan(hfov / 2.0) * (img_h / img_w))
        return (self.altitude * math.tan(hfov / 2.0),
                self.altitude * math.tan(vfov / 2.0))


@dataclass
class DronePose:
    """Where the observing drone is when it takes the frame. `heading` is yaw in
    radians (world frame); the image's 'up' is taken to be the drone's forward."""
    x: float
    y: float
    heading: float = 0.0
    altitude: float | None = None   # overrides CameraModel.altitude if set


# ------------------------------------------------------------------ perceptor

class Perceptor:
    """Runs the trained detector and projects boxes to world-frame Detections."""

    def __init__(self, weights: str | None = None, conf: float = 0.25,
                 camera: CameraModel | None = None, device: str | None = None):
        # import lazily so the engine/dashboard never pull in torch unless
        # perception is actually used.
        from ultralytics import YOLO
        self.weights = resolve_weights(weights)
        self.model = YOLO(self.weights)
        self.conf = conf
        self.camera = camera or CameraModel()
        self.device = device

    # -------------------------------------------------------------- inference

    def detect(self, image, pose: DronePose, agent_id: int, t: float,
               conf: float | None = None) -> list[Detection]:
        """Run the detector on one frame and return world-frame Detections.

        `image` is anything ultralytics accepts (path, ndarray, PIL). One
        Detection per box, confidence straight from the model, position projected
        through the camera + pose."""
        res = self.model(image, conf=conf or self.conf, verbose=False,
                         device=self.device)[0]
        img_h, img_w = res.orig_shape
        alt = pose.altitude if pose.altitude is not None else self.camera.altitude
        east_half, north_half = CameraModel(
            altitude=alt, hfov_deg=self.camera.hfov_deg,
            vfov_deg=self.camera.vfov_deg).ground_half_extents(img_w, img_h)

        out: list[Detection] = []
        boxes = res.boxes
        for i in range(len(boxes)):
            u, v = float(boxes.xywh[i][0]), float(boxes.xywh[i][1])  # box centre px
            c = float(boxes.conf[i])
            wx, wy = self._project(u, v, img_w, img_h, east_half, north_half, pose)
            out.append(Detection(
                agent_id=agent_id, x=wx, y=wy, confidence=c,
                bearing=bearing_between(pose.x, pose.y, wx, wy), t=t,
            ))
        return out

    # -------------------------------------------------------------- projection

    @staticmethod
    def _project(u: float, v: float, img_w: int, img_h: int,
                 east_half: float, north_half: float, pose: DronePose) -> tuple[float, float]:
        """Pixel (u,v) -> world (x,y). Camera-frame ground offset (right/forward)
        rotated into the world by the drone heading."""
        # normalised pixel offset from image centre, in [-1, 1]
        nx = (u - img_w / 2.0) / (img_w / 2.0)     # +right
        ny = (v - img_h / 2.0) / (img_h / 2.0)     # +down
        east = nx * east_half                       # camera-frame rightward ground offset
        fwd = -ny * north_half                      # image-up == drone-forward
        h = pose.heading
        # forward unit = (cos h, sin h); right unit = (sin h, -cos h)
        wx = pose.x + fwd * math.cos(h) + east * math.sin(h)
        wy = pose.y + fwd * math.sin(h) - east * math.cos(h)
        return wx, wy
