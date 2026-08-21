from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.state_service import StateService
from backend.websocket_manager import ConnectionManager

manager = ConnectionManager()
state_service = StateService(seed=42)
state_service.set_broadcast_callback(manager.broadcast)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from backend.config import ENGINE_MODE
    banner = {
        "hardware": "HARDWARE - waiting for the ESP32 bridge (khoj/sim/feeder_real.py). "
                    "The page stays empty until real boards feed it.",
        "real": "SIMULATION - pure-python swarm engine. Runs with NO hardware.",
        "fake": "MOCK - B1 fake generator.",
    }.get(ENGINE_MODE, "UNKNOWN mode '%s'" % ENGINE_MODE)
    print("=" * 70)
    print("  KHOJ dashboard backend   |   ENGINE MODE = %s" % ENGINE_MODE.upper())
    print("  " + banner)
    if ENGINE_MODE != "hardware":
        print("  For the real boards:  $env:KHOJ_ENGINE=\"hardware\"   (PowerShell)")
    print("=" * 70)
    await state_service.start()
    try:
        yield
    finally:
        await state_service.stop()
        manager.active_connections.clear()


app = FastAPI(title="Dashboard Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "dashboard-backend", "message": "B2 backend running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/state")
async def get_latest_state() -> dict[str, Any]:
    return state_service.get_state()


@app.post("/restart")
async def restart_search() -> dict[str, Any]:
    return await state_service.reset()


@app.post("/ingest")
async def ingest(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The hardware bridge (khoj/sim/feeder_real.py) POSTs a live snapshot here.
    We store it and immediately push the mapped frame to every connected
    dashboard, so the screen tracks the boards with no polling lag."""
    result = state_service.ingest(snapshot)
    if result.get("ok"):
        state_service.latest_state = state_service.generator.generate_state()
        await manager.broadcast(state_service.latest_state)
    return result


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    await websocket.send_json(state_service.get_state())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
