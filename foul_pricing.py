"""
Foul-out market pricing: projected total personal fouls (PF) ->
calibrated probability -> fair odds, edge, and expected ROI.

Extracted from main.py so the pricing math is a plain, dependency-free
module: it can be tested in isolation, and used as the reference
implementation that the browser version (site/engine.js) is checked
against. Pure standard library -- no FastAPI import.

IMPORTANT: this implements the calibrated pricing layer only. It does
NOT reconstruct the underlying raw predictive model, because the raw
model coefficients are not available here.
"""

from bisect import bisect_left
from typing import Optional

# Version of the frozen calibration below. Reported on API responses.
MODEL_VERSION = "1.0.0"

# Default safety margin in probability points.
DEFAULT_SAFETY_MARGIN_PP = 2.0


# ============================================================
# CALIBRATION CURVE
# ============================================================

# PF -> calibrated probability
#
# These are the final smooth calibrated values from the model.
#
# For values between points, we use linear interpolation.
# PF 30 to 38 were added from the same calibration formula as 39 to 50
# (logit(calibrated) = -0.30902 + 0.80413 * logit(raw), with
# logit(raw) = -7.13890 + 0.136513 * PF); every row 30 to 50 matches it to
# the 4 decimals stored here.
#
# For values outside the displayed range, we extrapolate using
# the nearest segment, but flag the result as extrapolated.
#
# Production recommendation:
# Prefer keeping production PF values inside this range until
# additional calibration data are available.
# ============================================================

CALIBRATION_TABLE = [
    (30.0, 0.0597),
    (31.0, 0.0662),
    (32.0, 0.0733),
    (33.0, 0.0811),
    (34.0, 0.0897),
    (35.0, 0.0991),
    (36.0, 0.1093),
    (37.0, 0.1205),
    (38.0, 0.1326),
    (39.0, 0.1457),
    (40.0, 0.1599),
    (41.0, 0.1752),
    (42.0, 0.1917),
    (43.0, 0.2093),
    (44.0, 0.2280),
    (45.0, 0.2479),
    (46.0, 0.2689),
    (47.0, 0.2911),
    (48.0, 0.3142),
    (49.0, 0.3383),
    (50.0, 0.3633),
]

PF_POINTS = [x[0] for x in CALIBRATION_TABLE]
PROB_POINTS = [x[1] for x in CALIBRATION_TABLE]


# ============================================================
# CORE MATHEMATICS
# ============================================================

def american_to_decimal_profit(american_odds: int) -> float:
    """
    Returns the net profit multiplier for a $1 stake.

    +400 -> 4.0
    -200 -> 0.5
    """
    if american_odds > 0:
        return american_odds / 100.0

    return 100.0 / abs(american_odds)


def market_break_even_probability(american_odds: int) -> float:
    """
    Convert American odds into break-even probability.

    +400 -> 20.00%
    +500 -> 16.67%
    -200 -> 66.67%
    """

    if american_odds > 0:
        return 100.0 / (american_odds + 100.0)

    return abs(american_odds) / (abs(american_odds) + 100.0)


def probability_to_fair_american(probability: float) -> Optional[int]:
    """
    Convert probability to fair American odds.

    For p < 50%:
        odds = 100 * (1-p) / p

    For p >= 50%, return negative American odds.
    """

    if probability <= 0.0 or probability >= 1.0:
        return None

    if probability < 0.5:
        odds = 100.0 * (1.0 - probability) / probability
        return int(round(odds))

    odds = -100.0 * probability / (1.0 - probability)
    return int(round(odds))


def probability_to_minimum_acceptable_odds(
    probability: float,
) -> Optional[int]:
    """
    Given a probability, calculate the fair American price.

    This is used after subtracting the required safety margin
    from the model probability.
    """

    if probability <= 0.0:
        return None

    if probability >= 1.0:
        return None

    return probability_to_fair_american(probability)


def odds_with_margin(probability: float, margin: float, mode: str = "relative"):
    """
    The price to QUOTE for a market whose fair probability is `probability`.
    The margin is added to the probability, so the quoted odds are shorter
    than fair (worse for whoever takes them).

    mode="relative": margin is a percentage OF the fair probability. The
        model's error is relative (roughly +-15% on a player's price), so
        this scales sensibly from a 60% market to a 1% one.
            fair 10.0%, margin 10% -> quoted 11.0% -> +809 (fair +900)
    mode="pp": margin is added in percentage points.
            fair 10.0%, margin 2pp -> quoted 12.0% -> +733 (fair +900)
        Beware on long shots: 2pp on a 1% market triples the probability.

    Returns a dict with quoted_probability and american_odds, or None if
    there is no valid price (probability outside (0, 1), or the margin
    pushes the quoted probability to 100% or beyond).
    """
    if mode not in ("relative", "pp"):
        raise ValueError(f"mode must be 'relative' or 'pp' (got {mode!r}).")
    if not (margin == margin) or margin in (float("inf"), float("-inf")):
        raise ValueError(f"Margin must be a finite number (got {margin}).")
    if margin < 0:
        raise ValueError(f"Margin cannot be negative (got {margin}).")

    if probability <= 0.0 or probability >= 1.0:
        return None

    if mode == "relative":
        quoted = probability * (1.0 + margin / 100.0)
    else:
        quoted = probability + margin / 100.0

    if quoted >= 1.0:
        return None

    return {
        "quoted_probability": quoted,
        "american_odds": probability_to_fair_american(quoted),
    }


def interpolate_probability(pf: float):
    """
    Interpolate PF -> calibrated probability.

    Returns:
        probability
        extrapolated flag
    """

    # Below calibration range
    if pf < PF_POINTS[0]:
        x1, y1 = CALIBRATION_TABLE[0]
        x2, y2 = CALIBRATION_TABLE[1]

        probability = y1 + (pf - x1) * (y2 - y1) / (x2 - x1)

        return max(0.0, min(1.0, probability)), True

    # Above calibration range
    if pf > PF_POINTS[-1]:
        x1, y1 = CALIBRATION_TABLE[-2]
        x2, y2 = CALIBRATION_TABLE[-1]

        probability = y1 + (pf - x1) * (y2 - y1) / (x2 - x1)

        return max(0.0, min(1.0, probability)), True

    # Exact / interpolated value
    index = bisect_left(PF_POINTS, pf)

    if index == 0:
        return PROB_POINTS[0], False

    if PF_POINTS[index] == pf:
        return PROB_POINTS[index], False

    x1 = PF_POINTS[index - 1]
    y1 = PROB_POINTS[index - 1]

    x2 = PF_POINTS[index]
    y2 = PROB_POINTS[index]

    probability = y1 + (pf - x1) * (y2 - y1) / (x2 - x1)

    return probability, False


def calculate_expected_roi(
    probability: float,
    american_odds: int,
) -> float:
    """
    Model-implied expected ROI per $1 staked.

    Positive odds:
        EV = p * profit - (1-p)

    Example:
        p = .20
        +500
        EV = .20*5 - .80
           = .20
           = +20%
    """

    profit_multiplier = american_to_decimal_profit(american_odds)

    return (
        probability * profit_multiplier
        - (1.0 - probability)
    )


# ============================================================
# PRICING ENGINE
# ============================================================

def calculate_price(
    pf: float,
    american_odds: int,
    safety_margin_pp: float = DEFAULT_SAFETY_MARGIN_PP,
):

    calibrated_probability, extrapolated = interpolate_probability(pf)

    break_even = market_break_even_probability(american_odds)

    probability_edge = calibrated_probability - break_even

    expected_roi = calculate_expected_roi(
        calibrated_probability,
        american_odds,
    )

    safety_margin = safety_margin_pp / 100.0

    # Probability required to qualify.
    required_probability = break_even + safety_margin

    margin_above_requirement = (
        calibrated_probability - required_probability
    )

    # The model probability minus the safety margin gives the
    # maximum acceptable break-even probability.
    #
    # Example:
    # calibrated = 22.8%
    # margin = 2%
    # acceptable break-even <= 20.8%
    #
    # This converts to the minimum acceptable market odds.

    acceptable_break_even = (
        calibrated_probability - safety_margin
    )

    if acceptable_break_even > 0.0:
        minimum_acceptable_odds = (
            probability_to_minimum_acceptable_odds(
                acceptable_break_even
            )
        )
    else:
        minimum_acceptable_odds = None

    # Classification
    if probability_edge < 0:
        status = "NO_EDGE"

    elif margin_above_requirement < 0:
        status = "FAIR_OR_BORDERLINE"

    else:
        status = "PASS"

    fair_odds = probability_to_fair_american(
        calibrated_probability
    )

    return {
        "model_version": MODEL_VERSION,

        "pf": round(pf, 4),

        "calibrated_probability": round(
            calibrated_probability,
            6,
        ),

        "calibrated_probability_percent": round(
            calibrated_probability * 100,
            4,
        ),

        "market_american_odds": american_odds,

        "market_break_even_probability": round(
            break_even,
            6,
        ),

        "market_break_even_probability_percent": round(
            break_even * 100,
            4,
        ),

        "probability_edge": round(
            probability_edge,
            6,
        ),

        "probability_edge_pp": round(
            probability_edge * 100,
            4,
        ),

        "fair_american_odds": fair_odds,

        "expected_roi": round(
            expected_roi,
            6,
        ),

        "expected_roi_percent": round(
            expected_roi * 100,
            4,
        ),

        "safety_margin_pp": safety_margin_pp,

        "required_probability": round(
            required_probability,
            6,
        ),

        "required_probability_percent": round(
            required_probability * 100,
            4,
        ),

        "margin_above_requirement_pp": round(
            margin_above_requirement * 100,
            4,
        ),

        "minimum_acceptable_american_odds":
            minimum_acceptable_odds,

        "status": status,

        "extrapolated": extrapolated,
    }
