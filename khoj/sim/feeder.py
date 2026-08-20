#!/usr/bin/env python3
"""KHOJ — position feeder (the testing scaffold).

Plays "the world" for every connected board at once: streams each one a private
sensor packet with its position, reads back the goal it chose, and moves that
board's fake body toward it. This is what turns the boards from radio nodes into
drones that know where they are — WITHOUT waiting on the full laptop stack.

    py feeder.py                 # auto-detect every board
    py feeder.py --ports COM3 COM14
    py feeder.py --hz 10 --grid 32

What you should see: every board reporting a position that moves, and every
board's STATS line showing its peers' positions changing too — which proves the
positions are crossing the ESP-NOW mesh, not just the USB cables.

Ctrl-C to stop.
"""
import argparse
import math
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial missing.  py -m pip install pyserial")

from protocol import pack_sensor, unpack_goal, frame, FrameParser, GOAL_SIZE, MSG_GOAL

# USB-UART bridges used by ESP32 devkits. Bluetooth COM ports are ignored.
BRIDGE_HINTS = ("CP210", "CH340", "CH910", "FTDI", "FT232", "Silicon Labs", "USB-SERIAL")


def find_ports():
    out = []
    for p in list_ports.comports():
        blob = " ".join(str(x) for x in (p.description, p.manufacturer, p.hwid))
        if any(h.lower() in blob.lower() for h in BRIDGE_HINTS):
            out.append(p.device)
    return sorted(out, key=lambda d: int("".join(c for c in d if c.isdigit()) or 0))


class Body:
    """One fake drone. The laptop owns this; the board never sees it."""
    def __init__(self, port, idx, grid):
        self.port = port
        self.grid = grid
        # spread the starting positions so the boards do not all sit on top
        # of each other — makes peer positions obviously different on screen
        self.x = 2.0 + (idx * 5.0) % max(1.0, grid - 4)
        self.y = 2.0 + (idx * 7.0) % max(1.0, grid - 4)
        self.goal = (self.x, self.y)
        # Provisional only. The BOARD owns its identity — it derives its agent_id
        # from its own MAC via khoj_ids.h, and port order has nothing to do with
        # it (COM3 is not necessarily board 1). We learn the real id from the
        # first goal it sends back and switch to that. Getting this wrong would
        # make the laptop address the wrong drone once the auction lands.
        self.agent_id = idx + 1
        self.real_id = None
        self.goals_back = 0
        self.sent = 0
        self.last_state = 0
        # DTR/RTS drive the ESP32 auto-reset circuit (EN and IO0). Different USB
        # bridges idle them differently — a CH9102 board gets HELD IN RESET when
        # the port opens or closes while a CP210x board carries on running. Park
        # both low so we never reset a board just by talking to it.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = 115200
        self.ser.timeout = 0
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        self.ser.dtr = False
        self.ser.rts = False
        self.parser = FrameParser()
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def rssi_from(self, px, py):
        """Log-distance path loss: what this drone would hear from a phone at (px,py).

        The laptop owns this because the laptop owns the truth. The boards never
        see the phone's position — only a number that gets weaker with distance,
        which is exactly what a real radio gives them.
        """
        d = max(0.5, math.hypot(self.x - px, self.y - py))
        rssi = -40.0 - 10.0 * 2.2 * math.log10(d)      # -40 dBm at 1 cell
        return int(max(-100, min(-20, round(rssi))))

    def step(self, tick, speed, phone=None):
        # Fly toward whatever goal the BOARD chose. That is the whole point —
        # the laptop moves the body, the board decides where the body goes.
        # Until a board says otherwise it just holds, so we add a slow drift to
        # keep the drones spread out and sampling from different places.
        tx, ty = self.goal
        if not self.goals_back:
            ang = tick * 0.02 + self.agent_id
            tx = self.grid / 2 + (self.grid / 3) * math.cos(ang)
            ty = self.grid / 2 + (self.grid / 3) * math.sin(ang)
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        if d > 1e-6:
            self.x += dx / d * min(speed, d)
            self.y += dy / d * min(speed, d)

        rssi = self.rssi_from(*phone) if phone else -128
        pkt = pack_sensor(self.agent_id, self.x, self.y, rssi=rssi, tick=tick)
        try:
            self.ser.write(frame(pkt))
            self.sent += 1
        except Exception:
            pass

        try:
            data = self.ser.read(512)
        except Exception:
            data = b""
        if data:
            for payload in self.parser.feed(data):
                if len(payload) == GOAL_SIZE and payload[0] == MSG_GOAL:
                    g = unpack_goal(payload)
                    self.goal = (g["goal_x"], g["goal_y"])
                    self.last_state = g["state"]
                    self.goals_back += 1
                    if g["agent_id"]:
                        if self.real_id is None and g["agent_id"] != self.agent_id:
                            print("   %s is board id=%d (not %d) — using the board's own id"
                                  % (self.port, g["agent_id"], self.agent_id))
                        self.real_id = g["agent_id"]
                        self.agent_id = g["agent_id"]

    def close(self):
        try:
            # leave the reset lines released, or the board sits in reset after
            # we exit and looks dead (LED stops blinking)
            self.ser.dtr = False
            self.ser.rts = False
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ports", nargs="*", default=None)
    ap.add_argument("--hz", type=float, default=10.0)
    ap.add_argument("--grid", type=float, default=32.0)
    ap.add_argument("--speed", type=float, default=0.25, help="cells per tick")
    ap.add_argument("--phone", nargs=2, type=float, metavar=("X", "Y"),
                    default=[22.0, 24.0],
                    help="hidden phone position (ground truth, boards never see it)")
    ap.add_argument("--no-phone", action="store_true",
                    help="send no RF readings at all (positions only)")
    a = ap.parse_args()
    phone = None if a.no_phone else tuple(a.phone)

    ports = a.ports if a.ports else find_ports()
    if not ports:
        sys.exit("No ESP32 boards found. Plug one in, or pass --ports COM3 ...")

    print("Feeding %d board(s): %s   (Ctrl-C to stop)\n" % (len(ports), ", ".join(ports)))
    bodies = []
    for i, p in enumerate(ports):
        try:
            bodies.append(Body(p, i, a.grid))
        except Exception as e:
            print("  !! could not open %s: %s" % (p, e))
    if not bodies:
        sys.exit("No port could be opened. Close any serial monitor holding them.")

    dt = 1.0 / a.hz
    tick = 0
    try:
        while True:
            t0 = time.time()
            for b in bodies:
                b.step(tick, a.speed, phone)
            if tick % max(1, int(a.hz / 2)) == 0:
                if phone:
                    # how close is the swarm getting? boards never see this line
                    ds = [math.hypot(b.x - phone[0], b.y - phone[1]) for b in bodies]
                    print("           hidden phone @ (%.1f,%.1f)   nearest drone %.1f cells"
                          % (phone[0], phone[1], min(ds)))
                cells = []
                for b in bodies:
                    ok = "OK " if b.goals_back else "...."
                    tag = ("id%d" % b.real_id) if b.real_id else "id?"
                    cells.append("%s %s (%4.1f,%4.1f)->(%4.1f,%4.1f) %s %d/%d"
                                 % (b.port, tag, b.x, b.y,
                                    b.goal[0], b.goal[1], ok, b.goals_back, b.sent))
                print("t=%-6d %s" % (tick, "   |   ".join(cells)))
            tick += 1
            time.sleep(max(0.0, dt - (time.time() - t0)))
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        silent = [b.port for b in bodies if b.goals_back == 0]
        for b in bodies:
            b.close()
        if silent:
            print("\nNO GOALS came back from: %s" % ", ".join(silent))
            print("That board is running firmware without USB support — reflash "
                  "the `mesh` env.")
        else:
            print("\nAll boards answered. Positions are live on the mesh.")


if __name__ == "__main__":
    main()
