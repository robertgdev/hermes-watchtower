# Skill audit (wrench modal) and trash-backed cleanup

Applies to `#amodal` (the 🔧 beside ⓘ), `/api/skillsaudit`, `/api/skillsdelete`.

## UI wiring
- Wrench (🔧) beside ⓘ on each card opens `#amodal` (`modal-panel ppanel`, same two-pane layout as `#pmodal`). Header: title, `#audit-days` select (30/90/forever), `#audit-run` "Audit Skills", `#delete-skills` (hidden), ✕.
- **The audit runs automatically on open** (`openActions()` calls `runAudit()`) and again on every `#audit-days` change; `#audit-run` is a manual re-run. The user expects the skill list the moment the wrench is clicked — a pre-audit "Press Audit Skills to scan." pane was reported as "none of the skills are listed" (twice, across two wrong root-cause hunts). Never gate the modal's primary content behind a button press, and keep the grey diagnostic line `api: profile=… days=… → N skills, M unused` under the header: it is what finally distinguished "click never ran" from "server returned nothing".
- `#audit-days` carries `margin-left:auto` so the select + buttons right-align left of ✕; `#amodal-close` keeps its own `margin-left:auto`.
- Rows are `.arow` = checkbox (`data-k`) + `.atext` (name/meta; click = preview the SKILL.md).
- Selection state: `A_SEL` = Set of `key = profile:rel`, `A_BY_KEY` = Map key→entry, `AUDIT_LIST` = every skill (used and unused). `renderAudit()` re-renders from those three so select-all and single toggles share one path; `updateDeleteBtn()` hides/shows `#delete-skills` as `Delete Skills (N)`.
- The actions modal is the designated home for future per-bot maintenance actions: each action is a header control group (select + `.actbtn`) left of ✕ with results in the two panes — extend this modal, don't add a new one per action.

## How "unused" is derived
- Enumerate `skills/**/SKILL.md` (frontmatter `name:`, fallback dir name), then join against per-profile usage = recorded `skill_view`/`skill_manage` tool RESULTS whose JSON has `"success": true` and a `name` (per-skill count + max timestamp), plus `skills/.usage.json` `last_used_at`/`last_viewed_at` (those survive message compaction).
- `unused` = never used, or last use older than the cutoff; `days=0` = never-used only.
- Never treat `.usage.json` alone as truth — it keeps entries for deleted skills. Messages are ground truth; `.usage.json` only extends the last-used timestamp.
- The API returns every skill with an `unused` flag, sorted never-used first.

## UI must list ALL skills, not just the unused subset
The first implementation filtered to unused rows. On a healthy bot (0 unused of 9) the pane rendered empty and read as "none of the skills are listed" — a bug report against working code. Correct shape:
- unused rows: checkbox enabled, meta `never used` or `unused · last used YYYY-MM-DD HH:MM · N loads`;
- used rows: dimmed (`.arow.used`), checkbox **disabled**, meta `used <when> · N loads`;
- header `N of M skills unused in the last Nd`, or `All M skills were used within the selected window` — and still list them;
- select-all ticks unused rows only; a new `renderAudit()` cycle drops selected keys that are no longer present or no longer unused.
- Empty-collection case (`total == 0`) gets an explicit "No skills installed for this bot." — never a blank pane.

## Preview
Row click fetches `/api/profilefile?profile=$A_PROFILE&file=<rel>`. For `profile=all` the entry's `rel` is already bot-prefixed and `read_profile_file` splits the first segment — so always pass `A_PROFILE`, never the entry's own `profile`, or per-bot entries 404.

## The one write action (delete)
- `#delete-skills` hidden until `A_SEL.size > 0`; opens `#delmodal`: real question ("Are you sure you wish to delete these unused skills?"), the full path list, Cancel and `Delete N skills`.
- `POST /api/skillsdelete` requires header `X-Watchtower: delete` — a custom header forces a CORS preflight the server never answers, so a random web page cannot drive-by POST to localhost.
- `delete_skills()` enforces, per item: `rel` ends `/SKILL.md`; realpath resolves inside `<profile>/skills/`; target is not a symlink; a real `SKILL.md` exists. Then it **moves** the dir to `~/.hermes/trash/watchtower-skills/<stamp>/<profile>/<rel>` — never `rm -rf`.
- After success: re-run the audit, show the trash path in the preview pane ("Deleted N skills — moved to <path>"), and say "moved to trash", never "permanently deleted". Restore = move it back into `<profile>/skills/<...>`.
- Inspect a non-empty `~/.hermes/trash/watchtower-skills/*` before deleting it — those are the user's real deletions (a leftover there held `airtable` and `apple-reminders`).

## Proving the flow without risking real skills
Create a throwaway skill in a real profile (`skills/dev/zz-<name>/SKILL.md`), drive the UI: select → **Cancel** → confirm the file still exists on disk → select again → Delete → check the returned trash path → confirm the audit list refreshed → remove the trash entry and the empty parent dirs. The Cancel pass is what proves the confirmation actually gates the write.
