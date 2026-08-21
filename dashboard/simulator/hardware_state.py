"""
KHOJ - hardware state generator (the 5 real ESP32 boards, on the real dashboard).

The third state source, alongside B1's `fake` mock and the pure-python `real`
engine:

    fake     -> FakeStateGenerator      (mock, for standalone frontend work)
    real     -> RealStateGenerator      (the python swarm engine)
    hardware -> HardwareStateGenerator  (THIS: the actual boards, live)   <--

Where the data comes from:
    The boards are driven by khoj/sim/feeder_real.py, which owns the 5 COM ports
    and therefore has to be its own process. It POSTs a compact hardware snapshot
    to the backend (`POST /ingest`), which lands here. This class holds the most
    recent snapshot and maps it onto the SimState payload `frontend/app.js`
    already consumes - so nothing in the frozen frontend changes for real
    hardware to render.

Why the mapping lives here and not in the feeder:
    Same split RealStateGenerator uses. The feeder speaks the firmware's
    vocabulary (board ids, state enums, radians, raw event strings); the
    dashboard contract wants degrees, UPPERCASE state names, tasks with bids, and
    survivor records. Keeping the translation on this side means the firmware-
    facing file never has to care about the dashboard contract, and this file is
    the single place to look when the two disagree.

Honesty notes (what is real vs. filled in), because a demo that overstates is
worse than one that admits its edges:
    * position / state / link  - REAL, straight off the boards.
    * detections               - REAL detector confidence when the feeder runs
                                 with weights; simulated bands otherwise. The
                                 snapshot says which, in `bridge.detector`.
    * battery                  - NOT real. The boards are USB-powered and report
                                 no battery, so this reports 100. Flagged in
                                 `bridge.battery_source`.
    * trust                    - NOT modelled on-device; reported as 1.0.
"""

from __future__ import annotations

import math
import time

# The board's usb_goal_t state enum (khoj/sim/protocol.py STATE_*), mapped to
# the state vocabulary frontend/app.js colours by substring match:
#   SEARCH -> amber, REOBS -> blue, IDLE -> grey, DEAD -> X marker.
# "RF SEARCH" deliberately contains SEARCH: the RF gradient IS the drone
# searching, and it renders in the searching colour instead of the unknown-state
# red the frontend falls back to.
_STATE_NAMES = {
    0: "SEARCHING",
    1: "REOBSERVING",
    2: "RF SEARCH",
}

STALE_AFTER = 3.0          # seconds without an ingest before the bridge reads offline


def _deg(rad: float) -> float:
    """Radians -> degrees in [0, 360), per the dashboard contract."""
    return round(math.degrees(rad) % 360.0, 1)


class HardwareStateGenerator:
    """Same interface as the other two generators: `generate_state() -> dict`.
    Unlike them it does not simulate anything - it is a mailbox that the live
    bridge fills and the dashboard drains."""

    def __init__(self, seed: int | None = None, grid: int = 32):
        self.grid = grid
        self._snap: dict | None = None
        self._at = 0.0                  # wall-clock time of the last ingest
        self._ingests = 0
        self._reset_pending = False     # RESTART pressed, not yet relayed to the bridge

    # ---------------------------------------------------------------- ingest

    def ingest(self, snapshot: dict) -> dict:
        """Called by the backend when the bridge POSTs. Last write wins - a late
        frame is worthless on a live dashboard, so nothing is queued. The reply
        relays a pending RESTART back to the bridge (one-shot)."""
        self._snap = snapshot or {}
        self._at = time.time()
        self._ingests += 1
        g = self._snap.get("grid")
        if isinstance(g, (int, float)) and g > 0:
            self.grid = int(g)
        reset, self._reset_pending = self._reset_pending, False
        return {"ok": True, "ingests": self._ingests, "reset": reset}

    def reset(self, seed: int | None = None) -> None:
        """The dashboard's RESTART button. The laptop can't wipe the boards' own
        resolved memory (that needs a power-cycle), but it CAN restart the search:
        this flags a reset that the next /ingest reply relays to the bridge, which
        clears coverage + the map and resumes searching."""
        self._reset_pending = True
        return None

    # ---------------------------------------------------------------- tick

    def generate_state(self) -> dict:
        snap = self._snap
        age = time.time() - self._at if self._snap is not None else None
        online = snap is not None and age is not None and age < STALE_AFTER
        if snap is None:
            return self._empty("waiting for the bridge (khoj/sim/feeder_real.py)")
        return self._payload(snap, online, age)

    # ---------------------------------------------------------------- mapping

    def _payload(self, snap: dict, online: bool, age: float) -> dict:
        grid = self.grid
        known = snap.get("known") or []
        boards = snap.get("boards") or []

        agents = [self._agent(b, online) for b in boards]
        confirmed = [{"x": round(float(p[0]), 2), "y": round(float(p[1]), 2)}
                     for p in (snap.get("confirmed") or [])]

        # Survivors on the map = only what the SWARM visually resolved. The RF
        # source is drawn separately (see the payload's `rf` / `ground_truth`),
        # NOT as a survivor - mixing a radio fix into the survivor list is exactly
        # what made the map confusing. Dismissed spots are carried additively but
        # deliberately not drawn (resolved non-events).
        survivors = [{"id": i, "x": c["x"], "y": c["y"], "confidence": 1.0,
                      "status": "confirmed", "n_views": 2, "modality": "vision"}
                     for i, c in enumerate(confirmed)]
        rf = snap.get("rf")

        return {
            # ---- exactly the shape frontend/app.js already consumes ----------
            "agents": agents,
            "map": {
                "width": grid, "height": grid,
                "grid_width": grid, "grid_height": grid,
                "buildings": [], "obstacles": [], "terrain": [],
                # real coverage, straight from the bridge's footprint tracking.
                "known": known,
                # left empty on purpose: with no prob grid the frontend falls
                # back to a per-detection glow, which is the more truthful
                # picture here (the boards keep log-odds per candidate, not a
                # laptop-side probability field).
                "prob": [],
            },
            "open_tasks": self._tasks(boards),
            "detections": self._detections(snap.get("detections") or []),
            "confirmed_survivors": confirmed,
            "trust_scores": {str(a["id"]): 1.0 for a in agents},
            # ---- additive richness (ignored by the current frontend) ---------
            "tick": snap.get("tick", 0),
            "coverage": self._coverage(known, grid),
            "mission_complete": bool(snap.get("mission_complete")),
            "survivors": survivors,
            "dismissed": snap.get("dismissed") or [],
            "events": snap.get("events") or [],
            "rf": rf,
            "rf_error": self._rf_error(rf, snap.get("phone")),
            "counts": {
                "confirmed": len(confirmed),
                "dismissed": len(snap.get("dismissed") or []),
                "boards_up": sum(1 for a in agents if a["connected"]),
                "boards": len(agents),
            },
            "bridge": {
                "online": online,
                "age": round(age, 2) if age is not None else None,
                "detector": snap.get("detector", "unknown"),
                "battery_source": "n/a (boards are USB powered)",
                "source": "hardware - 5x ESP32 over ESP-NOW",
                "ingests": self._ingests,
            },
            # ground truth the BOARDS never see; here so an operator view can
            # score the swarm instead of taking its word for it.
            "ground_truth": {
                "victims": snap.get("victims") or [],
                "decoys": snap.get("decoys") or [],
                "phone": snap.get("phone"),
            },
        }

    def _agent(self, b: dict, online: bool) -> dict:
        alive = bool(b.get("alive")) and online
        if not alive:
            state = "DEAD"
        elif b.get("returning"):
            state = "RETURNING"        # mission done, heading home (renders orange)
        elif b.get("beacon"):
            # the beacon is a fixed transmitter/relay, not a searcher. "IDLE"
            # renders grey, which reads correctly next to four moving drones.
            state = "BEACON IDLE"
        else:
            state = _STATE_NAMES.get(int(b.get("state", 0)), "SEARCHING")
        return {
            "id": int(b.get("id", 0)),
            "x": round(float(b.get("x", 0.0)), 2),
            "y": round(float(b.get("y", 0.0)), 2),
            "state": state,
            "battery": 100.0,               # see module docstring: not real
            "heading": _deg(float(b.get("heading", 0.0))),
            "trust": 1.0,
            "quarantined": False,
            "connected": alive,
            "goal": b.get("goal"),
            # --- additive, hardware-only ---
            "port": b.get("port"),
            "beacon": bool(b.get("beacon")),
            "packets": b.get("packets"),
        }

    def _detections(self, dets: list) -> list:
        out = []
        for d in dets:
            out.append({
                "agent_id": int(d.get("agent_id", 0)),
                "x": round(float(d.get("x", 0.0)), 2),
                "y": round(float(d.get("y", 0.0)), 2),
                "confidence": round(float(d.get("confidence", 0.0)), 3),
                "bearing": _deg(float(d.get("bearing", 0.0))),
                "timestamp": int(d.get("timestamp", time.time())),
            })
        return out

    def _tasks(self, boards: list) -> list:
        """Open tasks, reconstructed from what the boards report they're doing.

        There is no laptop-side task pool to read - that is the whole point of
        the architecture, the tasks live on the boards. So a task is shown where
        a board says it is working one: a REOBSERVE it won in the auction, or the
        frontier cell the laptop handed it while it had nothing to do.
        """
        seen: dict = {}
        for b in boards:
            if not b.get("alive") or b.get("beacon"):
                continue
            goal = b.get("goal")
            if not goal:
                continue
            cur = int(b.get("cur_task", 0) or 0)
            if cur:
                tid = "R_%d" % cur
                # several boards can name the same task; the owner is the one
                # actually flying it, so last-writer-wins is fine here.
                seen[tid] = {"id": tid, "type": "REOBSERVE",
                             "x": round(float(goal[0]), 2),
                             "y": round(float(goal[1]), 2),
                             "bids": {}, "owner": int(b.get("id", 0))}
            elif int(b.get("state", 0)) == 0:
                tid = "F_%d" % int(b.get("id", 0))
                seen[tid] = {"id": tid, "type": "FRONTIER",
                             "x": round(float(goal[0]), 2),
                             "y": round(float(goal[1]), 2),
                             "bids": {}, "owner": int(b.get("id", 0))}
        out = list(seen.values())
        out.sort(key=lambda d: (d["type"] != "REOBSERVE", d["id"]))
        return out

    @staticmethod
    def _coverage(known: list, grid: int) -> float:
        if not known:
            return 0.0
        seen = sum(1 for row in known for v in row if v)
        return round(seen / float(max(1, grid * grid)), 4)

    @staticmethod
    def _rf_error(rf, phone) -> float | None:
        """How far the swarm's radio fix is from the real transmitter, in cells.
        Ground truth, for scoring only - the boards never receive this."""
        if not rf or not phone:
            return None
        return round(math.hypot(float(rf[0]) - float(phone[0]),
                                float(rf[1]) - float(phone[1])), 2)

    # ---------------------------------------------------------------- idle

    def _empty(self, why: str) -> dict:
        grid = self.grid
        return {
            "agents": [], "map": {
                "width": grid, "height": grid,
                "grid_width": grid, "grid_height": grid,
                "buildings": [], "obstacles": [], "terrain": [],
                "known": [], "prob": [],
            },
            "open_tasks": [], "detections": [], "confirmed_survivors": [],
            "trust_scores": {}, "survivors": [], "events": [],
            "tick": 0, "coverage": 0.0, "mission_complete": False,
            "bridge": {"online": False, "reason": why, "ingests": self._ingests},
        }

    # convenience so StateService can call either name
    def update_state(self) -> dict:
        return self.generate_state()
