# SimState Contract for B1

This document defines the fake simulator contract used by B1, which produces a compatible SimState payload for later B2 and B3 consumers.

## Core principle

- STATIC MAP: The physical environment is generated once and remains unchanged across ticks.
- DYNAMIC STATE: Drone positions, battery levels, detections, tasks, and trust values update every tick.
- The dashboard must not care whether the SimState came from the fake generator or a future real simulator.

## Detection (NON-NEGOTIABLE)

Every detection contains the required fields below:

{
  "agent_id": 2,
  "x": 42.5,
  "y": 31.2,
  "confidence": 0.78,
  "bearing": 135.0,
  "timestamp": 1724150000
}

Rules:
- Detection.bearing is NON-NEGOTIABLE.
- detection.agent_id: integer drone id
- detection.x: map coordinate in x-space
- detection.y: map coordinate in y-space
- detection.confidence: float in the range 0.0 to 1.0
- detection.bearing: float in degrees, between 0 and < 360
- detection.timestamp: POSIX timestamp

## Agent

Each agent is represented as:

{
  "id": 1,
  "x": 20.0,
  "y": 30.0,
  "state": "SEARCHING",
  "battery": 95.0
}

Notes:
- x and y are map coordinates.
- battery is between 0 and 100.
- state is one of the configured fake states such as SEARCHING, REOBSERVING, RETURNING, IDLE.

## Map

The map is a static environment object created once and reused every tick.

{
  "width": 100,
  "height": 100,
  "grid_width": 32,
  "grid_height": 32,
  "buildings": [...],
  "obstacles": [...],
  "terrain": ["open", "urban", "forest", "ridge"]
}

Rules:
- map width and height do not change
- grid dimensions remain fixed at 32 x 32
- building positions stay fixed
- obstacle positions stay fixed
- only dynamic search-state metadata may change if later introduced

## OpenTask

Tasks have the following structure:

{
  "id": "task_001",
  "type": "FRONTIER",
  "x": 40.0,
  "y": 55.0,
  "bids": {
    "1": 4.2,
    "2": 7.1
  }
}

or:

{
  "id": "task_002",
  "type": "REOBSERVE",
  "x": 70.0,
  "y": 30.0,
  "bids": {
    "1": 2.1,
    "2": 8.6
  }
}

Rules:
- type is FRONTIER or REOBSERVE
- x and y are map coordinates
- bids are fake but plausible values only

## ConfirmedSurvivor

{
  "x": 72.0,
  "y": 41.0
}

Rules:
- fake survivor locations remain fixed unless a future simulation rule changes them
- they are not randomly repositioned every tick

## TrustScores

{
  "1": 0.92,
  "2": 0.81,
  "3": 0.67,
  "4": 0.95
}

Rules:
- trust values are floating point numbers between 0.0 and 1.0

## SimState

The top-level payload is:

{
  "agents": [...],
  "map": {...},
  "open_tasks": [...],
  "detections": [...],
  "confirmed_survivors": [...],
  "trust_scores": {...}
}

The structure is intentionally stable across ticks. The generator returns one fully compatible payload per tick.

## Static vs Dynamic Data

STATIC MAP:
- map dimensions
- grid geometry
- buildings
- obstacles
- terrain
- fixed coordinate system

DYNAMIC STATE:
- drone x/y
- drone heading
- drone state
- battery
- detections
- searched regions if later introduced
- tasks and bids
- trust scores

## Coordinate definitions

- x/y = map coordinates in the world frame
- bearing = degrees from 0 to 359.999...
- confidence = probability 0..1
- trust = 0..1
- battery = 0..100

## B2 compatibility

B2 later consumes this same SimState structure over a transport layer, without needing to know whether it came from B1 or a future real simulator.
