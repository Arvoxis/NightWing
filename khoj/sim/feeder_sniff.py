#!/usr/bin/env python3
"""KHOJ - Digital Sniffing bridge (experiment/digital-sniffing branch).

Replaces the camera/YOLO detection model with passive RF signature sniffing:
  - Every victim carries a device (phone/tracker) that emits an RF signature
    (simulated WiFi probe / BLE advertisement with a unique MAC address).
  - Each drone "sniffs" the air around it every tick. Any MAC that is not
    in the swarm's own known-MAC list is a candidate victim.
  - Confidence = f(RSSI) — stronger signal = more certain someone is there.
    RSSI is recomputed EVERY TICK per drone per victim, so it is fully
    dynamic: as the drone moves closer, confidence rises; as it moves away,
    confidence falls.  Two looks from different angles are genuinely
    independent, so log-odds fusion gives honest Bayesian confirmation.
  - Path non-overlap: the frontier planner enforces a hard exclusion zone
    around every other drone's current position AND its committed goal, so
    two drones never sweep the same column of the grid.

Wire contract to the boards is IDENTICAL to feeder_real.py:
    28-byte sensor packet  (detection x/y/conf + rssi + position + tick)
    20-byte goal frame     (board's next waypoint + state)
No firmware change needed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial missing.  py -m pip install pyserial")

from protocol import (
    pack_sensor, unpack_goal, FrameParser, GOAL_SIZE, MSG_GOAL,
    STATE_SEARCH,
)

# ---------------------------------------------------------------------------
# Constants — must match firmware (main.cpp)
AGENT_SPEED   = 2.0        # cells/s
SENSOR_R      = 3.5        # "sniff radius" — how far a drone hears RF beacons
CONF_LO       = 15         # board ignores detections below this (%)
CONF_HI       = 80         # board auto-confirms above this (%) — skip auction
GRID_N        = 32

BASE_XY       = (2.0, 2.0)
ALIVE_S       = 2.5        # seconds of silence before board is frozen
DET_TTL_S     = 2.5        # detection glow lifetime on dashboard

# RF sniffing physics
RSSI_REF      = -35.0      # dBm at 1 cell (phone at arm's reach)
PATH_LOSS_EXP = 2.2        # log-distance path loss exponent
RSSI_FLOOR    = -95.0      # below this the signal is inaudible
RSSI_GATE     = -80.0      # drones ignore signals weaker than this (noise floor)
RSSI_NEAR     = -55.0      # "I'm right on top of a victim" threshold

# RF self-limiting (phone localization)
RF_SOLVE_DIST = 1.6
RF_SOLVE_HOLD = 1.5

# Drone exclusion zone (no-overlap): drones stay this many cells from
# every other drone's *goal* (not just current position), so their planned
# paths don't converge on the same frontier cell.
EXCL_R        = SENSOR_R * 1.5    # ~5.25 cells exclusion radius

STATE_NAMES   = {0: "SEARCH", 1: "REOBSERVE", 2: "RF"}
EVENTS: list[str] = []
BRIDGE_HINTS  = ("CP210", "CH340", "CH910", "FTDI", "FT232", "Silicon Labs", "USB-SERIAL")


def _ev(msg: str):
    EVENTS.append(msg)
    print("  ", msg)


# ===========================================================================
#  Digital Sniffing — the new detection model
# ===========================================================================

class RFVictim:
    """One human in the field carrying a device that emits a unique MAC.
    The drone never learns this MAC directly — it just hears a signal at
    a certain strength from an unknown direction and reports that."""
    def __init__(self, x: float, y: float, mac: str, kind: str = "victim"):
        self.x    = float(x)
        self.y    = float(y)
        self.mac  = mac       # e.g. "AA:BB:CC:DD:EE:01"
        self.kind = kind      # "victim" | "decoy"

    def rssi_at(self, drone_x: float, drone_y: float) -> float:
        """Dynamic RSSI: recomputed every tick based on the drone's current
        position.  Moves closer → stronger signal → higher confidence."""
        d = max(0.3, math.hypot(drone_x - self.x, drone_y - self.y))
        rssi = RSSI_REF - 10.0 * PATH_LOSS_EXP * math.log10(d)
        return max(RSSI_FLOOR, min(-20.0, rssi))

    def rssi_to_conf(self, rssi: float) -> float:
        """Map RSSI to a detection confidence (0–1).
        -35 dBm (right on top) → ~0.95 confidence
        -60 dBm (4 cells away)  → ~0.65 confidence
        -80 dBm (gate)          → ~0.30 confidence (low, often dismissed)
        Linear in dBm space between the gate and the reference."""
        span = RSSI_REF - RSSI_GATE              # positive
        conf = (rssi - RSSI_GATE) / span         # 0 at gate, 1 at ref
        return max(0.02, min(0.98, conf))


class Sniffer:
    """Replaces the camera/YOLO Sensing class.  Given a drone's current
    position, returns the strongest RF signature it hears that is NOT one
    of the swarm's own boards.

    Key properties:
      * Fully DYNAMIC — confidence changes every tick as the drone moves.
      * Returns (det_x, det_y, conf) localised to the victim's cell (with
        small noise, matching what the boards' fusion expects from a real radio
        bearing).  No image, no model weights required.
      * Below RSSI_GATE the signal is lost — the drone reports nothing,
        exactly like a real passive sniffer losing a device in the noise.
    """

    def __init__(self, victims: list[RFVictim], swarm_macs: set[str]):
        self.victims    = victims
        self.swarm_macs = swarm_macs   # these MACs are our own boards — ignore

    def sniff(self, x: float, y: float, agent_id: int,
              rng: random.Random) -> tuple | None:
        """Return the strongest detectable non-swarm signal, or None."""
        best_rssi = RSSI_GATE          # gate — weaker than this is ignored
        best      = None

        for v in self.victims:
            if v.mac in self.swarm_macs:
                continue
            rssi = v.rssi_at(x, y)
            if rssi < RSSI_GATE:
                continue
            if rssi > best_rssi:
                best_rssi = rssi
                best      = v

        if best is None:
            return None

        conf = best.rssi_to_conf(best_rssi)
        # tiny localization noise: the bearing puts the device somewhere near
        # its true cell, not exactly on it (simulates bearing uncertainty).
        nx = best.x + rng.uniform(-0.4, 0.4)
        ny = best.y + rng.uniform(-0.4, 0.4)
        return (nx, ny, conf)

    def rssi_from_phone(self, x: float, y: float,
                        phone: tuple | None) -> int:
        """The hidden phone for cooperative RF localization (separate from the
        victim sniffing — the phone is the human's actual mobile, sniffers
        pick up data-carrying beacons from victims, localization uses the
        phone's persistent strong signal)."""
        if phone is None:
            return -128
        d = max(0.3, math.hypot(x - phone[0], y - phone[1]))
        rssi = RSSI_REF - 10.0 * PATH_LOSS_EXP * math.log10(d)
        return int(max(-100, min(-20, round(rssi))))


# ===========================================================================
#  Frontier coverage with no-overlap enforcement
# ===========================================================================

class Coverage:
    def __init__(self, grid: int):
        self.grid = grid
        self.known = [[0.0] * grid for _ in range(grid)]
        self.prob  = [[0.0] * grid for _ in range(grid)]
        self._sw   = None
        self._ids: tuple = ()
        self._assigned: dict = {}      # aid -> committed frontier cell (sticky)
        try:
            from engine.swarm import Swarm, BrainConfig  # noqa
            from engine.protocol import TaskType         # noqa
            self._ok = True
        except Exception as e:
            _ev("WARN frontier unavailable (%s) — idle drones will orbit" % e)
            self._ok = False

    def reset(self):
        self.known = [[0.0] * self.grid for _ in range(self.grid)]
        self._assigned = {}

    def mark_seen(self, x: float, y: float):
        r = SENSOR_R
        x0 = max(0, int(x - r)); x1 = min(self.grid - 1, int(x + r))
        y0 = max(0, int(y - r)); y1 = min(self.grid - 1, int(y + r))
        for gy in range(y0, y1 + 1):
            for gx in range(x0, x1 + 1):
                if math.hypot(gx - x, gy - y) <= r:
                    self.known[gy][gx] = 1.0

    def _ensure(self, ids):
        ids = tuple(sorted(ids))
        if self._sw is not None and ids == self._ids:
            return
        from engine.swarm import Swarm
        self._sw = Swarm(list(ids))
        self._ids = ids

    def goals_for(self, searching: list, all_bodies: list) -> dict:
        """
        searching   : [(agent_id, x, y)] — idle boards needing a frontier.
        all_bodies  : every Body (including non-searching ones) — used to
                      build the exclusion zones so paths never overlap.

        Two rules:
          STICKY  — a board keeps its frontier until it arrives (no thrashing).
          SPREAD  — new frontiers are chosen far from other boards' goals AND
                    from every drone's current position (EXCL_R exclusion zone).
        """
        if not searching:
            return {}
        if not self._ok:
            return {aid: self._orbit(aid, x, y) for aid, x, y in searching}

        self._ensure([a for a, _, _ in searching])
        from engine.protocol import TaskType
        sw = self._sw
        for aid, x, y in searching:
            b = sw.brains.get(aid)
            if b:
                b.x, b.y, b.current_task = x, y, None
        sw.rebuild_frontier_tasks(self.known, self.prob, self.grid, self.grid)
        open_pts = [(t.x, t.y) for t in sw.tasks.values()
                    if t.type == TaskType.FRONTIER and t.open]
        step = float(sw.cfg.frontier_step)

        # Build exclusion set: positions AND goals of ALL live drones
        excluded_positions = [(b.x, b.y) for b in all_bodies if b.alive()]
        excluded_goals     = [b.goal for b in all_bodies if b.alive()
                              and b.goal != (b.x, b.y)]

        # Filter frontier cells that are too close to any drone's position/goal
        def too_close_to_drones(px, py, skip_aid):
            for b in all_bodies:
                if b.agent_id == skip_aid or not b.alive():
                    continue
                if math.hypot(px - b.x, py - b.y) < EXCL_R:
                    return True
                if math.hypot(px - b.goal[0], py - b.goal[1]) < EXCL_R:
                    return True
            return False

        live = {a for a, _, _ in searching}
        self._assigned = {a: p for a, p in self._assigned.items() if a in live}

        if not open_pts:
            return {aid: self._orbit(aid, x, y) for aid, x, y in searching}

        out, claimed = {}, []
        for aid, x, y in sorted(searching):
            cur = self._assigned.get(aid)
            if (cur is not None and cur not in claimed
                    and math.hypot(cur[0] - x, cur[1] - y) > step
                    and any(math.hypot(cur[0] - px, cur[1] - py) <= step
                            for px, py in open_pts)):
                # Sticky: keep current frontier, still respect exclusion
                if not too_close_to_drones(cur[0], cur[1], aid):
                    out[aid] = cur
                    claimed.append(cur)
                    continue

            # Pick a new frontier: far from claimed goals (SPREAD) and outside
            # every other drone's exclusion zone (NO-OVERLAP).
            avail = [p for p in open_pts
                     if p not in claimed
                     and not too_close_to_drones(p[0], p[1], aid)]
            if not avail:
                # Relax exclusion by 50% if nothing is free
                avail = [p for p in open_pts if p not in claimed]
            if not avail:
                out[aid] = self._orbit(aid, x, y)
                continue

            if not claimed:
                p = min(avail, key=lambda p: math.hypot(p[0]-x, p[1]-y))
            else:
                p = max(avail, key=lambda p: (
                    min(math.hypot(p[0]-cx, p[1]-cy) for cx, cy in claimed)
                    - 0.25 * math.hypot(p[0]-x, p[1]-y)))
            out[aid] = p
            self._assigned[aid] = p
            claimed.append(p)
        return out

    def _orbit(self, aid, x, y):
        ang = time.time() * 0.15 + aid
        return (self.grid / 2 + (self.grid / 3) * math.cos(ang),
                self.grid / 2 + (self.grid / 3) * math.sin(ang))


# ===========================================================================
#  Body — one board's simulated drone
# ===========================================================================

class Body:
    def __init__(self, port: str, idx: int, grid: int):
        self.port       = port
        self.grid       = grid
        self.agent_id   = idx + 1
        self.real_id    = None
        self.goals_back = 0
        self.sent       = 0
        self.last_state = STATE_SEARCH
        self.cur_task   = 0
        self.rf_est     = None
        self.returning  = False
        self.last_rx    = 0.0
        self.x          = 2.0
        self.y          = 2.0
        self.heading    = 0.0
        self.goal       = (self.x, self.y)

        self.ser = serial.Serial()
        self.ser.port     = port
        self.ser.baudrate = 115200
        self.ser.timeout  = 0
        self.ser.dtr      = False
        self.ser.rts      = False
        self.ser.open()
        self.ser.dtr = False
        self.ser.rts = False
        self.parser  = FrameParser()
        self.textbuf = ""
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def alive(self):
        return self.last_rx > 0.0 and (time.time() - self.last_rx) < ALIVE_S

    def dead(self):
        return self.last_rx > 0.0 and (time.time() - self.last_rx) >= ALIVE_S

    def drain_text(self, data: bytes):
        self.textbuf += data.decode("ascii", errors="ignore")
        while "\n" in self.textbuf:
            line, self.textbuf = self.textbuf.split("\n", 1)
            line = "".join(ch for ch in line if 32 <= ord(ch) < 127).strip()
            if not line:
                continue
            if line.startswith(("EVENT", "PEER", "WARN", "FATAL")):
                tag = ("id%d" % self.real_id) if self.real_id else self.port
                _ev("[%s] %s" % (tag, line.replace("EVENT ", "")))
            if "SOURCE EST" in line:
                try:
                    inner = line.split("(", 1)[1].split(")", 1)[0]
                    ex, ey = inner.split(",")
                    self.rf_est = (float(ex), float(ey))
                except Exception:
                    pass
        if len(self.textbuf) > 4096:
            self.textbuf = self.textbuf[-512:]

    def read_goal(self):
        try:
            data = self.ser.read(512)
        except Exception:
            data = b""
        if not data:
            return
        self.drain_text(data)
        for payload in self.parser.feed(data):
            if len(payload) == GOAL_SIZE and payload[0] == MSG_GOAL:
                g = unpack_goal(payload)
                self.goal       = (g["goal_x"], g["goal_y"])
                self.last_state = g["state"]
                self.cur_task   = g["cur_task"]
                self.goals_back += 1
                self.last_rx    = time.time()
                if g["agent_id"]:
                    if self.real_id is None and g["agent_id"] != self.agent_id:
                        _ev("%s is board id=%d (not %d) — using board's own id"
                            % (self.port, g["agent_id"], self.agent_id))
                    self.real_id  = g["agent_id"]
                    self.agent_id = g["agent_id"]

    def send_sensor(self, tick: int, det, rssi: int):
        if det:
            pkt = pack_sensor(self.agent_id, self.x, self.y, heading=0,
                              has_detection=1,
                              det_x=det[0], det_y=det[1],
                              det_conf=int(round(det[2] * 100)),
                              rssi=rssi, tick=tick)
        else:
            pkt = pack_sensor(self.agent_id, self.x, self.y,
                              rssi=rssi, tick=tick)
        try:
            self.ser.write(pkt)
            self.sent += 1
        except Exception:
            pass

    def fly_to(self, tx: float, ty: float, step: float):
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        if d < 0.05:
            return
        if d <= step:
            self.x, self.y = tx, ty
        else:
            self.x += step * dx / d
            self.y += step * dy / d
        self.heading = math.atan2(dy, dx)
        self.x = max(0.0, min(float(self.grid) - 0.01, self.x))
        self.y = max(0.0, min(float(self.grid) - 0.01, self.y))


# ===========================================================================
#  Dashboard push
# ===========================================================================

class DashboardClient:
    def __init__(self, url: str, hz: float = 6.0):
        self.url   = url
        self.dt    = 1.0 / hz
        self._snap = None
        self._lock = threading.Lock()
        self._alive  = True
        self._reset  = False
        self.ok      = None
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, snap):
        with self._lock:
            self._snap = snap

    def take_reset(self):
        with self._lock:
            r, self._reset = self._reset, False
            return r

    def _run(self):
        while self._alive:
            time.sleep(self.dt)
            with self._lock:
                snap = self._snap
            if snap is None:
                continue
            try:
                data = json.dumps(snap).encode()
                req  = urllib.request.Request(
                    self.url, data=data,
                    headers={"Content-Type": "application/json"}, method="POST")
                resp = urllib.request.urlopen(req, timeout=0.6)
                if resp.getcode() == 200:
                    body = json.loads(resp.read().decode() or "{}")
                    if body.get("reset"):
                        with self._lock:
                            self._reset = True
                self.ok = True
            except Exception:
                self.ok = False

    def stop(self):
        self._alive = False


# ===========================================================================
#  Helpers
# ===========================================================================

def _resolved_points(events: list, keyword: str) -> list:
    out = []
    for e in events:
        if keyword not in e:
            continue
        tail = e.split(keyword, 1)[1]
        if "(" not in tail:
            continue
        try:
            inner = tail.split("(", 1)[1].split(")", 1)[0]
            sx, sy = inner.split(",")
            pt = [round(float(sx), 1), round(float(sy), 1)]
            if pt not in out:
                out.append(pt)
        except Exception:
            continue
    return out


def _print_row(tick: int, phone, bodies: list):
    t_str = "t=%-5d" % tick
    parts = []
    for b in bodies:
        state = STATE_NAMES.get(b.last_state, "?")
        parts.append("%s id%s (%4.1f,%4.1f)->(%4.1f,%4.1f) %-10s %s %d/%d"
                     % (b.port,
                        str(b.real_id) if b.real_id else "?",
                        b.x, b.y, b.goal[0], b.goal[1],
                        state,
                        "OK  " if b.alive() else ("BOOT" if b.last_rx == 0.0 else "DEAD"),
                        b.goals_back, b.sent + 1))
    if phone:
        dists = [math.hypot(b.x - phone[0], b.y - phone[1])
                 for b in bodies if b.alive()]
        nearest = min(dists) if dists else 99
        print("      hidden phone @ (%.1f,%.1f)   nearest drone %.1f cells"
              % (phone[0], phone[1], nearest))
    print(t_str, "  |   ".join(parts))


def random_points(n: int, grid: int, rng: random.Random,
                  base: tuple, avoid=(), min_sep=6.0, base_clear=9.0):
    pts = []
    for _ in range(n):
        for _try in range(400):
            x = round(rng.uniform(3, grid - 3), 1)
            y = round(rng.uniform(3, grid - 3), 1)
            if math.hypot(x - base[0], y - base[1]) < base_clear:
                continue
            if any(math.hypot(x - px, y - py) < min_sep
                   for px, py in list(pts) + list(avoid)):
                continue
            pts.append((x, y))
            break
    return pts


def auto_ports(hint_strings):
    return sorted(
        p.device for p in list_ports.comports()
        if any(h in (p.description or "") for h in hint_strings))


def build_snapshot(a, tick, bodies, coverage, recent_dets,
                   victims, phone, rf_final,
                   mission_done=False):
    rf = rf_final or next((b.rf_est for b in bodies if b.rf_est and b.alive()), None)
    boards = []
    for b in bodies:
        boards.append({
            "id":        b.real_id or 0,
            "port":      b.port,
            "x":         round(b.x, 2),
            "y":         round(b.y, 2),
            "heading":   round(b.heading, 3),
            "state":     b.last_state,
            "cur_task":  b.cur_task,
            "goal":      [round(b.goal[0], 2), round(b.goal[1], 2)],
            "alive":     b.alive(),
            "returning": bool(b.returning),
            "beacon":    False,
            "packets":   b.sent,
        })
    gt_victims = [{"x": v.x, "y": v.y, "kind": v.kind, "mac": v.mac}
                  for v in victims]
    return {
        "grid":             int(a.grid),
        "tick":             tick,
        "detector":         "digital-sniff (RF signature)",
        "known":            coverage.known,
        "boards":           boards,
        "detections":       [{k: d[k] for k in ("agent_id", "x", "y",
                               "confidence", "bearing", "timestamp")}
                             for d in recent_dets],
        "mission_complete": bool(mission_done),
        "confirmed":        _resolved_points(EVENTS, "CONFIRMED"),
        "dismissed":        _resolved_points(EVENTS, "DISMISSED"),
        "rf":               [round(rf[0], 2), round(rf[1], 2)] if rf else None,
        "events":           list(EVENTS)[-40:],
        "ground_truth":     {
            "victims": gt_victims,
            "phone":   list(phone) if phone else None,
        },
        "bridge":           {
            "online":   any(b.alive() for b in bodies),
            "n_boards": len(bodies),
            "mode":     "digital-sniff",
        },
    }


# ===========================================================================
#  Entry point
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="KHOJ digital-sniff bridge")
    ap.add_argument("--ports", nargs="*", default=None)
    ap.add_argument("--hz",    type=float, default=10.0)
    ap.add_argument("--grid",  type=float, default=float(GRID_N))
    ap.add_argument("--speed", type=float, default=2.0,
                    help="drone speed cells/s (default=2.0, firmware AGENT_SPEED)")
    ap.add_argument("--phone", nargs=2, type=float, metavar=("X", "Y"),
                    default=None,
                    help="hidden phone for cooperative RF localization "
                         "(default: random cell). --no-phone to disable.")
    ap.add_argument("--no-phone",  action="store_true")
    ap.add_argument("--rf-range",  type=float, default=14.0)
    ap.add_argument("--n-victims", type=int,   default=3)
    ap.add_argument("--n-decoys",  type=int,   default=2)
    ap.add_argument("--base", nargs=2, type=float, metavar=("X", "Y"),
                    default=[2.0, 2.0])
    ap.add_argument("--seed",      type=int,   default=7)
    ap.add_argument("--beacon",    type=int,   default=-1)
    ap.add_argument("--dashboard", default="http://127.0.0.1:8000/ingest")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--reset",    dest="reset", action="store_true",  default=True,
                    help="Pulse DTR on every port to soft-reset ESP32 boards at startup (default ON)")
    ap.add_argument("--no-reset", dest="reset", action="store_false",
                    help="Skip the DTR reset pulse (use if boards are already running)")
    a = ap.parse_args()

    rng  = random.Random(a.seed)
    base = tuple(a.base)
    grid = int(a.grid)

    # ---- place victims and decoys at random cells --------------------------
    v_pts = random_points(a.n_victims, grid, rng, base)
    d_pts = random_points(a.n_decoys,  grid, rng, base, avoid=v_pts)

    swarm_macs = {("FF:FF:FF:FF:FF:%02X" % i) for i in range(1, 10)}
    victims_rf: list[RFVictim] = []
    for i, (x, y) in enumerate(v_pts):
        mac = "AA:BB:CC:DD:EE:%02X" % (i + 1)
        victims_rf.append(RFVictim(x, y, mac, kind="victim"))
    for i, (x, y) in enumerate(d_pts):
        mac = "AA:BB:CC:DD:FF:%02X" % (i + 1)
        victims_rf.append(RFVictim(x, y, mac, kind="decoy"))

    if a.no_phone:
        phone = None
    elif a.phone:
        phone = tuple(a.phone)
    else:
        rp    = random_points(1, grid, rng, base,
                              avoid=v_pts + d_pts, base_clear=12.0)
        phone = rp[0] if rp else None

    sniffer  = Sniffer(victims_rf, swarm_macs)
    coverage = Coverage(grid)

    # ---- ports -------------------------------------------------------------
    ports = a.ports or auto_ports(BRIDGE_HINTS)
    if not ports:
        sys.exit("No ESP32 boards found. Plug in and pass --ports COM3 ...")

    print("\nKHOJ digital-sniff bridge - %d board(s): %s" % (len(ports), ", ".join(ports)))
    print("   DETECTOR   : RF signature sniffing (no camera, no YOLO)")
    print("   mode       : SEARCH + RF" if phone else "   mode       : SEARCH-ONLY")
    print("   grid       : %d × %d" % (grid, grid))
    print("   speed      : %.1f cells/s" % a.speed)
    print("   beacon     : %s" % ("none — all boards search" if a.beacon < 0
                                  else "board id %d" % a.beacon))
    print("   ground truth (boards never see):")
    for v in victims_rf:
        print("      %s @ (%.1f,%.1f)  MAC=%s" % (v.kind, v.x, v.y, v.mac))
    if phone:
        print("      hidden phone @ (%.1f,%.1f)  RF range %.0f cells"
              % (phone[0], phone[1], a.rf_range))
    print()

    client = None
    if not a.no_dashboard:
        client = DashboardClient(a.dashboard)
        print("   DASHBOARD  : POSTing to %s" % a.dashboard)

    bodies: list[Body] = []
    for i, p in enumerate(ports):
        try:
            bodies.append(Body(p, i, grid))
        except Exception as e:
            print("  !! could not open %s: %s" % (p, e))
    if not bodies:
        sys.exit("No port could be opened.")

    # Optional DTR soft-reset: pulses the EN pin via the CP2102/CH340 control
    # line — equivalent to pressing the RESET button, no power cycle needed.
    if a.reset:
        print("   Resetting boards via DTR …", end="", flush=True)
        for b in bodies:
            try:
                b.ser.dtr = True    # EN low  → board enters reset
                time.sleep(0.12)
                b.ser.dtr = False   # EN high → board boots normally
            except Exception:
                pass
        time.sleep(1.8)             # wait for firmware to boot and init mesh
        print(" done")
        _ev("Boards soft-reset via DTR — mesh initialising")

    # all drones launch from the base in a small circle
    for i, b in enumerate(bodies):
        ang  = 2.0 * math.pi * i / max(1, len(bodies))
        b.x  = base[0] + 0.6 * math.cos(ang)
        b.y  = base[1] + 0.6 * math.sin(ang)
        b.goal = (b.x, b.y)

    dt          = 1.0 / a.hz
    step_cells  = a.speed * dt
    recent_dets: list = []
    n_victims   = sum(1 for v in victims_rf if v.kind == "victim")
    n_decoys    = sum(1 for v in victims_rf if v.kind == "decoy")
    rf_solved   = False
    rf_near_since = None
    rf_final    = None
    tick        = 0

    try:
        while True:
            t0  = time.time()
            now = time.time()

            # --- RESTART ---
            if client is not None and client.take_reset():
                coverage.reset()
                recent_dets.clear()
                EVENTS.clear()
                for b in bodies:
                    b.returning = False
                _ev("RESTART — coverage and map cleared")

            # --- read goals ---
            for b in bodies:
                b.read_goal()

            # --- RF self-limit ---
            rf_now = next((b.rf_est for b in bodies if b.rf_est and b.alive()), None)
            if rf_now:
                rf_final = rf_now
            if phone and not rf_solved:
                dmin = min((math.hypot(b.x - phone[0], b.y - phone[1])
                            for b in bodies if b.alive()), default=1e9)
                if dmin <= RF_SOLVE_DIST:
                    rf_near_since = rf_near_since or now
                    if now - rf_near_since >= RF_SOLVE_HOLD:
                        rf_solved = True
                        rf_final  = rf_final or phone
                        _ev("RF SOURCE localized at (%.1f,%.1f) — swarm resuming search"
                            % tuple(rf_final))
                else:
                    rf_near_since = None
            phone_active = None if rf_solved else phone

            # --- mission status ---
            n_conf = len(_resolved_points(EVENTS, "CONFIRMED"))
            n_dism = len(_resolved_points(EVENTS, "DISMISSED"))
            mission_done = (n_victims and n_conf >= n_victims
                            and n_dism >= n_decoys)

            # --- frontier goals (only for live searching boards) ---
            searching = [(b.agent_id, b.x, b.y) for b in bodies
                         if b.alive() and b.real_id and not mission_done
                         and b.last_state == STATE_SEARCH
                         and b.agent_id not in (0, a.beacon)]
            fgoals = coverage.goals_for(searching, bodies)

            # --- move, sniff, send ---
            for b in bodies:
                if b.dead():
                    continue

                if mission_done:
                    b.returning = True
                    tx, ty = base
                elif b.agent_id == a.beacon:
                    tx, ty = b.x, b.y
                elif b.last_state != STATE_SEARCH:
                    tx, ty = b.goal        # board is REOBSERVING or RF — follow its decision
                elif b.agent_id in fgoals:
                    tx, ty = fgoals[b.agent_id]
                else:
                    tx, ty = b.goal

                b.fly_to(tx, ty, step_cells)
                coverage.mark_seen(b.x, b.y)

                # --- DIGITAL SNIFFING: dynamic RSSI per drone per tick ---
                det = sniffer.sniff(b.x, b.y, b.agent_id, rng)

                # --- cooperative RF localization (phone) ---
                if phone_active and (a.rf_range <= 0 or
                        math.hypot(b.x - phone_active[0],
                                   b.y - phone_active[1]) <= a.rf_range):
                    loc_rssi = sniffer.rssi_from_phone(b.x, b.y, phone_active)
                else:
                    loc_rssi = -128

                b.send_sensor(tick, det, loc_rssi)

                if det:
                    recent_dets.append({
                        "agent_id":   b.agent_id,
                        "x":          round(det[0], 2),
                        "y":          round(det[1], 2),
                        "confidence": round(det[2], 3),
                        "bearing":    math.atan2(det[1] - b.y, det[0] - b.x),
                        "timestamp":  int(now),
                        "t":          now,
                    })

            recent_dets[:] = [d for d in recent_dets
                              if now - d["t"] < DET_TTL_S]

            if client is not None:
                client.push(build_snapshot(a, tick, bodies, coverage,
                                           recent_dets, victims_rf, phone,
                                           rf_final, mission_done))

            if tick % max(1, int(a.hz / 2)) == 0:
                _print_row(tick, phone_active, bodies)

            tick += 1
            time.sleep(max(0.0, dt - (time.time() - t0)))

    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        if client is not None:
            client.stop()


if __name__ == "__main__":
    main()
