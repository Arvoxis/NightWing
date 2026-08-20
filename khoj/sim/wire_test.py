#!/usr/bin/env python3
"""QUORUM Module 1 — 'the wire' test.

Pretends to be the laptop-World for one agent: streams a fake moving-drone
MSG_SENSOR packet at 10 Hz and prints the MSG_GOAL the ESP32 echoes back.

SUCCESS looks like: a steady stream of "GOAL ..." lines whose coordinates match
what we sent (goal == our position, or == the detection spot every 20th tick),
and sent/goals_back counts climbing together. That proves the byte contract and
the framing work in both directions on real hardware.

Usage:
    pip install -r requirements.txt
    python wire_test.py                 # auto-detects the port
    python wire_test.py --port /dev/ttyUSB0
"""
import time
import argparse
import threading

import serial
from serial.tools import list_ports

import protocol as p


def find_port():
    ports = list(list_ports.comports())
    for pt in ports:
        desc = (pt.description or "") + (pt.manufacturer or "")
        if any(k in desc for k in ("CP210", "CH340", "CH910", "UART", "USB", "Silicon")):
            return pt.device
    return ports[0].device if ports else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="serial port (auto-detect if omitted)")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        print("No serial port found. Plug in the ESP32, or pass --port.")
        return
    print(f"Opening {port} @ {args.baud} ...")
    ser = serial.Serial(port, args.baud, timeout=0.1)
    time.sleep(2.0)  # ESP32 reboots when the port opens; let it settle

    stop = threading.Event()
    stats = {"sent": 0, "recv": 0}

    def sender():
        t = 0
        while not stop.is_set():
            # a fake drone walking a diagonal; every 20th tick it 'sees' something
            x = 5.0 + (t % 30) * 0.5
            y = 5.0 + (t % 30) * 0.3
            has_det = 1 if (t % 20 == 0) else 0
            pkt = p.pack_sensor(agent_id=1, x=x, y=y, has_detection=has_det,
                                det_x=x + 1.0, det_y=y + 1.0, det_conf=42,
                                rssi=-60, tick=t)
            ser.write(p.frame(pkt))
            stats["sent"] += 1
            t += 1
            time.sleep(0.1)

    threading.Thread(target=sender, daemon=True).start()

    parser = p.FrameParser()
    last_report = time.time()
    print("Streaming sensors at 10 Hz. Ctrl-C to stop.\n")
    try:
        while True:
            data = ser.read(256)
            if data:
                for payload in parser.feed(data):
                    if payload[0] == p.MSG_GOAL and len(payload) == p.GOAL_SIZE:
                        g = p.unpack_goal(payload)
                        stats["recv"] += 1
                        tag = "REOBSERVE" if g["state"] == p.STATE_REOBSERVE else "search"
                        print(f"GOAL  agent={g['agent_id']}  ->({g['goal_x']:5.1f},"
                              f"{g['goal_y']:5.1f})  {tag:9s}  tick={g['tick']}")
            if time.time() - last_report > 2.0:
                print(f"    --- sent={stats['sent']}  goals_back={stats['recv']} ---")
                last_report = time.time()
    except KeyboardInterrupt:
        stop.set()
        time.sleep(0.2)
        ser.close()
        loss = stats["sent"] - stats["recv"]
        print(f"\nDone. sent={stats['sent']} goals_back={stats['recv']} "
              f"(missing {loss}).")
        if stats["recv"] == 0:
            print("No goals came back. Check: right port? board flashed with the "
                  "'agent' env? monitor not stealing the port?")


if __name__ == "__main__":
    main()
