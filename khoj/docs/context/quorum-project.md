---
name: quorum-project
description: KHOJ (was QUORUM) — 48h hackathon SAR drone swarm; merged plan from two parallel Claude sessions
metadata: 
  node_type: memory
  type: project
  originSessionId: 690fe186-4e0c-4b47-b79d-66f6232f1a2a
  modified: 2026-08-19T21:23:40.367Z
---

QUORUM: 48-hour hackathon project (Innohack 26, VIT), problem "Autonomous Drone Swarm
for Search and Rescue" (DIOT-01, tagged SDG 9/11/16). Novelty-weighted judging. 4-person
team, hardware-strong (user Rishit = 3rd-yr ECE: ESP32/Arduino/Teensy/Embedded C/C++/
FreeRTOS/PID/KiCad; competent-not-expert Python). Repo /home/rishits/Desktop/quorum.

**Two Claude sessions (Rishit's + teammate Raksh's) planned independently and CONVERGED
on the same architecture, then merged.** Name QUORUM won over "KHOJ". Our byte-verified
message contract (quorum_proto.h + protocol.py) is CANONICAL — the other session
discarded theirs. See [[quorum-wire-contract]]. **Project name FINAL = KHOJ** (user
reverted from QUORUM on 2026-08-20; tell Raksh's session. Code identifiers/dir still say
"quorum" — pending a rename sweep to khoj_*).

**Concept:** leaderless swarm that finds people it can't see. **RF localization = hero
demo** (judge hides an *emitting* device, swarm gradient-climbs to it — NOT a sleeping
phone; must be actively emitting). **Re-observation loop = intellectual spine** (a low-
confidence sighting becomes an auctioned task; another agent re-observes from a different
angle; fusion confirms/dismisses — this is why it's a swarm, not a shared to-do list).
**Unified:** all 3 task types (FRONTIER search / REOBSERVE vision / RF_GRADIENT) are ONE
auction, one bid function, one survivor-probability grid. Bids in information gain.

**Bid function (the technical spine):**
bid = U * exp(-(t_now+c)/tau) / (c+eps)   [expected survivors/sec]; c = TIME not distance.
- FRONTIER  U = A*p_bar*p_det
- REOBSERVE U = H(p)*C_FN*(1 - exp(-dTheta/theta0))   (viewpoint-diversity term)
- RF_GRAD   U = H(p_src)*C_FN*(1 - exp(-dBaseline/b0)) (same term, geometric baseline)
survival-decay evaluated at ARRIVAL time (urgency, not distance). ST-SR-IA (Gerkey-
Mataric), sequential single-item auction (Lagoudakis 2005). Use gradient-seeking for RF,
NOT trilateration (cheaper, robust, no path-loss calibration).

**Architecture:** laptop = "the world" (physics sim, RF propagation model, YOLO on REAL
aerial imagery HERIDAL/SARD cropped per footprint, dashboard, lawnmower baseline+metrics)
— NEVER assigns work. ESP32s decide over ESP-NOW. USB carries private per-agent sensor
packet down + goal up. Real F450 (Pixhawk+RPi+camera+sniffer) = drop-in body swap for one
agent; RPi plays "world" onboard (MAVSDK-Python, owns grid<->GPS). Fly outdoors + RECORD;
demo indoors tethered/hand-carried (flight NEVER on the live critical path). Dedicated
sniffer board (ESP-NOW vs promiscuous fight over radio). Belief grid 32x32.

**Dual backend (fallback + insurance):** AGENT_BACKEND = "python" | "esp32". B writes
PythonAgent reference; A(Rishit) ports auction to C. Hardware is a plugin, never a
dependency (user has 1 board in hand, ~7 on order — Python path is primary until boards land).

**Team split (roles; confirm real name<->role):** A(Rishit)=firmware/ESP-NOW/auction/owns
contract. B=laptop sim + RF model + bid math + PythonAgent reference. C=perception(YOLO)+
eval/metrics h0-20 then F450/Pixhawk/RPi h20+. D=sniffer firmware + beacon h0-8 then
dashboard + pitch. Raksh = one of B/C/D (unconfirmed).

**Timeline:** freeze hour 32, backup video recorded by 36, rehearse 36-44, buffer 44-48.
Sleep shifts 2-down/2-up ~h16-20. Hardware de-risk EARLY (h1-5), hardware features LATE.
Scope tiers: T1 Python swarm+auction+reobs+dashboard+baseline (never cut); T2 ESP mesh;
T3 real drone + ESP-on-drone; T4 flex (2x PX4 SITL headless, SDG16 hash-chain evidence log).

**Buy:** 8x identical ESP32-WROOM-32 (6 agents+1 sniffer+1 spare), powered USB hub, data
cables, prop guards, power bank, 4-way power strip. Needs a GPU laptop for real YOLO.

**Open (human calls):** name<->role map; rubric (does it weight SDGs?); board arrival date
(if late, Python is primary); outdoor flight slot/permission/weather; which laptop has GPU.
Never cut: the auction, the RF/re-observation confirmation, the lawnmower baseline.

**3 grafts added (from a 3rd ideas doc) 2026-08-20:** (1) Hive Mind = shared dismissal
memory (a dismissed detection is broadcast so no agent re-investigates it — CORE, baked
into the re-observation loop). (2) RELAY = a 4th auction task type (an agent bids to
reposition as a comms relay when a peer's link degrades — FLEX, hits SDG 9, "disaster
infrastructure"). (3) Rescue-route = on CONFIRM, laptop computes a ground-rescuer path to
the survivor (FLEX polish, closing demo beat). Reject: survivor-webpage, payload delivery,
ARES 5-pillar combine, Digital Twin ML model.

Roles CONFIRMED: user Rishit = A (firmware/auction/ESP mesh, ports the Python auction to C).
Build status: Module 1 "the wire" done+verified. Building Tier-1 Python core (sim + Agent +
unified auction + re-observation + Hive-Mind + RF gradient) as the reference/fallback.
