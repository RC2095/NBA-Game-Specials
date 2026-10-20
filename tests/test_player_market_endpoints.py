import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


# ============================================================
# /player-markets/price  (threshold markets)
# ============================================================

def _reese_gray_howard_payload(threshold=25.0):
    return {
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


def test_price_player_threshold_market_basic():
    response = client.post(
        "/player-markets/price", json=_reese_gray_howard_payload()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["market_type"] == "points"
    assert body["threshold"] == 25.0
    assert len(body["players"]) == 2
    for player in body["players"]:
        assert 0.0 < player["probability"] < 1.0
        assert player["extrapolated"] is False
    assert 0.0 < body["combined_probability"] < 1.0


def test_price_player_threshold_combined_matches_manual_calc():
    response = client.post(
        "/player-markets/price", json=_reese_gray_howard_payload()
    )
    body = response.json()
    p1 = body["players"][0]["probability"]
    p2 = body["players"][1]["probability"]
    expected_combined = 1.0 - (1.0 - p1) * (1.0 - p2)
    assert body["combined_probability"] == pytest.approx(expected_combined, abs=1e-6)


def test_price_player_threshold_rejects_empty_players():
    payload = _reese_gray_howard_payload()
    payload["players"] = []
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 400


def test_price_player_threshold_rejects_player_with_no_lines():
    payload = _reese_gray_howard_payload()
    payload["players"][0]["lines"] = []
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 400


def test_price_player_threshold_rejects_zero_odds():
    payload = _reese_gray_howard_payload()
    payload["players"][0]["lines"][0]["over_odds"] = 0
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 422


def test_price_player_threshold_rejects_invalid_market_type():
    payload = _reese_gray_howard_payload()
    payload["market_type"] = "steals"
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 422


def test_price_player_threshold_rejects_nonpositive_threshold():
    payload = _reese_gray_howard_payload(threshold=0)
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 422


def test_price_player_threshold_extrapolation_flagged():
    # Threshold 50 is far past the widest line (24.5) in this fixture,
    # so it should be flagged as extrapolated (decay curve, not linear).
    payload = _reese_gray_howard_payload(threshold=50.0)
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 200
    body = response.json()
    for player in body["players"]:
        assert player["extrapolated"] is True
        assert player["probability"] > 0.0  # never clamps to exactly zero


def test_price_player_threshold_rejects_duplicate_player():
    payload = _reese_gray_howard_payload()
    payload["players"].append(payload["players"][0])  # duplicate Angel Reese
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 422


def test_price_player_threshold_rejects_case_insensitive_duplicate():
    payload = _reese_gray_howard_payload()
    duplicate = dict(payload["players"][0])
    duplicate["player_name"] = payload["players"][0]["player_name"].upper()
    payload["players"].append(duplicate)
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 422


def test_price_player_threshold_single_player():
    payload = _reese_gray_howard_payload()
    payload["players"] = payload["players"][:1]
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 200
    body = response.json()
    # With one player, combined probability equals that player's own.
    assert body["combined_probability"] == pytest.approx(
        body["players"][0]["probability"], abs=1e-6
    )


# ============================================================
# /player-markets/direct-price  (double-double / triple-double)
# ============================================================

def _dd_payload():
    return {
        "market_type": "double_double",
        "players": [
            {"player_name": "Nikola Jokic", "yes_odds": -700},
            {"player_name": "Domantas Sabonis", "yes_odds": -350, "no_odds": 260},
        ],
    }


def test_price_player_direct_market_basic():
    response = client.post("/player-markets/direct-price", json=_dd_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["market_type"] == "double_double"
    assert len(body["players"]) == 2
    assert body["players"][0]["devigged"] is False  # yes_odds only
    assert body["players"][1]["devigged"] is True  # both sides posted
    assert 0.0 < body["combined_probability"] < 1.0


def test_price_player_direct_market_rejects_empty_players():
    payload = _dd_payload()
    payload["players"] = []
    response = client.post("/player-markets/direct-price", json=payload)
    assert response.status_code == 400


def test_price_player_direct_market_rejects_zero_odds():
    payload = _dd_payload()
    payload["players"][0]["yes_odds"] = 0
    response = client.post("/player-markets/direct-price", json=payload)
    assert response.status_code == 422


def test_price_player_direct_market_rejects_invalid_market_type():
    payload = _dd_payload()
    payload["market_type"] = "quadruple_double"
    response = client.post("/player-markets/direct-price", json=payload)
    assert response.status_code == 422


def test_price_player_direct_market_triple_double():
    payload = _dd_payload()
    payload["market_type"] = "triple_double"
    response = client.post("/player-markets/direct-price", json=payload)
    assert response.status_code == 200
    assert response.json()["market_type"] == "triple_double"


def test_price_player_direct_market_rejects_duplicate_player():
    payload = _dd_payload()
    payload["players"].append(payload["players"][0])  # duplicate Nikola Jokic
    response = client.post("/player-markets/direct-price", json=payload)
    assert response.status_code == 422


def test_price_player_threshold_rejects_odds_below_100_in_magnitude():
    payload = _reese_gray_howard_payload()
    payload["players"][0]["lines"][0]["over_odds"] = 50
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 422


def test_price_player_direct_market_rejects_odds_below_100_in_magnitude():
    payload = _dd_payload()
    payload["players"][0]["yes_odds"] = -50
    response = client.post("/player-markets/direct-price", json=payload)
    assert response.status_code == 422


def test_price_player_threshold_rejects_single_far_line():
    """One line far from the threshold can't be priced -- it used to come
    back as that line's own probability (e.g. ~47% for "25+" from an
    Over 4.5 line) instead of an error."""
    payload = {
        "market_type": "points",
        "threshold": 25.0,
        "players": [
            {
                "player_name": "Jordan Walsh",
                "lines": [{"line": 4.5, "over_odds": 100, "under_odds": -132}],
            }
        ],
    }
    response = client.post("/player-markets/price", json=payload)
    assert response.status_code == 400
    assert "too far" in response.json()["detail"]
