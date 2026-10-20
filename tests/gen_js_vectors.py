"""
Generates site/test_vectors.json: inputs and expected outputs computed by
the PYTHON reference modules, which site/test_engine.js replays against
the JavaScript port (site/engine.js).

Run from the project root:  python tests/gen_js_vectors.py
tests/test_js_vectors_fresh.py fails if the checked-in file is stale, so
changing the Python math without regenerating (and re-checking the JS)
cannot go unnoticed.
"""

import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import foul_pricing as fp  # noqa: E402
import player_markets as pm  # noqa: E402
import team_markets as tm  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "site", "test_vectors.json")


def _try(fn, *args, **kwargs):
    try:
        return {"value": fn(*args, **kwargs)}
    except ValueError:
        return {"error": True}


def build():
    rng = random.Random(20261019)
    v = {}

    xs = [round(-8 + i * 0.01, 2) for i in range(1601)] + [10.0, -10.0, 37.5, -37.5]
    v["erf"] = [[x, math.erf(x)] for x in xs]
    v["norm_cdf"] = [[x, tm.norm_cdf(x)] for x in xs]

    ps = [10 ** -k for k in range(1, 13)] + [1 - 10 ** -k for k in range(1, 13)]
    ps += [i / 1000 for i in range(1, 1000)] + [rng.random() for _ in range(500)]
    v["norm_ppf"] = [[p, tm.norm_ppf(p)] for p in ps if 0 < p < 1]

    odds = [100, 101, 110, 150, 199, 250, 400, 999, 2500, 10000,
            -100, -101, -110, -120, -150, -300, -1000, -10000]
    v["implied_probability"] = [[o, pm.implied_probability(o)] for o in odds]
    bad = [0, 5, -50, 99, -99, 1, -1]
    v["implied_probability_invalid"] = bad

    v["devig"] = [
        [a, b, list(pm.devig_two_way(a, b))]
        for a in (-250, -130, -114, -110, 100, 105, 150)
        for b in (-250, -130, -114, -110, 100, 120, 200)
    ]

    plist = [i / 200 for i in range(1, 200)] + [rng.random() for _ in range(300)]
    plist += [0.4, 0.2, 0.25, 0.5, 0.6, 0.0001, 0.9999]
    v["fair_american"] = [[p, fp.probability_to_fair_american(p)] for p in plist]
    v["fair_american_invalid"] = [0.0, 1.0, -0.2, 1.5]

    cases = []
    for p in (0.003, 0.01, 0.05, 0.1, 0.25, 0.48, 0.5, 0.62, 0.9, 0.97):
        for m in (0.0, 1.0, 2.0, 5.0, 10.0, 25.0, 150.0):
            for mode in ("relative", "pp"):
                cases.append([p, m, mode, fp.odds_with_margin(p, m, mode)])
    v["odds_with_margin"] = cases
    v["odds_with_margin_invalid"] = [[0.2, -1.0, "pp"], [0.2, 1.0, "bogus"]]

    price = []
    for pf in [30, 38.9, 39, 39.5, 41, 44, 44.37, 49.99, 50, 52, 60]:
        for o in (-300, -110, 100, 150, 400, 900):
            for m in (0.0, 2.0, 5.0):
                price.append([pf, o, m, fp.calculate_price(pf, o, m)])
    v["calculate_price"] = price

    sl = []
    for stat, lines_, thr_ in (
        ("points", (4.5, 7.5, 10.5, 13.5, 15.5, 17.5, 20.5, 22.5, 24.5, 26.5, 30.5, 35.5), (10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 75)),
        ("rebounds", (2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 10.5, 11.5, 13.5), (4, 6, 8, 10, 12, 14, 16, 18, 20, 25)),
        ("assists", (1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 10.5), (4, 6, 8, 10, 12, 14, 16, 18, 20)),
    ):
        for line in lines_:
            for (o, u) in ((-110, -110), (-130, 102), (100, -132), (-150, 120), (120, -150)):
                for t in thr_:
                    sl.append([line, o, u, t, stat, pm.single_line_probability(line, o, u, t, stat)])
    v["single_line"] = sl
    v["single_line_invalid"] = [
        [0, -110, -110, 25, "points"], [-3, -110, -110, 25, "points"], [20.5, -110, -110, 0.5, "points"],
        [20.5, -110, -110, 0, "points"], [20.5, 50, -110, 25, "points"], [20.5, -110, -50, 25, "points"],
        [5.5, -110, -110, 10, "blocks"], [5.5, -110, -110, 10, "steals"], [5.5, -110, -110, 10, ""],
        [0, -110, -110, 12, "rebounds"],
    ]

    ladders = []
    for _ in range(300):
        n = rng.choice([1, 2, 2, 3, 3, 4])
        base = rng.choice([8.5, 12.5, 15.5, 20.5, 24.5])
        lines = sorted({base + 5 * i for i in range(n)})
        lines_odds = []
        for i, ln in enumerate(lines):
            p_over = max(0.03, min(0.95, 0.55 - 0.12 * i + rng.uniform(-0.04, 0.04)))
            over = -round(100 * p_over / (1 - p_over)) if p_over >= 0.5 else round(100 * (1 - p_over) / p_over)
            over = int(over) if abs(over) >= 100 else (100 if over >= 0 else -100)
            under = -int(round(100 * (1 - p_over) / p_over)) if (1 - p_over) >= 0.5 else int(round(100 * p_over / (1 - p_over)))
            under = under if abs(under) >= 100 else (100 if under >= 0 else -100)
            lines_odds.append([ln, over, under])
        thr = rng.choice([5, 10, 15, 20, 25, 30, 35, 40, 50, 60]) + rng.choice([0, 0, 0.5])
        res = _try(lambda: pm.probability_at_threshold(pm.build_ladder([tuple(x) for x in lines_odds]), thr))
        if "value" in res:
            res = {"value": list(res["value"])}
        ladders.append([lines_odds, thr, res])
    v["ladder"] = ladders

    v["direct"] = [
        [y, n, list(pm.direct_market_probability(y, n))]
        for y in (-350, -110, 100, 260, 1200)
        for n in (None, -350, -110, 150, 260)
    ]

    combos = [[[rng.random() * 0.6 for _ in range(rng.randint(0, 12))]] for _ in range(100)]
    v["combine"] = [[c[0], pm.combine_any_player(c[0])] for c in combos]
    v["combine_invalid"] = [[0.2, 1.2], [-0.1, 0.3]]

    # ---- team markets ----
    zs = [-3.0, -1.5, -0.5, 0.0, 0.4, 1.0, 2.2, 3.5]
    rhos = [-0.95, -0.5, -0.1, 0.0, 0.2, 0.5, 0.8, 0.99, 0.999999, 1.0, -1.0]
    v["bvn"] = [[za, zb, rho, tm.bivariate_normal_cdf(za, zb, rho)]
                for za in zs for zb in zs for rho in rhos]

    def prob_to_american(p):
        return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))

    def ladder_lines(mean, std, lines, vig=0.025):
        out = []
        for ln in lines:
            p_over = 1 - tm.norm_cdf((ln - mean) / std)
            over = prob_to_american(min(0.97, max(0.03, p_over + vig / 2 * 1)))
            under = prob_to_american(min(0.97, max(0.03, (1 - p_over) + vig / 2 * 1)))
            out.append([ln, over, under])
        return out

    fits = []
    for _ in range(80):
        mean = rng.uniform(70, 125); std = rng.uniform(6, 16)
        lines = sorted(rng.sample([mean + d + 0.5 for d in range(-18, 19, 3)], rng.choice([2, 3, 4, 5])))
        lines = [round(x, 1) for x in lines]
        ll = ladder_lines(mean, std, lines)
        res = _try(lambda: list(tm.fit_normal_from_ladder(pm.build_ladder([tuple(x) for x in ll]))))
        fits.append([ll, res])
    # inconsistent data: Over probability rising with the line must error
    fits.append([[[99.5, -150, 120], [104.5, -200, 160]], _try(lambda: list(tm.fit_normal_from_ladder(
        pm.build_ladder([(99.5, -150, 120), (104.5, -200, 160)]))))])
    v["fit_normal"] = fits

    v["implied_corr"] = [
        [a, b, t, tm.implied_correlation_from_variances(a, b, t)]
        for a in (64.0, 100.0, 144.0) for b in (81.0, 121.0, 169.0)
        for t in (60.0, 150.0, 220.0, 300.0, 520.0)
    ]
    v["implied_corr_invalid"] = [[0.0, 100.0, 200.0], [100.0, -4.0, 200.0]]

    v["joint"] = []
    for _ in range(150):
        ma, mb = rng.uniform(85, 125), rng.uniform(85, 125)
        sa, sb = rng.uniform(7, 15), rng.uniform(7, 15)
        ta, tb = rng.uniform(80, 130), rng.uniform(80, 130)
        rho = rng.choice([-0.6, -0.2, 0.0, 0.15, 0.35, 0.6, 0.95, 0.99999, -0.99999])
        v["joint"].append([ma, sa, ta, mb, sb, tb, rho, tm.joint_probability_both_exceed(ma, sa, ta, mb, sb, tb, rho)])
    v["joint_invalid"] = [[100, 0, 100, 100, 10, 100, 0.2], [100, 10, 100, 100, -3, 100, 0.2]]

    v["reach"] = [[m, sd, r, tm.team_reach_probability(m, sd, r)]
                  for m in (88.0, 99.5, 104.0, 111.3) for sd in (6.5, 10.0, 13.0) for r in (90, 100, 105, 110, 120)]
    v["mean_from_single_line"] = [
        [ln, o, u, sd, tm.mean_from_single_line(ln, o, u, sd)]
        for ln in (96.5, 99.5, 104.5) for (o, u) in ((-110, -110), (-130, 102), (115, -145)) for sd in (6.0, 10.5)
    ]
    v["both_reach"] = []
    for _ in range(60):
        ma, mb = rng.uniform(90, 120), rng.uniform(90, 120)
        sa, sb = rng.uniform(8, 13), rng.uniform(8, 13)
        v["both_reach"].append([ma, sa, rng.choice([95, 100, 105]), mb, sb, rng.choice([95, 100, 105]),
                                rng.choice([0.0, 0.25, 0.5]),
                                tm.price_both_teams_reach(ma, sa, 100, mb, sb, 100, 0.25) if False else None])
    for row in v["both_reach"]:
        ma, sa, ra, mb, sb, rb, rho, _ = row
        row[7] = tm.price_both_teams_reach(ma, sa, ra, mb, sb, rb, rho)

    # whole pipeline: three ladders -> correlation -> joint probability
    pipe = []
    for _ in range(40):
        ma, mb = rng.uniform(88, 118), rng.uniform(88, 118)
        sa, sb = rng.uniform(8, 12), rng.uniform(8, 12)
        rho_true = rng.uniform(0.05, 0.5)
        st = math.sqrt(sa ** 2 + sb ** 2 + 2 * rho_true * sa * sb)
        la = [round(ma + d, 1) for d in (-8.0, -3.0, 2.0, 7.0)]
        lb = [round(mb + d, 1) for d in (-8.0, -3.0, 2.0, 7.0)]
        lt = [round(ma + mb + d, 1) for d in (-14.0, -6.0, 3.0, 10.0)]
        ll_a, ll_b, ll_t = ladder_lines(ma, sa, la), ladder_lines(mb, sb, lb), ladder_lines(ma + mb, st, lt)
        ra, rb = rng.choice([95, 100, 105]), rng.choice([95, 100, 105])
        res = _try(lambda: tm.price_both_teams_over(
            pm.build_ladder([tuple(x) for x in ll_a]), pm.build_ladder([tuple(x) for x in ll_b]),
            pm.build_ladder([tuple(x) for x in ll_t]), ra - 0.5, rb - 0.5))
        pipe.append([ll_a, ll_b, ll_t, ra, rb, res])
    v["both_pipeline"] = pipe

    v["constants"] = {
        "calibration_table": [list(r) for r in fp.CALIBRATION_TABLE],
        "model_version": fp.MODEL_VERSION,
        "single_line_models": pm.SINGLE_LINE_MODELS,
        "max_single_line_gap": pm.MAX_SINGLE_LINE_GAP,
    }
    return v


def main():
    data = build()
    with open(OUT, "w") as fh:
        json.dump(data, fh, separators=(",", ":"), sort_keys=True)
        fh.write("\n")
    print("wrote", os.path.relpath(OUT), "with", sum(len(x) if isinstance(x, list) else 1 for x in data.values()), "entries")


if __name__ == "__main__":
    main()
