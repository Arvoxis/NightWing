"""
KHOJ — shared data schemas (v0).

These are the message/state shapes every other module speaks in. They are the
contract between the engine (world/swarm), the perception service, and the
dashboard — and they mirror what the ESP32 firmware will pack in C, so keep the
fields primitive (numbers, small enums, ids) and flat.

Nothing here has behaviour. Just shapes + a couple of tiny helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import math


# ------------------------------------------------------------------ enums

class AgentState(str, Enum):
    SEARCHING = "searching"      # heading to / covering an assigned frontier
    BIDDING = "bidding"          # between tasks, participating in the auction
    REOBSERVING = "reobserving"  # diverting to re-check an uncertain detection
    RETURNING = "returning"      # battery-low, heading to base
    SOLO = "solo"                # lost comms, operating on stale info
    DEAD = "dead"                # failed / killed
    QUARANTINED = "quarantined"  # distrusted (faulty sensor); still flies, ignored


class TaskType(str, Enum):
    FRONTIER = "frontier"        # explore an unknown region
    REOBSERVE = "reobserve"      # get a second look at an uncertain detection


# ------------------------------------------------------------------ core records

@dataclass
class Detection:
    """One perception hit. `bearing` is the angle (radians) from the observing
    agent to the detected point — the re-observation logic needs it to prefer a
    *different* viewing angle on the second look."""
    agent_id: int
    x: float
    y: float
    confidence: float
    bearing: float
    t: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Task:
    """A unit of work in the auction. Frontier and reobserve tasks compete in the
    same auction with the same currency (expected survivors / second)."""
    task_id: str
    type: TaskType
    x: float
    y: float
    prior: float = 0.0           # survivor prior / current posterior at (x,y)
    bearing: float = 0.0         # for REOBSERVE: bearing of the *original* sighting
    open: bool = True
    owner: Optional[int] = None  # agent id that won it, if any
    cand_id: Optional[int] = None  # for REOBSERVE: which candidate this re-checks

    def as_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d


@dataclass
class Bid:
    agent_id: int
    task_id: str
    value: float


@dataclass
class AgentSnapshot:
    """What the dashboard needs to draw one agent. A flattened view of an agent's
    live state — not the agent's full internal belief."""
    id: int
    x: float
    y: float
    heading: float
    battery: float
    state: str                   # AgentState value
    goal: Optional[tuple] = None
    trust: float = 1.0
    quarantined: bool = False
    connected: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Survivor:
    """A confirmed (or dismissed) survivor candidate on the shared map."""
    id: int
    x: float
    y: float
    confidence: float
    status: str                  # "confirmed" | "dismissed" | "candidate"
    n_views: int = 1
    modality: str = "vision"     # "vision" | "rf" | "fused"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimState:
    """One tick, everything the dashboard renders. This is THE contract between
    the engine and the show — Person B builds the dashboard against this shape."""
    t: float
    tick: int
    grid_w: int
    grid_h: int
    # known[y][x] in [0,1]: 0 unknown, 1 fully searched
    known: list = field(default_factory=list)
    # prob[y][x] in [0,1]: survivor probability
    prob: list = field(default_factory=list)
    agents: list = field(default_factory=list)      # list[AgentSnapshot-as-dict]
    tasks: list = field(default_factory=list)       # list[Task-as-dict]
    bids: list = field(default_factory=list)        # list[dict] for the task board
    survivors: list = field(default_factory=list)   # list[Survivor-as-dict]
    mission_complete: bool = False

    def as_dict(self) -> dict:
        return {
            "t": self.t,
            "tick": self.tick,
            "grid_w": self.grid_w,
            "grid_h": self.grid_h,
            "known": self.known,
            "prob": self.prob,
            "agents": self.agents,
            "tasks": self.tasks,
            "bids": self.bids,
            "survivors": self.survivors,
            "mission_complete": self.mission_complete,
        }


# ------------------------------------------------------------------ helpers

def bearing_between(x0: float, y0: float, x1: float, y1: float) -> float:
    """Angle in radians from point 0 to point 1."""
    return math.atan2(y1 - y0, x1 - x0)


def angular_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two angles, in [0, pi]."""
    d = abs(a - b) % (2 * math.pi)
    return d if d <= math.pi else 2 * math.pi - d
