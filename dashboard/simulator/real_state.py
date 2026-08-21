"""
KHOJ — real-engine state generator (the bridge).

Drop-in replacement for `FakeStateGenerator`: same `__init__(seed)` /
`generate_state() -> dict` interface, but instead of faking, it ticks the REAL
swarm engine (world + swarm brain + Bayesian belief) and maps our `SimState`
onto the exact payload shape the existing dashboard (`app.js`) already consumes.

    StateService.generator = RealStateGenerator(seed)   # the only swap point

Why an adapter and not a contract rewrite:
    The dashboard's frontend reads a fixed shape (agents / map / open_tasks /
    detections / confirmed_survivors / trust_scores). Rather than renegotiate
    that contract mid-hackathon, this adapter emits a SUPERSET — every field the
    frontend reads today, PLUS the engine's richer fields (belief grids,
    candidate/dismissed status, trust/quarantine/solo, coverage). Extra keys are
    ignored by the current frontend and available for progressive enhancement.
    Nothing in Rashmi's frontend has to change for real data to render.

Unit / convention bridging (this is where the two contracts actually differ):
    * bearing / heading : engine radians  ->  dashboard degrees [0, 360)
    * agent state       : engine lowercase ->  dashboard UPPERCASE
    * coordinates       : engine grid-cells; map.width/height = grid so the
                          frontend's world->canvas scaling maps 1 cell = 1 unit
    * bids              : engine flat list ->  per-task {agent_id: value} dict
"""

from __future__ import annotations

import math
import time
from collections import deque

from engine.world import World, WorldConfig
from engine.swarm import Swarm, BrainConfig
from engine.run_sim import tick_once, mission_complete


def _deg(rad: float) -> float:
    """Radians -> degrees in [0, 360), rounded for a clean wire payload."""
    return round(math.degrees(rad) % 360.0, 1)


class RealStateGenerator:
    def __init__(self, seed: int | None = None, detection_window: int = 40,
                 loop: bool = True, hold_frames: int = 30):
        # loop: after MISSION COMPLETE, hold the final frame briefly then restart
        # with a fresh seed so an unattended booth demo never freezes on one run.
        self.seed = 0 if seed is None else seed
        self.detection_window = detection_window
        self.loop = loop
        self.hold_frames = hold_frames
        self._recent: deque = deque(maxlen=detection_window)
        self._bids_by_task: dict[str, dict] = {}   # cached; last_bids is sparse
        self._hold = 0
        self._build(self.seed)

    def _build(self, seed: int):
        self.world = World(WorldConfig(seed=seed))
        self.swarm = Swarm(list(self.world.agents.keys()),
                           BrainConfig(speed=self.world.cfg.agent_speed))
        self._recent.clear()
        self._bids_by_task = {}
        self._hold = 0

    def reset(self, seed: int | None = None):
        """Reset the current mission while keeping the generator instance alive."""
        self.seed = self.seed + 1 if seed is None else seed
        self._build(self.seed)

    # -------------------------------------------------------------- tick

    def generate_state(self) -> dict:
        done = mission_complete(self.world, self.swarm)
        if done and self.loop:
            # hold the money shot (5/5 confirmed) for a few frames, then restart
            self._hold += 1
            if self._hold >= self.hold_frames:
                self.seed += 1
                self._build(self.seed)
                done = False
        if not done:
            dets = tick_once(self.world, self.swarm) or []
            now = int(time.time())
            for d in dets:
                self._recent.append({
                    "agent_id": d.agent_id,
                    "x": round(d.x, 2),
                    "y": round(d.y, 2),
                    "confidence": round(d.confidence, 3),
                    "bearing": _deg(d.bearing),        # degrees, per dashboard contract
                    "timestamp": now,
                })
            # last_bids is only repopulated on auction ticks; cache so the task
            # board isn't empty on the ~4/5 ticks between auctions.
            if self.swarm.last_bids:
                fresh: dict[str, dict] = {}
                for bid in self.swarm.last_bids:
                    fresh.setdefault(bid["task_id"], {})[str(bid["agent_id"])] = bid["value"]
                self._bids_by_task = fresh
        return self._payload(done)

    # -------------------------------------------------------------- mapping

    def _payload(self, done: bool) -> dict:
        w, s = self.world, self.swarm
        gw, gh = w.cfg.grid_w, w.cfg.grid_h

        agents = [self._agent(a) for a in s.snapshots()]
        survivors = s.store.survivors()                 # confirmed + candidate (rich)
        confirmed = [{"x": round(sv["x"], 2), "y": round(sv["y"], 2)}
                     for sv in survivors if sv["status"] == "confirmed"]
        trust_scores = {str(b.id): round(s.store.trust(b.id), 3) for b in s.brains.values()}

        return {
            # ---- shape the existing frontend already consumes ------------------
            "agents": agents,
            "map": {
                "width": gw, "height": gh,
                "grid_width": gw, "grid_height": gh,
                "buildings": [], "obstacles": [], "terrain": [],
                # --- additive richness: the real belief grids for a heatmap ---
                "known": w.known,                       # known[y][x] in [0,1]
                "prob": s.store.prob_grid(gw, gh),      # survivor-probability field
            },
            "open_tasks": self._open_tasks(),
            "detections": list(self._recent),
            "confirmed_survivors": confirmed,
            "trust_scores": trust_scores,
            # ---- additive canonical richness (ignored by current frontend) -----
            "t": round(w.t, 2),
            "tick": w.tick,
            "coverage": round(w.coverage(), 4),
            "mission_complete": done,
            "survivors": survivors,                     # full status/confidence/n_views
            "counts": s.store.counts(),                 # confirmed/candidate/dismissed
        }

    def _agent(self, snap: dict) -> dict:
        # snap comes from swarm.snapshots(): already carries the display-state
        # override (quarantined/solo/returning) + trust/quarantined/connected.
        return {
            "id": snap["id"],
            "x": round(snap["x"], 2),
            "y": round(snap["y"], 2),
            "state": str(snap["state"]).upper(),        # dashboard wants UPPERCASE
            "battery": round(snap["battery"], 1),
            "heading": _deg(snap["heading"]),           # degrees
            # --- additive richness ---
            "trust": snap.get("trust", 1.0),
            "quarantined": snap.get("quarantined", False),
            "connected": snap.get("connected", True),
            "goal": snap.get("goal"),
        }

    def _open_tasks(self) -> list[dict]:
        out = []
        for t in self.swarm.tasks.values():
            if not t.open:
                continue
            out.append({
                "id": t.task_id,
                "type": t.type.value.upper(),           # FRONTIER / REOBSERVE
                "x": round(t.x, 2),
                "y": round(t.y, 2),
                "bids": self._bids_by_task.get(t.task_id, {}),
                # --- additive richness ---
                "prior": round(t.prior, 3),
                "cand_id": t.cand_id,
            })
        # reobserve tasks first: they're the interesting ones for the task board
        out.sort(key=lambda d: (d["type"] != "REOBSERVE", d["id"]))
        return out

    # convenience so StateService can call either name
    def update_state(self) -> dict:
        return self.generate_state()
