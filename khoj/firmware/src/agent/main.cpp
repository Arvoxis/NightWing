// ============================================================================
//  QUORUM  —  Module 1: "the wire"  (agent firmware, wire-test build)
//  ----------------------------------------------------------------------------
//  Purpose: prove the USB message contract works both directions, byte-perfect,
//  on real hardware, before we build ANY swarm logic on top of it.
//
//  Behaviour:
//    * Blinks the onboard LED at 1 Hz so you can see the board is alive.
//    * Listens for framed MSG_SENSOR packets from the laptop.
//    * For each one, makes a trivial "decision" and sends a MSG_GOAL back:
//        - if the sensor reports a detection -> goal = the detection spot
//        - otherwise                          -> goal = hold current position
//
//  This is deliberately dumb. The point is the WIRE, not the brain. Once the
//  laptop sees goals coming back that match what it sent, the contract is
//  proven and every later module rides on it safely.
// ============================================================================
#include <Arduino.h>
#include <quorum_proto.h>
#include <string.h>

static const uint8_t LED_PIN = 2;   // onboard LED on most ESP32 devkits

// ---- send one framed message over USB serial -------------------------------
static void writeFrame(const uint8_t *payload, uint8_t len) {
  uint8_t xorc = 0;
  for (uint8_t i = 0; i < len; i++) xorc ^= payload[i];
  Serial.write(QP_SYNC0);
  Serial.write(QP_SYNC1);
  Serial.write(len);
  Serial.write(payload, len);
  Serial.write(xorc);
}

// ---- incremental frame parser (fed one byte at a time) ---------------------
enum RxState { WAIT0, WAIT1, GETLEN, GETPAYLOAD, GETCKSUM };
static RxState rxState = WAIT0;
static uint8_t rxLen = 0, rxIdx = 0, rxXor = 0;
static uint8_t rxBuf[QP_MAX_PAYLOAD];

// returns true when rxBuf holds one complete, checksum-valid payload of rxLen
static bool feed(uint8_t b) {
  switch (rxState) {
    case WAIT0:  if (b == QP_SYNC0) rxState = WAIT1; break;
    case WAIT1:  rxState = (b == QP_SYNC1) ? GETLEN : WAIT0; break;
    case GETLEN:
      rxLen = b; rxIdx = 0; rxXor = 0;
      rxState = (rxLen == 0 || rxLen > QP_MAX_PAYLOAD) ? WAIT0 : GETPAYLOAD;
      break;
    case GETPAYLOAD:
      rxBuf[rxIdx++] = b; rxXor ^= b;
      if (rxIdx >= rxLen) rxState = GETCKSUM;
      break;
    case GETCKSUM:
      rxState = WAIT0;
      return (b == rxXor);
  }
  return false;
}

static uint32_t lastBlink = 0;
static bool ledOn = false;

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
}

void loop() {
  // heartbeat blink so a human can see the board is running
  if (millis() - lastBlink > 500) {
    lastBlink = millis();
    ledOn = !ledOn;
    digitalWrite(LED_PIN, ledOn);
  }

  // drain and parse whatever the laptop sent
  while (Serial.available()) {
    if (!feed((uint8_t)Serial.read())) continue;

    // a full valid frame is in rxBuf — is it a sensor packet?
    if (rxBuf[0] == MSG_SENSOR && rxLen == sizeof(usb_sensor_t)) {
      usb_sensor_t s;
      memcpy(&s, rxBuf, sizeof(s));

      usb_goal_t g;
      g.msg_type = MSG_GOAL;
      g.agent_id = s.agent_id;
      if (s.has_detection) {
        g.goal_x = s.det_x;
        g.goal_y = s.det_y;
        g.state  = STATE_REOBSERVE;
      } else {
        g.goal_x = s.x;
        g.goal_y = s.y;
        g.state  = STATE_SEARCH;
      }
      g.cur_task = 0;
      g.tick     = s.tick;   // echo the tick so the laptop can measure round-trip
      writeFrame((const uint8_t *)&g, sizeof(g));
    }
  }
}
