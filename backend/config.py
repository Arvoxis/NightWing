"""Configuration for the B2 dashboard backend."""

import os

HOST = "127.0.0.1"
PORT = 8000
STATE_UPDATE_HZ = 10
STATE_UPDATE_INTERVAL = 1.0 / STATE_UPDATE_HZ

# Which state source drives the dashboard:
#   "real"     -> RealStateGenerator     (the python swarm engine)
#   "fake"     -> FakeStateGenerator     (B1's standalone mock, for frontend dev)
#   "hardware" -> HardwareStateGenerator (the 5 live ESP32 boards, fed by
#                 khoj/sim/feeder_real.py POSTing to /ingest)
# Override with:  KHOJ_ENGINE=hardware uvicorn backend.main:app
ENGINE_MODE = os.environ.get("KHOJ_ENGINE", "real").lower()
ENGINE_SEED = int(os.environ.get("KHOJ_SEED", "1"))
