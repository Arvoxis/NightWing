#pragma once
#include <stdint.h>

// ============================================================================
//  KHOJ — board identity
//  ---------------------------------------------------------------------------
//  ONE binary runs on EVERY board. A board learns its own agent_id by looking
//  up its WiFi MAC in the table below. That means you never rebuild per board:
//  flash the same firmware to all of them and each one figures out who it is.
//
//  BOOTSTRAP (do this once):
//    1. Flash this firmware to every board with the table still empty.
//    2. Each board prints, at boot, a ready-to-paste line like:
//         PASTE ME -> { {0x24,0x6F,0x28,0xAA,0xBB,0xCC}, 1 },
//    3. Paste all of them between the BEGIN/END markers, giving each a UNIQUE
//       id starting at 1.
//    4. Reflash every board. Done — identity is now permanent per board.
//
//  Put a sticker with the id on each board. At 3am you will thank yourself.
// ============================================================================

typedef struct {
  uint8_t mac[6];
  uint8_t id;
} khoj_id_entry_t;

static const khoj_id_entry_t KHOJ_ID_TABLE[] = {
  { {0xE0,0x5A,0x1B,0xA6,0x6A,0x98}, 1 },   
  { {0x80,0xF3,0xDA,0x41,0x53,0x94}, 2 },   
  { {0x68,0x25,0xDD,0x33,0x5F,0xFC}, 3 },   
  { {0x28,0x05,0xA5,0x2F,0xD3,0x78}, 4 },   
  { {0x80,0xF3,0xDA,0x42,0x80,0x5C}, 5 }, 
  // ---- BEGIN ID TABLE (paste boot lines here) ----

  // ---- END ID TABLE ----
};

static const uint8_t KHOJ_ID_TABLE_LEN =
    (uint8_t)(sizeof(KHOJ_ID_TABLE) / sizeof(KHOJ_ID_TABLE[0]));

// Look up this board's id from its MAC. Returns 0 if the MAC is not in the
// table yet (the caller then falls back to a provisional id and warns loudly).
static inline uint8_t khoj_id_from_mac(const uint8_t mac[6]) {
  for (uint8_t i = 0; i < KHOJ_ID_TABLE_LEN; i++) {
    const uint8_t *m = KHOJ_ID_TABLE[i].mac;
    if (m[0] == mac[0] && m[1] == mac[1] && m[2] == mac[2] &&
        m[3] == mac[3] && m[4] == mac[4] && m[5] == mac[5]) {
      return KHOJ_ID_TABLE[i].id;
    }
  }
  return 0;
}

// Provisional id for an unknown board: hashed over the WHOLE MAC (FNV-1a) so
// two unknown boards are unlikely to collide, and parked at 200+ so it can
// never be mistaken for a real assigned id (1..15).
static inline uint8_t khoj_provisional_id(const uint8_t mac[6]) {
  uint32_t h = 2166136261u;
  for (uint8_t i = 0; i < 6; i++) { h ^= mac[i]; h *= 16777619u; }
  return (uint8_t)(200 + (h % 50));
}

// An all-zero MAC means the WiFi driver had not started when we read it. Every
// board would then hash to the SAME provisional id and ignore each other's
// packets — which looks like a broken radio. Detect and shout instead.
static inline bool khoj_mac_is_bogus(const uint8_t mac[6]) {
  return (mac[0] | mac[1] | mac[2] | mac[3] | mac[4] | mac[5]) == 0;
}
