"""
Tests for database.py behavior around failure modes: duplicate creation
races, a missing database directory, foreign-key enforcement, and the
health check. Stdlib-only, so these run without FastAPI.
"""

import sqlite3
from unittest import mock

import pytest

import database


# ============================================================
# DUPLICATE game_id (the check-then-insert race)
# ============================================================

def test_create_game_twice_raises_game_already_exists_error(tmp_path):
    """
    The API pre-checks for an existing game, but two concurrent requests
    can both pass that check. Before this fix the second insert raised a
    raw sqlite3.IntegrityError, which surfaced as an unhandled 500.
    """
    path = str(tmp_path / "ledger.db")
    database.init_db(path)
    database.create_game("g1", "2026-01-01", path)

    with pytest.raises(database.GameAlreadyExistsError):
        database.create_game("g1", "2026-01-02", path)


def test_game_already_exists_error_is_a_value_error():
    # callers that already catch ValueError for bad input keep working
    assert issubclass(database.GameAlreadyExistsError, ValueError)


def test_failed_duplicate_create_leaves_the_original_untouched(tmp_path):
    path = str(tmp_path / "ledger.db")
    database.init_db(path)
    database.create_game("g1", "2026-01-01", path)

    with pytest.raises(database.GameAlreadyExistsError):
        database.create_game("g1", "SOMETHING-ELSE", path)

    assert database.get_game("g1", path)["game_date"] == "2026-01-01"
    assert database.count_games(db_path=path) == 1


def test_error_message_names_the_game(tmp_path):
    path = str(tmp_path / "ledger.db")
    database.init_db(path)
    database.create_game("my-game", "2026-01-01", path)
    try:
        database.create_game("my-game", "2026-01-01", path)
        raise AssertionError("expected GameAlreadyExistsError")
    except database.GameAlreadyExistsError as exc:
        assert "my-game" in str(exc)


# ============================================================
# FOREIGN KEY enforcement
# ============================================================

def test_prediction_for_a_nonexistent_game_is_rejected_by_the_database(tmp_path):
    """
    The API checks that a game exists first, but the game can be deleted
    between that check and the write. Foreign-key enforcement is what
    stops an orphaned prediction row from being created in that window.
    (main.py turns this IntegrityError into a 409.)
    """
    path = str(tmp_path / "ledger.db")
    database.init_db(path)

    with pytest.raises(sqlite3.IntegrityError):
        database.upsert_prediction(
            "no-such-game", "foul_out", 0.0,
            {"combined_probability": 0.2, "model_version": "1.0.0"},
            path,
        )

    assert database.list_predictions(db_path=path) == []


# ============================================================
# MISSING DATABASE DIRECTORY
# ============================================================

def test_init_db_creates_a_missing_parent_directory(tmp_path):
    """
    main.py calls init_db() at import time, so a configured path whose
    directory didn't exist used to crash the entire app on boot with
    "unable to open database file".
    """
    path = tmp_path / "does" / "not" / "exist" / "ledger.db"
    assert not path.parent.exists()

    database.init_db(str(path))

    assert path.exists()
    database.create_game("g1", "2026-01-01", str(path))
    assert database.get_game("g1", str(path)) is not None


def test_init_db_with_existing_directory_still_works(tmp_path):
    path = str(tmp_path / "ledger.db")
    database.init_db(path)
    database.init_db(path)
    assert database.count_games(db_path=path) == 0


# ============================================================
# HEALTH CHECK
# ============================================================

def test_health_is_ok_for_a_working_database(tmp_path):
    path = str(tmp_path / "ledger.db")
    database.init_db(path)

    assert database.check_health(path) == {"ok": True, "reason": None}


def test_health_does_not_create_a_missing_database_file(tmp_path):
    """
    The subtle one: a plain sqlite3.connect() to a nonexistent path
    silently creates an empty database. A health check that did that
    would turn "the persistent volume didn't mount" into a false
    "healthy" on a brand-new empty ledger -- and would have quietly
    created the empty file as a side effect.
    """
    path = tmp_path / "missing.db"

    result = database.check_health(str(path))

    assert result["ok"] is False
    assert "not readable" in result["reason"]
    assert not path.exists()


def test_health_fails_for_a_database_without_the_expected_tables(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(str(path)).close()  # valid sqlite file, but no schema

    result = database.check_health(str(path))

    assert result["ok"] is False
    assert "not readable" in result["reason"]


def test_health_fails_for_a_corrupt_file(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is definitely not a sqlite database" * 100)

    assert database.check_health(str(path))["ok"] is False


def test_health_fails_when_the_database_is_not_writable(tmp_path):
    path = str(tmp_path / "ledger.db")
    database.init_db(path)

    # Simulates a read-only filesystem / volume. (A chmod-based test would
    # be meaningless when the tests run as root, which can write anywhere.)
    with mock.patch("os.access", return_value=False):
        result = database.check_health(path)

    assert result["ok"] is False
    assert "not writable" in result["reason"]


def test_health_check_takes_no_write_lock(tmp_path):
    """
    A health probe must not contend with real traffic: with another
    connection holding a write lock, the check must still succeed
    instead of timing out and making a busy-but-healthy app look dead.
    """
    path = str(tmp_path / "ledger.db")
    database.init_db(path)

    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")  # holds the write lock
    try:
        result = database.check_health(path)
    finally:
        writer.rollback()
        writer.close()

    assert result["ok"] is True


def test_health_check_does_not_modify_the_database(tmp_path):
    path = str(tmp_path / "ledger.db")
    database.init_db(path)
    database.create_game("g1", "2026-01-01", path)

    database.check_health(path)

    assert database.count_games(db_path=path) == 1


def test_health_check_handles_paths_with_special_characters(tmp_path):
    folder = tmp_path / "dir with spaces & symbols #1"
    folder.mkdir()
    path = str(folder / "ledger.db")
    database.init_db(path)

    assert database.check_health(path)["ok"] is True
