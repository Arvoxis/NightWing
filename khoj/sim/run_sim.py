#!/usr/bin/env python3
"""KHOJ — Tier-1 core runner.

Runs the swarm on a world, then runs a camera-only lawnmower baseline on an
IDENTICAL world, and prints the comparison. Headless (no display) — the pretty
dashboard is D's job; this proves the brain works and produces the money metric.

    python run_sim.py            # one run + baseline
    python run_sim.py --seed 3   # a different world
"""
import argparse
import math

from world import World
from swarm import Swarm


def run_khoj(seed, n_drones=6, max_t=120.0):
    w = World(size=32, n_drones=n_drones, seed=seed)
    sw = Swarm(w, n_drones)
    while w.t < max_t and not w.all_found():
        sw.tick()
    return {
        "t_first": sw.first_victim_t,
        "found": len(sw.confirmed),
        "total": len(w.victims) + 1,          # +1 for the RF victim
        "rf_found": w.rf_source["found"],
        "dismissed": len(sw.dismissed),
        "t_end": w.t,
    }


def run_lawnmower(seed, n_drones=6, max_t=120.0):
    """Camera-only boustrophedon sweep: no re-observation, no RF sensing.
    A victim is 'found' only on a confident SINGLE look — borderline victims are
    missed, and the RF-only victim is never found (no RF sensor)."""
    w = World(size=32, n_drones=n_drones, seed=seed)
    lanes = [[] for _ in range(n_drones)]
    lane_w = w.size / n_drones
    for i in range(n_drones):                 # assign each drone a vertical stripe
        x = lane_w * (i + 0.5)
        pts, up = [], True
        for _ in range(4):
            pts += [(x, w.size - 1)] if up else [(x, 1)]
            up = not up
        lanes[i] = pts
    idx = [0] * n_drones
    found, t_first = 0, None
    while w.t < max_t:
        goals = []
        for i in range(n_drones):
            if idx[i] >= len(lanes[i]):
                goals.append(None); continue
            gx, gy = lanes[i][idx[i]]
            b = w.bodies[i]
            if math.hypot(gx - b["x"], gy - b["y"]) < 0.5:
                idx[i] += 1
            goals.append((gx, gy))
        w.step(goals)
        for i in range(n_drones):             # single-look confident detections only
            pkt = w.sense(i)
            if pkt["det"] and pkt["det"]["conf"] >= 0.80:
                for v in w.victims:
                    if not v["found"] and (v["x"] - pkt["det"]["x"]) ** 2 + \
                            (v["y"] - pkt["det"]["y"]) ** 2 < 4:
                        v["found"] = True; found += 1
                        if t_first is None:
                            t_first = w.t
        if all(v["found"] for v in w.victims):
            break
    return {"t_first": t_first, "found": found,
            "total": len(w.victims) + 1, "rf_found": False, "t_end": w.t}


def fmt(t):
    return f"{t:5.1f}s" if t is not None else "  —  "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--drones", type=int, default=6)
    args = ap.parse_args()

    k = run_khoj(args.seed, args.drones)
    l = run_lawnmower(args.seed, args.drones)

    print("=" * 58)
    print(f"  KHOJ vs LAWNMOWER   (seed {args.seed}, {args.drones} drones)")
    print("=" * 58)
    print(f"  {'metric':28s}{'KHOJ':>12s}{'lawnmower':>14s}")
    print("-" * 58)
    print(f"  {'time to first survivor':28s}{fmt(k['t_first']):>12s}{fmt(l['t_first']):>14s}")
    print(f"  {'survivors confirmed':28s}{k['found']:>9d}/{k['total']:<2d}{l['found']:>11d}/{l['total']:<2d}")
    print(f"  {'RF victim found (no visual)':28s}{str(k['rf_found']):>12s}{str(l['rf_found']):>14s}")
    print(f"  {'false alarms dismissed':28s}{k['dismissed']:>12d}{'  (n/a)':>14s}")
    print("=" * 58)
    if k["found"] > l["found"]:
        print(f"  => KHOJ found {k['found'] - l['found']} more survivor(s), "
              f"including the RF-only victim the camera-only sweep can never see.")
    print()


if __name__ == "__main__":
    main()
