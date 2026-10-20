import pytest

import player_markets as pm


# ============================================================
# ODDS CONVERSION / DE-VIG
# ============================================================

def test_implied_probability_underdog():
    assert pm.implied_probability(980) == pytest.approx(100 / 1080, abs=1e-6)


def test_implied_probability_favorite():
    assert pm.implied_probability(-125) == pytest.approx(125 / 225, abs=1e-6)


def test_implied_probability_rejects_zero():
    with pytest.raises(ValueError):
        pm.implied_probability(0)


def test_devig_two_way_matches_reese_ou_example():
    # Angel Reese O/U 15.5 points: Over -104, Under -125
    p_over, p_under = pm.devig_two_way(-104, -125)
    assert p_over == pytest.approx(0.47857, abs=1e-4)
    assert p_under == pytest.approx(0.52143, abs=1e-4)
    assert p_over + p_under == pytest.approx(1.0, abs=1e-9)


# ============================================================
# COMBINING ACROSS A ROSTER
# ============================================================

def test_combine_any_player_matches_screenshot_example():
    # Angel Reese +980, Allisha Gray +410, Rhyne Howard +415
    probs = [
        pm.implied_probability(980),
        pm.implied_probability(410),
        pm.implied_probability(415),
    ]
    combined = pm.combine_any_player(probs)
    assert combined == pytest.approx(0.4122, abs=1e-3)


def test_combine_any_player_empty_list():
    assert pm.combine_any_player([]) == 0.0


def test_combine_any_player_rejects_out_of_range():
    with pytest.raises(ValueError):
        pm.combine_any_player([0.5, 1.5])


def test_combine_any_player_single_certain_event():
    assert pm.combine_any_player([1.0]) == pytest.approx(1.0)


# ============================================================
# LADDER CONSTRUCTION
# ============================================================

@pytest.fixture
def sample_ladder():
    lines = [
        (14.5, -110, -110),
        (19.5, +150, -190),
        (24.5, +350, -450),
        (29.5, +700, -1000),
    ]
    return pm.build_ladder(lines)


def test_build_ladder_sorted_ascending(sample_ladder):
    assert [p.line for p in sample_ladder] == [14.5, 19.5, 24.5, 29.5]


def test_build_ladder_probabilities_decreasing(sample_ladder):
    probs = [p.probability_over for p in sample_ladder]
    assert all(probs[i] > probs[i + 1] for i in range(len(probs) - 1))


# ============================================================
# THRESHOLD LOOKUP: EXACT / INTERPOLATED
# ============================================================

def test_probability_at_threshold_exact_match(sample_ladder):
    # 25+ -> target_line 24.5, which is exactly on the ladder
    probability, extrapolated = pm.probability_at_threshold(sample_ladder, 25)
    assert extrapolated is False
    assert probability == pytest.approx(sample_ladder[2].probability_over)


def test_probability_at_threshold_interpolated(sample_ladder):
    # 22+ -> target_line 21.5, between the 19.5 and 24.5 lines
    p20, _ = pm.probability_at_threshold(sample_ladder, 20)
    p22, extrapolated = pm.probability_at_threshold(sample_ladder, 22)
    p25, _ = pm.probability_at_threshold(sample_ladder, 25)
    assert extrapolated is False
    assert p20 > p22 > p25


def test_probability_at_threshold_empty_ladder_raises():
    with pytest.raises(ValueError):
        pm.probability_at_threshold([], 25)


# ============================================================
# EXTRAPOLATION (DECAY CURVE)
# ============================================================

def test_extrapolation_above_range_never_hits_zero(sample_ladder):
    # This is the specific failure mode the decay curve was added to fix:
    # linear extrapolation from a 29.5 max line to a 50+ threshold used
    # to clamp to exactly 0.0.
    probability, extrapolated = pm.probability_at_threshold(sample_ladder, 50)
    assert extrapolated is True
    assert probability > 0.0
    assert probability < 0.02  # should still be small, just not zero


def test_extrapolation_above_range_is_monotonic(sample_ladder):
    p29, _ = pm.probability_at_threshold(sample_ladder, 29)
    p30, _ = pm.probability_at_threshold(sample_ladder, 30)
    p50, _ = pm.probability_at_threshold(sample_ladder, 50)
    assert p29 > p30 > p50


def test_extrapolation_above_range_is_geometric(sample_ladder):
    values = [
        pm.probability_at_threshold(sample_ladder, t)[0]
        for t in (30, 35, 40, 45, 50)
    ]
    ratios = [values[i + 1] / values[i] for i in range(len(values) - 1)]
    # Consecutive ratios should be consistent (constant decay rate),
    # confirming this is a true geometric/exponential decay curve.
    assert max(ratios) - min(ratios) < 1e-6


def test_extrapolation_below_range_bounded(sample_ladder):
    probability, extrapolated = pm.probability_at_threshold(sample_ladder, 5)
    assert extrapolated is True
    assert 0.0 <= probability <= 1.0


def test_single_line_ladder_far_from_threshold_is_refused():
    # Regression: this used to return the line's own probability (~51%)
    # for "25+", and a roster of such players combined to 99.99%.
    ladder = pm.build_ladder([(15.5, -104, -125)])
    with pytest.raises(ValueError, match="too far"):
        pm.probability_at_threshold(ladder, 25)


def test_single_line_ladder_refused_below_range_too():
    ladder = pm.build_ladder([(24.5, -110, -110)])
    with pytest.raises(ValueError, match="too far"):
        pm.probability_at_threshold(ladder, 10)


def test_single_line_ladder_within_gap_still_flat_and_flagged():
    ladder = pm.build_ladder([(26.5, -114, -114)])
    probability, extrapolated = pm.probability_at_threshold(ladder, 25)
    assert extrapolated is True
    assert probability == pytest.approx(ladder[0].probability_over)


def test_single_line_gap_boundary_is_inclusive():
    ladder = pm.build_ladder([(26.5, -110, -110)])
    # target 24.5 is exactly MAX_SINGLE_LINE_GAP below 26.5 -> allowed
    pm.probability_at_threshold(ladder, 25)
    # a threshold farther out is refused
    with pytest.raises(ValueError):
        pm.probability_at_threshold(ladder, 24.2)  # target 23.7, gap 2.8


# ============================================================
# DIRECT MARKETS (double-double / triple-double)
# ============================================================

def test_direct_market_both_sides_devigged():
    probability, devigged = pm.direct_market_probability(-350, +260)
    assert devigged is True
    assert 0.0 < probability < 1.0


def test_direct_market_yes_only_not_devigged():
    probability, devigged = pm.direct_market_probability(-350)
    assert devigged is False
    assert probability == pytest.approx(pm.implied_probability(-350))


# ============================================================
# INPUT VALIDITY (regressions for bugs reproduced before being fixed)
# ============================================================

@pytest.mark.parametrize("bad_odds", [1, 5, 50, 99, -1, -50, -99])
def test_implied_probability_rejects_odds_below_100_in_magnitude(bad_odds):
    """
    Real American odds are +100 or higher, or -100 or lower. Values in
    between used to be accepted and priced silently as if real (+5 ->
    95% implied probability, -50 -> 33%) -- almost always a typo.
    """
    with pytest.raises(ValueError):
        pm.implied_probability(bad_odds)


@pytest.mark.parametrize("odds,expected", [(100, 0.5), (-100, 0.5), (-110, 110 / 210), (101, 100 / 201)])
def test_implied_probability_accepts_the_valid_boundaries(odds, expected):
    assert pm.implied_probability(odds) == pytest.approx(expected, abs=1e-9)


def test_devig_two_way_rejects_invalid_odds_on_either_side():
    with pytest.raises(ValueError):
        pm.devig_two_way(50, -110)
    with pytest.raises(ValueError):
        pm.devig_two_way(-110, -50)


def test_direct_market_probability_rejects_invalid_odds():
    with pytest.raises(ValueError):
        pm.direct_market_probability(50)
    with pytest.raises(ValueError):
        pm.direct_market_probability(-350, 50)


@pytest.mark.parametrize("bad_line", [float("nan"), float("inf"), float("-inf")])
def test_build_ladder_rejects_non_finite_line(bad_line):
    """
    A NaN line used to be accepted, and probability_at_threshold then
    silently returned a 100% probability for it.
    """
    with pytest.raises(ValueError):
        pm.build_ladder([(bad_line, -110, -110), (19.5, 150, -190)])


@pytest.mark.parametrize("bad_threshold", [float("nan"), float("inf"), float("-inf")])
def test_probability_at_threshold_rejects_non_finite_threshold(bad_threshold):
    ladder = pm.build_ladder([(14.5, -110, -110), (19.5, 150, -190), (24.5, 350, -450)])
    with pytest.raises(ValueError):
        pm.probability_at_threshold(ladder, bad_threshold)
