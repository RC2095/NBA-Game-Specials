"""
Tests for the opt-in NBA_FOUL_PROTECT_READS switch: when on, EVERY
endpoint except / and /health requires the API key -- including the
read endpoints (ledger history, performance/ROI/calibration reports,
model-info) and the pricing endpoints, which are otherwise open to
anyone with the URL.

The decision logic itself is also checked against the real function
source with stand-in request types; these tests cover the actual
FastAPI wiring (that the dependency is attached app-wide).
"""

import pytest
from fastapi.testclient import TestClient

import database
from main import app

client = TestClient(app)

KEY = "a-sufficiently-long-test-key"

READ_ENDPOINTS = [
    "/history",
    "/performance",
    "/roi-report",
    "/calibration-report",
    "/games",
    "/model-info",
    "/calibration",
]


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_read_protection.db")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield


@pytest.fixture
def reads_protected(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", KEY)
    monkeypatch.setattr(main, "PROTECT_READS", True)


@pytest.fixture
def reads_open(monkeypatch):
    # Explicit, so a developer who happens to have NBA_FOUL_PROTECT_READS
    # exported in their shell doesn't turn the "open by default" tests red.
    import main

    monkeypatch.setattr(main, "API_KEY", KEY)
    monkeypatch.setattr(main, "PROTECT_READS", False)


# ============================================================
# Off (the default): reads stay open, writes stay protected
# ============================================================

@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_reads_are_open_when_protection_is_off(reads_open, path):
    assert client.get(path).status_code == 200


def test_pricing_is_open_when_protection_is_off(reads_open):
    response = client.post("/price", json={"pf": 44.0, "american_odds": 400})
    assert response.status_code == 200


def test_writes_still_need_the_key_when_protection_is_off(reads_open):
    response = client.post(
        "/games", json={"game_id": "g-off", "game_date": "2026-01-15"}
    )
    assert response.status_code == 401


# ============================================================
# On: everything except / and /health needs the key
# ============================================================

@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_reads_reject_a_missing_key(reads_protected, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_reads_reject_a_wrong_key(reads_protected, path):
    response = client.get(path, headers={"X-API-Key": "wrong-key-entirely"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_reads_reject_an_empty_key_header(reads_protected, path):
    response = client.get(path, headers={"X-API-Key": ""})
    assert response.status_code == 401


@pytest.mark.parametrize("path", READ_ENDPOINTS)
def test_reads_accept_the_correct_key(reads_protected, path):
    response = client.get(path, headers={"X-API-Key": KEY})
    assert response.status_code == 200


def test_pricing_needs_the_key_when_reads_are_protected(reads_protected):
    payload = {"pf": 44.0, "american_odds": 400}

    assert client.post("/price", json=payload).status_code == 401
    assert (
        client.post("/price", json=payload, headers={"X-API-Key": KEY}).status_code
        == 200
    )


def test_writes_work_with_the_key_when_reads_are_protected(reads_protected):
    headers = {"X-API-Key": KEY}

    created = client.post(
        "/games",
        json={"game_id": "g-on", "game_date": "2026-01-15"},
        headers=headers,
    )
    fetched = client.get("/games/g-on", headers=headers)

    assert created.status_code == 201
    assert fetched.status_code == 200


def test_health_stays_open_when_reads_are_protected(reads_protected):
    # The platform's health probe can't carry the key.
    assert client.get("/health").status_code == 200


def test_root_stays_open_when_reads_are_protected(reads_protected):
    assert client.get("/").status_code == 200


# ============================================================
# Misconfiguration: protection on but no key
# ============================================================

def test_protected_reads_fail_closed_if_no_key_is_configured(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", None)
    monkeypatch.setattr(main, "PROTECT_READS", True)

    assert client.get("/history").status_code == 401
    assert client.get("/history", headers={"X-API-Key": ""}).status_code == 401


def test_startup_refuses_to_run_with_protection_on_and_no_key(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", None)
    monkeypatch.setattr(main, "PROTECT_READS", True)

    with pytest.raises(RuntimeError):
        main.on_startup()


def test_startup_succeeds_with_protection_on_and_a_key(reads_protected):
    import main

    assert main.on_startup() is None


def test_startup_succeeds_with_protection_off(monkeypatch):
    import main

    monkeypatch.setattr(main, "API_KEY", None)
    monkeypatch.setattr(main, "PROTECT_READS", False)

    assert main.on_startup() is None
