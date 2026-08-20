"""
KHOJ — the swarm brain (v0: frontier auction).

This is the core IP. Each agent runs an identical decision loop with NO central
assigner: it looks at the shared belief map, finds frontier tasks (boundaries
between searched and unknown), scores every open task, bids, and the highest
bidder wins. Ties break by lowest agent id.

v0 implements the frontier auction only. v1 adds re-observation + fusion; v2 adds
failure handling / trust. The bid function is written so those slot in cleanly:
frontier and (later) reobserve tasks share the same currency.

    bid = U * exp(-(t_now + cost)/tau) / (cost + eps)      [expected survivors / sec]

The C port on the ESP32 translates THIS file, so keep the maths float-friendly
and the state machine explicit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from protocol import (
    Task, TaskType, Bid, AgentSnapshot, AgentState,
)


# ------------------------------------------------------------------ config

@dataclass
class BrainConfig:
    frontier_step: int = 3        # sample frontier cells on a coarse grid
    p_det: float = 0.7            # detector recall (for frontier utility)
    tau: float = 600.0            # survival time constant (seconds)
    eps: float = 1e-3
    speed: float = 4.0            # must match world agent_speed for cost=time
    t_obs: float = 1.0            # time to service a task
    arrive_dist: float = 0.6      # within this of goal counts as "arrived"


# ------------------------------------------------------------------ per-agent brain

class AgentBrain:
    """One agent's decision-making. Holds the agent's belief of its own state and
    its current assignment. Decisions are made in `decide()` given the shared
    task pool; allocation is resolved by the Swarm via broadcast bids."""

    def __init__(self, agent_id: int, cfg: BrainConfig):
        self.id = agent_id
        self.cfg = cfg
        self.state = AgentState.BIDDING
        self.current_task: Task | None = None
        # mirrored physical state (updated from the world each tick)
        self.x = 0.0
        self.y = 0.0
        self.heading = 0.0
        self.battery = 100.0
        self.alive = True

    # -------------------------------------------------------------- bidding

    def value_of(self, task: Task) -> float:
        """Expected survivors for servicing this task. v0: frontier only."""
        c = self.cfg
        if task.type == TaskType.FRONTIER:
            # cheap frontier utility: prior mass * recall (area folded into the
            # coarse frontier sampling). prior defaults small but > 0.
            return max(0.05, task.prior) * c.p_det
        # REOBSERVE handled in v1; keep a floor so it never crashes if present.
        return max(0.05, task.prior)

    def cost_of(self, task: Task) -> float:
        """Cost in TIME (seconds) to reach and service the task."""
        c = self.cfg
        d = math.hypot(task.x - self.x, task.y - self.y)
        return d / c.speed + c.t_obs

    def bid_for(self, task: Task, t_now: float) -> float:
        """The unified bid: expected survivors per second, discounted by survival
        decay evaluated at ARRIVAL time (so distant tasks are penalised extra)."""
        c = self.cfg
        if not self.alive:
            return 0.0
        U = self.value_of(task)
        cost = self.cost_of(task)
        decay = math.exp(-(t_now + cost) / c.tau)
        return U * decay / (cost + c.eps)

    # -------------------------------------------------------------- lifecycle

    def arrived(self) -> bool:
        if self.current_task is None:
            return False
        d = math.hypot(self.current_task.x - self.x, self.current_task.y - self.y)
        return d <= self.cfg.arrive_dist

    def snapshot(self) -> AgentSnapshot:
        goal = None
        if self.current_task is not None:
            goal = (self.current_task.x, self.current_task.y)
        return AgentSnapshot(
            id=self.id, x=self.x, y=self.y, heading=self.heading,
            battery=self.battery, state=self.state.value, goal=goal,
        )


# ------------------------------------------------------------------ the swarm

class Swarm:
    """Coordinates the agents via a sequential single-item auction. This object
    stands in for "the mesh": in v0 it runs the auction in one process, but the
    logic (bid -> compare -> award, no central scoring) is what the ESP-NOW
    firmware replicates. It never *assigns* by fiat — it only compares broadcast
    bids and lets the winner take the task."""

    def __init__(self, agent_ids: list[int], cfg: BrainConfig | None = None):
        self.cfg = cfg or BrainConfig()
        self.brains = {i: AgentBrain(i, self.cfg) for i in agent_ids}
        self.tasks: dict[str, Task] = {}
        self.last_bids: list[dict] = []   # for the dashboard task board

    # -------------------------------------------------------------- frontier tasks

    def rebuild_frontier_tasks(self, known: list[list[float]], prob: list[list[float]],
                               grid_w: int, grid_h: int):
        """Find frontier cells (searched cells adjacent to unknown) and turn them
        into FRONTIER tasks. Rebuilt each auction round from the current map.
        Existing REOBSERVE tasks (v1) are preserved."""
        step = self.cfg.frontier_step
        # drop stale frontier tasks; keep reobserve tasks
        self.tasks = {tid: t for tid, t in self.tasks.items()
                      if t.type != TaskType.FRONTIER}

        for gy in range(0, grid_h, step):
            for gx in range(0, grid_w, step):
                if known[gy][gx] > 0.5:
                    continue  # already searched
                if not self._is_frontier(known, gx, gy, grid_w, grid_h):
                    continue
                tid = f"F_{gx}_{gy}"
                self.tasks[tid] = Task(
                    task_id=tid, type=TaskType.FRONTIER,
                    x=float(gx), y=float(gy),
                    prior=self._local_prior(prob, gx, gy, grid_w, grid_h),
                )

    def _is_frontier(self, known, gx, gy, grid_w, grid_h) -> bool:
        """An unknown cell within `frontier_step` cells of a searched cell is on
        the frontier — that's where exploring reveals the most new area. The
        search radius matches the sampling step so the coarse frontier grid can't
        straddle the searched boundary and miss it (which deadlocks exploration)."""
        step = self.cfg.frontier_step
        for dy in range(-step, step + 1):
            for dx in range(-step, step + 1):
                nx, ny = gx + dx, gy + dy
                if 0 <= nx < grid_w and 0 <= ny < grid_h:
                    if known[ny][nx] > 0.5:
                        return True
        return False

    def _local_prior(self, prob, gx, gy, grid_w, grid_h) -> float:
        # small uniform baseline + any accumulated survivor probability nearby
        base = 0.1
        if prob and 0 <= gy < grid_h and 0 <= gx < grid_w:
            return base + prob[gy][gx]
        return base

    # -------------------------------------------------------------- auction

    def run_auction(self, t_now: float):
        """Sequential single-item auction. Repeatedly: every free agent bids its
        best open task; the globally-highest bid is awarded; repeat until no more
        awards can be made this round. Deterministic (ties -> lowest id)."""
        self.last_bids = []

        # agents already committed to a task stay committed (v0 has no hysteresis
        # yet; v2 adds it). Only free/alive agents bid.
        free = [b for b in self.brains.values()
                if b.alive and b.current_task is None]

        open_tasks = lambda: [t for t in self.tasks.values() if t.open]

        while free and open_tasks():
            best = None  # (bid_value, agent_id, task)
            for b in free:
                for t in open_tasks():
                    v = b.bid_for(t, t_now)
                    self.last_bids.append({"agent_id": b.id, "task_id": t.task_id,
                                           "value": round(v, 4)})
                    if best is None or v > best[0] or (v == best[0] and b.id < best[1]):
                        best = (v, b.id, t)
            if best is None or best[0] <= 0:
                break
            _, winner_id, task = best
            task.open = False
            task.owner = winner_id
            wb = self.brains[winner_id]
            wb.current_task = task
            wb.state = AgentState.SEARCHING
            free = [b for b in free if b.id != winner_id]

    # -------------------------------------------------------------- tick

    def sync_from_world(self, world):
        """Pull each agent's physical state from the world into its brain."""
        for i, brain in self.brains.items():
            body = world.agents.get(i)
            if body is None:
                continue
            brain.x, brain.y = body.x, body.y
            brain.heading = body.heading
            brain.battery = body.battery
            if not body.alive and brain.alive:
                # agent just died: release its task back to the pool
                brain.alive = False
                brain.state = AgentState.DEAD
                if brain.current_task is not None:
                    brain.current_task.open = True
                    brain.current_task.owner = None
                    brain.current_task = None

    def resolve_arrivals(self):
        """Agents that reached their task free themselves for the next round."""
        for b in self.brains.values():
            if not b.alive:
                continue
            if b.current_task is not None and b.arrived():
                # frontier serviced; the world will have marked it known
                b.current_task.open = False
                b.current_task.owner = None
                self.tasks.pop(b.current_task.task_id, None)
                b.current_task = None
                b.state = AgentState.BIDDING

    def commands(self) -> dict[int, tuple | None]:
        """Goal per agent for the world to drive."""
        out = {}
        for b in self.brains.values():
            if not b.alive:
                out[b.id] = None
            elif b.current_task is not None:
                out[b.id] = (b.current_task.x, b.current_task.y)
            else:
                out[b.id] = None
        return out
    