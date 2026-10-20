"""
Player-prop pricing engine for "any player to hit X" markets:
    - Points thresholds (25+ / 30+ / 35+ / 40+ / 50+)
    - Rebounds threshold (20+)
    - Assists threshold (15+)
    - Double-double / triple-double (direct Yes/No per player)

Design, per the accompanying conversation:
    - Threshold markets (points/rebounds/assists) are priced from each
      player's own Over/Under alt-line ladder, not from a single
      game-level proxy like the foul model uses. Each line is de-vigged
      individually, then interpolated/extrapolated to the exact
      milestone the same way the foul model interpolates PF.
    - Double-double/triple-double are priced directly from each player's
      own Yes/No line (or Yes/No two-way market if both sides are
      posted), never assembled from points/rebounds/assists components,
      because the stats are positively correlated within a player and
      naive combination would understate the true probability.
    - "Any player" probability across a roster combines each player's
      independent Yes/No event as 1 - product(1 - p_i).

This module is intentionally provider-agnostic: it operates on plain
ladders/odds you already have, however they were obtained. There is no
live odds feed; prices are pasted in.
"""

from typing import List, NamedTuple, Optional, Tuple
import math


# ============================================================
# ODDS CONVERSION
# ============================================================

def implied_probability(american_odds: int) -> float:
    """
    Raw (vig-inclusive) implied probability from a single American odds
    price. Same conversion as main.py's market_break_even_probability,
    kept local here so this module has no dependency on main.py.
    """
    # Real American odds are +100 or higher, or -100 or lower. Anything
    # in between (+5, -50, ...) isn't a real price and is almost always
    # a typo -- and it would otherwise be priced silently as if it were
    # real (+5 -> 95% implied probability, -50 -> 33%).
    if not math.isfinite(american_odds) or abs(american_odds) < 100:
        raise ValueError(
            f"American odds must be +100 or higher, or -100 or lower "
            f"(got {american_odds}); values in between aren't real prices "
            "and are almost always a typo."
        )

    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)

    return abs(american_odds) / (abs(american_odds) + 100.0)


def devig_two_way(over_odds: int, under_odds: int) -> Tuple[float, float]:
    """
    Given a two-way Over/Under market's odds, strip the vig and return
    (p_over, p_under) that sum to exactly 1.0.

    Example (Angel Reese O/U 15.5 points, -104 / -125):
        devig_two_way(-104, -125) -> (0.47857, 0.52143)
    """
    p_over_raw = implied_probability(over_odds)
    p_under_raw = implied_probability(under_odds)
    total = p_over_raw + p_under_raw

    return p_over_raw / total, p_under_raw / total


# ============================================================
# LADDER CONSTRUCTION AND INTERPOLATION
# ============================================================

# With only ONE quoted line there is no second point to estimate how fast
# the probability falls off, so any estimate away from that line is a
# guess. Carrying the single probability forward "flat" used to be the
# fallback, and it is badly wrong at distance: one Over 4.5 line (~47%)
# priced "25+ points" at 47%, and a 14-player roster combined to 99.99%.
# Beyond this gap (in stat units) a single line is refused instead.
MAX_SINGLE_LINE_GAP = 2.0

class LadderPoint(NamedTuple):
    line: float  # e.g. 24.5, meaning "scores 25 or more"
    probability_over: float  # de-vigged P(stat > line)


def _exponential_extrapolate(
    x_low: float,
    y_low: float,
    x_high: float,
    y_high: float,
    target_line: float,
) -> float:
    """
    Extrapolate beyond the two given ladder points along a log-linear
    (exponential decay) curve: ln(probability) is treated as linear in
    the line value, rather than probability itself being linear.

    This is the fix for a real failure mode found while testing: plain
    linear extrapolation can cross zero and clamp to exactly 0% for a
    threshold far past the observed range (e.g. pricing "50+ points"
    when the widest alt line offered is 29.5) -- clearly wrong, since
    there's always some nonzero chance. Exponential decay stays
    strictly positive by construction and tapers toward zero smoothly
    instead, which matches how scoring-distribution tails actually
    behave far better than a straight line does.

    Requires x_low < x_high. Works for extrapolating in either
    direction (target_line below x_low or above x_high) since both
    points define a single log-linear line.
    """
    epsilon = 1e-6
    y_low = max(y_low, epsilon)
    y_high = max(y_high, epsilon)

    slope = (math.log(y_high) - math.log(y_low)) / (x_high - x_low)

    # Probability should never increase as the line increases. Guard
    # against a positive slope from noisy/inverted input (e.g. bad
    # data) by falling back to flat (no assumed decay) rather than
    # implying the tail grows as the threshold moves further away.
    slope = min(slope, 0.0)

    if target_line >= x_high:
        anchor_x, anchor_y = x_high, y_high
    else:
        anchor_x, anchor_y = x_low, y_low

    log_probability = math.log(anchor_y) + slope * (target_line - anchor_x)
    probability = math.exp(log_probability)

    return max(0.0, min(1.0, probability))


def build_ladder(
    lines: List[Tuple[float, int, int]],
) -> List[LadderPoint]:
    """
    Converts a player's raw alt-line quotes into a sorted, de-vigged
    ladder.

    lines: list of (line_value, over_odds, under_odds) tuples, e.g.
        [(14.5, -110, -110), (19.5, +150, -190), (24.5, +350, -450)]

    Returns a list of LadderPoint sorted by ascending line, each with
    the de-vigged probability of going Over that line.
    """
    for line, _, _ in lines:
        # A NaN line used to be accepted here and then silently produced a
        # 100% probability downstream (reproduced before this check).
        if not math.isfinite(line):
            raise ValueError(f"Line must be a finite number (got {line}).")

    ladder = [
        LadderPoint(line=line, probability_over=devig_two_way(over_odds, under_odds)[0])
        for line, over_odds, under_odds in lines
    ]

    return sorted(ladder, key=lambda point: point.line)


def _check_single_line_gap(line: float, target_line: float, threshold: float) -> None:
    if abs(target_line - line) > MAX_SINGLE_LINE_GAP:
        raise ValueError(
            f"Only one line ({line}) was provided, which is too far from the "
            f"{threshold:g}+ threshold to estimate (limit: "
            f"{MAX_SINGLE_LINE_GAP:g} away). Add at least one more line, "
            "ideally one at or near the threshold, or use the milestone "
            "price directly."
        )


def probability_at_threshold(
    ladder: List[LadderPoint],
    threshold: float,
) -> Tuple[float, bool]:
    """
    Given a player's de-vigged ladder, return P(stat >= threshold).

    A milestone like "25+ points" corresponds to the Over 24.5 line
    (books use half-point lines to avoid pushes), so threshold is
    converted to target_line = threshold - 0.5 before looking it up.

    Interpolates linearly between the two nearest ladder points if
    target_line isn't an exact match. Extrapolates using an exponential
    decay curve (see _exponential_extrapolate) if target_line falls
    outside the observed range, which keeps the result strictly
    positive rather than risking a linear extrapolation crossing zero.
    With only one observed line, there's no second point to fit a decay
    rate from, so extrapolation falls back to carrying that single
    probability forward flat. The `extrapolated` flag tells you when
    any of this is happening, since it's a real assumption standing in
    for data the book didn't quote.

    Returns (probability, extrapolated).
    """
    if not ladder:
        raise ValueError(
            "Ladder is empty — no alt lines available for this player."
        )

    if not math.isfinite(threshold):
        raise ValueError(f"Threshold must be a finite number (got {threshold}).")

    target_line = threshold - 0.5
    lines = [point.line for point in ladder]
    probs = [point.probability_over for point in ladder]

    # Below the lowest observed line
    if target_line < lines[0]:
        if len(lines) == 1:
            _check_single_line_gap(lines[0], target_line, threshold)
            return max(0.0, min(1.0, probs[0])), True
        probability = _exponential_extrapolate(
            lines[0], probs[0], lines[1], probs[1], target_line
        )
        return probability, True

    # Above the highest observed line
    if target_line > lines[-1]:
        if len(lines) == 1:
            _check_single_line_gap(lines[0], target_line, threshold)
            return max(0.0, min(1.0, probs[-1])), True
        probability = _exponential_extrapolate(
            lines[-2], probs[-2], lines[-1], probs[-1], target_line
        )
        return probability, True

    # Exact match
    for point in ladder:
        if point.line == target_line:
            return point.probability_over, False

    # Interpolate between the two bracketing points
    for i in range(len(lines) - 1):
        if lines[i] < target_line < lines[i + 1]:
            x1, y1 = lines[i], probs[i]
            x2, y2 = lines[i + 1], probs[i + 1]
            probability = y1 + (target_line - x1) * (y2 - y1) / (x2 - x1)
            return max(0.0, min(1.0, probability)), False

    # Should be unreachable given the checks above.
    raise RuntimeError("Failed to locate target_line within the ladder.")


# ============================================================
# SINGLE BASE LINE MODEL (points and rebounds)
# ============================================================

# When a player has only ONE quoted Over/Under line (the usual case: the
# book's main line), the shape of the distribution has to be assumed to
# price a far-away milestone like 35+ points or 14+ rebounds. The stat is
# modelled as lognormal, pinned so that P(stat > line) equals the de-vigged
# Over probability, with a log-scale spread that shrinks as the line grows:
#
#     spread = spread_at_anchor * (line / anchor_line) ** -exponent
#
# Each stat has its OWN constants. They were first tried across stats and
# did not transfer: the points constants priced "16+ rebounds" for any
# player at 41.5% against the book's 22.3%.
#
# POINTS: fitted to 23 real sportsbook "To Score X+ Points" prices (25+
# through 50+, one game) and then checked, WITHOUT refitting, against 29
# prices from a second game: median model/book ratio 0.96, 26 of 29 within
# +-20%.
#
# REBOUNDS: fitted to 15 real "To Record X+ Rebounds" prices (12+/14+/16+,
# one game), then checked WITHOUT refitting on 15 prices from a second game:
# median model/book ratio 1.02 (range 0.70-1.30, 10 of 15 within +-20%);
# any-player totals for the listed players within 4% at 12+ and 14+, 10%
# high at 16+.
#
# Limits worth knowing:
#   - two games and one book per stat; rebounds is looser per player than
#     points (the book's own prices were off from the model by up to ~30%);
#   - the book lists only players above roughly a 3% chance, so the check
#     covers listed players. The rest of a roster is the model alone, and
#     for rebounds the any-player total is quite sensitive to the spread
#     constant because of those players;
#   - nothing checks points above 50+, or rebounds above 16+ (the book
#     does not post them);
#   - ASSISTS has no model at all yet and is refused;
#   - the benchmark was the book's own milestone prices, which carry vig
#     (the model runs a few percent below them, as expected).
SINGLE_LINE_MODELS = {
    "points": {
        "anchor_line": 25.0,
        "spread_at_anchor": 0.31,
        "exponent": 0.5,
        "validated_max_threshold": 50.0,
    },
    "rebounds": {
        "anchor_line": 10.0,
        "spread_at_anchor": 0.36,
        "exponent": 0.4,
        "validated_max_threshold": 16.0,
    },
    # Assists: fitted on one game (7 book prices, 10+ to 14+), then checked
    # without refitting on a second game (9 prices): median model/book 1.07,
    # 8 of 9 within 20%. A negative binomial with the same fitting did worse
    # out of sample (6 of 9) and plain Poisson ran far too low in the tails.
    # The book posts nothing above 14+ in either game, so 16+ and up are
    # unchecked.
    "assists": {
        "anchor_line": 5.0,
        "spread_at_anchor": 0.46,
        "exponent": 0.4,
        "validated_max_threshold": 14.0,
    },
}

# Kept for the points model (tests and the JavaScript port read these).
SINGLE_LINE_SPREAD_AT_25 = SINGLE_LINE_MODELS["points"]["spread_at_anchor"]
SINGLE_LINE_SPREAD_EXPONENT = SINGLE_LINE_MODELS["points"]["exponent"]
SINGLE_LINE_VALIDATED_MAX_THRESHOLD = SINGLE_LINE_MODELS["points"]["validated_max_threshold"]


def single_line_probability(
    line: float,
    over_odds: int,
    under_odds: int,
    threshold: float,
    stat: str = "points",
) -> float:
    """
    P(stat >= threshold) for ONE player from their single base Over/Under
    line, using the lognormal model described above. "X+" means strictly
    more than X - 0.5, the same convention as probability_at_threshold.
    Raises ValueError for a stat with no checked model (e.g. assists).
    """
    # Imported here because team_markets imports player_markets at module
    # level; a top-level import in this direction would be circular.
    from team_markets import norm_cdf, norm_ppf

    if stat not in SINGLE_LINE_MODELS:
        raise ValueError(
            f"No base-line model has been checked for '{stat}'. Use milestone "
            "prices or several alt lines per player instead."
        )
    model = SINGLE_LINE_MODELS[stat]

    if not math.isfinite(line) or line <= 0:
        raise ValueError(f"Line must be a positive finite number (got {line}).")
    if not math.isfinite(threshold) or threshold <= 0.5:
        raise ValueError(
            f"Threshold must be a finite number above 0.5 (got {threshold})."
        )

    p_over = devig_two_way(over_odds, under_odds)[0]
    spread = model["spread_at_anchor"] * (line / model["anchor_line"]) ** -model["exponent"]
    mu = math.log(line) - spread * norm_ppf(1.0 - p_over)
    z = (math.log(threshold - 0.5) - mu) / spread

    return 1.0 - norm_cdf(z)


# ============================================================
# DIRECT MARKETS (double-double / triple-double)
# ============================================================

def direct_market_probability(
    yes_odds: int,
    no_odds: Optional[int] = None,
) -> Tuple[float, bool]:
    """
    Probability for a directly-quoted Yes/No market (e.g. a player's
    double-double line), per the decision to price these directly
    rather than assemble them from points/rebounds/assists components.

    If both sides are posted, de-vig them against each other (accurate).
    If only the Yes side is posted (common for milestone-style props),
    fall back to its raw implied probability and flag it as
    non-devigged, since there's no second price to strip vig against.

    Returns (probability, devigged).
    """
    if no_odds is not None:
        p_yes, _ = devig_two_way(yes_odds, no_odds)
        return p_yes, True

    return implied_probability(yes_odds), False


# ============================================================
# COMBINING ACROSS A ROSTER
# ============================================================

def combine_any_player(probabilities: List[float]) -> float:
    """
    P(at least one player hits the milestone) from each player's
    independent probability of hitting it themselves:

        1 - product(1 - p_i)

    Treats each player's event as independent. This is standard and
    reasonable across *different* players (correlation within one
    player's own stat line is the double-double/triple-double problem
    this module handles separately, not a cross-player one).
    """
    if not probabilities:
        return 0.0

    product = 1.0
    for p in probabilities:
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"Probability {p} is out of [0, 1] range.")
        product *= (1.0 - p)

    return 1.0 - product
