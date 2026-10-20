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
  function readMargin() {
    const raw = $("margin").value.trim();
    const mode = $("margin-mode").value;
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
    const margin = readMargin();
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
    r.addEventListener("change", () => { state.stat = r.value; recomputeAny(); }));
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
  // TEAM TOTALS
  // ============================================================
  function numOrNull(id) {
    const raw = $(id).value.trim();
    return raw === "" ? null : Number(raw);
  }

  function recomputeTeam() {
    const margin = readMargin();
    const msgs = $("tmessages"), res = $("tresults");
    clear(msgs); clear(res);
    if (margin.error) msgs.appendChild(callout("warn", margin.error));

    const boxes = [
      { id: "t-home", label: "Home team" },
      { id: "t-away", label: "Away team" },
      { id: "t-total", label: "Game total" },
    ].map((b) => {
      const parsed = E.parseLadderPaste($(b.id).value);
      return { label: b.label, lines: parsed.lines, errors: parsed.errors, empty: $(b.id).value.trim() === "" };
    });
    const [home, away, total] = boxes;

    const problems = [];
    boxes.forEach((b) => b.errors.forEach((e) => problems.push(b.label + ": " + e)));
    if (problems.length) {
      msgs.appendChild(callout("err", "Some lines couldn't be read and are left out:", problems.slice(0, 8)));
    }

    if (home.empty && away.empty) {
      res.appendChild(h("p", { class: "empty", text: "Paste a team's lines and its chance of reaching the threshold appears here." }));
      return;
    }

    const reachHome = Number($("t-reach-home").value), reachAway = Number($("t-reach-away").value);
    const std = numOrNull("t-std"), rho = numOrNull("t-rho");
    const inputProblems = [];
    if (!Number.isFinite(reachHome) || reachHome < 1) inputProblems.push("Home threshold must be 1 or more.");
    if (!Number.isFinite(reachAway) || reachAway < 1) inputProblems.push("Away threshold must be 1 or more.");
    if (std !== null && (!Number.isFinite(std) || std <= 0)) inputProblems.push("The assumed spread must be above zero.");
    if (rho !== null && (!Number.isFinite(rho) || rho <= -1 || rho >= 1)) inputProblems.push("Correlation must be between -1 and 1.");
    if (inputProblems.length) {
      msgs.appendChild(callout("err", "Fix this to see prices:", inputProblems));
      return;
    }

    const rows = [];
    const warnings = [];
    const stdArg = std;
    let single = {};
    [["home", home, reachHome, "Home"], ["away", away, reachAway, "Away"]].forEach((t) => {
      const key = t[0], box = t[1], reach = t[2], name = t[3];
      if (box.empty) return;
      const label = name + " " + reach + "+";
      try {
        single[key] = E.priceSingleTeam(box.lines, reach, stdArg, name + " team");
        rows.push({ label: label, p: single[key].probability, flags: [], key: "team:" + key + ":" + reach });
      } catch (e) {
        rows.push({ label: label, p: null, reason: e.message, key: "team:" + key });
        warnings.push(e.message);
      }
    });

    let both = null;
    if (!home.empty && !away.empty) {
      const label = "Both: Home " + reachHome + "+ and Away " + reachAway + "+";
      try {
        both = E.priceBothTeams(home.lines, away.lines, total.lines, reachHome, reachAway, stdArg, rho);
        rows.push({ label: label, p: both.joint_probability, flags: [], key: "team:both:" + reachHome + ":" + reachAway });
        both.warnings.forEach((w) => warnings.push(w));
      } catch (e) {
        rows.push({ label: label, p: null, reason: e.message, key: "team:both" });
        warnings.push(e.message);
      }
    }
    if (warnings.length) msgs.appendChild(callout("warn", "Check this:", warnings.filter((w, i, a) => a.indexOf(w) === i)));

    res.appendChild(boardTable(rows, margin));

    const detail = h("dl");
    const add = (k, v) => { detail.appendChild(h("dt", { text: k })); detail.appendChild(h("dd", { text: v })); };
    ["home", "away"].forEach((k) => {
      if (!single[k]) return;
      add((k === "home" ? "Home" : "Away") + " team average and spread", single[k].mean.toFixed(1) + " and " + single[k].std.toFixed(1) + " points (" + single[k].basis + ")");
    });
    if (both) {
      add("Link between the teams", both.rho.toFixed(2) + " (" + both.rho_source + ")");
      add("If the teams scored independently", fmtPct(both.independent_probability));
      add("Both, with the link", fmtPct(both.joint_probability));
    }
    if (detail.children.length) {
      res.appendChild(h("details", { open: "" }, h("summary", { text: "How these were worked out" }), h("div", { class: "card", style: "margin-top:8px" }, detail)));
    }

    const notes = h("ul", { class: "notes" });
    [
      "Team scoring is treated as a bell curve, a standard approximation that has not been checked against real prices here.",
      "\"100+\" means 100 or more, so the chance is measured from 99.5, the same way the player markets work.",
      "Fast games lift both teams together, so the chance of both is higher than the two chances multiplied. The detail above shows by how much.",
      "Use lines for exactly the window you are pricing. Lines for the full game do not describe the first three quarters."
    ].forEach((n) => notes.appendChild(h("li", { text: n })));
    res.appendChild(notes);
  }
  ["t-home", "t-away", "t-total", "t-reach-home", "t-reach-away", "t-std", "t-rho"].forEach((id) => $(id).addEventListener("input", recomputeTeam));

  // ============================================================
  // DOUBLE / TRIPLE DOUBLE
  // ============================================================
  function recomputeDirect() {
    const margin = readMargin();
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
  $("dbook").addEventListener("input", recomputeDirect);

  // ============================================================
  // FOUL-OUT
  // ============================================================
  function recomputeFoul() {
    const margin = readMargin();
    const msgs = $("fmessages"), res = $("fresults");
    clear(msgs); clear(res);
    if (margin.error) msgs.appendChild(callout("warn", margin.error));

    const pfRaw = $("pf").value.trim(), oddsRaw = $("fbook").value.trim(), safetyRaw = $("fsafety").value.trim();
    if (pfRaw === "") {
      res.appendChild(h("p", { class: "empty", text: "Pick the two teams, or enter the projected fouls." }));
      return;
    }
    const pf = Number(pfRaw);
    const hasBook = oddsRaw !== "";
    const odds = Number(oddsRaw), safety = safetyRaw === "" ? 2 : Number(safetyRaw);
    const problems = [];
    if (!Number.isFinite(pf) || pf <= 0) problems.push("Projected fouls must be a positive number.");
    if (hasBook && (!Number.isInteger(odds) || Math.abs(odds) < 100)) problems.push("The book's price must be +100 or higher, or -100 or lower.");
    if (hasBook && (!Number.isFinite(safety) || safety < 0)) problems.push("The safety margin must be zero or more.");
    if (problems.length) { msgs.appendChild(callout("err", "Fix this to see a price:", problems)); return; }

    // Fair odds and odds with margin only need the PF. The book's price adds edge, ROI and status.
    let probability, fairOdds, extrapolated, r = null;
    if (hasBook) {
      r = E.calculatePrice(pf, odds, safety);
      probability = r.calibrated_probability; fairOdds = r.fair_american_odds; extrapolated = r.extrapolated;
    } else {
      const ip = E.interpolateProbability(pf);
      probability = ip[0]; extrapolated = ip[1];
      fairOdds = E.probabilityToFairAmerican(probability);
    }
    const q = quoteFor(probability, margin);
    const card = h("div", { class: "card", style: "margin-top:20px" });
    card.appendChild(h("div", { class: "pair" },
      h("div", { class: "big fair" }, h("div", { class: "k", text: "Fair odds" }), h("div", { class: "v", text: fmtOdds(fairOdds) })),
      h("div", { class: "big quote" }, h("div", { class: "k", text: "Odds with margin" }), h("div", { class: "v", text: fmtOdds(q.odds) }))));
    const dl = h("dl");
    const add = (k, v, cls) => { dl.appendChild(h("dt", { text: k })); dl.appendChild(h("dd", { class: cls || "", text: v })); };
    add("Probability", (probability * 100).toFixed(2) + "%");
    if (r) {
      add("Book's break-even", r.market_break_even_probability_percent.toFixed(2) + "%");
      add("Edge", fmtSigned(r.probability_edge_pp, 2, " pp"), r.probability_edge_pp > 0 ? "pos" : "neg");
      add("Expected ROI", fmtSigned(r.expected_roi_percent, 1, "%"), r.expected_roi_percent > 0 ? "pos" : "neg");
      add("Lowest price worth taking", fmtOdds(r.minimum_acceptable_american_odds));
      const stat = h("span", { class: "status " + r.status, text: r.status.replace(/_/g, " ").toLowerCase() });
      dl.appendChild(h("dt", { text: "Status" })); dl.appendChild(h("dd", null, stat));
    }
    card.appendChild(dl);
    if (!r) card.appendChild(h("p", { class: "hint", style: "margin:12px 0 0", text: "Add the book's price to see the edge, expected ROI and status." }));
    res.appendChild(card);
    if (extrapolated) {
      msgs.appendChild(callout("warn", "Outside the 30 to 50 fouls the model was calibrated on, so this probability is extended past its data."));
    }
  }
  ["pf", "fbook", "fsafety"].forEach((id) => $(id).addEventListener("input", recomputeFoul));

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
  function recomputeAll() { recomputeAny(); recomputeTeam(); recomputeDirect(); recomputeFoul(); recomputeRare(); }
  $("margin").addEventListener("input", recomputeAll);
  $("margin-mode").addEventListener("change", recomputeAll);

  recomputeAll();
})();
