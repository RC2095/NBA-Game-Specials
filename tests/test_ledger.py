import pytest
from fastapi.testclient import TestClient

import database
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    """
    Point the database module at a fresh, empty SQLite file for every
    test function so ledger tests never see each other's data.
    """
    db_path = str(tmp_path / "test_ledger.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield


# ============================================================
# /games
# ============================================================

def test_create_game():
    response = client.post(
        "/games", json={"game_id": "game-1", "game_date": "2026-01-15"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["game_id"] == "game-1"
    assert body["game_date"] == "2026-01-15"
    # game rows no longer carry market-specific fields
    assert "status" not in body
    assert "pf" not in body


def test_create_duplicate_game_rejected():
    client.post("/games", json={"game_id": "dup-1", "game_date": "2026-01-15"})
    response = client.post(
        "/games", json={"game_id": "dup-1", "game_date": "2026-01-16"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "bad_game_id", ["", "has a space", "has/a/slash", "has#hash", "a" * 201]
)
def test_create_game_rejects_invalid_game_id(bad_game_id):
    """
    game_id becomes part of URL paths (e.g. DELETE /games/{game_id}/
    predictions/{market_type}) -- a slash in particular would silently
    break that routing rather than erroring clearly, so it's rejected
    at creation time instead.
    """
    response = client.post(
        "/games", json={"game_id": bad_game_id, "game_date": "2026-01-15"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "good_game_id", ["2026-01-15-LAL-BOS", "game_1", "game.1", "A", "a" * 200]
)
def test_create_game_accepts_valid_game_id(good_game_id):
    response = client.post(
        "/games", json={"game_id": good_game_id, "game_date": "2026-01-15"}
    )
    assert response.status_code == 201


def test_list_games():
    for i in range(3):
        client.post(
            "/games", json={"game_id": f"list-{i}", "game_date": "2026-01-01"}
        )
    response = client.get("/games")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 3
    assert len(body["games"]) >= 3


def test_get_game_with_predictions_returns_all_markets():
    client.post("/games", json={"game_id": "multi-market", "game_date": "2026-01-15"})
    client.post(
        "/predictions",
        json={"game_id": "multi-market", "pf": 44.0, "american_odds": 400},
    )
    client.post(
        "/player-predictions/direct",
        json={
            "game_id": "multi-market",
            "market_type": "double_double",
            "players": [{"player_name": "Nikola Jokic", "yes_odds": -700}],
        },
    )

    response = client.get("/games/multi-market")
    assert response.status_code == 200
    body = response.json()
    assert body["game_id"] == "multi-market"
    assert len(body["predictions"]) == 2
    market_types = {p["market_type"] for p in body["predictions"]}
    assert market_types == {"foul_out", "double_double"}


def test_get_game_missing_returns_404():
    response = client.get("/games/does-not-exist")
    assert response.status_code == 404


def test_update_game_date():
    client.post("/games", json={"game_id": "edit-me", "game_date": "2026-01-01"})
    response = client.patch("/games/edit-me", json={"game_date": "2026-02-02"})
    assert response.status_code == 200
    assert response.json()["game_date"] == "2026-02-02"


def test_update_missing_game_returns_404():
    response = client.patch(
        "/games/does-not-exist", json={"game_date": "2026-02-02"}
    )
    assert response.status_code == 404


def test_delete_game_cascades_predictions():
    client.post("/games", json={"game_id": "delete-me", "game_date": "2026-01-01"})
    client.post(
        "/predictions",
        json={"game_id": "delete-me", "pf": 44.0, "american_odds": 400},
    )
    response = client.delete("/games/delete-me")
    assert response.status_code == 204

    follow_up = client.get("/games/delete-me")
    assert follow_up.status_code == 404


def test_delete_missing_game_returns_404():
    response = client.delete("/games/does-not-exist")
    assert response.status_code == 404


# ============================================================
# /predictions (foul-out market)
# ============================================================

def test_create_foul_prediction_updates_game():
    client.post("/games", json={"game_id": "game-2", "game_date": "2026-01-15"})

    response = client.post(
        "/predictions",
        json={"game_id": "game-2", "pf": 44.0, "american_odds": 400},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["market_type"] == "foul_out"
    assert body["threshold"] == 0.0
    assert body["combined_probability"] == pytest.approx(0.228)
    assert body["market_odds"] == 400
    assert body["status"] == "PASS"
    assert body["details"]["pf"] == 44.0


def test_create_foul_prediction_missing_game_returns_404():
    response = client.post(
        "/predictions",
        json={"game_id": "does-not-exist", "pf": 44.0, "american_odds": 400},
    )
    assert response.status_code == 404


def test_create_foul_prediction_rejects_zero_odds():
    client.post("/games", json={"game_id": "game-3", "game_date": "2026-01-15"})
    response = client.post(
        "/predictions",
        json={"game_id": "game-3", "pf": 44.0, "american_odds": 0},
    )
    assert response.status_code == 422


def test_resubmitting_foul_prediction_overwrites_not_duplicates():
    client.post("/games", json={"game_id": "game-resubmit", "game_date": "2026-01-15"})
    client.post(
        "/predictions",
        json={"game_id": "game-resubmit", "pf": 44.0, "american_odds": 400},
    )
    client.post(
        "/predictions",
        json={"game_id": "game-resubmit", "pf": 39.0, "american_odds": -300},
    )

    body = client.get("/games/game-resubmit").json()
    assert len(body["predictions"]) == 1
    prediction = body["predictions"][0]
    assert prediction["details"]["pf"] == 39.0  # second submission won, not first
    assert prediction["combined_probability"] == pytest.approx(0.1457)


# ============================================================
# /results
# ============================================================

def test_create_result_win_computes_realized_roi():
    client.post("/games", json={"game_id": "game-4", "game_date": "2026-01-15"})
    client.post(
        "/predictions",
        json={"game_id": "game-4", "pf": 44.0, "american_odds": 400},
    )

    response = client.post(
        "/results",
        json={"game_id": "game-4", "market_type": "foul_out", "actual_result": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["actual_result"] == 1
    assert body["realized_roi"] == pytest.approx(4.0)  # +400 -> profit 4.0


def test_create_result_loss_computes_realized_roi():
    client.post("/games", json={"game_id": "game-5", "game_date": "2026-01-15"})
    client.post(
        "/predictions",
        json={"game_id": "game-5", "pf": 44.0, "american_odds": 400},
    )

    response = client.post(
        "/results",
        json={"game_id": "game-5", "market_type": "foul_out", "actual_result": 0},
    )
    assert response.status_code == 200
    assert response.json()["realized_roi"] == pytest.approx(-1.0)


def test_create_result_missing_game_returns_404():
    response = client.post(
        "/results",
        json={
            "game_id": "does-not-exist",
            "market_type": "foul_out",
            "actual_result": 1,
        },
    )
    assert response.status_code == 404


def test_create_result_without_prediction_returns_400():
    client.post("/games", json={"game_id": "game-6", "game_date": "2026-01-15"})
    response = client.post(
        "/results",
        json={"game_id": "game-6", "market_type": "foul_out", "actual_result": 1},
    )
    assert response.status_code == 400


def test_create_result_wrong_market_type_returns_400():
    # A prediction exists for foul_out but not for points-25 on this game.
    client.post("/games", json={"game_id": "game-6b", "game_date": "2026-01-15"})
    client.post(
        "/predictions",
        json={"game_id": "game-6b", "pf": 44.0, "american_odds": 400},
    )
    response = client.post(
        "/results",
        json={
            "game_id": "game-6b",
            "market_type": "points",
            "threshold": 25,
            "actual_result": 1,
        },
    )
    assert response.status_code == 400


def test_create_result_rejects_invalid_actual_result():
    client.post("/games", json={"game_id": "game-7", "game_date": "2026-01-15"})
    client.post(
        "/predictions",
        json={"game_id": "game-7", "pf": 44.0, "american_odds": 400},
    )
    response = client.post(
        "/results",
        json={"game_id": "game-7", "market_type": "foul_out", "actual_result": 2},
    )
    assert response.status_code == 422


def test_result_without_market_odds_has_null_realized_roi():
    client.post("/games", json={"game_id": "game-no-odds", "game_date": "2026-01-15"})
    client.post(
        "/player-predictions/direct",
        json={
            "game_id": "game-no-odds",
            "market_type": "double_double",
            "players": [{"player_name": "Nikola Jokic", "yes_odds": -700}],
        },
    )
    response = client.post(
        "/results",
        json={
            "game_id": "game-no-odds",
            "market_type": "double_double",
            "actual_result": 1,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["actual_result"] == 1
    assert body["realized_roi"] is None


# ============================================================
# /history
# ============================================================

def _seed_two_settled_foul_predictions():
    client.post("/games", json={"game_id": "hist-win", "game_date": "2026-01-15"})
    client.post(
        "/predictions",
        json={"game_id": "hist-win", "pf": 44.0, "american_odds": 400},
    )
    client.post(
        "/results",
        json={"game_id": "hist-win", "market_type": "foul_out", "actual_result": 1},
    )

    client.post("/games", json={"game_id": "hist-loss", "game_date": "2026-01-16"})
    client.post(
        "/predictions",
        json={"game_id": "hist-loss", "pf": 39.0, "american_odds": -300},
    )
    client.post(
        "/results",
        json={"game_id": "hist-loss", "market_type": "foul_out", "actual_result": 0},
    )


def test_history_returns_all_predictions():
    _seed_two_settled_foul_predictions()
    response = client.get("/history")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["predictions"]) == 2


def test_history_filters_by_status():
    _seed_two_settled_foul_predictions()
    response = client.get("/history", params={"status": "PASS"})
    body = response.json()
    assert body["total"] == 1
    assert body["predictions"][0]["game_id"] == "hist-win"


def test_history_filters_by_market_type():
    _seed_two_settled_foul_predictions()
    client.post("/games", json={"game_id": "hist-points", "game_date": "2026-01-17"})
    client.post(
        "/player-predictions/threshold",
        json={
            "game_id": "hist-points",
            "market_type": "points",
            "threshold": 25,
            "players": [
                {
                    "player_name": "Angel Reese",
                    "lines": [{"line": 24.5, "over_odds": 980, "under_odds": -2000}],
                }
            ],
        },
    )

    response = client.get("/history", params={"market_type": "points"})
    body = response.json()
    assert body["total"] == 1
    assert body["predictions"][0]["market_type"] == "points"


def test_history_limit_validation():
    response = client.get("/history", params={"limit": 0})
    assert response.status_code == 422


def test_history_pagination_with_offset():
    for i in range(5):
        client.post(
            "/games", json={"game_id": f"page-game-{i}", "game_date": "2026-01-01"}
        )
        client.post(
            "/predictions",
            json={"game_id": f"page-game-{i}", "pf": 44.0, "american_odds": 400},
        )

    page1 = client.get("/history", params={"limit": 2, "offset": 0}).json()
    page2 = client.get("/history", params={"limit": 2, "offset": 2}).json()
    assert page1["total"] == 5
    assert len(page1["predictions"]) == 2
    assert len(page2["predictions"]) == 2


# ============================================================
# /performance
# ============================================================

def test_performance_summary_reflects_seeded_data():
    _seed_two_settled_foul_predictions()
    response = client.get("/performance")
    body = response.json()
    assert body["total_predictions"] == 2
    assert body["settled_predictions"] == 2
    assert body["hits"] == 1
    assert body["hit_rate"] == pytest.approx(0.5)
    assert body["market_type_counts"] == {"foul_out": 2}


def test_performance_summary_market_type_filter():
    _seed_two_settled_foul_predictions()
    response = client.get("/performance", params={"market_type": "points"})
    body = response.json()
    assert body["total_predictions"] == 0


def test_performance_summary_empty_ledger():
    response = client.get("/performance")
    body = response.json()
    assert body["total_predictions"] == 0
    assert body["hit_rate"] is None


# ============================================================
# /calibration-report
# ============================================================

def test_calibration_report_buckets_settled_predictions():
    _seed_two_settled_foul_predictions()
    response = client.get("/calibration-report")
    body = response.json()
    assert body["bucket_width"] == 0.1
    assert len(body["buckets"]) >= 1


def test_calibration_report_rejects_invalid_bucket_width():
    response = client.get("/calibration-report", params={"bucket_width": 0})
    assert response.status_code == 422


def test_calibration_report_rejects_bucket_width_above_one():
    response = client.get("/calibration-report", params={"bucket_width": 1.5})
    assert response.status_code == 422


# ============================================================
# /roi-report
# ============================================================

def test_roi_report_aggregates_settled_predictions():
    _seed_two_settled_foul_predictions()
    response = client.get("/roi-report")
    body = response.json()
    assert body["all_settled"]["count"] == 2
    # Win: +400 -> profit 4.0. Loss: -1.0. Total = 3.0
    assert body["all_settled"]["total_roi"] == pytest.approx(3.0)


def test_roi_report_empty_ledger():
    response = client.get("/roi-report")
    body = response.json()
    assert body["all_settled"]["count"] == 0
    assert body["all_settled"]["total_roi"] is None


# ============================================================
# API KEY AUTH (applies to all ledger writes, not just foul market)
# ============================================================

def test_writes_allowed_without_key_when_auth_disabled(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", None)
    response = client.post(
        "/games", json={"game_id": "no-auth-game", "game_date": "2026-01-01"}
    )
    assert response.status_code == 201


def test_writes_rejected_without_key_when_auth_enabled(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.post(
        "/games", json={"game_id": "needs-auth", "game_date": "2026-01-01"}
    )
    assert response.status_code == 401


def test_writes_accepted_with_correct_key(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.post(
        "/games",
        json={"game_id": "with-auth", "game_date": "2026-01-01"},
        headers={"X-API-Key": "secret123"},
    )
    assert response.status_code == 201


def test_reads_unaffected_by_api_key(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.get("/history")
    assert response.status_code == 200


# ============================================================
# GAME STATUS (postponed / canceled)
# ============================================================

def test_new_game_defaults_to_scheduled():
    response = client.post(
        "/games", json={"game_id": "status-default", "game_date": "2026-01-15"}
    )
    assert response.json()["status"] == "scheduled"


def test_update_game_status_to_postponed():
    client.post(
        "/games", json={"game_id": "status-postpone", "game_date": "2026-01-15"}
    )
    response = client.patch(
        "/games/status-postpone/status", json={"status": "postponed"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "postponed"


def test_update_game_status_to_canceled():
    client.post(
        "/games", json={"game_id": "status-cancel", "game_date": "2026-01-15"}
    )
    response = client.patch(
        "/games/status-cancel/status", json={"status": "canceled"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"


def test_update_game_status_back_to_scheduled():
    client.post(
        "/games", json={"game_id": "status-revert", "game_date": "2026-01-15"}
    )
    client.patch("/games/status-revert/status", json={"status": "postponed"})
    response = client.patch(
        "/games/status-revert/status", json={"status": "scheduled"}
    )
    assert response.json()["status"] == "scheduled"


def test_update_game_status_missing_game_returns_404():
    response = client.patch(
        "/games/does-not-exist/status", json={"status": "postponed"}
    )
    assert response.status_code == 404


def test_update_game_status_rejects_invalid_value():
    client.post(
        "/games", json={"game_id": "status-invalid", "game_date": "2026-01-15"}
    )
    response = client.patch(
        "/games/status-invalid/status", json={"status": "rained_out"}
    )
    assert response.status_code == 422


def test_update_game_status_requires_auth_when_enabled(monkeypatch):
    import main

    client.post(
        "/games", json={"game_id": "status-auth", "game_date": "2026-01-15"}
    )
    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.patch(
        "/games/status-auth/status", json={"status": "postponed"}
    )
    assert response.status_code == 401


def test_games_list_filters_by_status():
    client.post(
        "/games", json={"game_id": "filter-sched-1", "game_date": "2026-01-01"}
    )
    client.post(
        "/games", json={"game_id": "filter-postponed-1", "game_date": "2026-01-02"}
    )
    client.patch(
        "/games/filter-postponed-1/status", json={"status": "postponed"}
    )

    response = client.get("/games", params={"status": "postponed"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["games"][0]["game_id"] == "filter-postponed-1"


def test_predictions_unaffected_by_status_change():
    client.post(
        "/games", json={"game_id": "status-preds", "game_date": "2026-01-15"}
    )
    client.post(
        "/predictions",
        json={"game_id": "status-preds", "pf": 44.0, "american_odds": 400},
    )
    client.patch("/games/status-preds/status", json={"status": "postponed"})

    body = client.get("/games/status-preds").json()
    assert body["status"] == "postponed"
    assert len(body["predictions"]) == 1  # prediction left untouched


# ============================================================
# DELETE a single prediction (not the whole game)
# ============================================================

def test_delete_single_prediction():
    client.post(
        "/games", json={"game_id": "del-pred", "game_date": "2026-01-15"}
    )
    client.post(
        "/predictions",
        json={"game_id": "del-pred", "pf": 44.0, "american_odds": 400},
    )
    client.post(
        "/player-predictions/threshold",
        json={
            "game_id": "del-pred",
            "market_type": "points",
            "threshold": 25,
            "players": [
                {
                    "player_name": "Angel Reese",
                    "lines": [{"line": 24.5, "over_odds": 980, "under_odds": -2000}],
                }
            ],
        },
    )

    response = client.delete("/games/del-pred/predictions/points", params={"threshold": 25})
    assert response.status_code == 204

    body = client.get("/games/del-pred").json()
    market_types = {p["market_type"] for p in body["predictions"]}
    assert market_types == {"foul_out"}  # points removed, foul_out untouched


def test_delete_single_prediction_default_threshold_for_foul_out():
    client.post(
        "/games", json={"game_id": "del-pred-2", "game_date": "2026-01-15"}
    )
    client.post(
        "/predictions",
        json={"game_id": "del-pred-2", "pf": 44.0, "american_odds": 400},
    )
    # threshold omitted -> defaults to NO_THRESHOLD (0), correct for foul_out
    response = client.delete("/games/del-pred-2/predictions/foul_out")
    assert response.status_code == 204

    body = client.get("/games/del-pred-2").json()
    assert body["predictions"] == []


def test_delete_single_prediction_game_not_found_returns_404():
    response = client.delete("/games/does-not-exist/predictions/foul_out")
    assert response.status_code == 404


def test_delete_single_prediction_not_found_returns_404():
    client.post(
        "/games", json={"game_id": "del-pred-3", "game_date": "2026-01-15"}
    )
    response = client.delete("/games/del-pred-3/predictions/foul_out")
    assert response.status_code == 404


def test_delete_single_prediction_requires_auth_when_enabled(monkeypatch):
    import main

    client.post(
        "/games", json={"game_id": "del-pred-auth", "game_date": "2026-01-15"}
    )
    client.post(
        "/predictions",
        json={"game_id": "del-pred-auth", "pf": 44.0, "american_odds": 400},
    )
    monkeypatch.setattr(main, "API_KEY", "secret123")
    response = client.delete("/games/del-pred-auth/predictions/foul_out")
    assert response.status_code == 401


# ============================================================
# QUERY PARAMETER VALIDATION (typos must error, not silently
# return zero results)
# ============================================================
#
# Regression coverage for a usability gap: market_type / status filter
# params used to be plain strings, so a typo like ?market_type=pointz
# silently returned an empty result set -- indistinguishable from "no
# data yet" and very confusing to debug. They're validated against the
# same Literal types the request bodies use, so a typo is a clear 422.

ALL_MARKET_TYPES = [
    "foul_out",
    "points",
    "rebounds",
    "assists",
    "double_double",
    "triple_double",
    "both_teams_over",
]


@pytest.mark.parametrize("endpoint", ["/history", "/performance", "/calibration-report", "/roi-report"])
def test_market_type_filter_rejects_typo(endpoint):
    response = client.get(endpoint, params={"market_type": "pointz"})
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["/history", "/performance", "/calibration-report", "/roi-report"])
@pytest.mark.parametrize("market_type", ALL_MARKET_TYPES)
def test_market_type_filter_accepts_every_valid_market_type(endpoint, market_type):
    response = client.get(endpoint, params={"market_type": market_type})
    assert response.status_code == 200


def test_history_status_filter_rejects_typo():
    response = client.get("/history", params={"status": "PAS"})
    assert response.status_code == 422


@pytest.mark.parametrize("status", ["PASS", "NO_EDGE", "FAIR_OR_BORDERLINE"])
def test_history_status_filter_accepts_valid_values(status):
    response = client.get("/history", params={"status": status})
    assert response.status_code == 200


def test_games_status_filter_rejects_typo():
    response = client.get("/games", params={"status": "rained_out"})
    assert response.status_code == 422


@pytest.mark.parametrize("status", ["scheduled", "postponed", "canceled"])
def test_games_status_filter_accepts_valid_values(status):
    response = client.get("/games", params={"status": status})
    assert response.status_code == 200


def test_history_status_filter_is_case_sensitive():
    # Status values are stored uppercase; a lowercase filter is a typo
    # that would silently match nothing if it weren't validated.
    response = client.get("/history", params={"status": "pass"})
    assert response.status_code == 422


def test_market_type_list_in_tests_matches_the_real_literal():
    """
    Tripwire: ALL_MARKET_TYPES above is hardcoded on purpose (so
    accidentally REMOVING a market from main.MarketType makes the
    accepts-every-valid-market-type test fail), but a hardcoded list
    can't notice a market being ADDED. This test fails loudly in
    either direction, so adding a new market forces updating the list
    above -- and with it, coverage of that market's query filtering.
    """
    from typing import get_args

    import main

    assert set(ALL_MARKET_TYPES) == set(get_args(main.MarketType))


# ============================================================
# CONCURRENCY: check-then-write races must be 409, never 500
# ============================================================

def test_create_game_race_is_a_409_not_a_500(monkeypatch):
    """
    Two concurrent POST /games for the same id can both pass the
    endpoint's "does it exist?" pre-check; the PRIMARY KEY then rejects
    the loser. That used to surface as an unhandled IntegrityError (500).
    """
    import main

    def lost_the_race(*args, **kwargs):
        raise main.db.GameAlreadyExistsError("Game 'race' already exists.")

    monkeypatch.setattr(main.db, "create_game", lost_the_race)

    response = client.post(
        "/games", json={"game_id": "race", "game_date": "2026-01-15"}
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_integrity_error_during_a_write_is_a_409_not_a_500(monkeypatch):
    """
    E.g. the game is deleted between the endpoint's existence check and
    the prediction write: the foreign key rejects the orphan row, and
    that must come back as a retryable 409.
    """
    import sqlite3

    import main

    client.post("/games", json={"game_id": "ie-game", "game_date": "2026-01-15"})

    def foreign_key_failure(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(main.db, "upsert_prediction", foreign_key_failure)

    response = client.post(
        "/predictions",
        json={"game_id": "ie-game", "pf": 44.0, "american_odds": 400},
    )

    assert response.status_code == 409
    assert "concurrent" in response.json()["detail"].lower()


# ============================================================
# AUTH: a blank key must never let an empty header through
# ============================================================

def test_blank_api_key_never_authenticates_an_empty_header(monkeypatch):
    """
    Regression test for a reproduced bug: with the key configured as an
    empty string, auth looked enabled but an empty X-API-Key header
    compared equal to it and was let through. (Normal configuration
    now treats a blank key as unset; this covers the fail-closed
    behavior of the check itself if a blank value ever reaches it.)
    """
    import main

    monkeypatch.setattr(main, "API_KEY", "")

    with_empty_header = client.post(
        "/games",
        json={"game_id": "blank-1", "game_date": "2026-01-15"},
        headers={"X-API-Key": ""},
    )
    without_header = client.post(
        "/games", json={"game_id": "blank-2", "game_date": "2026-01-15"}
    )

    assert with_empty_header.status_code == 401
    assert without_header.status_code == 401
