// ============================================================================
//  KHOJ  —  the agent firmware (one binary runs on every board)
//  ---------------------------------------------------------------------------
//  WHAT THIS IS
//  A complete leaderless agent. Every board broadcasts a heartbeat 5x a second,
//  tracks who is alive, takes its position in over USB, shares radio readings,
//  prices sightings, and independently computes who should investigate them.
//  Nothing here is coordinated by the laptop.
//
//    P1  ESP-NOW mesh                    broadcast, no router, no leader
//    P2  peer table + failure detection   2 s of silence and a peer is dead
//    P3  position over USB, shared        every board knows where every drone is
//    P5  cooperative RF localization      hill-climb to a transmitter nobody sees
//    P4  sightings, bids, winner rule     the auction, on-device
//
//  WHY IT SENDS quorum_msg_t (not the throwaway 16-byte mesh_msg_t in
//  docs/mesh_bringup.md): we already have a byte-verified treaty struct with a
//  MSG_HEARTBEAT type in it. Testing the REAL struct over the air now means
//  nothing gets rewritten at P3, and the actual wire format is de-risked on day
//  one instead of at hour 30. Same test, one less thing to throw away.
//
//  WHAT YOU GET ON SERIAL
//    BOOT  ... one line: who I am, my MAC, the channel, the core version
//    RX    ... one line per received packet (turn off with VERBOSE_RX 0)
//    STATS ... every 2 s: per-peer packet count, LOSS %, RSSI, last-seen age
//
//  The STATS line is the whole point. Loss % is computed from gaps in each
//  sender sequence number, so it is a real measurement, not a guess — walk a
//  board across the room and watch loss climb live (that is your range test).
// ============================================================================
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_mac.h>
#include <string.h>

#include <quorum_proto.h>
#include "khoj_ids.h"

// ---- knobs -----------------------------------------------------------------
#define MESH_CHANNEL      1     // EVERY board must match. ESP-NOW never hops.
#define TX_INTERVAL_MS    200   // 5 Hz heartbeat
#define STATS_INTERVAL_MS 2000
// Per-packet RX logging. Useful while proving the radio works; at 5 boards it
// is 20 lines/second and it buries the PEER LOST / PEER REJOINED events you
// actually want to watch. Off now that the mesh is verified — set to 1 if you
// ever need to see individual packets again.
#define VERBOSE_RX        0
// A board is declared DEAD after this long with no packet from it. At a 5 Hz
// heartbeat that is 4 missed in a row — long enough not to trip on normal
// packet loss, short enough that a judge yanking a power cable sees the swarm
// react while they are still holding the cable.
#define PEER_TIMEOUT_MS   2000

// ---- P5: cooperative RF localization ---------------------------------------
//  BEACON_ID would be a board playing a PHYSICAL victim phone (others measure
//  the RSSI of its ESP-NOW packets to localize it). In the laptop-in-the-loop
//  demo the phone is SIMULATED — the feeder sends each board a synthetic RSSI vs
//  a virtual phone — so no board should be a beacon. Set to 255 (no board has
//  that id) so all five boards are equal searchers: they search, detect, and do
//  RF. Restore a real id (e.g. 5) only for a hardware-only RF demo with a real
//  transmitter board.
#define BEACON_ID         255
#define RF_GATE_DBM       -85   // ignore samples weaker than this
//  A board only DIVERTS to chase the RF gradient when its OWN signal is this
//  strong (i.e. it is genuinely near the source). Weaker boards keep searching,
//  so the RF fix never drags the whole swarm off the map — it stays smooth like
//  the pure-search sim, and only the closest one or two converge on the phone.
#define RF_NEAR_DBM       -60
#define RF_SAMPLE_MS      500   // how often to share my reading
#define RF_STALE_MS       6000  // drop a peer's sample older than this
#define RF_MIN_SAMPLES    2     // one reading locates nothing; two start to
// HILL-CLIMB ON THE STRONGEST MEASURED SAMPLE — and why not a centroid.
//
// A signal-weighted centroid of the agents' OWN positions cannot work here, for
// two reasons hit in this order on real hardware:
//   1. Send everyone to it and they collapse onto one point. All samples then
//      come from the same place, the spatial spread that carries the direction
//      information vanishes, and the estimate freezes.
//   2. Ring them around it instead and it is still CIRCULAR — the goals derive
//      from the estimate and the estimate from the goals. Any asymmetry in the
//      ring (four agents cannot sit symmetrically on six bearings) offsets it a
//      fixed amount per cycle and the whole thing accelerates off the map.
// A least-squares gradient fit avoids the feedback but overshoots and
// oscillates near the source.
//
// So the guess is simply WHERE THE SIGNAL WAS LOUDEST — a measured point, which
// nothing can feed back into. Agents move toward it, sampling fresh ground on
// the way; any stronger reading moves it. Converges to ~0.3 cells on hardware.
// ---- P4 (Path A): sightings, bids, and the leaderless winner rule ----------
//  A detection whose confidence lands in the UNCERTAIN band is too strong to
//  ignore and too weak to trust. Instead of guessing, the board broadcasts it
//  as a task; every board prices it; every board applies the SAME rule to the
//  SAME bids and therefore reaches the SAME winner without anyone announcing
//  it. That last sentence is the entire novelty claim, and this block is where
//  it stops being a slide and starts being true.
#define MAX_TASKS         8
#define CONF_LO           15    // <= this: too weak, ignore
#define CONF_HI           80    // >= this: confident enough on one look
#define TASK_TTL_MS       15000 // forget a task nobody serviced
#define AUCTION_MS        500   // one round
#define BID_TTL_MS        1500  // a bid older than this is stale
#define TAU               60.0f // survival decay (s)
#define KHOJ_EPS          0.1f
#define T_OBS             0.5f  // time spent actually observing
#define AGENT_SPEED       2.0f  // cells per second, matches the sim body
#define C_FN              100.0f// a false negative costs this much more than a false positive
#define DISMISS_R2        4.0f  // squared radius for "same place"
#define MAX_DISMISSED     12

#define RF_STEP           3.0f  // cells to advance along the gradient
#define RF_SPREAD         2.5f  // lateral separation, keeps the fit conditioned
#define GRID_N            32.0f // clamp goals to the world
#define LED_PIN           2
#define KHOJ_MAX_PEERS    12

// arduino-esp32 core 3.x hands us RSSI in the receive callback; core 2.x does
// not (its callback only gets the MAC). The RF hero demo NEEDS that RSSI, so
// the boot line prints which core you are on — check it.
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  #define KHOJ_HAVE_RX_RSSI 1
#else
  #define KHOJ_HAVE_RX_RSSI 0
#endif

static uint8_t  BROADCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
static uint8_t  myId = 0;
static uint8_t  myMac[6];
static uint16_t txSeq = 0;
static uint32_t lastTx = 0, lastStats = 0, lastBlink = 0;
static uint32_t txOk = 0, txFail = 0;
static bool     ledOn = false;

// ---- peer table ------------------------------------------------------------
//  One row per sender we have ever heard. This is the seed of P2 failure
//  detection: once we track "last time I heard from you", declaring a board
//  dead is just a timeout on that field.
struct Peer {
  bool     used;
  uint8_t  id;
  uint32_t rxCount;    // packets actually received
  uint32_t expected;   // packets they claim to have sent (from seq deltas)
  uint32_t dups;       // duplicates / out-of-order
  uint16_t lastSeq;
  uint32_t lastTxMs;   // their millis() — goes BACKWARDS iff they rebooted
  int8_t   lastRssi;
  uint32_t lastMs;     // our millis() when last heard  <- P2 hangs off this
  uint8_t  mac[6];     // so ONE board can print the whole roster (see ROSTER)
  bool     alive;      // P2: is this board currently reachable?
  float    x, y;       // P3: where that peer says it is, in grid cells
};
static Peer peers[KHOJ_MAX_PEERS];

static Peer *getPeer(uint8_t id) {
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++)
    if (peers[i].used && peers[i].id == id) return &peers[i];
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++) {
    if (!peers[i].used) {
      peers[i].used = true;
      peers[i].id = id;
      peers[i].rxCount = peers[i].expected = peers[i].dups = 0;
      peers[i].lastSeq = 0;
      peers[i].lastTxMs = 0;
      peers[i].lastRssi = 0;
      peers[i].lastMs = 0;
      peers[i].alive = false;
      return &peers[i];
    }
  }
  return nullptr;   // table full
}

// ---- P3: this board's own body ---------------------------------------------
//  The laptop (or, on the real drone, the RPi) owns where this agent actually
//  is. It arrives over USB as a usb_sensor_t; we answer with a usb_goal_t. The
//  board never invents a position — it only ever decides what to do with one.
static float myX = 0.0f, myY = 0.0f;
static bool  havePos = false;
static uint32_t lastTick = 0, sensorCount = 0;

// ---- P5 state ---------------------------------------------------------------
//  TWO RSSI SOURCES, one gradient. Which one is live depends on the demo:
//    simRssi  — the laptop computed it from the simulated drone position vs the
//               hidden phone, and sent it in usb_sensor_t.rssi. Coherent with
//               simulated positions, so this is the hardware-in-the-loop path.
//    realRssi — physically measured off the beacon board's packets. Real radio,
//               but only meaningful if the drone positions are real too (the
//               hand-carried "judge hides the beacon" moment).
//  The maths below does not care which it was given. That is the point.
static int8_t   simRssi = -128;        // -128 = laptop supplied nothing
static int8_t   realRssi = 0;
static bool     haveRealRssi = false;
static uint32_t lastRfTx = 0;

// ---- P4 state ---------------------------------------------------------------
struct Task {
  bool     used;
  uint16_t tid;
  float    x, y;
  float    logodds;              // fused belief this is a real survivor
  float    bearings[4];          // viewing angles already used, degrees
  uint8_t  nbear;
  uint32_t ms;                   // when we last heard about it
  float    bid[KHOJ_MAX_PEERS];  // what each agent said it was worth to them
  uint32_t bidMs[KHOJ_MAX_PEERS];
};
static Task tasks[MAX_TASKS];
static uint16_t nextTid = 1;
static uint32_t lastAuction = 0;
static uint16_t myTask = 0;      // the task I am currently servicing, 0 = none
static float myTaskX = 0, myTaskY = 0;

// Spots the swarm has finished with — BOTH dismissed decoys and confirmed
// survivors. Confirmations must be remembered too: a found survivor is still
// visible to the next drone that flies over, and without this the swarm
// re-announces the same person forever and drowns in duplicate tasks.
struct Resolved { bool used; float x, y; bool confirmed; };
static Resolved resolved[MAX_DISMISSED];

struct RfSample {
  bool     used;
  float    x, y;
  int8_t   rssi;
  uint32_t ms;
};
static RfSample rf[KHOJ_MAX_PEERS];    // one slot per agent id, newest wins

static bool  rfLocked = false;
static float rfEstX = 0, rfEstY = 0;
static uint8_t rfCount = 0;

static void rfStore(uint8_t agent, float x, float y, int8_t rssi, uint32_t now) {
  if (agent == 0 || agent >= KHOJ_MAX_PEERS) return;
  rf[agent].used = true;
  rf[agent].x = x; rf[agent].y = y;
  rf[agent].rssi = rssi; rf[agent].ms = now;
}

// ---- P4 maths: ported straight from sim/swarm.py ----------------------------
static inline float clamp01(float p) { return p < 1e-6f ? 1e-6f : (p > 0.999999f ? 0.999999f : p); }
static inline float logitf(float p)  { p = clamp01(p); return logf(p / (1.0f - p)); }
static inline float sigmoidf(float x){ if (x > 40) x = 40; if (x < -40) x = -40;
                                       return 1.0f / (1.0f + expf(-x)); }
static float binEntropy(float p) {     // uncertainty: 1 bit at p=0.5, 0 at the ends
  p = clamp01(p);
  return -(p * log2f(p) + (1.0f - p) * log2f(1.0f - p));
}
static float angDiff(float a, float b) {
  float d = fmodf(a - b + 180.0f, 360.0f);
  if (d < 0) d += 360.0f;
  return fabsf(d - 180.0f);
}

static Task *findTaskNear(float x, float y) {
  for (uint8_t i = 0; i < MAX_TASKS; i++)
    if (tasks[i].used) {
      float dx = tasks[i].x - x, dy = tasks[i].y - y;
      if (dx * dx + dy * dy < DISMISS_R2) return &tasks[i];
    }
  return nullptr;
}
static Task *findTaskById(uint16_t tid) {
  for (uint8_t i = 0; i < MAX_TASKS; i++)
    if (tasks[i].used && tasks[i].tid == tid) return &tasks[i];
  return nullptr;
}
static Task *newTask(uint32_t now) {
  for (uint8_t i = 0; i < MAX_TASKS; i++)
    if (!tasks[i].used) { memset(&tasks[i], 0, sizeof(Task)); tasks[i].used = true;
                          tasks[i].ms = now; return &tasks[i]; }
  return nullptr;
}
static bool isResolved(float x, float y) {
  for (uint8_t i = 0; i < MAX_DISMISSED; i++)
    if (resolved[i].used) {
      float dx = resolved[i].x - x, dy = resolved[i].y - y;
      if (dx * dx + dy * dy < DISMISS_R2) return true;
    }
  return false;
}
static void addResolved(float x, float y, bool confirmed) {
  if (isResolved(x, y)) return;
  for (uint8_t i = 0; i < MAX_DISMISSED; i++)
    if (!resolved[i].used) { resolved[i].used = true; resolved[i].x = x;
                             resolved[i].y = y; resolved[i].confirmed = confirmed;
                             return; }
}

// THE BID — expected survivors per second. Identical to swarm.py's bid().
//   U      how much uncertainty this second look would remove
//   c      how long it takes ME to get there — cost is TIME, not distance
//   decay  evaluated at ARRIVAL, so a task I would reach late is worth less
static float bidFor(const Task *t, float ax, float ay, float tnow) {
  float dx = t->x - ax, dy = t->y - ay;
  float c = sqrtf(dx * dx + dy * dy) / AGENT_SPEED + T_OBS;
  float p = sigmoidf(t->logodds);
  // viewpoint diversity: a second look from the SAME bearing tells us nothing
  float bear = atan2f(ay - t->y, ax - t->x) * 57.29578f;
  float dth = 180.0f;
  for (uint8_t i = 0; i < t->nbear; i++) {
    float d = angDiff(bear, t->bearings[i]);
    if (d < dth) dth = d;
  }
  float div = 1.0f - expf(-dth / 60.0f);
  float U = binEntropy(p) * C_FN * div;
  return U * expf(-(tnow + c) / TAU) / (c + KHOJ_EPS);
}

static int8_t rfBestRssi = -128;       // strongest reading anyone has taken

// The swarm's current guess is simply WHERE THE SIGNAL WAS LOUDEST — the
// position of the strongest sample anyone has shared. That is a measured point,
// not a quantity derived from where we chose to fly, which is what keeps this
// stable. Agents then move toward it, sample from new ground on the way, and if
// any of those readings beats it the guess moves. Hill-climbing on the real
// field, exactly "is it stronger here or there".
//
// Verified in simulation before it ever reached hardware: converges to ~1 cell
// from clustered, spread, far-corner and opposite-corner starts, and STAYS
// there. A least-squares gradient fit was tried first and oscillated — it
// overshot near the source and bounced.
static void rfEstimate(uint32_t now) {
  uint8_t n = 0;
  rfBestRssi = -128;
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++) {
    if (!rf[i].used) continue;
    if (now - rf[i].ms > RF_STALE_MS) { rf[i].used = false; continue; }
    if (rf[i].rssi < RF_GATE_DBM) continue;
    if (rf[i].rssi > rfBestRssi) {
      rfBestRssi = rf[i].rssi;
      rfEstX = rf[i].x;
      rfEstY = rf[i].y;
    }
    n++;
  }
  rfCount = n;
  rfLocked = (n >= RF_MIN_SAMPLES);
}

// ---- USB framing (same treaty as Module 1: AA 55 len payload xor) ----------
static void writeFrame(const uint8_t *payload, uint8_t len) {
  uint8_t xorc = 0;
  for (uint8_t i = 0; i < len; i++) xorc ^= payload[i];
  Serial.write(QP_SYNC0);
  Serial.write(QP_SYNC1);
  Serial.write(len);
  Serial.write(payload, len);
  Serial.write(xorc);
}

enum RxState { WAIT0, WAIT1, GETLEN, GETPAYLOAD, GETCKSUM };
static RxState uState = WAIT0;
static uint8_t uLen = 0, uIdx = 0, uXor = 0, uBuf[QP_MAX_PAYLOAD];

static bool feedUsb(uint8_t b) {
  switch (uState) {
    case WAIT0: if (b == QP_SYNC0) uState = WAIT1; break;
    case WAIT1: uState = (b == QP_SYNC1) ? GETLEN : WAIT0; break;
    case GETLEN:
      uLen = b; uIdx = 0; uXor = 0;
      uState = (uLen == 0 || uLen > QP_MAX_PAYLOAD) ? WAIT0 : GETPAYLOAD;
      break;
    case GETPAYLOAD:
      uBuf[uIdx++] = b; uXor ^= b;
      if (uIdx >= uLen) uState = GETCKSUM;
      break;
    case GETCKSUM:
      uState = WAIT0;
      return (b == uXor);
  }
  return false;
}

// ---- ISR-safe ring buffer --------------------------------------------------
//  NEVER Serial.print from the receive callback — that is the #1 cause of
//  random reboots on ESP-NOW projects. Stash bytes here, print from loop().
struct RxItem { quorum_msg_t m; int8_t rssi; uint8_t mac[6]; };
#define RX_RING 32
static volatile uint8_t rxHead = 0, rxTail = 0;
static RxItem rxRing[RX_RING];

static inline void pushRx(const quorum_msg_t *m, int8_t rssi, const uint8_t *mac) {
  uint8_t next = (uint8_t)((rxHead + 1) % RX_RING);
  if (next == rxTail) return;                 // full: drop, never block
  memcpy((void *)&rxRing[rxHead].m, m, sizeof(quorum_msg_t));
  rxRing[rxHead].rssi = rssi;
  if (mac) memcpy((void *)rxRing[rxHead].mac, mac, 6);
  else     memset((void *)rxRing[rxHead].mac, 0, 6);
  rxHead = next;
}

#if KHOJ_HAVE_RX_RSSI
static void onRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  if (len != (int)sizeof(quorum_msg_t)) return;
  int8_t rssi = (info && info->rx_ctrl) ? (int8_t)info->rx_ctrl->rssi : 0;
  pushRx((const quorum_msg_t *)data, rssi, info ? info->src_addr : nullptr);
}
#else
static void onRecv(const uint8_t *mac, const uint8_t *data, int len) {
  if (len != (int)sizeof(quorum_msg_t)) return;
  pushRx((const quorum_msg_t *)data, 0, mac); // no RSSI available on core 2.x
}
#endif

// ---- P2: failure detection --------------------------------------------------
//  Every board runs this independently on its OWN copy of the peer table. There
//  is no referee deciding who is dead — each board reaches the same conclusion
//  from the same evidence (silence). That is what makes the recovery emergent
//  rather than scripted, and it is the honest answer when a judge asks whether
//  the laptop is secretly in charge.
//
//  Later (P4) the LOST event is what returns a dead board's tasks to the pool
//  so the next auction round reassigns them. Right now it just reports.
static uint8_t aliveCount() {
  uint8_t n = 0;
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++)
    if (peers[i].used && peers[i].alive) n++;
  return n;
}

static void checkLiveness(uint32_t now) {
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++) {
    if (!peers[i].used || !peers[i].alive) continue;
    if (now - peers[i].lastMs > PEER_TIMEOUT_MS) {
      peers[i].alive = false;
      Serial.printf("PEER LOST id=%u  silent for %lums  (alive peers now %u)\n",
                    peers[i].id, (unsigned long)(now - peers[i].lastMs), aliveCount());
    }
  }
}

// ---- stats -----------------------------------------------------------------
static void printStats() {
  uint32_t now = millis();
  if (havePos)
    Serial.printf("STATS id=%u pos=(%.1f,%.1f) sensors=%lu tick=%lu alive=%u peers:",
                  myId, myX, myY, (unsigned long)sensorCount,
                  (unsigned long)lastTick, aliveCount());
  else
    Serial.printf("STATS id=%u pos=NONE tx=%lu tx_fail=%lu alive=%u peers:",
                  myId, (unsigned long)txOk, (unsigned long)txFail, aliveCount());
  bool any = false;
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++) {
    if (!peers[i].used) continue;
    any = true;
    Peer &p = peers[i];
    float loss = p.expected ? 100.0f * (float)(p.expected - p.rxCount) / (float)p.expected
                            : 0.0f;
    if (loss < 0.0f) loss = 0.0f;
    Serial.printf("  [id=%u %s (%.1f,%.1f) loss=%.1f%% rssi=%d age=%lums]",
                  p.id, p.alive ? "UP" : "DEAD", p.x, p.y, loss,
                  p.lastRssi, (unsigned long)(now - p.lastMs));
  }
  if (!any) Serial.print("  (none heard yet)");
  Serial.println();

  if (myId == BEACON_ID) {
    Serial.println("  RF  I am the BEACON — the thing being searched for.");
  } else if (rfLocked) {
    Serial.printf("  RF  SOURCE EST (%.1f,%.1f) @ %d dBm from %u shared "
                  "sample(s) | my rssi %d dBm [%s]\n",
                  rfEstX, rfEstY, rfBestRssi, rfCount,
                  (simRssi != -128) ? simRssi : realRssi,
                  (simRssi != -128) ? "simulated" : "measured");
  } else {
    Serial.printf("  RF  no fix yet (%u/%u samples)\n", rfCount, RF_MIN_SAMPLES);
  }
}

// ---- P4: broadcasting ------------------------------------------------------
static void meshSend(uint8_t type, uint16_t tid, float value, float x, float y) {
  quorum_msg_t m = {};
  m.msg_type  = type;
  m.agent_id  = myId;
  m.task_id   = tid;
  m.value     = value;
  m.x         = x;
  m.y         = y;
  m.timestamp = millis();
  m.seq       = txSeq++;
  esp_now_send(BROADCAST, (uint8_t *)&m, sizeof(m));
}

// MSG_AWARD carries the RESOLUTION of a task: value 1 = confirmed survivor,
// value 0 = dismissed. A dismissal is the Hive-Mind broadcast — every board
// records the spot and no agent ever investigates it again.
static void resolveTask(Task *t, bool confirmed) {
  meshSend(MSG_AWARD, t->tid, confirmed ? 1.0f : 0.0f, t->x, t->y);
  Serial.printf("EVENT %s at (%.1f,%.1f)  task=%u\n",
                confirmed ? "CONFIRMED survivor" : "DISMISSED (broadcast to swarm)",
                t->x, t->y, t->tid);
  addResolved(t->x, t->y, confirmed);
  if (myTask == t->tid) myTask = 0;
  t->used = false;
}

// ---- P4: a sighting arrives from my own camera ------------------------------
static void handleDetection(float dx, float dy, uint8_t conf, uint32_t now) {
  if (isResolved(dx, dy)) return;   // swarm already settled this spot
  // the bearing FROM the thing TO me — the angle I viewed it from
  float bear = atan2f(myY - dy, myX - dx) * 57.29578f;
  Task *t = findTaskNear(dx, dy);

  if (conf >= CONF_HI) {                    // confident on a single look
    if (t) resolveTask(t, true);
    else { Serial.printf("EVENT CONFIRMED survivor at (%.1f,%.1f) (single look %u%%)\n",
                         dx, dy, conf); }
    return;
  }
  if (conf <= CONF_LO) return;              // too weak to spend a drone on

  if (!t) {                                 // first uncertain sighting here
    t = newTask(now);
    if (!t) return;
    t->tid = (uint16_t)((myId << 12) | (nextTid++ & 0x0FFF));
    t->x = dx; t->y = dy;
    t->logodds = logitf(conf / 100.0f);
    t->bearings[0] = bear; t->nbear = 1;
    Serial.printf("EVENT SIGHTING %u%% at (%.1f,%.1f) -> REOBSERVE task %u announced\n",
                  conf, dx, dy, t->tid);
    meshSend(MSG_TASK_SPAWN, t->tid, t->logodds, dx, dy);
    return;
  }

  // Only fuse genuinely NEW viewpoints. Two looks from the same angle are not
  // independent evidence, and treating them as if they were would let the swarm
  // talk itself into a confident wrong answer.
  for (uint8_t i = 0; i < t->nbear; i++)
    if (angDiff(bear, t->bearings[i]) <= 20.0f) return;

  t->logodds += logitf(conf / 100.0f);
  if (t->nbear < 4) t->bearings[t->nbear++] = bear;
  t->ms = now;
  float fused = sigmoidf(t->logodds);
  Serial.printf("EVENT SECOND LOOK %u%% from a new angle -> fused %.2f\n", conf, fused);
  if (fused >= 0.80f)      resolveTask(t, true);
  else if (fused <= 0.15f) resolveTask(t, false);
}

// ---- P4: one auction round --------------------------------------------------
//  Runs on EVERY board, every AUCTION_MS, on that board's own copy of the task
//  and bid tables. No board is asked and no board announces — they simply all
//  apply the same rule to the same numbers.
static void runAuction(uint32_t now) {
  float tnow = now / 1000.0f;

  for (uint8_t i = 0; i < MAX_TASKS; i++)          // forget stale tasks
    if (tasks[i].used && now - tasks[i].ms > TASK_TTL_MS) {
      if (myTask == tasks[i].tid) myTask = 0;
      tasks[i].used = false;
    }

  for (uint8_t i = 0; i < MAX_TASKS; i++) {        // price every open task
    if (!tasks[i].used) continue;
    float b = bidFor(&tasks[i], myX, myY, tnow);
    tasks[i].bid[myId % KHOJ_MAX_PEERS] = b;
    tasks[i].bidMs[myId % KHOJ_MAX_PEERS] = now;
    meshSend(MSG_BID, tasks[i].tid, b, myX, myY);
  }

  // Sequential greedy: the most valuable task claims its best free agent first.
  bool taken[KHOJ_MAX_PEERS] = {false};
  bool handled[MAX_TASKS] = {false};
  uint16_t won = 0;
  myTaskX = myX; myTaskY = myY;

  for (uint8_t pass = 0; pass < MAX_TASKS; pass++) {
    int best = -1;
    float bestVal = 0;
    for (uint8_t i = 0; i < MAX_TASKS; i++) {
      if (!tasks[i].used || handled[i]) continue;
      float mx = 0;
      for (uint8_t a = 0; a < KHOJ_MAX_PEERS; a++)
        if (now - tasks[i].bidMs[a] < BID_TTL_MS && tasks[i].bid[a] > mx) mx = tasks[i].bid[a];
      if (mx > bestVal) { bestVal = mx; best = i; }
    }
    if (best < 0) break;
    handled[best] = true;
    Task *t = &tasks[best];

    // THE RULE: highest bid wins; ties go to the lowest agent id. Every board
    // runs exactly this, on the same bids, and so reaches the same winner.
    int winner = -1;
    float wb = 0;
    for (uint8_t a = 0; a < KHOJ_MAX_PEERS; a++) {
      if (taken[a]) continue;
      if (now - t->bidMs[a] >= BID_TTL_MS) continue;
      if (t->bid[a] > wb + 1e-9f) { wb = t->bid[a]; winner = a; }
    }
    if (winner < 0) continue;
    taken[winner] = true;
    if ((uint8_t)winner == (myId % KHOJ_MAX_PEERS)) {
      won = t->tid;
      myTaskX = t->x;
      myTaskY = t->y;
    }
  }
  if (won != myTask && won)
    Serial.printf("EVENT I WON task %u -> flying to (%.1f,%.1f)\n", won, myTaskX, myTaskY);
  myTask = won;
}

// ---- roster -----------------------------------------------------------------
//  Prints a paste-ready khoj_ids.h block for THIS board plus every board it can
//  hear. With all 5 powered you read this off ONE monitor instead of plugging
//  each board in to fish out its MAC.
//  It silences itself automatically once every board has a real id (< 200), so
//  it stops being noise the moment you have done the job.
#define ROSTER_INTERVAL_MS 10000
static uint32_t lastRoster = 0;
static bool isProvisional = false;

static void printRoster() {
  bool anyProvisional = isProvisional;
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++)
    if (peers[i].used && peers[i].id >= 200) anyProvisional = true;
  if (!anyProvisional) return;                // everyone has a real id — done

  uint8_t n = 1;
  Serial.println("ROSTER  paste between the markers in firmware/include/khoj_ids.h:");
  Serial.printf("  { {0x%02X,0x%02X,0x%02X,0x%02X,0x%02X,0x%02X}, %u },   // this board\n",
                myMac[0], myMac[1], myMac[2], myMac[3], myMac[4], myMac[5], n++);
  for (uint8_t i = 0; i < KHOJ_MAX_PEERS; i++) {
    if (!peers[i].used) continue;
    Peer &p = peers[i];
    Serial.printf("  { {0x%02X,0x%02X,0x%02X,0x%02X,0x%02X,0x%02X}, %u },   // heard as %u, rssi=%d\n",
                  p.mac[0], p.mac[1], p.mac[2], p.mac[3], p.mac[4], p.mac[5],
                  n++, p.id, p.lastRssi);
  }
  Serial.printf("ROSTER  end (%u board(s)). Reflash all of them after pasting.\n", n - 1);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(LED_PIN, OUTPUT);
  memset(peers, 0, sizeof(peers));

  // STATION mode, NOT connected to any access point. Do not call WiFi.begin().
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(false, false);
  delay(100);                       // let the driver actually come up

  // Power save parks the radio between beacons and silently eats incoming
  // broadcasts. ESP-NOW needs the receiver awake all the time.
  esp_wifi_set_ps(WIFI_PS_NONE);

  esp_err_t chErr = esp_wifi_set_channel(MESH_CHANNEL, WIFI_SECOND_CHAN_NONE);

  // Read the MAC from eFuse, NOT via WiFi.macAddress(): that returns all zeros
  // if the WiFi driver has not finished starting, and an all-zero MAC makes
  // every board derive the SAME provisional id — which looks exactly like a
  // dead radio. Learned the hard way.
  esp_read_mac(myMac, ESP_MAC_WIFI_STA);
  myId = khoj_id_from_mac(myMac);
  bool provisional = (myId == 0);
  isProvisional = provisional;
  if (provisional) myId = khoj_provisional_id(myMac);

  if (esp_now_init() != ESP_OK) {
    Serial.println("FATAL esp_now_init failed");
    while (1) delay(1000);
  }
  esp_now_register_recv_cb(onRecv);

  esp_now_peer_info_t peer = {};              // MUST be zero-initialised
  memcpy(peer.peer_addr, BROADCAST, 6);
  peer.channel = MESH_CHANNEL;
  peer.encrypt = false;
  if (esp_now_add_peer(&peer) != ESP_OK) {
    Serial.println("FATAL esp_now_add_peer(broadcast) failed");
    while (1) delay(1000);
  }

  Serial.printf("\nBOOT id=%u mac=%02X:%02X:%02X:%02X:%02X:%02X ch=%u core=%d "
                "rx_rssi=%s msg_bytes=%u\n",
                myId, myMac[0], myMac[1], myMac[2], myMac[3], myMac[4], myMac[5],
                MESH_CHANNEL,
#if defined(ESP_ARDUINO_VERSION_MAJOR)
                (int)ESP_ARDUINO_VERSION_MAJOR,
#else
                2,
#endif
                KHOJ_HAVE_RX_RSSI ? "YES" : "NO-core2.x",
                (unsigned)sizeof(quorum_msg_t));

  // Prove the channel actually took, rather than trusting the call.
  uint8_t chNow = 0;
  wifi_second_chan_t sec;
  esp_wifi_get_channel(&chNow, &sec);
  Serial.printf("RADIO set_channel=%s actual_ch=%u ps=off\n",
                chErr == ESP_OK ? "OK" : esp_err_to_name(chErr), chNow);
  if (chNow != MESH_CHANNEL)
    Serial.printf("WARN  channel is %u, expected %u — boards on different channels "
                  "NEVER hear each other.\n", chNow, MESH_CHANNEL);

  if (khoj_mac_is_bogus(myMac)) {
    Serial.println("FATAL MAC read back as all zeros — WiFi driver did not start. "
                   "Every board would take the same id. Power-cycle; tell Rishit if it persists.");
  }

  if (provisional) {
    Serial.println("WARN  this MAC is not in KHOJ_ID_TABLE — using a provisional id.");
    Serial.printf("PASTE ME -> { {0x%02X,0x%02X,0x%02X,0x%02X,0x%02X,0x%02X}, 1 },\n",
                  myMac[0], myMac[1], myMac[2], myMac[3], myMac[4], myMac[5]);
    Serial.println("WARN  put that in firmware/include/khoj_ids.h with a UNIQUE id, then reflash.");
  }
#if !KHOJ_HAVE_RX_RSSI
  Serial.println("WARN  arduino-esp32 core 2.x: no RSSI in the ESP-NOW callback. "
                 "The RF hero demo needs core 3.x.");
#endif
}

void loop() {
  uint32_t now = millis();

  // LED tells you the board's social state from across the room, no laptop:
  //   slow blink  = I can hear at least one peer
  //   fast blink  = I am ALONE (this is the "solo / no comms" state)
  checkLiveness(now);
  uint32_t blinkMs = aliveCount() > 0 ? 500 : 100;
  if (now - lastBlink >= blinkMs) {
    lastBlink = now;
    ledOn = !ledOn;
    digitalWrite(LED_PIN, ledOn);
  }

  // ---- transmit my heartbeat ----------------------------------------------
  if (now - lastTx >= TX_INTERVAL_MS) {
    lastTx = now;
    quorum_msg_t m = {};
    m.msg_type  = MSG_HEARTBEAT;
    m.agent_id  = myId;
    m.task_id   = 0;
    m.value     = 0.0f;
    m.x         = myX;       // share where my body is, so peers can bid on distance
    m.y         = myY;
    m.rssi      = 0;
    m.timestamp = now;
    m.seq       = txSeq++;
    esp_err_t e = esp_now_send(BROADCAST, (uint8_t *)&m, sizeof(m));
    if (e == ESP_OK) txOk++; else txFail++;
  }

  // ---- P5: share my reading, then re-estimate ------------------------------
  //  Only the agents do this. The beacon stays silent about RF — it is the
  //  thing being looked for, not one of the searchers.
  if (myId != BEACON_ID && now - lastRfTx >= RF_SAMPLE_MS) {
    lastRfTx = now;
    int8_t r = (simRssi != -128) ? simRssi : (haveRealRssi ? realRssi : 0);
    if (havePos && r != 0 && r >= RF_GATE_DBM) {
      rfStore(myId, myX, myY, r, now);       // my own sample counts too
      quorum_msg_t m = {};
      m.msg_type  = MSG_RF_SAMPLE;
      m.agent_id  = myId;
      m.x         = myX;
      m.y         = myY;
      m.rssi      = r;
      m.timestamp = now;
      m.seq       = txSeq++;
      esp_now_send(BROADCAST, (uint8_t *)&m, sizeof(m));
    }
    rfEstimate(now);
  }

  // ---- P4: run an auction round -------------------------------------------
  if (myId != BEACON_ID && now - lastAuction >= AUCTION_MS) {
    lastAuction = now;
    runAuction(now);
  }

  // ---- drain what the radio heard -----------------------------------------
  while (rxTail != rxHead) {
    RxItem it = rxRing[rxTail];
    rxTail = (uint8_t)((rxTail + 1) % RX_RING);

    // NOTE: no "ignore my own id" filter here on purpose. ESP-NOW broadcast is
    // never delivered back to its sender, so such a filter can only ever throw
    // away a REAL peer that happens to share your id — turning an id collision
    // into apparent radio silence. Shout about the collision instead.
    if (it.m.agent_id == myId) {
      static uint32_t lastDupWarn = 0;
      if (now - lastDupWarn > 3000) {
        lastDupWarn = now;
        Serial.printf("WARN  heard a packet claiming MY id (%u) — two boards share an id. "
                      "Fix khoj_ids.h and reflash.\n", myId);
      }
    }
    Peer *p = getPeer(it.m.agent_id);
    if (!p) continue;

    // A board you power-cycle restarts its seq at 0. Without this check its
    // seq delta would look like a 65000-packet jump and the loss maths would
    // stall for minutes — during a hackathon you reset boards constantly, so
    // detect it: their millis() can only go backwards if they rebooted.
    if (p->rxCount > 0 && it.m.timestamp < p->lastTxMs) {
      p->rxCount = p->expected = p->dups = 0;
    }
    p->lastTxMs = it.m.timestamp;

    if (p->rxCount == 0) {                    // first time we hear this board
      p->rxCount  = 1;
      p->expected = 1;
      p->lastSeq  = it.m.seq;
    } else {
      uint16_t d = (uint16_t)(it.m.seq - p->lastSeq);   // wrap-safe delta
      if (d == 0 || d > 1000) {               // duplicate, or stale/out-of-order
        p->dups++;
      } else {
        p->expected += d;                     // d-1 packets went missing
        p->rxCount  += 1;
        p->lastSeq   = it.m.seq;
      }
    }
    if (!p->alive) {                          // first contact, or it came back
      p->alive = true;
      Serial.printf("PEER %s id=%u  (alive peers now %u)\n",
                    p->rxCount <= 1 ? "FOUND" : "REJOINED", p->id, aliveCount());
    }
    p->lastRssi = it.rssi;
    p->lastMs   = now;
    p->x        = it.m.x;
    p->y        = it.m.y;
    memcpy(p->mac, it.mac, 6);

    // P5: the beacon's own packets ARE the measurement — no promiscuous mode,
    // no second radio. Its signal strength arrives free with every heartbeat.
    if (it.m.agent_id == BEACON_ID) {
      realRssi = it.rssi;
      haveRealRssi = true;
    }
    // A peer telling the swarm what IT heard, and from where.
    if (it.m.msg_type == MSG_RF_SAMPLE)
      rfStore(it.m.agent_id, it.m.x, it.m.y, it.m.rssi, now);

    // ---- P4: the swarm arguing about a sighting ---------------------------
    if (it.m.msg_type == MSG_TASK_SPAWN) {
      if (!isResolved(it.m.x, it.m.y) && !findTaskById(it.m.task_id)) {
        Task *t = findTaskNear(it.m.x, it.m.y);
        if (!t) t = newTask(now);
        if (t) {
          t->tid = it.m.task_id;
          t->x = it.m.x; t->y = it.m.y;
          t->logodds = it.m.value;
          t->nbear = 0;
          t->ms = now;
          Serial.printf("EVENT peer %u announced task %u at (%.1f,%.1f)\n",
                        it.m.agent_id, it.m.task_id, it.m.x, it.m.y);
        }
      }
    } else if (it.m.msg_type == MSG_BID) {
      Task *t = findTaskById(it.m.task_id);
      if (t) {
        uint8_t a = it.m.agent_id % KHOJ_MAX_PEERS;
        t->bid[a] = it.m.value;
        t->bidMs[a] = now;
        t->ms = now;
      }
    } else if (it.m.msg_type == MSG_AWARD) {
      // a task was resolved somewhere in the swarm
      Task *t = findTaskById(it.m.task_id);
      if (it.m.value < 0.5f) {
        addResolved(it.m.x, it.m.y, false);  // Hive-Mind: never look here again
        Serial.printf("EVENT peer %u DISMISSED (%.1f,%.1f) — noted, will not re-check\n",
                      it.m.agent_id, it.m.x, it.m.y);
      } else {
        addResolved(it.m.x, it.m.y, true);   // found — stop re-announcing them
        Serial.printf("EVENT peer %u CONFIRMED a survivor at (%.1f,%.1f)\n",
                      it.m.agent_id, it.m.x, it.m.y);
      }
      if (t) { if (myTask == t->tid) myTask = 0; t->used = false; }
    }

#if VERBOSE_RX
    Serial.printf("RX from=%u type=0x%02X seq=%u tx_ms=%lu rssi=%d\n",
                  it.m.agent_id, it.m.msg_type, it.m.seq,
                  (unsigned long)it.m.timestamp, it.rssi);
#endif
  }

  // ---- P3: take my body's position in, hand my decision back --------------
  //  Deliberately dumb for now: the goal is just "hold position". The point of
  //  this module is that the board HAS a position and shares it — the auction
  //  that picks a real goal lands on top of this, unchanged around it.
  while (Serial.available()) {
    if (!feedUsb((uint8_t)Serial.read())) continue;
    if (uBuf[0] == MSG_SENSOR && uLen == sizeof(usb_sensor_t)) {
      usb_sensor_t s;
      memcpy(&s, uBuf, sizeof(s));
      myX = s.x; myY = s.y;
      havePos = true;
      lastTick = s.tick;
      sensorCount++;

      simRssi = s.rssi;         // laptop-computed RF reading, if it sent one

      // P4: my camera saw something. Decide what it is worth telling the swarm.
      if (s.has_detection && myId != BEACON_ID)
        handleDetection(s.det_x, s.det_y, s.det_conf, now);

      usb_goal_t g = {};
      g.msg_type = MSG_GOAL;
      g.agent_id = myId;
      // THE DECISION, in priority order:
      //   1. a REOBSERVE task I won in the auction (a time-critical sighting)
      //   2. the RF gradient, but ONLY if MY own signal is strong — i.e. I'm
      //      genuinely near the source. This is the key to staying smooth: a far
      //      board hearing the swarm's shared fix does NOT abandon its search to
      //      pile onto the phone; only the closest one or two converge, while
      //      everyone else keeps covering ground, exactly like the search-only
      //      sim. (simRssi is what the laptop last told me I hear; -128 = nothing.)
      //   3. otherwise SEARCH — hold, and let the laptop hand me a frontier goal.
      if (myTask) {
        g.goal_x = myTaskX;
        g.goal_y = myTaskY;
        g.state  = STATE_REOBSERVE;
        g.cur_task = myTask;
      } else if (rfLocked && simRssi != -128 && simRssi >= RF_NEAR_DBM) {
        float gx2, gy2;
        float dx = rfEstX - myX, dy = rfEstY - myY;
        float d = sqrtf(dx * dx + dy * dy);
        if (d > 1e-3f) {
          float ux = dx / d, uy = dy / d;
          float step = (d < RF_STEP) ? d : RF_STEP;
          gx2 = myX + step * ux;
          gy2 = myY + step * uy;
          // fan sideways by id so the agents approach on different lines and
          // keep sampling fresh ground instead of retracing one another
          float off = RF_SPREAD * (float)(((int)myId % 3) - 1);
          gx2 += -uy * off;
          gy2 +=  ux * off;
        } else {
          // sitting on the best sample: spread out and look for a better one
          float th = 6.2831853f * (float)myId / 6.0f;
          gx2 = myX + RF_SPREAD * cosf(th);
          gy2 = myY + RF_SPREAD * sinf(th);
        }
        // never chase the gradient off the edge of the world
        g.goal_x = gx2 < 0 ? 0 : (gx2 > GRID_N - 1 ? GRID_N - 1 : gx2);
        g.goal_y = gy2 < 0 ? 0 : (gy2 > GRID_N - 1 ? GRID_N - 1 : gy2);
        g.state  = STATE_RF_LOCALIZE;
        g.cur_task = TASK_CONFIRM_RF;
      } else {
        g.goal_x = myX;
        g.goal_y = myY;
        g.state  = STATE_SEARCH;
        g.cur_task = 0;
      }
      g.tick = s.tick;          // echo it back so the laptop can time the loop
      writeFrame((const uint8_t *)&g, sizeof(g));
    }
  }

  if (now - lastStats >= STATS_INTERVAL_MS) {
    lastStats = now;
    printStats();
  }

  if (now - lastRoster >= ROSTER_INTERVAL_MS) {
    lastRoster = now;
    printRoster();
  }
}
