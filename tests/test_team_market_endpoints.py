import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _payload(threshold_a=100.0, threshold_b=None):
    payload = {
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
    return payload


def test_price_both_teams_over_basic():
    response = client.post("/team-markets/price", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["team_a_name"] == "Lakers"
    assert body["team_b_name"] == "Celtics"
    assert 0.0 < body["joint_probability"] < 1.0
    assert -1.0 <= body["implied_correlation"] <= 1.0
    assert body["team_a_std"] > 0
    assert body["team_b_std"] > 0


def test_price_both_teams_over_matches_hand_traced_values():
    response = client.post("/team-markets/price", json=_payload())
    body = response.json()
    # Matches the value independently verified in team_markets.py's own
    # stress testing with this exact synthetic data.
    assert body["joint_probability_percent"] == pytest.approx(20.8667, abs=0.01)
    assert body["naive_independent_probability_percent"] == pytest.approx(13.5261, abs=0.01)
    assert body["implied_correlation"] == pytest.approx(0.4995, abs=0.001)


def test_price_both_teams_over_joint_exceeds_naive_when_positively_correlated():
    response = client.post("/team-markets/price", json=_payload())
    body = response.json()
    if body["implied_correlation"] > 0:
        assert body["joint_probability_percent"] > body["naive_independent_probability_percent"]


def test_price_both_teams_over_threshold_b_defaults_to_threshold_a():
    response = client.post("/team-markets/price", json=_payload(threshold_a=100.0))
    assert response.status_code == 200
    assert response.json()["threshold_b"] == 100.0


def test_price_both_teams_over_explicit_differing_thresholds():
    response = client.post(
        "/team-markets/price", json=_payload(threshold_a=100.0, threshold_b=95.0)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["threshold_a"] == 100.0
    assert body["threshold_b"] == 95.0


def test_price_both_teams_over_rejects_single_line_ladder():
    payload = _payload()
    payload["team_a_lines"] = [{"line": 99.5, "over_odds": -110, "under_odds": -110}]
    response = client.post("/team-markets/price", json=payload)
    assert response.status_code == 400


def test_price_both_teams_over_rejects_zero_odds():
    payload = _payload()
    payload["team_a_lines"][0]["over_odds"] = 0
    response = client.post("/team-markets/price", json=payload)
    assert response.status_code == 422


def test_price_both_teams_over_rejects_nonpositive_threshold():
    payload = _payload(threshold_a=0)
    response = client.post("/team-markets/price", json=payload)
    assert response.status_code == 422


def test_price_both_teams_over_rejects_inconsistent_ladder_data():
    payload = _payload()
    # Probabilities that don't decrease with the line -- the exact bad
    # data case that team_markets.fit_normal_from_ladder rejects.
    payload["team_a_lines"] = [
        {"line": 95.5, "over_odds": -300, "under_odds": 250},  # high prob at low line
        {"line": 105.5, "over_odds": -300, "under_odds": 250},  # same high prob at high line
    ]
    response = client.post("/team-markets/price", json=payload)
    # Either rejected as degenerate (identical z-scores) or inconsistent
    # (negative std) -- both are ValueError -> 400 from team_markets.py.
    assert response.status_code == 400


def test_price_both_teams_over_rejects_odds_below_100_in_magnitude():
    payload = _payload()
    payload["team_a_lines"][0]["under_odds"] = -50
    response = client.post("/team-markets/price", json=payload)
    assert response.status_code == 422
