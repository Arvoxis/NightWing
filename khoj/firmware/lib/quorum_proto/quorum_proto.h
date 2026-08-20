#pragma once
#include <stdint.h>

// ============================================================================
//  QUORUM shared wire contract  —  THE TREATY
//  ----------------------------------------------------------------------------
//  Every board AND the laptop agree on exactly these bytes. This file is the
//  single source of truth for the two links in the system:
//
//    * ESP-NOW  (agent  <-> agent)   : bids, awards, heartbeats, RF samples
//    * USB      (laptop <-> agent)   : the private sensor packet + the decision
//
//  It is mirrored byte-for-byte in sim/protocol.py. If you change ANYTHING
//  here, change protocol.py in the same commit and tell all four owners.
//  Layout rule: little-endian, packed (no padding). ESP32 is little-endian, so
//  the Python side just uses struct '<...' with matching field order.
// ============================================================================

// ---- serial framing (USB, both directions) --------------------------------
//   [0xAA][0x55][len:1][payload:len bytes][xor:1]
//   xor = XOR over the payload bytes only. len = payload length (<= 64).
#define QP_SYNC0 0xAA
#define QP_SYNC1 0x55
#define QP_MAX_PAYLOAD 64

// ---- message type ids ------------------------------------------------------
// ESP-NOW (agent <-> agent), range 0x00..0x0F
enum {
  MSG_HEARTBEAT  = 0x01,
  MSG_BID        = 0x02,
  MSG_AWARD      = 0x03,
  MSG_TASK_SPAWN = 0x04,
  MSG_RF_SAMPLE  = 0x05,   // <- the heart of Bet A: a shared signal-strength reading
};
// USB (laptop <-> agent), range 0x10..0x1F
enum {
  MSG_SENSOR = 0x10,   // laptop -> agent : private sensor packet
  MSG_GOAL   = 0x11,   // agent  -> laptop: the decision (where I want to go)
};

// ---- agent behaviour states (for the dashboard / debugging) ---------------
enum {
  STATE_SEARCH      = 0,
  STATE_REOBSERVE   = 1,   // flying a second angle at an uncertain sighting
  STATE_RF_LOCALIZE = 2,   // climbing the RF gradient toward a hidden phone
};

// ---- task types (priority ordering: RF > VISUAL > SEARCH) -----------------
enum {
  TASK_SEARCH_CELL    = 0,
  TASK_CONFIRM_VISUAL = 1,
  TASK_CONFIRM_RF     = 2,
};

// ============================================================================
//  ESP-NOW message  (agent <-> agent).  23 bytes — well under the 250 cap.
//  One struct serves every message type; unused fields are just left 0.
// ============================================================================
typedef struct __attribute__((packed)) {
  uint8_t  msg_type;   // MSG_HEARTBEAT / BID / AWARD / TASK_SPAWN / RF_SAMPLE
  uint8_t  agent_id;
  uint16_t task_id;
  float    value;      // BID: bid amount     TASK_SPAWN: task value
  float    x;          // RF_SAMPLE: where sampled / TASK_SPAWN: task pos (grid cells)
  float    y;
  int8_t   rssi;       // RF_SAMPLE: measured signal strength in dBm; else 0
  uint32_t timestamp;  // millis() on the sending board
  uint16_t seq;        // per-sender sequence number (drop duplicates / stale)
} quorum_msg_t;

// ============================================================================
//  USB  laptop -> agent   : the private sensor packet.   28 bytes.
//  "Here is what YOUR body senses right now." Sent only to this one agent.
// ============================================================================
typedef struct __attribute__((packed)) {
  uint8_t  msg_type;       // MSG_SENSOR
  uint8_t  agent_id;
  float    x;              // current position (grid cells)
  float    y;
  int16_t  heading;        // degrees
  uint8_t  battery;        // percent
  uint8_t  has_detection;  // 0 = nothing, 1 = camera saw something
  float    det_x;          // detection position (grid cells)
  float    det_y;
  uint8_t  det_conf;       // detection confidence 0..100
  int8_t   rssi;           // RF reading at this position, dBm (-128 = no reading)
  uint32_t tick;           // sim tick / timestamp
} usb_sensor_t;

// ============================================================================
//  USB  agent -> laptop   : the decision.   17 bytes.
//  "Given what I sense and what the swarm agreed, THIS is where I'm going."
// ============================================================================
typedef struct __attribute__((packed)) {
  uint8_t  msg_type;   // MSG_GOAL
  uint8_t  agent_id;
  float    goal_x;     // where I want my body to move (grid cells)
  float    goal_y;
  uint8_t  state;      // STATE_*
  uint16_t cur_task;   // task id I'm currently servicing (0 = none)
  uint32_t tick;       // echoes the sensor tick I acted on (for latency checks)
} usb_goal_t;
