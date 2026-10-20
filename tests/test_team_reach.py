"""Tests for the 'X or more' (reach) wrappers in team_markets."""

import math

import pytest

import player_markets as pm
import team_markets as tm


def test_reach_is_half_a_point_below_the_raw_cutoff():
    assert tm.reach_to_cutoff(100) == 99.5


def test_team_reach_matches_exceeding_the_half_point_line():
    mean, std = 104.0, 11.0
    expected = 1.0 - tm.norm_cdf((99.5 - mean) / std)
    assert tm.team_reach_probability(mean, std, 100) == pytest.approx(expected, rel=1e-12)


def test_reach_probability_is_higher_than_exceeding_the_same_number():
    mean, std = 104.0, 11.0
    exceed_100 = 1.0 - tm.norm_cdf((100 - mean) / std)
    assert tm.team_reach_probability(mean, std, 100) > exceed_100


def test_reach_at_the_mean_plus_half_is_fifty_percent():
    assert tm.team_reach_probability(99.5, 10.0, 100) == pytest.approx(0.5, abs=1e-12)


def test_reach_probability_falls_as_the_threshold_rises():
    values = [tm.team_reach_probability(104.0, 11.0, t) for t in (90, 95, 100, 105, 110)]
    assert all(a > b for a, b in zip(values, values[1:]))


@pytest.mark.parametrize("mean,std", [(float("nan"), 10), (100, float("inf")), (100, 0), (100, -3)])
def test_team_reach_rejects_bad_inputs(mean, std):
    with pytest.raises(ValueError):
        tm.team_reach_probability(mean, std, 100)


def test_reach_rejects_non_finite_threshold():
    with pytest.raises(ValueError):
        tm.reach_to_cutoff(float("nan"))


def test_mean_from_single_line_pins_the_over_probability():
    std = 10.0
    mean = tm.mean_from_single_line(99.5, -110, -110, std)
    # a 50/50 line sits at the mean
    assert mean == pytest.approx(99.5, abs=1e-9)
    p_over = pm.devig_two_way(-130, 102)[0]
    mean2 = tm.mean_from_single_line(99.5, -130, 102, std)
    assert 1.0 - tm.norm_cdf((99.5 - mean2) / std) == pytest.approx(p_over, rel=1e-9)


def test_mean_from_single_line_favoured_over_means_higher_mean():
    assert tm.mean_from_single_line(99.5, -150, 120, 10.0) > 99.5


@pytest.mark.parametrize("std", [0, -1, float("nan")])
def test_mean_from_single_line_rejects_bad_std(std):
    with pytest.raises(ValueError):
        tm.mean_from_single_line(99.5, -110, -110, std)


def test_both_reach_independent_when_rho_zero():
    r = tm.price_both_teams_reach(104, 11, 100, 101, 11, 100, 0.0)
    assert r["joint_probability"] == pytest.approx(r["independent_probability"], abs=1e-6)
    assert r["independent_probability"] == pytest.approx(r["probability_a"] * r["probability_b"], rel=1e-12)


def test_positive_correlation_raises_the_joint_probability():
    low = tm.price_both_teams_reach(104, 11, 100, 101, 11, 100, 0.0)["joint_probability"]
    high = tm.price_both_teams_reach(104, 11, 100, 101, 11, 100, 0.5)["joint_probability"]
    assert high > low


def test_joint_never_exceeds_either_team_alone():
    r = tm.price_both_teams_reach(104, 11, 100, 101, 11, 100, 0.9)
    assert r["joint_probability"] <= min(r["probability_a"], r["probability_b"]) + 1e-12


def test_both_reach_uses_the_half_point_convention():
    # must equal the raw-cutoff function evaluated at 99.5, not 100
    r = tm.price_both_teams_reach(104, 11, 100, 101, 11, 100, 0.3)
    raw = tm.joint_probability_both_exceed(104, 11, 99.5, 101, 11, 99.5, 0.3)
    assert r["joint_probability"] == pytest.approx(raw, rel=1e-12)
