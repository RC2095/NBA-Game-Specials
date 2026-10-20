"""
Tests for database.init_db()'s additive schema migration.

Regression coverage for a real bug found by reproducing it: CREATE TABLE
IF NOT EXISTS alone does NOT add new columns to a table that already
exists, so a database file created by an earlier version of the schema
(before the `status` column was added to `games`) would crash with
"OperationalError: no such column: status" the first time that column
was used after upgrading the code. Nothing was deployed yet when this
was caught, but it would have hit the first real deployment on its
first schema change.
"""

import sqlite3

import pytest

import database


def _create_old_schema_games_table(path):
    """A games table as it looked BEFORE the status column existed."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE games (
            game_id TEXT PRIMARY KEY,
            game_date TEXT,
            timestamp TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO games (game_id, game_date, timestamp) "
        "VALUES ('old-game', '2026-01-01', 'old-ts')"
    )
    conn.commit()
    conn.close()


def _column_names(path, table):
    conn = sqlite3.connect(path)
    names = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    conn.close()
    return names


def test_init_db_adds_missing_status_column_to_old_games_table(tmp_path):
    path = str(tmp_path / "old.db")
    _create_old_schema_games_table(path)
    assert "status" not in _column_names(path, "games")  # precondition

    database.init_db(path)

    assert "status" in _column_names(path, "games")


def test_migration_backfills_existing_rows_with_default_status(tmp_path):
    path = str(tmp_path / "old.db")
    _create_old_schema_games_table(path)

    database.init_db(path)

    game = database.get_game("old-game", path)
    assert game["status"] == "scheduled"
    # existing data otherwise untouched
    assert game["game_date"] == "2026-01-01"
    assert game["timestamp"] == "old-ts"


def test_update_game_status_works_after_migration(tmp_path):
    """The exact call that crashed before the fix."""
    path = str(tmp_path / "old.db")
    _create_old_schema_games_table(path)
    database.init_db(path)

    updated = database.update_game_status("old-game", "postponed", path)

    assert updated["status"] == "postponed"


def test_status_filtering_works_after_migration(tmp_path):
    path = str(tmp_path / "old.db")
    _create_old_schema_games_table(path)
    database.init_db(path)
    database.update_game_status("old-game", "canceled", path)

    assert database.count_games(status="canceled", db_path=path) == 1
    assert database.count_games(status="scheduled", db_path=path) == 0


def test_predictions_table_created_alongside_migrated_games_table(tmp_path):
    path = str(tmp_path / "old.db")
    _create_old_schema_games_table(path)

    database.init_db(path)

    database.upsert_prediction(
        "old-game", "foul_out", 0.0,
        {"combined_probability": 0.2, "model_version": "1.0.0"},
        path,
    )
    assert database.get_prediction("old-game", "foul_out", 0.0, path) is not None


def test_init_db_is_noop_on_fresh_database(tmp_path):
    path = str(tmp_path / "fresh.db")
    database.init_db(path)

    database.create_game("fresh-game", "2026-01-01", path)

    game = database.get_game("fresh-game", path)
    assert game["status"] == "scheduled"


def test_init_db_is_idempotent(tmp_path):
    path = str(tmp_path / "idempotent.db")
    database.init_db(path)
    database.init_db(path)  # second call must not error
    database.init_db(path)

    database.create_game("g", "2026-01-01", path)
    assert database.get_game("g", path) is not None


def test_migration_preserves_existing_data_across_repeated_init(tmp_path):
    path = str(tmp_path / "old.db")
    _create_old_schema_games_table(path)

    database.init_db(path)
    database.update_game_status("old-game", "postponed", path)
    database.init_db(path)  # re-running must not reset the status back

    assert database.get_game("old-game", path)["status"] == "postponed"


def test_all_current_games_columns_exist_after_init(tmp_path):
    path = str(tmp_path / "fresh.db")
    database.init_db(path)
    assert _column_names(path, "games") >= {"game_id", "game_date", "status", "timestamp"}


def test_all_current_predictions_columns_exist_after_init(tmp_path):
    path = str(tmp_path / "fresh.db")
    database.init_db(path)
    expected = {
        "id", "game_id", "market_type", "threshold", "combined_probability",
        "combined_fair_odds", "market_odds", "market_break_even",
        "probability_edge", "expected_roi", "safety_margin",
        "required_probability", "status", "actual_result", "realized_roi",
        "details", "model_version", "timestamp",
    }
    assert _column_names(path, "predictions") >= expected
