from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass(frozen=True)
class Building:
    id: str
    x: float
    y: float
    width: float = 8.0
    height: float = 6.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class Obstacle:
    id: str
    x: float
    y: float
    radius: float = 5.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "radius": self.radius,
        }


@dataclass(frozen=True)
class StaticMap:
    width: float
    height: float
    grid_width: int
    grid_height: int
    buildings: List[Building] = field(default_factory=list)
    obstacles: List[Obstacle] = field(default_factory=list)
    terrain: List[str] = field(default_factory=lambda: ["open", "urban", "forest", "ridge"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "grid_width": self.grid_width,
            "grid_height": self.grid_height,
            "buildings": [building.to_dict() for building in self.buildings],
            "obstacles": [obstacle.to_dict() for obstacle in self.obstacles],
            "terrain": list(self.terrain),
        }


@dataclass
class DroneState:
    id: int
    x: float
    y: float
    heading: float
    speed: float
    state: str
    battery: float

    def to_public_agent(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "state": self.state,
            "battery": round(self.battery, 2),
        }
