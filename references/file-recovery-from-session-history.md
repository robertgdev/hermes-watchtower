# Recovering deleted text files from Hermes session history

When files written in past Hermes sessions disappear (deleted, cleaned up, host restored) and no backup exists, the exact bytes usually still live in the session store: assistant `tool_calls` arguments are persisted verbatim.

## Procedure

1. Find the DB that ran the session: try `~/.hermes/state.db` (default profile), then `~/.hermes/profiles/<name>/state.db` for the profile that did the work.
2. Query the `messages` table for assistant rows whose `tool_calls` mention the file path:
   `SELECT id, tool_calls FROM messages WHERE role='assistant' AND tool_calls LIKE '%<path-fragment>%' ORDER BY id`.
3. For each row, parse twice: `json.loads(row["tool_calls"])` (list of calls), then `json.loads(call["function"]["arguments"])` (the args string is itself JSON).
4. Replay the operations in `id` order: `write_file` → write `content`; `patch` → replace `old_string` with `new_string` (exactly once unless `replace_all`). chmod +x anything executable.
5. Verify before claiming success: syntax-compile scripts, grep for feature markers, then start/run and exercise the artifact.

## Pitfalls
- `patch` fuzzy matching means old_strings in the store match the file content at that point — replay exactly in order, do not skip or reorder.
- Reconstructed files are only as current as the last patch: apply every stored patch, not just the write_file.
- `token_count` and per-message metrics are not usable for this; only the `tool_calls` payload matters.
- The recovery is a restore, not a version history: if the directory was removed, check trash and the filesystem first, then use this as the fallback.
