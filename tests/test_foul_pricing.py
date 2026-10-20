"""
Tests for foul_pricing.py -- the foul-out pricing math, extracted from
main.py so it can be tested without FastAPI. Stdlib-only.
"""

import pytest

import foul_pricing as fp


# ============================================================
# CALIBRATION TABLE / INTERPOLATION
# ============================================================

def test_calibration_table_has_the_documented_points():
    assert len(fp.CALIBRATION_TABLE) == 21
    assert fp.CALIBRATION_TABLE[0] == (30.0, 0.0597)
    assert dict(fp.CALIBRATION_TABLE)[39.0] == 0.1457
    assert fp.CALIBRATION_TABLE[-1] == (50.0, 0.3633)


def test_calibration_table_is_strictly_increasing_in_both_columns():
    pfs = [p for p, _ in fp.CALIBRATION_TABLE]
    probs = [q for _, q in fp.CALIBRATION_TABLE]
    assert pfs == sorted(set(pfs))
    assert probs == sorted(set(probs))


@pytest.mark.parametrize("pf,prob", fp.CALIBRATION_TABLE)
def test_exact_table_points_return_the_table_value_unextrapolated(pf, prob):
    assert fp.interpolate_probability(pf) == (prob, False)


def test_midpoint_interpolates_linearly():
    probability, extrapolated = fp.interpolate_probability(44.5)
    assert probability == pytest.approx((0.2280 + 0.2479) / 2, abs=1e-12)
    assert extrapolated is False


@pytest.mark.parametrize("pf", [20.0, 29.999, 50.001, 60.0, 100.0])
def test_outside_the_table_is_flagged_and_stays_a_valid_probability(pf):
    probability, extrapolated = fp.interpolate_probability(pf)
    assert extrapolated is True
    assert 0.0 <= probability <= 1.0


def test_probability_never_decreases_as_pf_increases():
    values = [fp.interpolate_probability(30 + i * 0.1)[0] for i in range(0, 301)]
    assert all(values[i] <= values[i + 1] + 1e-12 for i in range(len(values) - 1))


# ============================================================
# ODDS HELPERS
# ============================================================

@pytest.mark.parametrize("odds,expected", [(400, 0.2), (500, 1 / 6), (-200, 2 / 3), (100, 0.5), (-100, 0.5)])
def test_market_break_even_probability(odds, expected):
    assert fp.market_break_even_probability(odds) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("odds,expected", [(400, 4.0), (-200, 0.5), (100, 1.0), (-100, 1.0)])
def test_american_to_decimal_profit_is_profit_per_dollar(odds, expected):
    assert fp.american_to_decimal_profit(odds) == pytest.approx(expected)


@pytest.mark.parametrize("p,expected", [(0.2, 400), (0.25, 300), (2 / 3, -200), (0.5, -100), (0.75, -300)])
def test_probability_to_fair_american(p, expected):
    assert fp.probability_to_fair_american(p) == expected


@pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.1])
def test_probability_to_fair_american_rejects_degenerate_probabilities(p):
    assert fp.probability_to_fair_american(p) is None


def test_expected_roi_matches_the_documented_example():
    # p=0.20 at +500: EV = 0.20*5 - 0.80 = +20%
    assert fp.calculate_expected_roi(0.2, 500) == pytest.approx(0.2)


def test_round_trip_price_to_probability_to_price():
    # +100 and -100 are the same price (even money); everything else
    # round-trips exactly.
    for odds in list(range(-5000, -99, 13)) + list(range(101, 5001, 13)):
        assert fp.probability_to_fair_american(fp.market_break_even_probability(odds)) == odds


def test_fair_odds_are_always_valid_american_prices():
    for i in range(1, 10000):
        odds = fp.probability_to_fair_american(i / 10000)
        assert isinstance(odds, int) and abs(odds) >= 100


# ============================================================
# calculate_price
# ============================================================

def test_price_matches_the_hand_traced_example():
    r = fp.calculate_price(44.0, 400)
    assert r["calibrated_probability"] == 0.228
    assert r["market_break_even_probability"] == 0.2
    assert r["probability_edge"] == pytest.approx(0.028)
    assert r["fair_american_odds"] == 339
    assert r["expected_roi"] == pytest.approx(0.14)
    assert r["required_probability"] == pytest.approx(0.22)
    assert r["margin_above_requirement_pp"] == pytest.approx(0.8)
    assert r["minimum_acceptable_american_odds"] == 381
    assert r["status"] == "PASS"
    assert r["extrapolated"] is False
    assert r["model_version"] == fp.MODEL_VERSION


def test_no_edge_status():
    assert fp.calculate_price(39.0, -300)["status"] == "NO_EDGE"


def test_borderline_status_when_edge_exists_but_misses_the_margin():
    # PF44 -> 22.8%; +430 -> 18.87% break-even: 3.93pp edge, but only
    # -1.07pp after a 5pp margin.
    r = fp.calculate_price(44.0, 430, safety_margin_pp=5.0)
    assert r["probability_edge_pp"] > 0
    assert r["margin_above_requirement_pp"] < 0
    assert r["status"] == "FAIR_OR_BORDERLINE"


def test_status_only_improves_as_the_offered_price_gets_better():
    order = {"NO_EDGE": 0, "FAIR_OR_BORDERLINE": 1, "PASS": 2}
    ranks = [order[fp.calculate_price(44.0, o)["status"]] for o in range(100, 1500, 10)]
    assert ranks == sorted(ranks)
    assert ranks[0] == 0 and ranks[-1] == 2


def test_default_safety_margin_is_two_points():
    assert fp.DEFAULT_SAFETY_MARGIN_PP == 2.0
    assert fp.calculate_price(44.0, 400)["safety_margin_pp"] == 2.0


def test_custom_safety_margin_is_echoed_and_applied():
    r = fp.calculate_price(44.0, 400, safety_margin_pp=10.0)
    assert r["safety_margin_pp"] == 10.0
    assert r["required_probability"] == pytest.approx(0.30)
    assert r["status"] == "FAIR_OR_BORDERLINE"


def test_minimum_acceptable_odds_is_none_when_margin_exceeds_the_probability():
    # PF39 -> 14.57%; a 20pp margin leaves a negative acceptable break-even.
    assert fp.calculate_price(39.0, 400, safety_margin_pp=20.0)["minimum_acceptable_american_odds"] is None


def test_extrapolated_pf_is_flagged_on_the_price():
    assert fp.calculate_price(55.0, 400)["extrapolated"] is True


def test_minimum_acceptable_odds_is_the_price_that_just_clears_the_margin():
    r = fp.calculate_price(44.0, 400)
    at_threshold = fp.calculate_price(44.0, r["minimum_acceptable_american_odds"])
    assert at_threshold["margin_above_requirement_pp"] == pytest.approx(0.0, abs=0.06)


def test_fair_odds_match_the_calibrated_probability():
    for pf in (39, 42.5, 44, 47.25, 50):
        r = fp.calculate_price(pf, 400)
        assert r["fair_american_odds"] == fp.probability_to_fair_american(r["calibrated_probability"])


def test_output_rounding_is_applied():
    r = fp.calculate_price(44.123456789, 400)
    assert r["pf"] == 44.1235
    assert r["calibrated_probability"] == round(r["calibrated_probability"], 6)
    assert r["calibrated_probability_percent"] == round(r["calibrated_probability_percent"], 4)


# ============================================================
# ODDS WITH MARGIN (the price to quote)
# ============================================================

def test_pp_margin_worked_example():
    result = fp.odds_with_margin(0.10, 2.0, mode="pp")
    assert result["quoted_probability"] == pytest.approx(0.12)
    assert result["american_odds"] == 733
    assert fp.probability_to_fair_american(0.10) == 900


def test_relative_margin_worked_example():
    result = fp.odds_with_margin(0.10, 10.0, mode="relative")
    assert result["quoted_probability"] == pytest.approx(0.11)
    assert result["american_odds"] == 809


def test_relative_is_the_default_mode():
    assert fp.odds_with_margin(0.10, 10.0) == fp.odds_with_margin(0.10, 10.0, mode="relative")


def test_pp_margin_triples_a_one_percent_longshot_but_relative_does_not():
    assert fp.odds_with_margin(0.01, 2.0, mode="pp")["quoted_probability"] == pytest.approx(0.03)
    assert fp.odds_with_margin(0.01, 10.0, mode="relative")["quoted_probability"] == pytest.approx(0.011)


@pytest.mark.parametrize("mode", ["pp", "relative"])
def test_zero_margin_equals_fair_odds(mode):
    for p in (0.03, 0.2, 0.5, 0.8):
        assert fp.odds_with_margin(p, 0.0, mode=mode)["american_odds"] == fp.probability_to_fair_american(p)


@pytest.mark.parametrize("mode", ["pp", "relative"])
def test_margin_always_shortens_the_price(mode):
    fair = fp.probability_to_fair_american(0.25)
    assert fp.odds_with_margin(0.25, 8.0, mode=mode)["american_odds"] < fair


def test_margin_crosses_from_plus_to_minus_money():
    assert fp.odds_with_margin(0.48, 4.0, mode="pp")["american_odds"] < 0


@pytest.mark.parametrize("mode", ["pp", "relative"])
def test_margin_that_reaches_100_percent_has_no_price(mode):
    assert fp.odds_with_margin(0.98, 200.0 if mode == "relative" else 2.0, mode=mode) is None


@pytest.mark.parametrize("probability", [0.0, 1.0, -0.1, 1.5])
def test_no_price_for_probability_outside_open_interval(probability):
    assert fp.odds_with_margin(probability, 2.0) is None


@pytest.mark.parametrize("margin", [-1.0, float("nan"), float("inf")])
def test_rejects_bad_margin(margin):
    with pytest.raises(ValueError):
        fp.odds_with_margin(0.2, margin)


def test_rejects_unknown_mode():
    with pytest.raises(ValueError):
        fp.odds_with_margin(0.2, 2.0, mode="percent")


def test_table_matches_the_calibration_formula_from_the_notebook():
    """Every row, 30 to 50, is the notebook's two-step formula rounded to 4 decimals:
    raw = expit(-7.138896059193029 + 0.1365129749388334 * PF), then
    calibrated = expit(-0.30902063352803844 + 0.8041330395767662 * logit(raw))."""
    import math

    def expit(x):
        return 1 / (1 + math.exp(-x))

    for pf, prob in fp.CALIBRATION_TABLE:
        raw = expit(-7.138896059193029 + 0.1365129749388334 * pf)
        logit = math.log(raw / (1 - raw))
        assert prob == round(expit(-0.30902063352803844 + 0.8041330395767662 * logit), 4), pf
