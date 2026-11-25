# Repo hygiene, packaging and publishing Watchtower

## Pre-publish hygiene
- `git ls-files` is the audit, not the working tree: IDE metadata (`.idea/`, `*.iml`, `.vscode/`, `.DS_Store`) gets committed silently when the dir existed before `.gitignore` covered it. Untrack with `git rm -r --cached .idea` (keep the local dir), add the pattern, re-run `git ls-files` until only app files remain.
- Prove nothing sensitive ever landed: `git log --all --oneline -- config.json server.log __pycache__` must print nothing. A clean working tree says nothing about history — survive the `git log` check before telling the user "no secrets were committed".
- Ship the template, not the live config: `config.example.json` is tracked, the real `config.json` (user rates/multiplier) stays gitignored, and `install.sh` must never overwrite an existing copy.
- `references/` drifts like SKILL.md does (installed copy canonical, repo copy behind): copy, `diff -rq`, then resolve every link the shipping docs mention — `grep -oh 'references/[a-z0-9-]*\.md' README.md SKILL.md | sort -u` and `test -f` each. The installed skill can hold a reference the repo never received while the repo's SKILL.md links it: a broken link on the page a cloner reads first.
- Neutralize one-user phrasing ("this user treats …") in anything that ships — restate it as the general rule; the behaviour survives, the private context does not.
- Exercise `install.sh`/`open.sh` against sandboxed `HERMES_HOME` and `XDG_DATA_HOME` (both pointed at a scratch dir): assert the copied tree, the `.desktop` `Exec=` path, that `open.sh` REUSES an already-running server (one listener per port via `ss`), and that it cold-starts on a free port — then delete the sandbox.
- Regenerable artifacts (`__pycache__/`, `server.log`) may be deleted from disk but stay ignored; deleting a running server's log means restarting it so a fresh one exists for the next debug session.

## Naming
- The name must be honest about scope: the tool observes, analyzes and reports. One confirmed, trash-backed delete does not make it a control panel — observation metaphors (watchtower, panoptes, observatory, scope) fit; control metaphors ("operate", "admin", "manager") promise actions it does not take. Chosen: **hermes-watchtower**.
- Availability check before committing: GitHub `curl -s "https://api.github.com/search/repositories?q=<name>+in:name"` → `.total_count`; unauthenticated search allows ~10 req/min, so sleep 6–7 s between queries (a faster loop returns `?`). PyPI: `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/<name>/json` → 404 = free.
- Repo metadata: description "Read-only observability for Hermes Agent profiles — sessions, tool calls, tokens, cost, attention items. Stdlib Python, zero deps."; topics `hermes`, `observability`, `dashboard`, `sqlite`, `python`.
- Renaming touches every surface: dir; index.html (title, brand crumb, footer); server.py (docstring, startup print); start.sh; open.sh/install.sh headers; tests docstring; README (title, screenshot alt, commands, layout tree); **LICENSE (the copyright line carries the old product name)**; and the installed skill dir + frontmatter. `mv` the skill dir, edit `name`/`description`/heading/paths, then `grep -rn '<old-name>' --include='*.md' --include='*.py' --include='*.sh' --include='*.html' --include='LICENSE' .` and restart before declaring done.

## Rewriting commit authorship before the first push
```bash
git config user.name  "<handle>"
git config user.email "<email>"
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --env-filter '
  export GIT_AUTHOR_NAME="<handle>" GIT_AUTHOR_EMAIL="<email>"
  export GIT_COMMITTER_NAME="<handle>" GIT_COMMITTER_EMAIL="<email>"
' --tag-name-filter cat -- --all
rm -rf .git/refs/original
git reflog expire --expire=now --all && git gc --prune=now
```
- Without the `refs/original` purge + reflog expiry, `git log --all` still shows the old identities (via the backup refs) and reads as "the rewrite failed".
- Verify: `git log --all --format='%an <%ae> | %cn <%ce>' | sort | uniq -c`, `git log --oneline | wc -l` (count unchanged), `git fsck --strict`.
- `--force-with-lease` fails after a rewrite because the rewrite also updated `refs/remotes/origin/*`; pass the explicit old SHA (`--force-with-lease=main:<old-sha>`) or use plain `--force` after acknowledging the overwrite.
- Remote tags keep pointing at pre-rewrite commits: delete (`git push origin :refs/tags/vX`) or re-tag the new HEAD and force-push the tag — otherwise the old author stays visible on GitHub under that tag.
