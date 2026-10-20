# NBA 6-Foul Pricing API

[![CI](https://github.com/your-org/nba-foul-api/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/nba-foul-api/actions/workflows/ci.yml)
<!-- Replace "your-org/nba-foul-api" above with your actual GitHub path once pushed. -->

A FastAPI service that prices the market for the event **"at least one player
reaches 6 personal fouls in an NBA game."** It converts a projected total
personal-foul count (PF) into a calibrated probability, then compares that
probability against market odds to flag betting edges.

## How it works

1. A frozen calibration table maps PF values (30–50) to calibrated
   probabilities.
2. For a given PF, the API linearly interpolates between table points (or
   extrapolates, with a flag, outside the 30–50 range).
3. Given market American odds, it computes the market's break-even
   probability, the edge (calibrated probability − break-even probability),
   expected ROI, and a minimum acceptable price after applying a safety
   margin.

## Browser version (`site/`)

A static page that runs the same pricing math in the browser. No server, no
database, no API key: nothing you paste is sent anywhere (only the Foul-out tab's team data box is remembered, in your own browser), and the
pricing logic is visible to anyone who opens the page.

- **Any player to reach** (points, rebounds, assists): paste each player's
  Over/Under lines, a milestone list ("To Score 25+ Points" prices), or
  several alt lines. Thresholds are chips (points 25/30/35/40/45/50,
  rebounds 12/14/16/18/20, assists 10/12/14/16/18) plus a free "Other" box.
  Output per threshold: probability, **fair odds**, **odds with margin**, and
  edge/ROI if you type the book's price.
- **Team totals** ("Home team 100+ through 3 quarters", "Both teams ..."):
  paste each team's lines (and the game total's) for the same window. A team
  with several lines is fitted; a team with a single line needs an assumed
  spread you type in. The link between the teams is worked out from the game
  total, or typed in. "100+" means 100 or more, measured from 99.5.
- **Double / triple double**: each player's Yes (and optionally No) price.
- **Foul-out**: projected PF and the book's price, same calibrated table as
  the API (PF 30 to 50). Pick two teams and the PF is filled in as
  `(A.last10 + B.last10 + A.season + B.season) / 2`, the notebook's own
  `blended_projection`, where last10 is a team's average fouls over its last 10
  games and season is the notebook's season-to-date average. That average does
  not reset each season (the notebook's season key, the first four characters of
  game_id, is the same for every regular season), so it covers every game since
  2024-25. The page matches the notebook, because the calibration was fitted on it. The team numbers go stale after
  every game: run `tools/refresh_team_fouls.py` as a notebook cell and paste what
  it prints into the "Team data" box (or into `site/team_fouls.js` to keep it for
  every visit). `tools/check_refresh_team_fouls.py` tests that cell's logic on
  synthetic data, and the cell has been run once on the real game logs. `site/team_fouls.js` ships with
  data through 2026-04-12 (the 2025-26 regular season); the page warns when the data is more than
  3 days old. A team with no season average falls back to its last 10 for that half, with a
  warning, because the model was never built for that case (it should not happen
  with this data).
- **Margin box** (top right): added to the fair probability to get the price
  to quote, either as a % of the fair probability (default 3) or in
  percentage points.

Paste format for Over/Under lines (several O/U pairs under one name add
lines; `Name, line, over, under` on one row also works):

```
Jayson Tatum
O 26.5-114
U 26.5-114
```

Anything the page can't read is listed, never guessed, and players left out
are called out because they lower the any-player number.

**Base-line mode (points, rebounds and assists)** estimates milestones from one
Over/Under line per player, using a lognormal model with separate constants per
stat (the points constants do not transfer to rebounds). Points: fitted on one
game's "To Score X+ Points" prices and checked without refitting on a second
(median model/book 0.96, 26 of 29 within 20%); only a few players were
available at 45+ and 50+, and nothing above 50+ has a book price. Rebounds:
fitted on one game, checked on a second (median 1.02, 10 of 15 within 20%);
any-player totals for the players the book lists came within about 10%, but
single players were off by up to 30%, the book lists only players above about
3% so the rest of the roster is model-only, and 18+/20+ have no book price.
Assists: fitted on one game (7 prices), checked on a second (median 1.07, 8 of
9 within 20%); a negative binomial did worse out of sample and plain Poisson
ran far too low in the tails. The book posts nothing above 14+, so 16+/18+
are unchecked, and the samples are small. Other stats are refused. A single line
far from the threshold is refused rather than guessed.

**Head Coach Ejection tab.** Head coach ejected at one fixed price for every game
(+10000, editable, shown as entered with no margin added). Any player
ejected, double overtime and the half and quarter team-total markets are not
built yet.

Files: `site/index.html`, `site/app.js` (page), `site/engine.js` (all the math
and parsing, usable from Node too).

### Checking the JavaScript against the Python

`site/engine.js` is a port; the Python modules are the reference.

```bash
python tests/gen_js_vectors.py   # recompute expected values from the Python
node site/test_engine.js         # replay them against engine.js (~11,700 checks)
```

`tests/test_js_vectors_fresh.py` fails if `site/test_vectors.json` is out of
date with the Python, so changing the Python math without re-running the above
cannot slip through.

### Deploying the site

- **Netlify:** connect the repo (or drag the `site/` folder onto Netlify
  Drop). `netlify.toml` already publishes `site/`; no build command.
- **GitHub Pages:** repo Settings > Pages > Source: "GitHub Actions". The
  `pages.yml` workflow runs the JavaScript tests and then publishes `site/` on
  every push to `main`.

**Daily team-data refresh.** `.github/workflows/refresh-team-fouls.yml` runs
every morning, rebuilds `site/team_fouls.js` from the game logs, checks it with
`tools/check_team_fouls_file.js`, and commits it if it changed; Netlify then
redeploys from the push, and for GitHub Pages the job starts `pages.yml` itself
(a push made with the built-in token doesn't trigger other workflows). One-time
setup: repo Settings > Actions > General > Workflow permissions > "Read and
write permissions". Run it by hand any time from the Actions tab ("Run
workflow"). This has not been run yet, so watch the first run's log. It stops
helping if the game-log dataset stops updating, and GitHub switches scheduled
workflows off after 60 days with no repository activity.

The Python API is not needed for the site. Deploy it only if you want the
ledger and report endpoints.

## Requirements

- Python 3.11+
- See `requirements.txt`

## Running locally

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

The API will be available at `http://localhost:8000`, with interactive docs
at `http://localhost:8000/docs`.

## Running with Docker

```bash
docker build -t nba-foul-api .

# Without a mounted volume, the ledger database lives inside the
# container and is lost when the container is removed.
docker run -p 8000:8000 nba-foul-api

# For persistent data, mount a host directory to /app/data (the
# Dockerfile already points NBA_FOUL_DB_PATH there):
mkdir -p data
docker run -p 8000:8000 -v $(pwd)/data:/app/data nba-foul-api
```

## Endpoints

| Method | Path                   | Auth required* | Description                                          |
|--------|------------------------|:--------------:|-------------------------------------------------------|
| GET    | `/`                    |                | Basic service info                                   |
| GET    | `/health`              |                | Health check — `503` if the ledger database isn't usable; exempt from rate limiting |
| GET    | `/model-info`          |                | Model metadata, historical performance, calibration  |
| GET    | `/calibration`         |                | Raw PF → probability calibration table (foul-out market) |
| POST   | `/predict`             |                | PF → calibrated probability + fair odds (foul-out market) |
| POST   | `/price`               |                | Full foul-out pricing: edge, ROI, minimum acceptable odds |
| POST   | `/batch-price`         |                | `/price` for up to 1000 games at once                |
| POST   | `/player-markets/price` |               | Price an "any player to hit X" threshold market (points/rebounds/assists) — stateless |
| POST   | `/player-markets/direct-price` |        | Price an "any player" double-double/triple-double market — stateless |
| POST   | `/team-markets/price`  |                | Price "both teams exceed X" using the correlation-implied model — stateless |
| POST   | `/games`               | ✓              | Register a game in the ledger                        |
| GET    | `/games`               |                | Paginated list of registered games                    |
| GET    | `/games/{game_id}`     |                | A game plus every prediction attached to it (any market) |
| PATCH  | `/games/{game_id}`     | ✓              | Correct a game's date                                |
| PATCH  | `/games/{game_id}/status` | ✓           | Mark a game `scheduled` / `postponed` / `canceled`    |
| DELETE | `/games/{game_id}`     | ✓              | Remove a game and all its predictions                |
| DELETE | `/games/{game_id}/predictions/{market_type}` | ✓ | Remove one prediction (pass `?threshold=` where applicable) without touching the rest of the game |
| POST   | `/predictions`         | ✓              | Attach/overwrite a **foul-out** prediction for a game |
| POST   | `/predictions/batch`   | ✓              | `/predictions` for many games in one call (per-item success/failure) |
| POST   | `/player-predictions/threshold` | ✓     | Attach/overwrite a **points/rebounds/assists** prediction for a game |
| POST   | `/player-predictions/threshold/batch` | ✓ | `/player-predictions/threshold` for many games/players at once |
| POST   | `/player-predictions/direct`    | ✓     | Attach/overwrite a **double-double/triple-double** prediction for a game |
| POST   | `/player-predictions/direct/batch` | ✓  | `/player-predictions/direct` for many games at once |
| POST   | `/team-predictions/both-over`   | ✓     | Attach/overwrite a **both-teams-over** prediction for a game (requires threshold_a == threshold_b) |
| POST   | `/team-predictions/both-over/batch` | ✓ | `/team-predictions/both-over` for many games at once |
| POST   | `/results`             | ✓              | Record the actual outcome for one (game, market_type, threshold) prediction |
| GET    | `/history`             |                | Paginated list of predictions, filterable by `market_type` and `status` |
| GET    | `/performance`         |                | Aggregate hit rate, average edge, status/market-type breakdown, optionally filtered by `market_type` |
| GET    | `/calibration-report`  |                | Predicted vs. actual hit rate, bucketed by predicted probability, optionally filtered by `market_type` |
| GET    | `/roi-report`          |                | Realized ROI across settled predictions, optionally filtered by `market_type` |

\* Only enforced if `NBA_FOUL_API_KEY` is set — see **Authentication** below. With `NBA_FOUL_PROTECT_READS=1`, every endpoint except `/` and `/health` requires it.

### Example: `/predict`

Request:
```json
{ "pf": 44.0 }
```

Response:
```json
{
  "model_version": "1.0.0",
  "pf": 44.0,
  "calibrated_probability": 0.228,
  "calibrated_probability_percent": 22.8,
  "fair_american_odds": 339,
  "extrapolated": false
}
```

### Example: `/price`

Request:
```json
{
  "pf": 44.0,
  "american_odds": 400,
  "safety_margin_pp": 2.0
}
```

Response includes the calibrated probability, the market's implied
break-even probability, the edge in percentage points, expected ROI, and a
`status` of `NO_EDGE`, `FAIR_OR_BORDERLINE`, or `PASS`.

### Example: `/batch-price`

Request:
```json
{
  "games": [
    { "pf": 42.0, "american_odds": 450 },
    { "pf": 47.0, "american_odds": -110 }
  ]
}
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

What each test file covers:

| File | Covers |
|------|--------|
| `test_main.py` | Foul-out pricing endpoints: `/price`, `/predict`, `/batch-price`, calibration, odds-conversion helpers |
| `test_foul_pricing.py` | The foul-out math (`foul_pricing.py`): calibration interpolation, odds conversions, edge/ROI/status, and the odds-with-margin price |
| `test_player_markets.py` | Pure math for player props: de-vigging, ladder interpolation, decay-curve extrapolation, refusing a single far-off line, any-player combination |
| `test_team_markets.py` | Pure math for the both-teams-over market: normal/bivariate-normal CDFs checked against exact closed-form identities, Fréchet bounds, symmetry, and regression tests for bugs found by stress testing |
| `test_player_market_endpoints.py`, `test_team_market_endpoints.py` | Stateless pricing endpoints |
| `test_ledger.py` | Games, foul-out predictions, results, history/performance/ROI/calibration reports, game status, single-prediction delete, query-parameter validation, auth |
| `test_player_predictions.py`, `test_team_predictions.py` | Persisting player/team predictions to the ledger |
| `test_batch_endpoints.py` | All four `/batch` endpoints, including per-item failure isolation |
| `test_middleware.py` | Rate limiter (against an isolated app) and CORS default-deny |
| `test_database_migration.py` | `init_db()` upgrading a database file created by an older schema |
| `test_read_protection.py` | The opt-in `NBA_FOUL_PROTECT_READS` switch: every endpoint except `/` and `/health` needs the key when on, fail-closed and startup refusal when misconfigured |
| `test_deployment_consistency.py` | Static checks that the Dockerfile `COPY` line and `requirements.txt` cover everything the code imports |
| `test_single_line_model.py` | The one-base-line points, rebounds and assists models, pinned against two games of real sportsbook prices each (the second never used for fitting), plus validation and monotonicity |
| `test_team_reach.py` | The "X or more" team wrappers: the half-point convention, team reach probability, mean from a single line, and both-teams-reach |
| `test_js_vectors_fresh.py` | `site/test_vectors.json` still matches what the Python reference computes, so the JavaScript check can't pass against stale numbers |

Run it from the project root. `pytest.ini` sets `pythonpath = .` so tests
can import the app's modules; without it, plain `pytest` fails to collect
every test file with `ModuleNotFoundError` (only `python -m pytest`
would work), and a tripwire test checks that the file is still there.

Two pieces of test infrastructure worth knowing about:

- **`tests/conftest.py` sets a very high rate limit before `main.py` is
  imported.** The rate limiter's counter lives on a middleware instance
  created at import time and shared by every test via the single `app`
  object, so with the default 60 requests/minute the suite would trip its
  own limiter and fail with unrelated 429s. The limiter itself is still
  tested for real in `test_middleware.py`, against its own small app.
- **`test_deployment_consistency.py` exists because the same bug happened
  three times:** a new module imported by `main.py` but never added to the
  Dockerfile's `COPY` line, which builds fine and then crashes on startup.
  It walks the import graph from `main.py` and fails CI if anything the
  app needs isn't copied (or isn't in `requirements.txt`).

## Operations

All of the following are opt-in via environment variables, so the API works
with zero configuration locally and can be locked down for deployment.

### Authentication

Unset by default (all endpoints open). To require an API key on ledger
*write* endpoints (`POST`/`PATCH`/`DELETE` under `/games`, `/predictions`,
`/results`), set:

```bash
export NBA_FOUL_API_KEY=some-long-random-secret
```

Callers must then send `X-API-Key: some-long-random-secret` on those
requests. **By default the read endpoints are NOT gated** — `/history`,
`/performance`, `/roi-report`, `/calibration-report`, `/games`,
`/model-info`, `/calibration` and the pricing endpoints stay open to
anyone with the URL, which means your picks, results, ROI and calibration
curve are public. To lock all of that behind the same key:

```bash
export NBA_FOUL_PROTECT_READS=1
```

With that on, every endpoint except `/` and `/health` needs a valid
`X-API-Key`. It requires `NBA_FOUL_API_KEY`: the app refuses to start
with protection on and no key, rather than run in a state where you
think your data is private and it isn't. The interactive `/docs` page
shows an `X-API-Key` field on each operation so you can paste the key
there. (`/docs` and `/openapi.json` describe the API's shape, not your
data; I haven't verified from here whether FastAPI also puts them behind
the app-wide check, so confirm what they do on your deployment.)

Notes:

- **A blank value counts as unset** (auth disabled, with a startup
  warning). Hosting platforms often can only express "no value" as an
  empty string, and copying `.env.example`'s blank `NBA_FOUL_API_KEY=`
  line produces exactly that. An empty `X-API-Key` header can never
  authenticate, whatever the configured value.
- Keys shorter than 16 characters work but log a warning; use
  `openssl rand -hex 32`.
- The key is compared in constant time.

### Rate limiting

A simple in-memory, per-client-IP sliding-window limiter applies to every
endpoint except `/health`, default 60 requests/minute. Configure with:

```bash
export NBA_FOUL_RATE_LIMIT_PER_MINUTE=120   # must be at least 1
```

Limited requests get a `429` with a `Retry-After` header (seconds).
Rejected requests don't count against the limit, and `/health` is exempt
so a traffic burst can't make a platform health check fail and trigger a
restart.

This is process-local — it resets on restart and is **not** shared across
multiple replicas. Fine for a single instance; swap in a shared store
(e.g. Redis) if you scale horizontally.

**Behind a hosting platform's reverse proxy, read this.** The limiter keys
on the client address uvicorn reports. Behind a proxy, that is the
*proxy's* address unless uvicorn is told to trust the platform's
`X-Forwarded-For` header — and by default it only trusts `127.0.0.1`. If
that's your situation, **every user shares one 60-requests/minute bucket**
(and so does the platform's own traffic). To check, look at the request
logs (each line ends in `client=<address>`): if every line shows the same
address, you're affected. The fix is to set uvicorn's
`FORWARDED_ALLOW_IPS` environment variable (also available as
`--forwarded-allow-ips`) to the proxy's address range, or to `*` **only if
the container is reachable exclusively through the platform's proxy** —
with `*` on a directly-exposed container, a client could send a fake
`X-Forwarded-For` header to dodge the limit. Check your platform's docs
for its proxy ranges and confirm the setting against uvicorn's
documentation for the version you deploy.

### CORS

No origins are allowed by default (safe default: blocks cross-origin
browser requests). To allow a frontend to call this API from the browser:

```bash
export NBA_FOUL_ALLOWED_ORIGINS=https://example.com,https://app.example.com
```

### Logging

Every request is logged to stdout with method, path, status code, and
duration — no configuration needed.

## Before deploying this publicly

A checklist, since "it runs" and "it's safe to put a URL in front of
strangers" are different bars:

- [ ] **Set `NBA_FOUL_API_KEY`.** Without it, anyone who finds the URL can
      create/edit/delete games and predictions. Generate one with
      `openssl rand -hex 32`. The app logs a warning on startup if this
      is unset *or blank* (a blank value is treated as unset), precisely
      so it's hard to miss.
- [ ] **Decide whether your data should be private, and if so set
      `NBA_FOUL_PROTECT_READS=1`.** Otherwise anyone with the URL can
      read your ledger, win rate and ROI, and use your pricing engine.
      (Also make the GitHub repository private: `main.py` contains your
      calibration table and historical statistics.)
- [ ] **Check the client addresses in the logs, and set
      `FORWARDED_ALLOW_IPS` if needed.** Behind a platform proxy, all
      users can end up sharing a single rate-limit bucket — see
      "Rate limiting" above for how to tell and how to fix it.
- [ ] **Check whether your host's filesystem is persistent.** SQLite
      writes to a file (`NBA_FOUL_DB_PATH`, `/app/data/nba_foul_api.db`
      in the Dockerfile). Many free/hobby tiers on hosting platforms
      wipe the container's filesystem on every restart or redeploy —
      if that's the case for your host, **the entire ledger resets** the
      next time the app restarts, silently. Either attach a persistent
      volume/disk (most platforms offer this, sometimes only on paid
      tiers) or accept that the ledger is ephemeral for now. `/health`
      reports `503` if the database file is missing, unreadable, or on a
      read-only filesystem, so point your platform's health check at it —
      but note it can't tell you the data is *persistent*, only that the
      file is currently usable.
- [ ] **Plan to run exactly one instance.** SQLite doesn't handle
      concurrent writers well, and the rate limiter and ledger both keep
      state in memory / a local file — neither is shared across
      replicas. Don't turn on autoscaling or multiple instances without
      revisiting both (a shared Postgres + Redis setup would be the
      eventual fix; out of scope for now).
- [ ] **Set `NBA_FOUL_ALLOWED_ORIGINS`** if a browser-based frontend on a
      different domain needs to call this API directly. Leave it unset
      if only server-to-server calls will hit it.
- [ ] **Reconsider `NBA_FOUL_RATE_LIMIT_PER_MINUTE`** (default 60/IP/min)
      for your expected traffic.
- [ ] **Confirm your host respects `$PORT`.** The Dockerfile's `CMD`
      already binds to whatever `$PORT` the platform injects, falling
      back to 8000 if none is set — this is required by most platforms
      (Render, Railway, etc.) and won't need changes on your end, but
      it's worth knowing why the container listens on a variable port.
- [ ] **HTTPS** is normally handled by the hosting platform automatically
      (Render, Railway, Fly.io all terminate TLS for you) — nothing to
      configure here, just confirm your chosen host does this before
      assuming traffic is encrypted.

See `.env.example` for every environment variable the app reads, in one
place.

## Forward-validation ledger

Beyond one-off pricing, the API can log games, attach one or more priced
predictions to each one (foul-out, a points threshold, a double-double,
...), record actual results, and report on performance over time. Data is
stored in a local SQLite file (`nba_foul_api.db` by default, created
automatically on first run; override the path with the `NBA_FOUL_DB_PATH`
environment variable).

**A game and its predictions are separate things.** A game is just an ID
and a date. Any number of predictions — one per market — can be attached
to it: a single game might have a foul-out prediction, a "25+ points"
prediction, and a double-double prediction all logged at once, each
tracked and settled independently.

Typical workflow:

1. **Register a game**
   ```
   POST /games
   { "game_id": "2026-01-15-LAL-BOS", "game_date": "2026-01-15" }
   ```

2. **Attach one or more predictions**, one endpoint per market family:

   Foul-out (runs the same pricing engine as `/price`):
   ```
   POST /predictions
   { "game_id": "2026-01-15-LAL-BOS", "pf": 44.0, "american_odds": 400 }
   ```

   Points / rebounds / assists threshold (runs the same engine as
   `/player-markets/price`):
   ```
   POST /player-predictions/threshold
   {
     "game_id": "2026-01-15-LAL-BOS",
     "market_type": "points",
     "threshold": 25,
     "players": [ { "player_name": "...", "lines": [...] }, ... ],
     "market_odds": 150
   }
   ```

   Double-double / triple-double (runs the same engine as
   `/player-markets/direct-price`):
   ```
   POST /player-predictions/direct
   {
     "game_id": "2026-01-15-LAL-BOS",
     "market_type": "double_double",
     "players": [ { "player_name": "...", "yes_odds": -700 }, ... ]
   }
   ```

   `market_odds` is optional on both player-prediction endpoints — pass
   it if you have the book's own price for that combined market to
   compare your model against (you get edge/ROI/status back); omit it
   to just log your model's own probability with nothing to compare it
   to yet.

3. **Record the actual result** once the game is final. You specify
   which prediction you're settling via `market_type` (and `threshold`
   for points/rebounds/assists):
   ```
   POST /results
   {
     "game_id": "2026-01-15-LAL-BOS",
     "market_type": "points",
     "threshold": 25,
     "actual_result": 1
   }
   ```
   Realized ROI is computed automatically from the `market_odds` that
   were logged with that prediction — if none were given, the outcome
   is still recorded but `realized_roi` stays null (there's no market
   price to compute ROI against).

   Made a mistake? Re-submitting any of the three prediction endpoints
   for the same (game, market_type, threshold) overwrites the previous
   value in place — same upsert behavior as before.

4. **Correct or remove a game** (cascades to every prediction attached
   to it):
   ```
   PATCH  /games/2026-01-15-LAL-BOS   { "game_date": "2026-01-16" }
   DELETE /games/2026-01-15-LAL-BOS
   ```

5. **Query the ledger**
   - `GET /games/2026-01-15-LAL-BOS` — that game plus every prediction
     attached to it, across all markets
   - `GET /history?market_type=points&status=PASS&limit=50&offset=0` —
     paginated list of predictions, filterable by market type and
     status. Response shape:
     `{ "total": <int>, "limit": <int>, "offset": <int>, "predictions": [...] }`
   - `GET /performance?market_type=points` — total/settled predictions,
     hit rate, average edge, and breakdowns by status and market type.
     Omit `market_type` to aggregate across every market.
   - `GET /calibration-report?market_type=points&bucket_width=0.1` — for
     settled predictions, buckets by *predicted probability* (not a raw
     stat value) and compares the average predicted probability against
     the actual hit rate in each bucket — this generalizes across every
     market, since "predicted probability" is the one thing every market
     has in common
   - `GET /roi-report?market_type=points` — total and average realized
     ROI, and win rate, both across all settled predictions and
     restricted to ones that passed the safety-margin threshold

This is intentionally a lightweight, dependency-free layer (stdlib
`sqlite3`) — enough to validate the model against real outcomes without
standing up a separate database.

### Marking a game postponed or canceled

A game that never actually happens otherwise just sits forever with
unsettled predictions, quietly inflating "pending" counts in
`/performance` indefinitely. Instead:

```
PATCH /games/2026-01-15-LAL-BOS/status
{ "status": "postponed" }
```

Valid values: `scheduled` (the default), `postponed`, `canceled`.
Predictions already attached to the game are left as-is — this just
flags the game, it doesn't delete anything. `GET /games?status=postponed`
filters the game list by it.

### Removing a single prediction

`DELETE /games/{game_id}` removes the game *and everything attached to
it*. If you just logged one bad prediction — the wrong double-double
line, a typo'd threshold — and want to fix just that without touching
the game's other markets:

```
DELETE /games/2026-01-15-LAL-BOS/predictions/points?threshold=25
```

`threshold` is only needed for points/rebounds/assists/both_teams_over;
omit it for foul_out/double_double/triple_double.

### Batch ingestion

Each prediction-creating endpoint has a `/batch` counterpart —
`/predictions/batch`, `/player-predictions/threshold/batch`,
`/player-predictions/direct/batch`, `/team-predictions/both-over/batch`
— that accepts a list of the same request shape under `"predictions"`
(up to 500 items) and processes every item independently. One bad item
(a missing game, inconsistent odds data) doesn't abort the rest of the
batch — the response reports per-item success/failure instead of being
all-or-nothing:

```json
{
  "total": 3,
  "succeeded": 2,
  "failed": 1,
  "results": [
    {"success": true, "game_id": "game-1", "prediction": {...}, "error": null},
    {"success": false, "game_id": "does-not-exist", "prediction": null, "error": "Game 'does-not-exist' not found. Create it via POST /games first."},
    {"success": true, "game_id": "game-2", "prediction": {...}, "error": null}
  ]
}
```

## Notes

- PF values outside the 30–50 calibration range are extrapolated linearly
  from the nearest edge segment; results are flagged `"extrapolated": true`.
  Per the model notes in `main.py`, production use should prefer keeping PF
  inputs inside the calibrated range until more data is available.
- This service implements the calibration/pricing layer only — it does not
  reconstruct or expose the underlying raw predictive model.
