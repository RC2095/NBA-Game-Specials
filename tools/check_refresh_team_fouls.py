import sys, json, random
import pandas as pd
sys.path.insert(0, ".")
sys.modules.setdefault("duckdb", type(sys)("duckdb"))   # not needed for team_table
import refresh_team_fouls as rc

random.seed(5)
rows = []
gid = 0
# two seasons with different 4-char id prefixes; team X plays 14 games in the new season, team Y only 3
def add(team, season_prefix, n, start):
    global gid
    for k in range(n):
        gid += 1
        d = pd.Timestamp(start) + pd.Timedelta(days=2 * k)
        # three player rows per team-game that sum to the team's fouls
        total = random.randint(14, 26)
        a = random.randint(0, total); b = random.randint(0, total - a)
        for v in (a, b, total - a - b):
            rows.append({"game_id": f"{season_prefix}{gid:06d}", "game_date": d, "team_id": team, "pf": v})
        add.log.setdefault((team, season_prefix), []).append(total)
add.log = {}
add("XXX", "0022", 40, "2025-01-01"); add("XXX", "0023", 14, "2025-11-01")
add("YYY", "0022", 40, "2025-01-01"); add("YYY", "0023", 3, "2025-11-01")
res = rc.team_table(pd.DataFrame(rows))

def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name, detail if not ok else "")
    if not ok: sys.exit(1)

X = add.log[("XXX", "0022")] + add.log[("XXX", "0023")]
Y = add.log[("YYY", "0022")] + add.log[("YYY", "0023")]
check("X last10 = mean of last 10 games", abs(res["teams"]["XXX"]["last10"] - sum(X[-10:]) / 10) < 1e-3)
check("X season = mean of this season's games only", abs(res["teams"]["XXX"]["season"] - sum(add.log[("XXX", "0023")]) / 14) < 1e-3)
check("Y last10 crosses into last season", abs(res["teams"]["YYY"]["last10"] - sum(Y[-10:]) / 10) < 1e-3)
check("Y season uses only its 3 new games", abs(res["teams"]["YYY"]["season"] - sum(add.log[("YYY", "0023")]) / 3) < 1e-3 and res["teams"]["YYY"]["games_this_season"] == 3)
check("as_of is the latest game date", res["as_of"] == str(max(r["game_date"] for r in rows).date()))

# cross-check against the notebook's own window definitions on the same data
g = pd.DataFrame(rows).groupby(["game_id", "game_date", "team_id"], as_index=False)["pf"].sum().rename(columns={"pf": "team_pf"})
g = g.sort_values(["team_id", "game_date", "game_id"])
g["season_key"] = g["game_id"].str[:4]
t = g[g.team_id == "XXX"].reset_index(drop=True)
# the notebook's prior_10_pf / season_to_date_pf for a hypothetical NEXT game = last 10 / season so far
prior10 = t["team_pf"].iloc[-10:].mean()
std = t[t.season_key == t.season_key.iloc[-1]]["team_pf"].mean()
check("matches notebook definitions for the next game", abs(res["teams"]["XXX"]["last10"] - prior10) < 1e-3 and abs(res["teams"]["XXX"]["season"] - std) < 1e-3)
print(json.dumps(res)[:200])

# ---- the file the scheduled job writes must pass the page's own checker
import subprocess, tempfile, os, datetime
teams = {c: {"last10": 19.0 + i * 0.1, "season": 19.5, "games_this_season": 164, "last_game": "2026-04-12"}
         for i, c in enumerate("ATL BKN BOS CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAN TOR UTA WAS".split())}
good = {"as_of": "2026-04-12", "teams": teams}
here = os.path.dirname(os.path.abspath(__file__))
def run_check(payload):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(rc.to_js(payload)); name = f.name
    try:
        return subprocess.run(["node", os.path.join(here, "check_team_fouls_file.js"), name], capture_output=True, text=True).returncode
    finally:
        os.unlink(name)
check("checker accepts a full 30-team file", run_check(good) == 0)
short = {"as_of": "2026-04-12", "teams": dict(list(teams.items())[:29])}
check("checker rejects a file with 29 teams", run_check(short) == 1)
bad = json.loads(json.dumps(good)); bad["teams"]["BOS"]["last10"] = 190.0
check("checker rejects an absurd average", run_check(bad) == 1)
noseason = json.loads(json.dumps(good)); noseason["teams"]["BOS"]["season"] = None
check("checker rejects a missing season average", run_check(noseason) == 1)
check("the shipped team_fouls.js passes the checker", subprocess.run(["node", os.path.join(here, "check_team_fouls_file.js")], capture_output=True).returncode == 0)
