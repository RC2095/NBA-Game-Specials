import pytest
from fastapi.testclient import TestClient

import database
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_batch.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield


def _make_games(*game_ids):
    for game_id in game_ids:
        client.post("/games", json={"game_id": game_id, "game_date": "2026-01-15"})


# ============================================================
# POST /predictions/batch  (foul-out)
# ============================================================

def test_foul_batch_all_succeed():
    _make_games("b-foul-1", "b-foul-2", "b-foul-3")
    response = client.post(
        "/predictions/batch",
        json={
            "predictions": [
                {"game_id": "b-foul-1", "pf": 44.0, "american_odds": 400},
                {"game_id": "b-foul-2", "pf": 39.0, "american_odds": -300},
                {"game_id": "b-foul-3", "pf": 48.0, "american_odds": 200},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["succeeded"] == 3
    assert body["failed"] == 0
    assert all(r["success"] for r in body["results"])


def test_foul_batch_isolates_one_bad_item():
    _make_games("b-foul-good-1", "b-foul-good-2")
    response = client.post(
        "/predictions/batch",
        json={
            "predictions": [
                {"game_id": "b-foul-good-1", "pf": 44.0, "american_odds": 400},
                {"game_id": "does-not-exist", "pf": 44.0, "american_odds": 400},
                {"game_id": "b-foul-good-2", "pf": 39.0, "american_odds": -300},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["results"][0]["success"] is True
    assert body["results"][1]["success"] is False
    assert "not found" in body["results"][1]["error"]
    assert body["results"][2]["success"] is True

    # Confirm both good ones actually landed
    assert client.get("/games/b-foul-good-1").json()["predictions"]
    assert client.get("/games/b-foul-good-2").json()["predictions"]


def test_foul_batch_rejects_empty():
    response = client.post("/predictions/batch", json={"predictions": []})
    assert response.status_code == 400


def test_foul_batch_requires_auth_when_enabled(monkeypatch):
    import main

    _make_games("b-foul-auth")
    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.post(
        "/predictions/batch",
        json={"predictions": [{"game_id": "b-foul-auth", "pf": 44.0, "american_odds": 400}]},
    )
    assert response.status_code == 401


# ============================================================
# POST /player-predictions/threshold/batch
# ============================================================

def _threshold_item(game_id, threshold=25.0):
    return {
        "game_id": game_id,
        "market_type": "points",
        "threshold": threshold,
        "players": [
            {
                "player_name": "Angel Reese",
                "lines": [{"line": 24.5, "over_odds": 980, "under_odds": -2000}],
            }
        ],
    }


def test_threshold_batch_all_succeed():
    _make_games("b-thresh-1", "b-thresh-2")
    response = client.post(
        "/player-predictions/threshold/batch",
        json={
            "predictions": [
                _threshold_item("b-thresh-1"),
                _threshold_item("b-thresh-2", threshold=30.0),
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0


def test_threshold_batch_isolates_bad_data():
    _make_games("b-thresh-good", "b-thresh-bad")
    bad_item = _threshold_item("b-thresh-bad")
    bad_item["players"] = []  # empty players -> rejected by _price_threshold_players

    response = client.post(
        "/player-predictions/threshold/batch",
        json={"predictions": [_threshold_item("b-thresh-good"), bad_item]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    assert body["results"][1]["success"] is False


# ============================================================
# POST /player-predictions/direct/batch
# ============================================================

def _direct_item(game_id, market_type="double_double"):
    return {
        "game_id": game_id,
        "market_type": market_type,
        "players": [{"player_name": "Nikola Jokic", "yes_odds": -700}],
    }


def test_direct_batch_all_succeed():
    _make_games("b-direct-1", "b-direct-2")
    response = client.post(
        "/player-predictions/direct/batch",
        json={
            "predictions": [
                _direct_item("b-direct-1", "double_double"),
                _direct_item("b-direct-2", "triple_double"),
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == 2


def test_direct_batch_isolates_missing_game():
    _make_games("b-direct-good")
    response = client.post(
        "/player-predictions/direct/batch",
        json={
            "predictions": [
                _direct_item("b-direct-good"),
                _direct_item("does-not-exist"),
            ]
        },
    )
    body = response.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1


# ============================================================
# POST /team-predictions/both-over/batch
# ============================================================

def _both_over_item(game_id, threshold_a=100.0, threshold_b=None):
    item = {
        "game_id": game_id,
        "team_a_name": "Lakers",
        "team_a_lines": [
            {"line": 94.5, "over_odds": -130, "under_odds": -100},
            {"line": 99.5, "over_odds": 100, "under_odds": -130},
        ],
        "team_b_name": "Celtics",
        "team_b_lines": [
            {"line": 89.5, "over_odds": -120, "under_odds": -110},
            {"line": 94.5, "over_odds": 110, "under_odds": -140},
        ],
        "game_total_lines": [
            {"line": 189.5, "over_odds": -115, "under_odds": -115},
            {"line": 199.5, "over_odds": 120, "under_odds": -150},
        ],
        "threshold_a": threshold_a,
    }
    if threshold_b is not None:
        item["threshold_b"] = threshold_b
    return item


def test_both_over_batch_all_succeed():
    _make_games("b-both-1", "b-both-2")
    response = client.post(
        "/team-predictions/both-over/batch",
        json={
            "predictions": [
                _both_over_item("b-both-1"),
                _both_over_item("b-both-2"),
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == 2


def test_both_over_batch_isolates_mismatched_threshold():
    _make_games("b-both-good", "b-both-mismatch")
    response = client.post(
        "/team-predictions/both-over/batch",
        json={
            "predictions": [
                _both_over_item("b-both-good"),
                _both_over_item("b-both-mismatch", threshold_a=100.0, threshold_b=95.0),
            ]
        },
    )
    body = response.json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    assert "threshold_a and threshold_b must match" in body["results"][1]["error"]


# ============================================================
# Cross-cutting: batch size limits (shared _process_batch logic,
# checked once here rather than duplicated across all four endpoints)
# ============================================================

def test_batch_rejects_oversized_request():
    _make_games("b-oversized")
    items = [{"game_id": "b-oversized", "pf": 44.0, "american_odds": 400}] * 501
    response = client.post("/predictions/batch", json={"predictions": items})
    assert response.status_code == 400


# ============================================================
# Unexpected errors must not leak internals to the client
# ============================================================

def test_batch_does_not_leak_internal_error_details(monkeypatch):
    """
    The catch-all used to return f"{type(exc).__name__}: {exc}", which
    would expose things like file paths or SQL from an unexpected
    failure. It's logged server-side and reported generically now.
    """
    import main

    _make_games("b-leak")

    def blow_up(item):
        raise RuntimeError("secret internal path /var/lib/app/secrets.db")

    monkeypatch.setattr(main, "_create_foul_prediction_internal", blow_up)

    response = client.post(
        "/predictions/batch",
        json={"predictions": [{"game_id": "b-leak", "pf": 44.0, "american_odds": 400}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    error = body["results"][0]["error"]
    assert "secret" not in error
    assert "RuntimeError" not in error
    assert "Internal error" in error
