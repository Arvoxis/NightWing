# KHOJ — Complete Knowledge Handoff

> Full state dump of the planning + build so far, written so a fresh Claude Code
> (or any AI) with ZERO prior context can continue seamlessly. Read top to bottom
> once. Nothing here should need re-deriving. Code lives in this same repo; this
> doc is the reasoning and decisions behind it.

---

## 0. How to use this document

- This captures a long multi-session planning + build conversation. Treat every
  decision marked **LOCKED** as settled — do not re-litigate without new info.
- The user is **Rishit** (owns the decentralized/firmware stack). Teammate
  **Rakshit** runs a parallel Claude Code session (owns the laptop software stack).
- After reading, you may also load the memory files (`MEMORY.md`,
  `quorum-project.md`, `quorum-wire-contract.md`) if present — they summarise this.

---

## 1. Project identity & event

- **Name: KHOJ** (Hindi "search / the quest"). **LOCKED.** (Earlier candidate
  "QUORUM" was dropped; some code identifiers still say `quorum_*` pending a rename
  sweep — functionally irrelevant, don't let it confuse you.)
- **Event:** Innohack 26, VIT. 48-hour hackathon, large, external participants,
  real prize pool. **Judging is novelty-weighted** (uniqueness > feature count).
- **Problem statement DIOT-01:** "Autonomous Drone Swarm for Search and Rescue
  Operations — a fleet of autonomous drones coordinating SAR in disaster areas."
  Tagged **SDG 9, 11, 16**. That one sentence is the whole brief; the rest is our
  interpretation.
- **Goal: win.** Secondary: resume / TUM masters application material.

**One-line pitch:** *Other swarms search together. KHOJ finds the invisible.*
A leaderless drone swarm that finds people cameras can't see, by cooperatively
locating a trapped victim's phone via its radio signal.

---

## 2. Team & division of labor (LOCKED)

- **Rishit (user) = the DECENTRALIZED STACK.** Everything that runs *on the boards*
  and *between the boards*: the ESP32/ESP8266 ESP-NOW mesh, the on-device auction,
  and all the RSSI work. Strong embedded (ESP32, Arduino, Teensy, Embedded C/C++,
  FreeRTOS, PID, KiCad). Competent-not-expert on Python — **explain architectural
  decisions plainly.**
- **Rakshit = the WHOLE LAPTOP SOFTWARE STACK.** World simulator, dashboard,
  perception (YOLO), metrics. Runs a separate Claude Code session.
- **Two other members** exist (4-person team) but their name↔role mapping is not
  yet confirmed. Original abstract split had: A=firmware/auction, B=sim/RF-model/bid
  math, C=perception→then F450/Pixhawk/RPi, D=RF sniffer→then dashboard+pitch.
  Rishit = A. Confirm the rest with the team.
- **The seam between the two stacks = the USB serial contract** (`quorum_proto.h`).
  Both stacks are built and tested independently, then integrated only through that
  contract at the end.

---

## 3. Hardware inventory & purchases

- **In hand:** 1 ESP32; ~7 more on order. **Buy 8 identical classic ESP32-WROOM-32**
  (board id `esp32dev`): 6 agents + 1 dedicated RF sniffer + 1 spare (the spare is
  non-negotiable — a dead board at hour 30 with no replacement is a lost night).
- ESP8266 boards optional (can serve as extra light agents or as the victim beacon).
- **Also buy:** powered USB hub (unpowered browns out with ≥4 boards), data-quality
  USB cables (not charge-only), prop guards, a power bank, a 4-way power strip.
- **Real drone:** one F450-class airframe + Pixhawk flight controller + Raspberry Pi
  + camera + (optional) onboard sniffer. **Outdoor flight, GPS available.**
- **A GPU laptop is required** for real YOLO on aerial imagery (~40 inferences/sec).

---

## 4. The idea — final concept + how we got here

**LOCKED concept:** a leaderless SAR swarm whose hero capability is **cooperative
RF localization** — several agents share phone signal-strength (RSSI) samples over
their mesh and gradient-climb to a phone none of them can see. The **re-observation
loop** is the intellectual spine (below). One real F450 flies the confirmation.

**Path we walked to get here (so you don't re-suggest dead ends):**
- Started from a spec (QUORUM_CONTEXT.md) centred on decentralized auction + RF +
  uncertainty-as-a-task.
- Considered and **REJECTED**: real-drone-leads / sim-drones-follow (leader-follower
  contradicts the leaderless novelty, and full Pixhawk+ROS autonomous flight is a
  48h scope trap); acoustic victim detection (whole extra subsystem); WiFi-CSI
  through-wall sensing (too high-risk in 48h); a physical multi-rover swarm (real but
  heavy on build time).
- User pushed for a "shock the judges / crazy" ceiling → landed on **"find the
  hidden phone"** as the hero, because it's visceral, participatory, publishable,
  and transfers directly to real drones.
- A third ideas doc (8 concepts + "ARES" 5-in-1) was reviewed: it **validated** our
  direction (its top-ranked ideas are exactly our hero + spine). We took 3 small
  grafts from it (§14) and rejected the "combine 5 pillars" trap.

---

## 5. The novelty (say this to judges)

The swarm **bids in information gain, not area.** Routine search, a second look at
an uncertain sighting, and triangulating an invisible phone all become the *same
decision*: "where will my next move most reduce what the swarm doesn't yet know?"

- **RF localization = the hero demo** (unforgettable moment).
- **Re-observation loop = the intellectual spine** (answers the hardest Q&A question:
  *"why is this a swarm and not five drones with a shared to-do list?"* — because the
  swarm reasons *together* about what it thinks it saw).
- They unify: an RF gradient hit and a weak visual hit are both evidence updating the
  *same* survivor-probability grid. That single sentence turns two features into one.
- Correct citations for credibility: task allocation class **ST-SR-IA** (Gerkey–
  Matarić taxonomy), solved by a **sequential single-item (SSI) auction** (2-competitive
  for MiniSum, Lagoudakis et al. 2005 — our dynamic variant doesn't inherit the bound
  cleanly; say so out loud). Belief/search theory is classic (found the USS Scorpion,
  Air France 447). State honestly that the auction and belief map are *sound
  engineering*, not the novelty — the novelty is the seam + the RF hero.

---

## 6. Architecture (LOCKED)

**Two worlds:**
- **Laptop = "the world."** Holds ground truth (victim positions, hidden phone,
  terrain), runs the physics sim + YOLO + dashboard + lawnmower baseline + metrics.
  **It NEVER assigns work** — it simulates and draws. This is the difference between
  a swarm demo and a puppet/dashboard demo, and the first thing a judge probes.
- **ESP32s = the minds.** Each holds its own private belief map, computes its own
  bids, decides its own goal. They negotiate peer-to-peer.

**Two links, never mixed:**
- **ESP-NOW = agent ↔ agent** (bids, awards, heartbeats, RF samples, dismissals).
  Broadcast, no router, no pairing, ≤250-byte packets.
- **USB serial = agent ↔ its own physics engine** (laptop, or the RPi for the real
  drone). Private per-agent sensor packet *down*, chosen goal *up*.

**Belief map:** 32×32 grid, one per agent. **Kept UNNORMALISED on purpose** — bidding
only needs relative cell values, so renormalising to sum-to-1 changes nothing about
which cell is most valuable; skipping it also kills the distributed-consensus headache
on the mesh. Searched cells are drained multiplicatively (×0.15). The laptop may
renormalise only its display copy.

**Real drone = a drop-in body swap for ONE agent:** same ESP firmware, but its
"world" is the onboard **RPi** (MAVSDK-Python for waypoint flight, real camera, real
RSSI) instead of the laptop. **Fly it outdoors and RECORD it** (becomes B-roll); on
stage **demo indoors tethered/hand-carried** — outdoor flight NEVER on the live
critical path (wind/battery/space/permission). Grid↔GPS mapping lives on the RPi.

---

## 7. The auction (the core; Rishit ports this to C from `swarm.py`)

**One auction, four task types, one currency (expected survivors / second):**

```
bid(agent, task) = U(task) · exp(-(t_now + c) / tau) / (c + eps)
  c   = distance_to_task / speed + t_obs        (cost is TIME, not distance)
  tau = 60 s  (survival decay, evaluated at ARRIVAL time -> distant tasks penalised
               superlinearly: a survivor found later is less likely alive)
  eps = 0.1

U by task type:
  FRONTIER    U = A · p_bar · p_det                          (routine search; implicit,
                each agent picks its best unsearched high-belief cell, repelled from
                peers' goals so the swarm spreads without a per-cell auction)
  REOBSERVE   U = H(p) · C_FN · (1 - exp(-dTheta/theta0))    (H = binary entropy;
                C_FN≈100 = false-negative cost; theta0≈60°; viewpoint-diversity term
                means a second look from the SAME bearing gains nothing)
  CONFIRM_RF  U = 500  (highest value — invisible victims are rarest/most valuable)
  RELAY       (flex, stubbed) reposition as a comms relay when a peer's link degrades
```

**The viewpoint-diversity term is the merge's key unifier:** for vision it's the
difference in viewing angle; for RF it's the geometric baseline between samples (a
sample near an existing one adds little; a fresh bearing sharply cuts source-location
uncertainty). Same equation, same code path, all task types.

**Consensus WITHOUT a leader:** every ~500 ms — ANNOUNCE tasks → each agent computes
its bids → broadcast bids → every agent runs the SAME deterministic rule (**highest
bid wins; ties → lowest agent ID**) on the SAME data, so they all reach the SAME
winner with no referee. Sequential greedy: highest-value tasks claim their best free
agent first.

**Failure detection / self-healing:** HEARTBEAT ~2 Hz. No message from agent k for
~2 s (4 missed) → all agents independently drop k and return its tasks to the pool →
next auction round reassigns them. This is the "yank a node's power" demo beat — the
recovery is emergent, not scripted.

---

## 8. Re-observation + fusion + Hive-Mind (LOCKED; Hive-Mind is core, built)

- A detection with confidence in the uncertain band (≈0.25–0.65) becomes a
  **REOBSERVE task**. Another agent bids and re-looks from a **different bearing**.
- Confidences fuse via **log-odds** (Bayesian): `logodds += logit(conf)`, only
  fusing genuinely new viewpoints (>20° apart) to keep the looks conditionally
  independent. `fused = sigmoid(logodds)`.
- `fused ≥ 0.80` → **CONFIRM** (real survivor). `fused ≤ 0.15` → **DISMISS**.
- **Hive-Mind:** a dismissal is broadcast; every agent adds the spot to a "dismissed"
  set and won't re-investigate it. *"The individual forgets; the swarm remembers."*
- Why this matters: it lets us run the detector at a **low threshold** (catching
  half-buried/occluded victims a normal system discards) without flooding responders
  with false alarms — every marginal detection gets a second independent look. In SAR
  a false positive costs ~3 minutes; a false negative costs a life.

---

## 9. RF localization (Rishit's RF part)

- **Gradient-seeking, NOT trilateration.** No path-loss calibration, no geometry
  requirements — just "is the signal stronger here or there, move toward stronger."
  Robust to a jammed hall and concrete walls. Implementation: pool shared RSSI
  samples → signal-weighted centroid (weight grows with amplitude) → that's the
  source estimate → an agent bids CONFIRM_RF and flies there → resample → converge.
- **The RSSI beacon trick (resolves the radio conflict):** make the "victim phone" an
  **ESP-NOW beacon broadcasting on the mesh channel.** Every agent then reads its RSSI
  *for free* through the normal ESP-NOW receive callback (`info->rx_ctrl->rssi`) — **no
  promiscuous mode needed**, so the ESP-NOW-vs-promiscuous conflict disappears. Agents
  tag each reading with their position and broadcast `RF_SAMPLE`.
- **TOOLCHAIN TRAP (verified on hardware toolchain 2026-08-20):** that free RSSI exists
  **only on arduino-esp32 core 3.x**. Official PlatformIO's core stops at **2.0.17**, whose
  ESP-NOW receive callback is `(mac, data, len)` — no `rx_ctrl`, no RSSI, so the whole
  cooperative gradient is impossible on it. `firmware/platformio.ini` is therefore pinned to
  the **pioarduino** fork (builds as core 3.3.11 / ESP-IDF 5.5.5). A dedicated sniffer board
  does NOT rescue this: the gradient needs *each agent* sampling at *its own* position.
  Every board prints `rx_rssi=YES` or `rx_rssi=NO-core2.x` at boot — if you ever see the
  latter, the platform pin got reverted and P5 is dead until you restore it.
- **Only** for detecting a *real, unknown* phone's probe requests do you need a
  dedicated promiscuous-mode sniffer board (which cannot also run ESP-NOW — hence
  dedicated). That's an optional credibility stretch. For the demo, use the beacon
  (also per the rule: **plant a beacon, never trust a sleeping phone** — modern phones
  randomise MACs and suppress probes when idle).
- **Demo-honesty catch:** the hero beat "judge hides their phone" must use a device
  that is *actively emitting* (a phone running a ping app, or the ESP beacon) — not a
  locked idle phone. Rehearse RF in the actual venue; graceful degrade = "localised to
  this 3×3 m zone" still counts. The recorded backup MUST include a clean RF run.

---

## 10. The message contract (`firmware/lib/quorum_proto/quorum_proto.h`, mirrored in `sim/protocol.py`) — CANONICAL, byte-verified

- **Serial framing (USB, both ways):** `[0xAA][0x55][len:1][payload:len][xor:1]`,
  xor over payload only, len ≤ 64. Little-endian, packed structs.
- **ESP-NOW `quorum_msg_t` (23 B):** msg_type, agent_id, task_id, value(float),
  x(float), y(float), rssi(int8), timestamp(uint32), seq(uint16). Types:
  HEARTBEAT/BID/AWARD/TASK_SPAWN/RF_SAMPLE.
- **USB down `usb_sensor_t` (28 B, fmt `<BBffhBBffBbI`):** the private sensor packet
  (position, heading, battery, detection {x,y,conf}, rssi, tick).
- **USB up `usb_goal_t` (17 B, fmt `<BBffBHI`):** the decision (goal x/y, state, task).
- **Rule:** change nothing here without updating both files in the same commit and
  telling all owners. Two divergent protocol files is the #1 silent demo-killer.

---

## 11. What's built + verified

- **Module 1 "the wire"** ✅ — `quorum_proto.h` + `protocol.py` + wire-test firmware
  (`firmware/src/agent/main.cpp`) + `sim/wire_test.py`. Struct sizes verified (28/17 B);
  framer survives garbage + partial syncs. Not yet flashed on hardware (1 board so far).
- **Tier-1 Python core** ✅ (runs headless, stdlib only, no deps):
  - `sim/world.py` — the universe: physics, RF path-loss, low-threshold detector with
    easy/borderline victims + false-alarm decoys + a hidden RF-only victim.
  - `sim/swarm.py` — **THE AUCTION REFERENCE (port to C).** Unified bid function, SSI
    auction, log-odds re-observation fusion, Hive-Mind dismissal, cooperative RF
    gradient. `RELAY` + rescue-route are TODO stubs.
  - `sim/run_sim.py` — runs KHOJ vs a camera-only lawnmower baseline on identical
    worlds; prints the comparison. `python3 run_sim.py --seed 7`.
  - **Verified result across seeds:** KHOJ confirms 4–5/5 survivors *including the
    RF-only victim every run*; camera-only lawnmower gets 3–4/5 and *never* the RF
    victim; 3–4 false alarms auto-dismissed.
  - **Known gap (don't overclaim):** KHOJ is not yet *faster to first survivor* — the
    lawnmower beelines while KHOJ self-organises. Fix = seed the belief prior so KHOJ
    heads for likely areas first. Until then, pitch "more survivors + the invisible
    one + fewer false negatives," not "faster to first."
- **`docs/mesh_bringup.md`** ✅ — context-free Phase-1 spec to bring up the ESP-NOW
  mesh (broadcast + receive + loss measurement; no algos). Ready for hardware.

---

## 12. Swarm-tech background (settled understanding, for pitch + design)

- **Real 1000-drone shows (DJI, Xiaoduoji, most military) are CENTRALIZED:** a Ground
  Control Station with RTK-GPS (±2 cm) computes each drone's absolute trajectory and
  broadcasts positions; each drone just runs PID to a target. GCS compute is **O(N)**
  → needs a supercomputer; single point of failure; needs GPS; can't work offline.
- **KHOJ is DECENTRALIZED:** each drone computes only for itself (**O(1)**, all in
  parallel), so a cheap ESP32 suffices; no central point of failure; works offline;
  self-heals. Trade-off: doesn't scale to 1000 (radio would saturate) and is slower
  per decision (~500 ms round). For 6 drones in SAR this is the *better* architecture.
- **Position without GPS on the boards = dead reckoning** (integrate velocity from
  motor commands / IMU / optical flow); drifts ~±1–2 m per 30 s; correct periodically
  from the laptop (HIL) or peer RSSI. ±2 m is fine for SAR (32×32 grid, ~3 m cells);
  ±20 m is mission-failure. In HIL the laptop provides position via the sensor packet.
- **ESP-NOW budget at 6 drones is tiny** (~8 kbps vs ~1 Mbps capacity) — you can be
  verbose. ESP-NOW is not the bottleneck at this scale.
- **ESP32 vs ESP8266:** ESP32 (dual-core 240 MHz, 520 KB) does the auction in <10 ms;
  ESP8266 (80 MHz, 160 KB) works but ~50 ms/round — use it for the beacon or as a
  light extra agent. Both little-endian, both support ESP-NOW, same struct on the wire.

---

## 13. The 3 grafts (from the 3rd ideas doc)

1. **Hive-Mind = shared dismissal memory** — CORE, already built into the re-observation
   loop in `swarm.py`. Demo beat: agent dismisses hot machinery → another flies past
   and does NOT re-investigate.
2. **RELAY = a 4th auction task type** — FLEX. An agent bids to reposition as a comms
   relay when a peer's link to base degrades. Hits **SDG 9** ("temporary disaster
   infrastructure"). Coded as a TODO stub.
3. **Rescue-route generation** — FLEX polish. On CONFIRM, the laptop computes a ground
   path from an entry point to the survivor. Closing demo beat ("rescuers can reach
   them," not just "detected"). TODO stub.

**Rejected:** the survivor-facing webpage, physical payload delivery, the "ARES"
5-pillar combine, a trained Digital-Twin ML prior. All are scope traps or roadmap slides.

---

## 14. Rishit's decentralized-stack build plan (the active track)

Each phase runs on real hardware before the next; none needs Rakshit's code.

- **P1 — Mesh bringup** *(doc done: `docs/mesh_bringup.md`)*: broadcast + receive +
  loss measurement, HELLO packets. → Rishit executes on hardware.
- **P2 — Peer table + heartbeat + failure detection**: each board tracks who's alive
  (last-seen per sender); timeout → mark dead. Substrate for self-healing. *(next code)*
- **P3 — Real KHOJ message set**: replace HELLO with `quorum_msg_t`; dedup by
  (sender, seq); the 5 ESP-NOW message types.
- **P4 — On-device auction + belief map**: port `swarm.py` to C — 32×32 belief grid,
  unified bid, SSI auction + tiebreak, re-observation spawn, Hive-Mind dismissal.
- **P5 — RSSI**: the beacon-node firmware + on-device RSSI capture via the ESP-NOW
  receive callback (no promiscuous) + `RF_SAMPLE` sharing + cooperative gradient
  localization. Optional: a dedicated promiscuous sniffer for real-phone detection.
- **P6 — Integration** with Rakshit's stack through the USB serial contract.

**Testing scaffold:** a throwaway **position-feeder stub** (extend `sim/wire_test.py`)
streams sensor packets with fake positions to the boards and reads goals back — lets
Rishit test P4/P5 without waiting on Rakshit's sim. The two stacks still meet only at
the contract.

---

## 15. Demo (5 beats — rehearse until boring)

1. Press GO → swarm self-organises in ~2 s (LEDs blue searching / yellow bidding /
   purple re-observing / orange solo-no-comms / off dead).
2. Beat the lawnmower baseline on survivors found — freeze and point at the metric.
3. **Re-observation:** a 0.41 sighting → divert → second angle → 0.87 → CONFIRMED
   (and a decoy dismissed, then ignored by another agent = Hive-Mind).
4. **Yank a node's power** → others absorb its territory live (self-healing).
5. **The hero:** sniffer/beacon finds a phone with nobody visible → an agent (ideally
   the real F450) confirms "person here, not visible → trapped."
   Optional close: rescue-route drawn to the confirmed survivor.

**Beats 4 and 5 are what judges repeat.** If running long, cut the solo/re-sync beat,
then the real-drone beat — **never cut the node-yank.**

**Metrics (20+ Monte Carlo runs, mean ± std):** time-to-first-survivor · survivors
found · % area covered · **recall & false alarms, single-pass vs re-observation** ·
completion with 20% agent loss · completion with 50% comms loss. Almost no hackathon
team quantifies against a baseline — it's devastatingly effective.

**Honesty boundary (a judge WILL probe):** REAL = the auction on separate MCUs, the
radio link, failure detection + re-auction, comms-loss behaviour, belief maps, YOLO on
real aerial imagery. SIMULATED = terrain (a photo, not a place) + agent motion. This is
**hardware-in-the-loop**, standard practice (PX4 ships a HITL mode). The ESPs don't
move and that's fine — an ESP-class board is what bolts to a real airframe; its job is
to *decide*, not to move. Draw this line yourself = credibility; blur it = taken apart.

---

## 16. Timeline & scope discipline (LOCKED)

- **Freeze at hour 32** (features stop). **Backup video recorded to local disk by
  hour 36.** Rehearse ≥5 full runs h36–44. Buffer h44–48.
- **Sleep shifts: 2 down / 2 up, ~hours 16–20.** The pitch is worth more than the last
  feature.
- **Hardware de-risk EARLY (h1–5), hardware features LATE (h12+).** A dead board must
  be discovered at hour 3, not hour 20.
- **Dual backend** `AGENT_BACKEND = "python" | "esp32"` — the Python core (`swarm.py`)
  is the fallback AND the C-port reference. If boards arrive late or fail, Python is
  the primary path. Hardware is a plugin, never a dependency.
- **Scope tiers:** T1 = Python swarm + auction + re-observation + dashboard + baseline
  (a complete project alone, NEVER cut). T2 = ESP-NOW mesh (4+ boards). T3 = real drone
  + ESP-on-drone. T4 flex = PX4 SITL headless agents, SDG-16 hash-chained evidence log.
- **Never cut:** the auction, the RF/re-observation confirmation, the lawnmower baseline.

---

## 17. Open questions / pending decisions

- [ ] Judging rubric — does it weight SDGs? (Decides if the SDG-16 evidence chain is
      worth ~3 h, and whether to lead novelty-first or feasibility-first.)
- [ ] Board arrival date (if day 2, the Python backend becomes the primary path).
- [ ] Which laptop has the GPU, and does it also drive the demo?
- [ ] Outdoor flight slot — when, where, whose permission, weather backup?
- [ ] Name↔role map for the two unnamed team members.
- [ ] `quorum_* → khoj_*` code-identifier rename sweep (cheap, cosmetic, not done).

---

## 18. How to work with Rishit (user preferences — follow these)

- Explain architectural/software decisions in **simple language** (strong hardware,
  competent-not-expert Python). **After any build step, explain plainly what it did.**
- Be **blunt** about scope, risk, and what to cut. He'd rather cut now than at 3am.
- Build **module by module**; let him test each on real hardware before the next.
- **Don't use tools when it's just chatting/Q&A.** Use tools only when he asks for code
  or a file. Don't waste tokens narrating process; limit filler words.
- **GitHub repo: `Arvoxis/NightWing`** (shared with Rakshit). **Do NOT push until he
  explicitly says so.** Local repo folder is `quorum/`.
