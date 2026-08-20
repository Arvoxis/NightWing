from __future__ import annotations

from dashboard.simulator.models import Building, Obstacle, StaticMap


def create_static_map() -> StaticMap:
    buildings = [
        Building(id="building_1", x=20.0, y=20.0, width=8.0, height=8.0),
        Building(id="building_2", x=68.0, y=70.0, width=10.0, height=8.0),
        Building(id="building_3", x=46.0, y=28.0, width=9.0, height=7.0),
        Building(id="building_4", x=82.0, y=45.0, width=8.0, height=9.0),
    ]
    obstacles = [
        Obstacle(id="obstacle_1", x=35.0, y=50.0, radius=4.0),
        Obstacle(id="obstacle_2", x=60.0, y=35.0, radius=4.5),
        Obstacle(id="obstacle_3", x=52.0, y=78.0, radius=5.0),
        Obstacle(id="obstacle_4", x=75.0, y=16.0, radius=3.5),
    ]

    return StaticMap(
        width=100.0,
        height=100.0,
        grid_width=32,
        grid_height=32,
        buildings=buildings,
        obstacles=obstacles,
        terrain=["open", "urban", "forest", "ridge"],
    )
