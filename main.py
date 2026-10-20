import logging
import os
import sqlite3
import time
import json
from typing import List, Literal, Optional
import math

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import database as db
from foul_pricing import (
    CALIBRATION_TABLE,
    DEFAULT_SAFETY_MARGIN_PP,
    MODEL_VERSION,
    PF_POINTS,
    american_to_decimal_profit,
    calculate_expected_roi,
    calculate_price,
    interpolate_probability,
    market_break_even_probability,
    probability_to_fair_american,
)
import player_markets
import security
import team_markets


# ============================================================
# NBA 6-FOUL PRICING API
# Version 1.0
#
# Pricing model:
#     PF -> calibrated probability -> fair odds
#
# Frozen calibration points from the final model:
#     PF 30 to 38 -> 5.97% to 13.26% (added later, same formula)
#     PF 39 -> 14.57%
#     PF 40 -> 15.99%
#     PF 41 -> 17.52%
#     PF 42 -> 19.17%
#     PF 43 -> 20.93%
#     PF 44 -> 22.80%
#     PF 45 -> 24.79%
#     PF 46 -> 26.89%
#     PF 47 -> 29.11%
#     PF 48 -> 31.42%
#     PF 49 -> 33.83%
#     PF 50 -> 36.33%
#
# Safety margin:
#     2 percentage points
#
# IMPORTANT:
# This API implements the calibrated pricing layer.
# It does NOT reconstruct the underlying raw predictive model,
# because the raw model coefficients are not available here.
# ============================================================


# ============================================================
# OPERATIONAL CONFIGURATION
#
# All of these are opt-in via environment variables so the API keeps
# working with zero configuration for local development, but can be
# locked down for a real deployment.
# ============================================================

# Set this to require an API key on ledger write endpoints. If unset --
# or set to a blank/whitespace-only value, which counts as unset (see
# security.normalize_api_key for why) -- those endpoints remain open
# (useful for local dev / testing).
API_KEY = security.normalize_api_key(os.environ.get("NBA_FOUL_API_KEY"))

# Opt in to requiring the API key on EVERY endpoint, not just the ledger
# writes. By default only writes are protected, which leaves the ledger
# history, performance/ROI/calibration reports, model-info, and the
# pricing endpoints readable and usable by anyone who has the URL. Turn
# this on (1/true/yes/on) to keep all of that private. Requires
# NBA_FOUL_API_KEY; startup refuses to run without one.
PROTECT_READS = os.environ.get("NBA_FOUL_PROTECT_READS", "").strip().lower() in {
    "1", "true", "yes", "on",
}

# Reachable without a key even when reads are protected: a hosting
# platform's health probe has to work, and the root document reveals
# nothing but the service name and version.
PUBLIC_PATHS = frozenset({"/", "/health"})

# Comma-separated list of allowed browser origins for CORS, e.g.
# "https://example.com,https://app.example.com". Empty by default,
# which blocks all cross-origin browser requests.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("NBA_FOUL_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

# Simple per-process, per-client-IP rate limit: N requests per rolling
# 60-second window. Not shared across multiple replicas/instances.
RATE_LIMIT_REQUESTS_PER_MINUTE = int(
    os.environ.get("NBA_FOUL_RATE_LIMIT_PER_MINUTE", "60")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("nba_foul_api")

# Historical reference values.
HISTORICAL_GAMES = 2453
HISTORICAL_HITS = 405
HISTORICAL_RATE = 0.165104

OOS_GAMES = 2437
OOS_HITS = 402
OOS_RATE = 0.164957

OOS_MEAN_RAW_PROBABILITY = 0.164991
OOS_MEAN_CALIBRATED_PROBABILITY = 0.16594

RAW_BRIER = 0.137026
CALIBRATED_BRIER = 0.137037

RAW_LOG_LOSS = 0.445011
CALIBRATED_LOG_LOSS = 0.445165


# ============================================================
# FASTAPI APP
# ============================================================

def require_key_when_reads_protected(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    App-wide dependency; a no-op unless NBA_FOUL_PROTECT_READS is on.
    When it is, EVERY endpoint except PUBLIC_PATHS needs a valid
    X-API-Key -- the read endpoints and the pricing endpoints included.
    Fails closed: if reads are protected but no key is configured,
    nothing validates (startup also refuses to run in that state).
    """
    if not PROTECT_READS or request.url.path in PUBLIC_PATHS:
        return

    if not security.api_key_is_valid(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key. Send it in the X-API-Key header.",
        )


app = FastAPI(
    title="NBA 6-Foul Pricing API",
    description=(
        "Probability and market-pricing API for the NBA event "
        "'at least one player reaches 6 personal fouls'. "
        "Uses the frozen PF-to-calibrated-probability pricing curve."
    ),
    version=MODEL_VERSION,
    dependencies=[Depends(require_key_when_reads_protected)],
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs method, path, status code, and duration for every request."""

    async def dispatch(self, request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        # The client address is logged so an operator can verify the
        # reverse-proxy setup: if every request shows the SAME address
        # (the platform's proxy) instead of varied real client IPs, the
        # rate limiter is treating all users as one client. See README,
        # "Before deploying this publicly".
        client = request.client.host if request.client else "unknown"
        logger.info(
            "%s %s -> %s (%.1fms) client=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            client,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-client-IP sliding-window rate limiter. The actual logic lives in
    security.SlidingWindowLimiter, kept out of this file so it can be
    tested in isolation. In-memory only: state resets on restart and is
    not shared across multiple app instances. Fine for a single-process
    deployment; swap for a shared store (e.g. Redis) if you scale out
    horizontally.

    IMPORTANT behind a hosting platform's reverse proxy: the client IP
    seen here is whatever uvicorn reports, which is the PROXY's address
    unless uvicorn is told to trust the platform's X-Forwarded-For
    header (via the FORWARDED_ALLOW_IPS environment variable). Left at
    its default, every user behind the proxy shares ONE bucket. See
    README, "Before deploying this publicly".
    """

    def __init__(self, app, requests_per_minute: int):
        super().__init__(app)
        self.limit = requests_per_minute
        self._limiter = security.SlidingWindowLimiter(requests_per_minute, 60.0)

    async def dispatch(self, request, call_next):
        # Platform health checks must never be rate limited: a burst of
        # real traffic could otherwise make /health answer 429, and a
        # platform that sees failing health checks restarts the app --
        # turning a traffic spike into a self-inflicted outage. (/health
        # is a cheap read-only query, so exempting it costs little.)
        if request.url.path == "/health":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        allowed, retry_after = self._limiter.check(client_ip)

        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": (
                        f"Rate limit exceeded: max {self.limit} requests "
                        "per minute."
                    )
                },
            )

        return await call_next(request)


app.add_middleware(RateLimitMiddleware, requests_per_minute=RATE_LIMIT_REQUESTS_PER_MINUTE)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # Auth here is an X-API-Key header, not cookies, so credentialed
    # cross-origin requests aren't needed. Leaving this True would also
    # make a wildcard origin setting echo back ANY origin with
    # credentials allowed -- a classic CORS misconfiguration.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    """
    Dependency for ledger write endpoints. If NBA_FOUL_API_KEY is unset
    (or blank), auth is disabled (local dev / testing default). If it's
    set, callers must send a matching X-API-Key header -- compared in
    constant time, and an empty/missing header can never match.
    """
    if API_KEY is None:
        return

    if not security.api_key_is_valid(x_api_key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key. Send it in the X-API-Key header.",
        )


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error_handler(request, exc):
    """
    The endpoints check for conflicts first (does the game exist? is the
    id taken?), but those checks and the writes that follow aren't
    atomic: a concurrent request can delete a game or create a duplicate
    in between. The database's constraints still catch it -- this turns
    the resulting IntegrityError into a retryable 409 instead of an
    unhandled 500.
    """
    logger.warning(
        "Integrity error on %s %s: %s", request.method, request.url.path, exc
    )
    return JSONResponse(
        status_code=409,
        content={
            "detail": (
                "The request conflicted with a concurrent change (for "
                "example, the game was deleted while the prediction was "
                "being saved). Retry the request."
            )
        },
    )


# Create the games table immediately on import so the ledger endpoints
# work out of the box, regardless of how the app is served or tested.
db.init_db()


@app.on_event("startup")
def on_startup():
    if PROTECT_READS and API_KEY is None:
        # Fail fast rather than start in a state where the operator
        # believes their data is private but the lock has no key.
        raise RuntimeError(
            "NBA_FOUL_PROTECT_READS is enabled but NBA_FOUL_API_KEY is "
            "not set (or is blank). Set the key, or turn off "
            "NBA_FOUL_PROTECT_READS."
        )

    db.init_db()

    if API_KEY is not None and not PROTECT_READS:
        logger.info(
            "Read endpoints (history, performance, ROI, calibration, "
            "model-info, pricing) are OPEN to anyone with the URL; only "
            "ledger writes need the API key. Set NBA_FOUL_PROTECT_READS=1 "
            "to require the key on those too."
        )

    if API_KEY is None:
        logger.warning(
            "NBA_FOUL_API_KEY is not set -- every ledger write endpoint "
            "(POST/PATCH/DELETE under /games, /predictions, /results) is "
            "UNAUTHENTICATED. Fine for local development; set this "
            "environment variable before deploying anywhere public."
        )

    if os.environ.get("NBA_FOUL_API_KEY") is not None and API_KEY is None:
        logger.warning(
            "NBA_FOUL_API_KEY is set but blank -- treating it as unset, so "
            "auth is DISABLED. Set a real value or remove the variable."
        )
    elif (
        API_KEY is not None
        and len(API_KEY) < security.MIN_RECOMMENDED_API_KEY_LENGTH
    ):
        logger.warning(
            "NBA_FOUL_API_KEY is only %d characters; use at least %d "
            "(e.g. `openssl rand -hex 32`).",
            len(API_KEY),
            security.MIN_RECOMMENDED_API_KEY_LENGTH,
        )

    if not ALLOWED_ORIGINS:
        logger.info(
            "NBA_FOUL_ALLOWED_ORIGINS is not set -- browser-based "
            "cross-origin requests are blocked by default. Set it if a "
            "frontend on a different domain needs to call this API "
            "directly from the browser."
        )


def _validate_american_odds(value: int) -> int:
    """
    Shared body for every American-odds field validator. Real odds are
    +100 or higher, or -100 or lower. Zero and everything in between
    (+5, -50, ...) isn't a real price and is almost always a typo that
    would otherwise be priced silently as if it were real (+5 would
    read as a 95% implied probability).
    """
    if abs(value) < 100:
        raise ValueError(
            "American odds must be +100 or higher, or -100 or lower "
            f"(got {value})."
        )
    return value


def _reject_duplicate_players(players: list) -> list:
    """
    Shared field validator for every players: List[...] field across
    the threshold/direct market models. Catches a real, plausible
    mistake: accidentally listing the same player twice (e.g. merging
    data from two sources) silently inflates the "any player" combined
    probability, since combine_any_player's 1 - product(1-p_i) formula
    treats each entry as an independent person -- two entries for the
    same player double-counts them as if there were two separate
    players with that exact probability. Comparison is case-insensitive
    and whitespace-trimmed, since "Nikola Jokic" and "nikola jokic " are
    the same data-entry mistake, not two different players.
    """
    seen = set()
    for player in players:
        key = player.player_name.strip().lower()
        if not key:
            raise ValueError("player_name cannot be blank.")
        if key in seen:
            raise ValueError(
                f"Duplicate player '{player.player_name}' in players list -- "
                "each player should appear once. Listing the same player "
                "twice silently inflates the combined probability."
            )
        seen.add(key)
    return players


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================

class PriceRequest(BaseModel):
    pf: float = Field(
        ...,
        description="Projected PF value.",
        examples=[44.0],
    )

    american_odds: int = Field(
        ...,
        description="American odds offered by the market.",
        examples=[400],
    )

    safety_margin_pp: float = Field(
        DEFAULT_SAFETY_MARGIN_PP,
        ge=0.0,
        le=20.0,
        description="Required safety margin in percentage points.",
        examples=[2.0],
    )

    @field_validator("pf")
    @classmethod
    def validate_pf(cls, value):
        if not math.isfinite(value):
            raise ValueError("PF must be a finite number.")
        return value

    @field_validator("american_odds")
    @classmethod
    def validate_odds(cls, value):
        return _validate_american_odds(value)


class PredictRequest(BaseModel):
    pf: float = Field(
        ...,
        description="Projected PF value.",
        examples=[44.0],
    )


class BatchPriceRequest(BaseModel):
    games: List[PriceRequest]


class PredictionResponse(BaseModel):
    model_version: str
    pf: float
    calibrated_probability: float
    calibrated_probability_percent: float
    fair_american_odds: Optional[int]
    extrapolated: bool


class PriceResponse(BaseModel):
    model_version: str

    pf: float

    calibrated_probability: float
    calibrated_probability_percent: float

    market_american_odds: int
    market_break_even_probability: float
    market_break_even_probability_percent: float

    probability_edge: float
    probability_edge_pp: float

    fair_american_odds: Optional[int]

    expected_roi: float
    expected_roi_percent: float

    safety_margin_pp: float
    required_probability: float
    required_probability_percent: float

    margin_above_requirement_pp: float

    minimum_acceptable_american_odds: Optional[int]

    status: str

    extrapolated: bool


MarketType = Literal[
    "foul_out", "points", "rebounds", "assists", "double_double", "triple_double",
    "both_teams_over",
]

GameStatus = Literal["scheduled", "postponed", "canceled"]

PredictionStatus = Literal["PASS", "NO_EDGE", "FAIR_OR_BORDERLINE"]


class GameCreateRequest(BaseModel):
    game_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description=(
            "Unique identifier for the game. Letters, digits, hyphens, "
            "underscores, and periods only -- no slashes or spaces. "
            "This becomes part of URL paths (e.g. DELETE /games/"
            "{game_id}/predictions/{market_type}), so a '/' would "
            "silently break those routes rather than erroring clearly."
        ),
        examples=["2026-01-15-LAL-BOS"],
    )

    game_date: str = Field(
        ...,
        description="Date of the game, e.g. YYYY-MM-DD.",
        examples=["2026-01-15"],
    )


class GameUpdateRequest(BaseModel):
    game_date: str = Field(
        ...,
        description="Corrected date for this game, e.g. YYYY-MM-DD.",
        examples=["2026-01-15"],
    )


class GameStatusUpdateRequest(BaseModel):
    status: GameStatus = Field(
        ...,
        description=(
            "Mark a game as postponed or canceled so it stops looking "
            "like an indefinitely-pending prediction in /performance "
            "and friends, or set back to 'scheduled' to undo that."
        ),
    )


class GameRecord(BaseModel):
    """
    A game is now just a registration row -- market-specific fields
    live on PredictionRecord instead, since one game can have several
    predictions attached (foul-out, points-30, double-double, ...).
    """

    game_id: str
    game_date: Optional[str] = None
    status: Optional[str] = None
    timestamp: Optional[str] = None


# Market types with no meaningful threshold use this sentinel in the
# database (see database.py's schema docstring for why 0 rather than
# NULL).
NO_THRESHOLD = 0.0


class FoulPredictionCreateRequest(BaseModel):
    """Attach a foul-out prediction to a game (the original market)."""

    game_id: str = Field(..., description="Existing game to attach this prediction to.")

    pf: float = Field(..., description="Projected PF value.", examples=[44.0])

    american_odds: int = Field(
        ..., description="American odds offered by the market.", examples=[400]
    )

    safety_margin_pp: float = Field(
        DEFAULT_SAFETY_MARGIN_PP,
        ge=0.0,
        le=20.0,
        description="Required safety margin in percentage points.",
    )

    @field_validator("pf")
    @classmethod
    def validate_pf(cls, value):
        if not math.isfinite(value):
            raise ValueError("PF must be a finite number.")
        return value

    @field_validator("american_odds")
    @classmethod
    def validate_odds(cls, value):
        return _validate_american_odds(value)


class ResultCreateRequest(BaseModel):
    game_id: str = Field(..., description="Game to record a result for.")

    market_type: MarketType = Field(
        ..., description="Which prediction on this game this result settles."
    )

    threshold: float = Field(
        NO_THRESHOLD,
        ge=0,
        description=(
            "Threshold of the prediction being settled (e.g. 25 for "
            "points). Omit for foul_out/double_double/triple_double, "
            "which have no threshold."
        ),
    )

    actual_result: int = Field(
        ...,
        ge=0,
        le=1,
        description="1 if the predicted event actually happened, else 0.",
    )


class PredictionRecord(BaseModel):
    id: Optional[int] = None
    game_id: str
    market_type: str
    threshold: float
    combined_probability: Optional[float] = None
    combined_fair_odds: Optional[int] = None
    market_odds: Optional[int] = None
    market_break_even: Optional[float] = None
    probability_edge: Optional[float] = None
    expected_roi: Optional[float] = None
    safety_margin: Optional[float] = None
    required_probability: Optional[float] = None
    status: Optional[str] = None
    actual_result: Optional[int] = None
    realized_roi: Optional[float] = None
    details: Optional[dict] = None
    model_version: Optional[str] = None
    timestamp: Optional[str] = None


class GameWithPredictions(GameRecord):
    predictions: List[PredictionRecord] = []


class PlayerLineInput(BaseModel):
    line: float = Field(..., description="Alt line value, e.g. 24.5.", examples=[24.5])
    over_odds: int = Field(..., description="American odds for the Over.", examples=[350])
    under_odds: int = Field(..., description="American odds for the Under.", examples=[-450])

    @field_validator("over_odds", "under_odds")
    @classmethod
    def validate_odds_magnitude(cls, value):
        return _validate_american_odds(value)


class ThresholdPlayerInput(BaseModel):
    player_name: str = Field(..., examples=["Angel Reese"])
    lines: List[PlayerLineInput] = Field(
        ..., description="This player's alt-line ladder for the stat."
    )


class ThresholdPriceRequest(BaseModel):
    market_type: Literal["points", "rebounds", "assists"] = Field(
        ..., description="Which stat this milestone is measured on."
    )
    threshold: float = Field(
        ..., gt=0, description="Milestone to price, e.g. 25 for '25+ points'."
    )
    players: List[ThresholdPlayerInput]

    @field_validator("players")
    @classmethod
    def validate_no_duplicate_players(cls, value):
        return _reject_duplicate_players(value)


class PlayerThresholdResult(BaseModel):
    player_name: str
    probability: float
    probability_percent: float
    fair_american_odds: Optional[int]
    extrapolated: bool


class ThresholdPriceResponse(BaseModel):
    market_type: str
    threshold: float
    players: List[PlayerThresholdResult]
    combined_probability: float
    combined_probability_percent: float
    combined_fair_american_odds: Optional[int]


class DirectMarketPlayerInput(BaseModel):
    player_name: str = Field(..., examples=["Nikola Jokic"])
    yes_odds: int = Field(..., description="American odds for Yes.", examples=[-350])
    no_odds: Optional[int] = Field(
        None,
        description=(
            "American odds for No, if the book posts both sides. "
            "If omitted, the Yes odds are used as-is (not de-vigged)."
        ),
    )

    @field_validator("yes_odds")
    @classmethod
    def validate_yes_odds_magnitude(cls, value):
        return _validate_american_odds(value)

    @field_validator("no_odds")
    @classmethod
    def validate_no_odds_magnitude(cls, value):
        return None if value is None else _validate_american_odds(value)


class DirectPriceRequest(BaseModel):
    market_type: Literal["double_double", "triple_double"]
    players: List[DirectMarketPlayerInput]

    @field_validator("players")
    @classmethod
    def validate_no_duplicate_players(cls, value):
        return _reject_duplicate_players(value)


class PlayerDirectResult(BaseModel):
    player_name: str
    probability: float
    probability_percent: float
    fair_american_odds: Optional[int]
    devigged: bool


class DirectPriceResponse(BaseModel):
    market_type: str
    players: List[PlayerDirectResult]
    combined_probability: float
    combined_probability_percent: float
    combined_fair_american_odds: Optional[int]


class PlayerThresholdPredictionCreateRequest(BaseModel):
    """Attach a points/rebounds/assists threshold prediction to a game."""

    game_id: str = Field(..., description="Existing game to attach this prediction to.")

    market_type: Literal["points", "rebounds", "assists"]

    threshold: float = Field(..., gt=0, examples=[25.0])

    players: List[ThresholdPlayerInput]

    market_odds: Optional[int] = Field(
        None,
        description=(
            "The book's own American odds for this exact combined "
            "market ('any player to hit this milestone'), if you have "
            "one to compare your model against. If omitted, edge/ROI/"
            "status are left null -- you still get the combined "
            "probability, just nothing to compare it to yet."
        ),
    )

    safety_margin_pp: float = Field(
        DEFAULT_SAFETY_MARGIN_PP, ge=0.0, le=20.0
    )

    @field_validator("market_odds")
    @classmethod
    def validate_market_odds_magnitude(cls, value):
        return None if value is None else _validate_american_odds(value)

    @field_validator("players")
    @classmethod
    def validate_no_duplicate_players(cls, value):
        return _reject_duplicate_players(value)


class PlayerDirectPredictionCreateRequest(BaseModel):
    """Attach a double-double/triple-double prediction to a game."""

    game_id: str = Field(..., description="Existing game to attach this prediction to.")

    market_type: Literal["double_double", "triple_double"]

    players: List[DirectMarketPlayerInput]

    market_odds: Optional[int] = Field(
        None, description="Same meaning as in PlayerThresholdPredictionCreateRequest."
    )

    safety_margin_pp: float = Field(
        DEFAULT_SAFETY_MARGIN_PP, ge=0.0, le=20.0
    )

    @field_validator("market_odds")
    @classmethod
    def validate_market_odds_magnitude(cls, value):
        return None if value is None else _validate_american_odds(value)

    @field_validator("players")
    @classmethod
    def validate_no_duplicate_players(cls, value):
        return _reject_duplicate_players(value)


class BothTeamsOverPriceRequest(BaseModel):
    """
    'Both Team A and Team B exceed their own threshold' -- the
    correlation-implied joint market built on team_markets.py. Each
    ladder needs >= 2 alt-line points to fit a standard deviation, not
    just a mean (see team_markets.fit_normal_from_ladder).
    """

    team_a_name: str = Field(..., examples=["Lakers"])
    team_a_lines: List[PlayerLineInput] = Field(
        ..., description="Team A's own alt-line ladder for this window (>= 2 points)."
    )

    team_b_name: str = Field(..., examples=["Celtics"])
    team_b_lines: List[PlayerLineInput] = Field(
        ..., description="Team B's own alt-line ladder for this window (>= 2 points)."
    )

    game_total_lines: List[PlayerLineInput] = Field(
        ...,
        description=(
            "The combined game total's alt-line ladder for the same "
            "window (>= 2 points) -- this is what makes the correlation "
            "between the two teams derivable from market data instead "
            "of assumed."
        ),
    )

    threshold_a: float = Field(..., gt=0, examples=[100.0])
    threshold_b: Optional[float] = Field(
        None,
        gt=0,
        description="Defaults to threshold_a if omitted (the common 'both teams X+' case).",
    )


class BothTeamsOverPriceResponse(BaseModel):
    team_a_name: str
    team_b_name: str
    threshold_a: float
    threshold_b: float
    team_a_mean: float
    team_a_std: float
    team_b_mean: float
    team_b_std: float
    game_total_mean: float
    game_total_std: float
    implied_correlation: float
    joint_probability: float
    joint_probability_percent: float
    fair_american_odds: Optional[int]
    naive_independent_probability: float
    naive_independent_probability_percent: float


class BothTeamsOverPredictionCreateRequest(BaseModel):
    """
    Attach/overwrite a both-teams-over prediction for a game. The
    ledger's schema stores a single threshold per prediction, so this
    endpoint requires threshold_a == threshold_b (or threshold_b
    omitted) -- the stateless /team-markets/price endpoint above
    supports differing thresholds if you need that, just not persisted
    to the ledger yet.
    """

    game_id: str = Field(..., description="Existing game to attach this prediction to.")

    team_a_name: str
    team_a_lines: List[PlayerLineInput]
    team_b_name: str
    team_b_lines: List[PlayerLineInput]
    game_total_lines: List[PlayerLineInput]

    threshold_a: float = Field(..., gt=0)
    threshold_b: Optional[float] = Field(None, gt=0)

    market_odds: Optional[int] = Field(
        None, description="Same meaning as the other prediction endpoints."
    )

    safety_margin_pp: float = Field(DEFAULT_SAFETY_MARGIN_PP, ge=0.0, le=20.0)

    @field_validator("market_odds")
    @classmethod
    def validate_market_odds_magnitude(cls, value):
        return None if value is None else _validate_american_odds(value)


# ============================================================
# API ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "name": "NBA 6-Foul Pricing API",
        "version": MODEL_VERSION,
        "status": "online",
        "docs": "/docs",
        "event": (
            "At least one NBA player reaches "
            "6 personal fouls"
        ),
    }


@app.get("/health")
def health():
    """
    Reports unhealthy (503) when the ledger database isn't usable, so a
    hosting platform's health check can restart or alert on an app whose
    database file is missing, corrupt, or on a read-only/unmounted
    volume -- instead of cheerfully reporting healthy while every
    ledger request fails.
    """
    database_status = db.check_health()

    body = {
        "status": "healthy" if database_status["ok"] else "unhealthy",
        "model_version": MODEL_VERSION,
        "database": database_status,
    }

    if not database_status["ok"]:
        return JSONResponse(status_code=503, content=body)

    return body


@app.get("/model-info")
def model_info():
    return {
        "model_version": MODEL_VERSION,

        "event": (
            "At least one player reaches "
            "6 personal fouls"
        ),

        "historical_games": HISTORICAL_GAMES,
        "historical_hits": HISTORICAL_HITS,
        "historical_rate": HISTORICAL_RATE,

        "oos_games": OOS_GAMES,
        "oos_hits": OOS_HITS,
        "oos_rate": OOS_RATE,

        "oos_mean_raw_probability":
            OOS_MEAN_RAW_PROBABILITY,

        "oos_mean_calibrated_probability":
            OOS_MEAN_CALIBRATED_PROBABILITY,

        "raw_brier": RAW_BRIER,
        "calibrated_brier": CALIBRATED_BRIER,

        "raw_log_loss": RAW_LOG_LOSS,
        "calibrated_log_loss": CALIBRATED_LOG_LOSS,

        "default_safety_margin_pp":
            DEFAULT_SAFETY_MARGIN_PP,

        "calibration_range": {
            "minimum_pf": PF_POINTS[0],
            "maximum_pf": PF_POINTS[-1],
        },

        "calibration_table": [
            {
                "pf": pf,
                "calibrated_probability": probability,
                "calibrated_probability_percent":
                    round(probability * 100, 4),
                "fair_american_odds":
                    probability_to_fair_american(probability),
            }
            for pf, probability in CALIBRATION_TABLE
        ],
    }


@app.post(
    "/predict",
    response_model=PredictionResponse,
)
def predict(request: PredictRequest):

    probability, extrapolated = interpolate_probability(
        request.pf
    )

    fair_odds = probability_to_fair_american(
        probability
    )

    return {
        "model_version": MODEL_VERSION,

        "pf": round(request.pf, 4),

        "calibrated_probability":
            round(probability, 6),

        "calibrated_probability_percent":
            round(probability * 100, 4),

        "fair_american_odds":
            fair_odds,

        "extrapolated":
            extrapolated,
    }


@app.post(
    "/price",
    response_model=PriceResponse,
)
def price(request: PriceRequest):

    return calculate_price(
        pf=request.pf,
        american_odds=request.american_odds,
        safety_margin_pp=request.safety_margin_pp,
    )


@app.post("/batch-price")
def batch_price(request: BatchPriceRequest):

    if len(request.games) == 0:
        raise HTTPException(
            status_code=400,
            detail="The games list cannot be empty.",
        )

    if len(request.games) > 1000:
        raise HTTPException(
            status_code=400,
            detail="Maximum batch size is 1000 games.",
        )

    results = []

    for game in request.games:

        result = calculate_price(
            pf=game.pf,
            american_odds=game.american_odds,
            safety_margin_pp=game.safety_margin_pp,
        )

        results.append(result)

    return {
        "model_version": MODEL_VERSION,
        "count": len(results),
        "results": results,
    }


@app.get("/calibration")
def calibration():

    return {
        "model_version": MODEL_VERSION,

        "points": [
            {
                "pf": pf,
                "calibrated_probability":
                    probability,

                "calibrated_probability_percent":
                    round(probability * 100, 4),

                "fair_american_odds":
                    probability_to_fair_american(
                        probability
                    ),
            }
            for pf, probability in CALIBRATION_TABLE
        ],
    }


# ============================================================
# PLAYER PROP MARKETS
#
# "Any player to hit X" markets, priced from each player's own quoted
# odds rather than a game-level proxy (see player_markets.py for the
# full design rationale). The pricing logic lives in these two helper
# functions so it can be shared between the stateless endpoints below
# and the ledger-persisting endpoints further down, rather than
# duplicated between them.
# ============================================================

def _price_threshold_players(threshold: float, players: list):
    """
    Shared by POST /player-markets/price (stateless) and
    POST /player-predictions/threshold (persists to the ledger).

    Returns (player_results, combined_probability).
    """
    if len(players) == 0:
        raise HTTPException(
            status_code=400,
            detail="players list cannot be empty.",
        )

    player_results = []
    probabilities = []

    for player in players:
        if len(player.lines) == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Player '{player.player_name}' has no lines provided.",
            )

        line_tuples = [
            (line.line, line.over_odds, line.under_odds)
            for line in player.lines
        ]

        try:
            ladder = player_markets.build_ladder(line_tuples)
            probability, extrapolated = player_markets.probability_at_threshold(
                ladder, threshold
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        probabilities.append(probability)

        player_results.append(
            {
                "player_name": player.player_name,
                "probability": round(probability, 6),
                "probability_percent": round(probability * 100, 4),
                "fair_american_odds": probability_to_fair_american(probability),
                "extrapolated": extrapolated,
            }
        )

    combined_probability = player_markets.combine_any_player(probabilities)

    return player_results, combined_probability


def _price_direct_players(players: list):
    """
    Shared by POST /player-markets/direct-price (stateless) and
    POST /player-predictions/direct (persists to the ledger).

    Returns (player_results, combined_probability).
    """
    if len(players) == 0:
        raise HTTPException(
            status_code=400,
            detail="players list cannot be empty.",
        )

    player_results = []
    probabilities = []

    for player in players:
        try:
            probability, devigged = player_markets.direct_market_probability(
                player.yes_odds, player.no_odds
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        probabilities.append(probability)

        player_results.append(
            {
                "player_name": player.player_name,
                "probability": round(probability, 6),
                "probability_percent": round(probability * 100, 4),
                "fair_american_odds": probability_to_fair_american(probability),
                "devigged": devigged,
            }
        )

    combined_probability = player_markets.combine_any_player(probabilities)

    return player_results, combined_probability


@app.post(
    "/player-markets/price",
    response_model=ThresholdPriceResponse,
)
def price_player_threshold_market(request: ThresholdPriceRequest):
    """
    Prices an "any player to hit <threshold> <market_type>" market from
    each player's alt-line ladder (points, rebounds, or assists).
    Stateless -- see /player-predictions/threshold to persist this.
    """
    player_results, combined_probability = _price_threshold_players(
        request.threshold, request.players
    )

    return {
        "market_type": request.market_type,
        "threshold": request.threshold,
        "players": player_results,
        "combined_probability": round(combined_probability, 6),
        "combined_probability_percent": round(combined_probability * 100, 4),
        "combined_fair_american_odds": probability_to_fair_american(
            combined_probability
        ),
    }


@app.post(
    "/player-markets/direct-price",
    response_model=DirectPriceResponse,
)
def price_player_direct_market(request: DirectPriceRequest):
    """
    Prices an "any player to record a <market_type>" market (double-double
    or triple-double) directly from each player's own Yes/No line, per
    the design decision not to assemble these from points/rebounds/
    assists components. Stateless -- see /player-predictions/direct to
    persist this.
    """
    player_results, combined_probability = _price_direct_players(request.players)

    return {
        "market_type": request.market_type,
        "players": player_results,
        "combined_probability": round(combined_probability, 6),
        "combined_probability_percent": round(combined_probability * 100, 4),
        "combined_fair_american_odds": probability_to_fair_american(
            combined_probability
        ),
    }


def _price_both_teams_over(
    team_a_lines: list,
    team_b_lines: list,
    game_total_lines: list,
    threshold_a: float,
    threshold_b: Optional[float],
) -> dict:
    """
    Shared by POST /team-markets/price (stateless) and
    POST /team-predictions/both-over (persists to the ledger).

    Converts each Pydantic line list into the (line, over_odds,
    under_odds) tuples team_markets.build_ladder expects, runs the
    correlation-implied pricing pipeline, and wraps every ValueError
    team_markets.py can raise (empty/single-point ladders, inconsistent
    data producing a non-positive std) into a clean 400 instead of
    letting it surface as an unhandled 500.
    """
    if threshold_b is None:
        threshold_b = threshold_a

    def to_tuples(lines):
        return [(line.line, line.over_odds, line.under_odds) for line in lines]

    try:
        team_a_ladder = player_markets.build_ladder(to_tuples(team_a_lines))
        team_b_ladder = player_markets.build_ladder(to_tuples(team_b_lines))
        game_total_ladder = player_markets.build_ladder(to_tuples(game_total_lines))

        result = team_markets.price_both_teams_over(
            team_a_ladder, team_b_ladder, game_total_ladder, threshold_a, threshold_b
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Naive independent-multiplication probability, surfaced alongside
    # the correct one for transparency -- this is the "wrong" answer
    # the correlation-implied model was specifically built to improve on.
    p_a_alone = 1 - team_markets.norm_cdf(
        (threshold_a - result["team_a_mean"]) / result["team_a_std"]
    )
    p_b_alone = 1 - team_markets.norm_cdf(
        (threshold_b - result["team_b_mean"]) / result["team_b_std"]
    )
    naive_probability = p_a_alone * p_b_alone

    return {
        "threshold_b": threshold_b,
        "joint_probability": result["joint_probability"],
        "naive_independent_probability": naive_probability,
        **result,
    }


@app.post(
    "/team-markets/price",
    response_model=BothTeamsOverPriceResponse,
)
def price_both_teams_over_market(request: BothTeamsOverPriceRequest):
    """
    Prices 'Team A >= threshold_a AND Team B >= threshold_b' using the
    correlation-implied model in team_markets.py: fits each team's and
    the combined game total's distribution from their alt-line ladders,
    derives the correlation between the two teams from market data
    (rather than assuming independence), and computes the true joint
    probability. Stateless -- see /team-predictions/both-over to persist
    this.
    """
    priced = _price_both_teams_over(
        request.team_a_lines,
        request.team_b_lines,
        request.game_total_lines,
        request.threshold_a,
        request.threshold_b,
    )

    joint_probability = priced["joint_probability"]

    return {
        "team_a_name": request.team_a_name,
        "team_b_name": request.team_b_name,
        "threshold_a": request.threshold_a,
        "threshold_b": priced["threshold_b"],
        "team_a_mean": priced["team_a_mean"],
        "team_a_std": priced["team_a_std"],
        "team_b_mean": priced["team_b_mean"],
        "team_b_std": priced["team_b_std"],
        "game_total_mean": priced["game_total_mean"],
        "game_total_std": priced["game_total_std"],
        "implied_correlation": priced["implied_correlation"],
        "joint_probability": round(joint_probability, 6),
        "joint_probability_percent": round(joint_probability * 100, 4),
        "fair_american_odds": probability_to_fair_american(joint_probability),
        "naive_independent_probability": round(
            priced["naive_independent_probability"], 6
        ),
        "naive_independent_probability_percent": round(
            priced["naive_independent_probability"] * 100, 4
        ),
    }


# ============================================================
# FORWARD-VALIDATION LEDGER
#
# Stateful layer on top of the pricing engine: register a game, attach
# one or more priced predictions to it (foul-out, points-30, double-
# double, ...), later record each one's actual result, and query
# history/performance/calibration/ROI across everything logged --
# optionally filtered down to a single market type.
# ============================================================

def _prediction_row_to_response(row: dict) -> dict:
    """
    Converts a raw predictions-table row into the shape PredictionRecord
    expects, parsing the `details` JSON string (if present) back into a
    dict so callers get structured data instead of an opaque string.
    """
    response = dict(row)
    if response.get("details"):
        try:
            response["details"] = json.loads(response["details"])
        except (TypeError, ValueError):
            pass  # leave malformed/legacy details as-is rather than 500ing
    return response


# Max items per batch request across every /*/batch endpoint. Keeps a
# single pathological request from tying up the process for a long time
# or producing an enormous response body.
MAX_BATCH_SIZE = 500


def _process_batch(items: list, process_one) -> dict:
    """
    Runs process_one(item) -> raw prediction row dict for each item,
    continuing through the whole batch even if some items fail rather
    than aborting on the first error -- a real bulk-ingestion run
    (many games, many markets) shouldn't be all-or-nothing when one
    item has bad data or references a game that doesn't exist yet.

    process_one should raise HTTPException (or anything with a
    `.detail` attribute) or ValueError on failure; any other exception
    type is also caught (logged server-side and reported generically,
    so internal details like file paths never reach the client), since a
    single bad item should
    never take down the rest of the batch or surface as an unhandled
    500 for the whole request.

    Returns {"total", "succeeded", "failed", "results": [...]}, where
    each result item is {"success", "game_id", "prediction", "error"}.
    """
    if len(items) == 0:
        raise HTTPException(status_code=400, detail="predictions list cannot be empty.")

    if len(items) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum batch size is {MAX_BATCH_SIZE} predictions.",
        )

    results = []

    for item in items:
        game_id = getattr(item, "game_id", None)
        try:
            row = process_one(item)
            results.append(
                {
                    "success": True,
                    "game_id": game_id,
                    "prediction": _prediction_row_to_response(row),
                    "error": None,
                }
            )
        except HTTPException as exc:
            results.append(
                {
                    "success": False,
                    "game_id": game_id,
                    "prediction": None,
                    "error": str(exc.detail),
                }
            )
        except ValueError as exc:
            results.append(
                {
                    "success": False,
                    "game_id": game_id,
                    "prediction": None,
                    "error": str(exc),
                }
            )
        except Exception:  # noqa: BLE001 -- see docstring
            logger.exception(
                "Unexpected error processing batch item for game %s", game_id
            )
            results.append(
                {
                    "success": False,
                    "game_id": game_id,
                    "prediction": None,
                    "error": (
                        "Internal error while processing this item "
                        "(details are in the server logs)."
                    ),
                }
            )

    succeeded = sum(1 for r in results if r["success"])

    return {
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


@app.post(
    "/games",
    response_model=GameRecord,
    status_code=201,
    dependencies=[Depends(require_api_key)],
)
def create_game(request: GameCreateRequest):

    if db.get_game(request.game_id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Game '{request.game_id}' already exists.",
        )

    try:
        return db.create_game(request.game_id, request.game_date)
    except db.GameAlreadyExistsError:
        # Two concurrent requests can both pass the check above; the
        # PRIMARY KEY is the real guard.
        raise HTTPException(
            status_code=409,
            detail=f"Game '{request.game_id}' already exists.",
        )


@app.get("/games")
def list_games(
    status: Optional[GameStatus] = Query(
        None, description="Filter by game status."
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return {
        "total": db.count_games(status=status),
        "limit": limit,
        "offset": offset,
        "games": db.list_games(status=status, limit=limit, offset=offset),
    }


@app.get(
    "/games/{game_id}",
    response_model=GameWithPredictions,
)
def get_game_with_predictions(game_id: str):
    game = db.get_game(game_id)

    if game is None:
        raise HTTPException(
            status_code=404,
            detail=f"Game '{game_id}' not found.",
        )

    predictions = [
        _prediction_row_to_response(row)
        for row in db.list_predictions_for_game(game_id)
    ]

    return {**game, "predictions": predictions}


@app.patch(
    "/games/{game_id}",
    response_model=GameRecord,
    dependencies=[Depends(require_api_key)],
)
def update_game(game_id: str, request: GameUpdateRequest):

    if db.get_game(game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Game '{game_id}' not found.",
        )

    return db.update_game_date(game_id, request.game_date)


@app.patch(
    "/games/{game_id}/status",
    response_model=GameRecord,
    dependencies=[Depends(require_api_key)],
)
def update_game_status(game_id: str, request: GameStatusUpdateRequest):
    """
    Mark a game as postponed or canceled. Without this, a game that
    never happens just sits forever with unsettled predictions,
    quietly inflating "pending" counts in /performance indefinitely --
    this gives you a way to flag it explicitly instead. Predictions
    already attached to the game are left as-is (not auto-deleted);
    use DELETE /games/{game_id}/predictions/{market_type} or
    DELETE /games/{game_id} if you want those gone too.
    """

    if db.get_game(game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Game '{game_id}' not found.",
        )

    return db.update_game_status(game_id, request.status)


@app.delete(
    "/games/{game_id}/predictions/{market_type}",
    status_code=204,
    dependencies=[Depends(require_api_key)],
)
def delete_prediction(
    game_id: str,
    market_type: str,
    threshold: float = Query(
        NO_THRESHOLD,
        ge=0,
        description=(
            "Required for points/rebounds/assists/both_teams_over; "
            "omit for foul_out/double_double/triple_double."
        ),
    ),
):
    """
    Removes a single prediction without deleting the game or any of its
    other predictions -- DELETE /games/{game_id} removes everything
    attached to a game, which is more than you want if you just logged
    one bad double-double line by mistake.
    """

    if db.get_game(game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Game '{game_id}' not found.",
        )

    deleted = db.delete_prediction(game_id, market_type, threshold)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No prediction found for game '{game_id}', "
                f"market_type '{market_type}', threshold {threshold}."
            ),
        )

    return None


@app.delete(
    "/games/{game_id}",
    status_code=204,
    dependencies=[Depends(require_api_key)],
)
def delete_game(game_id: str):

    deleted = db.delete_game(game_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Game '{game_id}' not found.",
        )

    return None


def _create_foul_prediction_internal(request: FoulPredictionCreateRequest) -> dict:
    """
    Core logic shared by POST /predictions and POST /predictions/batch.
    Raises HTTPException on failure (missing game); returns the raw
    prediction row dict (not yet converted to the response shape).
    """
    if db.get_game(request.game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Game '{request.game_id}' not found. "
                "Create it via POST /games first."
            ),
        )

    priced = calculate_price(
        pf=request.pf,
        american_odds=request.american_odds,
        safety_margin_pp=request.safety_margin_pp,
    )

    fields = {
        "combined_probability": priced["calibrated_probability"],
        "combined_fair_odds": priced["fair_american_odds"],
        "market_odds": priced["market_american_odds"],
        "market_break_even": priced["market_break_even_probability"],
        "probability_edge": priced["probability_edge"],
        "expected_roi": priced["expected_roi"],
        "safety_margin": priced["safety_margin_pp"],
        "required_probability": priced["required_probability"],
        "status": priced["status"],
        "details": json.dumps(
            {"pf": priced["pf"], "extrapolated": priced["extrapolated"]}
        ),
        "model_version": priced["model_version"],
    }

    return db.upsert_prediction(request.game_id, "foul_out", NO_THRESHOLD, fields)


@app.post(
    "/predictions",
    response_model=PredictionRecord,
    dependencies=[Depends(require_api_key)],
)
def create_foul_prediction(request: FoulPredictionCreateRequest):
    """Attach/overwrite a foul-out prediction for a game."""
    row = _create_foul_prediction_internal(request)
    return _prediction_row_to_response(row)


class FoulPredictionBatchRequest(BaseModel):
    predictions: List[FoulPredictionCreateRequest]


@app.post(
    "/predictions/batch",
    dependencies=[Depends(require_api_key)],
)
def create_foul_predictions_batch(request: FoulPredictionBatchRequest):
    """
    Attach/overwrite foul-out predictions for many games in one call.
    Each item is processed independently -- one bad item (e.g. a
    missing game) doesn't abort the rest of the batch. See the response
    shape: a per-item success/error list, not an all-or-nothing result.
    """
    return _process_batch(request.predictions, _create_foul_prediction_internal)


def _compute_market_comparison(
    combined_probability: float,
    market_odds: Optional[int],
    safety_margin_pp: float,
):
    """
    Shared by both player-prediction endpoints: if a market price was
    given to compare against, computes break-even/edge/ROI/status the
    same way calculate_price does for the foul market. Returns a dict
    of Nones if no market_odds was given -- there's nothing to compare
    the model's probability to yet.
    """
    if market_odds is None:
        return {
            "market_break_even": None,
            "probability_edge": None,
            "expected_roi": None,
            "required_probability": None,
            "status": None,
        }

    break_even = market_break_even_probability(market_odds)
    edge = combined_probability - break_even
    expected_roi = calculate_expected_roi(combined_probability, market_odds)
    required_probability = break_even + (safety_margin_pp / 100.0)
    margin_above_requirement = combined_probability - required_probability

    if edge < 0:
        status = "NO_EDGE"
    elif margin_above_requirement < 0:
        status = "FAIR_OR_BORDERLINE"
    else:
        status = "PASS"

    return {
        "market_break_even": round(break_even, 6),
        "probability_edge": round(edge, 6),
        "expected_roi": round(expected_roi, 6),
        "required_probability": round(required_probability, 6),
        "status": status,
    }


def _create_player_threshold_prediction_internal(
    request: PlayerThresholdPredictionCreateRequest,
) -> dict:
    """Core logic shared by the single and batch threshold-prediction endpoints."""

    if db.get_game(request.game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Game '{request.game_id}' not found. "
                "Create it via POST /games first."
            ),
        )

    player_results, combined_probability = _price_threshold_players(
        request.threshold, request.players
    )

    comparison = _compute_market_comparison(
        combined_probability, request.market_odds, request.safety_margin_pp
    )

    fields = {
        "combined_probability": round(combined_probability, 6),
        "combined_fair_odds": probability_to_fair_american(combined_probability),
        "market_odds": request.market_odds,
        "safety_margin": request.safety_margin_pp,
        "details": json.dumps({"players": player_results}),
        "model_version": MODEL_VERSION,
        **comparison,
    }

    return db.upsert_prediction(
        request.game_id, request.market_type, request.threshold, fields
    )


@app.post(
    "/player-predictions/threshold",
    response_model=PredictionRecord,
    dependencies=[Depends(require_api_key)],
)
def create_player_threshold_prediction(
    request: PlayerThresholdPredictionCreateRequest,
):
    """Attach/overwrite a points/rebounds/assists prediction for a game."""
    row = _create_player_threshold_prediction_internal(request)
    return _prediction_row_to_response(row)


class PlayerThresholdPredictionBatchRequest(BaseModel):
    predictions: List[PlayerThresholdPredictionCreateRequest]


@app.post(
    "/player-predictions/threshold/batch",
    dependencies=[Depends(require_api_key)],
)
def create_player_threshold_predictions_batch(
    request: PlayerThresholdPredictionBatchRequest,
):
    """
    Attach/overwrite points/rebounds/assists predictions for many
    games/players in one call. Per-item success/failure, same as
    POST /predictions/batch -- see _process_batch.
    """
    return _process_batch(
        request.predictions, _create_player_threshold_prediction_internal
    )


def _create_player_direct_prediction_internal(
    request: PlayerDirectPredictionCreateRequest,
) -> dict:
    """Core logic shared by the single and batch direct-prediction endpoints."""

    if db.get_game(request.game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Game '{request.game_id}' not found. "
                "Create it via POST /games first."
            ),
        )

    player_results, combined_probability = _price_direct_players(request.players)

    comparison = _compute_market_comparison(
        combined_probability, request.market_odds, request.safety_margin_pp
    )

    fields = {
        "combined_probability": round(combined_probability, 6),
        "combined_fair_odds": probability_to_fair_american(combined_probability),
        "market_odds": request.market_odds,
        "safety_margin": request.safety_margin_pp,
        "details": json.dumps({"players": player_results}),
        "model_version": MODEL_VERSION,
        **comparison,
    }

    return db.upsert_prediction(
        request.game_id, request.market_type, NO_THRESHOLD, fields
    )


@app.post(
    "/player-predictions/direct",
    response_model=PredictionRecord,
    dependencies=[Depends(require_api_key)],
)
def create_player_direct_prediction(
    request: PlayerDirectPredictionCreateRequest,
):
    """Attach/overwrite a double-double/triple-double prediction for a game."""
    row = _create_player_direct_prediction_internal(request)
    return _prediction_row_to_response(row)


class PlayerDirectPredictionBatchRequest(BaseModel):
    predictions: List[PlayerDirectPredictionCreateRequest]


@app.post(
    "/player-predictions/direct/batch",
    dependencies=[Depends(require_api_key)],
)
def create_player_direct_predictions_batch(
    request: PlayerDirectPredictionBatchRequest,
):
    """
    Attach/overwrite double-double/triple-double predictions for many
    games in one call. Per-item success/failure -- see _process_batch.
    """
    return _process_batch(
        request.predictions, _create_player_direct_prediction_internal
    )


def _create_both_teams_over_prediction_internal(
    request: BothTeamsOverPredictionCreateRequest,
) -> dict:
    """Core logic shared by the single and batch both-teams-over endpoints."""

    if db.get_game(request.game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Game '{request.game_id}' not found. "
                "Create it via POST /games first."
            ),
        )

    resolved_threshold_b = (
        request.threshold_b if request.threshold_b is not None else request.threshold_a
    )

    if resolved_threshold_b != request.threshold_a:
        raise HTTPException(
            status_code=400,
            detail=(
                "The ledger stores a single threshold per prediction, so "
                "threshold_a and threshold_b must match to persist this "
                "market. Use POST /team-markets/price (stateless) if you "
                "need differing thresholds without saving it."
            ),
        )

    priced = _price_both_teams_over(
        request.team_a_lines,
        request.team_b_lines,
        request.game_total_lines,
        request.threshold_a,
        resolved_threshold_b,
    )

    joint_probability = priced["joint_probability"]

    comparison = _compute_market_comparison(
        joint_probability, request.market_odds, request.safety_margin_pp
    )

    fields = {
        "combined_probability": round(joint_probability, 6),
        "combined_fair_odds": probability_to_fair_american(joint_probability),
        "market_odds": request.market_odds,
        "safety_margin": request.safety_margin_pp,
        "details": json.dumps(
            {
                "team_a_name": request.team_a_name,
                "team_b_name": request.team_b_name,
                "team_a_mean": priced["team_a_mean"],
                "team_a_std": priced["team_a_std"],
                "team_b_mean": priced["team_b_mean"],
                "team_b_std": priced["team_b_std"],
                "game_total_mean": priced["game_total_mean"],
                "game_total_std": priced["game_total_std"],
                "implied_correlation": priced["implied_correlation"],
                "naive_independent_probability": priced["naive_independent_probability"],
            }
        ),
        "model_version": MODEL_VERSION,
        **comparison,
    }

    return db.upsert_prediction(
        request.game_id, "both_teams_over", request.threshold_a, fields
    )


@app.post(
    "/team-predictions/both-over",
    response_model=PredictionRecord,
    dependencies=[Depends(require_api_key)],
)
def create_both_teams_over_prediction(
    request: BothTeamsOverPredictionCreateRequest,
):
    """
    Attach/overwrite a 'both teams over their own threshold' prediction
    for a game. The ledger stores one threshold per prediction, so this
    requires threshold_a == threshold_b (or threshold_b omitted) --
    /team-markets/price above supports differing thresholds if you need
    that without persisting it.
    """
    row = _create_both_teams_over_prediction_internal(request)
    return _prediction_row_to_response(row)


class BothTeamsOverPredictionBatchRequest(BaseModel):
    predictions: List[BothTeamsOverPredictionCreateRequest]


@app.post(
    "/team-predictions/both-over/batch",
    dependencies=[Depends(require_api_key)],
)
def create_both_teams_over_predictions_batch(
    request: BothTeamsOverPredictionBatchRequest,
):
    """
    Attach/overwrite both-teams-over predictions for many games in one
    call. Per-item success/failure -- see _process_batch.
    """
    return _process_batch(
        request.predictions, _create_both_teams_over_prediction_internal
    )


@app.post(
    "/results",
    response_model=PredictionRecord,
    dependencies=[Depends(require_api_key)],
)
def create_result(request: ResultCreateRequest):

    if db.get_game(request.game_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Game '{request.game_id}' not found.",
        )

    prediction = db.get_prediction(
        request.game_id, request.market_type, request.threshold
    )

    if prediction is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot record a result before a prediction exists for "
                f"this game/market_type/threshold. Call the matching "
                "predictions endpoint first."
            ),
        )

    if prediction["market_odds"] is not None:
        realized_roi = (
            american_to_decimal_profit(prediction["market_odds"])
            if request.actual_result == 1
            else -1.0
        )
    else:
        # No market price was logged with this prediction, so there's
        # nothing to compute a realized ROI against -- still record
        # the outcome itself.
        realized_roi = None

    row = db.update_prediction_result(
        request.game_id,
        request.market_type,
        request.threshold,
        request.actual_result,
        realized_roi,
    )

    return _prediction_row_to_response(row)


@app.get("/history")
def history(
    market_type: Optional[MarketType] = Query(
        None, description="Filter by market type."
    ),
    status: Optional[PredictionStatus] = Query(
        None, description="Filter by pricing status."
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    predictions = db.list_predictions(
        market_type=market_type, status=status, limit=limit, offset=offset
    )
    total = db.count_predictions(market_type=market_type, status=status)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "predictions": [
            _prediction_row_to_response(row) for row in predictions
        ],
    }


@app.get("/performance")
def performance(
    market_type: Optional[MarketType] = Query(None, description="Filter by market type."),
):
    return db.performance_summary(market_type=market_type)


@app.get("/calibration-report")
def calibration_report(
    market_type: Optional[MarketType] = Query(None, description="Filter by market type."),
    bucket_width: float = Query(
        0.1,
        gt=0.0,
        le=1.0,
        description="Width of each predicted-probability bucket (e.g. 0.1 = 10%).",
    ),
):
    return db.calibration_report(market_type=market_type, bucket_width=bucket_width)


@app.get("/roi-report")
def roi_report(
    market_type: Optional[MarketType] = Query(None, description="Filter by market type."),
):
    return db.roi_report(market_type=market_type)


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
