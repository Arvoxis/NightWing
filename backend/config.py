"""Configuration for the B2 dashboard backend."""

import os

HOST = "127.0.0.1"
PORT = 8000
STATE_UPDATE_HZ = 10
STATE_UPDATE_INTERVAL = 1.0 / STATE_UPDATE_HZ

# Which state source drives the dashboard:
#   "real" -> RealStateGenerator (the actual swarm engine, via the bridge)
#   "fake" -> FakeStateGenerator (B1's standalone mock, kept for frontend dev)
# Override with:  KHOJ_ENGINE=fake uvicorn backend.main:app
ENGINE_MODE = os.environ.get("KHOJ_ENGINE", "real").lower()
ENGINE_SEED = int(os.environ.get("KHOJ_SEED", "1"))
