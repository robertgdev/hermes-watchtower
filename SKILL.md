---
name: hermes-watchtower
description: "Use when extending or fixing the Hermes Watchtower dashboard."
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
- The wrench (🔧) beside ⓘ on each card opens `#amodal` (`modal-panel ppanel`, same two-pane layout as `#pmodal`); header holds title, `#audit-days` select (30/90/forever), `#audit-run` "Audit Skills" button, ✕.
- Method (ported from the Nov 22 skills-prune session): enumerate `skills/**/SKILL.md` (frontmatter `name:`, fallback dir name), then join against per-profile usage = recorded `skill_view`/`skill_manage` tool RESULTS whose JSON has `"success": true` and a `name` (per-skill count + max timestamp) plus `skills/.usage.json` `last_used_at`/`last_viewed_at` (those survive message compaction). `unused` = never used, or last use older than the cutoff; `days=0` = never used only.
- Never treat `.usage.json` alone as truth — it keeps entries for deleted skills (the prune session hit exactly that). Results use the message records as ground truth and `.usage.json` only to extend the last-used timestamp.
- The API returns every skill with an `unused` flag, sorted never-used first; the UI shows only the unused subset plus a "N of M skills unused in the last Nd" header. Row click previews the SKILL.md via `/api/profilefile`; for `profile=all` pass `file=<bot>/skills/...` (`read_profile_file` splits the bot prefix).
- Multi-select: each row is `.arow` = checkbox (`data-k`) + `.atext` (name/meta, click = preview). Selection lives in `A_SEL` (Set of `key = profile:rel`) and `A_BY_KEY`; `renderAudit()` re-renders from those two, so select-all and single toggles share one code path.
- **The one write action**: `#delete-skills` is hidden until `A_SEL.size > 0`, then reads "Delete Skills (N)" and opens `#delmodal` — a real confirmation step ("Are you sure you wish to delete these unused skills?") listing every skill path, with Cancel / Delete N skills. `POST /api/skillsdelete` requires header `X-Watchtower: delete` (custom header ⇒ CORS preflight ⇒ random pages can't drive-by POST) and `delete_skills()` enforces: rel ends `/SKILL.md`, realpath inside `<profile>/skills/`, not a symlink, real SKILL.md present — then **moves** to `~/.hermes/trash/watchtower-skills/<stamp>/<profile>/<rel>`. Never rm -rf; the trash path is shown after success and the audit re-runs automatically. Say "moved to trash", never "permanently deleted".
- The actions modal is the designated home for future per-bot maintenance actions: each action is a header control group (select + `.actbtn`) left of ✕ with its result in the two panes — extend this modal rather than adding a new one per action.
- Prove destructive flows on a throwaway skill, never a real one: create `skills/dev/zz-<name>/SKILL.md` in a real profile, drive the UI (select → Cancel → confirm the file still exists → Delete), check the returned trash path and that the audit list refreshes, then remove the trash entry and the empty parent dirs. The Cancel-first pass is what proves the confirmation actually gates the write.

## Frontend conventions (index.html, vanilla JS + inline SVG, zero deps)
- Prefs persisted in localStorage: `ho-theme` (dark/light), `ho-all` (All-card toggle). Apply stored prefs in a head inline script before first paint to avoid theme flash.
- Profiles sort active → idle → inactive, then cost; the "All" card is a client-side sum of totals and renders first.
- Success bar = green (success%) + red (errors%) segments; bots with zero tool results get a solid gray `na` bar and NO detail lines below it.
- Modal stack: tool-call list (#modal) → full-message viewer (#dmodal, copy button) → info popover (#imodal) → profile files (#pmodal). All close on ✕, backdrop, Escape. The tool-call modal has a session-title selector + All/Success/Failed outcome filter, and a summary line under the title ("N messages, M tool calls").
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

## Packaging & naming
- Repo: `~/hermes-watchtower`, git repo, push-ready. SKILL.md at the repo root makes it installable as a Hermes skill (skill + app in one artifact); references/, LICENSE, tests/, docs/screenshot.png ship with it. Publish = `git remote add origin <repo> && git push -u origin main`.
- Two skill copies exist: the installed skill (`~/.hermes/skills/software-development/hermes-watchtower/SKILL.md`) is canonical and richer; the repo copy drifts. After editing either, re-sync (`cp` installed → repo) and confirm with `diff`.
- Name must be honest about scope: the tool observes, analyzes and reports — the single write action (confirmed, trash-backed skill deletion) does not make it a control panel — so observation metaphors (watchtower, panoptes, observatory, scope) fit; control metaphors ("operate", "admin", "manager") promise actions it does not take and the user rejects them. Chosen: **hermes-watchtower**.
- Availability check before committing: GitHub `curl -s "https://api.github.com/search/repositories?q=<name>+in:name"` → `.total_count`; unauthenticated search is ~10 req/min, so sleep 6–7 s between queries (a fast loop returns `?`). PyPI: `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/<name>/json` → 404 = free.
- Repo metadata: description "Read-only observability for Hermes Agent profiles — sessions, tool calls, tokens, cost, attention items. Stdlib Python, zero deps."; topics `hermes`, `observability`, `dashboard`, `sqlite`, `python`.
- Renaming touches every surface: dir; index.html (title, brand crumb, footer); server.py (docstring, startup print); start.sh; tests docstring; README (title, screenshot alt, commands, layout tree); and the installed skill dir + frontmatter. `mv` the skill directory, edit `name`/`description`/heading/paths, then grep for leftovers across all extensions and restart before declaring done.

## Pitfalls
- Patch precision: `new_string` must be a complete, line-terminated block, and the returned diff must be read on every edit. A dropped trailing newline merges two statements (`def f():    """…"""`, or a `## heading` glued to its first bullet) and only lint/execution catches it; an invented wrapper (`$(x) && (…)`) silently changes behaviour. Run the linter or tests right after a patch batch in the same file — do not stack several patches and only test at the end.
- Query params: `encodeURIComponent` turns `/` into `%2F`; a naive `split("=")` query parser never URL-decodes, so path/whitelist lookups silently fail for any value containing slashes. Decode server-side with `urllib.parse.unquote`.
- Never set a container's innerHTML to a "Loading…" placeholder if child elements must survive — it destroys them (wiping #modal-body killed #callbody). Put loading/error rows inside the tbody.
- Give every element the JS references an actual `id` — a div with only `class="modal-body"` makes `$("#modal-body")` null and the open function dies silently right after setting the title.
- Synthetic cards (the "All" aggregate) must carry a separate API key (`profile_key: "all"`) from their display name, or drill-down fetches 404 on the capitalized name.
- Sparkline max must come from the actual data array, not a lookup keyed by the profile name (an aggregate card's name has no activity rows → overflow).
- Flex spacing: to shrink the gap between a wrapped flex line and a full-width child, use a negative margin on the child (gap + margin combine) — measure the actual px with getBoundingClientRect, don't eyeball.
- Deleting a skill in the browser harness: `checkbox.click()` **toggles** — read `.checked` first. A "delete did nothing" scare was a second click un-selecting the row, not a bug in the delete path.
- In test fixtures that build state DBs, commit() before close() — sqlite3 rolls back uncommitted inserts on close(), so a session silently vanishes and aggregation totals come back short with no error.
- Adding `tool_result` rows to the fixture shifts the asserted `tool_results` / failure totals. To exercise recorded skill usage, rewrite an EXISTING call's result content to `{"success": true, "name": "<skill>"}`, or drive last-used dates through `skills/.usage.json` — only insert new rows when the test is meant to change those totals.

## Verification with the browser harness
- Each `js()` call is a fresh evaluation: Python variables don't cross into JS strings, and top-level `const` collides on re-declaration — wrap in an IIFE or reassign without `const`.
- Keep async IIFEs under the harness's ~5s response timeout — split long flows (click → wait → assert) into separate calls.
- `bool(js("document.getElementById(...)"))` lies (DOM-element serialization) — probe `.textContent`/`outerHTML` instead.
- `Array.from(nodeList).includes('All')` compares DOM NODES to strings and is always false — map to `.textContent` first.
- Do not call the harness `js()` helper from inside page JS (it's not defined there); use plain DOM `.click()`/`dispatchEvent` in the page.
- README screenshot: verify the DOM first (card count, brand text), then `Emulation.setDeviceMetricsOverride` (width 1600, deviceScaleFactor 1) + `Page.captureScreenshot(captureBeyondViewport=True)` → base64-decode into `docs/screenshot.png`. A fresh harness context follows the OS theme, so `localStorage.setItem('ho-theme','dark')` and reload in the SAME call, and assert `data-theme` before capturing. Verify the file by reading the PNG header (`struct.unpack('>II', data[16:24])`) rather than trusting the call.

## If the dashboard files vanish
See references/file-recovery-from-session-history.md — the exact files can be reconstructed from Hermes's own session history when no backup exists.
