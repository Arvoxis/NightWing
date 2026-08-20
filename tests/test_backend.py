import json

from fastapi.testclient import TestClient

from backend.main import app


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_websocket_streams_complete_simstate():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()

    assert set(first.keys()) == {"agents", "map", "open_tasks", "detections", "confirmed_survivors", "trust_scores"}
    assert set(second.keys()) == set(first.keys())
    assert first["detections"]

    for detection in first["detections"]:
        assert set(detection.keys()) == {"agent_id", "x", "y", "confidence", "bearing", "timestamp"}
        assert 0.0 <= detection["confidence"] <= 1.0
        assert 0.0 <= detection["bearing"] < 360.0


def test_static_map_stays_same_across_messages():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            state_1 = websocket.receive_json()
            state_2 = websocket.receive_json()
            state_3 = websocket.receive_json()

    assert state_1["map"] == state_2["map"] == state_3["map"]
    assert state_1["map"]["width"] == state_2["map"]["width"] == state_3["map"]["width"] == 100
    assert state_1["map"]["grid_width"] == state_2["map"]["grid_width"] == state_3["map"]["grid_width"] == 32


def test_drones_move_between_messages():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            state_1 = websocket.receive_json()
            state_2 = state_1

            for _ in range(20):
                try:
                    state_2 = websocket.receive_json()
                except Exception:
                    break
                positions_1 = {agent["id"]: (agent["x"], agent["y"]) for agent in state_1["agents"]}
                positions_2 = {agent["id"]: (agent["x"], agent["y"]) for agent in state_2["agents"]}
                if positions_1 != positions_2:
                    break

    pos_1 = {agent["id"]: (agent["x"], agent["y"]) for agent in state_1["agents"]}
    pos_2 = {agent["id"]: (agent["x"], agent["y"]) for agent in state_2["agents"]}

    assert pos_1 != pos_2


def test_multiple_clients_receive_same_world_state():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
            state_a = ws_a.receive_json()
            state_b = ws_b.receive_json()

    assert state_a["map"] == state_b["map"]
    assert [agent["id"] for agent in state_a["agents"]] == [agent["id"] for agent in state_b["agents"]]
    assert set(state_a["trust_scores"].keys()) == set(state_b["trust_scores"].keys())


def test_client_disconnect_does_not_crash_server():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            _ = websocket.receive_json()
            websocket.close()

        response = client.get("/health")
        assert response.status_code == 200


def test_json_is_valid_and_serializable():
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            state = websocket.receive_json()

    json.dumps(state)
    assert isinstance(state["open_tasks"], list)
    assert isinstance(state["trust_scores"], dict)
