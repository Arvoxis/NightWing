"""
KHOJ — belief / fusion (v1).  LAPTOP-SIDE (not ported to the ESP32).

Turns a stream of Detections into *candidates* (possible survivors) and fuses
multiple views into a confidence. The core rule — the project's thesis in code:

    A candidate is CONFIRMED only when independent agents agree.
    One agent's look = uncertain. A second agent, from a different angle,
    is what tips a candidate to confirmed (or, if it sees nothing, dismissed).

Fusion is log-odds over per-agent best views:

    logodds = Σ_agents logit(best_confidence)  +  (#agents that looked & missed) * MISS_LLR
    prob    = sigmoid(logodds)

Per-agent (not per-detection) so an agent flying past a survivor — whose bearing
sweeps as it passes — contributes ONE view, not dozens. That both prevents a
single agent from self-confirming and makes confirmation genuinely cooperative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from protocol import Survivor, bearing_between


# ------------------------------------------------------------------ tunables

CONFIRM_P = 0.80        # posterior >= this -> confirmed
MIN_CONFIRM_AGENTS = 2  # ...AND at least this many *distinct* agents must have
                        # seen it. Corroboration is structural, not a side-effect
                        # of the sim's confidence cap: a single high-confidence
                        # real-YOLO hit (0.8+) must NOT self-confirm. Independent
                        # agreement is the thesis — enforce it by construction.
DISMISS_P = 0.15        # posterior <= this -> dismissed
MERGE_RADIUS = 3.0      # detections within this distance are the same candidate
CONF_CLAMP = (0.02, 0.98)
BASE_RATE = 0.35        # calibration prior: two mid-confidence hits shouldn't confirm;
                        # a genuine glimpse + a close re-observation should
MISS_LLR = -2.5         # log-odds added when an agent looks and sees nothing (strong:
                        # one clean miss dismisses a phantom)


def _logit(p: float) -> float:
    lo, hi = CONF_CLAMP
    p = min(hi, max(lo, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


_LOGIT_BASE = _logit(BASE_RATE)


def binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


# ------------------------------------------------------------------ candidate

@dataclass
class Candidate:
    id: int
    x: float
    y: float
    # per-agent best view: agent_id -> (confidence, bearing, x, y)
    views: dict = field(default_factory=dict)
    misses: set = field(default_factory=set)   # agents that looked & saw nothing
    status: str = "candidate"                  # candidate | confirmed | dismissed
    scored: bool = False                       # trust already credited/debited?

    def logodds(self) -> float:
        # Bayesian fusion of independent observations against the base rate:
        #   L = logit(prior) + Σ_views [logit(c) - logit(prior)] + Σ_miss MISS_LLR
        # Each detection is evidence *relative to the base rate*, so two faint but
        # genuine looks reinforce (0.41 + 0.55 -> ~0.88) instead of averaging away.
        L = _LOGIT_BASE
        for (c, _, _, _) in self.views.values():
            L += _logit(c) - _LOGIT_BASE
        L += len(self.misses) * MISS_LLR
        return L

    def prob(self) -> float:
        return _sigmoid(self.logodds())

    def n_contributors(self) -> int:
        return len(self.views) + len(self.misses)

    def primary_bearing(self) -> float:
        """Bearing of the strongest existing view — the angle a re-observation
        should try to differ from."""
        best = None
        for (c, b, _, _) in self.views.values():
            if best is None or c > best[0]:
                best = (c, b)
        return best[1] if best else 0.0

    def recompute_centroid(self):
        if not self.views:
            return
        xs = [vx for (_, _, vx, _) in self.views.values()]
        ys = [vy for (_, _, _, vy) in self.views.values()]
        self.x = sum(xs) / len(xs)
        self.y = sum(ys) / len(ys)

    def as_survivor(self) -> Survivor:
        return Survivor(
            id=self.id, x=round(self.x, 2), y=round(self.y, 2),
            confidence=round(self.prob(), 3), status=self.status,
            n_views=len(self.views), modality="vision",
        )


# ------------------------------------------------------------------ store

class CandidateStore:
    """Holds all candidates and fuses detections into them. Shared belief in v1
    (models 'agents gossip detections over the mesh'); per-agent belief with
    explicit MAP_DELTA gossip is a later refinement — control is already
    decentralised via the auction, which is what makes this a swarm."""

    # trust: an agent whose detections keep getting dismissed is a faulty sensor.
    QUAR_TRUST = 0.35       # below this trust -> quarantine
    QUAR_MIN_SAMPLES = 3    # need at least this many resolved claims first
    TRUST_PRIOR_GOOD = 3.0  # Beta prior: start optimistic (~0.75)
    TRUST_PRIOR_BAD = 1.0

    def __init__(self):
        self.cands: dict[int, Candidate] = {}
        self._next_id = 0
        # per-agent resolved-claim tallies for the trust score
        self.agent_good: dict[int, int] = {}   # views that ended up confirmed
        self.agent_bad: dict[int, int] = {}    # views that ended up dismissed
        self.quarantined: set[int] = set()

    # -------------------------------------------------------------- trust

    def trust(self, agent_id: int) -> float:
        g = self.agent_good.get(agent_id, 0)
        b = self.agent_bad.get(agent_id, 0)
        return ((g + self.TRUST_PRIOR_GOOD) /
                (g + b + self.TRUST_PRIOR_GOOD + self.TRUST_PRIOR_BAD))

    def _score_agents(self, cand: Candidate):
        """Credit/debit the agents that claimed a detection on this candidate, once
        it resolves. Agents that MISSED (looked, saw nothing) are never penalised —
        only false *claims* cost trust."""
        good = cand.status == "confirmed"
        for aid in cand.views:
            tally = self.agent_good if good else self.agent_bad
            tally[aid] = tally.get(aid, 0) + 1

    def _refresh_quarantine(self):
        for aid in set(self.agent_good) | set(self.agent_bad):
            samples = self.agent_good.get(aid, 0) + self.agent_bad.get(aid, 0)
            if samples >= self.QUAR_MIN_SAMPLES and self.trust(aid) < self.QUAR_TRUST:
                self.quarantined.add(aid)
            elif aid in self.quarantined and self.trust(aid) >= self.QUAR_TRUST:
                self.quarantined.discard(aid)   # rehabilitated if it recovers

    # -------------------------------------------------------------- ingest

    def _nearest(self, x: float, y: float) -> Candidate | None:
        best = None
        for c in self.cands.values():
            if c.status == "dismissed":
                continue
            d = math.hypot(c.x - x, c.y - y)
            if d <= MERGE_RADIUS and (best is None or d < best[0]):
                best = (d, c)
        return best[1] if best else None

    def ingest(self, det):
        """Fold one detection into the matching candidate (or create one). Keeps
        only each agent's *best* view, so repeated looks from one agent don't
        stack into a false confirmation. Detections from a QUARANTINED agent are
        dropped — a distrusted sensor can't poison the shared map."""
        if det.agent_id in self.quarantined:
            return None
        c = self._nearest(det.x, det.y)
        if c is None:
            c = Candidate(id=self._next_id, x=det.x, y=det.y)
            self.cands[self._next_id] = c
            self._next_id += 1
        prev = c.views.get(det.agent_id)
        if prev is None or det.confidence > prev[0]:
            c.views[det.agent_id] = (det.confidence, det.bearing, det.x, det.y)
        c.misses.discard(det.agent_id)   # it did see something after all
        c.recompute_centroid()
        return c

    def ingest_many(self, dets):
        for d in dets:
            self.ingest(d)

    def register_miss(self, cand_id: int, agent_id: int):
        """An agent serviced a re-observation but produced no supporting view —
        evidence *against* the candidate."""
        c = self.cands.get(cand_id)
        if c is not None and agent_id not in c.views:
            c.misses.add(agent_id)

    # -------------------------------------------------------------- status

    def update_statuses(self):
        for c in self.cands.values():
            if c.status == "dismissed":
                continue                     # dismissal is sticky (hive-mind memory)
            p = c.prob()
            if p >= CONFIRM_P and len(c.views) >= MIN_CONFIRM_AGENTS:
                c.status = "confirmed"
            elif p <= DISMISS_P and (len(c.misses) >= 1 or len(c.views) >= 2):
                # only dismiss with actual evidence of absence — never kill a real
                # survivor off a single faint glimpse (that just stays a candidate
                # awaiting a second look).
                c.status = "dismissed"
            else:
                c.status = "candidate"
            # credit/debit trust once, when a candidate first resolves
            if c.status in ("confirmed", "dismissed") and not c.scored:
                self._score_agents(c)
                c.scored = True
        self._refresh_quarantine()

    # -------------------------------------------------------------- queries

    def reobserve_candidates(self) -> list[Candidate]:
        """Uncertain candidates that warrant a second look."""
        return [c for c in self.cands.values() if c.status == "candidate"]

    def survivors(self) -> list[dict]:
        """For the dashboard: confirmed + still-open candidates (skip dismissed)."""
        return [c.as_survivor().as_dict()
                for c in self.cands.values() if c.status != "dismissed"]

    def counts(self) -> dict:
        out = {"confirmed": 0, "candidate": 0, "dismissed": 0}
        for c in self.cands.values():
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def prob_grid(self, grid_w: int, grid_h: int) -> list[list[float]]:
        """Splat each live candidate as a small gaussian bump for the heatmap."""
        grid = [[0.0] * grid_w for _ in range(grid_h)]
        sigma2 = 2.0
        for c in self.cands.values():
            if c.status == "dismissed":
                continue
            p = c.prob()
            cx, cy = int(c.x), int(c.y)
            for gy in range(max(0, cy - 3), min(grid_h, cy + 4)):
                for gx in range(max(0, cx - 3), min(grid_w, cx + 4)):
                    d2 = (gx - c.x) ** 2 + (gy - c.y) ** 2
                    v = p * math.exp(-d2 / (2 * sigma2))
                    if v > grid[gy][gx]:
                        grid[gy][gx] = round(v, 3)
        return grid
