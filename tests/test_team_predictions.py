import pytest
from fastapi.testclient import TestClient

import database
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_team_predictions.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield


def _make_game(game_id="game-1"):
    client.post("/games", json={"game_id": game_id, "game_date": "2026-01-15"})


def _payload(game_id="game-1", threshold_a=100.0, threshold_b=None, market_odds=None):
    payload = {
        "game_id": game_id,
        "team_a_name": "Lakers",
        "team_a_lines": [
            {"line": 94.5, "over_odds": -130, "under_odds": -100},
            {"line": 99.5, "over_odds": 100, "under_odds": -130},
            {"line": 104.5, "over_odds": 170, "under_odds": -220},
            {"line": 109.5, "over_odds": 280, "under_odds": -400},
        ],
        "team_b_name": "Celtics",
        "team_b_lines": [
            {"line": 89.5, "over_odds": -120, "under_odds": -110},
            {"line": 94.5, "over_odds": 110, "under_odds": -140},
            {"line": 99.5, "over_odds": 190, "under_odds": -250},
            {"line": 104.5, "over_odds": 320, "under_odds": -500},
        ],
        "game_total_lines": [
            {"line": 189.5, "over_odds": -115, "under_odds": -115},
            {"line": 199.5, "over_odds": 120, "under_odds": -150},
            {"line": 209.5, "over_odds": 220, "under_odds": -300},
            {"line": 219.5, "over_odds": 400, "under_odds": -700},
        ],
        "threshold_a": threshold_a,
    }
    if threshold_b is not None:
        payload["threshold_b"] = threshold_b
    if market_odds is not None:
        payload["market_odds"] = market_odds
    return payload


def test_create_both_teams_over_prediction_without_market_odds():
    _make_game()
    response = client.post("/team-predictions/both-over", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["market_type"] == "both_teams_over"
    assert body["threshold"] == 100.0
    assert 0.0 < body["combined_probability"] < 1.0
    assert body["status"] is None
    assert body["details"]["team_a_name"] == "Lakers"
    assert body["details"]["team_b_name"] == "Celtics"
    assert "implied_correlation" in body["details"]


def test_create_both_teams_over_prediction_with_market_odds():
    _make_game()
    response = client.post(
        "/team-predictions/both-over", json=_payload(market_odds=450)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["market_odds"] == 450
    assert body["status"] in ("PASS", "NO_EDGE", "FAIR_OR_BORDERLINE")


def test_create_both_teams_over_prediction_missing_game_returns_404():
    response = client.post(
        "/team-predictions/both-over", json=_payload(game_id="does-not-exist")
    )
    assert response.status_code == 404


def test_create_both_teams_over_prediction_rejects_mismatched_thresholds():
    _make_game()
    response = client.post(
        "/team-predictions/both-over",
        json=_payload(threshold_a=100.0, threshold_b=95.0),
    )
    assert response.status_code == 400


def test_create_both_teams_over_prediction_threshold_b_omitted_defaults_to_a():
    _make_game()
    response = client.post(
        "/team-predictions/both-over", json=_payload(threshold_a=100.0)
    )
    assert response.status_code == 200
    assert response.json()["threshold"] == 100.0


def test_resubmitting_both_teams_over_overwrites():
    _make_game()
    client.post("/team-predictions/both-over", json=_payload(market_odds=450))
    client.post("/team-predictions/both-over", json=_payload(market_odds=600))

    body = client.get("/games/game-1").json()
    both_teams_predictions = [
        p for p in body["predictions"] if p["market_type"] == "both_teams_over"
    ]
    assert len(both_teams_predictions) == 1
    assert both_teams_predictions[0]["market_odds"] == 600


def test_both_teams_over_coexists_with_other_markets_on_same_game():
    _make_game()
    client.post(
        "/predictions",
        json={"game_id": "game-1", "pf": 44.0, "american_odds": 400},
    )
    client.post("/team-predictions/both-over", json=_payload())

    body = client.get("/games/game-1").json()
    market_types = {p["market_type"] for p in body["predictions"]}
    assert market_types == {"foul_out", "both_teams_over"}


def test_both_teams_over_settles_via_results():
    _make_game()
    client.post("/team-predictions/both-over", json=_payload(market_odds=450))

    response = client.post(
        "/results",
        json={
            "game_id": "game-1",
            "market_type": "both_teams_over",
            "threshold": 100.0,
            "actual_result": 1,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["actual_result"] == 1
    assert body["realized_roi"] == pytest.approx(4.5)  # +450 -> profit 4.5


def test_both_teams_over_requires_auth_when_enabled(monkeypatch):
    import main

    _make_game()
    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.post("/team-predictions/both-over", json=_payload())
    assert response.status_code == 401


def test_performance_report_filters_to_both_teams_over():
    _make_game()
    client.post("/team-predictions/both-over", json=_payload(market_odds=450))
    client.post(
        "/results",
        json={
            "game_id": "game-1",
            "market_type": "both_teams_over",
            "threshold": 100.0,
            "actual_result": 1,
        },
    )

    response = client.get("/performance", params={"market_type": "both_teams_over"})
    body = response.json()
    assert body["total_predictions"] == 1
    assert body["settled_predictions"] == 1
    assert body["hits"] == 1
