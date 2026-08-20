# KHOJ — ESP-NOW Mesh Bringup (Phase 1: Intercommunication Only)

Context-free brief for any AI or engineer. No prior chat needed. Scope: **get N boards broadcasting and receiving small packets over ESP-NOW.** No auction, no routing, no allocation logic — that comes in later phases.

---

## 1. What you are building

A flat, leaderless broadcast mesh of ESP32 + ESP8266 boards using **ESP-NOW** (Espressif's connection-less link-layer protocol on 2.4 GHz WiFi hardware). Every board transmits the same struct on the same channel; every board receives every other board's transmissions. No pairing UI, no router, no internet.

**Success criterion:** N boards powered on, each printing over USB serial the packets it hears from the other N−1 boards, at a steady rate, with a measurable loss rate < 5% at benchtop distance.

---

## 2. Hardware

| Item | Qty | Notes |
|---|---|---|
| ESP32-WROOM-32 dev board (ESP32-DevKitC / NodeMCU-32S) | ≥ 2 | Primary target. Little-endian, dual-core Xtensa, 240 MHz. |
| ESP8266 dev board (NodeMCU / Wemos D1 mini) | 0 or more | Optional peer. Also little-endian. Runs same protocol; different API (see §7). |
| USB data cables | one per board | Must carry data, not charge-only. Common failure. |
| Powered USB hub | 1 | Unpowered hubs brown out with ≥ 4 boards. |
| Host laptop | 1 | Runs serial monitors for each board. |

All ESP32 boards should be **identical model** in a first bringup — mixing variants (S2/S3/C3) introduces radio and API differences that waste time.

---

## 3. Non-negotiable constraints (read before writing code)

1. **Every board must be on the same WiFi channel.** ESP-NOW does not channel-hop. Set channel explicitly, do not rely on default.
2. **All boards must use STATION mode** (`WIFI_STA`) and must **not** be connected to any AP. Do not call `WiFi.begin(ssid, pwd)`. Do call `WiFi.mode(WIFI_STA)` and `WiFi.disconnect()`.
3. **Broadcast address is `FF:FF:FF:FF:FF:FF`.** Register it as a peer before sending. No per-peer registration is needed for receiving — the receive callback fires for any ESP-NOW frame on the current channel with a matching payload.
4. **Payload ≤ 250 bytes.** Any single ESP-NOW frame exceeding this is silently dropped by the driver.
5. **Struct must be `__attribute__((packed))`** and use fixed-width integer types (`uint8_t`, `uint16_t`, `uint32_t`, `int8_t`, `float`). Never `int` or `long` — width differs across toolchains.
6. **Little-endian on the wire.** ESP32 and ESP8266 are both little-endian, so no byte-swapping. Do not assume this on any non-Espressif receiver.
7. **No ACK, no retransmit, no ordering.** ESP-NOW broadcast is fire-and-forget. Design every packet to be self-contained and idempotent. Include a monotonic `seq` field per sender so the receiver can detect loss and duplicates.
8. **WiFi promiscuous mode and ESP-NOW cannot coexist reliably on the same chip.** If a sniffer role is later needed, dedicate a separate board to it.

---

## 4. Wire format (Phase 1)

Fixed 16-byte packed struct. Same bytes on ESP32 and ESP8266. Same bytes visible on serial when the receiver dumps them.

```c
// mesh_msg.h — shared by every firmware target
#pragma once
#include <stdint.h>

#define MESH_MSG_HELLO 0x01

typedef struct __attribute__((packed)) {
    uint8_t  msg_type;    // MESH_MSG_HELLO for Phase 1
    uint8_t  sender_id;   // 1..255, unique per board (hardcoded in Phase 1)
    uint16_t seq;         // monotonic per sender; wraps
    uint32_t millis_tx;   // sender's millis() at transmit
    uint8_t  payload[8];  // reserved; zero in Phase 1
} mesh_msg_t;

_Static_assert(sizeof(mesh_msg_t) == 16, "mesh_msg_t must be 16 bytes");
```

**Sender IDs must be unique.** In Phase 1 hardcode them (`#define SENDER_ID 1`, `2`, ...). In a later phase derive from `WiFi.macAddress()`.

---

## 5. Phase-1 firmware behaviour (both roles run on every board)

Every board simultaneously acts as sender and receiver.

**On boot:**
1. `Serial.begin(115200)`.
2. `WiFi.mode(WIFI_STA); WiFi.disconnect();`
3. Force channel: `esp_wifi_set_channel(MESH_CHANNEL, WIFI_SECOND_CHAN_NONE);` — use `MESH_CHANNEL = 1`.
4. `esp_now_init()`. Fail → print error and halt (do not silently retry).
5. Register broadcast peer `FF:FF:FF:FF:FF:FF` on `MESH_CHANNEL`.
6. Register receive callback.
7. Print own MAC and `SENDER_ID` once — needed for §9 verification.

**Loop (every 200 ms):**
1. Fill a `mesh_msg_t` with `msg_type=HELLO`, own `sender_id`, incrementing `seq`, current `millis()`.
2. `esp_now_send(broadcast_addr, (uint8_t*)&msg, sizeof(msg));`
3. Print one line to serial: `TX seq=<n>`.

**Receive callback (interrupt context — keep short):**
- Copy the incoming bytes into a `mesh_msg_t` (validate `len == sizeof(mesh_msg_t)` first).
- Push into a small ring buffer.

**Main loop drain:**
- Pop from the ring buffer, print one line per packet:
  `RX from=<sender_id> seq=<n> tx_ms=<millis_tx> rssi=<rssi>` (RSSI comes from the receive callback's info struct on ESP32; use `-1` if unavailable on ESP8266).

---

## 6. Minimum ESP32 sketch (Arduino framework)

```cpp
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "mesh_msg.h"

#define SENDER_ID      1        // CHANGE PER BOARD
#define MESH_CHANNEL   1
#define TX_INTERVAL_MS 200

static uint8_t BROADCAST[6] = {0xFF,0xFF,0xFF,0xFF,0xFF,0xFF};
static uint16_t tx_seq = 0;
static uint32_t last_tx = 0;

// keep ISR short: stash into a small buffer, print from loop()
struct RxItem { mesh_msg_t m; int8_t rssi; };
static volatile uint8_t rx_head = 0, rx_tail = 0;
static RxItem rx_buf[16];

void on_recv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
    if (len != sizeof(mesh_msg_t)) return;
    uint8_t next = (rx_head + 1) & 15;
    if (next == rx_tail) return;  // drop on overflow
    memcpy((void*)&rx_buf[rx_head].m, data, sizeof(mesh_msg_t));
    rx_buf[rx_head].rssi = info->rx_ctrl ? info->rx_ctrl->rssi : 0;
    rx_head = next;
}

void setup() {
    Serial.begin(115200);
    delay(200);
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    esp_wifi_set_channel(MESH_CHANNEL, WIFI_SECOND_CHAN_NONE);

    if (esp_now_init() != ESP_OK) { Serial.println("esp_now_init FAIL"); while(1); }
    esp_now_register_recv_cb(on_recv);

    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, BROADCAST, 6);
    peer.channel = MESH_CHANNEL;
    peer.encrypt = false;
    if (esp_now_add_peer(&peer) != ESP_OK) { Serial.println("add_peer FAIL"); while(1); }

    Serial.printf("BOOT id=%u mac=%s ch=%u\n",
                  SENDER_ID, WiFi.macAddress().c_str(), MESH_CHANNEL);
}

void loop() {
    uint32_t now = millis();
    if (now - last_tx >= TX_INTERVAL_MS) {
        last_tx = now;
        mesh_msg_t m = { MESH_MSG_HELLO, SENDER_ID, tx_seq++, now, {0} };
        esp_now_send(BROADCAST, (uint8_t*)&m, sizeof(m));
        Serial.printf("TX seq=%u\n", m.seq);
    }
    while (rx_tail != rx_head) {
        RxItem it = rx_buf[rx_tail];
        rx_tail = (rx_tail + 1) & 15;
        Serial.printf("RX from=%u seq=%u tx_ms=%lu rssi=%d\n",
                      it.m.sender_id, it.m.seq, (unsigned long)it.m.millis_tx, it.rssi);
    }
}
```

Change `SENDER_ID` per flash. That is the only per-board diff in Phase 1.

---

## 7. ESP8266 differences

ESP8266 runs the same protocol but the API differs. Key changes:

- Include `<ESP8266WiFi.h>` and `<espnow.h>` (not `<esp_now.h>`).
- No `esp_now_recv_info_t`. Callback signature is `void cb(uint8_t *mac, uint8_t *data, uint8_t len)`.
- No RSSI in the receive callback — set `rssi = 0` and skip.
- Channel set via `wifi_set_channel(MESH_CHANNEL)` after `WiFi.mode(WIFI_STA)`.
- Must call `esp_now_set_self_role(ESP_NOW_ROLE_COMBO)` after init.
- `esp_now_add_peer(mac, ESP_NOW_ROLE_SLAVE, MESH_CHANNEL, NULL, 0)` — different signature.

Struct layout, channel, and payload semantics are identical. An ESP32 broadcast is received unchanged by an ESP8266 on the same channel, and vice versa.

---

## 8. Build & flash

**PlatformIO (recommended for multiple targets in one repo):**

```ini
; platformio.ini
[platformio]
default_envs = agent_esp32

[env]
framework = arduino
monitor_speed = 115200
build_flags = -Wall

[env:agent_esp32]
platform = espressif32
board = esp32dev

[env:agent_esp8266]
platform = espressif8266
board = nodemcuv2
```

Layout:
```
firmware/
  include/mesh_msg.h        ; shared struct
  src/agent_esp32/main.cpp  ; §6 sketch
  src/agent_esp8266/main.cpp; §7 port
  platformio.ini
```

Build one target with `pio run -e agent_esp32`. Flash a specific board by port: `pio run -e agent_esp32 -t upload --upload-port /dev/ttyUSB0`.

**Serial port pinning:** on Linux, `/dev/ttyUSB0` numbering changes across reboots. Pin by USB serial number with a udev rule so each board maps to a stable name (`/dev/ttyKHOJ_1`, `_2`, ...). This matters as soon as you have ≥ 3 boards on one laptop.

---

## 9. Bring-up test procedure

Execute in this order. Do not skip.

**T1 — one board, transmit only.** Flash one ESP32 with `SENDER_ID = 1`. Open serial monitor. Expect one `TX seq=<n>` line every 200 ms. Confirms init succeeds, no crash.

**T2 — two boards, mutual reception.** Flash a second ESP32 with `SENDER_ID = 2`. Open two serial monitors. Each board must print `RX from=<other_id> seq=<n>` continuously. `seq` must be monotonically increasing (allowing wraps).

**T3 — loss measurement.** Let T2 run 60 seconds. Count RX lines. Expected ≈ 300 per side (60 s × 5 Hz). Loss rate = `(300 − actual) / 300`. Benchtop < 5%. Log the number.

**T4 — third board.** Add `SENDER_ID = 3`. Every board must now RX from the other two. If one direction stops working, check channel and MAC uniqueness first.

**T5 — ESP8266 interop (if applicable).** Flash one ESP8266 with `SENDER_ID = 4`. The ESP32s must receive its packets and vice versa. Same channel, same struct.

**T6 — range check.** Walk one board away from the others. Log the distance at which loss rate crosses 20%. This is the effective operating range for the current environment. Indoor with obstacles: expect 15–40 m. Outdoor line-of-sight: 100+ m.

---

## 10. Common failures and their causes

| Symptom | Likely cause |
|---|---|
| No `RX` lines on any board | Different channels. Set `MESH_CHANNEL` identically and force it after `WiFi.mode`. |
| `esp_now_init FAIL` | Called before `WiFi.mode(WIFI_STA)`, or WiFi is still associating. Add `WiFi.disconnect()` before init. |
| `add_peer FAIL` on ESP32 | Peer struct not zero-initialised. Use `esp_now_peer_info_t peer = {};`. |
| Random reboots / stack overflow | Doing `Serial.printf` from the receive callback. Move all printing to `loop()` via a buffer. |
| RX works one direction only | Two boards happen to share `SENDER_ID`. IDs must be unique. |
| `seq` jumps by hundreds | Packet loss (radio contention, distance, interference). Expected occasionally; investigate if constant. |
| Loss rate > 20% at 1 m | Poor USB power (unpowered hub), or another WiFi network on the same channel saturating the air. Try `MESH_CHANNEL = 6` or `11`. |
| ESP8266 crashes on RX | `Serial.print` inside the ESP8266 callback. Same rule as ESP32 — buffer and print from `loop()`. |
| Bytes on wire don't match struct | Missing `__attribute__((packed))`, or use of `int` / `long`. Enforce with `_Static_assert(sizeof(...) == 16, ...)`. |

---

## 11. What is explicitly out of scope for Phase 1

Do not implement any of the following yet. They belong to later phases and will be added on top of the working mesh:

- Auction bidding, task allocation, consensus tiebreakers.
- Heartbeat timeouts and failure detection logic.
- Any application-layer message types beyond `HELLO`.
- Encryption (`esp_now_set_pmk`) — off in Phase 1 for debuggability.
- Routing or multi-hop relaying — Phase 1 is one-hop broadcast only.
- Dynamic channel selection.
- Position, motor, sensor payloads.

The `payload[8]` reserved field exists solely so Phase 2 can extend without changing struct size.

---

## 12. Definition of done

Phase 1 is complete when **all** of the following hold:

- [ ] ≥ 3 boards (mix of ESP32 and, optionally, ESP8266) power on and print `BOOT id=... mac=... ch=...`.
- [ ] Every board prints `RX` lines from every other board within 2 seconds of the last board booting.
- [ ] `seq` values received are monotonic per sender (allowing 16-bit wrap).
- [ ] Measured loss rate at benchtop distance < 5% over a 60-second window.
- [ ] Effective range logged from §T6.
- [ ] Struct layout verified: `sizeof(mesh_msg_t) == 16` on both ESP32 and ESP8266 (`_Static_assert` compiles).
- [ ] No crashes, no watchdog resets, no `esp_now_send` returning non-`OK` more than occasionally.

Once all six hold, hand off to Phase 2 (application-layer message types + heartbeat + peer table). Do not add features before this list is green.
