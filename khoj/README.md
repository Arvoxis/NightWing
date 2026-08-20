# KHOJ

**A leaderless rescue swarm that finds people it cannot see.**

*("Khoj" — the search / the quest.)*

Search-and-rescue drones are blind to the victims who need them most — the ones
buried, trapped, or out of sight. KHOJ isn't. It doesn't look for victims; it
listens for the one signal a trapped person can't stop sending: their phone's
radio. Several agents share signal-strength samples over their own mesh and
**cooperatively climb the gradient to a phone none of them can see** — something
no single drone can do. Kill any agent and its work is re-auctioned in seconds,
with no leader and no central computer.

> **Pitch line:** *Other swarms search together. KHOJ finds the invisible.*

---

## Status — what actually runs on hardware

Five ESP32 boards, verified on the bench.

| Capability | Status | Measured |
|---|---|---|
| ESP-NOW mesh, 5 boards | ✅ working | **0.0% packet loss** (from sequence-number gaps) |
| Peer table + heartbeat | ✅ working | 5 Hz, 23-byte `quorum_msg_t` |
| Failure detection | ✅ working | **< 2 s**, reached independently by every board |
| Position over USB, shared on the mesh | ✅ working | every board tracks every peer's coordinates |
| Cooperative RF localization | ✅ working | **converges to ~0.3 grid cells** |
| USB message contract, both directions | ✅ byte-verified | 28 B down / 17 B up |
| Python swarm core vs lawnmower baseline | ✅ working | 4–5 of 5 survivors vs 3–4, RF victim every run |

**Not yet done:** on-device auction (still the Python reference), belief-map
priors, integration with the full laptop simulator, real-drone flight.

### What is real and what is simulated

Draw this line yourself before a judge draws it for you.

**Real** — the decision logic running on five separate microcontrollers · the
ESP-NOW radio link · failure detection and independent recovery · per-agent
signal-strength capture · the USB wire contract.

**Simulated** — drone motion (the boards decide, they do not fly) · terrain ·
and, in the current demo, the RSSI values themselves.

The firmware prints `simulated` or `measured` on every RF line so the two can
never be confused. This is **hardware-in-the-loop**, standard practice — PX4
ships a HITL mode. An ESP-class board is what bolts to a real airframe; its job
is to *decide*, not to move.

---

## The one idea that makes it click

Two worlds run at once:

- **The real world** lives on the laptop: where victims actually are, the
  terrain, the hidden phone. Agents never see this answer key.
- **Each agent's belief** lives inside its ESP32: its own private guess, built
  only from what it has personally sensed and what peers have broadcast.

The laptop is the *universe*; the ESP32s are *minds* moving through it. That
separation is the whole reason it's a real swarm and not a puppet show — and the
clean answer to "is the laptop secretly in charge?" is **no**.

**The novelty:** the swarm bids in **information gain**, not area. Searching
empty ground, taking a second look at an uncertain sighting, and triangulating
an invisible phone all become the *same decision* — "where will my next move
most reduce what the swarm doesn't yet know?"

---

## Repo layout

```
firmware/
  platformio.ini             build config — PINNED to arduino-esp32 core 3.x (see below)
  flash_all.ps1              flash every connected board, print a pass/fail table
  include/khoj_ids.h         MAC -> agent id table; one binary runs on all boards
  lib/quorum_proto/          THE TREATY: shared wire structs (mirrored in sim/protocol.py)
  src/mesh/main.cpp          the live firmware: mesh + peer table + failure detection + RF
  src/agent/main.cpp         Module 1 wire test (kept for reference)
sim/
  protocol.py                Python mirror of quorum_proto.h
  feeder.py                  position feeder — plays "the world" for the boards
  world.py  swarm.py  run_sim.py    the Python swarm core + lawnmower baseline
  wire_test.py               Module 1 laptop side
docs/
  mesh_bringup.md            ESP-NOW bringup spec
  ppt/                       pitch deck + diagram generators
HANDOFF.md                   full project reasoning and decisions — read this first
```

---

## Running it

### 1. Flash the boards

```bash
cd firmware
powershell -ExecutionPolicy Bypass -File .\flash_all.ps1
```

Finds every connected board, builds once, flashes each, and prints a pass/fail
table. **A silently failed flash looks exactly like a working board** until it
misbehaves — hence the table.

First time on a new set of boards: flash with `khoj_ids.h`'s table empty, let
each board print its `ROSTER` block over serial, paste those lines into the
table with unique ids, and reflash. Each board then derives its own id from its
MAC, so one binary serves every board.

### 2. Give the boards positions and run the RF demo

```bash
cd sim
py feeder.py                                   # auto-detect every board
py feeder.py --ports COM3 COM13 COM14 COM16    # feed the agents, not the beacon
py feeder.py --phone 8 28                      # move the hidden transmitter
```

The feeder streams each board its private sensor packet, reads back the goal
that board chose, and moves its simulated body. Watch `nearest drone` fall
toward zero.

### 3. Watch a board

```bash
cd firmware
py -m platformio device monitor --monitor-port COM16 -b 115200
```

```
STATS id=2 pos=(18.3,21.4) sensors=412 alive=4 peers:  [id=1 UP (12.4,19.8) loss=0.0% rssi=-31 age=190ms] ...
  RF  SOURCE EST (21.8,23.6) @ -34 dBm from 4 shared sample(s) | my rssi -41 dBm [simulated]
```

### 4. Python swarm vs baseline (no hardware needed)

```bash
cd sim
py run_sim.py --seed 7
```

---

## How the RF localization works

**Gradient-seeking, not trilateration.** It never needs to know absolute
distance — only whether the signal is stronger here or there. No path-loss
calibration, no geometry requirement, no matrix solve on the chip.

1. Each agent measures signal strength and broadcasts `RF_SAMPLE (x, y, rssi)`.
2. Every agent pools the shared samples and takes **the position of the
   strongest reading** as the swarm's current guess.
3. Each agent moves toward it, fanned sideways by its id so they sample fresh
   ground instead of retracing one another.
4. Any stronger reading moves the guess. Repeat.

**Why the guess is a measured point and not a computed centroid:** a
signal-weighted centroid of the *agents' own positions* makes the estimate
depend on where they chose to fly, which makes it depend on itself. Send
everyone to it and they collapse onto one spot, killing the spatial spread that
carries the information. Ring them around it and any asymmetry in the ring
offsets the estimate a fixed amount every cycle and it accelerates off the map.
Both were observed on hardware. A least-squares gradient fit avoids the feedback
but overshoots and oscillates near the source. Hill-climbing on the strongest
measured sample converges to ~1 cell in simulation from clustered, spread,
far-corner and opposite-corner starts — and to ~0.3 cells on real boards.

> `sim/swarm.py` still uses the signal-weighted centroid, which works there
> because agents keep exploring the whole map. The two need reconciling.

---

## The message contract

`firmware/lib/quorum_proto/quorum_proto.h`, mirrored byte-for-byte in
`sim/protocol.py`. Little-endian, packed structs.

- **Serial framing** (USB, both ways): `[0xAA][0x55][len][payload][xor]`
- **ESP-NOW** `quorum_msg_t` — 23 B. Types: `HEARTBEAT`, `BID`, `AWARD`,
  `TASK_SPAWN`, `RF_SAMPLE`
- **USB down** `usb_sensor_t` — 28 B, the private per-agent sensor packet
- **USB up** `usb_goal_t` — 17 B, the decision

**Never change one side without the other in the same commit.** Two divergent
protocol files is the single most likely silent demo-killer, and this contract
is the only seam between the firmware and laptop stacks.

`sim/feeder.py` is a working reference implementation of the laptop half — the
full simulator can drop in behind the same bytes with no firmware change.

---

## Toolchain notes (Windows)

These cost real time; they are written down so they cost it only once.

- **arduino-esp32 core 3.x is a hard requirement.** Official PlatformIO stops at
  core 2.0.17, whose ESP-NOW receive callback gives you `(mac, data, len)` and
  **no signal strength** — which makes the entire RF concept impossible. The
  build is pinned to the pioarduino fork. Every board prints `rx_rssi=YES` or
  `NO-core2.x` at boot; if you ever see the latter, the pin was reverted.
- **`PYTHONUTF8=1` is required** or PlatformIO dies mid-flash with
  `UnicodeEncodeError: 'charmap' codec`. `flash_all.ps1` sets it.
- Some boards need the **BOOT button held** during upload
  (`Wrong boot mode detected (0x13)`).
- **COM port numbers renumber on replug.** Never reuse a remembered port; let
  `feeder.py` auto-detect, or re-run `pio device list`.
- Charge-only USB cables power a flashed board fine but never enumerate as a
  COM port — useful for the beacon, useless for flashing.
- DTR/RTS drive the ESP32 reset circuit, and a CH9102 bridge behaves differently
  from a CP210x. `feeder.py` parks both lines low so it never resets a board
  just by opening its port.

---

## Next

1. **Integration** — replace `feeder.py` with the full laptop simulator. Same
   bytes, no firmware change.
2. **On-device auction** — port the bid function and belief grid from
   `sim/swarm.py` to C.
3. **Belief-map priors** — closes the known "not yet faster to first survivor"
   gap against the baseline.
4. **Reconcile** the firmware's RF rule with `sim/swarm.py`'s centroid.

See `HANDOFF.md` for the full reasoning, the auction design, and the demo plan.
