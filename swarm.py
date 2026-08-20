"""
KHOJ — the swarm brain (v0 auction · v1 re-observation · v2 failure handling).

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
    bearing_between, angular_diff,
)
from belief import CandidateStore, binary_entropy


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
    c_fn: float = 100.0           # cost of a false negative (missing a survivor)
    theta0: float = 1.047         # viewpoint-diversity scale (~60 deg, radians)
    max_active_reobserve: int = 3  # admission control: cap concurrent re-observes
    comms_range: float = 100.0    # radio range (cells); multi-hop via other agents.
                                  # default large = always connected; set small (or
                                  # walk a real node away) to trigger SOLO behaviour
    battery_rtb: float = 25.0     # return-to-base below this battery %


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
        self.connected = True         # in radio range of the swarm/base?
        self.returning = False        # heading home on low battery?

    # -------------------------------------------------------------- bidding

    def value_of(self, task: Task) -> float:
        """Expected survivors for servicing this task, in the same currency for
        both task types so they compete in one auction."""
        c = self.cfg
        if task.type == TaskType.FRONTIER:
            # cheap frontier utility: prior mass * recall (area folded into the
            # coarse frontier sampling). prior defaults small but > 0.
            return max(0.05, task.prior) * c.p_det

        # REOBSERVE: expected value of information on an uncertain candidate.
        #   U = H(p) * C_FN * viewpoint_diversity
        # H(p)         -> most valuable when the candidate is genuinely uncertain
        # C_FN         -> a missed survivor is far costlier than a false alarm
        # viewpoint    -> a look from a *different* bearing than the original is
        #                 what actually resolves occlusion; a redundant angle isn't
        p = min(0.98, max(0.02, task.prior))
        H = binary_entropy(p)
        approach = bearing_between(self.x, self.y, task.x, task.y)
        dtheta = angular_diff(approach, task.bearing)
        viewpoint = 1.0 - math.exp(-dtheta / c.theta0)
        return H * c.c_fn * viewpoint

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
        self.store = CandidateStore()     # shared belief / fusion (laptop-side)
        self.base_xy: tuple = (2.0, 2.0)
        # detections from agents currently out of comms are held here and merged
        # into the shared map when they reconnect (the "map merge on rendezvous")
        self.detection_buffer: dict[int, list] = {}

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

        # cells an agent is already committed to — don't re-post them (dedup, so
        # two agents don't chase the same frontier)
        committed = [(b.current_task.x, b.current_task.y)
                     for b in self.brains.values()
                     if b.alive and b.current_task is not None
                     and b.current_task.type == TaskType.FRONTIER]

        for gy in range(0, grid_h, step):
            for gx in range(0, grid_w, step):
                if known[gy][gx] > 0.5:
                    continue  # already searched
                if not self._is_frontier(known, gx, gy, grid_w, grid_h):
                    continue
                if any(abs(cx - gx) + abs(cy - gy) <= step for cx, cy in committed):
                    continue  # an agent is already heading here
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

    def rebuild_reobserve_tasks(self):
        """Turn each uncertain candidate into a REOBSERVE task, unless an agent is
        already committed to re-checking it. These compete in the same auction as
        frontier tasks — and outbid them, because confirming a likely survivor is
        worth far more than exploring blank space."""
        busy = {b.current_task.cand_id for b in self.brains.values()
                if b.alive and b.current_task is not None
                and b.current_task.type == TaskType.REOBSERVE
                and b.current_task.cand_id is not None}
        # drop all open reobserve tasks; committed ones live on the brains, not here
        self.tasks = {tid: t for tid, t in self.tasks.items()
                      if t.type != TaskType.REOBSERVE}

        # admission control: at most `max_active_reobserve` re-observes in flight at
        # once, so a noisy detector can't flood the auction and pull every agent off
        # exploration. Prioritise the most *uncertain* candidates (highest entropy).
        budget = self.cfg.max_active_reobserve - len(busy)
        if budget <= 0:
            return
        cands = [c for c in self.store.reobserve_candidates() if c.id not in busy]
        cands.sort(key=lambda c: binary_entropy(c.prob()), reverse=True)
        for c in cands[:budget]:
            tid = f"R_{c.id}"
            self.tasks[tid] = Task(
                task_id=tid, type=TaskType.REOBSERVE,
                x=c.x, y=c.y, prior=c.prob(),
                bearing=c.primary_bearing(), cand_id=c.id,
            )

    # -------------------------------------------------------------- comms / connectivity

    def update_connectivity(self, base_xy: tuple):
        """Compute which agents can reach the swarm, base included, within radio
        range — multi-hop, so agents relay for each other. An agent that drops out
        goes SOLO; one that comes back merges its buffered detections (map merge)."""
        self.base_xy = base_xy
        R = self.cfg.comms_range
        connected: set[int] = set()
        alive = [b for b in self.brains.values() if b.alive]

        changed = True
        while changed:
            changed = False
            for b in alive:
                if b.id in connected:
                    continue
                # connected if within range of the base or any connected agent
                if math.hypot(b.x - base_xy[0], b.y - base_xy[1]) <= R:
                    connected.add(b.id); changed = True; continue
                for oid in list(connected):
                    ob = self.brains[oid]
                    if math.hypot(b.x - ob.x, b.y - ob.y) <= R:
                        connected.add(b.id); changed = True; break

        for b in self.brains.values():
            was = b.connected
            now = (b.id in connected) if b.alive else False
            b.connected = now
            if now and not was:
                self._flush_buffer(b.id)   # rendezvous: merge what it found while solo

    def _flush_buffer(self, agent_id: int):
        for det in self.detection_buffer.pop(agent_id, []):
            self.store.ingest(det)

    def route_detections(self, dets: list):
        """Connected agents' detections fuse immediately; a solo agent's are held
        until it reconnects."""
        for d in dets:
            b = self.brains.get(d.agent_id)
            if b is not None and b.alive and not b.connected:
                self.detection_buffer.setdefault(d.agent_id, []).append(d)
            else:
                self.store.ingest(d)

    # -------------------------------------------------------------- battery

    def check_battery(self):
        """Low battery -> return to base, releasing the current task. Recharged
        agents (battery restored by the world at base) rejoin."""
        for b in self.brains.values():
            if not b.alive:
                continue
            if not b.returning and b.battery <= self.cfg.battery_rtb:
                b.returning = True
                b.state = AgentState.RETURNING
                if b.current_task is not None:
                    b.current_task.open = True
                    b.current_task.owner = None
                    b.current_task = None
            elif b.returning and b.battery >= 95.0:
                b.returning = False
                b.state = AgentState.BIDDING

    # -------------------------------------------------------------- auction

    def run_auction(self, t_now: float):
        """Sequential single-item auction. Repeatedly: every free agent bids its
        best open task; the globally-highest bid is awarded; repeat until no more
        awards can be made this round. Deterministic (ties -> lowest id).

        Only connected, non-returning agents bid — a solo agent can't hear the
        auction (it self-assigns a local frontier afterwards)."""
        self.last_bids = []

        free = [b for b in self.brains.values()
                if b.alive and b.current_task is None and b.connected and not b.returning]

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
            wb.state = (AgentState.REOBSERVING if task.type == TaskType.REOBSERVE
                        else AgentState.SEARCHING)
            free = [b for b in free if b.id != winner_id]

        self._assign_solo()

    def _assign_solo(self):
        """A solo (out-of-comms) free agent can't bid, so it greedily claims the
        nearest open frontier task on its own local map and keeps working."""
        open_frontier = [t for t in self.tasks.values()
                         if t.open and t.type == TaskType.FRONTIER]
        for b in self.brains.values():
            if not (b.alive and b.current_task is None
                    and not b.connected and not b.returning):
                continue
            if not open_frontier:
                continue
            nearest = min(open_frontier,
                          key=lambda t: math.hypot(t.x - b.x, t.y - b.y))
            nearest.open = False
            nearest.owner = b.id
            b.current_task = nearest
            b.state = AgentState.SOLO
            open_frontier = [t for t in open_frontier if t.task_id != nearest.task_id]

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
                t = b.current_task
                if t.type == TaskType.REOBSERVE and t.cand_id is not None:
                    # if the agent actually saw the candidate on approach, its view
                    # is already fused; if not, this records a miss (evidence
                    # against) — no-op when a view exists.
                    self.store.register_miss(t.cand_id, b.id)
                t.open = False
                t.owner = None
                self.tasks.pop(t.task_id, None)
                b.current_task = None
                b.state = AgentState.BIDDING

    def snapshots(self) -> list[dict]:
        """Per-agent view for the dashboard, enriched with trust/quarantine from
        the belief store."""
        out = []
        for b in self.brains.values():
            snap = b.snapshot()
            snap.trust = round(self.store.trust(b.id), 3)
            snap.quarantined = b.id in self.store.quarantined
            snap.connected = b.connected
            if b.alive:
                if snap.quarantined:
                    snap.state = AgentState.QUARANTINED.value
                elif not b.connected:
                    snap.state = AgentState.SOLO.value
                elif b.returning:
                    snap.state = AgentState.RETURNING.value
            out.append(snap.as_dict())
        return out

    def commands(self) -> dict[int, tuple | None]:
        """Goal per agent for the world to drive."""
        out = {}
        for b in self.brains.values():
            if not b.alive:
                out[b.id] = None
            elif b.returning:
                out[b.id] = self.base_xy           # head home to recharge
            elif b.current_task is not None:
                out[b.id] = (b.current_task.x, b.current_task.y)
            else:
                out[b.id] = None
        return out
    