import pytest
from fastapi.testclient import TestClient

import database
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_player_predictions.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield


def _make_game(game_id="game-1"):
    client.post("/games", json={"game_id": game_id, "game_date": "2026-01-15"})


# ============================================================
# /player-predictions/threshold
# ============================================================

def _threshold_payload(game_id="game-1", threshold=25.0, market_odds=None):
    payload = {
        "game_id": game_id,
        "market_type": "points",
        "threshold": threshold,
        "players": [
            {
                "player_name": "Angel Reese",
                "lines": [
                    {"line": 14.5, "over_odds": -110, "under_odds": -110},
                    {"line": 19.5, "over_odds": 150, "under_odds": -190},
                    {"line": 24.5, "over_odds": 980, "under_odds": -2000},
                ],
            },
            {
                "player_name": "Allisha Gray",
                "lines": [
                    {"line": 14.5, "over_odds": -105, "under_odds": -115},
                    {"line": 19.5, "over_odds": 180, "under_odds": -220},
                    {"line": 24.5, "over_odds": 410, "under_odds": -600},
                ],
            },
        ],
    }
    if market_odds is not None:
        payload["market_odds"] = market_odds
    return payload


def test_create_threshold_prediction_without_market_odds():
    _make_game()
    response = client.post(
        "/player-predictions/threshold", json=_threshold_payload()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["market_type"] == "points"
    assert body["threshold"] == 25.0
    assert 0.0 < body["combined_probability"] < 1.0
    # No market_odds given -> nothing to compare against
    assert body["market_odds"] is None
    assert body["status"] is None
    assert body["probability_edge"] is None
    assert len(body["details"]["players"]) == 2


def test_create_threshold_prediction_with_market_odds_computes_edge():
    _make_game()
    response = client.post(
        "/player-predictions/threshold",
        json=_threshold_payload(market_odds=150),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["market_odds"] == 150
    assert body["status"] in ("PASS", "NO_EDGE", "FAIR_OR_BORDERLINE")
    assert body["probability_edge"] is not None
    assert body["market_break_even"] == pytest.approx(0.4, abs=1e-6)


def test_create_threshold_prediction_missing_game_returns_404():
    response = client.post(
        "/player-predictions/threshold",
        json=_threshold_payload(game_id="does-not-exist"),
    )
    assert response.status_code == 404


def test_create_threshold_prediction_rejects_empty_players():
    _make_game()
    payload = _threshold_payload()
    payload["players"] = []
    response = client.post("/player-predictions/threshold", json=payload)
    assert response.status_code == 400


def test_create_threshold_prediction_rejects_duplicate_player():
    _make_game()
    payload = _threshold_payload()
    payload["players"].append(payload["players"][0])
    response = client.post("/player-predictions/threshold", json=payload)
    assert response.status_code == 422


def test_resubmitting_threshold_prediction_overwrites():
    _make_game()
    client.post("/player-predictions/threshold", json=_threshold_payload())
    client.post(
        "/player-predictions/threshold",
        json=_threshold_payload(market_odds=150),
    )

    body = client.get("/games/game-1").json()
    points_predictions = [
        p for p in body["predictions"] if p["market_type"] == "points"
    ]
    assert len(points_predictions) == 1  # overwritten, not duplicated
    assert points_predictions[0]["market_odds"] == 150


def test_different_thresholds_same_game_coexist():
    _make_game()
    client.post(
        "/player-predictions/threshold", json=_threshold_payload(threshold=25.0)
    )
    client.post(
        "/player-predictions/threshold", json=_threshold_payload(threshold=30.0)
    )

    body = client.get("/games/game-1").json()
    points_predictions = [
        p for p in body["predictions"] if p["market_type"] == "points"
    ]
    assert len(points_predictions) == 2
    thresholds = {p["threshold"] for p in points_predictions}
    assert thresholds == {25.0, 30.0}


def test_threshold_prediction_settles_via_results():
    _make_game()
    client.post(
        "/player-predictions/threshold",
        json=_threshold_payload(market_odds=150),
    )
    response = client.post(
        "/results",
        json={
            "game_id": "game-1",
            "market_type": "points",
            "threshold": 25.0,
            "actual_result": 1,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["actual_result"] == 1
    assert body["realized_roi"] == pytest.approx(1.5)  # +150 -> profit 1.5


# ============================================================
# /player-predictions/direct
# ============================================================

def _direct_payload(game_id="game-1", market_type="double_double", market_odds=None):
    payload = {
        "game_id": game_id,
        "market_type": market_type,
        "players": [
            {"player_name": "Nikola Jokic", "yes_odds": -700},
            {"player_name": "Domantas Sabonis", "yes_odds": -350, "no_odds": 260},
        ],
    }
    if market_odds is not None:
        payload["market_odds"] = market_odds
    return payload


def test_create_direct_prediction_without_market_odds():
    _make_game()
    response = client.post(
        "/player-predictions/direct", json=_direct_payload()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["market_type"] == "double_double"
    assert body["threshold"] == 0.0
    assert 0.0 < body["combined_probability"] < 1.0
    assert body["status"] is None
    assert len(body["details"]["players"]) == 2


def test_create_direct_prediction_with_market_odds():
    _make_game()
    response = client.post(
        "/player-predictions/direct",
        json=_direct_payload(market_odds=-1000),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["market_odds"] == -1000
    assert body["status"] in ("PASS", "NO_EDGE", "FAIR_OR_BORDERLINE")


def test_create_direct_prediction_missing_game_returns_404():
    response = client.post(
        "/player-predictions/direct",
        json=_direct_payload(game_id="does-not-exist"),
    )
    assert response.status_code == 404


def test_create_direct_prediction_rejects_empty_players():
    _make_game()
    payload = _direct_payload()
    payload["players"] = []
    response = client.post("/player-predictions/direct", json=payload)
    assert response.status_code == 400


def test_create_direct_prediction_rejects_duplicate_player():
    _make_game()
    payload = _direct_payload()
    payload["players"].append(payload["players"][0])
    response = client.post("/player-predictions/direct", json=payload)
    assert response.status_code == 422


def test_double_double_and_triple_double_coexist_same_game():
    _make_game()
    client.post(
        "/player-predictions/direct",
        json=_direct_payload(market_type="double_double"),
    )
    client.post(
        "/player-predictions/direct",
        json=_direct_payload(market_type="triple_double"),
    )

    body = client.get("/games/game-1").json()
    market_types = {p["market_type"] for p in body["predictions"]}
    assert market_types == {"double_double", "triple_double"}


def test_direct_prediction_settles_via_results():
    _make_game()
    client.post(
        "/player-predictions/direct",
        json=_direct_payload(market_odds=-1000),
    )
    response = client.post(
        "/results",
        json={
            "game_id": "game-1",
            "market_type": "double_double",
            "actual_result": 1,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["actual_result"] == 1
    assert body["realized_roi"] == pytest.approx(0.1)  # -1000 -> profit 0.1


# ============================================================
# Auth applies to these write endpoints too
# ============================================================

def test_threshold_prediction_requires_auth_when_enabled(monkeypatch):
    import main

    _make_game()
    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.post(
        "/player-predictions/threshold", json=_threshold_payload()
    )
    assert response.status_code == 401


def test_direct_prediction_requires_auth_when_enabled(monkeypatch):
    import main

    _make_game()
    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.post(
        "/player-predictions/direct", json=_direct_payload()
    )
    assert response.status_code == 401


def test_prediction_rejects_market_odds_below_100_in_magnitude():
    _make_game()
    response = client.post(
        "/player-predictions/threshold",
        json=_threshold_payload(market_odds=50),
    )
    assert response.status_code == 422
