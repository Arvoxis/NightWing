#!/usr/bin/env python3
"""KHOJ — generate the pitch-deck diagrams as SVG + PNG.

Six diagrams, one per important phase of the system. Written with explicit SVG
presentation attributes (no CSS, no gradients, no filters, arrowheads drawn as
polygons) so they render identically in svglib, in a browser, and inside
PowerPoint when inserted as .svg.

    py make_diagrams.py

Outputs into this directory: 01..06 as both .svg and .png (2x scale).
"""
import os
import subprocess
import sys

OUT = os.path.dirname(os.path.abspath(__file__))

# ---- palette: navy = structure, cyan = radio, amber = task, red = failure ----
NAVY   = "#12263F"
SLATE  = "#2E5266"
MUTED  = "#6B7F94"
CYAN   = "#0E8FA0"
CYAN_L = "#D6EEF1"
AMBER  = "#D98829"
AMBER_L= "#FBEEDA"
RED    = "#B8433A"
RED_L  = "#F7E0DE"
GREEN  = "#2E7D5B"
GREEN_L= "#DCEDE5"
LIGHT  = "#EEF3F7"
WHITE  = "#FFFFFF"
LINE   = "#B9C6D3"

F = "Arial"


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def txt(x, y, s, size=15, fill=NAVY, anchor="middle", bold=False, italic=False):
    w = ' font-weight="bold"' if bold else ''
    i = ' font-style="italic"' if italic else ''
    return (f'<text x="{x}" y="{y}" font-family="{F}" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}"{w}{i}>{esc(s)}</text>')


def lines(x, y, rows, size=13, fill=SLATE, anchor="middle", gap=None, bold=False):
    gap = gap or size + 5
    return "".join(txt(x, y + i * gap, r, size, fill, anchor, bold)
                   for i, r in enumerate(rows))


def box(x, y, w, h, fill=WHITE, stroke=LINE, sw=2, r=10):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def circle(cx, cy, r, fill=WHITE, stroke=LINE, sw=2):
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>')


def line(x1, y1, x2, y2, stroke=LINE, sw=2, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" '
            f'stroke-width="{sw}"{d}/>')


def arrow(x1, y1, x2, y2, stroke=SLATE, sw=2.5, head=9, dash=None):
    """Line + explicit polygon head (markers are unreliable across renderers)."""
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    bx, by = x2 - head * math.cos(ang), y2 - head * math.sin(ang)
    p1 = (x2, y2)
    p2 = (bx - head * 0.55 * math.sin(ang), by + head * 0.55 * math.cos(ang))
    p3 = (bx + head * 0.55 * math.sin(ang), by - head * 0.55 * math.cos(ang))
    pts = " ".join(f"{px:.1f},{py:.1f}" for px, py in (p1, p2, p3))
    return (line(x1, y1, bx, by, stroke, sw, dash) +
            f'<polygon points="{pts}" fill="{stroke}"/>')


def chip(cx, cy, w, h, label, fill=CYAN_L, stroke=CYAN, sub=None):
    """A little ESP32-looking block with pins."""
    s = box(cx - w / 2, cy - h / 2, w, h, fill, stroke, 2, 6)
    for i in range(5):
        px = cx - w / 2 + w * (i + 1) / 6
        s += line(px, cy - h / 2, px, cy - h / 2 - 6, stroke, 2)
        s += line(px, cy + h / 2, px, cy + h / 2 + 6, stroke, 2)
    s += txt(cx, cy + 5, label, 15, NAVY, bold=True)
    if sub:
        s += txt(cx, cy + 21, sub, 11, MUTED)
    return s


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">'
            f'<rect width="{w}" height="{h}" fill="{WHITE}"/>'
            f'<title>{esc(title)}</title>{body}</svg>')


# ============================================================================
# 1 — SYSTEM ARCHITECTURE : two worlds, two links
# ============================================================================
def d1():
    W, H = 1600, 940
    s = txt(W / 2, 46, "KHOJ SYSTEM ARCHITECTURE", 30, NAVY, bold=True)
    s += txt(W / 2, 76, "The laptop owns reality. The boards own the decisions. They never swap roles.",
             16, MUTED, italic=True)

    # --- the world -----------------------------------------------------------
    s += box(90, 116, 900, 168, LIGHT, SLATE, 2.5, 14)
    s += txt(120, 150, "THE WORLD   ·   laptop", 19, NAVY, anchor="start", bold=True)
    for i, (t, sub) in enumerate([
            ("Physics sim", "drone bodies, terrain"),
            ("RF propagation", "true phone position"),
            ("YOLO detector", "real aerial imagery"),
            ("Dashboard", "+ lawnmower baseline")]):
        bx = 120 + i * 215
        s += box(bx, 168, 195, 92, WHITE, LINE, 1.8, 8)
        s += txt(bx + 97, 200, t, 15, NAVY, bold=True)
        s += txt(bx + 97, 222, sub, 12, MUTED)
    s += txt(540, 274, "holds ground truth  ·  NEVER assigns work", 14, RED, bold=True)

    # --- real drone ----------------------------------------------------------
    s += box(1040, 116, 470, 168, AMBER_L, AMBER, 2.5, 14)
    s += txt(1070, 150, "REAL DRONE   ·   one agent, embodied", 17, NAVY, anchor="start", bold=True)
    s += lines(1275, 186, [
        "F450 airframe + Pixhawk  (flies)",
        "Raspberry Pi: MAVSDK · camera · GPS",
        "plays 'the World' onboard, outdoors"], 13, SLATE)
    s += txt(1275, 268, "same firmware — only the body changes", 13, AMBER, bold=True)

    # --- USB links -----------------------------------------------------------
    # Only the four AGENTS get a sensor feed. The beacon is a victim's phone —
    # it has no laptop link, it only emits.
    for i in range(4):
        x = 220 + i * 215
        s += arrow(x, 284, x, 470, SLATE, 2, 8)
    s += box(920, 336, 300, 74, WHITE, SLATE, 2, 8)
    s += txt(1070, 362, "USB  ·  private, per-agent", 14, NAVY, bold=True)
    s += txt(1070, 384, "sensor packet DOWN  ·  goal UP", 12, MUTED)
    s += arrow(1275, 284, 1275, 470, AMBER, 2, 8)
    s += txt(1352, 384, "UART", 13, AMBER, bold=True)

    # --- the minds -----------------------------------------------------------
    s += box(90, 470, 1420, 300, WHITE, CYAN, 2.5, 14)
    s += txt(120, 506, "THE MINDS   ·   ESP32 agents", 19, NAVY, anchor="start", bold=True)
    s += txt(1480, 506, "each holds its OWN belief map", 13, CYAN, anchor="end", bold=True)

    xs = [220, 435, 650, 865, 1080, 1275]
    labs = ["AGENT 1", "AGENT 2", "AGENT 3", "AGENT 4", "BEACON", "ON-DRONE"]
    for i, (x, lab) in enumerate(zip(xs, labs)):
        f, st = (CYAN_L, CYAN)
        if lab == "BEACON":
            f, st = (RED_L, RED)
        if lab == "ON-DRONE":
            f, st = (AMBER_L, AMBER)
        s += chip(x, 560, 172, 62, lab, f, st)

    # mesh bus
    s += line(160, 656, 1440, 656, CYAN, 3)
    for x in xs:
        s += line(x, 592, x, 656, CYAN, 2)
        s += circle(x, 656, 5, CYAN, CYAN, 1)
    s += txt(800, 692, "ESP-NOW MESH   ·   broadcast, no router, no leader", 17, CYAN, bold=True)
    s += txt(800, 716, "bids  ·  awards  ·  heartbeats  ·  RF samples  ·  dismissals", 13, MUTED)
    s += txt(800, 748, "23-byte packets  ·  5 Hz heartbeat  ·  0.0% measured loss on 5 boards",
             13, NAVY, bold=True)

    # --- footer key ----------------------------------------------------------
    s += box(90, 802, 1420, 96, LIGHT, LINE, 1.8, 10)
    s += txt(120, 834, "WHY IT IS A SWARM AND NOT A PUPPET SHOW", 15, NAVY, anchor="start", bold=True)
    s += txt(120, 862, "The laptop simulates and draws. It never chooses a goal. Every goal on screen was decided",
             13, SLATE, anchor="start")
    s += txt(120, 882, "on a separate microcontroller, from that board's own private belief, and agreed by radio.",
             13, SLATE, anchor="start")
    return svg(W, H, s, "KHOJ System Architecture")


# ============================================================================
# 2 — THE AUCTION ROUND : leaderless consensus
# ============================================================================
def d2():
    W, H = 1600, 900
    s = txt(W / 2, 46, "THE AUCTION ROUND  —  ONE DECISION, NO LEADER", 30, NAVY, bold=True)
    s += txt(W / 2, 76, "Repeats every ~500 ms on every board, in parallel", 16, MUTED, italic=True)

    steps = [
        ("1", "SENSE", ["laptop sends this board", "its private packet:", "position · detection · RSSI"], CYAN),
        ("2", "UPDATE BELIEF", ["drain the 32x32 grid", "where I just looked", "(my opinion, nobody else's)"], CYAN),
        ("3", "ANNOUNCE", ["saw something uncertain?", "broadcast it as a TASK", "to every peer"], AMBER),
        ("4", "BID", ["score EVERY open task:", "expected survivors", "per second"], AMBER),
        ("5", "BROADCAST BIDS", ["every board now holds", "every board's bid —", "identical data"], GREEN),
        ("6", "DECIDE ALONE", ["run the SAME rule:", "highest bid wins,", "tie -> lowest ID"], GREEN),
    ]
    bw, bh, y0 = 232, 196, 140
    for i, (n, t, rows, col) in enumerate(steps):
        x = 60 + i * 250
        s += box(x, y0, bw, bh, WHITE, col, 2.5, 12)
        s += circle(x + 30, y0 + 30, 19, col, col, 1)
        s += txt(x + 30, y0 + 36, n, 17, WHITE, bold=True)
        s += txt(x + bw / 2 + 18, y0 + 36, t, 15, NAVY, bold=True)
        s += lines(x + bw / 2, y0 + 78, rows, 12.5, SLATE)
        if i < 5:
            s += arrow(x + bw + 4, y0 + bh / 2, x + bw + 14, y0 + bh / 2, MUTED, 2.5, 9)

    # the punchline band
    s += box(60, 380, 1480, 128, GREEN_L, GREEN, 2.5, 14)
    s += txt(800, 418, "SAME DATA  +  SAME RULE  =  SAME WINNER, computed independently on 5 chips",
             21, NAVY, bold=True)
    s += txt(800, 450, "No board announces the result. No board is asked. There is no referee to kill —",
             14.5, SLATE)
    s += txt(800, 474, "which is exactly why killing any board changes nothing about how the rest decide.",
             14.5, SLATE)
    s += txt(800, 496, "Task allocation class ST-SR-IA (Gerkey & Mataric) solved by a sequential single-item auction.",
             12, MUTED, italic=True)

    # bid function
    s += box(60, 540, 720, 300, WHITE, NAVY, 2.5, 14)
    s += txt(100, 578, "ONE BID FUNCTION, EVERY TASK TYPE", 17, NAVY, anchor="start", bold=True)
    s += box(100, 596, 640, 56, LIGHT, LINE, 1.5, 8)
    s += txt(420, 632, "bid  =  U(task) · exp( -(t + c) / tau )  /  (c + eps)", 20, NAVY, bold=True)
    s += txt(420, 676, "c = travel TIME, not distance   ·   tau = 60 s survival decay", 13, SLATE)
    s += txt(420, 698, "decay evaluated at ARRIVAL — a survivor reached later is less likely alive",
             12.5, MUTED, italic=True)
    for i, (t, u, col) in enumerate([
            ("FRONTIER", "U = A · p · p_det", CYAN),
            ("REOBSERVE", "U = H(p) · C_FN · viewpoint", AMBER),
            ("CONFIRM_RF", "U = 500  (highest)", RED),
            ("RELAY", "keep the mesh connected", MUTED)]):
        yy = 726 + i * 28
        s += circle(118, yy - 5, 6, col, col, 1)
        s += txt(136, yy, t, 13, NAVY, anchor="start", bold=True)
        s += txt(300, yy, u, 13, SLATE, anchor="start")

    # failure panel
    s += box(820, 540, 720, 300, RED_L, RED, 2.5, 14)
    s += txt(860, 578, "WHEN A BOARD DIES", 17, NAVY, anchor="start", bold=True)
    for i, (n, t) in enumerate([
            ("1", "Heartbeats stop arriving."),
            ("2", "After 2 s of silence, EVERY surviving board independently marks it dead."),
            ("3", "Its tasks return to the pool — no handover, no election."),
            ("4", "Next round re-auctions them. Recovery is emergent, not scripted.")]):
        yy = 616 + i * 46
        s += circle(880, yy - 5, 14, RED, RED, 1)
        s += txt(880, yy, n, 13, WHITE, bold=True)
        s += txt(908, yy, t, 13.5, SLATE, anchor="start")
    s += txt(1180, 812, "VERIFIED ON HARDWARE: detected in under 2 s, 5 boards", 13, RED, bold=True)
    return svg(W, H, s, "The Auction Round")


# ============================================================================
# 3 — RE-OBSERVATION LOOP + HIVE-MIND
# ============================================================================
def d3():
    W, H = 1600, 900
    s = txt(W / 2, 46, "THE RE-OBSERVATION LOOP  —  WHY IT IS A SWARM", 30, NAVY, bold=True)
    s += txt(W / 2, 76, "The swarm reasons together about what it thinks it saw", 16, MUTED, italic=True)

    # confidence band ruler
    s += txt(90, 132, "A single camera look returns a confidence. What happens next depends on where it lands:",
             15, SLATE, anchor="start")
    bx, by, bw2, bh2 = 90, 152, 1420, 62
    s += box(bx, by, bw2 * 0.15, bh2, RED_L, RED, 2, 8)
    s += box(bx + bw2 * 0.15, by, bw2 * 0.5, bh2, AMBER_L, AMBER, 2, 0)
    s += box(bx + bw2 * 0.65, by, bw2 * 0.35, bh2, GREEN_L, GREEN, 2, 8)
    s += txt(bx + bw2 * 0.075, by + 30, "0.00 – 0.15", 13, NAVY, bold=True)
    s += txt(bx + bw2 * 0.075, by + 50, "ignore", 12, SLATE)
    s += txt(bx + bw2 * 0.40, by + 30, "0.15 – 0.80   THE UNCERTAIN BAND", 15, NAVY, bold=True)
    s += txt(bx + bw2 * 0.40, by + 50, "too strong to ignore, too weak to trust  ->  becomes an auctioned TASK", 12.5, SLATE)
    s += txt(bx + bw2 * 0.825, by + 30, "0.80 – 1.00", 13, NAVY, bold=True)
    s += txt(bx + bw2 * 0.825, by + 50, "confirm immediately", 12, SLATE)

    # the loop
    steps = [
        ("Agent 2 sees 0.41", ["half-buried shape", "at grid (14, 9)"], AMBER),
        ("Spawn REOBSERVE", ["broadcast as a task;", "value = information gain"], AMBER),
        ("Agent 5 wins it", ["it is closest AND", "approaches from a", "DIFFERENT bearing"], CYAN),
        ("Second look: 0.63", ["only fused if the angle", "differs by > 20 deg", "(independent evidence)"], CYAN),
        ("Log-odds fusion", ["logodds += logit(conf)", "fused = sigmoid(logodds)", "-> 0.87"], GREEN),
        ("CONFIRMED", ["a real survivor,", "found by two drones", "that each doubted it"], GREEN),
    ]
    y0 = 268
    for i, (t, rows, col) in enumerate(steps):
        x = 60 + i * 250
        s += box(x, y0, 232, 176, WHITE, col, 2.5, 12)
        s += box(x, y0, 232, 34, col, col, 0, 12)
        s += txt(x + 116, y0 + 23, t, 14, WHITE, bold=True)
        s += lines(x + 116, y0 + 66, rows, 12.5, SLATE)
        if i < 5:
            s += arrow(x + 236, y0 + 88, x + 246, y0 + 88, MUTED, 2.5, 9)

    # dismissal / hive mind
    s += box(60, 486, 720, 174, RED_L, RED, 2.5, 14)
    s += txt(100, 524, "THE OTHER OUTCOME  —  DISMISSAL", 17, NAVY, anchor="start", bold=True)
    s += txt(100, 552, "Hot machinery reads 0.34, then 0.11 from a second angle.", 13.5, SLATE, anchor="start")
    s += txt(100, 574, "Fused it falls below 0.15 — the swarm rules it out.", 13.5, SLATE, anchor="start")
    s += txt(100, 604, "HIVE-MIND: the dismissal is BROADCAST. Every board records", 13.5, NAVY, anchor="start", bold=True)
    s += txt(100, 626, "the spot. No drone ever wastes a second look on it again.", 13.5, NAVY, anchor="start", bold=True)
    s += txt(100, 650, "\"The individual forgets; the swarm remembers.\"", 13, RED, anchor="start", italic=True)

    # why it matters
    s += box(820, 486, 720, 174, LIGHT, NAVY, 2.5, 14)
    s += txt(860, 524, "WHY THIS IS THE WHOLE POINT", 17, NAVY, anchor="start", bold=True)
    s += txt(860, 552, "It lets us run the detector at a LOW threshold — catching the", 13.5, SLATE, anchor="start")
    s += txt(860, 574, "half-buried and occluded victims a normal system throws away —", 13.5, SLATE, anchor="start")
    s += txt(860, 596, "without burying rescuers in false alarms, because every marginal", 13.5, SLATE, anchor="start")
    s += txt(860, 618, "detection gets a second, independent look from a new angle.", 13.5, SLATE, anchor="start")
    s += txt(860, 650, "In SAR a false positive costs 3 minutes. A false negative costs a life.",
             13.5, RED, anchor="start", bold=True)

    # the Q&A answer
    s += box(60, 690, 1480, 156, WHITE, GREEN, 2.5, 14)
    s += txt(800, 728, "\"Why is this a swarm and not five drones with a shared to-do list?\"", 20, NAVY, bold=True)
    s += txt(800, 766, "Because a to-do list divides work. This divides DOUBT. One drone's uncertainty becomes", 15, SLATE)
    s += txt(800, 792, "another drone's task, priced by how much that second look would reduce what the swarm", 15, SLATE)
    s += txt(800, 818, "does not yet know — and the conclusion belongs to all of them, not to whoever looked first.", 15, SLATE)
    return svg(W, H, s, "Re-observation Loop")


# ============================================================================
# 4 — COOPERATIVE RF LOCALIZATION (the hero)
# ============================================================================
def d4():
    W, H = 1600, 900
    s = txt(W / 2, 46, "COOPERATIVE RF LOCALIZATION  —  FINDING THE INVISIBLE", 30, NAVY, bold=True)
    s += txt(W / 2, 76, "No drone can see the phone. No drone can find it alone.", 16, MUTED, italic=True)

    # --- field ---------------------------------------------------------------
    fx, fy, fw, fh = 70, 116, 700, 620
    s += box(fx, fy, fw, fh, LIGHT, LINE, 2, 12)
    for i in range(1, 10):
        s += line(fx + fw * i / 10, fy, fx + fw * i / 10, fy + fh, "#DCE5EC", 1)
        s += line(fx, fy + fh * i / 10, fx + fw, fy + fh * i / 10, "#DCE5EC", 1)

    px, py = fx + fw * 0.52, fy + fh * 0.46
    for rr, op in ((190, "#E7F4F6"), (140, "#D2EAEE"), (92, "#B8DFE5"), (48, "#8FCFD8")):
        s += circle(px, py, rr, op, "none", 0)
    s += circle(px, py, 15, RED, RED, 2)
    s += txt(px, py + 44, "HIDDEN PHONE", 14, RED, bold=True)
    s += txt(px, py + 64, "no visual signature", 12, MUTED)

    # agents pushed to the corners with their labels on the OUTSIDE, so no
    # sample arrow ever crosses the phone's caption
    agents = [(fx + 80, fy + 80, "-71", -32), (fx + 610, fy + 95, "-58", -32),
              (fx + 85, fy + 505, "-64", 40), (fx + 600, fy + 520, "-49", 40)]
    for i, (ax, ay, r, dy) in enumerate(agents):
        s += arrow(ax, ay, px + (ax - px) * 0.42, py + (ay - py) * 0.42, CYAN, 2.2, 9, "7,5")
        s += circle(ax, ay, 21, CYAN_L, CYAN, 2.5)
        s += txt(ax, ay + 5, str(i + 1), 15, NAVY, bold=True)
        s += txt(ax, ay + dy, r + " dBm", 12.5, CYAN, bold=True)

    ex, ey = px - 58, py - 46
    s += circle(ex, ey, 11, "none", AMBER, 3)
    s += line(ex - 20, ey, ex + 20, ey, AMBER, 2)
    s += line(ex, ey - 20, ex, ey + 20, AMBER, 2)
    s += txt(ex - 24, ey - 26, "swarm estimate", 12.5, AMBER, bold=True, anchor="end")
    s += txt(fx + fw / 2, fy + fh + 26, "Each agent samples where it happens to be. Alone, each reading is meaningless.",
             13.5, SLATE)

    # --- method --------------------------------------------------------------
    s += box(810, 116, 720, 300, WHITE, NAVY, 2.5, 14)
    s += txt(850, 154, "THE METHOD  —  GRADIENT, NOT TRILATERATION", 17, NAVY, anchor="start", bold=True)
    s += box(850, 172, 640, 92, LIGHT, LINE, 1.5, 8)
    s += txt(1170, 206, "w = 10 ^ ( (RSSI + 100) / 20 )", 19, NAVY, bold=True)
    s += txt(1170, 240, "estimate = SUM(w · position) / SUM(w)", 19, NAVY, bold=True)
    s += txt(1170, 288, "Pool every agent's sample over the mesh. Weight by signal amplitude,", 13, SLATE)
    s += txt(1170, 310, "so the strongest readings pull hardest. Fly there. Resample. Converge.", 13, SLATE)
    s += txt(1170, 344, "A fresh bearing cuts uncertainty far more than a nearby repeat —", 13, AMBER, italic=True)
    s += txt(1170, 366, "the same viewpoint-diversity term the vision tasks use. One equation.", 13, AMBER, italic=True)
    s += txt(1170, 396, "No path-loss calibration. No geometry requirement. No matrix solve.", 13.5, NAVY, bold=True)

    # --- why not trilateration ----------------------------------------------
    s += box(810, 436, 350, 300, RED_L, RED, 2.5, 14)
    s += txt(985, 474, "WHY NOT TRILATERATION", 15, NAVY, bold=True)
    s += lines(985, 508, [
        "Needs a calibrated", "path-loss model and", "good geometry.",
        "", "Rubble is noisy and", "multipath-wrecked —", "your calibration is",
        "wrong the moment the", "environment changes."], 12.5, SLATE)

    s += box(1180, 436, 350, 300, GREEN_L, GREEN, 2.5, 14)
    s += txt(1355, 474, "WHY THE GRADIENT WINS", 15, NAVY, bold=True)
    s += lines(1355, 508, [
        "Only ever asks:", "\"stronger here, or", "there?\"",
        "", "Survives concrete,", "jamming and a hall", "full of phones.",
        "Cheap enough to run", "on a $3 chip."], 12.5, SLATE)

    # --- proof band ----------------------------------------------------------
    s += box(70, 786, 1460, 92, AMBER_L, AMBER, 2.5, 14)
    s += txt(100, 818, "ALREADY PROVEN ON HARDWARE", 16, NAVY, anchor="start", bold=True)
    s += txt(100, 844, "Every board reads a peer's signal strength directly from the ESP-NOW receive callback — no promiscuous",
             13.5, SLATE, anchor="start")
    s += txt(100, 866, "mode, no extra radio. Measured live across 5 boards: -23 dBm close, -72 dBm across the room.",
             13.5, SLATE, anchor="start")
    return svg(W, H, s, "Cooperative RF Localization")


# ============================================================================
# 5 — CENTRALIZED vs KHOJ
# ============================================================================
def d5():
    W, H = 1600, 840
    s = txt(W / 2, 46, "WHY DECENTRALIZED  —  AND WHY THAT IS THE RIGHT CALL HERE", 29, NAVY, bold=True)
    s += txt(W / 2, 76, "Every 1000-drone light show you have seen is centralized. That architecture cannot do disaster response.",
             15, MUTED, italic=True)

    # left: centralized
    s += box(70, 116, 700, 470, RED_L, RED, 2.5, 14)
    s += txt(420, 152, "CENTRALIZED   (DJI shows, most military)", 18, NAVY, bold=True)
    gx, gy = 420, 250
    s += box(gx - 78, gy - 30, 156, 58, WHITE, RED, 2.5, 8)
    s += txt(gx, gy + 5, "GROUND STATION", 13, NAVY, bold=True)
    for i in range(6):
        import math
        a = math.pi * (0.12 + 0.76 * i / 5)
        # vertical spread kept tight so the lowest node clears the caption below
        dx, dy = gx + 250 * math.cos(a), gy + 160 * math.sin(a) + 26
        s += arrow(gx, gy + 30, dx, dy - 14, RED, 1.8, 8)
        s += circle(dx, dy, 15, WHITE, RED, 2)
    s += txt(420, 470, "computes EVERY drone's trajectory  ·  O(N) compute", 14, NAVY, bold=True)
    s += txt(420, 494, "one brain  ·  one uplink  ·  one point of failure", 13, SLATE)
    for i, t in enumerate([
            "Needs RTK-GPS (+/- 2 cm) and a permanent uplink",
            "One failure point — the station dies, the show ends",
            "Useless offline, indoors, or in a collapsed building"]):
        s += txt(110, 528 + i * 22, "x   " + t, 13, SLATE, anchor="start")

    # right: khoj
    s += box(830, 116, 700, 470, GREEN_L, GREEN, 2.5, 14)
    s += txt(1180, 152, "KHOJ   (decentralized)", 18, NAVY, bold=True)
    import math
    cx, cy, R = 1180, 300, 118
    pts = []
    for i in range(6):
        a = -math.pi / 2 + 2 * math.pi * i / 6
        pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
    for i in range(6):
        for j in range(i + 1, 6):
            s += line(pts[i][0], pts[i][1], pts[j][0], pts[j][1], GREEN, 1.2)
    for i, (x, y) in enumerate(pts):
        s += circle(x, y, 21, WHITE, GREEN, 2.5)
        s += txt(x, y + 5, str(i + 1), 13, NAVY, bold=True)
    s += txt(1180, 470, "each board computes only for ITSELF  ·  O(1), all in parallel", 14, NAVY, bold=True)
    s += txt(1180, 494, "peer-to-peer  ·  no station  ·  no uplink  ·  no leader", 13, SLATE)
    for i, t in enumerate([
            "Runs on a $3 chip that already bolts to an airframe",
            "Kill any node — the rest re-auction its work in ~2 s",
            "Works offline, indoors, in a disaster zone. That is the point."]):
        s += txt(870, 528 + i * 22, "+   " + t, 13, SLATE, anchor="start")

    # honest trade-off
    s += box(70, 616, 1460, 190, WHITE, NAVY, 2.5, 14)
    s += txt(100, 654, "THE HONEST TRADE-OFF   (we say this before a judge asks)", 17, NAVY, anchor="start", bold=True)
    s += txt(100, 686, "Decentralized does NOT scale to 1000 drones — the radio would saturate — and each decision round",
             14, SLATE, anchor="start")
    s += txt(100, 710, "costs ~500 ms instead of being instant. For a 1000-drone light show, centralized is simply better.",
             14, SLATE, anchor="start")
    s += txt(100, 746, "For six drones in a collapsed building with no infrastructure and no guarantee every drone survives",
             14.5, NAVY, anchor="start", bold=True)
    s += txt(100, 770, "the hour, decentralized is not a preference. It is the only architecture that still works.",
             14.5, NAVY, anchor="start", bold=True)
    return svg(W, H, s, "Centralized vs KHOJ")


# ============================================================================
# 6 — RESULTS
# ============================================================================
def d6():
    W, H = 1600, 880
    s = txt(W / 2, 46, "MEASURED RESULTS  —  AGAINST A REAL BASELINE", 30, NAVY, bold=True)
    s += txt(W / 2, 76, "Identical worlds, identical drone count. The only difference is how they decide.",
             16, MUTED, italic=True)

    # bar chart
    s += box(70, 116, 760, 480, WHITE, LINE, 2, 14)
    s += txt(450, 154, "SURVIVORS CONFIRMED  (out of 5)", 17, NAVY, bold=True)
    base_y, bh_max = 500, 260
    seeds = [("seed 0", 5, 3), ("seed 3", 5, 4), ("seed 7", 5, 3), ("seed 11", 4, 4)]
    for i, (lab, k, l) in enumerate(seeds):
        gx0 = 140 + i * 172
        for j, (v, col, nm) in enumerate([(k, CYAN, "KHOJ"), (l, MUTED, "baseline")]):
            h = bh_max * v / 5
            bx2 = gx0 + j * 62
            s += box(bx2, base_y - h, 52, h, col, col, 0, 5)
            s += txt(bx2 + 26, base_y - h - 10, str(v), 17, NAVY, bold=True)
        s += txt(gx0 + 57, base_y + 26, lab, 13, SLATE)
    s += line(120, base_y, 800, base_y, SLATE, 2)
    s += circle(150, 556, 8, CYAN, CYAN, 1)
    s += txt(166, 561, "KHOJ", 14, NAVY, anchor="start", bold=True)
    s += circle(260, 556, 8, MUTED, MUTED, 1)
    s += txt(276, 561, "camera-only lawnmower sweep", 14, SLATE, anchor="start")

    # stat cards
    cards = [
        # 4-5, not 5/5 — seed 11 returned 4, and it is on the chart directly
        # beside this card. Never let a headline stat contradict your own graph.
        ("4-5", "of 5 survivors confirmed", "baseline: 3-4, every run", CYAN, CYAN_L),
        ("100%", "RF victim found", "baseline: never — it has no radio", RED, RED_L),
        ("3-4", "false alarms auto-dismissed", "before any rescuer was sent", GREEN, GREEN_L),
        ("0.0%", "mesh packet loss", "measured across 5 real boards", NAVY, LIGHT),
    ]
    for i, (big, lab, sub, col, fill) in enumerate(cards):
        x = 870
        y = 116 + i * 122
        s += box(x, y, 660, 106, fill, col, 2.5, 12)
        s += txt(x + 108, y + 66, big, 40, col, bold=True)
        s += txt(x + 220, y + 48, lab, 16, NAVY, anchor="start", bold=True)
        s += txt(x + 220, y + 76, sub, 13, SLATE, anchor="start")

    # honesty band
    s += box(70, 632, 1460, 210, LIGHT, NAVY, 2.5, 14)
    s += txt(100, 670, "WHAT IS REAL AND WHAT IS SIMULATED   (we draw this line ourselves)", 17, NAVY, anchor="start", bold=True)
    s += txt(110, 706, "REAL", 15, GREEN, anchor="start", bold=True)
    for i, t in enumerate([
            "The auction running on 5 separate microcontrollers",
            "The ESP-NOW radio link, measured at 0.0% loss",
            "Failure detection and re-auction after a node dies",
            "Per-agent signal strength, live on hardware"]):
        s += txt(110, 730 + i * 24, "·  " + t, 13.5, SLATE, anchor="start")
    s += txt(830, 706, "SIMULATED", 15, AMBER, anchor="start", bold=True)
    for i, t in enumerate([
            "Terrain — a photograph, not a place",
            "Drone motion — the boards decide, they do not fly",
            "One real F450 flies the confirmation outdoors"]):
        s += txt(830, 730 + i * 24, "·  " + t, 13.5, SLATE, anchor="start")
    s += txt(830, 806, "This is hardware-in-the-loop. PX4 ships a HITL mode; it is standard practice.",
             13.5, NAVY, anchor="start", bold=True)
    return svg(W, H, s, "Measured Results")


# ============================================================================
DIAGRAMS = [
    ("01_architecture", d1),
    ("02_auction_round", d2),
    ("03_reobservation", d3),
    ("04_rf_localization", d4),
    ("05_centralized_vs_khoj", d5),
    ("06_results", d6),
]


def main():
    """Write the SVGs only.

    PNG rasterising is deliberately NOT done here. Every Python SVG rasteriser
    on Windows (cairosvg, rlPyCairo) needs the native Cairo DLL, which Windows
    does not ship — and rlPyCairo raises OSError at *import* time, which is
    easy to mistake for a missing package. Run svg_to_png.ps1 afterwards; it
    uses headless Edge, which is already installed and renders text correctly.
    """
    for name, fn in DIAGRAMS:
        sp = os.path.join(OUT, name + ".svg")
        with open(sp, "w", encoding="utf-8") as f:
            f.write(fn())
        print("wrote", sp)
    print("\nNow run:  powershell -ExecutionPolicy Bypass -File .\\svg_to_png.ps1")


if __name__ == "__main__":
    main()
