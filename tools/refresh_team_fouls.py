# ============================================================
# REFRESH TEAM FOUL DATA FOR THE NBA GAME SPECIALS PAGE
# Paste this into a new Colab cell and run it. It prints one block of
# text; copy that block into the page's "Team data" box.
#
# It uses the same definitions as the PF projection cell:
#   last10  = average team fouls over the team's last 10 games
#             (the window runs across seasons, like prior_10_pf)
#   season  = average team fouls over the team's games so far within the
#             same "season" key, SUBSTR(game_id, 1, 4) (like season_to_date_pf).
#             NOTE: that key is the same for every regular season, so with these
#             files it does NOT reset each season; it averages every game since
#             2024-25 (about 164 games per team after two seasons).
# PF for a game = (A.last10 + B.last10 + A.season + B.season) / 2
# ============================================================
import json
import duckdb
import pandas as pd

BASE = "https://huggingface.co/datasets/cbratkovics/nba-game-logs/resolve/main/game_logs/"
SEASON_FILES = ["game_logs_2024-25.parquet", "game_logs_2025-26.parquet", "game_logs_2026-27.parquet"]


def load_rows():
    parts = []
    for name in SEASON_FILES:
        url = BASE + name
        try:
            df = duckdb.sql(
                f"SELECT game_id, game_date, team AS team_id, pf FROM read_parquet('{url}')"
            ).df()
            parts.append(df)
            print("loaded", name, len(df), "rows")
        except Exception as e:  # a season that has no file yet is fine
            print("skipped", name, "-", str(e)[:80])
    return pd.concat(parts, ignore_index=True)


def team_table(rows):
    rows = rows.copy()
    rows["game_date"] = pd.to_datetime(rows["game_date"])
    games = (
        rows.groupby(["game_id", "game_date", "team_id"], as_index=False)["pf"].sum()
        .rename(columns={"pf": "team_pf"})
        .sort_values(["team_id", "game_date", "game_id"])
    )
    games["season_key"] = games["game_id"].astype(str).str[:4]
    out = {}
    for team, g in games.groupby("team_id"):
        last = g.iloc[-1]
        this_season = g[g["season_key"] == last["season_key"]]
        out[team] = {
            "last10": round(float(g["team_pf"].tail(10).mean()), 3),
            "season": round(float(this_season["team_pf"].mean()), 3),
            "games_this_season": int(len(this_season)),
            "last_game": str(last["game_date"].date()),
        }
    as_of = str(games["game_date"].max().date())
    return {"as_of": as_of, "teams": out}


JS_HEADER = """// Team foul data for the Foul-out tab, written by tools/refresh_team_fouls.py
// (a scheduled GitHub workflow runs it daily). Do not edit by hand.
// Note: "season" is the notebook's season_to_date_pf, which does not reset between
// seasons (its season key is the first 4 characters of game_id, the same for every
// regular season), so with these files it averages every game since 2024-25.
// Data pasted into the page's "Team data" box overrides this for that browser.
window.TEAM_FOULS_DATA = """


def to_js(result):
    return JS_HEADER + json.dumps(result, indent=1) + ";\n"


if __name__ == "__main__":
    import sys

    result = team_table(load_rows())
    if len(sys.argv) == 3 and sys.argv[1] == "--out":
        if len(result["teams"]) != 30:
            sys.exit("Expected 30 teams, got %d. Not writing the file." % len(result["teams"]))
        with open(sys.argv[2], "w") as f:
            f.write(to_js(result))
        print("wrote", sys.argv[2], "as of", result["as_of"])
    else:
        print("\nCOPY EVERYTHING BELOW THIS LINE INTO THE PAGE\n")
        print(json.dumps(result))
