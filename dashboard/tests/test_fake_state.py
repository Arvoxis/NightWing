import json

from dashboard.simulator.fake_state import FakeStateGenerator


def test_simstate_generation_and_fields():
    generator = FakeStateGenerator(seed=42)
    state = generator.generate_state()

    assert set(state.keys()) == {"agents", "map", "open_tasks", "detections", "confirmed_survivors", "trust_scores"}
    assert isinstance(state["agents"], list)
    assert isinstance(state["map"], dict)
    assert isinstance(state["open_tasks"], list)
    assert isinstance(state["detections"], list)
    assert isinstance(state["confirmed_survivors"], list)
    assert isinstance(state["trust_scores"], dict)


def test_detection_schema_and_bounds():
    generator = FakeStateGenerator(seed=42)
    state = generator.generate_state()

    assert state["detections"]
    for det in state["detections"]:
        assert set(det.keys()) == {"agent_id", "x", "y", "confidence", "bearing", "timestamp"}
        assert 0.0 <= det["confidence"] <= 1.0
        assert 0.0 <= det["bearing"] < 360.0
        assert 0.0 <= det["x"] <= 100.0
        assert 0.0 <= det["y"] <= 100.0


def test_battery_and_position_bounds():
    generator = FakeStateGenerator(seed=42)
    state = generator.generate_state()

    for agent in state["agents"]:
        assert 0.0 <= agent["battery"] <= 100.0
        assert 0.0 <= agent["x"] <= 100.0
        assert 0.0 <= agent["y"] <= 100.0


def test_positions_change_and_drones_are_independent():
    generator = FakeStateGenerator(seed=42)
    state_1 = generator.generate_state()
    state_2 = generator.generate_state()

    positions_1 = {agent["id"]: (agent["x"], agent["y"]) for agent in state_1["agents"]}
    positions_2 = {agent["id"]: (agent["x"], agent["y"]) for agent in state_2["agents"]}

    assert positions_1 != positions_2
    assert len(positions_1) == len(positions_2)
    for agent_id in positions_1:
        assert positions_1[agent_id] != positions_2[agent_id]


def test_map_is_static_across_ticks():
    generator = FakeStateGenerator(seed=42)
    state_1 = generator.generate_state()
    state_2 = generator.generate_state()
    state_3 = generator.generate_state()

    map_1 = state_1["map"]
    map_2 = state_2["map"]
    map_3 = state_3["map"]

    assert map_1["width"] == map_2["width"] == map_3["width"]
    assert map_1["height"] == map_2["height"] == map_3["height"]
    assert map_1["grid_width"] == map_2["grid_width"] == map_3["grid_width"]
    assert map_1["grid_height"] == map_2["grid_height"] == map_3["grid_height"]
    assert map_1["buildings"] == map_2["buildings"] == map_3["buildings"]
    assert map_1["obstacles"] == map_2["obstacles"] == map_3["obstacles"]


def test_tasks_and_ids_are_valid():
    generator = FakeStateGenerator(seed=42)
    state = generator.generate_state()
    task_ids = [task["id"] for task in state["open_tasks"]]
    assert len(task_ids) == len(set(task_ids))
    assert task_ids

    for task in state["open_tasks"]:
        assert task["type"] in {"FRONTIER", "REOBSERVE"}
        assert 0.0 <= task["x"] <= 100.0
        assert 0.0 <= task["y"] <= 100.0


def test_agent_ids_unique_and_json_serializable():
    generator = FakeStateGenerator(seed=42)
    state = generator.generate_state()
    agent_ids = [agent["id"] for agent in state["agents"]]

    assert len(agent_ids) == len(set(agent_ids))
    json.dumps(state)


def test_random_seed_is_reproducible():
    state_a = FakeStateGenerator(seed=42).generate_state()
    state_b = FakeStateGenerator(seed=42).generate_state()
    assert state_a == state_b


def test_agents_do_not_teleport_between_ticks():
    generator = FakeStateGenerator(seed=42)
    state_1 = generator.generate_state()
    state_2 = generator.generate_state()

    for a1, a2 in zip(sorted(state_1["agents"], key=lambda x: x["id"]), sorted(state_2["agents"], key=lambda x: x["id"])):
        travel = ((a2["x"] - a1["x"]) ** 2 + (a2["y"] - a1["y"]) ** 2) ** 0.5
        assert travel < 20.0


def test_map_geometry_stays_static_and_grid_dimensions_fixed():
    generator = FakeStateGenerator(seed=42)
    state_1 = generator.generate_state()
    state_2 = generator.generate_state()

    assert state_1["map"]["grid_width"] == state_2["map"]["grid_width"] == 32
    assert state_1["map"]["grid_height"] == state_2["map"]["grid_height"] == 32
    assert state_1["map"]["width"] == state_2["map"]["width"] == 100
    assert state_1["map"]["height"] == state_2["map"]["height"] == 100


def test_trust_scores_are_in_range():
    state = FakeStateGenerator(seed=42).generate_state()
    for value in state["trust_scores"].values():
        assert 0.0 <= value <= 1.0


def test_confirmed_survivors_are_present_and_fixed():
    generator = FakeStateGenerator(seed=42)
    state_1 = generator.generate_state()
    state_2 = generator.generate_state()

    assert state_1["confirmed_survivors"]
    assert state_1["confirmed_survivors"] == state_2["confirmed_survivors"]
    for survivor in state_1["confirmed_survivors"]:
        assert 0.0 <= survivor["x"] <= 100.0
        assert 0.0 <= survivor["y"] <= 100.0


def test_repeated_state_has_same_structure():
    generator = FakeStateGenerator(seed=42)
    state_1 = generator.generate_state()
    state_2 = generator.generate_state()

    assert set(state_1.keys()) == set(state_2.keys())
    assert len(state_1["agents"]) == len(state_2["agents"])
    assert len(state_1["open_tasks"]) == len(state_2["open_tasks"])
