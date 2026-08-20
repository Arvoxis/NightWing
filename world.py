"""
KHOJ — the world (v0).

A fast 2D grid simulation of the search area. This is "the environment": it owns
ground truth (where survivors actually are), each agent's physical position, the
belief grids, and the sensor model. It does NOT make decisions — the swarm brain
does. The world just simulates and reports.

Coordinates: continuous (x, y) in "cells". The grid is grid_w x grid_h cells.
Agents move in continuous space; sensing/known-map are per-cell.

v0 scope: movement, battery, footprint sensing, and ground-truth survivor
"detection" as a stand-in for real YOLO (perception service replaces this later).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from protocol import Detection, bearing_between


# ------------------------------------------------------------------ config

@dataclass
class WorldConfig:
    grid_w: int = 60
    grid_h: int = 40
    n_agents: int = 6
    sensor_radius: float = 3.5        # cells; footprint radius
    agent_speed: float = 4.0          # cells / second
    dt: float = 0.1                   # seconds per tick
    battery_full: float = 100.0
    battery_drain: float = 0.15       # per second while moving
    n_survivors: int = 5
    base_xy: tuple = (2.0, 2.0)       # return-to-base point
    # detection model (stand-in until real YOLO): confidence falls off with range
    detect_range: float = 4.0
    # false positives: chance per moving agent per tick of a spurious detection
    # (debris mistaken for a person) — gives the re-observation loop something to
    # reject. Real YOLO supplies these naturally at v3.
    fp_rate: float = 0.002
    seed: int | None = None


# ------------------------------------------------------------------ agent body

@dataclass
class AgentBody:
    """The *physical* agent the world simulates. The decision-making lives in the
    swarm brain; this is just the body it drives."""
    id: int
    x: float
    y: float
    heading: float = 0.0
    battery: float = 100.0
    alive: bool = True
    # commanded goal, set by the brain via world.command()
    goal: tuple | None = None

    def distance_to(self, x: float, y: float) -> float:
        return math.hypot(x - self.x, y - self.y)


# ------------------------------------------------------------------ world

class World:
    def __init__(self, cfg: WorldConfig | None = None):
        self.cfg = cfg or WorldConfig()
        self.rng = random.Random(self.cfg.seed)
        self.t = 0.0
        self.tick = 0

        c = self.cfg
        # belief grids (shared view for rendering; agents keep their own copies
        # in the brain, but v0 uses one shared known/prob grid for simplicity)
        self.known = [[0.0 for _ in range(c.grid_w)] for _ in range(c.grid_h)]
        self.prob = [[0.0 for _ in range(c.grid_w)] for _ in range(c.grid_h)]

        # ground truth survivors (unknown to the brain)
        self.survivors_gt = self._place_survivors()
        self.found_gt = set()   # indices of survivors whose cell has been sensed

        # agent bodies, spread near base
        self.agents: dict[int, AgentBody] = {}
        for i in range(c.n_agents):
            ax = c.base_xy[0] + self.rng.uniform(-1, 1)
            ay = c.base_xy[1] + self.rng.uniform(-1, 1)
            self.agents[i] = AgentBody(id=i, x=ax, y=ay, battery=c.battery_full)

        self.corrupted: set[int] = set()   # agents with a faulty sensor (phantom hits)

    # -------------------------------------------------------------- setup

    def _place_survivors(self) -> list[tuple]:
        c = self.cfg
        pts = []
        for _ in range(c.n_survivors):
            # keep survivors away from the immediate base area
            while True:
                x = self.rng.uniform(4, c.grid_w - 2)
                y = self.rng.uniform(4, c.grid_h - 2)
                if math.hypot(x - c.base_xy[0], y - c.base_xy[1]) > 8:
                    break
            pts.append((x, y))
        return pts

    # -------------------------------------------------------------- commands

    def command(self, agent_id: int, goal: tuple | None):
        """The brain tells an agent where to go. None = hold position."""
        a = self.agents.get(agent_id)
        if a and a.alive:
            a.goal = goal

    def kill(self, agent_id: int):
        """Failure injection: an agent dies."""
        a = self.agents.get(agent_id)
        if a:
            a.alive = False
            a.goal = None

    def corrupt(self, agent_id: int, on: bool = True):
        """Failure injection: an agent's sensor goes faulty and starts reporting
        phantom detections (a cracked/fogged lens after flying through smoke).
        Drives the trust/quarantine demo."""
        if on:
            self.corrupted.add(agent_id)
        else:
            self.corrupted.discard(agent_id)

    # -------------------------------------------------------------- step

    def step(self) -> list[Detection]:
        """Advance one tick. Move agents toward goals, drain battery, sense the
        footprint, and emit detections. Returns detections produced this tick."""
        c = self.cfg
        detections: list[Detection] = []

        for a in self.agents.values():
            if not a.alive:
                continue
            self._move(a)
            self._drain(a)
            detections.extend(self._sense(a))

        self.t += c.dt
        self.tick += 1
        return detections

    def _move(self, a: AgentBody):
        c = self.cfg
        if a.goal is None:
            return
        gx, gy = a.goal
        d = a.distance_to(gx, gy)
        if d < 0.05:
            a.goal = None
            return
        step = min(c.agent_speed * c.dt, d)
        a.heading = math.atan2(gy - a.y, gx - a.x)
        a.x += math.cos(a.heading) * step
        a.y += math.sin(a.heading) * step

    def _drain(self, a: AgentBody):
        c = self.cfg
        if a.distance_to(*c.base_xy) < 2.0:
            a.battery = min(c.battery_full, a.battery + 5.0 * c.dt)   # recharge at base
        elif a.goal is not None:
            a.battery = max(0.0, a.battery - c.battery_drain * c.dt)

    def _sense(self, a: AgentBody) -> list[Detection]:
        """Mark the footprint as known, and produce ground-truth-based detections.
        v0 stand-in for real perception: any survivor within detect_range yields a
        detection whose confidence falls off with distance. The perception service
        will replace this with real YOLO output (same Detection shape)."""
        c = self.cfg
        r = c.sensor_radius
        # mark known map
        x0 = max(0, int(a.x - r)); x1 = min(c.grid_w - 1, int(a.x + r))
        y0 = max(0, int(a.y - r)); y1 = min(c.grid_h - 1, int(a.y + r))
        for gy in range(y0, y1 + 1):
            for gx in range(x0, x1 + 1):
                if math.hypot(gx - a.x, gy - a.y) <= r:
                    self.known[gy][gx] = 1.0

        # detections from ground truth
        out: list[Detection] = []
        for idx, (sx, sy) in enumerate(self.survivors_gt):
            dist = a.distance_to(sx, sy)
            if dist <= c.detect_range:
                self.found_gt.add(idx)
                # confidence falls off with range. Capped below the confirm
                # threshold so no single pass is conclusive — confirmation always
                # needs a second agent (the re-observation loop). A close look
                # (re-observation) scores higher than a distant glimpse, which is
                # what separates a real survivor from a mid-confidence false alarm.
                conf = max(0.05, min(0.78, 1.0 - (dist / c.detect_range) ** 1.5))
                out.append(Detection(
                    agent_id=a.id,
                    x=sx + self.rng.uniform(-0.3, 0.3),   # small localization noise
                    y=sy + self.rng.uniform(-0.3, 0.3),
                    confidence=conf,
                    bearing=bearing_between(a.x, a.y, sx, sy),
                    t=self.t,
                ))

        # spurious detection (false positive): a moving agent occasionally mistakes
        # debris for a person, at a low-ish confidence, somewhere in its footprint.
        if a.goal is not None and self.rng.random() < c.fp_rate:
            fx = a.x + self.rng.uniform(-r, r)
            fy = a.y + self.rng.uniform(-r, r)
            if 0 <= fx < c.grid_w and 0 <= fy < c.grid_h:
                out.append(Detection(
                    agent_id=a.id,
                    x=fx, y=fy,
                    confidence=self.rng.uniform(0.28, 0.5),
                    bearing=bearing_between(a.x, a.y, fx, fy),
                    t=self.t,
                ))

        # corrupted sensor: emits phantom detections frequently, at confident-looking
        # scores, all over the place — these are false and the swarm must reject them.
        if a.id in self.corrupted and self.rng.random() < 0.4:
            fx = a.x + self.rng.uniform(-r, r)
            fy = a.y + self.rng.uniform(-r, r)
            if 0 <= fx < c.grid_w and 0 <= fy < c.grid_h:
                out.append(Detection(
                    agent_id=a.id,
                    x=fx, y=fy,
                    confidence=self.rng.uniform(0.45, 0.65),
                    bearing=bearing_between(a.x, a.y, fx, fy),
                    t=self.t,
                ))
        return out

    def survivor_within(self, x: float, y: float, radius: float) -> bool:
        """Ground-truth check: is there a real survivor within `radius` of (x,y)?
        Used only to decide whether a re-observing agent legitimately sees
        something (a real survivor) or should register a miss (a false alarm)."""
        for (sx, sy) in self.survivors_gt:
            if math.hypot(sx - x, sy - y) <= radius:
                return True
        return False

    # -------------------------------------------------------------- queries

    def coverage(self) -> float:
        c = self.cfg
        total = c.grid_w * c.grid_h
        seen = sum(1 for row in self.known for v in row if v > 0.5)
        return seen / total

    def alive_agents(self) -> list[AgentBody]:
        return [a for a in self.agents.values() if a.alive]
