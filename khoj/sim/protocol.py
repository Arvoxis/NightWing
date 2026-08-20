"""QUORUM wire contract — Python mirror of firmware/lib/quorum_proto/quorum_proto.h

Keep this in lockstep with the C header. The struct format strings below MUST
list the fields in the same order and types as the C structs. Little-endian
('<') + explicit types == no padding, which matches the packed C structs.
"""
import struct

# ---- framing ---------------------------------------------------------------
SYNC0 = 0xAA
SYNC1 = 0x55
MAX_PAYLOAD = 64

# ---- message type ids (must match the C header) ----------------------------
# ESP-NOW
MSG_HEARTBEAT  = 0x01
MSG_BID        = 0x02
MSG_AWARD      = 0x03
MSG_TASK_SPAWN = 0x04
MSG_RF_SAMPLE  = 0x05
# USB
MSG_SENSOR = 0x10
MSG_GOAL   = 0x11

# ---- agent states ----------------------------------------------------------
STATE_SEARCH      = 0
STATE_REOBSERVE   = 1
STATE_RF_LOCALIZE = 2

# ---- struct layouts --------------------------------------------------------
# usb_sensor_t: msg_type,agent_id, x,y, heading, battery,has_det, det_x,det_y,
#               det_conf, rssi, tick
SENSOR_FMT  = "<BBffhBBffBbI"
# usb_goal_t:  msg_type,agent_id, goal_x,goal_y, state, cur_task, tick
GOAL_FMT    = "<BBffBHI"
SENSOR_SIZE = struct.calcsize(SENSOR_FMT)   # 28
GOAL_SIZE   = struct.calcsize(GOAL_FMT)     # 17


def pack_sensor(agent_id, x, y, heading=0, battery=100, has_detection=0,
                det_x=0.0, det_y=0.0, det_conf=0, rssi=-128, tick=0):
    """Build a MSG_SENSOR payload (laptop -> agent)."""
    return struct.pack(SENSOR_FMT, MSG_SENSOR, agent_id, x, y, heading, battery,
                       has_detection, det_x, det_y, det_conf, rssi, tick)


def unpack_goal(payload):
    """Parse a MSG_GOAL payload (agent -> laptop) into a dict."""
    (_mt, agent_id, gx, gy, state, cur_task, tick) = struct.unpack(GOAL_FMT, payload)
    return dict(agent_id=agent_id, goal_x=gx, goal_y=gy,
                state=state, cur_task=cur_task, tick=tick)


def frame(payload: bytes) -> bytes:
    """Wrap a payload in the serial frame: sync, len, payload, xor-checksum."""
    xorc = 0
    for b in payload:
        xorc ^= b
    return bytes([SYNC0, SYNC1, len(payload)]) + payload + bytes([xorc])


class FrameParser:
    """Feed it raw serial bytes; it yields complete, checksum-valid payloads.

    Robust to garbage and to the ESP32's boot noise: it hunts for the 0xAA 0x55
    sync, validates the checksum, and resynchronises on any bad frame.
    """
    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf.extend(data)
        out = []
        while True:
            i = self.buf.find(b"\xAA\x55")
            if i < 0:                       # no sync yet; keep a trailing byte
                if len(self.buf) > 1:
                    del self.buf[:-1]
                break
            if i > 0:                       # drop junk before the sync
                del self.buf[:i]
            if len(self.buf) < 3:           # need the length byte
                break
            length = self.buf[2]
            if length == 0 or length > MAX_PAYLOAD:
                del self.buf[:2]            # bad length -> not a real frame
                continue
            total = 3 + length + 1
            if len(self.buf) < total:       # frame not fully arrived yet
                break
            payload = bytes(self.buf[3:3 + length])
            cksum = self.buf[3 + length]
            xorc = 0
            for b in payload:
                xorc ^= b
            if xorc == cksum:
                del self.buf[:total]
                out.append(payload)
            else:
                del self.buf[:2]            # bad checksum -> resync past this sync
        return out
