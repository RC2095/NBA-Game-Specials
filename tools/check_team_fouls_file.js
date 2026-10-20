// Fails (exit 1) unless site/team_fouls.js holds 30 usable teams.
// The scheduled refresh runs this before committing, so a bad download
// can never replace good data on the page.
const fs = require("fs");
const path = require("path");
const E = require(path.join(__dirname, "..", "site", "engine.js"));
global.window = {};
const file = process.argv[2] || path.join(__dirname, "..", "site", "team_fouls.js");
// eslint-disable-next-line no-eval
eval(fs.readFileSync(file, "utf8"));
const raw = window.TEAM_FOULS_DATA;
if (!raw) { console.error("team_fouls.js has no data"); process.exit(1); }
const r = E.parseTeamFouls(JSON.stringify(raw));
if (!r.data) { console.error("Unusable team data:", r.errors.join("; ")); process.exit(1); }
const n = Object.keys(r.data.teams).length;
if (n !== 30) { console.error("Expected 30 teams, found " + n); process.exit(1); }
const teams = Object.keys(r.data.teams);
const missing = teams.filter((t) => r.data.teams[t].season === null);
if (missing.length) { console.error("Missing season average for: " + missing.join(", ")); process.exit(1); }
const age = (Date.now() - Date.parse(r.data.as_of)) / 86400000;
console.log("OK: 30 teams, as of " + r.data.as_of + " (" + Math.round(age) + " days old)");
