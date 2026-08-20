---
name: quorum-wire-contract
description: QUORUM message contract — the treaty all boards + laptop build against
metadata: 
  node_type: memory
  type: reference
  originSessionId: 690fe186-4e0c-4b47-b79d-66f6232f1a2a
  modified: 2026-08-19T20:20:13.155Z
---

QUORUM's single source of truth for wire formats:
`/home/rishits/Desktop/quorum/firmware/lib/quorum_proto/quorum_proto.h`, mirrored in
`/home/rishits/Desktop/quorum/sim/protocol.py`. Keep the two in lockstep — changing
one without the other breaks everyone. Little-endian, packed structs.

**Serial framing (USB, both ways):** `[0xAA][0x55][len:1][payload:len][xor:1]`,
xor over payload bytes only, len ≤ 64.

**Messages:** ESP-NOW `quorum_msg_t` (23 B): msg_type, agent_id, task_id, value(f),
x(f), y(f), rssi(i8), timestamp(u32), seq(u16). Types: HEARTBEAT/BID/AWARD/TASK_SPAWN/
RF_SAMPLE. USB down `usb_sensor_t` (28 B, fmt `<BBffhBBffBbI`) = private sensor packet.
USB up `usb_goal_t` (17 B, fmt `<BBffBHI`) = the decision.

Module 1 ("the wire") is built and verified: firmware at firmware/src/agent/main.cpp
echoes goals; sim/wire_test.py streams sensors at 10 Hz and prints goals back.
Firmware NOT yet flashed/tested on hardware (user has 1 board so far). See [[quorum-project]].
