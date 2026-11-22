---
name: hermes-operate-dashboard
description: "Use when extending or fixing the Hermes Operate dashboard."
version: 1.0.0
author: Hermes Agent + rng
license: MIT
metadata:
  hermes:
    tags: [hermes, dashboard, monitoring, sqlite, per-profile, usage]
    related_skills: [hermes-agent]
---

# Hermes Operate dashboard

Live per-profile usage dashboard for Hermes agents (sessions, tool calls, tokens, estimated cost, success/failure per bot) served from ~/hermes-dashboard at http://127.0.0.1:8790. The user iterates on this heavily; a future session must be able to pick up where a previous one left off.

## When to Use

Load this skill whenever the task involves the ~/hermes-dashboard project: adding a metric, section, modal, or filter; changing layout/theme/formatting; debugging why a number or view is wrong; restarting or recovering the server; or packaging the dashboard for other users. It also covers reading Hermes's per-profile state DBs (sessions/tokens/cost) for any purpose.

## Run / restart
- Start: `~/hermes-dashboard/start.sh [port]` (default 8790). Frontend edits need no restart (index.html is read fresh per request); server.py edits do.
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
- `/api/toolcalls?profile=&range=&date=` — per-call history. `profile=all` merges every bot: prefix session titles `"bot · title"` and keep a per-call `profile` field so the detail fetch routes to the right DB.
- `/api/toolmessage?profile=&sid=&call=` — full raw content of one tool result, fetched lazily per row (never embed full payloads in the list endpoint).
- `/api/profilefiles?profile=` and `/api/profilefile?profile=&file=` — whitelisted key files only: SOUL.md, config.yaml, profile.yaml, memories/*.md, skills/**/SKILL.md, plugins/*/plugin.yaml. Never list or serve .env, auth.json, DBs, caches, lock files, or transient runtime JSON.

## Frontend conventions (index.html, vanilla JS + inline SVG, zero deps)
- Prefs persisted in localStorage: `ho-theme` (dark/light), `ho-all` (All-card toggle). Apply stored prefs in a head inline script before first paint to avoid theme flash.
- Profiles sort active → idle → inactive, then cost; the "All" card is a client-side sum of totals and renders first.
- Success bar = green (success%) + red (errors%) segments; bots with zero tool results get a solid gray `na` bar and NO detail lines below it.
- Modal stack: tool-call list (#modal) → full-message viewer (#dmodal, copy button) → info popover (#imodal) → profile files (#pmodal). All close on ✕, backdrop, Escape.
- Date format in tables: `YYYY-MM-DD HH:MM` (zero-padded), not locale strings.

## Configuration & hardening
- Env knobs: `HO_FAIL_MARKERS` (comma-separated kinds `success_false|error_nonnull|status_error|exit_code`), `HO_EXTRA_FAIL_MARKERS` (pipe-separated literal substrings). If Hermes changes tool-result JSON, add the new error shape via `HO_EXTRA_FAIL_MARKERS` — no code change.
- Cost: `config.json` (`rates` per model in USD/1M + `cost_multiplier`, env `HO_RATES`/`HO_COST_MULTIPLIER`) recomputes cost from tokens when rates exist; otherwise stored `estimated_cost_usd`. This dashboard only sees Hermes sessions — provider balances shared with other tools (IDE/CLI) will show more spend than this.
- Schema guard: `SUPPORTED_SCHEMA = 33`. `/api/data` returns `schema.profiles` + `schema.mismatch`; mismatched profiles get a `schema_warning` and the rest keeps working. Bump `SUPPORTED_SCHEMA` when queries change.
- Port fallback: if the requested port is busy, `main()` binds an ephemeral port and prints the real URL.
- Tests: `python3 tests/test_aggregation.py` (31 checks, stdlib only, synthetic Hermes home fixture). Run after any `server.py` change.

## Pitfalls
- Query params: `encodeURIComponent` turns `/` into `%2F`; a naive `split("=")` query parser never URL-decodes, so path/whitelist lookups silently fail for any value containing slashes. Decode server-side with `urllib.parse.unquote`.
- Never set a container's innerHTML to a "Loading…" placeholder if child elements must survive — it destroys them (wiping #modal-body killed #callbody). Put loading/error rows inside the tbody.
- Give every element the JS references an actual `id` — a div with only `class="modal-body"` makes `$("#modal-body")` null and the open function dies silently right after setting the title.
- Synthetic cards (the "All" aggregate) must carry a separate API key (`profile_key: "all"`) from their display name, or drill-down fetches 404 on the capitalized name.
- Sparkline max must come from the actual data array, not a lookup keyed by the profile name (an aggregate card's name has no activity rows → overflow).
- Flex spacing: to shrink the gap between a wrapped flex line and a full-width child, use a negative margin on the child (gap + margin combine) — measure the actual px with getBoundingClientRect, don't eyeball.

## Verification with the browser harness
- Each `js()` call is a fresh evaluation: Python variables don't cross into JS strings, and top-level `const` collides on re-declaration — wrap in an IIFE or reassign without `const`.
- Keep async IIFEs under the harness's ~5s response timeout — split long flows (click → wait → assert) into separate calls.
- `bool(js("document.getElementById(...)"))` lies (DOM-element serialization) — probe `.textContent`/`outerHTML` instead.
- `Array.from(nodeList).includes('All')` compares DOM NODES to strings and is always false — map to `.textContent` first.
- Do not call the harness `js()` helper from inside page JS (it's not defined there); use plain DOM `.click()`/`dispatchEvent` in the page.

## If the dashboard files vanish
See references/file-recovery-from-session-history.md — the exact files can be reconstructed from Hermes's own session history when no backup exists.
