/*
 * Page logic for index.html. All pricing math lives in engine.js (which
 * is tested against the Python reference); this file only reads inputs,
 * calls the engine, and draws results. Nothing is stored or sent anywhere.
 */
(function () {
  "use strict";

  const E = window.Engine;
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------
  // small helpers
  // ------------------------------------------------------------
  function h(tag, attrs) {
    const el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach((k) => {
        if (k === "class") el.className = attrs[k];
        else if (k === "text") el.textContent = attrs[k];
        else el.setAttribute(k, attrs[k]);
      });
    }
    for (let i = 2; i < arguments.length; i++) {
      const c = arguments[i];
      if (c === null || c === undefined) continue;
      el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return el;
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function fmtPct(p) {
    if (p === null || p === undefined || !Number.isFinite(p)) return "—";
    const pct = p * 100;
    if (pct >= 10) return pct.toFixed(1) + "%";
    if (pct >= 1) return pct.toFixed(2) + "%";
    return pct.toFixed(3) + "%";
  }

  function fmtOdds(n) {
    if (n === null || n === undefined) return "—";
    return n > 0 ? "+" + n : String(n);
  }

  function fmtSigned(x, digits, suffix) {
    const s = x.toFixed(digits);
    return (x > 0 ? "+" : "") + s + suffix;
  }

  function callout(kind, title, items) {
    const box = h("div", { class: "callout " + kind });
    box.appendChild(h("p", { text: title }));
    if (items && items.length) {
      const ul = h("ul");
      items.forEach((t) => ul.appendChild(h("li", { text: t })));
      box.appendChild(ul);
    }
    return box;
  }

  // ------------------------------------------------------------
  // margin (shared by every tab)
  // ------------------------------------------------------------
  // Each tab has its own margin box: ids m-<tab> and mm-<tab>.
  function readMargin(tab) {
    const raw = $("m-" + tab).value.trim();
    const mode = $("mm-" + tab).value;
    if (raw === "") return { value: null, mode: mode, error: null };
    const v = Number(raw);
    if (!Number.isFinite(v) || v < 0) {
      return { value: null, mode: mode, error: "The margin must be zero or more. Quoted prices are hidden until it is." };
    }
    return { value: v, mode: mode, error: null };
  }

  function quoteFor(p, margin) {
    if (margin.value === null) return { odds: null, reason: "no margin set" };
    const q = E.oddsWithMargin(p, margin.value, margin.mode);
    if (q === null) return { odds: null, reason: "margin pushes the price to 100% or more" };
    return { odds: q.american_odds, reason: null };
  }

  // ------------------------------------------------------------
  // tabs
  // ------------------------------------------------------------
  const tabs = ["any", "team", "direct", "foul", "rare"];
  function selectTab(name) {
    tabs.forEach((t) => {
      const on = t === name;
      $("tab-" + t).setAttribute("aria-selected", on ? "true" : "false");
      $("tab-" + t).tabIndex = on ? 0 : -1;
      $("panel-" + t).hidden = !on;
    });
  }
  tabs.forEach((t, i) => {
    $("tab-" + t).addEventListener("click", () => selectTab(t));
    $("tab-" + t).addEventListener("keydown", (e) => {
      let j = null;
      if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
      if (e.key === "ArrowLeft") j = (i + tabs.length - 1) % tabs.length;
      if (j !== null) {
        selectTab(tabs[j]);
        $("tab-" + tabs[j]).focus();
        e.preventDefault();
      }
    });
  });

  // ============================================================
  // ANY PLAYER
  // ============================================================
  const STATS = {
    points: { label: "points", thresholds: [25, 30, 35, 40, 45, 50] },
    rebounds: { label: "rebounds", thresholds: [12, 14, 16, 18, 20] },
    assists: { label: "assists", thresholds: [10, 12, 14, 16, 18] },
  };

  const state = {
    stat: "points",
    mode: "base",
    available: { points: STATS.points.thresholds.slice(), rebounds: STATS.rebounds.thresholds.slice(), assists: STATS.assists.thresholds.slice() },
    selected: { points: STATS.points.thresholds.slice(), rebounds: STATS.rebounds.thresholds.slice(), assists: STATS.assists.thresholds.slice() },
    single: { points: 25, rebounds: 12, assists: 10 }, // milestone mode: one threshold
    book: {}, // "stat:threshold" -> text typed in the book price box
  };

  const PLACEHOLDERS = {
    base: "Jayson Tatum\nO 26.5-114\nU 26.5-114\nCade Cunningham\nO 23.5-130\nU 23.5-102\nJalen Duren\nO 17.5-108\nU 17.5-122",
    ladder: "Jayson Tatum\nO 24.5-160\nU 24.5+130\nO 29.5+210\nU 29.5-270\nCade Cunningham\nO 19.5-150\nU 19.5+120\nO 24.5+110\nU 24.5-140",
    milestone: "Jayson Tatum -172\nCade Cunningham -104\nJalen Duren +450\nDerrick White +560",
  };

  const MODE_HINTS = {
    base: "One Over/Under line per player. The page estimates the milestones from it with a model checked against real prices on two games.",
    milestone: "One price per player for a single threshold, like the book's \"To Score 25+ Points\" list. These prices still include the book's margin.",
    ladder: "Several Over/Under lines per player. The page fits between and beyond them. A single line far from the threshold is refused.",
  };

  function activeThresholds() {
    if (state.mode === "milestone") return [state.single[state.stat]];
    return state.available[state.stat].filter((t) => state.selected[state.stat].indexOf(t) !== -1).sort((a, b) => a - b);
  }

  function syncModeControls() {
    const baseLabel = $("mode-base-label");
    const baseInput = baseLabel.querySelector("input");
    const baseOk = E.hasSingleLineModel(state.stat);
    baseInput.disabled = !baseOk;
    baseLabel.classList.toggle("disabled", !baseOk);
    baseLabel.title = baseOk ? "" : "Base-line estimates have not been checked for " + STATS[state.stat].label;
    if (!baseOk && state.mode === "base") {
      state.mode = "milestone";
    }
    document.querySelectorAll('input[name="mode"]').forEach((r) => { r.checked = r.value === state.mode; });
    $("mode-hint").textContent =
      baseOk ? "One Over/Under line per player. The page estimates the milestones from it." : "Base-line estimates have not been checked for " + STATS[state.stat].label + " yet. Paste one price per player for a single threshold, like the book's list.";
    $("paste").placeholder = PLACEHOLDERS[state.mode];
  }

  function renderChips() {
    const box = $("chips");
    clear(box);
    const stat = state.stat;
    const single = state.mode === "milestone";
    $("thr-label").textContent = single ? "Threshold for this list" : "Thresholds";
    state.available[stat].slice().sort((a, b) => a - b).forEach((t) => {
      const input = h("input", { type: single ? "radio" : "checkbox", name: "thr-" + stat, value: String(t) });
      input.checked = single ? state.single[stat] === t : state.selected[stat].indexOf(t) !== -1;
      input.addEventListener("change", () => {
        if (single) state.single[stat] = t;
        else if (input.checked) state.selected[stat].push(t);
        else state.selected[stat] = state.selected[stat].filter((x) => x !== t);
        recomputeAny();
      });
      box.appendChild(h("div", { class: "seg" }, h("label", null, input, t + "+")));
    });
  }

  // Keeps the typed book prices through re-renders, and updates a row's
  // edge/ROI cells without rebuilding the table (so typing keeps focus).
  function wireBookCell(input, edgeCell, roiCell, p, key) {
    function update() {
      state.book[key] = input.value;
      edgeCell.className = "";
      roiCell.className = "";
      const raw = input.value.trim();
      if (raw === "") {
        edgeCell.textContent = "—"; roiCell.textContent = "—";
        edgeCell.classList.add("dim"); roiCell.classList.add("dim");
        return;
      }
      const odds = Number(raw);
      if (!Number.isInteger(odds) || Math.abs(odds) < 100) {
        edgeCell.textContent = "not a real price"; roiCell.textContent = "—";
        edgeCell.classList.add("neg"); roiCell.classList.add("dim");
        return;
      }
      const d = E.describePrice(p, null, null, odds).vs_book;
      edgeCell.textContent = fmtSigned(d.edge * 100, 2, " pp");
      roiCell.textContent = fmtSigned(d.expected_roi * 100, 1, "%");
      const cls = d.edge > 0 ? "pos" : "neg";
      edgeCell.classList.add(cls); roiCell.classList.add(cls);
    }
    input.addEventListener("input", update);
    update();
  }

  // The results table shared by "any player" and the team tab. rows:
  // [{label, p (null = no price), flags: [..], reason?, key}]. Typed book
  // prices are kept in state.book[key] across redraws, and edge/ROI update
  // in place so typing in the book box never loses focus.
  function boardTable(rows, margin) {
    const head = h("tr", null,
      h("th", { text: "Market" }), h("th", { text: "Probability" }), h("th", { text: "Fair odds" }),
      h("th", { text: "Odds with margin" }), h("th", { text: "Book price" }), h("th", { text: "Edge" }), h("th", { text: "ROI" }));
    const body = h("tbody");

    rows.forEach((row) => {
      const tr = h("tr");
      tr.appendChild(h("td", { class: "thr", text: row.label }));
      if (row.p === null || row.p === undefined) {
        tr.appendChild(h("td", { class: "dim", text: "—" }));
        const why = h("td", { class: "dim", text: "no price" });
        if (row.reason) why.title = row.reason;
        tr.appendChild(why);
        tr.appendChild(h("td", { class: "dim", text: "—" }));
        tr.appendChild(h("td")); tr.appendChild(h("td")); tr.appendChild(h("td"));
        body.appendChild(tr);
        return;
      }
      const p = row.p;
      const probCell = h("td", { class: "prob", text: fmtPct(p) });
      (row.flags || []).forEach((f) => probCell.appendChild(h("span", { class: "flag", text: f })));
      tr.appendChild(probCell);
      tr.appendChild(h("td", { class: "fair", text: fmtOdds(E.probabilityToFairAmerican(p)) }));
      const q = quoteFor(p, margin);
      const qc = h("td", { class: "quote", text: fmtOdds(q.odds) });
      if (q.odds === null && q.reason) qc.title = q.reason;
      tr.appendChild(qc);

      const input = h("input", { type: "text", autocomplete: "off", placeholder: "optional", "aria-label": "Book price for " + row.label });
      input.value = state.book[row.key] || "";
      const edge = h("td"), roi = h("td");
      tr.appendChild(h("td", null, input));
      tr.appendChild(edge); tr.appendChild(roi);
      wireBookCell(input, edge, roi, p, row.key);
      body.appendChild(tr);
    });

    return h("div", { class: "board" }, h("table", null, h("thead", null, head), body));
  }

  function computeAny() {
    const text = $("paste").value;
    const T = activeThresholds();
    const out = { errors: [], warnings: [], rows: [], players: [], playerCount: 0, notes: [], hasText: text.trim() !== "" };
    if (!out.hasText) return out;
    if (!T.length) {
      out.warnings.push("Choose at least one threshold.");
      return out;
    }

    if (state.mode === "milestone") {
      const parsed = E.parseMilestonePaste(text);
      out.errors = parsed.errors;
      if (!parsed.entries.length) return out;
      const r = E.priceMilestoneRoster(parsed.entries);
      out.playerCount = parsed.entries.length;
      out.rows.push({ T: T[0], combined: r.combined, flags: [] });
      out.players = [{ cols: [T[0]], per: { [T[0]]: r.per_player } }];
      out.notes.push("Built from the book's own prices, so the margin in them is still in this number. It runs a little high.");
      out.notes.push("Players missing from the list count as zero chance. Paste everyone who could reach the threshold.");
      return out;
    }

    const parsed = E.parseOverUnderPaste(text);
    out.errors = parsed.errors;
    if (!parsed.players.length) return out;
    out.playerCount = parsed.players.length;
    const perByT = {};

    T.forEach((t) => {
      let r;
      const flags = [];
      if (state.mode === "base") {
        r = E.priceSingleLineRoster(parsed.players, t, state.stat);
        if (r.beyond_validated_range) flags.push("beyond checked range");
        if (r.skipped.length) out.warnings.push(r.skipped.length + " player(s) have several lines and were left out of base-line mode (e.g. " + r.skipped[0].name + "). Switch to Alt-line ladders to include them.");
      } else {
        r = E.priceLadderRoster(parsed.players, t);
        if (r.failed.length) out.warnings.push(r.failed.length + " player(s) couldn't be priced at " + t + "+ and are left out, so the number is lower than it should be: " + r.failed.slice(0, 3).map((f) => f.name + " (" + f.reason + ")").join("; ") + (r.failed.length > 3 ? "…" : ""));
        if (r.any_extrapolated) flags.push("extends past the lines you gave");
      }
      const priced = r.per_player.length;
      perByT[t] = r.per_player;
      out.rows.push({ T: t, combined: priced ? r.combined : null, flags: flags });
    });
    out.warnings = out.warnings.filter((w, i, a) => a.indexOf(w) === i);
    out.players = [{ cols: T, per: perByT }];
    if (state.mode === "base") {
      if (state.stat === "points") {
        out.notes.push("Model estimate. For points, it matched the book's own milestone prices within about 15% per player on two games (the any-player totals within a few percent), and it runs slightly below them because their margin is in the book's prices.");
        out.notes.push("Only a few players were checked at 45+ and 50+, and nothing above 50+ has a book price to check against. Treat those rows as rough.");
      } else if (state.stat === "assists") {
        out.notes.push("Model estimate. For assists, fitted on one game and checked on a second against the book's 10+, 12+ and 14+ prices: 8 of 9 prices were within 20%.");
        out.notes.push("That is a small sample, since the book listed only a handful of players per game. It doesn't post 16+ or 18+, so those rows are unchecked, and the rest of the roster is the model alone.");
      } else {
        out.notes.push("Model estimate. For rebounds, checked on two games against the book's 12+, 14+ and 16+ prices: the any-player totals for the players the book lists came within about 10%, but single players were off by as much as 30%.");
        out.notes.push("The book lists only players above roughly a 3% chance, so the rest of the roster is the model alone, and it moves the total a good deal. The book doesn't post 18+ or 20+, so those rows are unchecked.");
      }
    } else {
      out.notes.push("Read directly off your lines where they cover the threshold, and extended with a decaying curve where they don't.");
    }
    out.notes.push("Players you leave out count as zero chance. Paste everyone who could reach the threshold. Teammates are treated as independent.");
    return out;
  }

  function renderPlayersTable(out) {
    const per = out.players[0].per;
    const cols = out.players[0].cols;
    const names = [];
    const seen = {};
    cols.forEach((t) => (per[t] || []).forEach((p) => { if (!seen[p.name]) { seen[p.name] = true; names.push(p.name); } }));
    const showLine = state.mode === "base";
    const lines = {};
    if (showLine) (per[cols[0]] || []).forEach((p) => { lines[p.name] = p.line; });

    const thead = h("tr", null, h("th", { text: "Player" }));
    if (showLine) thead.appendChild(h("th", { text: "Line" }));
    cols.forEach((t) => thead.appendChild(h("th", { text: t + "+" })));
    const tbody = h("tbody");
    names.forEach((n) => {
      const tr = h("tr", null, h("td", { text: n }));
      if (showLine) tr.appendChild(h("td", { text: lines[n] !== undefined ? String(lines[n]) : "—" }));
      cols.forEach((t) => {
        const hit = (per[t] || []).find((p) => p.name === n);
        tr.appendChild(h("td", { text: hit ? fmtPct(hit.probability) : "—", class: hit ? "" : "dim" }));
      });
      tbody.appendChild(tr);
    });
    return h("details", null,
      h("summary", { text: "Each player (" + names.length + ")" }),
      h("div", { class: "board" }, h("table", null, h("thead", null, thead), tbody)));
  }

  function recomputeAny() {
    renderChips();
    syncModeControls();
    const margin = readMargin("any");
    const out = computeAny();
    const msgs = $("messages");
    const res = $("results");
    clear(msgs); clear(res);

    if (margin.error) msgs.appendChild(callout("warn", margin.error));
    if (out.errors.length) {
      msgs.appendChild(callout("err",
        out.rows.length
          ? "Some lines couldn't be read and are left out, so the number below is missing them:"
          : "Nothing could be read yet:", out.errors.slice(0, 8).concat(out.errors.length > 8 ? ["…and " + (out.errors.length - 8) + " more"] : [])));
    }
    if (out.warnings.length) msgs.appendChild(callout("warn", "Check this:", out.warnings));

    if (!out.hasText) {
      res.appendChild(h("p", { class: "empty", text: "Paste the lines above and the prices appear here." }));
      return;
    }
    if (!out.rows.length) return;

    res.appendChild(boardTable(
      out.rows.map((row) => ({ label: row.T + "+", p: row.combined, flags: row.flags, key: state.stat + ":" + row.T })),
      margin));
    if (out.players.length) res.appendChild(renderPlayersTable(out));
    if (out.notes.length) {
      const ul = h("ul", { class: "notes" });
      out.notes.forEach((n) => ul.appendChild(h("li", { text: n })));
      res.appendChild(ul);
    }
  }

  document.querySelectorAll('input[name="stat"]').forEach((r) =>
    r.addEventListener("change", () => { state.stat = r.value; $("paste").value = ""; recomputeAny(); $("paste").focus(); }));
  document.querySelectorAll('input[name="mode"]').forEach((r) =>
    r.addEventListener("change", () => { state.mode = r.value; recomputeAny(); }));
  $("paste").addEventListener("input", recomputeAny);
  $("clear").addEventListener("click", () => { $("paste").value = ""; recomputeAny(); $("paste").focus(); });
  $("add-thr").addEventListener("click", () => {
    const v = Number($("custom-thr").value);
    if (!Number.isInteger(v) || v < 1) return;
    const s = state.stat;
    if (state.available[s].indexOf(v) === -1) state.available[s].push(v);
    if (state.selected[s].indexOf(v) === -1) state.selected[s].push(v);
    state.single[s] = v;
    $("custom-thr").value = "";
    recomputeAny();
  });
  $("custom-thr").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("add-thr").click(); } });

  // ============================================================
  // TEAM SCORE THROUGH 3 QUARTERS, FROM QUARTER SPREADS AND TOTALS
  // ============================================================
  const QROWS = [
    { key: "q1", label: "Q1", optional: false },
    { key: "q2", label: "Q2", optional: false },
    { key: "q3", label: "Q3", optional: false },
    { key: "q4", label: "Q4 (optional)", optional: true },
  ];
  const QFIELDS = [
    { id: "total", label: "Total", prop: "total_line", kind: "line", ph: "56.5" },
    { id: "over", label: "Over price", prop: "over_odds", kind: "odds", ph: "-110" },
    { id: "under", label: "Under price", prop: "under_odds", kind: "odds", ph: "-110" },
    { id: "line", label: "Home spread", prop: "home_line", kind: "line", ph: "-1.5" },
    { id: "home", label: "Home price", prop: "home_odds", kind: "odds", ph: "-110" },
    { id: "away", label: "Away price", prop: "away_odds", kind: "odds", ph: "-110" },
  ];
  const TTFIELDS = [
    { id: "line", label: "Full-game line", prop: "line", kind: "line", ph: "111.5" },
    { id: "over", label: "Over price", prop: "over_odds", kind: "odds", ph: "-110" },
    { id: "under", label: "Under price", prop: "under_odds", kind: "odds", ph: "-110" },
  ];

  function buildQuarterGrid() {
    const grid = $("qgrid"), tt = $("qtotals");
    clear(grid); clear(tt);
    grid.appendChild(h("thead", null, h("tr", null, h("th", { text: "Quarter" }),
      ...QFIELDS.map((f) => h("th", { text: f.label })))));
    const body = h("tbody");
    QROWS.forEach((r) => {
      const tr = h("tr", null, h("td", { class: "thr", text: r.label }));
      QFIELDS.forEach((f) => {
        const input = h("input", { type: "text", id: "qin-" + r.key + "-" + f.id, autocomplete: "off", spellcheck: "false",
          placeholder: f.ph, inputmode: "text", "aria-label": r.label + " " + f.label.toLowerCase() });
        tr.appendChild(h("td", null, input));
      });
      body.appendChild(tr);
    });
    grid.appendChild(body);
    tt.appendChild(h("thead", null, h("tr", null, h("th", { text: "Team total, full game (optional)" }),
      ...TTFIELDS.map((f) => h("th", { text: f.label })))));
    const tb = h("tbody");
    [["home", "Home"], ["away", "Away"]].forEach((t) => {
      const tr = h("tr", null, h("td", { class: "thr", text: t[1] }));
      TTFIELDS.forEach((f) => {
        tr.appendChild(h("td", null, h("input", { type: "text", id: "qtt-" + t[0] + "-" + f.id, autocomplete: "off",
          spellcheck: "false", placeholder: f.ph, "aria-label": t[1] + " team total " + f.label.toLowerCase() })));
      });
      tb.appendChild(tr);
    });
    tt.appendChild(tb);
    grid.querySelectorAll("input").forEach((el) => el.addEventListener("input", recomputeQuarters));
    tt.querySelectorAll("input").forEach((el) => el.addEventListener("input", recomputeQuarters));
  }

  // One typed cell -> {empty} | {bad: reason} | {v}
  function readCell(id, kind) {
    const raw = $(id).value.trim().replace(/[\u2212\u2013\u2014]/g, "-");
    if (raw === "") return { empty: true };
    if (kind === "odds") {
      if (!/^[+-]\s*\d+$/.test(raw)) return { bad: "needs a + or - in front, like -110 or +150" };
      const v = parseInt(raw.replace(/\s+/g, ""), 10);
      if (Math.abs(v) < 100) return { bad: v + " isn't a real American price" };
      return { v: v };
    }
    if (!/^[+-]?\d+(\.\d+)?$/.test(raw)) return { bad: "isn't a number" };
    return { v: Number(raw) };
  }

  // Reads a row of fields. Returns {state: "empty"|"partial"|"ok"|"bad", data, problems}
  function readRow(prefix, fields, label) {
    const data = {}, problems = [], missing = [];
    let filled = 0;
    fields.forEach((f) => {
      const c = readCell(prefix + f.id, f.kind);
      if (c.empty) { missing.push(f.label.toLowerCase()); return; }
      filled++;
      if (c.bad) problems.push(label + " " + f.label.toLowerCase() + ": " + c.bad + ".");
      else data[f.prop] = c.v;
    });
    if (filled === 0) return { state: "empty", data: null, problems: [], missing: missing };
    if (problems.length) return { state: "bad", data: null, problems: problems, missing: missing };
    if (missing.length) return { state: "partial", data: null, problems: [], missing: missing };
    return { state: "ok", data: data, problems: [], missing: [] };
  }

  function recomputeQuarters() {
    const margin = readMargin("team");
    const msgs = $("qmessages"), res = $("qresults");
    clear(msgs); clear(res);
    if (margin.error) msgs.appendChild(callout("warn", margin.error));

    const rows = QROWS.map((r) => ({ r: r, row: readRow("qin-" + r.key + "-", QFIELDS, r.label.split(" ")[0]) }));
    const tts = ["home", "away"].map((t) => ({ t: t, row: readRow("qtt-" + t + "-", TTFIELDS, (t === "home" ? "Home" : "Away") + " team total") }));
    const anything = rows.some((x) => x.row.state !== "empty") || tts.some((x) => x.row.state !== "empty");
    if (!anything) {
      res.appendChild(h("p", { class: "empty", text: "Enter Q1, Q2 and Q3 and each team's chance of reaching the score appears here." }));
      return;
    }

    const problems = [];
    rows.forEach((x) => { problems.push(...x.row.problems); });
    tts.forEach((x) => { problems.push(...x.row.problems); });
    rows.filter((x) => !x.r.optional).forEach((x) => {
      if (x.row.state === "empty") problems.push(x.r.label + ": enter its total and spread with both prices.");
      else if (x.row.state === "partial") problems.push(x.r.label + " still needs: " + x.row.missing.join(", ") + ".");
    });
    const reachHome = Number($("q-reach-home").value), reachAway = Number($("q-reach-away").value);
    if (!Number.isFinite(reachHome) || reachHome < 1) problems.push("Home threshold must be 1 or more.");
    if (!Number.isFinite(reachAway) || reachAway < 1) problems.push("Away threshold must be 1 or more.");
    if (problems.length) {
      msgs.appendChild(callout("err", "Fix this to see prices:", problems.slice(0, 10)));
      return;
    }

    const warn = [];
    const q4row = rows[3].row;
    let q4 = null;
    if (q4row.state === "ok") q4 = q4row.data;
    else if (q4row.state === "partial") warn.push("Q4 is incomplete (still needs: " + q4row.missing.join(", ") + "), so it is left out.");
    const tt = {};
    tts.forEach((x) => {
      if (x.row.state === "ok") tt[x.t] = x.row.data;
      else if (x.row.state === "partial") warn.push((x.t === "home" ? "Home" : "Away") + " team total is incomplete (still needs: " + x.row.missing.join(", ") + "), so it is left out.");
    });

    let r;
    try {
      r = E.priceThreeQuarterTeams([rows[0].row.data, rows[1].row.data, rows[2].row.data],
        reachHome, reachAway, q4, tt.home || null, tt.away || null);
    } catch (e) {
      msgs.appendChild(callout("err", "Couldn't price this:", [e.message]));
      return;
    }
    r.notes.forEach((n) => warn.push(n));
    if (warn.length) msgs.appendChild(callout("warn", "Check this:", warn));

    const homeName = $("q-home-name").value.trim().slice(0, 24) || "Home";
    const awayName = $("q-away-name").value.trim().slice(0, 24) || "Away";
    res.appendChild(boardTable([
      { label: homeName + " " + reachHome + "+ through 3 quarters", p: r.probability_home, flags: [], key: "q3:home:" + reachHome },
      { label: awayName + " " + reachAway + "+ through 3 quarters", p: r.probability_away, flags: [], key: "q3:away:" + reachAway },
    ], margin));

    const lo = E.THREE_QUARTER_SWING_RANGE[0], hi = E.THREE_QUARTER_SWING_RANGE[1];
    const spreadOdds = (mean, reach, sw) => fmtOdds(E.probabilityToFairAmerican(E.teamReachProbability(mean, sw, reach)));
    const detail = h("dl");
    const add = (k, v) => { detail.appendChild(h("dt", { text: k })); detail.appendChild(h("dd", { text: v })); };
    add(homeName + " expected score through 3 quarters", r.home_mean.toFixed(1) + (r.scaled.home ? " (corrected to the team total)" : " (from the quarter lines only)"));
    add(awayName + " expected score through 3 quarters", r.away_mean.toFixed(1) + (r.scaled.away ? " (corrected to the team total)" : " (from the quarter lines only)"));
    add("Swing in a team's score", r.swing.toFixed(2) + " points (plausible range " + lo + " to " + hi + ")");
    add(homeName + " fair odds across that range", spreadOdds(r.home_mean, reachHome, hi) + " to " + spreadOdds(r.home_mean, reachHome, lo));
    add(awayName + " fair odds across that range", spreadOdds(r.away_mean, reachAway, hi) + " to " + spreadOdds(r.away_mean, reachAway, lo));
    res.appendChild(h("details", { open: "" }, h("summary", { text: "How these were worked out" }), h("div", { class: "card", style: "margin-top:8px" }, detail)));

    const notes = h("ul", { class: "notes" });
    [
      "Each team's average comes from the quarter totals and spreads. How widely a team's score varies is fixed at " + r.swing.toFixed(2) + " points, measured from the book's own prices on three games.",
      "The range above shows how much the fair odds move if the true swing is at either end of what was measured. It is the honest size of the uncertainty.",
      "\"100+\" means 100 or more, so the chance is measured from 99.5, the same way the player markets work.",
      "Only the first three quarters are priced. The swing was measured for that window and is not used for other windows.",
      "Checked against six teams from one book. Those are the same prices the swing was measured on, so this is a consistency check, not an independent test."
    ].forEach((n) => notes.appendChild(h("li", { text: n })));
    res.appendChild(notes);
  }
  ["q-home-name", "q-away-name", "q-reach-home", "q-reach-away"].forEach((id) => $(id).addEventListener("input", recomputeQuarters));
  buildQuarterGrid();

  // ============================================================
  // DOUBLE / TRIPLE DOUBLE
  // ============================================================
  function recomputeDirect() {
    const margin = readMargin("direct");
    const msgs = $("dmessages"), res = $("dresults");
    clear(msgs); clear(res);
    if (margin.error) msgs.appendChild(callout("warn", margin.error));
    const text = $("dpaste").value;
    if (text.trim() === "") {
      res.appendChild(h("p", { class: "empty", text: "Paste each player's Yes price and the any-player price appears here." }));
      return;
    }
    const parsed = E.parseDirectPaste(text);
    if (parsed.errors.length) {
      msgs.appendChild(callout("err", parsed.entries.length ? "Some lines couldn't be read and are left out, so the number is missing them:" : "Nothing could be read yet:", parsed.errors.slice(0, 8)));
    }
    if (!parsed.entries.length) return;
    const r = E.priceDirectRoster(parsed.entries);
    const p = r.combined;
    const q = quoteFor(p, margin);

    const card = h("div", { class: "card", style: "margin-top:20px" });
    card.appendChild(h("div", { class: "pair" },
      h("div", { class: "big fair" }, h("div", { class: "k", text: "Fair odds" }), h("div", { class: "v", text: fmtOdds(E.probabilityToFairAmerican(p)) })),
      h("div", { class: "big quote" }, h("div", { class: "k", text: "Odds with margin" }), h("div", { class: "v", text: fmtOdds(q.odds) }))));
    const dl = h("dl");
    dl.appendChild(h("dt", { text: "Probability that at least one does it" }));
    dl.appendChild(h("dd", { text: fmtPct(p) }));
    dl.appendChild(h("dt", { text: "Players counted" }));
    dl.appendChild(h("dd", { text: String(parsed.entries.length) }));

    const bookRaw = $("dbook").value.trim();
    if (bookRaw !== "") {
      const odds = Number(bookRaw);
      if (!Number.isInteger(odds) || Math.abs(odds) < 100) {
        msgs.appendChild(callout("warn", "The book's price must be +100 or higher, or -100 or lower."));
      } else {
        const d = E.describePrice(p, null, null, odds).vs_book;
        dl.appendChild(h("dt", { text: "Edge vs the book" }));
        dl.appendChild(h("dd", { class: d.edge > 0 ? "pos" : "neg", text: fmtSigned(d.edge * 100, 2, " pp") }));
        dl.appendChild(h("dt", { text: "Expected ROI" }));
        dl.appendChild(h("dd", { class: d.edge > 0 ? "pos" : "neg", text: fmtSigned(d.expected_roi * 100, 1, "%") }));
      }
    }
    card.appendChild(dl);
    res.appendChild(card);

    const det = h("details", null, h("summary", { text: "Each player (" + r.per_player.length + ")" }));
    const tb = h("tbody");
    r.per_player.forEach((x) => tb.appendChild(h("tr", null,
      h("td", { text: x.name }), h("td", { text: fmtPct(x.probability) }),
      h("td", { class: x.devigged ? "" : "dim", text: x.devigged ? "margin removed" : "Yes price only" }))));
    det.appendChild(h("div", { class: "board" }, h("table", null,
      h("thead", null, h("tr", null, h("th", { text: "Player" }), h("th", { text: "Probability" }), h("th", { text: "Basis" }))), tb)));
    res.appendChild(det);

    const notes = h("ul", { class: "notes" });
    if (r.any_not_devigged) notes.appendChild(h("li", { text: "Players with only a Yes price still carry the book's margin, so the total runs a little high. Add their No price to remove it." }));
    notes.appendChild(h("li", { text: "Each player is priced from their own line, never rebuilt from points, rebounds and assists, because those move together. Teammates are treated as independent." }));
    res.appendChild(notes);
  }
  document.querySelectorAll('input[name="dmarket"]').forEach((r) => r.addEventListener("change", recomputeDirect));
  $("dpaste").addEventListener("input", recomputeDirect);
  $("dclear").addEventListener("click", () => { $("dpaste").value = ""; recomputeDirect(); $("dpaste").focus(); });
  $("dbook").addEventListener("input", recomputeDirect);

  // ============================================================
  // FOUL-OUT
  // ============================================================
  function recomputeFoul() {
    const margin = readMargin("foul");
    const msgs = $("fmessages"), res = $("fresults");
    clear(msgs); clear(res);
    if (margin.error) msgs.appendChild(callout("warn", margin.error));

    const pfRaw = $("pf").value.trim();
    if (pfRaw === "") {
      res.appendChild(h("p", { class: "empty", text: "Pick the two teams, or enter the projected fouls." }));
      return;
    }
    const pf = Number(pfRaw);
    if (!Number.isFinite(pf) || pf <= 0) {
      msgs.appendChild(callout("err", "Fix this to see a price:", ["Projected fouls must be a positive number."]));
      return;
    }

    const ip = E.interpolateProbability(pf);
    const probability = ip[0], extrapolated = ip[1];
    const fairOdds = E.probabilityToFairAmerican(probability);
    const q = quoteFor(probability, margin);
    const card = h("div", { class: "card", style: "margin-top:20px" });
    card.appendChild(h("div", { class: "pair" },
      h("div", { class: "big fair" }, h("div", { class: "k", text: "Fair odds" }), h("div", { class: "v", text: fmtOdds(fairOdds) })),
      h("div", { class: "big quote" }, h("div", { class: "k", text: "Odds with margin" }), h("div", { class: "v", text: fmtOdds(q.odds) }))));
    const dl = h("dl");
    dl.appendChild(h("dt", { text: "Probability" }));
    dl.appendChild(h("dd", { text: (probability * 100).toFixed(2) + "%" }));
    card.appendChild(dl);
    res.appendChild(card);
    if (extrapolated) {
      msgs.appendChild(callout("warn", "Outside the 30 to 50 fouls the model was calibrated on, so this probability is extended past its data."));
    }
  }
  $("pf").addEventListener("input", recomputeFoul);

  // ---- pick two teams -> PF
  const teamState = { data: null, source: "" };
  const TEAM_KEY = "nba-game-specials-team-fouls";

  function loadTeamData() {
    let saved = null;
    try { saved = window.localStorage.getItem(TEAM_KEY); } catch (e) { saved = null; }
    if (saved) {
      const r = E.parseTeamFouls(saved);
      if (r.data) { teamState.data = r.data; teamState.source = "pasted in this browser"; $("fteam-data").value = saved; return; }
    }
    if (window.TEAM_FOULS_DATA) {
      const r = E.parseTeamFouls(JSON.stringify(window.TEAM_FOULS_DATA));
      if (r.data) { teamState.data = r.data; teamState.source = "team_fouls.js"; }
    }
  }

  function fillTeamSelects() {
    ["fteam-a", "fteam-b"].forEach((id, i) => {
      const sel = $(id), keep = sel.value;
      clear(sel);
      sel.appendChild(h("option", { value: "", text: i === 0 ? "First team" : "Second team" }));
      Object.keys(E.TEAM_NAMES).sort((x, y) => E.TEAM_NAMES[x].localeCompare(E.TEAM_NAMES[y])).forEach((code) => {
        const have = teamState.data && teamState.data.teams[code];
        const o = h("option", { value: code, text: E.TEAM_NAMES[code] + (teamState.data && !have ? " (no data)" : "") });
        if (teamState.data && !have) o.disabled = true;
        sel.appendChild(o);
      });
      sel.value = keep;
    });
  }

  function updateFromTeams() {
    const info = $("fteam-info"); clear(info);
    const a = $("fteam-a").value, b = $("fteam-b").value;
    if (!teamState.data) { info.textContent = "No team data loaded yet. Open Team data below and paste the refresh cell's output, or type the PF yourself."; return; }
    if (!a || !b) { info.textContent = "Team data as of " + teamState.data.as_of + " (" + teamState.source + ")."; return; }
    if (a === b) { info.textContent = "Pick two different teams."; return; }
    const today = new Date().toISOString().slice(0, 10);
    let r;
    try { r = E.projectedPf(a, b, teamState.data, today); }
    catch (e) { info.textContent = e.message; return; }
    $("pf").value = (Math.round(r.pf * 100) / 100).toString();
    info.appendChild(document.createTextNode(
      "PF " + r.pf.toFixed(2) + " = (last 10: " + r.last10_total.toFixed(1) + " + season: " + r.season_total.toFixed(1) + ") / 2. Data as of " + teamState.data.as_of + ". You can still type over the PF."));
    if (r.notes.length) info.appendChild(h("div", { style: "margin-top:8px" }, callout("warn", "Heads up:", r.notes)));
    recomputeFoul();
  }

  $("fteam-a").addEventListener("change", updateFromTeams);
  $("fteam-b").addEventListener("change", updateFromTeams);
  $("fteam-data").addEventListener("input", () => {
    const box = $("fteam-msgs"); clear(box);
    const raw = $("fteam-data").value.trim();
    if (raw === "") {
      try { window.localStorage.removeItem(TEAM_KEY); } catch (e) { /* storage may be unavailable */ }
      teamState.data = null; teamState.source = ""; loadTeamData();
    } else {
      const r = E.parseTeamFouls(raw);
      if (!r.data) { box.appendChild(callout("err", "Can't use that:", r.errors)); return; }
      teamState.data = r.data; teamState.source = "pasted in this browser";
      try { window.localStorage.setItem(TEAM_KEY, raw); } catch (e) { /* storage may be unavailable */ }
      box.appendChild(callout("ok", "Loaded " + Object.keys(r.data.teams).length + " teams, as of " + r.data.as_of + "."));
    }
    fillTeamSelects(); updateFromTeams();
  });
  loadTeamData(); fillTeamSelects(); updateFromTeams();

  // ============================================================
  // HEAD COACH EJECTED (one fixed price)
  // ============================================================
  const COACH_PRICE = "+10000";
  function recomputeRare() {
    const res = $("rresults"); clear(res);
    const raw = $("coach-price").value.trim();
    const o = Number(raw);
    if (raw === "" || !Number.isInteger(o) || Math.abs(o) < 100) {
      res.appendChild(callout("err", "Enter a price of +100 or higher, or -100 or lower."));
      return;
    }
    res.appendChild(h("div", { class: "card", style: "margin-top:20px" },
      h("div", { class: "pair" },
        h("div", { class: "big quote" }, h("div", { class: "k", text: "Head Coach Ejection" }), h("div", { class: "v", text: fmtOdds(o) })))));
  }
  $("coach-price").value = COACH_PRICE;
  $("coach-price").addEventListener("input", recomputeRare);

  // ============================================================
  // margin changes redraw everything
  // ============================================================
  function recomputeAll() { recomputeAny(); recomputeQuarters(); recomputeDirect(); recomputeFoul(); recomputeRare(); }
  [["any", recomputeAny], ["team", recomputeQuarters], ["direct", recomputeDirect], ["foul", recomputeFoul]].forEach((t) => {
    $("m-" + t[0]).addEventListener("input", t[1]);
    $("mm-" + t[0]).addEventListener("change", t[1]);
  });

  recomputeAll();
})();
