import math

import pytest

import team_markets as tm
from player_markets import build_ladder, LadderPoint


# ============================================================
# NORMAL DISTRIBUTION HELPERS
# ============================================================

@pytest.mark.parametrize(
    "p,expected",
    [
        (0.5, 0.0),
        (0.975, 1.959963985),
        (0.025, -1.959963985),
        (0.8413447, 1.0),
        (0.9772499, 2.0),
    ],
)
def test_norm_ppf_matches_reference_values(p, expected):
    assert tm.norm_ppf(p) == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("p", [0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99])
def test_norm_cdf_norm_ppf_roundtrip(p):
    z = tm.norm_ppf(p)
    assert tm.norm_cdf(z) == pytest.approx(p, abs=1e-8)


def test_norm_ppf_boundary_values():
    assert tm.norm_ppf(0.0) == float("-inf")
    assert tm.norm_ppf(1.0) == float("inf")


# ============================================================
# BIVARIATE NORMAL CDF
# ============================================================

@pytest.mark.parametrize("rho", [-0.9, -0.5, -0.2, 0.0, 0.2, 0.5, 0.8, 0.95])
def test_bivariate_normal_cdf_matches_closed_form_identity(rho):
    got = tm.bivariate_normal_cdf(0.0, 0.0, rho)
    expected = 0.25 + math.asin(rho) / (2 * math.pi)
    assert got == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("za,zb", [(0.5, 1.0), (-1.0, 2.0), (1.5, -0.5)])
def test_bivariate_normal_cdf_zero_rho_is_independent(za, zb):
    got = tm.bivariate_normal_cdf(za, zb, 0.0)
    expected = tm.norm_cdf(za) * tm.norm_cdf(zb)
    assert got == pytest.approx(expected, abs=1e-5)


@pytest.mark.parametrize("za,zb", [(0.5, 1.0), (-1.0, 2.0)])
def test_bivariate_normal_cdf_near_perfect_correlation(za, zb):
    got = tm.bivariate_normal_cdf(za, zb, 0.999999)
    expected = min(tm.norm_cdf(za), tm.norm_cdf(zb))
    assert got == pytest.approx(expected, abs=1e-3)


# ============================================================
# FITTING A NORMAL DISTRIBUTION FROM A LADDER
# ============================================================

def _synthetic_ladder(mean, std, lines):
    points = []
    for line in lines:
        p_over = 1 - tm.norm_cdf((line - mean) / std)
        points.append(LadderPoint(line=line, probability_over=p_over))
    return points


def test_fit_normal_from_ladder_recovers_known_parameters():
    true_mean, true_std = 102.0, 13.5
    ladder = _synthetic_ladder(true_mean, true_std, [89.5, 94.5, 99.5, 104.5, 109.5, 114.5])

    fitted_mean, fitted_std = tm.fit_normal_from_ladder(ladder)

    assert fitted_mean == pytest.approx(true_mean, abs=0.01)
    assert fitted_std == pytest.approx(true_std, abs=0.01)


def test_fit_normal_from_ladder_exact_two_points():
    true_mean, true_std = 50.0, 8.0
    ladder = _synthetic_ladder(true_mean, true_std, [45.5, 54.5])

    fitted_mean, fitted_std = tm.fit_normal_from_ladder(ladder)

    assert fitted_mean == pytest.approx(true_mean, abs=0.01)
    assert fitted_std == pytest.approx(true_std, abs=0.01)


def test_fit_normal_from_ladder_requires_at_least_two_points():
    single_point = [LadderPoint(line=99.5, probability_over=0.5)]
    with pytest.raises(ValueError):
        tm.fit_normal_from_ladder(single_point)


def test_fit_normal_from_ladder_rejects_empty():
    with pytest.raises(ValueError):
        tm.fit_normal_from_ladder([])


def test_fit_normal_from_ladder_rejects_non_decreasing_probabilities():
    """
    Regression test for a real bug found via stress testing: if a
    ladder's probabilities don't properly decrease as the line
    increases (stale/misaligned lines, data entry error), the least-
    squares fit can silently produce a NEGATIVE standard deviation,
    which would then silently corrupt every downstream calculation
    (flipped z-scores) with no error raised. This must be rejected
    loudly instead.
    """
    bad_ladder = [
        LadderPoint(line=95.5, probability_over=0.30),
        LadderPoint(line=105.5, probability_over=0.55),
    ]
    with pytest.raises(ValueError):
        tm.fit_normal_from_ladder(bad_ladder)


# ============================================================
# CORRELATION FROM VARIANCES
# ============================================================

def test_implied_correlation_recovers_known_rho():
    true_rho = 0.35
    std_a, std_b = 12.0, 11.0
    var_a, var_b = std_a**2, std_b**2
    var_total = var_a + var_b + 2 * true_rho * std_a * std_b

    recovered = tm.implied_correlation_from_variances(var_a, var_b, var_total)

    assert recovered == pytest.approx(true_rho, abs=1e-9)


def test_implied_correlation_zero_when_variance_simply_adds():
    var_a, var_b = 100.0, 121.0
    var_total = var_a + var_b
    assert tm.implied_correlation_from_variances(var_a, var_b, var_total) == pytest.approx(0.0, abs=1e-9)


def test_implied_correlation_clamped_to_valid_range():
    too_high = tm.implied_correlation_from_variances(1.0, 1.0, 100.0)
    too_low = tm.implied_correlation_from_variances(1.0, 1.0, -100.0)
    assert too_high == 1.0
    assert too_low == -1.0


# ============================================================
# JOINT PROBABILITY (THE CORE DELIVERABLE)
# ============================================================

def test_joint_probability_zero_rho_matches_naive_independence():
    mean_a, std_a, threshold_a = 100.0, 12.0, 100.0
    mean_b, std_b, threshold_b = 95.0, 11.0, 100.0

    joint = tm.joint_probability_both_exceed(
        mean_a, std_a, threshold_a, mean_b, std_b, threshold_b, rho=0.0
    )

    p_a = 1 - tm.norm_cdf((threshold_a - mean_a) / std_a)
    p_b = 1 - tm.norm_cdf((threshold_b - mean_b) / std_b)

    assert joint == pytest.approx(p_a * p_b, abs=1e-6)


def test_joint_probability_increases_with_correlation():
    mean_a, std_a, threshold_a = 100.0, 12.0, 100.0
    mean_b, std_b, threshold_b = 95.0, 11.0, 100.0

    values = [
        tm.joint_probability_both_exceed(
            mean_a, std_a, threshold_a, mean_b, std_b, threshold_b, rho
        )
        for rho in (0.0, 0.2, 0.4, 0.6)
    ]

    assert all(values[i] < values[i + 1] for i in range(len(values) - 1))


def test_joint_probability_in_valid_range():
    probability = tm.joint_probability_both_exceed(
        100.0, 15.0, 100.0, 95.0, 14.0, 100.0, rho=0.5
    )
    assert 0.0 <= probability <= 1.0


@pytest.mark.parametrize("bad_std_a,bad_std_b", [(0.0, 10.0), (-5.0, 10.0), (10.0, 0.0), (10.0, -5.0)])
def test_joint_probability_rejects_non_positive_std(bad_std_a, bad_std_b):
    """
    Regression test for a real bug found via stress testing: calling
    this function directly with std=0 (bypassing fit_normal_from_ladder,
    which has its own guard) crashed with a bare, unhelpful
    ZeroDivisionError instead of a clear validation error. This matters
    for deployment specifically -- an unhandled exception here would
    surface as an ugly 500 with a traceback if this is ever called with
    a caller-supplied std rather than one derived from a ladder fit.
    """
    with pytest.raises(ValueError):
        tm.joint_probability_both_exceed(
            100.0, bad_std_a, 105.0, 95.0, bad_std_b, 100.0, rho=0.3
        )


@pytest.mark.parametrize(
    "rho",
    [-0.999999, -0.99999, -0.9999, -0.999, -0.99, -0.9, -0.5, 0.0, 0.5, 0.9, 0.999, 0.9999, 0.99999, 0.999999],
)
def test_joint_probability_symmetric_at_extreme_correlation(rho):
    """
    Regression test for a real bug found via stress testing: the same
    mathematical quantity, computed by swapping which team is "A" and
    which is "B", disagreed by ~4e-4 at rho=-0.999999 before this was
    fixed by averaging both integration orderings. Must be exactly
    symmetric (to floating-point precision) at every correlation,
    including the extremes where the bug actually showed up.
    """
    mean_a, std_a, ta = 100.0, 12.0, 103.0
    mean_b, std_b, tb = 95.0, 11.0, 97.0

    joint_ab = tm.joint_probability_both_exceed(mean_a, std_a, ta, mean_b, std_b, tb, rho)
    joint_ba = tm.joint_probability_both_exceed(mean_b, std_b, tb, mean_a, std_a, ta, rho)

    assert joint_ab == pytest.approx(joint_ba, abs=1e-9)


@pytest.mark.parametrize(
    "rho",
    [-0.999999, -0.99, -0.5, 0.0, 0.5, 0.99, 0.999999],
)
def test_joint_probability_respects_frechet_bounds(rho):
    """
    Regression test: P(A and B) must always sit within
    [max(0, P(A)+P(B)-1), min(P(A), P(B))] -- a mathematically exact
    requirement for any valid joint distribution, regardless of
    correlation. A naive [0,1] clamp was not tight enough on its own;
    found via stress testing that rho=+0.999999 slightly exceeded the
    true upper bound before this was fixed.
    """
    mean_a, std_a, ta = 100.0, 12.0, 103.0
    mean_b, std_b, tb = 95.0, 11.0, 97.0

    p_a = 1 - tm.norm_cdf((ta - mean_a) / std_a)
    p_b = 1 - tm.norm_cdf((tb - mean_b) / std_b)
    lower_bound = max(0.0, p_a + p_b - 1.0)
    upper_bound = min(p_a, p_b)

    joint = tm.joint_probability_both_exceed(mean_a, std_a, ta, mean_b, std_b, tb, rho)

    assert lower_bound - 1e-9 <= joint <= upper_bound + 1e-9


# ============================================================
# END-TO-END: price_both_teams_over
# ============================================================

def _realistic_ladders():
    team_a_lines = [(94.5, -130, -100), (99.5, 100, -130), (104.5, 170, -220), (109.5, 280, -400)]
    team_b_lines = [(89.5, -120, -110), (94.5, 110, -140), (99.5, 190, -250), (104.5, 320, -500)]
    game_total_lines = [(189.5, -115, -115), (199.5, 120, -150), (209.5, 220, -300), (219.5, 400, -700)]
    return (
        build_ladder(team_a_lines),
        build_ladder(team_b_lines),
        build_ladder(game_total_lines),
    )


def test_price_both_teams_over_end_to_end():
    team_a_ladder, team_b_ladder, game_total_ladder = _realistic_ladders()

    result = tm.price_both_teams_over(
        team_a_ladder, team_b_ladder, game_total_ladder, 100.0, 100.0
    )

    assert 0.0 < result["joint_probability"] < 1.0
    assert -1.0 <= result["implied_correlation"] <= 1.0
    assert result["team_a_std"] > 0
    assert result["team_b_std"] > 0


def test_price_both_teams_over_beats_naive_independence_when_positively_correlated():
    team_a_ladder, team_b_ladder, game_total_ladder = _realistic_ladders()

    result = tm.price_both_teams_over(
        team_a_ladder, team_b_ladder, game_total_ladder, 100.0, 100.0
    )

    p_a_alone = 1 - tm.norm_cdf((100.0 - result["team_a_mean"]) / result["team_a_std"])
    p_b_alone = 1 - tm.norm_cdf((100.0 - result["team_b_mean"]) / result["team_b_std"])
    naive_product = p_a_alone * p_b_alone

    assert result["implied_correlation"] > 0
    assert result["joint_probability"] > naive_product


# ============================================================
# SPREAD + TOTAL -> IMPLIED TEAM MEANS
# ============================================================

def test_implied_team_means_from_spread_and_total():
    total, margin = 228.5, 7.5
    favorite_mean, underdog_mean = tm.implied_team_means_from_spread_and_total(total, margin)

    assert favorite_mean + underdog_mean == pytest.approx(total)
    assert favorite_mean - underdog_mean == pytest.approx(margin)
    assert favorite_mean > underdog_mean


def test_implied_team_means_with_period_fraction_scales_both():
    total, margin = 228.5, 7.5
    full_favorite, full_underdog = tm.implied_team_means_from_spread_and_total(total, margin)
    scaled_favorite, scaled_underdog = tm.implied_team_means_from_spread_and_total(
        total, margin, period_fraction=0.75
    )

    assert scaled_favorite == pytest.approx(full_favorite * 0.75)
    assert scaled_underdog == pytest.approx(full_underdog * 0.75)


# ============================================================
# INPUT VALIDITY (regressions for bugs reproduced before being fixed)
# ============================================================

@pytest.mark.parametrize(
    "position", ["mean_a", "std_a", "threshold_a", "mean_b", "std_b", "threshold_b", "rho"]
)
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_joint_probability_rejects_non_finite_input_in_every_position(position, bad_value):
    """
    A NaN rho used to be silently clamped to a near-perfect correlation
    by the min/max inside bivariate_normal_cdf and produce a plausible-
    looking wrong answer instead of an error.
    """
    args = dict(mean_a=100.0, std_a=12.0, threshold_a=100.0,
                mean_b=95.0, std_b=11.0, threshold_b=100.0, rho=0.3)
    args[position] = bad_value
    with pytest.raises(ValueError):
        tm.joint_probability_both_exceed(**args)


@pytest.mark.parametrize("bad_p", [0.0, 1.0, -0.1, 1.1, float("nan")])
def test_fit_normal_from_ladder_rejects_probability_outside_open_unit_interval(bad_p):
    ladder = [LadderPoint(line=99.5, probability_over=bad_p), LadderPoint(line=104.5, probability_over=0.3)]
    with pytest.raises(ValueError):
        tm.fit_normal_from_ladder(ladder)


@pytest.mark.parametrize("bad_line", [float("nan"), float("inf")])
def test_fit_normal_from_ladder_rejects_non_finite_line(bad_line):
    ladder = [LadderPoint(line=bad_line, probability_over=0.6), LadderPoint(line=104.5, probability_over=0.3)]
    with pytest.raises(ValueError):
        tm.fit_normal_from_ladder(ladder)


@pytest.mark.parametrize("var_a,var_b,var_total", [
    (0.0, 1.0, 1.0), (1.0, 0.0, 1.0), (-1.0, 1.0, 1.0),
    (float("nan"), 1.0, 1.0), (1.0, 1.0, float("nan")), (1.0, float("inf"), 1.0),
])
def test_implied_correlation_rejects_invalid_variances(var_a, var_b, var_total):
    with pytest.raises(ValueError):
        tm.implied_correlation_from_variances(var_a, var_b, var_total)
