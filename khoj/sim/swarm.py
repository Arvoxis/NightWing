"""KHOJ — the Swarm (the decentralized brain).  ** This is the reference A ports to C. **

Each Agent holds its own private belief grid and computes its own bids. The
`Swarm` object models the ESP-NOW mesh: the shared task pool, RF samples, and
Hive-Mind dismissals that in the real system propagate as broadcasts. On the
ESP32 backend these shared structures are replaced by `esp_now` broadcast +
each board's local copy — but the DECISION logic (the bid function and the
winner rule) is exactly what lives here.

One auction, four task types, one currency (expected survivors / second):
    FRONTIER    routine search of the belief map        (implicit, per-agent)
    REOBSERVE   second look at an uncertain sighting     (auctioned)
    CONFIRM_RF  converge on a hidden phone               (auctioned, highest value)
    RELAY       reposition as a comms relay              (FLEX — stubbed below)
"""
import math

# --- tunables (mirror these as #defines in the C port) ----------------------
PARAMS = dict(
    confirm_hi=0.80, confirm_lo=0.15,   # fuse >hi => CONFIRM; <lo => DISMISS
    tau=60.0,        # survival-decay time constant (s) — urgency, evaluated at arrival
    eps=0.1, t_obs=0.5,
    theta0=60.0,     # viewpoint-diversity angle constant (deg)
    C_FN=100.0,      # cost of a false negative relative to a false positive
    U_rf=500.0,      # RF task base value (highest priority)
    rf_gate=-68.0,   # dBm above which an RF sample counts as "strong"
    belief_drain=0.15,   # multiply cell belief by this after searching it
)


def _clamp01(p):
    return min(max(p, 1e-6), 1 - 1e-6)

def binary_entropy(p):
    p = _clamp01(p)
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))

def logit(p):
    p = _clamp01(p)
    return math.log(p / (1 - p))

def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-max(-40, min(40, x))))

def ang_diff(a, b):
    """smallest absolute difference between two bearings (deg)."""
    return abs(((a - b + 180) % 360) - 180)


class Agent:
    """Private state only. No agent can read another's belief grid."""
    def __init__(self, aid, size):
        self.id = aid
        self.size = size
        self.pos = (1.0, 1.0)
        self.t = 0.0
        # belief that a cell still hides an un-found victim (UNNORMALISED on purpose:
        # bidding only needs relative values, so we never renormalise — that also
        # kills the distributed-consensus headache on the mesh).
        self.belief = [[1.0] * size for _ in range(size)]
        self.state = "SEARCH"
        self.cur_task = None


class Swarm:
    def __init__(self, world, n_agents, params=None):
        self.world = world
        self.p = dict(PARAMS, **(params or {}))
        self.agents = [Agent(i, world.size) for i in range(n_agents)]
        # --- shared "mesh state" (broadcasts in the real system) ---
        self.rf_samples = []      # [(x, y, rssi)]
        self.tasks = {}           # tid -> task dict
        self.dismissed = []       # [(x, y)]  <- Hive-Mind memory
        self.confirmed = []       # [(x, y, kind)]
        self.next_tid = 1
        self.first_victim_t = None

    # ======================================================================
    #  one mesh round == one tick
    # ======================================================================
    def tick(self):
        w = self.world
        for a in self.agents:                       # bodies -> agents' self-knowledge
            b = w.bodies[a.id]
            a.pos, a.t = (b["x"], b["y"]), w.t
        for a in self.agents:                       # sense, update belief, spawn tasks
            self.ingest(a, w.sense(a.id))
        self.update_rf_task()                       # cooperative RF localization
        goals = self.assign()                       # THE AUCTION
        w.step(goals)                               # physics
        return goals

    # ----------------------------------------------------------------------
    def ingest(self, a, pkt):
        x, y = pkt["x"], pkt["y"]
        # drain belief over the searched footprint (multiplicative, no renormalise)
        r = int(self.world.sensor_r) + 1
        cx, cy = int(round(x)), int(round(y))
        rr = (self.world.sensor_r + 0.5) ** 2
        for gx in range(max(0, cx - r), min(self.size, cx + r + 1)):
            for gy in range(max(0, cy - r), min(self.size, cy + r + 1)):
                if (gx - x) ** 2 + (gy - y) ** 2 <= rr:
                    a.belief[gx][gy] *= self.p["belief_drain"]
        self.rf_samples.append((x, y, pkt["rssi"]))     # broadcast RF sample
        if pkt["det"]:
            self.handle_detection(a, pkt["det"], pkt)

    @property
    def size(self):
        return self.world.size

    def handle_detection(self, a, det, pkt):
        dx, dy, conf = det["x"], det["y"], det["conf"]
        # Hive-Mind: if the swarm already dismissed this spot, don't look again
        if any((px - dx) ** 2 + (py - dy) ** 2 < 4 for px, py in self.dismissed):
            return
        if any((px - dx) ** 2 + (py - dy) ** 2 < 4 for px, py, _ in self.confirmed):
            return
        # bearing FROM the victim TO the observer (the viewing angle)
        bearing = math.degrees(math.atan2(pkt["y"] - dy, pkt["x"] - dx))
        tid = next((k for k, t in self.tasks.items()
                    if t["type"] == "REOBSERVE"
                    and (t["x"] - dx) ** 2 + (t["y"] - dy) ** 2 < 4), None)

        if conf >= self.p["confirm_hi"]:            # confident single look
            self.confirm(dx, dy, "VISUAL")
            if tid:
                del self.tasks[tid]
            return
        if conf <= self.p["confirm_lo"]:            # too weak to bother
            return
        # uncertain band -> spawn / fuse a REOBSERVE task
        if tid is None:
            self.tasks[self.next_tid] = {"type": "REOBSERVE", "x": dx, "y": dy,
                                         "logodds": logit(conf), "bearings": [bearing]}
            self.next_tid += 1
        else:
            t = self.tasks[tid]
            # only fuse genuinely NEW viewpoints (conditional-independence by design)
            if all(ang_diff(bearing, bb) > 20 for bb in t["bearings"]):
                t["logodds"] += logit(conf)
                t["bearings"].append(bearing)
                fused = sigmoid(t["logodds"])
                if fused >= self.p["confirm_hi"]:
                    self.confirm(dx, dy, "VISUAL")
                    del self.tasks[tid]
                elif fused <= self.p["confirm_lo"]:
                    self.dismiss(dx, dy)            # <- shared Hive-Mind dismissal
                    del self.tasks[tid]

    def confirm(self, x, y, kind):
        self.confirmed.append((x, y, kind))
        for v in self.world.victims:
            if not v["found"] and (v["x"] - x) ** 2 + (v["y"] - y) ** 2 < 4:
                v["found"] = True
        if self.first_victim_t is None:
            self.first_victim_t = self.world.t

    def dismiss(self, x, y):
        self.dismissed.append((x, y))

    # ----------------------------------------------------------------------
    #  cooperative RF localization: pool shared samples -> estimate -> converge
    # ----------------------------------------------------------------------
    def update_rf_task(self):
        rf_tid = next((k for k, t in self.tasks.items()
                       if t["type"] == "CONFIRM_RF"), None)
        s = self.world.rf_source
        if s["found"]:
            if rf_tid:
                del self.tasks[rf_tid]
            return
        strong = [(x, y, r) for x, y, r in self.rf_samples if r > self.p["rf_gate"]]
        if len(strong) < 2:
            return
        # signal-weighted centroid == a cheap, noise-robust gradient estimate.
        # (weight grows with amplitude, so the closest/strongest samples pull hardest)
        wsum = ex = ey = 0.0
        for x, y, r in strong:
            w = 10 ** ((r + 100) / 20.0)
            wsum += w; ex += w * x; ey += w * y
        ex, ey = ex / wsum, ey / wsum
        if rf_tid is None:
            self.tasks[self.next_tid] = {"type": "CONFIRM_RF", "x": ex, "y": ey}
            self.next_tid += 1
        else:
            self.tasks[rf_tid]["x"], self.tasks[rf_tid]["y"] = ex, ey
        # an agent reaching the true source with a strong signal == RF confirm
        for b in self.world.bodies:
            if math.hypot(b["x"] - s["x"], b["y"] - s["y"]) < 1.5:
                s["found"] = True
                self.confirmed.append((s["x"], s["y"], "RF"))
                if self.first_victim_t is None:
                    self.first_victim_t = self.world.t
                tid2 = next((k for k, t in self.tasks.items()
                             if t["type"] == "CONFIRM_RF"), None)
                if tid2:
                    del self.tasks[tid2]
                break

    # ----------------------------------------------------------------------
    #  THE AUCTION  (sequential single-item; highest bid wins, tie -> lowest id)
    # ----------------------------------------------------------------------
    def bid(self, a, t):
        ax, ay = a.pos
        d = math.hypot(t["x"] - ax, t["y"] - ay)
        c = d / self.world.speed + self.p["t_obs"]          # cost is TIME, not distance
        if t["type"] == "CONFIRM_RF":
            U = self.p["U_rf"]
        elif t["type"] == "REOBSERVE":
            p = sigmoid(t["logodds"])
            bear = math.degrees(math.atan2(ay - t["y"], ax - t["x"]))
            dth = min(ang_diff(bear, bb) for bb in t["bearings"])
            div = 1 - math.exp(-dth / self.p["theta0"])     # viewpoint diversity
            U = binary_entropy(p) * self.p["C_FN"] * div
        else:
            U = 0.0
        # survival decay evaluated at ARRIVAL time -> distant tasks penalised hard
        return U * math.exp(-(a.t + c) / self.p["tau"]) / (c + self.p["eps"])

    def assign(self):
        goals = [None] * len(self.agents)
        tasks = list(self.tasks.items())
        bids = {a.id: {tid: self.bid(a, t) for tid, t in tasks} for a in self.agents}
        taken = set()

        def maxbid(tid):
            return max((bids[a.id][tid] for a in self.agents), default=0.0)

        # sequential greedy: highest-value tasks claim their best free agent first
        for tid, t in sorted(tasks, key=lambda kv: -maxbid(kv[0])):
            for a in sorted(self.agents, key=lambda a: (-bids[a.id][tid], a.id)):
                if a.id in taken or bids[a.id][tid] <= 0:
                    continue
                taken.add(a.id)
                goals[a.id] = (t["x"], t["y"])
                a.state, a.cur_task = t["type"], tid
                break

        for a in self.agents:                               # everyone else -> frontier
            if goals[a.id] is None:
                goals[a.id] = self.frontier(a, goals)
                a.state, a.cur_task = "SEARCH", None
        return goals

    def frontier(self, a, goals):
        """Best unsearched high-belief cell, repelled from peers' goals so the
        swarm spreads out without a per-cell auction."""
        ax, ay = a.pos
        peers = [g for i, g in enumerate(goals) if g is not None and i != a.id]
        best, best_score = None, -1.0
        for gx in range(0, self.size, 2):
            for gy in range(0, self.size, 2):
                b = a.belief[gx][gy]
                if b < 0.2:
                    continue
                score = b * math.exp(-math.hypot(gx - ax, gy - ay) / 12.0)
                for px, py in peers:
                    if (px - gx) ** 2 + (py - gy) ** 2 < 25:
                        score *= 0.3
                if score > best_score:
                    best_score, best = score, (float(gx), float(gy))
        return best or (ax, ay)

    # TODO(flex): RELAY task type — when a peer's link to base degrades, an agent
    # bids to reposition on the midpoint to keep the mesh connected (SDG 9).
    # TODO(flex): rescue-route — on CONFIRM, compute a ground path to the survivor.
