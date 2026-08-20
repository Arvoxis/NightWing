"""
KHOJ — quick visual viewer (dev/preview tool, NOT the real dashboard).

Watch the swarm self-organize and cover the map with a live matplotlib window.
This is a throwaway sanity tool so the team can *see* the engine working before
the real web dashboard exists. Run it in the `ml` env (has matplotlib):

    conda activate ml
    python -m scripts.viewer                 # seed 0
    python -m scripts.viewer --seed 1        # the seed that used to deadlock
    python -m scripts.viewer --save run.png  # render one frame headless (no window)

Colors:
    agents  blue=searching  yellow=bidding  purple=reobserving
            orange=returning  black X=dead
    map     dark=unknown     light=searched
    survivors (ground truth) red X ; turns green when sensed
"""

from __future__ import annotations

import argparse

import matplotlib
import numpy as np

from engine.world import World, WorldConfig
from engine.swarm import Swarm, BrainConfig
from engine.run_sim import tick_once, mission_complete


STATE_COLOR = {
    "searching": "#2b8cff",
    "bidding": "#ffcc00",
    "reobserving": "#a855f7",
    "returning": "#22d3ee",
    "solo": "#ff8800",
    "quarantined": "#dc2626",
    "dead": "#000000",
}


def make(seed: int = 0):
    w = World(WorldConfig(seed=seed))
    s = Swarm(list(w.agents.keys()), BrainConfig(speed=w.cfg.agent_speed))
    return w, s


def draw(ax, w: World, s: Swarm):
    """Render one frame of the current sim state onto ax."""
    ax.clear()
    # searched/unknown map
    known = np.array(w.known)
    ax.imshow(known, origin="lower", cmap="bone", vmin=0, vmax=1,
              extent=[0, w.cfg.grid_w, 0, w.cfg.grid_h], alpha=0.9)

    # ground-truth survivors (small faint X) — for debugging belief vs truth
    for idx, (sx, sy) in enumerate(w.survivors_gt):
        ax.scatter([sx], [sy], marker="x", s=40, c="#94a3b8",
                   linewidths=1.0, zorder=4)

    # BELIEF: what the swarm thinks. confirmed=green ring, candidate=yellow ring,
    # dismissed=faint grey (false alarms it rejected).
    for cand in s.store.cands.values():
        if cand.status == "confirmed":
            ax.scatter([cand.x], [cand.y], marker="o", s=240,
                       facecolors="none", edgecolors="#22c55e", linewidths=2.5, zorder=6)
            ax.text(cand.x + 0.5, cand.y + 0.5, f"{cand.prob():.2f}",
                    fontsize=7, color="#22c55e", zorder=8)
        elif cand.status == "candidate":
            ax.scatter([cand.x], [cand.y], marker="o", s=180,
                       facecolors="none", edgecolors="#fbbf24", linewidths=1.8, zorder=6)
        else:  # dismissed
            ax.scatter([cand.x], [cand.y], marker="x", s=60,
                       c="#64748b", linewidths=1.0, alpha=0.6, zorder=5)

    # agents + their goal lines. display state reflects quarantine/solo/returning.
    for b in s.brains.values():
        if not b.alive:
            ax.scatter([b.x], [b.y], marker="x", s=90, c="black", zorder=6)
            ax.text(b.x + 0.4, b.y + 0.4, str(b.id), fontsize=8, color="#888", zorder=8)
            continue
        if b.id in s.store.quarantined:
            disp = "quarantined"
        elif not b.connected:
            disp = "solo"
        elif b.returning:
            disp = "returning"
        else:
            disp = b.state.value
        col = STATE_COLOR.get(disp, "#888888")
        if b.current_task is not None:
            ax.plot([b.x, b.current_task.x], [b.y, b.current_task.y],
                    color=col, linewidth=0.8, alpha=0.5, zorder=3)
        ax.scatter([b.x], [b.y], marker="o", s=70, c=col,
                   edgecolors="white", linewidths=1.0, zorder=7)
        ax.text(b.x + 0.4, b.y + 0.4, str(b.id), fontsize=8, color="white", zorder=8)
        # trust readout under each agent
        ax.text(b.x + 0.4, b.y - 1.1, f"t{s.store.trust(b.id):.2f}",
                fontsize=6, color=col, zorder=8)

    alive = len(w.alive_agents())
    counts = s.store.counts()
    ax.set_title(f"KHOJ v1   t={w.t:5.1f}s   cov={w.coverage()*100:4.1f}%   "
                 f"agents={alive}/{w.cfg.n_agents}   "
                 f"confirmed={counts['confirmed']}/{w.cfg.n_survivors}   "
                 f"candidates={counts['candidate']}   dismissed={counts['dismissed']}"
                 + ("   COMPLETE" if mission_complete(w, s) else ""),
                 fontsize=9)
    ax.set_xlim(0, w.cfg.grid_w)
    ax.set_ylim(0, w.cfg.grid_h)
    ax.set_xticks([]); ax.set_yticks([])


def run_window(seed: int = 0, steps_per_frame: int = 3):
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    w, s = make(seed)
    fig, ax = plt.subplots(figsize=(10, 6.5))
    fig.patch.set_facecolor("#0f1115")

    def update(_frame):
        for _ in range(steps_per_frame):
            if not mission_complete(w, s):
                tick_once(w, s)
        draw(ax, w, s)

    # keep a reference so the animation isn't garbage-collected
    _ani = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
    plt.tight_layout()
    plt.show()


def save_frame(seed: int, path: str, warmup_ticks: int = 200):
    """Headless: run warmup_ticks then save a single PNG (no GUI)."""
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    w, s = make(seed)
    for _ in range(warmup_ticks):
        if mission_complete(w, s):
            break
        tick_once(w, s)
    fig, ax = plt.subplots(figsize=(10, 6.5))
    draw(ax, w, s)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    print(f"saved {path}  (t={w.t:.1f}s coverage={w.coverage()*100:.1f}%)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fps-steps", type=int, default=3,
                    help="sim ticks advanced per drawn frame")
    ap.add_argument("--save", type=str, default=None,
                    help="render one headless frame to this PNG instead of a window")
    args = ap.parse_args()
    if args.save:
        save_frame(args.seed, args.save)
    else:
        run_window(args.seed, args.fps_steps)
