"""
Persistence layer for the NBA pricing API's forward-validation ledger.

SCHEMA v2 -- split from the original single-table design now that the API
prices more than one market. A game can now have several predictions
attached to it (foul-out, points-30, double-double, ...), so "games" and
"predictions" are separate tables instead of one flat row per game.

    games:
        game_id (PK), game_date, status ('scheduled' | 'postponed' |
        'canceled', default 'scheduled'), timestamp

    predictions:
        one row per (game_id, market_type, threshold) combination.
        threshold is a REAL with a 0 sentinel for market types that
        don't have one (foul_out, double_double, triple_double) --
        SQLite treats every NULL in a UNIQUE constraint as distinct
        from every other NULL, which would silently break upsert
        behavior for those market types, so 0 is used instead (real
        thresholds are always > 0, so this is unambiguous).

Re-submitting a prediction for the same (game_id, market_type,
threshold) upserts in place via SQLite's ON CONFLICT clause -- same
"editable by re-submission" behavior as the original single-market
design.

Uses the Python standard library's sqlite3 module directly -- no new
dependency.
"""

import os
import pathlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.environ.get("NBA_FOUL_DB_PATH", "nba_foul_api.db")

# Columns on `predictions` that a caller can set when upserting. Kept as
# an explicit list (rather than trusting whatever dict keys show up) so
# a typo in a caller's fields dict fails loudly instead of silently
# writing nothing.
PREDICTION_FIELDS = [
    "combined_probability",
    "combined_fair_odds",
    "market_odds",
    "market_break_even",
    "probability_edge",
    "expected_roi",
    "safety_margin",
    "required_probability",
    "status",
    "details",
    "model_version",
]


class GameAlreadyExistsError(ValueError):
    """
    Raised by create_game when the game_id is already taken. The API
    endpoint pre-checks for this, but two concurrent requests can both
    pass the pre-check; the PRIMARY KEY constraint is the real guard, and
    this lets the API turn that race into a clean 409 instead of an
    unhandled IntegrityError (a 500).
    """


@contextmanager
def get_connection(db_path: Optional[str] = None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_def: str) -> None:
    """
    Adds `column` to `table` if it doesn't already exist, via
    ALTER TABLE ... ADD COLUMN. Additive-only -- never removes, renames,
    or changes an existing column -- so it's safe to call unconditionally
    on every startup.

    This is what keeps init_db() from crashing against a database file
    created by an EARLIER version of this schema (e.g. one created
    before the `status` column existed): CREATE TABLE IF NOT EXISTS
    alone does NOT retroactively add new columns to a table that
    already exists, confirmed by reproducing the resulting
    "OperationalError: no such column: status" against a pre-migration
    games table before this fix existed.

    Limitation: SQLite's ALTER TABLE ADD COLUMN can only add the column
    itself (with its own DEFAULT/NOT NULL) -- it cannot retroactively
    add table-level constraints like UNIQUE or FOREIGN KEY to an
    existing table. A future schema change that needs a new constraint,
    not just a new column, needs a real migration step, not this.
    """
    existing_columns = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")


def init_db(db_path: Optional[str] = None) -> None:
    """
    Creates both tables if they don't already exist, then ensures every
    column in the current schema exists even on a database file created
    by an older version of it (see _ensure_column). Idempotent and safe
    to call on every startup.

    Also creates the database file's parent directory if it doesn't
    exist. main.py calls this at import time, so a configured path like
    NBA_FOUL_DB_PATH=/data/ledger.db with no /data directory used to
    crash the whole app on boot with a cryptic "unable to open database
    file" (reproduced before this fix).
    """
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY,
                game_date TEXT,
                status TEXT NOT NULL DEFAULT 'scheduled',
                timestamp TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                market_type TEXT NOT NULL,
                threshold REAL NOT NULL DEFAULT 0,
                combined_probability REAL,
                combined_fair_odds INTEGER,
                market_odds INTEGER,
                market_break_even REAL,
                probability_edge REAL,
                expected_roi REAL,
                safety_margin REAL,
                required_probability REAL,
                status TEXT,
                actual_result INTEGER,
                realized_roi REAL,
                details TEXT,
                model_version TEXT,
                timestamp TEXT,
                UNIQUE(game_id, market_type, threshold),
                FOREIGN KEY(game_id) REFERENCES games(game_id)
            )
            """
        )

        # Column-by-column migration for every column in the current
        # schema. Harmless no-op for a freshly-created table (every
        # column already exists); backfills a pre-existing table
        # created by an earlier schema version. Add a line here
        # whenever a future column is added to either table.
        for column, column_def in [
            ("game_date", "TEXT"),
            ("status", "TEXT NOT NULL DEFAULT 'scheduled'"),
            ("timestamp", "TEXT"),
        ]:
            _ensure_column(conn, "games", column, column_def)

        for column, column_def in [
            ("game_id", "TEXT"),
            ("market_type", "TEXT"),
            ("threshold", "REAL NOT NULL DEFAULT 0"),
            ("combined_probability", "REAL"),
            ("combined_fair_odds", "INTEGER"),
            ("market_odds", "INTEGER"),
            ("market_break_even", "REAL"),
            ("probability_edge", "REAL"),
            ("expected_roi", "REAL"),
            ("safety_margin", "REAL"),
            ("required_probability", "REAL"),
            ("status", "TEXT"),
            ("actual_result", "INTEGER"),
            ("realized_roi", "REAL"),
            ("details", "TEXT"),
            ("model_version", "TEXT"),
            ("timestamp", "TEXT"),
        ]:
            _ensure_column(conn, "predictions", column, column_def)


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


# ============================================================
# HEALTH
# ============================================================

def check_health(db_path: Optional[str] = None) -> dict:
    """
    Returns {"ok": bool, "reason": Optional[str]} describing whether the
    ledger database is actually usable -- for the /health endpoint, so a
    platform health check can't report "healthy" for an app whose
    database file is missing, corrupt, unmigrated, or on a read-only
    filesystem (e.g. a persistent volume that failed to mount).

    Opens the file READ-ONLY on purpose. A normal sqlite3.connect() to a
    nonexistent path silently creates an empty database file, which
    would turn "the volume didn't mount" into a false "healthy" on a
    fresh empty database. Takes no write lock, so it can't contend with
    real traffic or make a busy-but-healthy app look unhealthy.
    """
    path = db_path or DB_PATH

    try:
        uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            conn.execute("SELECT 1 FROM games LIMIT 1").fetchone()
            conn.execute("SELECT 1 FROM predictions LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "reason": f"database not readable: {exc}"}

    directory = os.path.dirname(os.path.abspath(path))
    if not os.access(path, os.W_OK) or not os.access(directory, os.W_OK):
        return {
            "ok": False,
            "reason": "database file or its directory is not writable",
        }

    return {"ok": True, "reason": None}


# ============================================================
# GAMES
# ============================================================

def create_game(
    game_id: str,
    game_date: str,
    db_path: Optional[str] = None,
) -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()

    try:
        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO games (game_id, game_date, timestamp) VALUES (?, ?, ?)",
                (game_id, game_date, timestamp),
            )
    except sqlite3.IntegrityError as exc:
        raise GameAlreadyExistsError(f"Game '{game_id}' already exists.") from exc

    return get_game(game_id, db_path)


def get_game(game_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM games WHERE game_id = ?",
            (game_id,),
        ).fetchone()

    return _row_to_dict(row)


def update_game_date(
    game_id: str,
    game_date: str,
    db_path: Optional[str] = None,
) -> dict:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE games SET game_date = ? WHERE game_id = ?",
            (game_date, game_id),
        )

    return get_game(game_id, db_path)


def update_game_status(
    game_id: str,
    status: str,
    db_path: Optional[str] = None,
) -> dict:
    """
    status: 'scheduled' | 'postponed' | 'canceled' (validated at the API
    layer via a Pydantic Literal, not here -- this function just writes
    whatever string it's given).
    """
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE games SET status = ? WHERE game_id = ?",
            (status, game_id),
        )

    return get_game(game_id, db_path)


def delete_game(game_id: str, db_path: Optional[str] = None) -> bool:
    """
    Deletes a game and every prediction attached to it. Returns True if
    the game existed, False otherwise.
    """
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM predictions WHERE game_id = ?", (game_id,))
        cursor = conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
        deleted = cursor.rowcount > 0

    return deleted


def list_games(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[str] = None,
) -> list:
    query = "SELECT * FROM games"
    params: list = []

    if status is not None:
        query += " WHERE status = ?"
        params.append(status)

    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [_row_to_dict(row) for row in rows]


def count_games(status: Optional[str] = None, db_path: Optional[str] = None) -> int:
    query = "SELECT COUNT(*) AS c FROM games"
    params: list = []

    if status is not None:
        query += " WHERE status = ?"
        params.append(status)

    with get_connection(db_path) as conn:
        row = conn.execute(query, params).fetchone()

    return row["c"]


# ============================================================
# PREDICTIONS
# ============================================================

def upsert_prediction(
    game_id: str,
    market_type: str,
    threshold: float,
    fields: dict,
    db_path: Optional[str] = None,
) -> dict:
    """
    Insert a prediction for (game_id, market_type, threshold), or
    overwrite it in place if one already exists for that exact
    combination (the "re-submitting edits it" behavior).

    fields: any subset of PREDICTION_FIELDS. `details`, if present,
    should already be a JSON string (use json.dumps on the caller's
    side) since SQLite has no native structured type.
    """
    unknown = set(fields) - set(PREDICTION_FIELDS)
    if unknown:
        raise ValueError(f"Unknown prediction field(s): {sorted(unknown)}")

    timestamp = datetime.now(timezone.utc).isoformat()
    values = [fields.get(column) for column in PREDICTION_FIELDS]

    column_list = ", ".join(PREDICTION_FIELDS)
    placeholders = ", ".join(["?"] * len(PREDICTION_FIELDS))
    update_clause = ", ".join(
        f"{column} = excluded.{column}" for column in PREDICTION_FIELDS
    )

    with get_connection(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO predictions
                (game_id, market_type, threshold, {column_list}, timestamp)
            VALUES (?, ?, ?, {placeholders}, ?)
            ON CONFLICT(game_id, market_type, threshold) DO UPDATE SET
                {update_clause},
                timestamp = excluded.timestamp
            """,
            (game_id, market_type, threshold, *values, timestamp),
        )

    return get_prediction(game_id, market_type, threshold, db_path)


def get_prediction(
    game_id: str,
    market_type: str,
    threshold: float,
    db_path: Optional[str] = None,
) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM predictions
            WHERE game_id = ? AND market_type = ? AND threshold = ?
            """,
            (game_id, market_type, threshold),
        ).fetchone()

    return _row_to_dict(row)


def update_prediction_result(
    game_id: str,
    market_type: str,
    threshold: float,
    actual_result: int,
    realized_roi: Optional[float],
    db_path: Optional[str] = None,
) -> dict:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE predictions
            SET actual_result = ?, realized_roi = ?
            WHERE game_id = ? AND market_type = ? AND threshold = ?
            """,
            (actual_result, realized_roi, game_id, market_type, threshold),
        )

    return get_prediction(game_id, market_type, threshold, db_path)


def delete_prediction(
    game_id: str,
    market_type: str,
    threshold: float,
    db_path: Optional[str] = None,
) -> bool:
    """
    Removes a single prediction without touching the game or any of its
    other predictions. Returns True if a row was deleted, False if no
    prediction existed for that (game_id, market_type, threshold).
    """
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM predictions
            WHERE game_id = ? AND market_type = ? AND threshold = ?
            """,
            (game_id, market_type, threshold),
        )
        deleted = cursor.rowcount > 0

    return deleted


def list_predictions_for_game(
    game_id: str,
    db_path: Optional[str] = None,
) -> list:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM predictions WHERE game_id = ? ORDER BY timestamp",
            (game_id,),
        ).fetchall()

    return [_row_to_dict(row) for row in rows]


def list_predictions(
    market_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[str] = None,
) -> list:
    query = "SELECT * FROM predictions"
    clauses = []
    params: list = []

    if market_type is not None:
        clauses.append("market_type = ?")
        params.append(market_type)

    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [_row_to_dict(row) for row in rows]


def count_predictions(
    market_type: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    query = "SELECT COUNT(*) AS c FROM predictions"
    clauses = []
    params: list = []

    if market_type is not None:
        clauses.append("market_type = ?")
        params.append(market_type)

    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    with get_connection(db_path) as conn:
        row = conn.execute(query, params).fetchone()

    return row["c"]


# ============================================================
# REPORTING
# ============================================================

def performance_summary(
    market_type: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    where_clause = ""
    params: list = []
    if market_type is not None:
        where_clause = " WHERE market_type = ?"
        params.append(market_type)

    settled_extra = "actual_result IS NOT NULL"
    hits_extra = "actual_result = 1"
    prob_extra = "combined_probability IS NOT NULL"
    edge_extra = "probability_edge IS NOT NULL"
    status_extra = "status IS NOT NULL"

    def _joiner():
        return " AND " if where_clause else " WHERE "

    with get_connection(db_path) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM predictions{where_clause}", params
        ).fetchone()["c"]

        settled = conn.execute(
            f"SELECT COUNT(*) AS c FROM predictions{where_clause}{_joiner()}{settled_extra}",
            params,
        ).fetchone()["c"]

        hits = conn.execute(
            f"SELECT COUNT(*) AS c FROM predictions{where_clause}{_joiner()}{hits_extra}",
            params,
        ).fetchone()["c"]

        avg_probability = conn.execute(
            f"SELECT AVG(combined_probability) AS a FROM predictions{where_clause}{_joiner()}{prob_extra}",
            params,
        ).fetchone()["a"]

        avg_edge = conn.execute(
            f"SELECT AVG(probability_edge) AS a FROM predictions{where_clause}{_joiner()}{edge_extra}",
            params,
        ).fetchone()["a"]

        status_rows = conn.execute(
            f"""
            SELECT status, COUNT(*) AS c FROM predictions{where_clause}{_joiner()}{status_extra}
            GROUP BY status
            """,
            params,
        ).fetchall()

        market_type_rows = conn.execute(
            f"SELECT market_type, COUNT(*) AS c FROM predictions{where_clause} GROUP BY market_type",
            params,
        ).fetchall()

    hit_rate = (hits / settled) if settled else None

    return {
        "total_predictions": total,
        "settled_predictions": settled,
        "hits": hits,
        "hit_rate": round(hit_rate, 6) if hit_rate is not None else None,
        "average_combined_probability": (
            round(avg_probability, 6) if avg_probability is not None else None
        ),
        "average_probability_edge": (
            round(avg_edge, 6) if avg_edge is not None else None
        ),
        "status_counts": {row["status"]: row["c"] for row in status_rows},
        "market_type_counts": {
            row["market_type"]: row["c"] for row in market_type_rows
        },
    }


def calibration_report(
    market_type: Optional[str] = None,
    bucket_width: float = 0.1,
    db_path: Optional[str] = None,
) -> dict:
    """
    Buckets settled predictions by predicted probability (default 10%-
    wide buckets: 0-10%, 10-20%, ...) and compares the average predicted
    probability in each bucket against the actual hit rate observed.

    This bins by *predicted probability* rather than by a raw stat value
    like the original foul-only design did, specifically so it
    generalizes across every market type -- "predicted probability" is
    the one thing every market has in common, whereas "PF" only ever
    meant something for the foul market.
    """
    where_clause = " WHERE combined_probability IS NOT NULL AND actual_result IS NOT NULL"
    params: list = []
    if market_type is not None:
        where_clause += " AND market_type = ?"
        params.append(market_type)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT combined_probability, actual_result FROM predictions{where_clause}",
            params,
        ).fetchall()

    buckets: dict = {}
    num_buckets = int(round(1.0 / bucket_width))

    for row in rows:
        probability = row["combined_probability"]
        # Clamp 100% into the top bucket instead of spilling into a
        # bucket of its own.
        bucket_index = min(int(probability / bucket_width), num_buckets - 1)
        bucket_key = round(bucket_index * bucket_width, 6)

        bucket = buckets.setdefault(
            bucket_key,
            {"count": 0, "sum_predicted": 0.0, "sum_actual": 0},
        )
        bucket["count"] += 1
        bucket["sum_predicted"] += probability
        bucket["sum_actual"] += row["actual_result"]

    report = []
    for bucket_key in sorted(buckets):
        bucket = buckets[bucket_key]
        report.append(
            {
                "probability_bucket_start": bucket_key,
                "probability_bucket_end": round(bucket_key + bucket_width, 6),
                "count": bucket["count"],
                "average_predicted_probability": round(
                    bucket["sum_predicted"] / bucket["count"], 6
                ),
                "actual_hit_rate": round(
                    bucket["sum_actual"] / bucket["count"], 6
                ),
            }
        )

    return {"bucket_width": bucket_width, "buckets": report}


def roi_report(
    market_type: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    where_clause = " WHERE realized_roi IS NOT NULL"
    params: list = []
    if market_type is not None:
        where_clause += " AND market_type = ?"
        params.append(market_type)

    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT realized_roi, status FROM predictions{where_clause}",
            params,
        ).fetchall()

    def summarize(values):
        if not values:
            return {
                "count": 0,
                "total_roi": None,
                "average_roi": None,
                "win_rate": None,
            }

        wins = sum(1 for v in values if v > 0)

        return {
            "count": len(values),
            "total_roi": round(sum(values), 6),
            "average_roi": round(sum(values) / len(values), 6),
            "win_rate": round(wins / len(values), 6),
        }

    all_settled = [row["realized_roi"] for row in rows]
    pass_only = [row["realized_roi"] for row in rows if row["status"] == "PASS"]

    return {
        "all_settled": summarize(all_settled),
        "pass_status_only": summarize(pass_only),
    }
