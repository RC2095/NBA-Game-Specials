/*
 * NBA pricing engine -- browser/Node port of the Python reference modules
 * (foul_pricing.py, player_markets.py, team_markets.py normal helpers).
 *
 * Everything here is pure math and parsing: no network, no storage, no
 * DOM. The Python versions are the tested reference; test_engine.js checks
 * this file against vectors generated from them (to ~1e-9).
 *
 * Two JavaScript gaps worth knowing about:
 *   - there is no Math.erf, so erf() below is implemented (series for
 *     small |x|, continued fraction for large |x|) and checked against
 *     Python's math.erf;
 *   - Math.round rounds halves up, Python's round() rounds halves to
 *     even, so roundHalfEven() is used wherever Python used round().
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.Engine = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ============================================================
  // SMALL HELPERS
  // ============================================================

  function roundHalfEven(x) {
    const f = Math.floor(x);
    const diff = x - f;
    if (diff < 0.5) return f;
    if (diff > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }

  // Python's round(x, ndigits) for the magnitudes used here.
  function roundTo(x, digits) {
    return Number(x.toFixed(digits));
  }

  // ============================================================
  // NORMAL DISTRIBUTION
  // ============================================================

  const SQRT_PI = Math.sqrt(Math.PI);

  function erf(x) {
    if (Number.isNaN(x)) return NaN;
    if (x === 0) return x;
    const ax = Math.abs(x);
    let result;
    if (ax < 2.5) {
      // erf(x) = 2/sqrt(pi) * exp(-x^2) * sum_n 2^n x^(2n+1) / (2n+1)!!
      // All terms positive, so no cancellation.
      let term = ax;
      let sum = ax;
      const x2 = ax * ax;
      for (let n = 0; n < 200; n++) {
        term *= (2 * x2) / (2 * n + 3);
        sum += term;
        if (term < 1e-18 * sum) break;
      }
      result = (2 / SQRT_PI) * Math.exp(-x2) * sum;
    } else {
      // erfc(x) = exp(-x^2)/sqrt(pi) / (x + (1/2)/(x + (2/2)/(x + (3/2)/(x + ...))))
      let t = ax;
      for (let k = 120; k >= 1; k--) {
        t = ax + k / 2 / t;
      }
      const erfc = Math.exp(-ax * ax) / (SQRT_PI * t);
      result = 1 - erfc;
    }
    return x < 0 ? -result : result;
  }

  function normCdf(x) {
    return 0.5 * (1.0 + erf(x / Math.sqrt(2.0)));
  }

  function normPpf(p) {
    if (p <= 0.0) return -Infinity;
    if (p >= 1.0) return Infinity;

    const a = [
      -3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
      1.38357751867269e2, -3.066479806614716e1, 2.506628277459239,
    ];
    const b = [
      -5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
      6.680131188771972e1, -1.328068155288572e1,
    ];
    const c = [
      -7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838,
      -2.549732539343734, 4.374664141464968, 2.938163982698783,
    ];
    const d = [
      7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996,
      3.754408661907416,
    ];

    const pLow = 0.02425;
    const pHigh = 1 - pLow;
    let x;

    if (p < pLow) {
      const q = Math.sqrt(-2 * Math.log(p));
      x =
        (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    } else if (p <= pHigh) {
      const q = p - 0.5;
      const r = q * q;
      x =
        ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) /
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
    } else {
      const q = Math.sqrt(-2 * Math.log(1 - p));
      x =
        -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    }

    // One Halley refinement step, as in the Python reference.
    const e = normCdf(x) - p;
    const u = e * Math.sqrt(2 * Math.PI) * Math.exp((x * x) / 2);
    return x - u / (1 + (x * u) / 2);
  }

  // ============================================================
  // ODDS CONVERSION
  // ============================================================

  // Strict version used by the player-market code: real American prices
  // only (+100 or higher, or -100 or lower).
  function impliedProbability(americanOdds) {
    if (!Number.isFinite(americanOdds) || Math.abs(americanOdds) < 100) {
      throw new Error(
        "American odds must be +100 or higher, or -100 or lower (got " +
          americanOdds +
          "); values in between aren't real prices and are almost always a typo."
      );
    }
    if (americanOdds > 0) return 100.0 / (americanOdds + 100.0);
    return Math.abs(americanOdds) / (Math.abs(americanOdds) + 100.0);
  }

  function devigTwoWay(overOdds, underOdds) {
    const pOver = impliedProbability(overOdds);
    const pUnder = impliedProbability(underOdds);
    const total = pOver + pUnder;
    return [pOver / total, pUnder / total];
  }

  // Profit per $1 staked (+400 -> 4.0, -200 -> 0.5).
  function americanToDecimalProfit(americanOdds) {
    if (americanOdds > 0) return americanOdds / 100.0;
    return 100.0 / Math.abs(americanOdds);
  }

  function marketBreakEvenProbability(americanOdds) {
    if (americanOdds > 0) return 100.0 / (americanOdds + 100.0);
    return Math.abs(americanOdds) / (Math.abs(americanOdds) + 100.0);
  }

  // Returns an integer, or null when there is no valid price.
  function probabilityToFairAmerican(probability) {
    if (probability <= 0.0 || probability >= 1.0) return null;
    if (probability < 0.5) {
      return roundHalfEven((100.0 * (1.0 - probability)) / probability);
    }
    return roundHalfEven((-100.0 * probability) / (1.0 - probability));
  }

  // The price to QUOTE: margin is added to the fair probability.
  //   mode "relative": margin is a % of the fair probability
  //   mode "pp":       margin is added in percentage points
  function oddsWithMargin(probability, margin, mode) {
    mode = mode === undefined ? "relative" : mode;
    if (mode !== "relative" && mode !== "pp") {
      throw new Error("mode must be 'relative' or 'pp' (got '" + mode + "').");
    }
    if (!Number.isFinite(margin)) {
      throw new Error("Margin must be a finite number (got " + margin + ").");
    }
    if (margin < 0) {
      throw new Error("Margin cannot be negative (got " + margin + ").");
    }
    if (probability <= 0.0 || probability >= 1.0) return null;

    const quoted =
      mode === "relative"
        ? probability * (1.0 + margin / 100.0)
        : probability + margin / 100.0;

    if (quoted >= 1.0) return null;

    return {
      quoted_probability: quoted,
      american_odds: probabilityToFairAmerican(quoted),
    };
  }

  // ============================================================
  // FOUL-OUT PRICING (calibrated table)
  // ============================================================

  const MODEL_VERSION = "1.0.0";
  const DEFAULT_SAFETY_MARGIN_PP = 2.0;

  const CALIBRATION_TABLE = [
    [30.0, 0.0597], [31.0, 0.0662], [32.0, 0.0733], [33.0, 0.0811], [34.0, 0.0897],
    [35.0, 0.0991], [36.0, 0.1093], [37.0, 0.1205], [38.0, 0.1326],
    [39.0, 0.1457], [40.0, 0.1599], [41.0, 0.1752], [42.0, 0.1917],
    [43.0, 0.2093], [44.0, 0.228], [45.0, 0.2479], [46.0, 0.2689],
    [47.0, 0.2911], [48.0, 0.3142], [49.0, 0.3383], [50.0, 0.3633],
  ];
  const PF_POINTS = CALIBRATION_TABLE.map((r) => r[0]);
  const PROB_POINTS = CALIBRATION_TABLE.map((r) => r[1]);

  function clamp01(x) {
    return Math.max(0.0, Math.min(1.0, x));
  }

  // Returns [probability, extrapolated].
  function interpolateProbability(pf) {
    if (pf < PF_POINTS[0]) {
      const [x1, y1] = CALIBRATION_TABLE[0];
      const [x2, y2] = CALIBRATION_TABLE[1];
      return [clamp01(y1 + ((pf - x1) * (y2 - y1)) / (x2 - x1)), true];
    }
    const last = CALIBRATION_TABLE.length - 1;
    if (pf > PF_POINTS[last]) {
      const [x1, y1] = CALIBRATION_TABLE[last - 1];
      const [x2, y2] = CALIBRATION_TABLE[last];
      return [clamp01(y1 + ((pf - x1) * (y2 - y1)) / (x2 - x1)), true];
    }
    // bisect_left
    let lo = 0;
    let hi = PF_POINTS.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (PF_POINTS[mid] < pf) lo = mid + 1;
      else hi = mid;
    }
    const index = lo;
    if (index === 0) return [PROB_POINTS[0], false];
    if (PF_POINTS[index] === pf) return [PROB_POINTS[index], false];

    const x1 = PF_POINTS[index - 1];
    const y1 = PROB_POINTS[index - 1];
    const x2 = PF_POINTS[index];
    const y2 = PROB_POINTS[index];
    return [y1 + ((pf - x1) * (y2 - y1)) / (x2 - x1), false];
  }

  function calculateExpectedRoi(probability, americanOdds) {
    return (
      probability * americanToDecimalProfit(americanOdds) - (1.0 - probability)
    );
  }

  function calculatePrice(pf, americanOdds, safetyMarginPp) {
    if (safetyMarginPp === undefined) safetyMarginPp = DEFAULT_SAFETY_MARGIN_PP;

    const [calibrated, extrapolated] = interpolateProbability(pf);
    const breakEven = marketBreakEvenProbability(americanOdds);
    const edge = calibrated - breakEven;
    const roi = calculateExpectedRoi(calibrated, americanOdds);
    const safetyMargin = safetyMarginPp / 100.0;
    const required = breakEven + safetyMargin;
    const marginAbove = calibrated - required;
    const acceptableBreakEven = calibrated - safetyMargin;

    const minimumAcceptable =
      acceptableBreakEven > 0.0
        ? probabilityToFairAmerican(acceptableBreakEven)
        : null;

    let status;
    if (edge < 0) status = "NO_EDGE";
    else if (marginAbove < 0) status = "FAIR_OR_BORDERLINE";
    else status = "PASS";

    return {
      model_version: MODEL_VERSION,
      pf: roundTo(pf, 4),
      calibrated_probability: roundTo(calibrated, 6),
      calibrated_probability_percent: roundTo(calibrated * 100, 4),
      market_american_odds: americanOdds,
      market_break_even_probability: roundTo(breakEven, 6),
      market_break_even_probability_percent: roundTo(breakEven * 100, 4),
      probability_edge: roundTo(edge, 6),
      probability_edge_pp: roundTo(edge * 100, 4),
      fair_american_odds: probabilityToFairAmerican(calibrated),
      expected_roi: roundTo(roi, 6),
      expected_roi_percent: roundTo(roi * 100, 4),
      safety_margin_pp: safetyMarginPp,
      required_probability: roundTo(required, 6),
      required_probability_percent: roundTo(required * 100, 4),
      margin_above_requirement_pp: roundTo(marginAbove * 100, 4),
      minimum_acceptable_american_odds: minimumAcceptable,
      status: status,
      extrapolated: extrapolated,
    };
  }

  // ============================================================
  // PLAYER MARKETS: LADDERS, DIRECT MARKETS, COMBINING
  // ============================================================

  const MAX_SINGLE_LINE_GAP = 2.0;

  // lines: array of [line, overOdds, underOdds]; returns sorted ladder of
  // {line, probability_over}.
  function buildLadder(lines) {
    for (const l of lines) {
      if (!Number.isFinite(l[0])) {
        throw new Error("Line must be a finite number (got " + l[0] + ").");
      }
    }
    return lines
      .map((l) => ({ line: l[0], probability_over: devigTwoWay(l[1], l[2])[0] }))
      .sort((p, q) => p.line - q.line);
  }

  function exponentialExtrapolate(xLow, yLow, xHigh, yHigh, targetLine) {
    const epsilon = 1e-6;
    yLow = Math.max(yLow, epsilon);
    yHigh = Math.max(yHigh, epsilon);

    let slope = (Math.log(yHigh) - Math.log(yLow)) / (xHigh - xLow);
    slope = Math.min(slope, 0.0);

    let anchorX, anchorY;
    if (targetLine >= xHigh) {
      anchorX = xHigh;
      anchorY = yHigh;
    } else {
      anchorX = xLow;
      anchorY = yLow;
    }
    const logProbability = Math.log(anchorY) + slope * (targetLine - anchorX);
    return Math.max(0.0, Math.min(1.0, Math.exp(logProbability)));
  }

  function checkSingleLineGap(line, targetLine, threshold) {
    if (Math.abs(targetLine - line) > MAX_SINGLE_LINE_GAP) {
      throw new Error(
        "Only one line (" + line + ") was provided, which is too far from the " +
          threshold + "+ threshold to estimate (limit: " + MAX_SINGLE_LINE_GAP +
          " away). Add at least one more line, ideally one at or near the " +
          "threshold, or use the milestone price directly."
      );
    }
  }

  // Returns [probability, extrapolated].
  function probabilityAtThreshold(ladder, threshold) {
    if (!ladder.length) {
      throw new Error("Ladder is empty — no alt lines available for this player.");
    }
    if (!Number.isFinite(threshold)) {
      throw new Error("Threshold must be a finite number (got " + threshold + ").");
    }

    const targetLine = threshold - 0.5;
    const lines = ladder.map((p) => p.line);
    const probs = ladder.map((p) => p.probability_over);
    const n = lines.length;

    if (targetLine < lines[0]) {
      if (n === 1) {
        checkSingleLineGap(lines[0], targetLine, threshold);
        return [clamp01(probs[0]), true];
      }
      return [
        exponentialExtrapolate(lines[0], probs[0], lines[1], probs[1], targetLine),
        true,
      ];
    }

    if (targetLine > lines[n - 1]) {
      if (n === 1) {
        checkSingleLineGap(lines[0], targetLine, threshold);
        return [clamp01(probs[n - 1]), true];
      }
      return [
        exponentialExtrapolate(
          lines[n - 2], probs[n - 2], lines[n - 1], probs[n - 1], targetLine
        ),
        true,
      ];
    }

    for (const point of ladder) {
      if (point.line === targetLine) return [point.probability_over, false];
    }

    for (let i = 0; i < n - 1; i++) {
      if (lines[i] < targetLine && targetLine < lines[i + 1]) {
        const x1 = lines[i], y1 = probs[i];
        const x2 = lines[i + 1], y2 = probs[i + 1];
        return [clamp01(y1 + ((targetLine - x1) * (y2 - y1)) / (x2 - x1)), false];
      }
    }
    throw new Error("Failed to locate target_line within the ladder.");
  }

  // Returns [probability, devigged].
  function directMarketProbability(yesOdds, noOdds) {
    if (noOdds !== undefined && noOdds !== null) {
      return [devigTwoWay(yesOdds, noOdds)[0], true];
    }
    return [impliedProbability(yesOdds), false];
  }

  function combineAnyPlayer(probabilities) {
    if (!probabilities.length) return 0.0;
    let product = 1.0;
    for (const p of probabilities) {
      if (!(p >= 0.0 && p <= 1.0)) {
        throw new Error("Probability " + p + " is out of [0, 1] range.");
      }
      product *= 1.0 - p;
    }
    return 1.0 - product;
  }

  // ------------------------------------------------------------
  // Single base line model, one set of constants per stat -- see
  // player_markets.py for how each was fitted and what was (and wasn't)
  // checked. Assists has no model and is refused.
  // ------------------------------------------------------------

  const SINGLE_LINE_MODELS = {
    points: { anchor_line: 25.0, spread_at_anchor: 0.31, exponent: 0.5, validated_max_threshold: 50.0 },
    rebounds: { anchor_line: 10.0, spread_at_anchor: 0.36, exponent: 0.4, validated_max_threshold: 16.0 },
    assists: { anchor_line: 5.0, spread_at_anchor: 0.46, exponent: 0.4, validated_max_threshold: 14.0 },
  };
  const SINGLE_LINE_SPREAD_AT_25 = SINGLE_LINE_MODELS.points.spread_at_anchor;
  const SINGLE_LINE_SPREAD_EXPONENT = SINGLE_LINE_MODELS.points.exponent;
  const SINGLE_LINE_VALIDATED_MAX_THRESHOLD = SINGLE_LINE_MODELS.points.validated_max_threshold;

  function hasSingleLineModel(stat) {
    return Object.prototype.hasOwnProperty.call(SINGLE_LINE_MODELS, stat);
  }

  function singleLineProbability(line, overOdds, underOdds, threshold, stat) {
    stat = stat === undefined ? "points" : stat;
    if (!hasSingleLineModel(stat)) {
      throw new Error(
        "No base-line model has been checked for '" + stat + "'. Use milestone prices " +
          "or several alt lines per player instead."
      );
    }
    const model = SINGLE_LINE_MODELS[stat];
    if (!Number.isFinite(line) || line <= 0) {
      throw new Error("Line must be a positive finite number (got " + line + ").");
    }
    if (!Number.isFinite(threshold) || threshold <= 0.5) {
      throw new Error(
        "Threshold must be a finite number above 0.5 (got " + threshold + ")."
      );
    }
    const pOver = devigTwoWay(overOdds, underOdds)[0];
    const spread =
      model.spread_at_anchor * Math.pow(line / model.anchor_line, -model.exponent);
    const mu = Math.log(line) - spread * normPpf(1.0 - pOver);
    const z = (Math.log(threshold - 0.5) - mu) / spread;
    return 1.0 - normCdf(z);
  }

  // ============================================================
  // TEAM MARKETS (port of team_markets.py)
  // ============================================================

  function normPdf(x) {
    return Math.exp(-0.5 * x * x) / Math.sqrt(2.0 * Math.PI);
  }

  function bivariateNormalCdfRaw(za, zb, rho, nSteps, lower) {
    const denom = Math.sqrt(1 - rho * rho);
    const integrand = (x) => normPdf(x) * normCdf((zb - rho * x) / denom);
    if (za <= lower) return 0.0;
    const step = (za - lower) / nSteps;
    let total = integrand(lower) + integrand(za);
    for (let i = 1; i < nSteps; i++) {
      total += integrand(lower + i * step) * (i % 2 === 1 ? 4 : 2);
    }
    return (total * step) / 3.0;
  }

  // P(Za <= za, Zb <= zb) for a standard bivariate normal.
  function bivariateNormalCdf(za, zb, rho, nSteps, lower) {
    nSteps = nSteps === undefined ? 2000 : nSteps;
    lower = lower === undefined ? -8.0 : lower;
    rho = Math.max(-0.999999, Math.min(0.999999, rho));
    const r1 = bivariateNormalCdfRaw(za, zb, rho, nSteps, lower);
    const r2 = bivariateNormalCdfRaw(zb, za, rho, nSteps, lower);
    return Math.max(0.0, Math.min(1.0, (r1 + r2) / 2.0));
  }

  // ladder: [{line, probability_over}]; returns [mean, std].
  function fitNormalFromLadder(ladder) {
    for (const pt of ladder) {
      if (!(Number.isFinite(pt.line) && pt.probability_over > 0.0 && pt.probability_over < 1.0)) {
        throw new Error(
          "Ladder point (line=" + pt.line + ", probability_over=" + pt.probability_over +
            ") is invalid: the line must be finite and the probability strictly between 0 and 1."
        );
      }
    }
    if (ladder.length < 2) {
      throw new Error(
        "Need at least 2 alt-line points to fit a standard deviation (one line only pins " +
          "down the mean). If you only have a single line, supply an assumed std directly."
      );
    }
    const z = ladder.map((pt) => normPpf(1.0 - pt.probability_over));
    const lines = ladder.map((pt) => pt.line);
    const n = ladder.length;
    const meanZ = z.reduce((a, b) => a + b, 0) / n;
    const meanLine = lines.reduce((a, b) => a + b, 0) / n;
    let cov = 0;
    let varZ = 0;
    for (let i = 0; i < n; i++) {
      cov += (z[i] - meanZ) * (lines[i] - meanLine);
      varZ += (z[i] - meanZ) * (z[i] - meanZ);
    }
    cov /= n;
    varZ /= n;
    if (varZ === 0.0) {
      throw new Error("All ladder points produced the same z-score -- can't fit a standard deviation from degenerate data.");
    }
    const std = cov / varZ;
    const mean = meanLine - std * meanZ;
    if (std <= 0.0) {
      throw new Error(
        "Fitted a non-positive standard deviation (" + std.toFixed(4) + ") from this ladder, " +
          "which means the input data is inconsistent: Over probabilities must fall as the line rises. " +
          "Check that the lines were taken at the same time and entered correctly."
      );
    }
    return [mean, std];
  }

  function impliedCorrelationFromVariances(varA, varB, varTotal) {
    if (![varA, varB, varTotal].every(Number.isFinite)) {
      throw new Error("Variances must be finite numbers.");
    }
    if (varA <= 0.0 || varB <= 0.0) {
      throw new Error("var_a and var_b must be positive (got " + varA + ", " + varB + ").");
    }
    const covariance = (varTotal - varA - varB) / 2.0;
    const rho = covariance / Math.sqrt(varA * varB);
    return Math.max(-1.0, Math.min(1.0, rho));
  }

  function jointProbabilityBothExceed(meanA, stdA, thresholdA, meanB, stdB, thresholdB, rho) {
    if (![meanA, stdA, thresholdA, meanB, stdB, thresholdB, rho].every(Number.isFinite)) {
      throw new Error("All inputs must be finite numbers.");
    }
    if (stdA <= 0.0 || stdB <= 0.0) {
      throw new Error("std_a and std_b must both be positive (got " + stdA + ", " + stdB + ").");
    }
    const za = (thresholdA - meanA) / stdA;
    const zb = (thresholdB - meanB) / stdB;
    const probability = 1.0 - normCdf(za) - normCdf(zb) + bivariateNormalCdf(za, zb, rho);
    const pA = 1.0 - normCdf(za);
    const pB = 1.0 - normCdf(zb);
    const lowerBound = Math.max(0.0, pA + pB - 1.0);
    const upperBound = Math.min(pA, pB);
    return Math.max(lowerBound, Math.min(upperBound, probability));
  }

  // "100+" means 100 or more: more than 99.5, same convention as players.
  function reachToCutoff(reach) {
    if (!Number.isFinite(reach)) {
      throw new Error("Threshold must be a finite number (got " + reach + ").");
    }
    return reach - 0.5;
  }

  function teamReachProbability(mean, std, reach) {
    if (!(Number.isFinite(mean) && Number.isFinite(std))) {
      throw new Error("mean and std must be finite numbers.");
    }
    if (std <= 0.0) throw new Error("std must be positive (got " + std + ").");
    return 1.0 - normCdf((reachToCutoff(reach) - mean) / std);
  }

  function meanFromSingleLine(line, overOdds, underOdds, std) {
    if (!Number.isFinite(line)) {
      throw new Error("Line must be a finite number (got " + line + ").");
    }
    if (!Number.isFinite(std) || std <= 0.0) {
      throw new Error("std must be a positive finite number (got " + std + ").");
    }
    const pOver = devigTwoWay(overOdds, underOdds)[0];
    return line - std * normPpf(1.0 - pOver);
  }

  function priceBothTeamsReach(meanA, stdA, reachA, meanB, stdB, reachB, rho) {
    const joint = jointProbabilityBothExceed(
      meanA, stdA, reachToCutoff(reachA), meanB, stdB, reachToCutoff(reachB), rho
    );
    const pA = teamReachProbability(meanA, stdA, reachA);
    const pB = teamReachProbability(meanB, stdB, reachB);
    return {
      probability_a: pA,
      probability_b: pB,
      joint_probability: joint,
      independent_probability: pA * pB,
    };
  }

  // ---- what the page calls: lines are [{line, over, under}] ----

  function ladderFromLines(lines) {
    return buildLadder(lines.map((l) => [l.line, l.over, l.under]));
  }

  // A team's distribution from its posted lines: fitted from 2+ lines, or
  // pinned from a single line using an assumed spread the caller supplies.
  function resolveTeam(lines, assumedStd, label) {
    if (!lines || !lines.length) {
      throw new Error(label + ": no lines entered.");
    }
    if (lines.length >= 2) {
      const [mean, std] = fitNormalFromLadder(ladderFromLines(lines));
      return { mean: mean, std: std, basis: "fitted from " + lines.length + " lines" };
    }
    if (assumedStd === null || assumedStd === undefined) {
      throw new Error(
        label + ": one line only fixes the average, not the spread. Add another line, " +
          "or type an assumed spread (standard deviation) in points."
      );
    }
    const l = lines[0];
    return {
      mean: meanFromSingleLine(l.line, l.over, l.under, assumedStd),
      std: assumedStd,
      basis: "one line with an assumed spread of " + assumedStd,
    };
  }

  function priceSingleTeam(lines, reach, assumedStd, label) {
    const team = resolveTeam(lines, assumedStd, label || "Team");
    return {
      mean: team.mean, std: team.std, basis: team.basis,
      probability: teamReachProbability(team.mean, team.std, reach),
    };
  }

  // Both teams reach their own threshold. The link between the teams comes
  // from the game-total lines (all three need 2+ lines), or is typed in.
  function priceBothTeams(homeLines, awayLines, totalLines, reachHome, reachAway, assumedStd, rhoOverride) {
    const warnings = [];
    const home = resolveTeam(homeLines, assumedStd, "Home team");
    const away = resolveTeam(awayLines, assumedStd, "Away team");
    let rho;
    let rhoSource;
    let totalFit = null;

    if (rhoOverride !== null && rhoOverride !== undefined) {
      if (!Number.isFinite(rhoOverride) || rhoOverride <= -1 || rhoOverride >= 1) {
        throw new Error("Correlation must be between -1 and 1.");
      }
      rho = rhoOverride;
      rhoSource = "typed in";
    } else {
      if (!(homeLines.length >= 2 && awayLines.length >= 2)) {
        throw new Error(
          "To work out the link between the teams from the game total, both teams need 2+ lines. " +
            "Add lines, or type a correlation."
        );
      }
      if (!totalLines || totalLines.length < 2) {
        throw new Error(
          "The game-total box needs 2+ lines to work out the link between the teams. " +
            "Add lines, or type a correlation."
        );
      }
      totalFit = fitNormalFromLadder(ladderFromLines(totalLines));
      rho = impliedCorrelationFromVariances(
        home.std * home.std, away.std * away.std, totalFit[1] * totalFit[1]
      );
      rhoSource = "worked out from the game total";
      if (rho >= 0.999 || rho <= -0.999) {
        warnings.push("The three sets of lines disagree badly (correlation came out at " + rho.toFixed(3) +
          "). Check that they are all for the same window and were taken at the same time.");
      } else if (rho < 0) {
        warnings.push("The lines imply the teams' scoring moves in opposite directions (correlation " +
          rho.toFixed(2) + "), which is unusual. Check the lines.");
      }
    }

    const r = priceBothTeamsReach(home.mean, home.std, reachHome, away.mean, away.std, reachAway, rho);
    return {
      home: home, away: away, rho: rho, rho_source: rhoSource, total: totalFit,
      probability_home: r.probability_a, probability_away: r.probability_b,
      joint_probability: r.joint_probability, independent_probability: r.independent_probability,
      warnings: warnings,
    };
  }

  // ============================================================
  // PASTE PARSERS
  // ============================================================

  const PRICE_RE = /^[+\-]\s*\d+$/;

  function parseAmerican(text) {
    const cleaned = text.replace(/\s+/g, "");
    if (!PRICE_RE.test(cleaned)) return NaN;
    return parseInt(cleaned, 10);
  }

  function checkPrice(odds, label, lineNo, errors) {
    if (!Number.isFinite(odds)) {
      errors.push("Line " + lineNo + ": couldn't read the " + label + " price.");
      return false;
    }
    if (Math.abs(odds) < 100) {
      errors.push(
        "Line " + lineNo + ": " + label + " price " + odds +
          " isn't a real American price (must be +100 or higher, or -100 or lower)."
      );
      return false;
    }
    return true;
  }

  /*
   * Over/Under paste. Accepted shapes (mix freely):
   *
   *   Jayson Tatum            Jayson Tatum, 26.5, -114, -114
   *   O 26.5-114
   *   U 26.5-114              (more O/U pairs under the same name add lines)
   *
   * Returns { players: [{name, lines: [{line, over, under}]}], errors: [] }.
   * Anything that can't be read is reported, never guessed.
   */
  function parseOverUnderPaste(text) {
    const errors = [];
    const players = [];
    const byName = new Map();
    let current = null;
    let pending = null; // { side, line, odds, lineNo } waiting for its partner

    function getPlayer(name) {
      const key = name.toLowerCase();
      if (byName.has(key)) return byName.get(key);
      const player = { name: name, lines: [] };
      byName.set(key, player);
      players.push(player);
      return player;
    }

    function addLine(player, line, over, under, lineNo) {
      if (player.lines.some((l) => l.line === line)) {
        errors.push(
          "Line " + lineNo + ": " + player.name + " already has a " + line + " line."
        );
        return;
      }
      player.lines.push({ line: line, over: over, under: under });
    }

    function flushPending() {
      if (pending) {
        errors.push(
          "Line " + pending.lineNo + ": " + (current ? current.name : "?") + " has a " +
            (pending.side === "O" ? "Over" : "Under") + " " + pending.line +
            " with no matching " + (pending.side === "O" ? "Under" : "Over") + "."
        );
        pending = null;
      }
    }

    const rows = String(text).split(/\r?\n/);
    const sideRe = /^(over|under|o|u)\s*(\d+(?:\.\d+)?)\s*([+\-]\s*\d+)$/i;
    const csvRe = /^(.+?)\s*[,;\t]\s*(\d+(?:\.\d+)?)\s*[,;\t]\s*([+\-]\s*\d+)\s*[,;\t]\s*([+\-]\s*\d+)$/;

    rows.forEach((raw, i) => {
      const lineNo = i + 1;
      const row = raw.trim();
      if (!row) return;

      const csv = row.match(csvRe);
      if (csv) {
        flushPending();
        const name = csv[1].trim();
        const line = parseFloat(csv[2]);
        const over = parseAmerican(csv[3]);
        const under = parseAmerican(csv[4]);
        current = getPlayer(name);
        if (checkPrice(over, "Over", lineNo, errors) &&
            checkPrice(under, "Under", lineNo, errors)) {
          addLine(current, line, over, under, lineNo);
        }
        return;
      }

      const side = row.match(sideRe);
      if (side) {
        if (!current) {
          errors.push("Line " + lineNo + ": an Over/Under price appears before any player name.");
          return;
        }
        const which = side[1][0].toUpperCase();
        const line = parseFloat(side[2]);
        const odds = parseAmerican(side[3]);
        if (!checkPrice(odds, which === "O" ? "Over" : "Under", lineNo, errors)) {
          pending = null;
          return;
        }
        if (!pending) {
          pending = { side: which, line: line, odds: odds, lineNo: lineNo };
          return;
        }
        if (pending.side === which) {
          errors.push(
            "Line " + lineNo + ": two " + (which === "O" ? "Over" : "Under") +
              " prices in a row for " + current.name + "."
          );
          pending = { side: which, line: line, odds: odds, lineNo: lineNo };
          return;
        }
        if (pending.line !== line) {
          errors.push(
            "Line " + lineNo + ": " + current.name + "'s Over and Under are on " +
              "different lines (" + pending.line + " vs " + line + ")."
          );
          pending = null;
          return;
        }
        const over = which === "O" ? odds : pending.odds;
        const under = which === "U" ? odds : pending.odds;
        addLine(current, line, over, under, lineNo);
        pending = null;
        return;
      }

      // Anything else is a player name.
      flushPending();
      current = getPlayer(row);
    });
    flushPending();

    players.forEach((p) => {
      if (!p.lines.length) {
        errors.push(p.name + ": no complete Over/Under line was found.");
      }
    });

    return {
      players: players.filter((p) => p.lines.length > 0),
      errors: errors,
    };
  }

  /*
   * A team's (or the game total's) lines: O/U pairs with no player name.
   *   O 99.5-110
   *   U 99.5-110
   *   O 104.5+150
   *   U 104.5-190
   * A name line such as "Home" is allowed and ignored.
   */
  function parseLadderPaste(text) {
    const r = parseOverUnderPaste("Team\n" + String(text));
    // A stray name line creates a second, empty entry that is already
    // filtered out; two entries WITH lines means two sets were pasted.
    if (r.players.length > 1) {
      r.errors.push("Expected one set of lines here but found " + r.players.length + " (" +
        r.players.map((p) => p.name).join(", ") + ").");
    }
    const lines = r.players.length ? r.players[0].lines : [];
    // Drop the dummy entry's own "no complete line" note, and undo the one
    // line the dummy name added to every reported line number.
    const errors = r.errors
      .filter((e) => !/^Team: no complete/.test(e))
      .map((e) => e.replace(/^Line (\d+):/, (m, n) => "Line " + (Number(n) - 1) + ":"));
    return { lines: lines.sort((a, b) => a.line - b.line), errors: errors };
  }

  /*
   * Milestone-list paste: one price per player for one threshold.
   *   Jayson Tatum -172        Tatum, +450        Jayson Tatum
   *                                               +450
   */
  function parseMilestonePaste(text) {
    const errors = [];
    const entries = [];
    const seen = new Set();
    let pendingName = null;

    function add(name, odds, lineNo) {
      if (!checkPrice(odds, "milestone", lineNo, errors)) return;
      const key = name.toLowerCase();
      if (seen.has(key)) {
        errors.push("Line " + lineNo + ": " + name + " appears more than once.");
        return;
      }
      seen.add(key);
      entries.push({ name: name, odds: odds });
    }

    String(text).split(/\r?\n/).forEach((raw, i) => {
      const lineNo = i + 1;
      const row = raw.trim();
      if (!row) return;

      if (PRICE_RE.test(row.replace(/\s+/g, ""))) {
        if (pendingName === null) {
          errors.push("Line " + lineNo + ": a price appears with no player name.");
        } else {
          add(pendingName, parseAmerican(row), lineNo);
          pendingName = null;
        }
        return;
      }

      const m = row.match(/^(.*?)[\s,:;\t]+([+\-]\s*\d+)$/);
      if (m && m[1].trim()) {
        if (pendingName !== null) {
          errors.push("Line " + (lineNo - 1) + ": " + pendingName + " has no price.");
        }
        pendingName = null;
        add(m[1].trim(), parseAmerican(m[2]), lineNo);
        return;
      }

      if (pendingName !== null) {
        errors.push("Line " + lineNo + ": " + pendingName + " has no price.");
      }
      pendingName = row;
    });
    if (pendingName !== null) {
      errors.push(pendingName + " has no price.");
    }

    return { entries: entries, errors: errors };
  }

  /*
   * Direct Yes/No paste (double-double / triple-double): a Yes price and,
   * if the book posts it, a No price.
   *   Nikola Jokic, -250, +190      Nikola Jokic -250 +190      Nikola Jokic -250
   */
  // The sportsbook's copied text puts the market on the name ("Jalen Duren
  // Double-Double"), a "[Yes](link)" label on its own line, then the price on
  // the next line, and uses a real minus sign. Rewrite that into one
  // "Name price" (or "Name yes no") row per player so the parser below can
  // read it. Text already in the "Name price" form passes through unchanged
  // apart from the minus sign.
  const MARKET_SUFFIX = /\s+(double[- ]double|triple[- ]double|to foul out|foul out|to be ejected|to be ejected from the game)\s*$/i;
  function normalizeDirectPaste(text) {
    const lines = String(text)
      .replace(/[\u2212\u2013\u2014]/g, "-")
      .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .split(/\r?\n/)
      .map((l) => l.trim());
    const isLabel = (l) => /^(yes|no)$/i.test(l);
    if (!lines.some(isLabel)) {
      // Name on one line, price on the next ("Jalen Duren" / "-128").
      const bare = (l) => /^[+-]\s*\d+$/.test(l);
      const out = [];
      let pending = null;
      for (const l of lines) {
        if (!l) continue;
        if (bare(l) && pending !== null) { pending.prices.push(l.replace(/\s+/g, "")); continue; }
        if (pending !== null) { out.push(pending.name + " " + pending.prices.join(" ")); pending = null; }
        if (bare(l)) { out.push(l); continue; }
        if (/[+-]\s*\d+$/.test(l)) { out.push(l); continue; }
        pending = { name: l.replace(MARKET_SUFFIX, "").trim(), prices: [] };
      }
      if (pending !== null) out.push(pending.name + " " + pending.prices.join(" "));
      return out.join("\n");
    }
    const rows = [];
    let cur = null;
    const flush = () => {
      if (cur && cur.yes !== null) rows.push(cur.name + " " + cur.yes + (cur.no !== null ? " " + cur.no : ""));
      else if (cur) rows.push(cur.name);
      cur = null;
    };
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i];
      if (!l) continue;
      if (isLabel(l)) {
        const price = lines[i + 1] || "";
        if (cur && /^[+-]\s*\d+$/.test(price)) {
          if (/^yes$/i.test(l)) cur.yes = price.replace(/\s+/g, ""); else cur.no = price.replace(/\s+/g, "");
          i += 1;
        }
        continue;
      }
      if (/^[+-]\s*\d+$/.test(l)) continue;
      flush();
      cur = { name: l.replace(MARKET_SUFFIX, "").trim(), yes: null, no: null };
    }
    flush();
    return rows.join("\n");
  }

  function parseDirectPaste(text) {
    text = normalizeDirectPaste(text);
    const errors = [];
    const entries = [];
    const seen = new Set();
    const re = /^(.*?)[\s,:;\t]+([+\-]\s*\d+)(?:[\s,;\t]+([+\-]\s*\d+))?$/;

    String(text).split(/\r?\n/).forEach((raw, i) => {
      const lineNo = i + 1;
      const row = raw.trim();
      if (!row) return;
      const m = row.match(re);
      if (!m || !m[1].trim()) {
        errors.push("Line " + lineNo + ": expected a player name followed by a Yes price (and optionally a No price).");
        return;
      }
      const name = m[1].trim();
      const yes = parseAmerican(m[2]);
      const no = m[3] === undefined ? null : parseAmerican(m[3]);
      if (!checkPrice(yes, "Yes", lineNo, errors)) return;
      if (no !== null && !checkPrice(no, "No", lineNo, errors)) return;
      const key = name.toLowerCase();
      if (seen.has(key)) {
        errors.push("Line " + lineNo + ": " + name + " appears more than once.");
        return;
      }
      seen.add(key);
      entries.push({ name: name, yes: yes, no: no });
    });
    return { entries: entries, errors: errors };
  }

  // ============================================================
  // PROJECTED PF FROM TWO TEAMS
  //
  // The model's PF for a game between A and B is
  //   (A.last10 + B.last10 + A.season + B.season) / 2
  // where last10 is a team's average fouls over its last 10 games and
  // season is its average over the games it has played this season.
  // ============================================================

  const TEAM_NAMES = {
    ATL: "Atlanta Hawks", BKN: "Brooklyn Nets", BOS: "Boston Celtics", CHA: "Charlotte Hornets",
    CHI: "Chicago Bulls", CLE: "Cleveland Cavaliers", DAL: "Dallas Mavericks", DEN: "Denver Nuggets",
    DET: "Detroit Pistons", GSW: "Golden State Warriors", HOU: "Houston Rockets", IND: "Indiana Pacers",
    LAC: "Los Angeles Clippers", LAL: "Los Angeles Lakers", MEM: "Memphis Grizzlies", MIA: "Miami Heat",
    MIL: "Milwaukee Bucks", MIN: "Minnesota Timberwolves", NOP: "New Orleans Pelicans", NYK: "New York Knicks",
    OKC: "Oklahoma City Thunder", ORL: "Orlando Magic", PHI: "Philadelphia 76ers", PHX: "Phoenix Suns",
    POR: "Portland Trail Blazers", SAC: "Sacramento Kings", SAN: "San Antonio Spurs", TOR: "Toronto Raptors",
    UTA: "Utah Jazz", WAS: "Washington Wizards",
  };

  const TEAM_FOUL_MIN = 10;   // fouls per team per game: anything outside this
  const TEAM_FOUL_MAX = 35;   // range is a typo, not a real average

  // Reads the block the refresh cell prints. Returns { data, errors }.
  function parseTeamFouls(text) {
    const errors = [];
    let raw;
    try {
      raw = JSON.parse(String(text).trim());
    } catch (e) {
      return { data: null, errors: ["That isn't the block the refresh cell prints. Copy everything from the first { to the last }."] };
    }
    if (raw === null || typeof raw !== "object" || raw.teams === null || typeof raw.teams !== "object" || Array.isArray(raw.teams)) {
      return { data: null, errors: ["The block has no \"teams\" section."] };
    }
    if (typeof raw.as_of !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(raw.as_of) || Number.isNaN(Date.parse(raw.as_of))) {
      errors.push("The block has no valid \"as_of\" date (expected YYYY-MM-DD).");
    }
    const teams = {};
    Object.keys(raw.teams).forEach((key) => {
      const code = key.toUpperCase();
      const t = raw.teams[key];
      if (!Object.prototype.hasOwnProperty.call(TEAM_NAMES, code)) {
        errors.push(key + ": not a team code this page knows.");
        return;
      }
      const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
      const last10 = t === null || typeof t !== "object" ? null : num(t.last10);
      const season = t === null || typeof t !== "object" ? null : num(t.season);
      if (last10 === null || last10 < TEAM_FOUL_MIN || last10 > TEAM_FOUL_MAX) {
        errors.push(code + ": last10 must be a number between " + TEAM_FOUL_MIN + " and " + TEAM_FOUL_MAX + ".");
        return;
      }
      if (t.season !== null && t.season !== undefined && (season === null || season < TEAM_FOUL_MIN || season > TEAM_FOUL_MAX)) {
        errors.push(code + ": season must be a number between " + TEAM_FOUL_MIN + " and " + TEAM_FOUL_MAX + ", or left out.");
        return;
      }
      const g = t.games_this_season;
      teams[code] = {
        last10: last10,
        season: season,
        games_this_season: typeof g === "number" && Number.isInteger(g) && g >= 0 ? g : null,
      };
    });
    if (!Object.keys(teams).length) errors.push("No usable teams in that block.");
    return { data: errors.length ? null : { as_of: raw.as_of, teams: teams }, errors: errors };
  }

  // PF for a game between two teams. todayIso is YYYY-MM-DD, used only for the age warning.
  function projectedPf(codeA, codeB, data, todayIso) {
    const notes = [];
    if (codeA === codeB) throw new Error("Pick two different teams.");
    const a = data.teams[codeA], b = data.teams[codeB];
    if (!a || !b) throw new Error("No data for " + (!a ? codeA : codeB) + ".");
    let fallback = false;
    const seasonOf = (t, code) => {
      if (t.season === null || t.season === undefined) {
        fallback = true;
        notes.push(code + " has no games yet this season, so its last 10 games stand in for the season half. That is not how the model was built.");
        return t.last10;
      }
      return t.season;
    };
    const sa = seasonOf(a, codeA), sb = seasonOf(b, codeB);
    [[codeA, a], [codeB, b]].forEach(([code, t]) => {
      if (t.games_this_season !== null && t.games_this_season > 0 && t.games_this_season < 5) {
        notes.push(code + "'s season average rests on only " + t.games_this_season + " game" + (t.games_this_season === 1 ? "" : "s") + ".");
      }
    });
    if (todayIso) {
      const days = Math.round((Date.parse(todayIso) - Date.parse(data.as_of)) / 86400000);
      if (Number.isFinite(days) && days > 3) notes.push("The team data is " + days + " days old (as of " + data.as_of + "). Refresh it before pricing.");
    }
    return {
      pf: (a.last10 + b.last10 + sa + sb) / 2,
      last10_total: a.last10 + b.last10,
      season_total: sa + sb,
      fallback: fallback,
      notes: notes,
    };
  }

  // ============================================================
  // ROSTER-LEVEL PRICING (what the page calls)
  // ============================================================

  function describePrice(probability, marginValue, marginMode, bookOdds) {
    const out = {
      probability: probability,
      fair_american_odds: probabilityToFairAmerican(probability),
      with_margin: null,
      vs_book: null,
    };
    if (marginValue !== undefined && marginValue !== null) {
      out.with_margin = oddsWithMargin(probability, marginValue, marginMode);
    }
    if (bookOdds !== undefined && bookOdds !== null) {
      if (!Number.isFinite(bookOdds) || Math.abs(bookOdds) < 100) {
        throw new Error("The book's price must be +100 or higher, or -100 or lower.");
      }
      const breakEven = marketBreakEvenProbability(bookOdds);
      out.vs_book = {
        american_odds: bookOdds,
        break_even_probability: breakEven,
        edge: probability - breakEven,
        expected_roi: calculateExpectedRoi(probability, bookOdds),
      };
    }
    return out;
  }

  // Points, one base line per player. players: parseOverUnderPaste output.
  function priceSingleLineRoster(players, threshold, stat) {
    stat = stat === undefined ? "points" : stat;
    const perPlayer = [];
    const skipped = [];
    players.forEach((p) => {
      if (p.lines.length !== 1) {
        skipped.push({ name: p.name, reason: "has " + p.lines.length + " lines; single-line mode needs exactly one" });
        return;
      }
      const l = p.lines[0];
      perPlayer.push({
        name: p.name,
        line: l.line,
        probability: singleLineProbability(l.line, l.over, l.under, threshold, stat),
      });
    });
    return {
      per_player: perPlayer,
      skipped: skipped,
      combined: combineAnyPlayer(perPlayer.map((x) => x.probability)),
      beyond_validated_range: threshold > SINGLE_LINE_MODELS[stat].validated_max_threshold,
    };
  }

  // Any stat, a full ladder per player (needs >= 2 lines or one near the threshold).
  function priceLadderRoster(players, threshold) {
    const perPlayer = [];
    const failed = [];
    let anyExtrapolated = false;
    players.forEach((p) => {
      try {
        const ladder = buildLadder(p.lines.map((l) => [l.line, l.over, l.under]));
        const [prob, extrapolated] = probabilityAtThreshold(ladder, threshold);
        anyExtrapolated = anyExtrapolated || extrapolated;
        perPlayer.push({ name: p.name, probability: prob, extrapolated: extrapolated });
      } catch (e) {
        failed.push({ name: p.name, reason: e.message });
      }
    });
    return {
      per_player: perPlayer,
      failed: failed,
      combined: combineAnyPlayer(perPlayer.map((x) => x.probability)),
      any_extrapolated: anyExtrapolated,
    };
  }

  // Milestone list: the book's own price per player, one threshold.
  function priceMilestoneRoster(entries) {
    const perPlayer = entries.map((e) => ({
      name: e.name,
      probability: impliedProbability(e.odds),
    }));
    return {
      per_player: perPlayer,
      combined: combineAnyPlayer(perPlayer.map((x) => x.probability)),
      devigged: false,
    };
  }

  // Double-double / triple-double: each player's own Yes (and No) line.
  function priceDirectRoster(entries) {
    let anyNotDevigged = false;
    const perPlayer = entries.map((e) => {
      const [probability, devigged] = directMarketProbability(e.yes, e.no);
      if (!devigged) anyNotDevigged = true;
      return { name: e.name, probability: probability, devigged: devigged };
    });
    return {
      per_player: perPlayer,
      combined: combineAnyPlayer(perPlayer.map((x) => x.probability)),
      any_not_devigged: anyNotDevigged,
    };
  }

  return {
    // math
    erf: erf, normCdf: normCdf, normPpf: normPpf,
    // odds
    impliedProbability: impliedProbability, devigTwoWay: devigTwoWay,
    americanToDecimalProfit: americanToDecimalProfit,
    marketBreakEvenProbability: marketBreakEvenProbability,
    probabilityToFairAmerican: probabilityToFairAmerican,
    oddsWithMargin: oddsWithMargin,
    // foul
    MODEL_VERSION: MODEL_VERSION, CALIBRATION_TABLE: CALIBRATION_TABLE,
    interpolateProbability: interpolateProbability,
    calculateExpectedRoi: calculateExpectedRoi, calculatePrice: calculatePrice,
    // player markets
    MAX_SINGLE_LINE_GAP: MAX_SINGLE_LINE_GAP,
    buildLadder: buildLadder, probabilityAtThreshold: probabilityAtThreshold,
    directMarketProbability: directMarketProbability,
    combineAnyPlayer: combineAnyPlayer,
    SINGLE_LINE_SPREAD_AT_25: SINGLE_LINE_SPREAD_AT_25,
    SINGLE_LINE_SPREAD_EXPONENT: SINGLE_LINE_SPREAD_EXPONENT,
    SINGLE_LINE_VALIDATED_MAX_THRESHOLD: SINGLE_LINE_VALIDATED_MAX_THRESHOLD,
    SINGLE_LINE_MODELS: SINGLE_LINE_MODELS, hasSingleLineModel: hasSingleLineModel,
    singleLineProbability: singleLineProbability,
    // team markets
    normPdf: normPdf, bivariateNormalCdf: bivariateNormalCdf,
    fitNormalFromLadder: fitNormalFromLadder,
    impliedCorrelationFromVariances: impliedCorrelationFromVariances,
    jointProbabilityBothExceed: jointProbabilityBothExceed,
    reachToCutoff: reachToCutoff, teamReachProbability: teamReachProbability,
    meanFromSingleLine: meanFromSingleLine, priceBothTeamsReach: priceBothTeamsReach,
    resolveTeam: resolveTeam, priceSingleTeam: priceSingleTeam, priceBothTeams: priceBothTeams,
    // parsing + roster pricing
    parseOverUnderPaste: parseOverUnderPaste,
    parseMilestonePaste: parseMilestonePaste,
    parseLadderPaste: parseLadderPaste,
    parseDirectPaste: parseDirectPaste,
    TEAM_NAMES: TEAM_NAMES, parseTeamFouls: parseTeamFouls, projectedPf: projectedPf,
    normalizeDirectPaste: normalizeDirectPaste,
    priceDirectRoster: priceDirectRoster,
    describePrice: describePrice,
    priceSingleLineRoster: priceSingleLineRoster,
    priceLadderRoster: priceLadderRoster,
    priceMilestoneRoster: priceMilestoneRoster,
    roundHalfEven: roundHalfEven,
  };
});
