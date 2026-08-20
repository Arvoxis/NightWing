# NightWing / KHOJ

## Project layout

- `engine/` - shared protocol, world simulation, swarm auction, belief fusion, and perception projection.
- `backend/` - FastAPI service and WebSocket state transport.
- `dashboard/` - simulator state adapters and dashboard-side models.
- `frontend/` - frozen Canvas dashboard (`index.html`, `app.js`, `styles.css`).
- `detector/` - YOLO training, inference, conversion, and Jetson export tools.
- `scripts/` - metrics, viewer, and perception demonstration entry points.
- `tests/` - backend and WebSocket checks.
- `docs/` - state contract documentation.

## Run the system

Start the real backend:

```text
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Serve the dashboard in a second terminal:

```text
python -m http.server 3000 --bind 127.0.0.1 --directory frontend
```

Open `http://127.0.0.1:3000/`.

Use module entry points for the simulation tools:

```text
python -m engine.run_sim --ticks 400
python -m scripts.viewer
python -m scripts.metrics --seeds 30 --budget 60
```