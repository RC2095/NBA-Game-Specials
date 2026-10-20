import pytest
from fastapi.testclient import TestClient

from main import (
    app,
    CALIBRATION_TABLE,
    PF_POINTS,
    interpolate_probability,
    market_break_even_probability,
    probability_to_fair_american,
)

client = TestClient(app)


# ============================================================
# BASIC ENDPOINTS
# ============================================================

def test_root():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "online"
    assert body["version"] == "1.0.0"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_model_info():
    response = client.get("/model-info")
    assert response.status_code == 200
    body = response.json()
    assert body["historical_games"] == 2453
    assert len(body["calibration_table"]) == len(CALIBRATION_TABLE)


def test_calibration_matches_table():
    response = client.get("/calibration")
    assert response.status_code == 200
    points = response.json()["points"]
    assert len(points) == len(CALIBRATION_TABLE)
    for point, (pf, prob) in zip(points, CALIBRATION_TABLE):
        assert point["pf"] == pf
        assert point["calibrated_probability"] == pytest.approx(prob)


# ============================================================
# INTERPOLATION / PREDICT
# ============================================================

def test_predict_exact_table_value():
    response = client.post("/predict", json={"pf": 44.0})
    assert response.status_code == 200
    body = response.json()
    assert body["calibrated_probability"] == pytest.approx(0.228)
    assert body["extrapolated"] is False


def test_predict_interpolated_midpoint():
    # Halfway between PF 44 (22.80%) and PF 45 (24.79%)
    response = client.post("/predict", json={"pf": 44.5})
    assert response.status_code == 200
    body = response.json()
    expected = (0.2280 + 0.2479) / 2
    assert body["calibrated_probability"] == pytest.approx(expected, abs=1e-6)
    assert body["extrapolated"] is False


def test_predict_extrapolated_below_range():
    response = client.post("/predict", json={"pf": 25.0})
    assert response.status_code == 200
    assert response.json()["extrapolated"] is True


def test_predict_extrapolated_above_range():
    response = client.post("/predict", json={"pf": 60.0})
    assert response.status_code == 200
    assert response.json()["extrapolated"] is True


def test_interpolate_probability_boundaries_directly():
    # Below range clamps/extrapolates from the first segment
    prob, extrapolated = interpolate_probability(PF_POINTS[0] - 1)
    assert extrapolated is True
    assert 0.0 <= prob <= 1.0

    # Above range extrapolates from the last segment
    prob, extrapolated = interpolate_probability(PF_POINTS[-1] + 1)
    assert extrapolated is True
    assert 0.0 <= prob <= 1.0

    # Exact match on the first point
    prob, extrapolated = interpolate_probability(PF_POINTS[0])
    assert extrapolated is False
    assert prob == pytest.approx(CALIBRATION_TABLE[0][1])


# ============================================================
# ODDS MATH HELPERS
# ============================================================

@pytest.mark.parametrize(
    "odds,expected",
    [
        (400, 0.20),
        (500, 1 / 6),
        (-200, 2 / 3),
        (100, 0.5),
    ],
)
def test_market_break_even_probability(odds, expected):
    assert market_break_even_probability(odds) == pytest.approx(expected, abs=1e-4)


def test_probability_to_fair_american_underdog():
    # 20% probability -> +400
    assert probability_to_fair_american(0.20) == 400


def test_probability_to_fair_american_favorite():
    # 66.67% probability -> negative odds
    odds = probability_to_fair_american(2 / 3)
    assert odds is not None
    assert odds < 0


def test_probability_to_fair_american_edge_cases():
    assert probability_to_fair_american(0.0) is None
    assert probability_to_fair_american(1.0) is None


# ============================================================
# /price ENDPOINT
# ============================================================

def test_price_pass_status():
    # PF 44 (22.8%) vs +400 (20% break-even) with default 2pp margin -> PASS
    response = client.post(
        "/price",
        json={"pf": 44.0, "american_odds": 400},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PASS"
    assert body["probability_edge_pp"] == pytest.approx(2.8, abs=1e-2)


def test_price_no_edge_status():
    # PF 39 (14.57%) vs a heavily favored market -> negative edge
    response = client.post(
        "/price",
        json={"pf": 39.0, "american_odds": -300},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "NO_EDGE"


def test_price_borderline_status():
    # Edge exists but is smaller than the safety margin requires
    response = client.post(
        "/price",
        json={"pf": 44.0, "american_odds": 430, "safety_margin_pp": 5.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAIR_OR_BORDERLINE"


def test_price_rejects_zero_odds():
    response = client.post(
        "/price",
        json={"pf": 44.0, "american_odds": 0},
    )
    assert response.status_code == 422


def test_price_custom_safety_margin():
    response = client.post(
        "/price",
        json={"pf": 44.0, "american_odds": 400, "safety_margin_pp": 10.0},
    )
    assert response.status_code == 200
    assert response.json()["safety_margin_pp"] == 10.0


# ============================================================
# /batch-price ENDPOINT
# ============================================================

def test_batch_price_normal():
    response = client.post(
        "/batch-price",
        json={
            "games": [
                {"pf": 42.0, "american_odds": 450},
                {"pf": 47.0, "american_odds": -110},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["results"]) == 2


def test_batch_price_rejects_empty_list():
    response = client.post("/batch-price", json={"games": []})
    assert response.status_code == 400


def test_batch_price_rejects_oversized_batch():
    games = [{"pf": 44.0, "american_odds": 400} for _ in range(1001)]
    response = client.post("/batch-price", json={"games": games})
    assert response.status_code == 400


# ============================================================
# /health reflects the real state of the database
# ============================================================

def test_health_includes_database_status():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == {"ok": True, "reason": None}


def test_health_returns_503_when_the_database_is_unhealthy(monkeypatch):
    """
    A platform health check must be able to see a broken database --
    previously /health returned a hardcoded "healthy" no matter what.
    """
    import database

    monkeypatch.setattr(
        database,
        "check_health",
        lambda *args, **kwargs: {"ok": False, "reason": "volume not mounted"},
    )

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["database"]["reason"] == "volume not mounted"


# ============================================================
# American odds must have magnitude >= 100
# ============================================================

@pytest.mark.parametrize("bad_odds", [1, 5, 50, 99, -1, -50, -99])
def test_price_rejects_american_odds_below_100_in_magnitude(bad_odds):
    """
    +5 or -50 aren't real prices -- they're almost always typos, and used
    to be priced silently as if real (+5 read as a 95% implied
    probability).
    """
    response = client.post("/price", json={"pf": 44.0, "american_odds": bad_odds})
    assert response.status_code == 422


@pytest.mark.parametrize("good_odds", [100, -100, 101, -101, 400, -110])
def test_price_accepts_odds_at_and_beyond_the_boundary(good_odds):
    response = client.post("/price", json={"pf": 44.0, "american_odds": good_odds})
    assert response.status_code == 200
