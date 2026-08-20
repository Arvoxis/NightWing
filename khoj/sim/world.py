"""KHOJ — the World (ground truth + physics + sensing).

This is "the universe": it holds where victims actually are, where the hidden
RF phone is, where the false-alarm decoys are, and where each drone body is.
Agents NEVER see this directly — they only get per-agent sensor packets from
`sense()`. The world moves bodies toward the goals the agents choose; it never
chooses goals itself.

The visual detector here is a stochastic stand-in tuned to behave like a real
low-threshold YOLO on aerial imagery (C's module later replaces it, same output
shape):
  * easy victim      -> a single look is usually confident   (immediate confirm)
  * borderline victim-> each look is uncertain, but looks fuse UP across angles
                        (this is what the re-observation loop RESCUES)
  * decoy (no person)-> looks are weak and fuse DOWN          (dismissed -> Hive-Mind)
"""
import math
import random


class World:
    def __init__(self, size=32, n_drones=6, seed=0):
        self.rng = random.Random(seed)
        self.size = size
        self.dt = 0.1          # seconds per tick
        self.speed = 6.0       # cells / second
        self.sensor_r = 3.0    # visual footprint radius (cells)
        self.t = 0.0

        # visual victims: 2 easy + 2 borderline. Order of rng calls fixed so the
        # same seed gives the same layout for the baseline run (fair comparison).
        self.victims = []
        for diff in (0.15, 0.15, 0.58, 0.58):
            self.victims.append({
                "x": self.rng.uniform(5, size - 5),
                "y": self.rng.uniform(5, size - 5),
                "difficulty": diff, "found": False,
            })

        # false-alarm decoys (hot machinery, debris) — real detections, no person
        self.decoys = [{"x": self.rng.uniform(4, size - 4),
                        "y": self.rng.uniform(4, size - 4)} for _ in range(4)]

        # the hero: a hidden RF phone, NO visual signature. Only RF finds it.
        self.rf_source = {"x": self.rng.uniform(7, size - 7),
                          "y": self.rng.uniform(7, size - 7), "found": False}

        # drone bodies launch clustered at a base corner
        self.bodies = [{"x": 1.0 + 0.4 * i, "y": 1.0, "heading": 0.0, "battery": 100.0}
                       for i in range(n_drones)]

    # --- physics: move each body toward its goal -------------------------------
    def step(self, goals):
        self.t += self.dt
        for b, g in zip(self.bodies, goals):
            if g is None:
                continue
            gx, gy = g
            dx, dy = gx - b["x"], gy - b["y"]
            d = math.hypot(dx, dy)
            if d > 1e-6:
                step = min(self.speed * self.dt, d)
                b["x"] += dx / d * step
                b["y"] += dy / d * step
                b["heading"] = math.degrees(math.atan2(dy, dx))
            b["battery"] = max(0.0, b["battery"] - 0.02)

    # --- RF path-loss model: RSSI (dBm) at a point -----------------------------
    def rssi_at(self, x, y):
        s = self.rf_source
        d = max(1.0, math.hypot(x - s["x"], y - s["y"]))
        P0, n, noise = -40.0, 2.5, 2.0      # dBm@1cell, path-loss exp, gaussian noise
        return P0 - 10 * n * math.log10(d) + self.rng.gauss(0, noise)

    def _conf(self, mu, prox):
        return max(0.0, min(1.0, mu + 0.05 * prox + self.rng.gauss(0, 0.06)))

    # --- per-agent PRIVATE sensor packet ---------------------------------------
    def sense(self, i):
        b = self.bodies[i]
        det, best_d = None, self.sensor_r
        # nearest visible object (victim OR decoy) inside the footprint
        for v in self.victims:
            if v["found"]:
                continue
            d = math.hypot(b["x"] - v["x"], b["y"] - v["y"])
            if d <= best_d:
                prox = 1.0 - d / self.sensor_r
                mu = 0.85 if v["difficulty"] <= 0.3 else 0.68     # easy vs borderline
                det, best_d = {"x": v["x"], "y": v["y"], "conf": self._conf(mu, prox)}, d
        for c in self.decoys:
            d = math.hypot(b["x"] - c["x"], b["y"] - c["y"])
            if d <= best_d:
                prox = 1.0 - d / self.sensor_r
                det, best_d = {"x": c["x"], "y": c["y"], "conf": self._conf(0.30, prox)}, d
        return {"agent": i, "x": b["x"], "y": b["y"], "heading": b["heading"],
                "battery": b["battery"], "det": det,
                "rssi": self.rssi_at(b["x"], b["y"]), "t": self.t}

    def all_found(self):
        return all(v["found"] for v in self.victims) and self.rf_source["found"]
