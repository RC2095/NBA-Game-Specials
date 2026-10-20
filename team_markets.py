"""
Team-level joint-probability markets: "Both Team A and Team B exceed
threshold X" (e.g. "both teams 100+ through 3 quarters").

This is a structurally different problem from everything else in the
API. Every other market here is an "at least one of many" event (any
player hits a milestone). This one is "both of exactly two" (an AND),
and the two events are NOT independent: a fast-paced, whistle-heavy
game lifts both teams' scoring together, so naively multiplying each
team's standalone probability understates how often both actually hit
the mark -- the same category of mistake flagged for double-double/
triple-double, but here it's solvable with real market data instead of
requiring a direct line, because team point totals are (approximately)
continuous enough to model as normal distributions and because the
market publishes three numbers that are mathematically related:

    Var(Team A + Team B) = Var(Team A) + Var(Team B) + 2*Cov(A, B)

If a team's own total is quoted at enough alt lines to fit both a mean
AND a standard deviation (not just one point), and the SAME is true for
the combined game total, the equation above has exactly one unknown
left: the covariance between the two teams' scoring in this specific
matchup. That covariance is not an assumption -- it is backed out of
market data the book already published, which is a meaningfully
stronger position than guessing a correlation coefficient.

KEY ASSUMPTION, stated plainly: team point totals are treated as
normally distributed. This is a standard, defensible approximation in
sports modeling for a continuous-ish, roughly symmetric quantity like a
team's quarter/game point total -- but it is an approximation, not
measured fact, same spirit as the decay-curve extrapolation elsewhere
in this codebase.

DATA REQUIREMENT, stated plainly: fitting a mean AND a standard
deviation from a ladder needs at least two alt-line quotes for that
total (team A's, team B's, and the combined game total each need >= 2
points). A single O/U line only pins down the mean, not the spread --
same limitation as player point ladders.
"""

import math
from typing import List, Tuple

from player_markets import LadderPoint


# ============================================================
# NORMAL DISTRIBUTION HELPERS (stdlib only -- no scipy/numpy)
# ============================================================

def norm_cdf(x: float) -> float:
    """Standard normal CDF, exact via math.erf (not an approximation)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def norm_ppf(p: float) -> float:
    """
    Inverse standard normal CDF (probit function). Peter Acklam's
    rational approximation, refined with one Halley's-method step using
    the exact erf-based CDF above. Accurate to roughly 1e-9, verified
    directly against reference values and round-trip tested against
    norm_cdf.
    """
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return float("inf")

    a = [
        -3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
        1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00,
    ]
    b = [
        -5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
        6.680131188771972e+01, -1.328068155288572e+01,
    ]
    c = [
        -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
        -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00,
    ]
    d = [
        7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
        3.754408661907416e+00,
    ]

    p_low = 0.02425
    p_high = 1 - p_low

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        )
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        )

    # One Halley refinement step for extra precision.
    e = norm_cdf(x) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def bivariate_normal_cdf(
    za: float,
    zb: float,
    rho: float,
    n_steps: int = 2000,
    lower: float = -8.0,
) -> float:
    """
    P(Z_a <= za, Z_b <= zb) for a standard bivariate normal with
    correlation rho, via the standard reduction to a 1D integral:

        BVN(za, zb, rho) = integral_{-inf}^{za}
                                phi(x) * Phi((zb - rho*x) / sqrt(1-rho^2)) dx

    computed with Simpson's rule. Verified against the exact closed-form
    identity BVN(0, 0, rho) = 1/4 + arcsin(rho)/(2*pi) to ~1e-13, and
    against the independence case (rho=0 reduces to Phi(za)*Phi(zb)) and
    the perfect-correlation limit (rho->1 reduces to min(Phi(za),Phi(zb))).

    Computed symmetrically: the same integral can be written with
    either za or zb as the outer bound of integration (mathematically
    identical), but fixed-step Simpson's rule accumulates different
    error depending on which one is chosen, especially as |rho| -> 1.
    Found via stress testing: at rho=-0.999999, computing it the two
    equally-valid ways disagreed by ~4e-4 -- averaging both orderings
    cancels most of this directional bias, which is why this function
    computes it both ways internally rather than picking one arbitrarily.

    rho is clamped away from exactly +/-1 for numerical stability.
    """
    rho = max(-0.999999, min(0.999999, rho))

    result_za_outer = _bivariate_normal_cdf_raw(za, zb, rho, n_steps, lower)
    result_zb_outer = _bivariate_normal_cdf_raw(zb, za, rho, n_steps, lower)
    result = (result_za_outer + result_zb_outer) / 2.0

    # Fixed-step Simpson's rule loses accuracy as |rho| -> 1 (the
    # integrand becomes a near-step function), which can still push the
    # averaged result slightly outside [0, 1] -- a CDF can never
    # actually do that, so clamp rather than let a nonsensical value
    # propagate.
    return max(0.0, min(1.0, result))


def _bivariate_normal_cdf_raw(
    za: float,
    zb: float,
    rho: float,
    n_steps: int,
    lower: float,
) -> float:
    """
    One-directional integration with za as the outer bound. Not
    symmetric on its own -- see bivariate_normal_cdf, which averages
    this against the zb-outer ordering to cancel directional bias.
    """
    denom = math.sqrt(1 - rho * rho)

    def integrand(x: float) -> float:
        return norm_pdf(x) * norm_cdf((zb - rho * x) / denom)

    if za <= lower:
        return 0.0

    h = (za - lower) / n_steps
    total = integrand(lower) + integrand(za)
    for i in range(1, n_steps):
        x = lower + i * h
        total += integrand(x) * (4 if i % 2 == 1 else 2)

    return total * h / 3.0


# ============================================================
# FITTING A TEAM TOTAL'S DISTRIBUTION FROM ITS ALT-LINE LADDER
# ============================================================

def fit_normal_from_ladder(ladder: List[LadderPoint]) -> Tuple[float, float]:
    """
    Fits a normal distribution's (mean, std) to a de-vigged alt-line
    ladder (team total, game total -- same LadderPoint shape as player
    markets). Requires at least 2 points: a single line only pins down
    where the distribution's median sits, not its spread.

    The fit works by noting that if X ~ Normal(mean, std), then
    P(X > line) = p implies line = mean + std * Phi^-1(1-p), which is
    linear in Phi^-1(1-p) -- so this reduces to ordinary least-squares
    linear regression of each line against its implied z-score.

    Verified to recover known (mean, std) exactly from synthetic data
    generated from a normal distribution.
    """
    for point in ladder:
        # P(over) must be strictly inside (0, 1): 0 or 1 map to infinite
        # z-scores, and NaN poisons the whole regression.
        if not (math.isfinite(point.line) and 0.0 < point.probability_over < 1.0):
            raise ValueError(
                f"Ladder point (line={point.line}, probability_over="
                f"{point.probability_over}) is invalid: the line must be "
                "finite and the probability strictly between 0 and 1."
            )

    if len(ladder) < 2:
        raise ValueError(
            "Need at least 2 alt-line points to fit a standard deviation "
            "(one line only pins down the mean). If you only have a "
            "single line, supply an assumed std directly instead of "
            "using this function."
        )

    z_scores = [norm_ppf(1.0 - point.probability_over) for point in ladder]
    lines = [point.line for point in ladder]

    n = len(ladder)
    mean_z = sum(z_scores) / n
    mean_line = sum(lines) / n

    covariance = sum(
        (z - mean_z) * (line - mean_line) for z, line in zip(z_scores, lines)
    ) / n
    variance_z = sum((z - mean_z) ** 2 for z in z_scores) / n

    if variance_z == 0.0:
        raise ValueError(
            "All ladder points produced the same z-score -- can't fit a "
            "standard deviation from degenerate data."
        )

    std = covariance / variance_z
    mean = mean_line - std * mean_z

    if std <= 0.0:
        raise ValueError(
            f"Fitted a non-positive standard deviation ({std:.4f}) from "
            "this ladder, which means the input data is inconsistent -- "
            "a real distribution's Over-probability must decrease as the "
            "line increases. This usually means the lines were pulled at "
            "different times (stale/misaligned), entered in the wrong "
            "order, or have a data error. Check the raw (line, over_odds, "
            "under_odds) inputs before trusting this fit."
        )

    return mean, std


# ============================================================
# CORRELATION FROM MARKET-IMPLIED VARIANCES
# ============================================================

def implied_correlation_from_variances(
    var_a: float,
    var_b: float,
    var_total: float,
) -> float:
    """
    Backs out the correlation between two teams' scoring from three
    market-implied variances via:

        Cov(A, B) = (Var(A+B) - Var(A) - Var(B)) / 2
        rho = Cov(A, B) / sqrt(Var(A) * Var(B))

    Verified to recover an exact known rho from constructed variances.
    Clamped to [-1, 1]; a value needing clamping signals the three
    market lines are mutually inconsistent (a real possibility --
    different lines can come from different models or move at different
    times), which is worth a sanity check rather than silently trusting.
    """
    if not all(math.isfinite(v) for v in (var_a, var_b, var_total)):
        raise ValueError("Variances must be finite numbers.")
    if var_a <= 0.0 or var_b <= 0.0:
        raise ValueError(
            f"var_a and var_b must be positive (got {var_a}, {var_b})."
        )

    covariance = (var_total - var_a - var_b) / 2.0
    rho = covariance / math.sqrt(var_a * var_b)
    return max(-1.0, min(1.0, rho))


# ============================================================
# THE JOINT PROBABILITY ITSELF
# ============================================================

def joint_probability_both_exceed(
    mean_a: float,
    std_a: float,
    threshold_a: float,
    mean_b: float,
    std_b: float,
    threshold_b: float,
    rho: float,
) -> float:
    """
    P(Team A >= threshold_a AND Team B >= threshold_b) under a bivariate
    normal model with the given correlation.

    Verified: rho=0 matches naive independent multiplication exactly;
    increasing rho strictly increases the joint probability, matching
    the intuition that a fast game lifts both teams together.
    """
    inputs = (mean_a, std_a, threshold_a, mean_b, std_b, threshold_b, rho)
    if not all(math.isfinite(v) for v in inputs):
        # A NaN rho in particular used to be silently clamped to a
        # near-perfect correlation by the min/max in bivariate_normal_cdf.
        raise ValueError(
            f"All inputs must be finite numbers (got mean_a={mean_a}, "
            f"std_a={std_a}, threshold_a={threshold_a}, mean_b={mean_b}, "
            f"std_b={std_b}, threshold_b={threshold_b}, rho={rho})."
        )

    if std_a <= 0.0 or std_b <= 0.0:
        raise ValueError(
            f"std_a and std_b must both be positive (got std_a={std_a}, "
            f"std_b={std_b}). A zero or negative standard deviation isn't "
            "a valid distribution -- this function doesn't go through "
            "fit_normal_from_ladder's own validation if called directly "
            "with explicit values, so it checks here too."
        )

    za = (threshold_a - mean_a) / std_a
    zb = (threshold_b - mean_b) / std_b

    probability = (
        1.0
        - norm_cdf(za)
        - norm_cdf(zb)
        + bivariate_normal_cdf(za, zb, rho)
    )

    # Numerical integration accumulates error at extreme |rho|, found
    # via stress testing in two forms: results slightly below 0, and
    # results slightly ABOVE the true Frechet upper bound min(P(A),P(B))
    # (a plain [0,1] clamp catches the first but not the second). The
    # Frechet bounds are exact and cheap to compute here, so clamp to
    # them directly rather than the looser [0,1] range -- this is a
    # mathematically guaranteed bound, not a heuristic one.
    p_a_alone = 1.0 - norm_cdf(za)
    p_b_alone = 1.0 - norm_cdf(zb)
    lower_bound = max(0.0, p_a_alone + p_b_alone - 1.0)
    upper_bound = min(p_a_alone, p_b_alone)

    return max(lower_bound, min(upper_bound, probability))


def price_both_teams_over(
    team_a_ladder: List[LadderPoint],
    team_b_ladder: List[LadderPoint],
    game_total_ladder: List[LadderPoint],
    threshold_a: float,
    threshold_b: float,
) -> dict:
    """
    End-to-end convenience function: fits each team's and the combined
    game total's distribution from their alt-line ladders, derives the
    implied correlation, and returns the joint probability plus every
    intermediate number for transparency/debugging.

    Each ladder needs >= 2 points (see fit_normal_from_ladder).
    """
    mean_a, std_a = fit_normal_from_ladder(team_a_ladder)
    mean_b, std_b = fit_normal_from_ladder(team_b_ladder)
    mean_total, std_total = fit_normal_from_ladder(game_total_ladder)

    rho = implied_correlation_from_variances(
        std_a ** 2, std_b ** 2, std_total ** 2
    )

    probability = joint_probability_both_exceed(
        mean_a, std_a, threshold_a, mean_b, std_b, threshold_b, rho
    )

    return {
        "team_a_mean": mean_a,
        "team_a_std": std_a,
        "team_b_mean": mean_b,
        "team_b_std": std_b,
        "game_total_mean": mean_total,
        "game_total_std": std_total,
        "implied_correlation": rho,
        "joint_probability": probability,
    }


# ============================================================
# SPREAD + TOTAL -> IMPLIED TEAM MEANS (fallback when a team doesn't
# have its own quoted total for this specific window)
# ============================================================

def implied_team_means_from_spread_and_total(
    total_mean: float,
    margin_for_favorite: float,
    period_fraction: float = 1.0,
) -> Tuple[float, float]:
    """
    Derives each team's implied mean total from the game total and the
    point spread, using:

        favorite_mean = (total + margin) / 2
        underdog_mean = (total - margin) / 2

    Useful specifically when a team-level total isn't quoted for the
    window you're pricing (e.g. a book offers a full-game spread and
    total but no "through 3 quarters" team totals) -- the spread tells
    you how the total splits between the two teams, which a team-level
    O/U line would otherwise tell you directly.

    IMPORTANT: this only recovers each team's MEAN, not their standard
    deviation or the correlation between them -- it is not a substitute
    for fit_normal_from_ladder / implied_correlation_from_variances,
    which still need genuine alt-line data to produce a std.

    period_fraction: if total_mean and margin_for_favorite are full-game
    numbers but you're pricing a partial-game window (e.g. through Q3),
    pass the fraction of scoring assumed to occur in that window (e.g.
    0.75). THIS IS A REAL ASSUMPTION, not a measured fact -- NBA fourth
    quarters often run at a different pace than the first three (close
    games are played out fully, blowouts see bench minutes and a slower
    pace), so a flat proportional scaling is an approximation. Leave at
    1.0 if total_mean/margin_for_favorite are already quoted for the
    exact window you're pricing.
    """
    scaled_total = total_mean * period_fraction
    scaled_margin = margin_for_favorite * period_fraction

    mean_favorite = (scaled_total + scaled_margin) / 2.0
    mean_underdog = (scaled_total - scaled_margin) / 2.0

    return mean_favorite, mean_underdog


# ============================================================
# "REACH" MARKETS: "Team scores 100+ points" (>= 100, not > 100)
# ============================================================
#
# The functions above treat a threshold as a raw cutoff: P(score > cutoff).
# A market called "100+" means 100 OR MORE. Points are whole numbers, so
# "100 or more" is "more than 99.5" -- the same half-point convention the
# player markets use for "25+". Skipping it shifts every probability by
# roughly half a point of scoring (about 1-2 percentage points near the
# middle of the distribution). The helpers below apply it so callers can
# speak in the book's own terms.

def reach_to_cutoff(reach: float) -> float:
    """'X+' -> the cutoff the normal model should use (X - 0.5)."""
    if not math.isfinite(reach):
        raise ValueError(f"Threshold must be a finite number (got {reach}).")
    return reach - 0.5


def team_reach_probability(mean: float, std: float, reach: float) -> float:
    """P(team scores `reach` or more) for a normal(mean, std) team total."""
    if not (math.isfinite(mean) and math.isfinite(std)):
        raise ValueError("mean and std must be finite numbers.")
    if std <= 0.0:
        raise ValueError(f"std must be positive (got {std}).")
    return 1.0 - norm_cdf((reach_to_cutoff(reach) - mean) / std)


def mean_from_single_line(
    line: float,
    over_odds: int,
    under_odds: int,
    std: float,
) -> float:
    """
    Mean of a normal team total with a GIVEN std, pinned so that
    P(total > line) equals the de-vigged Over probability. For when a book
    posts only its main team-total line: one line fixes where the
    distribution sits but not how wide it is, so the std is an assumption
    the caller supplies.
    """
    # Local import: player_markets is already imported above, this just
    # keeps the dependency visible next to its only use.
    from player_markets import devig_two_way

    if not math.isfinite(line):
        raise ValueError(f"Line must be a finite number (got {line}).")
    if not math.isfinite(std) or std <= 0.0:
        raise ValueError(f"std must be a positive finite number (got {std}).")
    p_over = devig_two_way(over_odds, under_odds)[0]
    return line - std * norm_ppf(1.0 - p_over)


def price_both_teams_reach(
    mean_a: float,
    std_a: float,
    reach_a: float,
    mean_b: float,
    std_b: float,
    reach_b: float,
    rho: float,
) -> dict:
    """
    P(A reaches reach_a AND B reaches reach_b), with the independent
    product returned alongside so the effect of correlation is visible.
    """
    joint = joint_probability_both_exceed(
        mean_a, std_a, reach_to_cutoff(reach_a),
        mean_b, std_b, reach_to_cutoff(reach_b),
        rho,
    )
    p_a = team_reach_probability(mean_a, std_a, reach_a)
    p_b = team_reach_probability(mean_b, std_b, reach_b)
    return {
        "probability_a": p_a,
        "probability_b": p_b,
        "joint_probability": joint,
        "independent_probability": p_a * p_b,
    }
