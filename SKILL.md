---
name: hermes-watchtower
description: "Use when extending or fixing the Watchtower dashboard."
version: 1.0.0
author: Hermes Agent + rng
license: MIT
metadata:
  hermes:
    tags: [hermes, dashboard, monitoring, sqlite, per-profile, usage]
    related_skills: [hermes-agent]
---

# Hermes Watchtower

Live per-profile usage dashboard for Hermes agents (sessions, tool calls, tokens, estimated cost, success/failure per bot) served from ~/hermes-watchtower at http://127.0.0.1:8790. The user iterates on this heavily; a future session must be able to pick up where a previous one left off.

## When to Use

Load this skill whenever the task involves the ~/hermes-watchtower project: adding a metric, section, modal, or filter; changing layout/theme/formatting; debugging why a number or view is wrong; restarting or recovering the server; or packaging the dashboard for other users. It also covers reading Hermes's per-profile state DBs (sessions/tokens/cost) for any purpose.

## Run / restart
- Start (foreground): `~/hermes-watchtower/start.sh [port]` (default 8790). Frontend edits need no restart (index.html is read fresh per request); server.py edits do.
- Launch (start-if-needed + browser): `~/hermes-watchtower/open.sh [port]` — probes `/api/data`, starts detached only when nothing answers, prints the URL, opens `$BROWSER`/`xdg-open`/`open`/`wslview`. This is what the desktop menu entry runs.
- Install for a Hermes user: `./install.sh` copies the app to `~/.hermes/skills/hermes-watchtower/`, writes `~/.local/share/applications/hermes-watchtower.desktop` (Exec → `open.sh`), prints next steps; `--systemd` adds an always-on user service; `--no-skill` runs the clone in place; `--force` upgrades. Never clobbers `config.json`.
- Restart: `kill $(pgrep -f "^python3 server.py")` then nohup-start again. NEVER an unanchored `pkill -f server.py` in the same command — the pattern matches your own shell's command line (self-termination), and the bracket trick fails when the command also contains the plain string (e.g. in the nohup part). Anchor `^python3 ` and kill the PID.
- After every change, verify with curl (API) and the browser harness (UI), not by reading the code.

## Data sources (all read-only)
- `~/.hermes/state.db` = default profile; `~/.hermes/profiles/<name>/state.db` per bot; `profiles/<name>/cron/executions.db` (usually empty). Open with `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` — safe alongside a live gateway (WAL).
- `sessions`: per-session tokens (input/output/cache_read/cache_write/reasoning), `estimated_cost_usd` (providers never report actual cost — always say "estimated"), model, timestamps, tool_call_count, title, profile_name.
- `session_model_usage`: per-model cost/calls per session — drives cost-by-model views.
- `messages`: `role='tool'` rows are tool results; `tool_call_id` == the assistant call's `call_id`/`id`, so calls pair exactly to results (duration = result.ts - call.ts). `token_count` is NULL on every message: per-call tokens are NOT recorded — estimate from payload chars/4 and label the column as an estimate.

## Failure detection
A tool result is failed iff its content matches any of: `"success": false` OR a non-null `"error":` key OR `"status": "error"` OR `EXIT_CODE=[1-9]`. A naive `LIKE '%error%'` is ~40x too noisy (matches `"error": null` and file names).

## API (server.py)
- `/api/data?range=&date=` — aggregate payload; range 1/2/7/30/0; an optional `date` anchors the window END (24h = that calendar day, 7d = N days ending on it); no date = rolling window from now.
- `/api/toolcalls?profile=&range=&date=` — per-call history. `profile=all` merges every bot: prefix session titles `"bot · title"` and keep a per-call `profile` field so the detail fetch routes to the right DB. Also returns `tokens_in`, `tokens_out`, `est_cost` and a per-session `sessions[]` array (title, messages, calls, tokens in/out, cost) — the modal summary line is computed from these and follows the session selector.
- `/api/toolmessage?profile=&sid=&call=` — full raw content of one tool result, fetched lazily per row (never embed full payloads in the list endpoint).
- `/api/profilefiles?profile=` and `/api/profilefile?profile=&file=` — whitelisted key files only: SOUL.md, config.yaml, profile.yaml, memories/*.md, skills/**/SKILL.md, plugins/*/plugin.yaml. Never list or serve .env, auth.json, DBs, caches, lock files, or transient runtime JSON.
- `/api/skillsaudit?profile=&days=30|90|0` — installed skills vs. recorded use (see Skill audit below).
- `POST /api/skillsdelete` (header `X-Watchtower: delete`, body `{items:[{profile,rel}]}`) — the ONLY write endpoint; moves skill dirs to `<home>/trash/watchtower-skills/<stamp>/`.

## Read-only claim: nuance
- DBs, file browsing and everything else stay read-only — but the dashboard is no longer *entirely* read-only: skill deletion is an explicit, confirmed, trash-backed action. When documenting, say "one write action, trash-backed", not "read-only".

## Skill audit (🔧 actions modal)
- Every installed skill is listed (unused first, checkboxes enabled) with used skills dimmed and their checkboxes disabled — an empty pane on a healthy bot reads as a bug. The list is generated **automatically when the wrench modal opens**; `#audit-run` only re-runs it. The only write action is confirmed + trash-backed deletion. Full wiring, the `unused` derivation, the delete guards and the throwaway-skill proof: `references/skill-audit-and-cleanup.md`.

## Frontend conventions (index.html, vanilla JS + inline SVG, zero deps)
- Prefs persisted in localStorage: `ho-theme` (dark/light), `ho-all` (All-card toggle). Apply stored prefs in a head inline script before first paint to avoid theme flash.
- Profiles sort active → idle → inactive, then cost; the "All" card is a client-side sum of totals and renders first.
- Success bar = green (success%) + red (errors%) segments; bots with zero tool results get a solid gray `na` bar and NO detail lines below it.
- Modal stack: tool-call list (#modal) → full-message viewer (#dmodal, copy button) → info popover (#imodal) → profile files (#pmodal) → actions (#amodal) → delete confirmation (#delmodal). All close on ✕, backdrop, Escape. The tool-call modal has a session-title selector + All/Success/Failed outcome filter, and a summary line under the title ("N messages, M tool calls").
- A NEW modal is closeable only after two registrations in the global handlers: a `#<id>-close` / `#<id>-back` line in the document click handler (next to the existing `#pmodal-close` clause) and `$("#<id>").hidden = true` in the Escape handler. Skip either and the modal can only be dismissed by reloading the page — and a screenshot taken through the harness will happily show it open.
- The tool-call modal's summary line sits inside the header (above the border) and reads `N messages, M tool calls · X in / Y out · ~$Z`; when a session is selected it switches to that session's stats from `sessions[]`. Do NOT append the session title to that line — the selector already shows it (the user removed redundant labels).
- Fixed-size panels: the profile-files modal has an explicit height (`min(84vh, 820px)`) with `flex:1;min-height:0` panes so the left file list does not move when file length changes. Apply the same rule to any new split-pane modal: content length must never shift interactive elements.
- Never label an idle/no-activity bot "down" — the user reads "down" as broken. Display "inactive Xh Ym" or "no activity in window" (status values stay active/idle/down internally for dot colors); attention entries read "<bot> — <detail>" with a last-seen sub-line.
- Date format in tables: `YYYY-MM-DD HH:MM` (zero-padded), not locale strings.
- Modal ✕ buttons are right-aligned only because a sibling carries `margin-left:auto` (a select, a copy button) or the close button itself does (`#imodal-close`, `#pmodal-close`, `#amodal-close`). A new title-only header needs `#<modal>-close{margin-left:auto}` or the ✕ hugs the title — the user notices and asks for the fix.
- Right-aligning a control group next to ✕: give the FIRST control `margin-left:auto` (`#audit-days`) and let ✕ sit after it; `#callsession` uses the same pattern in the tool-call modal.
- `.pfiles button` is single-line with ellipsis; a two-line row (name + `.ameta` meta span) needs `.ameta{display:block;white-space:normal}` or the meta get clipped.
- Info icons use `--info`/`--info2`: bright in dark mode (`#a7b2c0` / `#8f9aab`), muted in light (`#59636e` / `#6d7681`). Never drop the dark value to `--dim`/`--dim2` — the user reports the icons as "hardly visible" on dark cards.

## Configuration & hardening
- Env knobs: `HO_FAIL_MARKERS` (comma-separated kinds `success_false|error_nonnull|status_error|exit_code`), `HO_EXTRA_FAIL_MARKERS` (pipe-separated literal substrings). If Hermes changes tool-result JSON, add the new error shape via `HO_EXTRA_FAIL_MARKERS` — no code change.
- Cost: `config.json` (`rates` per model in USD/1M + `cost_multiplier`, env `HO_RATES`/`HO_COST_MULTIPLIER`) recomputes cost from tokens when rates exist; otherwise stored `estimated_cost_usd`. This dashboard only sees Hermes sessions — provider balances shared with other tools (IDE/CLI) will show more spend than this. The user accepts an approximate figure: show tokens × provider list rate, state the Hermes-only scope, and calibrate only if asked — do not chase exact billing. Rates, the recompute formula, the rate-solving technique and the calibration procedure: `references/cost-and-pricing.md`.
- Schema guard: `SUPPORTED_SCHEMA = 33`. `/api/data` returns `schema.profiles` + `schema.mismatch`; mismatched profiles get a `schema_warning` and the rest keeps working. Bump `SUPPORTED_SCHEMA` when queries change.
- Port fallback: if the requested port is busy, `main()` binds an ephemeral port and prints the real URL.
- Tests: `python3 tests/test_aggregation.py` — stdlib-only suite over a synthetic Hermes home; every check must pass (the count grows; don't hardcode it). Run after any `server.py` change, especially cost/failure-marker logic.

## Repo hygiene & pre-publish
- Nothing sensitive may sit in history (`git log --all --oneline -- config.json server.log __pycache__` prints nothing), IDE metadata must be untracked, and the shipped repo carries `config.example.json` while the live `config.json` stays gitignored. Full checklist (including `references/` link resolution and sandboxed `install.sh`/`open.sh` verification): `references/packaging-and-publishing.md`.

## Packaging & naming
- Repo: `~/hermes-watchtower`, push-ready; SKILL.md at the root makes it installable as a Hermes skill (skill + app in one artifact). Two skill copies exist — the installed one is canonical, re-sync the repo copy with `cp` + `diff`. Name honesty, availability checks, repo metadata, rename surfaces and the git-authorship rewrite recipe: `references/packaging-and-publishing.md`.

## Pitfalls
- Patch precision: `new_string` must be a complete, line-terminated block, and the returned diff must be read on every edit. A dropped trailing newline merges two statements (`def f():    """…"""`, or a `## heading` glued to its first bullet) and only lint/execution catches it; an invented wrapper (`$(x) && (…)`) silently changes behaviour. Run the linter or tests right after a patch batch in the same file — do not stack several patches and only test at the end.
- Query params: `encodeURIComponent` turns `/` into `%2F`; a naive `split("=")` query parser never URL-decodes, so path/whitelist lookups silently fail for any value containing slashes. Decode server-side with `urllib.parse.unquote`.
- Never set a container's innerHTML to a "Loading…" placeholder if child elements must survive — it destroys them (wiping #modal-body killed #callbody). Put loading/error rows inside the tbody.
- Give every element the JS references an actual `id` — a div with only `class="modal-body"` makes `$("#modal-body")` null and the open function dies silently right after setting the title.
- Synthetic cards (the "All" aggregate) must carry a separate API key (`profile_key: "all"`) from their display name, or drill-down fetches 404 on the capitalized name.
- Sparkline max must come from the actual data array, not a lookup keyed by the profile name (an aggregate card's name has no activity rows → overflow).
- Flex spacing: to shrink the gap between a wrapped flex line and a full-width child, use a negative margin on the child (gap + margin combine) — measure the actual px with getBoundingClientRect, don't eyeball.
- A "nothing is listed" report means reproduce on the case that renders EMPTY, not the richest dataset: a first repro on a bot with 183 unused of 205 "proved it works" while the real failure was a bot with 0 unused of 9 — an empty pane on a healthy bot. Check the frontend filter even when the API returns rows (healthy backend ≠ healthy UI), and state which bot/state you tested so a "works for me" cannot silently miss the user's case.
- Deleting a skill in the browser harness: `checkbox.click()` **toggles** — read `.checked` first. A "delete did nothing" scare was a second click un-selecting the row, not a bug in the delete path.
- Counting servers with `pgrep -fc 'python3 server.py'` also matches the pgrep's own shell and over-reports; use `ps -eo pid,args | grep '[s]erver\.py'`. Same trap with `kill $(pgrep -f '... --port N')` — it SIGTERMs your own command, so kill by explicit PID.
- `index.html` MUST be served with `Cache-Control: no-store` (it has no ETag/Last-Modified, so browsers apply heuristic freshness and silently reuse old JS after an upgrade). Symptom when missing: a UI fix "does nothing" — the user reported "still empty" twice while the server was already serving the fixed file. The footer shows `ui <UI_BUILD>`; bump `UI_BUILD` in index.html on UI changes and ask the user to confirm it after a hard reload. Add a `do_HEAD` for `/` alongside it — health checks (`curl -I`) return `501 Unsupported method` without one.
- In test fixtures that build state DBs, commit() before close() — sqlite3 rolls back uncommitted inserts on close(), so a session silently vanishes and aggregation totals come back short with no error.
- Adding `tool_result` rows to the fixture shifts the asserted `tool_results` / failure totals. To exercise recorded skill usage, rewrite an EXISTING call's result content to `{"success": true, "name": "<skill>"}`, or drive last-used dates through `skills/.usage.json` — only insert new rows when the test is meant to change those totals.

## Debugging a UI report you cannot reproduce
The harness agreeing with you is not evidence the user's browser agrees. Work this ladder in order and stop at the first step that explains the report:
1. **Identify the build.** The footer prints `ui <UI_BUILD>` — ask for it (or a screenshot) before touching code. An old build explains "the fix did nothing"; re-fixing code the user cannot be running wastes a full round trip.
2. **Find the reproducing case.** Curl the endpoint for EVERY profile, not the richest one: `for prof in <all names>; do curl -s "/api/skillsaudit?profile=$prof&days=30"; done`. A failure that only shows on a healthy account (zero unused / zero rows) is invisible on the default profile, which is exactly the case a "works for me" repro misses.
3. **Instrument instead of arguing.** Add a server-side log line per request (`[audit] profile=… days=… total=… unused=…` → `server.log`) and an on-screen diagnostic (`api: profile=… days=… → N skills, M unused`). Then ask the user for that one line or a screenshot — it makes their next message diagnostic instead of another "same issue".
4. **Map the value to the cause and say so up front:** `→ 0` = server-side/data problem for that profile; `→ N` but no rows = rendering problem in their build; no line at all = the request never reached the server (log stays silent) or the modal never opened (click target/overlay).

Never reply "works for me" — state which build, which bot and which state you tested, then hand the user one observable that discriminates the remaining causes.

## Verification with the browser harness
- Each `js()` call is a fresh evaluation: Python variables don't cross into JS strings, and top-level `const` collides on re-declaration — wrap in an IIFE or reassign without `const`.
- Keep async IIFEs under the harness's ~5s response timeout — split long flows (click → wait → assert) into separate calls.
- `bool(js("document.getElementById(...)"))` lies (DOM-element serialization) — probe `.textContent`/`outerHTML` instead.
- `Array.from(nodeList).includes('All')` compares DOM NODES to strings and is always false — map to `.textContent` first.
- Do not call the harness `js()` helper from inside page JS (it's not defined there); use plain DOM `.click()`/`dispatchEvent` in the page.
- README screenshot: verify the DOM first (card count, brand text), then `Emulation.setDeviceMetricsOverride` (width 1600, deviceScaleFactor 1) + `Page.captureScreenshot(captureBeyondViewport=True)` → base64-decode into `docs/screenshot.png`. A fresh harness context follows the OS theme, so `localStorage.setItem('ho-theme','dark')` and reload in the SAME call, and assert `data-theme` before capturing. Verify the file by reading the PNG header (`struct.unpack('>II', data[16:24])`) rather than trusting the call.
- The harness page is NOT the user's browser: a fresh context can be tiny (~780×437), so `click_at_xy` on an element whose centre sits near the fold silently misses and `document.elementFromPoint` returns `null`. Check `innerWidth/innerHeight`, call `Emulation.setDeviceMetricsOverride` (e.g. 1600×1000) before coordinate clicks, or drive the element with `.click()` and reserve real-coordinate clicks for gesture-specific tests (e.g. an overlay blocking a button).
- `localStorage` is inaccessible before the first navigation (`SecurityError: Access is denied for this document`): navigate once, set the pref, then navigate again and assert the effect — do not call `js("localStorage.setItem(...)")` as the first statement of a call.

## If the dashboard files vanish
See references/file-recovery-from-session-history.md — the exact files can be reconstructed from Hermes's own session history when no backup exists.
