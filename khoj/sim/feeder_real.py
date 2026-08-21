#!/usr/bin/env python3
"""KHOJ - the REAL bridge (hardware-in-the-loop).

This is the seam between the two stacks. It replaces feeder.py's *fake*
confidence model with the real trained detector from the laptop stack, while
keeping the exact same USB byte contract the boards already speak. No firmware
change - the boards can't tell whether the confidence in a sensor packet came
from a hand-tuned band or from YOLO; they only ever see a number.

    real SARD frame ─► YOLO(best.pt) ─► det_conf ─┐
    log-distance RF  ───────────────────────────► │  usb_sensor_t  ─► ESP32
    frontier coverage (laptop plans idle search) ─┘        (28 bytes, framed)
                                                   ESP32 ─► usb_goal_t ─► body

What crosses the wire is identical to feeder.py. What's different is WHERE the
numbers come from:

  * det_conf now comes from the real detector (engine.perception.Perceptor),
    run on a real aerial frame. That's the honest half of HIL: real detector
    confidence, simulation-known position. (Full camera->world projection lives
    in perception.py for the real Jetson drone; here we already know the cell.)
  * RSSI is still log-distance path loss to a hidden phone - the RF hero.
  * When a board is just SEARCHing (its firmware returns "hold position", it has
    no search planner on-device), the laptop hands it a FRONTIER goal using the
    real engine.swarm frontier logic, so routine coverage still happens. The
    laptop NEVER decides an auction or a confirmation - the boards do that. It
    only drives exploration into blank space, which is a sensor/actuator job.

Who owns what (this is the whole "no central brain" claim, kept honest):
    board decides : who wins the REOBSERVE auction, RF gradient steps, confirm/
                    dismiss via log-odds fusion, failure re-auction.
    laptop drives : where the body physically is, what its camera "sees" (real
                    detector), what RF it hears, and - only when the board has
                    nothing to do - which blank cell to go explore.

Run it (needs the ml env for real detection: ultralytics + torch + best.pt):
    # from the NightWing repo root:
    python khoj/sim/feeder_real.py --ports COM3 COM13 COM14 COM16
    python khoj/sim/feeder_real.py --detector sim      # no weights yet? still runs
    python khoj/sim/feeder_real.py --sard SARD_YOLO.v1-original.yolov11/test

Ctrl-C to stop.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import sys
import threading
import time
import urllib.request

# ---------------------------------------------------------------------------
# Import path: this file lives at <NightWing>/khoj/sim/feeder_real.py and needs
# to reach BOTH stacks - protocol.py (its own folder) and engine.* (repo root).
# Wiring the path here means it runs the same from any cwd, `python khoj/sim/...`
# or `cd khoj/sim && python feeder_real.py`, without a package install.
_HERE = os.path.dirname(os.path.abspath(__file__))          # .../khoj/sim
_ROOT = os.path.dirname(os.path.dirname(_HERE))              # .../NightWing
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial missing.  py -m pip install pyserial")

# the byte contract - shared with the firmware (khoj/firmware/lib/quorum_proto)
from protocol import (
    pack_sensor, unpack_goal, frame, FrameParser, GOAL_SIZE, MSG_GOAL,
    STATE_SEARCH,
)

# Headless by design. The live dashboard is deliberately not in this repo, and a
# bare `import dashboard` here would collide with NightWing's own dashboard/
# package anyway. Live visibility comes from the console scoreboard + event feed.


# ---------------------------------------------------------------------------
# Constants that MUST agree with the firmware, or the two halves disagree about
# physics and the auction's cost-is-time model is wrong. Sourced from
# khoj/firmware/src/mesh/main.cpp - do not "improve" one side alone.
AGENT_SPEED = 2.0       # cells / second   (main.cpp #define AGENT_SPEED)
SENSOR_R    = 3.5       # camera footprint radius, cells (world.sensor_radius)
CONF_LO     = 15        # <= this: board ignores it        (main.cpp CONF_LO)
CONF_HI     = 80        # >= this: board auto-confident     (main.cpp CONF_HI)
GRID_N      = 32        # canonical grid, float cells
BASE_XY     = (2.0, 2.0)  # return-to-base origin the drones launch from / go home to
ALIVE_S     = 2.5       # no goal back for this long => board is offline (frozen on screen)
DET_TTL_S   = 2.5       # a detection older than this stops glowing on the map

# Detector confidence bands (0..1) we STEER the world toward, so detections land
# in the auction band (CONF_LO < c < CONF_HI) and actually resolve under fusion:
#   victims: above 0.5  -> repeated independent looks climb past 0.80  -> CONFIRM
#   decoys : below 0.5  -> repeated looks fall below 0.15              -> DISMISS
# In real mode these come from picking frames the DETECTOR itself scores in-band
# (genuinely ambiguous aerial shots); in sim mode they're drawn here.
VICTIM_BAND = (0.52, 0.72)   # top kept < CONF_HI even after +jitter, so it auctions
DECOY_BAND  = (0.18, 0.42)   # kept > CONF_LO (spawns a task) and < 0.5 (fusion dismisses)

STATE_NAMES = {0: "SEARCH", 1: "REOBSERVE", 2: "RF"}
EVENTS: list[str] = []

BRIDGE_HINTS = ("CP210", "CH340", "CH910", "FTDI", "FT232", "Silicon Labs", "USB-SERIAL")


# ===========================================================================
#  The world's ground truth - targets the boards are NEVER told about
# ===========================================================================

class Target:
    """A thing on the ground a camera might see. In real mode `base_conf` is a
    REAL YOLO score, chosen once at build time from a real SARD frame (`img`):
    for a victim, a frame the model scores in the auction band (0.52-0.79); for a
    decoy, one of the model's genuinely-weak boxes (0.18-0.45). `kind` is only
    for the operator's scoreboard - the boards decide victim-vs-decoy themselves,
    from whether repeated looks push fused confidence up or down. In sim mode
    base_conf is None and the confidence is drawn from a band instead."""
    def __init__(self, x, y, kind, base_conf=None, img=None):
        self.x = float(x)
        self.y = float(y)
        self.kind = kind                 # "victim" | "decoy"
        self.base_conf = base_conf       # real YOLO score (real mode) or None
        self.img = img                   # the frame it came from (for reference)


# ===========================================================================
#  Sensing - turns "a drone is over a target" into a real detection confidence
# ===========================================================================

class Sensing:
    """Owns the detector and the ground truth. Given a drone pose, returns what
    its camera reports this instant: (det_x, det_y, conf) or None.

    Two modes, chosen at startup:
      real - engine.perception.Perceptor runs the trained YOLO on the target's
             frame; conf is the model's own score. Honest HIL.
      sim  - analytic bands (feeder.py's model), for wiring/bench tests before
             the weights arrive. Loudly announced so nobody mistakes it for real.
    """
    def __init__(self, mode, targets, per=None):
        self.mode = mode                 # "real" | "sim"
        self.targets = targets
        self.per = per                   # Perceptor (only used at build time now)

    def look(self, x, y, heading, agent_id, t, rng):
        """The single most-confident thing this camera sees, or None."""
        best = None
        for tg in self.targets:
            if math.hypot(x - tg.x, y - tg.y) > SENSOR_R:
                continue
            conf = self._confidence(tg, x, y, heading, agent_id, t, rng)
            if conf is None:
                continue
            if best is None or conf > best[2]:
                # small localization noise: the box centre is never dead-on the
                # true cell, which is what the boards' log-odds fusion tolerates.
                best = (tg.x + rng.uniform(-0.3, 0.3),
                        tg.y + rng.uniform(-0.3, 0.3), conf)
        return best

    def _confidence(self, tg, x, y, heading, agent_id, t, rng):
        if self.mode == "real" and tg.base_conf is not None:
            # tg.base_conf is a REAL YOLO score from a real SARD frame, chosen at
            # build time (see build_world). YOLO on a static frame is pose-
            # independent and deterministic, so there's nothing to gain from re-
            # running it every tick; a tiny per-look jitter stands in for the fact
            # that independent drones image from slightly different angles, so
            # fusion sees genuinely separate looks instead of one identical number.
            return max(0.02, min(0.98, tg.base_conf + rng.uniform(-0.03, 0.03)))

        # sim mode: draw from the band for this kind of target.
        lo, hi = VICTIM_BAND if tg.kind == "victim" else DECOY_BAND
        return lo + (hi - lo) * rng.random()

    @staticmethod
    def rssi_from(x, y, px, py):
        """Log-distance path loss the drone would hear from a phone at (px,py).
        The boards never learn the phone position - only this number, weaker with
        distance, exactly like a real radio."""
        d = max(0.5, math.hypot(x - px, y - py))
        rssi = -40.0 - 10.0 * 2.2 * math.log10(d)      # -40 dBm at 1 cell
        return int(max(-100, min(-20, round(rssi))))


# ===========================================================================
#  Frontier coverage - reuse the laptop stack's real frontier logic
# ===========================================================================

class Coverage:
    """Hands idle (SEARCHING) boards somewhere to explore, using the REAL
    engine.swarm frontier code so the laptop and the pure-python sim never
    diverge on what "the frontier" means. This is the ONLY planning the laptop
    does, and it's coverage only - it assigns no tasks, picks no auction winners.
    """
    def __init__(self, grid):
        self.grid = grid
        self.known = [[0.0] * grid for _ in range(grid)]
        self.prob = [[0.0] * grid for _ in range(grid)]   # laptop tracks no belief
        self._sw = None
        self._ids: tuple = ()
        try:
            from engine.swarm import Swarm, BrainConfig  # noqa: F401
            from engine.protocol import TaskType         # noqa: F401
            self._ok = True
        except Exception as e:
            _ev("WARN frontier unavailable (%s) - idle drones will orbit" % e)
            self._ok = False

    def reset(self):
        """Wipe the searched map so the frontier search starts over (RESTART)."""
        self.known = [[0.0] * self.grid for _ in range(self.grid)]

    def mark_seen(self, x, y):
        """Everything under a footprint is now searched - retires the frontier."""
        r = SENSOR_R
        x0, x1 = max(0, int(x - r)), min(self.grid - 1, int(x + r))
        y0, y1 = max(0, int(y - r)), min(self.grid - 1, int(y + r))
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

    def goals_for(self, searching):
        """searching: list of (agent_id, x, y). Returns {agent_id: (gx, gy)}.
        Nearest open frontier per idle board; if the map is fully searched, an
        expanding orbit so nobody freezes on a hold."""
        if not searching:
            return {}
        if not self._ok:
            return {aid: self._orbit(aid, x, y) for aid, x, y in searching}
        self._ensure([a for a, _, _ in searching])
        from engine.protocol import TaskType
        sw = self._sw
        for aid, x, y in searching:                 # sync bodies into the brains
            b = sw.brains.get(aid)
            if b:
                b.x, b.y, b.current_task = x, y, None
        sw.rebuild_frontier_tasks(self.known, self.prob, self.grid, self.grid)
        open_tasks = [t for t in sw.tasks.values()
                      if t.type == TaskType.FRONTIER and t.open]
        out = {}
        for aid, x, y in sorted(searching):         # deterministic: lowest id first
            if open_tasks:
                t = min(open_tasks, key=lambda t: math.hypot(t.x - x, t.y - y))
                open_tasks.remove(t)                # one frontier per drone this round
                out[aid] = (t.x, t.y)
            else:
                out[aid] = self._orbit(aid, x, y)   # nothing left unknown -> orbit
        return out

    def _orbit(self, aid, x, y):
        ang = time.time() * 0.15 + aid
        return (self.grid / 2 + (self.grid / 3) * math.cos(ang),
                self.grid / 2 + (self.grid / 3) * math.sin(ang))


# ===========================================================================
#  A body - one board's fake drone. Same wire dance as feeder.py.
# ===========================================================================

class Body:
    def __init__(self, port, idx, grid):
        self.port = port
        self.grid = grid
        self.x = 2.0 + (idx * 5.0) % max(1.0, grid - 4)
        self.y = 2.0 + (idx * 7.0) % max(1.0, grid - 4)
        self.heading = 0.0
        self.goal = (self.x, self.y)
        # provisional id only - the BOARD owns its identity (from its MAC), and
        # COM order is meaningless. We learn the real id from the first goal it
        # returns and switch to it. Addressing the wrong drone here would send an
        # auction winner's move command to a different board.
        self.agent_id = idx + 1
        self.real_id = None
        self.goals_back = 0
        self.sent = 0
        self.last_state = STATE_SEARCH
        self.cur_task = 0
        # DTR/RTS drive the ESP32 auto-reset (EN/IO0). A CH9102 bridge gets HELD
        # IN RESET when the port opens/closes; a CP210x doesn't. Park both low so
        # merely talking to a board never resets it.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = 115200
        self.ser.timeout = 0
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        self.ser.dtr = False
        self.ser.rts = False
        self.parser = FrameParser()
        self.textbuf = ""               # boards mix log text with binary frames
        self.rf_est = None
        self.returning = False          # heading home (mission complete)
        self.last_rx = 0.0              # set on first goal; 0 = never heard yet
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def alive(self):
        """Actively responding: a goal came back recently. Drives the DEAD marker
        on the dashboard."""
        return self.last_rx > 0.0 and (time.time() - self.last_rx) < ALIVE_S

    def dead(self):
        """WAS talking and then went silent (unplugged / powered off) -> freeze it
        so we don't fly a phantom. A board that has NEVER replied yet is only
        'connecting', NOT dead - we must keep sending it sensor packets or it can
        never bootstrap into replying (the board only answers a packet it receives)."""
        return self.last_rx > 0.0 and (time.time() - self.last_rx) >= ALIVE_S

    # ---- talk to the board -------------------------------------------------

    def drain_text(self, data):
        """Surface EVENT / PEER / WARN lines the board prints on the same wire."""
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
        """Non-blocking: drain the port, update goal/state from any goal frame."""
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
                self.goal = (g["goal_x"], g["goal_y"])
                self.last_state = g["state"]
                self.cur_task = g["cur_task"]
                self.goals_back += 1
                self.last_rx = time.time()
                if g["agent_id"]:
                    if self.real_id is None and g["agent_id"] != self.agent_id:
                        _ev("%s is board id=%d (not %d) - using the board's own id"
                            % (self.port, g["agent_id"], self.agent_id))
                    self.real_id = g["agent_id"]
                    self.agent_id = g["agent_id"]

    def send_sensor(self, tick, det, rssi):
        if det:
            pkt = pack_sensor(self.agent_id, self.x, self.y, heading=0,
                              has_detection=1, det_x=det[0], det_y=det[1],
                              det_conf=int(round(det[2] * 100)),
                              rssi=rssi, tick=tick)
        else:
            pkt = pack_sensor(self.agent_id, self.x, self.y, rssi=rssi, tick=tick)
        try:
            self.ser.write(frame(pkt))
            self.sent += 1
        except Exception:
            pass

    # ---- move ----------------------------------------------------------------

    def fly_to(self, tx, ty, step):
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        if d > 1e-6:
            self.heading = math.atan2(dy, dx)
            self.x += dx / d * min(step, d)
            self.y += dy / d * min(step, d)

    def board_in_charge(self):
        """True when the board returned a real task goal (auction re-observe or
        RF gradient). Then the laptop OBEYS the board. Only in SEARCH does the
        laptop supply a frontier goal, because the board has no search planner."""
        return self.last_state != STATE_SEARCH or self.cur_task != 0

    def close(self):
        try:
            self.ser.dtr = False        # leave reset released so the board runs on
            self.ser.rts = False
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass


# ===========================================================================
#  helpers
# ===========================================================================

def _ev(txt):
    print("   " + txt)
    EVENTS.append(txt)
    del EVENTS[:-160]


def find_ports():
    out = []
    for p in list_ports.comports():
        blob = " ".join(str(x) for x in (p.description, p.manufacturer, p.hwid))
        if any(h.lower() in blob.lower() for h in BRIDGE_HINTS):
            out.append(p.device)
    return sorted(out, key=lambda d: int("".join(c for c in d if c.isdigit()) or 0))


def parse_pts(s):
    out = []
    for part in s.split(";"):
        part = part.strip()
        if part:
            x, y = part.split(",")
            out.append((float(x), float(y)))
    return out


# ---- world construction ----------------------------------------------------

def _bucket_frames(per, sard_dir, rng, want_victims, want_decoys, scan=140):
    """Run the real detector over SARD frames and pull two real-score pools:

      victims -> (frame, conf) where the model's BEST box is in the auction band
                 (0.52-0.79): a real, strong-but-sub-threshold detection that
                 climbs to CONFIRMED once a second drone re-observes it.
      decoys  -> (frame, conf) using one of the model's genuinely-WEAK boxes
                 (0.18-0.45): a real low-confidence hit (occlusion / clutter) that
                 repeated looks DON'T support, so the swarm dismisses it.

    The DETECTOR decides which frames are ambiguous, not us - that is what makes
    it honest HIL rather than a scripted result."""
    from engine.perception import DronePose
    lab = os.path.join(sard_dir, "labels")
    img = os.path.join(sard_dir, "images")
    if os.path.isdir(img):
        cands = sorted(glob.glob(os.path.join(img, "*.jpg")))
    else:                                   # tolerate a flat/nested layout
        cands = sorted(glob.glob(os.path.join(sard_dir, "**", "*.jpg"), recursive=True))
    rng.shuffle(cands)

    victims, decoys = [], []
    for p in cands[:scan]:
        try:
            confs = [d.confidence for d in
                     per.detect(p, DronePose(0, 0, 0), 0, 0.0, conf=0.05)]
        except Exception:
            continue
        if not confs:
            continue
        top = max(confs)
        if len(victims) < want_victims and VICTIM_BAND[0] <= top <= VICTIM_BAND[1]:
            victims.append((p, top))
            continue
        weak = [c for c in confs if DECOY_BAND[0] <= c <= DECOY_BAND[1]]
        if len(decoys) < want_decoys and weak:
            decoys.append((p, max(weak)))       # the strongest weak box: a plausible false alarm
        if len(victims) >= want_victims and len(decoys) >= want_decoys:
            break
    return victims, decoys


def build_world(mode, victims_xy, decoys_xy, per, sard_dir, rng):
    """Assemble the ground-truth targets. In real mode each target carries a REAL
    YOLO score from a real frame (see _bucket_frames); in sim mode scores are
    drawn from a band. Falls back to sim if the detector yields no usable frames."""
    targets = []
    v_pool = d_pool = []
    if mode == "real":
        v_pool, d_pool = _bucket_frames(per, sard_dir, rng,
                                        len(victims_xy), len(decoys_xy))
        if not v_pool:
            _ev("WARN no in-band victim frames under %s - falling back to sim bands"
                % sard_dir)
            mode = "sim"
        elif not d_pool:
            _ev("WARN no weak decoy boxes found - decoys will use the sim band")
    for i, (x, y) in enumerate(victims_xy):
        if v_pool:
            img, conf = v_pool[i % len(v_pool)]
            targets.append(Target(x, y, "victim", base_conf=conf, img=img))
        else:
            targets.append(Target(x, y, "victim"))
    for i, (x, y) in enumerate(decoys_xy):
        if d_pool:
            img, conf = d_pool[i % len(d_pool)]
            targets.append(Target(x, y, "decoy", base_conf=conf, img=img))
        else:
            targets.append(Target(x, y, "decoy"))
    return mode, targets


# ---- detector bring-up -----------------------------------------------------

def make_perceptor(weights, sard_dir):
    """Try to stand up the real detector. Returns (Perceptor|None, reason)."""
    try:
        from engine.perception import Perceptor, CameraModel
    except Exception as e:
        return None, "engine.perception import failed (%s)" % e
    try:
        per = Perceptor(weights=weights, camera=CameraModel(altitude=30.0, hfov_deg=66.0))
    except Exception as e:
        return None, "weights not loadable (%s)" % e
    if not (os.path.isdir(os.path.join(sard_dir, "images")) or
            glob.glob(os.path.join(sard_dir, "**", "*.jpg"), recursive=True)):
        return None, "no SARD frames under %s" % sard_dir
    return per, "loaded %s" % per.weights


# ===========================================================================
#  Dashboard push - feed the NightWing web dashboard, not the terminal
# ===========================================================================

class DashboardClient:
    """POSTs the live snapshot to the NightWing backend (`/ingest`) on its own
    thread, so HTTP never stalls the serial loop. Holds only the LATEST snapshot
    - a late frame is worthless on a live view, so nothing queues up. If the
    backend is down it just keeps failing quietly and retries; the bench test
    runs fine headless."""

    def __init__(self, url, hz=6.0):
        self.url = url
        self.dt = 1.0 / hz
        self._snap = None
        self._lock = threading.Lock()
        self._alive = True
        self._reset = False             # set when the backend reports RESTART
        self.ok = None                  # None=never tried, True/False=last POST
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, snap):
        with self._lock:
            self._snap = snap

    def take_reset(self):
        """One-shot: True exactly once after the dashboard's RESTART was pressed."""
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
                req = urllib.request.Request(
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


def _resolved_points(events, keyword):
    """Pull the (x,y) out of every CONFIRMED / DISMISSED event line the boards
    printed. Both the self lines ('CONFIRMED survivor at (x,y)') and the peer
    lines ('peer 3 DISMISSED (x,y)') carry the point as the first parenthesised
    pair after the keyword."""
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


def build_snapshot(a, tick, mode, detector_reason, phone, targets, bodies,
                   coverage, recent_dets, mission_done=False):
    """Assemble the hardware snapshot the dashboard's HardwareStateGenerator maps
    onto SimState. This is the firmware-facing side of the same split real_state.py
    uses: we speak boards/ports/state-enums here; the dashboard translates."""
    rf = next((b.rf_est for b in bodies if b.rf_est and b.alive()), None)
    boards = []
    for b in bodies:
        boards.append({
            "id": b.real_id or 0,
            "port": b.port,
            "x": round(b.x, 2), "y": round(b.y, 2),
            "heading": round(b.heading, 3),
            "state": b.last_state,
            "cur_task": b.cur_task,
            "goal": [round(b.goal[0], 2), round(b.goal[1], 2)],
            "alive": b.alive(),
            "returning": bool(b.returning),
            "beacon": (b.agent_id == a.beacon),
            "packets": b.sent,
        })
    return {
        "grid": int(a.grid),
        "tick": tick,
        "detector": ("real (%s)" % detector_reason) if mode == "real" else "sim bands",
        "known": coverage.known,
        "boards": boards,
        "detections": [{k: d[k] for k in ("agent_id", "x", "y", "confidence",
                        "bearing", "timestamp")} for d in recent_dets],
        "mission_complete": bool(mission_done),
        "confirmed": _resolved_points(EVENTS, "CONFIRMED"),
        "dismissed": _resolved_points(EVENTS, "DISMISSED"),
        "rf": [round(rf[0], 2), round(rf[1], 2)] if rf else None,
        "events": list(EVENTS)[-40:],
        # ground truth for an operator/scoring view (boards never see these)
        "phone": list(phone) if phone else None,
        "victims": [[t.x, t.y] for t in targets if t.kind == "victim"],
        "decoys": [[t.x, t.y] for t in targets if t.kind == "decoy"],
    }


# ===========================================================================
#  main
# ===========================================================================

def main():
    ap = argparse.ArgumentParser(description="KHOJ real HIL bridge (feeder_real)")
    ap.add_argument("--ports", nargs="*", default=None)
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--grid", type=float, default=float(GRID_N))
    ap.add_argument("--speed", type=float, default=AGENT_SPEED,
                    help="body speed in CELLS/SECOND (must match firmware AGENT_SPEED)")
    ap.add_argument("--detector", choices=["auto", "real", "sim"], default="auto",
                    help="auto: real if weights+SARD present, else sim")
    ap.add_argument("--weights", default=None, help="path to best.pt (else auto-resolved)")
    ap.add_argument("--sard", default="SARD_YOLO.v1-original.yolov11/test",
                    help="SARD test split (images/ + labels/)")
    ap.add_argument("--phone", nargs=2, type=float, metavar=("X", "Y"), default=None,
                    help="OPT-IN RF demo: hidden phone at X Y (boards never see it). "
                         "OFF by default so the swarm behaves like the pure-search "
                         "python sim. With the RF-near firmware gate, turning it on "
                         "only pulls the closest drone or two onto the phone; the "
                         "rest keep searching.")
    ap.add_argument("--no-phone", action="store_true", help="(kept for compatibility; "
                    "phone is already off unless --phone is given)")
    ap.add_argument("--rf-range", type=float, default=12.0,
                    help="when --phone is on, it's only 'audible' within this many "
                         "cells (the grid is 32 wide). 0 = audible everywhere.")
    ap.add_argument("--victims", default="10,10;26,8", help="real survivors 'x,y;x,y'")
    ap.add_argument("--decoys", default="6,20;18,17", help="false alarms")
    ap.add_argument("--base", nargs=2, type=float, metavar=("X", "Y"), default=[2.0, 2.0],
                    help="return-to-base origin; drones fly here once every victim is "
                         "confirmed and every decoy dismissed")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--beacon", type=int, default=-1,
                    help="board id to treat as a fixed beacon (held, not searched). "
                         "Default -1 = none: all boards are equal searchers, "
                         "matching firmware BEACON_ID=255. Set only for a hardware "
                         "RF demo with a real transmitter board.")
    ap.add_argument("--dashboard", default="http://127.0.0.1:8000/ingest",
                    help="NightWing backend ingest URL (run it with KHOJ_ENGINE=hardware)")
    ap.add_argument("--no-dashboard", action="store_true",
                    help="don't POST to the web dashboard (console only)")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    phone = None if (a.no_phone or not a.phone) else tuple(a.phone)
    victims_xy = parse_pts(a.victims)
    decoys_xy = parse_pts(a.decoys)

    # ---- decide detector mode ----------------------------------------------
    per, reason = (None, "")
    mode = a.detector
    if a.detector in ("auto", "real"):
        per, reason = make_perceptor(a.weights, a.sard)
        if per is not None:
            mode = "real"
        elif a.detector == "real":
            sys.exit("--detector real, but couldn't: %s\n"
                     "Get best.pt + the SARD test split, or use --detector sim." % reason)
        else:
            mode = "sim"

    if mode == "real":
        print("   scanning SARD frames with the detector to pick real "
              "in-band victims / weak decoys ...")
    mode, targets = build_world(mode, victims_xy, decoys_xy, per, a.sard, rng)
    sensing = Sensing(mode, targets, per)
    coverage = Coverage(int(a.grid))

    # ---- ports --------------------------------------------------------------
    ports = a.ports if a.ports else find_ports()
    if not ports:
        sys.exit("No ESP32 boards found. Plug one in, or pass --ports COM3 ...")

    print("KHOJ real bridge - feeding %d board(s): %s" % (len(ports), ", ".join(ports)))
    if mode == "real":
        print("   DETECTOR   : REAL - %s" % reason)
        for t in targets:
            if t.base_conf is not None:
                print("                %-6s @ (%.0f,%.0f)  YOLO conf %.2f  <- %s"
                      % (t.kind, t.x, t.y, t.base_conf, os.path.basename(t.img)))
    else:
        print("   DETECTOR   : SIM bands (%s) - no real weights in the loop"
              % (reason or "chosen"))
    print("   speed      : %.2f cells/s (firmware AGENT_SPEED=%.1f)" % (a.speed, AGENT_SPEED))
    print("   mode       : %s" % (
        "SEARCH + RF (closest drones converge on the phone, rest keep searching)"
        if phone else "SEARCH-ONLY (frontier + re-observe + fusion, like the sim)"))
    print("   beacon     : %s" % ("none - all boards search" if a.beacon < 0
                                  else "board id %d held fixed" % a.beacon))
    print("   task band  : detections with %d < conf*100 < %d get auctioned"
          % (CONF_LO, CONF_HI))
    print("   ground truth the boards never see -")
    print("      real survivors : %s" % (victims_xy,))
    print("      decoys         : %s" % (decoys_xy,))
    if phone:
        print("      hidden phone   : %s   (audible within %.0f cells)"
              % (phone, a.rf_range) if a.rf_range > 0
              else "      hidden phone   : %s   (audible everywhere)" % (phone,))

    client = None
    if not a.no_dashboard:
        client = DashboardClient(a.dashboard)
        print("   DASHBOARD  : POSTing to %s" % a.dashboard)
        print("                start the backend with:  KHOJ_ENGINE=hardware "
              "uvicorn backend.main:app --port 8000")
        print("                then open the frontend (http://127.0.0.1:3000)")
    print()

    bodies = []
    for i, p in enumerate(ports):
        try:
            bodies.append(Body(p, i, a.grid))
        except Exception as e:
            print("  !! could not open %s: %s" % (p, e))
    if not bodies:
        sys.exit("No port could be opened. Close any serial monitor holding them.")

    dt = 1.0 / a.hz
    step_cells = a.speed * dt            # cells moved per tick at this rate
    recent_dets: list = []               # time-expired, so old glows fade off the map
    base = tuple(a.base)
    n_victims, n_decoys = len(victims_xy), len(decoys_xy)
    t_wall0 = time.time()
    tick = 0
    try:
        while True:
            t0 = time.time()
            now = time.time()
            sim_t = now - t_wall0            # seconds since start, for detector t

            # 0) RESTART button: the backend flags a reset in its /ingest reply.
            #    Clear everything the LAPTOP owns - coverage, detection glows, the
            #    resolved-spot display - so the search visibly starts over. (The
            #    boards keep their own resolved memory; a full wipe needs a
            #    power-cycle, which is called out in the docs.)
            if client is not None and client.take_reset():
                coverage.reset()
                recent_dets.clear()
                EVENTS.clear()
                for b in bodies:
                    b.returning = False
                _ev("RESTART - coverage and map cleared, resuming search")

            # 1) read every board's latest goal + log lines
            for b in bodies:
                b.read_goal()

            # mission complete once every real survivor is confirmed and every
            # decoy dismissed -> the swarm has nothing left to do but come home.
            n_conf = len(_resolved_points(EVENTS, "CONFIRMED"))
            n_dism = len(_resolved_points(EVENTS, "DISMISSED"))
            mission_done = (n_victims and n_conf >= n_victims and n_dism >= n_decoys)

            # 2) plan frontier goals for LIVE, searching boards only (a dead board
            #    is frozen, and the beacon, if any, is held).
            searching = [(b.agent_id, b.x, b.y) for b in bodies
                         if b.alive() and b.real_id and not mission_done
                         and not b.board_in_charge() and b.agent_id not in (0, a.beacon)]
            fgoals = coverage.goals_for(searching)

            # 3) move each body, sense, and stream a fresh sensor packet.
            #    Freeze ONLY boards that were alive and then went silent. A board
            #    that hasn't replied yet still gets packets so it can bootstrap.
            for b in bodies:
                if b.dead():
                    continue                            # unplugged: no fly, no sense, no send
                if mission_done:
                    b.returning = True                  # all found -> return to base
                    tx, ty = base
                elif b.agent_id == a.beacon:
                    tx, ty = b.x, b.y                   # beacon holds its position
                elif b.board_in_charge():
                    tx, ty = b.goal                     # obey the board's decision
                elif b.agent_id in fgoals:
                    tx, ty = fgoals[b.agent_id]         # laptop-planned coverage
                else:
                    tx, ty = b.goal                     # not yet talking: hold/spread
                b.fly_to(tx, ty, step_cells)
                coverage.mark_seen(b.x, b.y)

                # RF is only heard within rf_range of the phone. Beyond that the
                # drone gets no reading (-128), so it keeps searching instead of
                # RF-locking on a signal it realistically couldn't detect yet.
                if phone and (a.rf_range <= 0 or
                              math.hypot(b.x - phone[0], b.y - phone[1]) <= a.rf_range):
                    rssi = Sensing.rssi_from(b.x, b.y, *phone)
                else:
                    rssi = -128
                det = sensing.look(b.x, b.y, b.heading, b.agent_id, sim_t, rng)
                b.send_sensor(tick, det, rssi)
                if det:
                    recent_dets.append({
                        "agent_id": b.agent_id, "x": round(det[0], 2),
                        "y": round(det[1], 2), "confidence": round(det[2], 3),
                        "bearing": math.atan2(det[1] - b.y, det[0] - b.x),
                        "timestamp": int(now), "t": now,
                    })
            # expire stale detection glows so the map doesn't fill with old spots
            recent_dets[:] = [d for d in recent_dets if now - d["t"] < DET_TTL_S]

            # 4) push the live snapshot to the web dashboard (non-blocking)
            if client is not None:
                client.push(build_snapshot(a, tick, mode, reason, phone, targets,
                                           bodies, coverage, recent_dets, mission_done))

            # 5) console scoreboard, twice a second
            if tick % max(1, int(a.hz / 2)) == 0:
                _print_row(tick, phone, bodies)

            tick += 1
            time.sleep(max(0.0, dt - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        if client is not None:
            client.stop()
            if client.ok is False:
                print("\nNOTE: the dashboard never accepted a POST. Start the "
                      "backend with KHOJ_ENGINE=hardware, or pass --no-dashboard.")
        silent = [b.port for b in bodies if b.goals_back == 0]
        for b in bodies:
            b.close()
        if silent:
            print("\nNO GOALS came back from: %s" % ", ".join(silent))
            print("Those boards are running firmware without USB support - reflash "
                  "the `mesh` env.")
        else:
            print("\nAll boards answered. The real bridge is live end-to-end.")


def _print_row(tick, phone, bodies):
    if phone:
        ds = [math.hypot(b.x - phone[0], b.y - phone[1]) for b in bodies]
        print("           hidden phone @ (%.1f,%.1f)   nearest drone %.1f cells"
              % (phone[0], phone[1], min(ds)))
    cells = []
    for b in bodies:
        ok = "OK " if b.goals_back else "...."
        tag = ("id%d" % b.real_id) if b.real_id else "id?"
        cells.append("%s %s (%4.1f,%4.1f)->(%4.1f,%4.1f) %-9s %s %d/%d"
                     % (b.port, tag, b.x, b.y, b.goal[0], b.goal[1],
                        STATE_NAMES.get(b.last_state, ""), ok, b.goals_back, b.sent))
    print("t=%-6d %s" % (tick, "   |   ".join(cells)))


if __name__ == "__main__":
    main()
