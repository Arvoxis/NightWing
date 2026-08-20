from __future__ import annotations

import json
import math
import random
import time
from typing import Any, Dict, List

from dashboard.simulator.config import (
    AGENT_STATES,
    BATTERY_DRAIN_PER_TICK,
    DETECTION_PROBABILITY,
    MAP_HEIGHT,
    MAP_WIDTH,
    MAX_TURN_RATE_DEG,
    MAX_DRONE_SPEED,
    MIN_DRONE_SPEED,
    NUM_AGENTS,
    TICK_RATE,
    VALID_TASK_TYPES,
)
from dashboard.simulator.environment import create_static_map
from dashboard.simulator.models import DroneState


class FakeStateGenerator:
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.static_map = create_static_map()
        self.tick_count = 0
        self.drones: Dict[int, DroneState] = {}
        self._task_counter = 0
        self.open_tasks: List[Dict[str, Any]] = []
        self.confirmed_survivors: List[Dict[str, float]] = [
            {"x": 72.0, "y": 41.0},
            {"x": 23.0, "y": 81.0},
            {"x": 88.0, "y": 66.0},
        ]
        self.trust_scores: Dict[str, float] = {}
        self._initialize_agents()
        self._initialize_tasks()

    def _initialize_agents(self) -> None:
        initial_positions = [
            (20.0, 30.0),
            (60.0, 20.0),
            (78.0, 70.0),
            (35.0, 82.0),
        ]
        states = ["SEARCHING", "SEARCHING", "REOBSERVING", "RETURNING"]

        for agent_id in range(1, NUM_AGENTS + 1):
            x, y = initial_positions[agent_id - 1]
            drone = DroneState(
                id=agent_id,
                x=x,
                y=y,
                heading=float((agent_id - 1) * 90),
                speed=self.rng.uniform(MIN_DRONE_SPEED, MAX_DRONE_SPEED),
                state=states[agent_id - 1],
                battery=95.0 - (agent_id * 1.5),
            )
            self.drones[agent_id] = drone
            self.trust_scores[str(agent_id)] = round(0.75 + (agent_id * 0.06), 2)

    def _initialize_tasks(self) -> None:
        frontier_task = {
            "id": self._new_task_id(),
            "type": "FRONTIER",
            "x": 42.0,
            "y": 58.0,
            "bids": {"1": 4.2, "2": 7.1, "3": 5.3, "4": 6.4},
        }
        reobserve_task = {
            "id": self._new_task_id(),
            "type": "REOBSERVE",
            "x": 70.0,
            "y": 30.0,
            "bids": {"1": 2.1, "2": 8.6, "3": 4.8, "4": 5.7},
        }
        self.open_tasks = [frontier_task, reobserve_task]

    def _new_task_id(self) -> str:
        self._task_counter += 1
        return f"task_{self._task_counter:03d}"

    def _update_drones(self) -> None:
        dt = 1.0 / TICK_RATE
        for drone in self.drones.values():
            turn = self.rng.uniform(-MAX_TURN_RATE_DEG, MAX_TURN_RATE_DEG)
            drone.heading = (drone.heading + turn) % 360.0

            angle_rad = math.radians(drone.heading)
            step_x = math.cos(angle_rad) * drone.speed * dt
            step_y = math.sin(angle_rad) * drone.speed * dt

            next_x = drone.x + step_x
            next_y = drone.y + step_y

            if next_x < 0.0 or next_x > MAP_WIDTH:
                drone.heading = (180.0 - drone.heading) % 360.0
                next_x = max(0.0, min(MAP_WIDTH, next_x))
            if next_y < 0.0 or next_y > MAP_HEIGHT:
                drone.heading = (-drone.heading) % 360.0
                next_y = max(0.0, min(MAP_HEIGHT, next_y))

            drone.x = max(0.0, min(MAP_WIDTH, next_x))
            drone.y = max(0.0, min(MAP_HEIGHT, next_y))

            if drone.battery > 0.0:
                drone.battery = max(0.0, drone.battery - BATTERY_DRAIN_PER_TICK)

            if drone.battery < 15.0 and drone.state != "RETURNING":
                drone.state = "RETURNING"
            elif drone.battery > 35.0 and drone.state == "RETURNING":
                drone.state = "SEARCHING"

    def _create_detections(self) -> List[Dict[str, Any]]:
        detections: List[Dict[str, Any]] = []
        force_detection = self.tick_count % 3 == 1

        for drone in self.drones.values():
            should_emit = force_detection or self.rng.random() < DETECTION_PROBABILITY
            if should_emit:
                offset_x = self.rng.uniform(-3.0, 3.0)
                offset_y = self.rng.uniform(-3.0, 3.0)
                x = max(0.0, min(MAP_WIDTH, drone.x + offset_x))
                y = max(0.0, min(MAP_HEIGHT, drone.y + offset_y))
                bearing = float(self.rng.uniform(0.0, 360.0))
                confidence = round(self.rng.uniform(0.35, 0.92), 2)
                detections.append(
                    {
                        "agent_id": drone.id,
                        "x": round(x, 2),
                        "y": round(y, 2),
                        "confidence": confidence,
                        "bearing": bearing,
                        "timestamp": int(time.time()),
                    }
                )

        if not detections:
            drone = next(iter(self.drones.values()))
            detection = {
                "agent_id": drone.id,
                "x": round(max(0.0, min(MAP_WIDTH, drone.x + 2.0)), 2),
                "y": round(max(0.0, min(MAP_HEIGHT, drone.y + 2.0)), 2),
                "confidence": 0.61,
                "bearing": 120.0,
                "timestamp": int(time.time()),
            }
            detections.append(detection)
        return detections

    def _update_tasks(self) -> None:
        for task in self.open_tasks:
            for agent_id, bid in list(task["bids"].items()):
                task["bids"][agent_id] = round(max(0.1, float(bid) + self.rng.uniform(-0.2, 0.4)), 2)

        if self.tick_count % 6 == 0:
            frontier_task = {
                "id": self._new_task_id(),
                "type": "FRONTIER",
                "x": round(self.rng.uniform(15.0, 85.0), 1),
                "y": round(self.rng.uniform(15.0, 85.0), 1),
                "bids": {
                    str(agent_id): round(self.rng.uniform(2.0, 8.0), 2)
                    for agent_id in range(1, NUM_AGENTS + 1)
                },
            }
            reobserve_task = {
                "id": self._new_task_id(),
                "type": "REOBSERVE",
                "x": round(self.rng.uniform(20.0, 90.0), 1),
                "y": round(self.rng.uniform(20.0, 90.0), 1),
                "bids": {
                    str(agent_id): round(self.rng.uniform(1.5, 7.5), 2)
                    for agent_id in range(1, NUM_AGENTS + 1)
                },
            }
            self.open_tasks = [frontier_task, reobserve_task]

    def _update_trust_scores(self) -> None:
        for agent_id in range(1, NUM_AGENTS + 1):
            drift = self.rng.uniform(-0.03, 0.03)
            self.trust_scores[str(agent_id)] = max(0.1, min(0.99, self.trust_scores[str(agent_id)] + drift))

    def generate_state(self) -> Dict[str, Any]:
        self._update_drones()
        self._update_tasks()
        self._update_trust_scores()
        self.tick_count += 1

        state = {
            "agents": [drone.to_public_agent() for drone in self.drones.values()],
            "map": self.static_map.to_dict(),
            "open_tasks": self.open_tasks,
            "detections": self._create_detections(),
            "confirmed_survivors": self.confirmed_survivors,
            "trust_scores": self.trust_scores,
        }
        return state

    def update_state(self) -> Dict[str, Any]:
        return self.generate_state()


def main() -> None:
    generator = FakeStateGenerator(seed=42)
    print("MAP:")
    map_data = generator.static_map.to_dict()
    print(f"{map_data['width']} x {map_data['height']}")
    print(f"Buildings: {len(map_data['buildings'])}")
    print(f"Obstacles: {len(map_data['obstacles'])}")
    print(f"Grid: {map_data['grid_width']} x {map_data['grid_height']}")

    for tick in range(1, 4):
        state = generator.generate_state()
        print(f"\nTICK {tick}")
        for agent in sorted(state["agents"], key=lambda item: item["id"]):
            print(f"Drone {agent['id']} → ({agent['x']:.1f}, {agent['y']:.1f}) [{agent['state']}] battery={agent['battery']:.1f}")
        print("Map remains fixed:", state["map"]["width"], "x", state["map"]["height"])
        print("Detections:", len(state["detections"]))
        print("Tasks:", [task["type"] for task in state["open_tasks"]])


if __name__ == "__main__":
    main()
