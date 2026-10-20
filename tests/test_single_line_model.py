"""
Tests for player_markets.single_line_probability -- the lognormal model
that prices "X+ points" from one base Over/Under line.

The real-data tests use sportsbook prices transcribed from screenshots of
two games' "To Score X+ Points" markets. The model's constants were fitted
on game 1 only; game 2 is an out-of-sample check. Book prices include vig,
so the model is expected to run slightly BELOW them -- these tests bound
how far off it may be, they do not demand equality.
"""

import math

import pytest

import player_markets as pm

# (player, base line, over, under)
GAME1_LINES = {
    "Tatum": (26.5, -114, -114), "Cunningham": (23.5, -130, -102),
    "Duren": (17.5, -108, -122), "White": (15.5, -122, -108),
    "George": (15.5, -120, -110), "Pritchard": (15.5, -108, -122),
    "Collins": (11.5, -122, -110), "Robinson": (10.5, -108, -122),
}
GAME1_BOOK = {
    25: {"Tatum": -172, "Cunningham": -104, "Duren": 450, "White": 560,
         "George": 680, "Pritchard": 750, "Collins": 2000, "Robinson": 2500},
    30: {"Tatum": 158, "Cunningham": 260, "Duren": 1200, "White": 1500,
         "George": 1500, "Pritchard": 1700},
    35: {"Tatum": 370, "Cunningham": 680, "Duren": 3500},
    40: {"Tatum": 900, "Cunningham": 1500},
    45: {"Tatum": 1800, "Cunningham": 4000},
    50: {"Tatum": 5500, "Cunningham": 8000},
}

GAME2_LINES = {
    "Brunson": (26.5, -128, -104), "Embiid": (22.5, -102, -130),
    "Maxey": (21.5, -104, -128), "Brown": (20.5, -120, -110),
    "Towns": (18.5, -112, -118), "Anunoby": (16.5, -114, -114),
    "LeBron": (15.5, -122, -108), "Bridges": (13.5, 100, -132),
    "Edgecombe": (11.5, -125, -106), "Hart": (10.5, -136, 102),
}
GAME2_BOOK = {
    25: {"Brunson": -186, "Embiid": 150, "Maxey": 180, "Brown": 210,
         "Towns": 350, "Anunoby": 520, "LeBron": 680, "Bridges": 1500,
         "Edgecombe": 1700, "Hart": 3000},
    30: {"Brunson": 144, "Embiid": 400, "Maxey": 470, "Brown": 600,
         "Towns": 1000, "Anunoby": 1400, "LeBron": 1700},
    35: {"Brunson": 370, "Embiid": 1000, "Maxey": 1200, "Brown": 1400,
         "Towns": 2500, "Anunoby": 4000},
    40: {"Brunson": 900, "Embiid": 2500, "Maxey": 3000, "Brown": 4000},
    45: {"Brunson": 1700},
    50: {"Brunson": 5500},
}


def _model(lines, name, threshold):
    line, over, under = lines[name]
    return pm.single_line_probability(line, over, under, threshold)


def _ratios(lines, book):
    out = []
    for threshold, prices in book.items():
        for name, odds in prices.items():
            out.append(
                (name, threshold,
                 _model(lines, name, threshold) / pm.implied_probability(odds))
            )
    return out


@pytest.mark.parametrize("lines,book", [
    (GAME1_LINES, GAME1_BOOK), (GAME2_LINES, GAME2_BOOK),
])
def test_every_player_price_within_a_factor_of_book(lines, book):
    for name, threshold, ratio in _ratios(lines, book):
        assert 0.75 <= ratio <= 1.5, (name, threshold, ratio)


def test_out_of_sample_game_median_ratio_is_close_to_one():
    ratios = sorted(r for _, _, r in _ratios(GAME2_LINES, GAME2_BOOK))
    median = ratios[len(ratios) // 2]
    assert 0.9 <= median <= 1.05


def test_out_of_sample_game_most_prices_within_20_percent():
    ratios = [r for _, _, r in _ratios(GAME2_LINES, GAME2_BOOK)]
    within = sum(1 for r in ratios if 0.8 <= r <= 1.2)
    assert within / len(ratios) >= 0.85


@pytest.mark.parametrize("lines,book", [
    (GAME1_LINES, GAME1_BOOK), (GAME2_LINES, GAME2_BOOK),
])
@pytest.mark.parametrize("threshold", [25, 30, 35, 40])
def test_any_player_probability_close_to_book_for_listed_players(
    lines, book, threshold
):
    names = list(book[threshold])
    model = pm.combine_any_player([_model(lines, n, threshold) for n in names])
    raw = pm.combine_any_player(
        [pm.implied_probability(book[threshold][n]) for n in names]
    )
    assert 0.9 <= model / raw <= 1.1


def test_pinned_reference_values():
    # Locks the exact constants. The JavaScript port is checked against
    # these same numbers, so changing the model means changing both.
    assert pm.single_line_probability(26.5, -114, -114, 25) == pytest.approx(
        0.6028063131534283, rel=1e-9)
    assert pm.single_line_probability(26.5, -114, -114, 60) == pytest.approx(
        0.0036127554058116296, rel=1e-9)


def test_line_probability_is_pinned_at_the_line_itself():
    # The model is built so P(points > line) equals the de-vigged Over
    # probability; "27+" means more than 26.5.
    p_over = pm.devig_two_way(-128, -104)[0]
    assert pm.single_line_probability(26.5, -128, -104, 27) == pytest.approx(p_over, rel=1e-9)


def test_probability_strictly_decreases_as_threshold_rises():
    values = [pm.single_line_probability(20.5, -110, -110, t)
              for t in (10, 15, 20, 25, 30, 35, 40, 45, 50, 60)]
    assert all(a > b for a, b in zip(values, values[1:]))
    assert all(0.0 < v < 1.0 for v in values)


def test_higher_over_price_means_higher_probability():
    low = pm.single_line_probability(20.5, 110, -140, 30)
    high = pm.single_line_probability(20.5, -140, 110, 30)
    assert high > low


def test_higher_base_line_means_higher_probability():
    assert (pm.single_line_probability(28.5, -110, -110, 35)
            > pm.single_line_probability(22.5, -110, -110, 35))


def test_tail_never_reaches_zero_at_60():
    assert pm.single_line_probability(10.5, -110, -110, 60) > 0.0


@pytest.mark.parametrize("line", [0, -5, float("nan"), float("inf")])
def test_rejects_bad_line(line):
    with pytest.raises(ValueError):
        pm.single_line_probability(line, -110, -110, 25)


@pytest.mark.parametrize("threshold", [0, 0.5, -1, float("nan"), float("inf")])
def test_rejects_bad_threshold(threshold):
    with pytest.raises(ValueError):
        pm.single_line_probability(20.5, -110, -110, threshold)


@pytest.mark.parametrize("over,under", [(50, -110), (-110, -50), (0, -110)])
def test_rejects_non_real_american_odds(over, under):
    with pytest.raises(ValueError):
        pm.single_line_probability(20.5, over, under, 25)


def test_constants_are_the_documented_fit():
    assert pm.SINGLE_LINE_SPREAD_AT_25 == 0.31
    assert pm.SINGLE_LINE_SPREAD_EXPONENT == 0.5


# ============================================================
# REBOUNDS (its own constants; points constants do not transfer)
# ============================================================
# Real "To Record X+ Rebounds" prices. The constants were fitted on game 1
# only; game 2 is the out-of-sample check.

REB1_LINES = {
    "Duren": (10.5, 110, -146), "Tatum": (8.5, -130, -102),
    "MRobinson": (8.5, 118, -158), "Queta": (7.5, -104, -128),
    "Thompson": (6.5, 100, -132), "Reed": (4.5, -158, 118),
    "Cunningham": (5.5, 122, -162),
}
REB1_BOOK = {
    12: {"Duren": 172, "Tatum": 300, "MRobinson": 370, "Queta": 560,
         "Thompson": 1300, "Reed": 1800, "Cunningham": 3000},
    14: {"Duren": 420, "Tatum": 750, "MRobinson": 830, "Queta": 1300},
    16: {"Duren": 1000, "Tatum": 1500, "MRobinson": 1500, "Queta": 3500},
}
REB2_LINES = {
    "Towns": (11.5, 100, -132), "Embiid": (7.5, -122, -108),
    "Hart": (7.5, -114, -114), "Drummond": (6.5, -114, -114),
    "LeBron": (6.5, 116, -154), "Brown": (5.5, -132, 100),
    "Anunoby": (5.5, 114, -152),
}
REB2_BOOK = {
    12: {"Towns": 102, "Embiid": 520, "Hart": 560, "Drummond": 750,
         "LeBron": 1300, "Brown": 1700, "Anunoby": 3000},
    14: {"Towns": 255, "Embiid": 1300, "Hart": 1300, "Drummond": 1500},
    16: {"Towns": 560, "Embiid": 3000, "Hart": 3500, "Drummond": 3500},
}


def _reb(lines, name, threshold):
    line, over, under = lines[name]
    return pm.single_line_probability(line, over, under, threshold, "rebounds")


def _reb_ratios(lines, book):
    return [
        (n, t, _reb(lines, n, t) / pm.implied_probability(o))
        for t, prices in book.items() for n, o in prices.items()
    ]


@pytest.mark.parametrize("lines,book", [(REB1_LINES, REB1_BOOK), (REB2_LINES, REB2_BOOK)])
def test_rebounds_every_price_within_a_factor_of_book(lines, book):
    for name, threshold, ratio in _reb_ratios(lines, book):
        assert 0.6 <= ratio <= 1.5, (name, threshold, ratio)


def test_rebounds_out_of_sample_game_median_ratio_close_to_one():
    ratios = sorted(r for _, _, r in _reb_ratios(REB2_LINES, REB2_BOOK))
    assert 0.95 <= ratios[len(ratios) // 2] <= 1.1


def test_rebounds_out_of_sample_game_most_prices_within_20_percent():
    ratios = [r for _, _, r in _reb_ratios(REB2_LINES, REB2_BOOK)]
    assert sum(1 for r in ratios if 0.8 <= r <= 1.2) / len(ratios) >= 0.6


@pytest.mark.parametrize("lines,book", [(REB1_LINES, REB1_BOOK), (REB2_LINES, REB2_BOOK)])
@pytest.mark.parametrize("threshold", [12, 14, 16])
def test_rebounds_any_player_close_to_book_for_listed_players(lines, book, threshold):
    names = list(book[threshold])
    model = pm.combine_any_player([_reb(lines, n, threshold) for n in names])
    raw = pm.combine_any_player([pm.implied_probability(book[threshold][n]) for n in names])
    assert 0.88 <= model / raw <= 1.15


def test_points_constants_do_not_transfer_to_rebounds():
    """The reason rebounds has its own model: the points constants priced
    16+ rebounds for the listed players far above the book."""
    names = list(REB2_BOOK[16])
    book = pm.combine_any_player([pm.implied_probability(REB2_BOOK[16][n]) for n in names])
    points_model = pm.combine_any_player([
        pm.single_line_probability(*REB2_LINES[n], 16, "points") for n in names
    ])
    assert points_model / book > 1.3


def test_stat_selects_the_model():
    assert (pm.single_line_probability(11.5, 100, -132, 14, "rebounds")
            != pm.single_line_probability(11.5, 100, -132, 14, "points"))


def test_default_stat_is_points():
    assert (pm.single_line_probability(26.5, -114, -114, 30)
            == pm.single_line_probability(26.5, -114, -114, 30, "points"))


def test_rebounds_pinned_reference_values():
    # The JavaScript port is checked against these same numbers.
    assert pm.single_line_probability(11.5, 100, -132, 12, "rebounds") == pytest.approx(
        0.4677419354838708, rel=1e-9)


def test_rebounds_line_pinned_at_the_line_itself():
    p_over = pm.devig_two_way(100, -132)[0]
    assert pm.single_line_probability(11.5, 100, -132, 12, "rebounds") == pytest.approx(p_over, rel=1e-9)


def test_rebounds_probability_strictly_decreases_with_threshold():
    values = [pm.single_line_probability(8.5, -110, -110, t, "rebounds") for t in (6, 8, 10, 12, 14, 16, 18, 20)]
    assert all(a > b for a, b in zip(values, values[1:]))
    assert all(0.0 < v < 1.0 for v in values)


@pytest.mark.parametrize("stat", ["steals", "blocks", "", "Points"])
def test_stat_without_a_checked_model_is_refused(stat):
    with pytest.raises(ValueError, match="No base-line model"):
        pm.single_line_probability(5.5, -110, -110, 10, stat)


def test_documented_model_constants():
    assert pm.SINGLE_LINE_MODELS["rebounds"] == {
        "anchor_line": 10.0, "spread_at_anchor": 0.36,
        "exponent": 0.4, "validated_max_threshold": 16.0,
    }
    assert pm.SINGLE_LINE_MODELS["points"]["validated_max_threshold"] == 50.0
    assert pm.SINGLE_LINE_MODELS["assists"] == {
        "anchor_line": 5.0, "spread_at_anchor": 0.46,
        "exponent": 0.4, "validated_max_threshold": 14.0,
    }


# ---------------------------------------------------------------------------
# Assists: fitted on game 1, checked without refitting on game 2.
# Real book prices (To Record X+ Assists); the book listed nothing above 14+.
AST1_LINES = {
    "Cunningham": (8.5, -120, -110), "Tatum": (5.5, -148, 112),
    "Pritchard": (4.5, -114, -114), "White": (4.5, 118, -158),
}
AST1_BOOK = {
    10: {"Cunningham": 126, "Tatum": 700, "Pritchard": 1400, "White": 2500},
    12: {"Cunningham": 350, "Tatum": 1700},
    14: {"Cunningham": 900},
}
AST2_LINES = {
    "LeBron": (7.5, -132, 100), "Brunson": (6.5, -118, -112), "Towns": (4.5, -154, 116),
    "Maxey": (4.5, -130, -102), "Hart": (4.5, 124, -166),
}
AST2_BOOK = {
    10: {"LeBron": 200, "Brunson": 420, "Towns": 1200, "Maxey": 1500, "Hart": 2000},
    12: {"LeBron": 540, "Brunson": 1200},
    14: {"LeBron": 1400, "Brunson": 3500},
}


def _ast_ratios(lines, book):
    return [
        (n, t, pm.single_line_probability(*lines[n][:1], lines[n][1], lines[n][2], t, "assists")
         / pm.implied_probability(o))
        for t, prices in book.items() for n, o in prices.items()
    ]


@pytest.mark.parametrize("lines,book", [(AST1_LINES, AST1_BOOK), (AST2_LINES, AST2_BOOK)])
def test_assists_every_price_within_a_factor_of_book(lines, book):
    for name, threshold, ratio in _ast_ratios(lines, book):
        assert 0.6 <= ratio <= 1.5, (name, threshold, ratio)


def test_assists_out_of_sample_game_median_ratio_close_to_one():
    ratios = sorted(r for _, _, r in _ast_ratios(AST2_LINES, AST2_BOOK))
    assert 0.95 <= ratios[len(ratios) // 2] <= 1.2


def test_assists_out_of_sample_game_most_prices_within_20_percent():
    ratios = [r for _, _, r in _ast_ratios(AST2_LINES, AST2_BOOK)]
    assert sum(1 for r in ratios if 0.8 <= r <= 1.2) / len(ratios) >= 0.75


def test_assists_rebounds_constants_are_not_the_assists_constants():
    assert pm.SINGLE_LINE_MODELS["assists"] != pm.SINGLE_LINE_MODELS["rebounds"]


def test_assists_probability_strictly_decreases_with_threshold():
    values = [pm.single_line_probability(6.5, -110, -110, t, "assists") for t in (6, 8, 10, 12, 14, 16, 18)]
    assert values == sorted(values, reverse=True) and len(set(values)) == len(values)


def test_assists_line_pinned_at_the_line_itself():
    p_over = pm.devig_two_way(-132, 100)[0]
    assert pm.single_line_probability(7.5, -132, 100, 8, "assists") == pytest.approx(p_over, rel=1e-9)
