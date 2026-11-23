#!/usr/bin/env python3
"""Hermes Watchtower — read-only observability server for Hermes agent profiles.

Reads ~/.hermes/state.db + ~/.hermes/profiles/*/state.db (read-only) and serves
a Corda-style ops dashboard at http://127.0.0.1:8790

Usage:
    python3 server.py [--port 8790] [--home ~/.hermes]

Stdlib only. Binds to localhost.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_cost_config():
    """Custom pricing: config.json {rates: {model: {input,output,cache_read,cache_write}}, cost_multiplier}
    plus env overrides HO_RATES (JSON) and HO_COST_MULTIPLIER."""
    cfg = {}
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    rates = cfg.get("rates") or {}
    if os.environ.get("HO_RATES"):
        try:
            rates = json.loads(os.environ["HO_RATES"])
        except Exception:
            pass
    mult = 1.0
    try:
        mult = float(cfg.get("cost_multiplier") or 1.0)
    except Exception:
        mult = 1.0
    if os.environ.get("HO_COST_MULTIPLIER"):
        try:
            mult = float(os.environ["HO_COST_MULTIPLIER"])
        except Exception:
            pass
    return {"rates": rates, "multiplier": mult}


_CFG = load_cost_config()
_RATES = _CFG["rates"]
_COST_MULT = _CFG["multiplier"]


def session_cost_values(model, tin, tout, tread, twrite, treason, stored):
    """Per-session cost: recompute from tokens when custom rates exist for the model,
    otherwise use the stored estimate; then apply the calibration multiplier."""
    rates = _RATES.get(model) if _RATES else None
    if rates:
        c = (tin * rates.get("input", 0) + (tout + treason) * rates.get("output", 0)
             + tread * rates.get("cache_read", 0) + twrite * rates.get("cache_write", 0)) / 1e6
    else:
        c = stored or 0
    return c * _COST_MULT

SUPPORTED_SCHEMA = 33

_DEFAULT_FAIL_KINDS = ["success_false", "error_nonnull", "status_error", "exit_code"]


def fail_marker_config():
    """Enabled failure-marker kinds, from HO_FAIL_MARKERS env (comma-separated)."""
    raw = os.environ.get("HO_FAIL_MARKERS", "")
    if raw:
        valid = set(_DEFAULT_FAIL_KINDS)
        kinds = [k.strip() for k in raw.split(",") if k.strip() in valid]
        if kinds:
            return kinds
    return list(_DEFAULT_FAIL_KINDS)


def extra_fail_markers():
    """Extra literal substrings that count as failure, from HO_EXTRA_FAIL_MARKERS (pipe-separated)."""
    raw = os.environ.get("HO_EXTRA_FAIL_MARKERS", "")
    return [s for s in raw.split("|") if s] if raw else []


def _like_literal(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_failure_sql(kinds, extra):
    parts = []
    if "success_false" in kinds:
        parts.append("""m.content LIKE '%"success": false%'""")
    if "error_nonnull" in kinds:
        parts.append("""(m.content LIKE '%"error":%' AND m.content NOT LIKE '%"error": null%')""")
    if "status_error" in kinds:
        parts.append("""m.content LIKE '%"status": "error"%'""")
    if "exit_code" in kinds:
        parts.append("""m.content GLOB '*EXIT_CODE=[1-9]*'""")
    for s in extra:
        parts.append(f"m.content LIKE '%{_like_literal(s)}%' ESCAPE '\\'")
    return "(" + " OR ".join(parts) + ")"


_FAIL_KINDS = fail_marker_config()
_EXTRA_MARKERS = extra_fail_markers()
FAILURE_SQL = build_failure_sql(_FAIL_KINDS, _EXTRA_MARKERS)

TOKEN_SUM = "(input_tokens + output_tokens + cache_read_tokens + cache_write_tokens + reasoning_tokens)"


def ro_connect(path):
    """Read-only sqlite connection (safe against live writers / WAL)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def find_profile_dbs(home):
    """Return [(profile_name, state_db_path, cron_db_path_or_None), ...]."""
    dbs = [("default", os.path.join(home, "state.db"), None)]
    for pdir in sorted(glob.glob(os.path.join(home, "profiles", "*"))):
        name = os.path.basename(pdir)
        state = os.path.join(pdir, "state.db")
        cron = os.path.join(pdir, "cron", "executions.db")
        if os.path.isfile(state):
            dbs.append((name, state, cron if os.path.isfile(cron) else None))
    return dbs


def bucket_key(ts, granularity):
    dt = datetime.fromtimestamp(ts)
    if granularity == "hour":
        return dt.strftime("%Y-%m-%d %H:00")
    return dt.strftime("%Y-%m-%d")


def bucket_start(key, granularity):
    if granularity == "hour":
        return datetime.strptime(key, "%Y-%m-%d %H:00")
    return datetime.strptime(key, "%Y-%m-%d")


def load_profile(profile, state_path, cron_path, cutoff_ts, now_ts):
    """Aggregate one profile's data within [cutoff_ts, now_ts]."""
    out = {
        "name": profile, "status": "down", "status_detail": "",
        "sessions": 0, "tool_calls": 0, "api_calls": 0, "messages": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "reasoning_tokens": 0, "tokens": 0,
        "est_cost": 0.0, "failed": 0, "tool_results": 0, "success_rate": 1.0,
        "last_activity_at": None, "last_title": None, "last_model": None,
        "models": [], "tools": [], "activity": {}, "fail_sessions": [],
        "schema_version": None,
    }
    try:
        conn = ro_connect(state_path)
    except Exception as e:
        out["status_detail"] = f"db error: {e}"
        return out
    try:
        try:
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            out["schema_version"] = row["version"] if row else None
        except sqlite3.Error:
            out["schema_version"] = None
        # --- session rollup ---
        rows = conn.execute(
            f"""SELECT COUNT(*) sessions,
                       COALESCE(SUM(tool_call_count),0) tool_calls,
                       COALESCE(SUM(api_call_count),0) api_calls,
                       COALESCE(SUM(message_count),0) messages,
                       COALESCE(SUM(input_tokens),0) input_tokens,
                       COALESCE(SUM(output_tokens),0) output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) cache_read_tokens,
                       COALESCE(SUM(cache_write_tokens),0) cache_write_tokens,
                       COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,
                       COALESCE(SUM(estimated_cost_usd),0) est_cost,
                       MAX(COALESCE(last_activity_at, started_at)) last_activity_at
                  FROM sessions WHERE started_at >= ? AND started_at <= ?""",
            (cutoff_ts, now_ts),
        ).fetchone()
        out.update({
            "sessions": rows["sessions"],
            "tool_calls": rows["tool_calls"],
            "api_calls": rows["api_calls"],
            "messages": rows["messages"],
            "input_tokens": rows["input_tokens"],
            "output_tokens": rows["output_tokens"],
            "cache_read_tokens": rows["cache_read_tokens"],
            "cache_write_tokens": rows["cache_write_tokens"],
            "reasoning_tokens": rows["reasoning_tokens"],
            "tokens": rows["input_tokens"] + rows["output_tokens"] + rows["cache_read_tokens"]
            + rows["cache_write_tokens"] + rows["reasoning_tokens"],
            "est_cost": rows["est_cost"],
            "last_activity_at": rows["last_activity_at"],
        })
        # --- cost: recompute from tokens when custom rates are configured ---
        if _RATES or _COST_MULT != 1.0:
            cost_total = 0.0
            for r in conn.execute(
                """SELECT model, input_tokens, output_tokens, cache_read_tokens,
                          cache_write_tokens, reasoning_tokens, estimated_cost_usd
                     FROM sessions WHERE started_at >= ? AND started_at <= ?""",
                (cutoff_ts, now_ts),
            ):
                cost_total += session_cost_values(
                    r["model"], r["input_tokens"], r["output_tokens"], r["cache_read_tokens"],
                    r["cache_write_tokens"], r["reasoning_tokens"], r["estimated_cost_usd"])
            out["est_cost"] = cost_total

        # last session title/model
        last = conn.execute(
            """SELECT title, model FROM sessions
               WHERE started_at >= ? AND started_at <= ?
               ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT 1""",
            (cutoff_ts, now_ts),
        ).fetchone()
        if last:
            out["last_title"] = last["title"]
            out["last_model"] = last["model"]

        # --- failed tool results (range-filtered via session join) ---
        fr = conn.execute(
            f"""SELECT COUNT(*) failed,
                       (SELECT COUNT(*) FROM messages m2 JOIN sessions s2 ON s2.id = m2.session_id
                         WHERE m2.role='tool' AND m2.content IS NOT NULL AND s2.started_at >= ? AND s2.started_at <= ?) total
                  FROM messages m JOIN sessions s ON s.id = m.session_id
                 WHERE m.role='tool' AND m.content IS NOT NULL
                   AND s.started_at >= ? AND s.started_at <= ? AND {FAILURE_SQL}""",
            (cutoff_ts, now_ts, cutoff_ts, now_ts),
        ).fetchone()
        out["failed"] = fr["failed"]
        out["tool_results"] = fr["total"]
        if fr["total"]:
            out["success_rate"] = 1.0 - fr["failed"] / fr["total"]

        # --- per-model usage (range via session join) ---
        for r in conn.execute(
            """SELECT u.model, SUM(u.api_call_count) calls, SUM(u.estimated_cost_usd) cost,
                      COALESCE(SUM(u.input_tokens),0) tin, COALESCE(SUM(u.output_tokens),0) tout,
                      COALESCE(SUM(u.cache_read_tokens),0) tread, COALESCE(SUM(u.cache_write_tokens),0) twrite,
                      COALESCE(SUM(u.reasoning_tokens),0) treason
                 FROM session_model_usage u JOIN sessions s ON s.id = u.session_id
                WHERE s.started_at >= ? AND s.started_at <= ?
                GROUP BY u.model ORDER BY cost DESC""",
            (cutoff_ts, now_ts),
        ):
            if r["calls"]:
                c = session_cost_values(r["model"], r["tin"], r["tout"], r["tread"], r["twrite"],
                                        r["treason"], r["cost"])
                out["models"].append({"model": r["model"], "calls": r["calls"], "cost": round(c, 4)})

        # --- tool usage (range via session join) ---
        for r in conn.execute(
            """SELECT m.tool_name tool, COUNT(*) cnt
                 FROM messages m JOIN sessions s ON s.id = m.session_id
                WHERE m.tool_name IS NOT NULL AND m.tool_name != '' AND s.started_at >= ? AND s.started_at <= ?
                GROUP BY m.tool_name ORDER BY cnt DESC LIMIT 12""",
            (cutoff_ts, now_ts),
        ):
            out["tools"].append({"tool": r["tool"], "count": r["cnt"]})

        # --- sessions with failures (needs attention) ---
        for r in conn.execute(
            f"""SELECT s.title, s.id, s.model, s.started_at, COUNT(*) failures
                  FROM messages m JOIN sessions s ON s.id = m.session_id
                 WHERE m.role='tool' AND m.content IS NOT NULL
                   AND s.started_at >= ? AND s.started_at <= ? AND {FAILURE_SQL}
                 GROUP BY s.id ORDER BY failures DESC LIMIT 8""",
            (cutoff_ts, now_ts),
        ):
            out["fail_sessions"].append({
                "title": r["title"], "session_id": r["id"], "model": r["model"],
                "started_at": r["started_at"], "failures": r["failures"],
            })

        # --- last activity across ALL time (for "last seen" context) ---
        row = conn.execute(
            "SELECT MAX(COALESCE(last_activity_at, started_at)) v FROM sessions"
        ).fetchone()
        out["last_seen_any"] = row["v"]
    except sqlite3.Error as e:
        out["status_detail"] = f"db error: {e}"
    finally:
        conn.close()

    # status
    la = out["last_activity_at"]
    if la:
        age = now_ts - la
        if age <= 3600:
            out["status"], out["status_detail"] = "active", "active"
        elif age <= 86400:
            h = int(age // 3600)
            out["status"], out["status_detail"] = "idle", f"idle {h}h {int((age % 3600)//60)}m"
        else:
            h = int(age // 3600)
            out["status"], out["status_detail"] = "down", f"inactive {h}h {int((age % 3600)//60)}m"
    else:
        out["status"], out["status_detail"] = "down", "no activity in window"
    if out["schema_version"] is not None and out["schema_version"] != SUPPORTED_SCHEMA:
        out["schema_warning"] = f"schema v{out['schema_version']} (dashboard built for v{SUPPORTED_SCHEMA})"

    # cron executions
    out["cron"] = {"total": 0, "completed": 0, "failed": 0, "missed": 0, "cancelled": 0, "running": 0}
    if cron_path:
        try:
            c = ro_connect(cron_path)
            row = c.execute("SELECT COUNT(*) total FROM executions").fetchone()
            out["cron"]["total"] = row["total"]
            for r in c.execute("SELECT status, COUNT(*) cnt FROM executions GROUP BY status"):
                key = r["status"] if r["status"] in out["cron"] else "other"
                out["cron"][key] = r["cnt"] if key != "other" else out["cron"].get("other", 0) + r["cnt"]
            c.close()
        except Exception:
            pass
    return out


def load_activity(profiles, state_paths, cutoff_ts, now_ts, granularity):
    """activity[profile][bucket] = session count. Returns (activity, bucket_list)."""
    activity = {}
    buckets = set()
    for profile, state_path, _ in state_paths:
        activity[profile] = {}
        try:
            conn = ro_connect(state_path)
            fmt = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"
            rows = conn.execute(
                f"""SELECT strftime('{fmt}', started_at, 'unixepoch', 'localtime') b, COUNT(*) c
                      FROM sessions WHERE started_at >= ? AND started_at <= ?
                     GROUP BY b""",
                (cutoff_ts, now_ts),
            )
            for r in rows:
                activity[profile][r["b"]] = r["c"]
                buckets.add(r["b"])
            conn.close()
        except Exception:
            pass
    return activity, sorted(buckets)


def parse_date_anchor(date_str):
    """Return (day_start_epoch, day_end_epoch, normalized) or None for invalid/empty."""
    if not date_str:
        return None
    try:
        d0 = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    start = d0.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    end = (d0 + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return start, end, date_str


def fmt_window(cutoff, end):
    cs, ce = datetime.fromtimestamp(cutoff), datetime.fromtimestamp(end)
    if (ce - cs).total_seconds() <= 90000:  # ~1 day window -> show times
        return cs.strftime("%a %d %b %H:%M") + " → " + ce.strftime("%a %d %b %H:%M")
    return cs.strftime("%a %d %b") + " → " + ce.strftime("%a %d %b")


def compute_window(range_days, date_str=None):
    """Shared window computation: (cutoff_ts, now_ts, granularity, window_label)."""
    anchor = parse_date_anchor(date_str)
    if anchor:
        date_start, date_end, _ = anchor
        now_ts = min(date_end, time.time())
        if range_days == 0:
            cutoff_ts = 0.0
        elif range_days == 1:
            cutoff_ts = date_start
        else:
            cutoff_ts = date_start - (range_days - 1) * 86400
    else:
        now_ts = time.time()
        cutoff_ts = now_ts - range_days * 86400 if range_days else 0.0
    granularity = "hour" if (range_days and range_days <= 2) else "day"
    return cutoff_ts, now_ts, granularity, fmt_window(cutoff_ts, now_ts)


def is_failed(content):
    """True if a tool result content carries an enabled failure marker."""
    if not content:
        return False
    if "success_false" in _FAIL_KINDS and '"success": false' in content:
        return True
    if "error_nonnull" in _FAIL_KINDS and '"error":' in content and '"error": null' not in content:
        return True
    if "status_error" in _FAIL_KINDS and '"status": "error"' in content:
        return True
    if "exit_code" in _FAIL_KINDS and re.search(r"EXIT_CODE=[1-9]", content) is not None:
        return True
    return any(s in content for s in _EXTRA_MARKERS)


def short_summary(content, n=110):
    if not content:
        return ""
    s = content.replace("\\n", " ").replace("\n", " ")
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


KEYFILE_PATTERNS = ["SOUL.md", "AGENTS.md", ".hermes.md", "config.yaml", "profile.yaml",
                    "memories/MEMORY.md", "memories/USER.md"]
SKILL_GLOB = "skills/**/SKILL.md"
PLUGIN_GLOB = "plugins/*/plugin.yaml"
MAX_PREVIEW = 500_000


def fmt_label(rel):
    if rel == "SOUL.md":
        return "SOUL · persona"
    if rel == "AGENTS.md":
        return "AGENTS · project rules"
    if rel == ".hermes.md":
        return ".hermes · project rules"
    if rel == "config.yaml":
        return "config.yaml"
    if rel == "profile.yaml":
        return "profile.yaml"
    if rel == "memories/MEMORY.md":
        return "memory"
    if rel == "memories/USER.md":
        return "user profile"
    return rel


def profile_base(home, profile):
    return home if profile == "default" else os.path.join(home, "profiles", profile)


def list_profile_files(home, profile):
    """Whitelisted key files for one profile (or merged when profile == 'all')."""
    if profile == "all":
        merged = {"profile": "all", "files": []}
        for name, _, _ in find_profile_dbs(home):
            sub = list_profile_files(home, name)
            for f in sub.get("files", []):
                merged["files"].append({
                    "rel": f"{name}/{f['rel']}", "label": f"{name} · {f['label']}", "kind": f["kind"],
                })
        return merged
    base = profile_base(home, profile)
    if not os.path.isdir(base):
        return {"error": "profile not found"}
    items, seen = [], set()
    for pat in KEYFILE_PATTERNS:
        for p in sorted(glob.glob(os.path.join(base, pat))):
            rel = os.path.relpath(p, base).replace(os.sep, "/")
            if rel in seen or rel.startswith(".."):
                continue
            seen.add(rel)
            items.append({"rel": rel, "label": fmt_label(rel), "kind": "soul" if rel == "SOUL.md"
                          else "config" if rel in ("config.yaml", "profile.yaml") else "memory" if rel.startswith("memories/") else "doc"})
    for p in sorted(glob.glob(os.path.join(base, SKILL_GLOB), recursive=True)):
        rel = os.path.relpath(p, base).replace(os.sep, "/")
        if rel in seen:
            continue
        seen.add(rel)
        name = os.path.basename(os.path.dirname(p))
        items.append({"rel": rel, "label": f"skill · {name}", "kind": "skill"})
    for p in sorted(glob.glob(os.path.join(base, PLUGIN_GLOB))):
        rel = os.path.relpath(p, base).replace(os.sep, "/")
        if rel in seen:
            continue
        seen.add(rel)
        name = os.path.basename(os.path.dirname(p))
        items.append({"rel": rel, "label": f"plugin · {name}", "kind": "plugin"})
    return {"profile": profile, "files": items}


def read_profile_file(home, profile, rel):
    """Read one whitelisted key file's text content."""
    if profile == "all":
        parts = rel.split("/", 1)
        if len(parts) != 2:
            return {"error": "bad path"}
        profile, rel = parts
    base = profile_base(home, profile)
    allowed = {f["rel"] for f in list_profile_files(home, profile).get("files", [])}
    if rel not in allowed:
        return {"error": "file not in whitelist"}
    abs_path = os.path.realpath(os.path.join(base, rel))
    if not abs_path.startswith(os.path.realpath(base) + os.sep) or not os.path.isfile(abs_path):
        return {"error": "not found"}
    try:
        size = os.path.getsize(abs_path)
        if size > MAX_PREVIEW:
            return {"error": f"file too large ({size} bytes, max {MAX_PREVIEW})"}
        with open(abs_path, "rb") as f:
            data = f.read()
        if b"\x00" in data[:8192]:
            return {"error": "binary file"}
        return {"path": rel, "content": data.decode("utf-8", "replace")}
    except OSError as e:
        return {"error": str(e)}


def load_tool_calls(profile, state_path, cutoff_ts, now_ts, limit=3000):
    """Per-tool-call history for one profile within the window."""
    out = {"profile": profile, "calls": [], "total": 0, "truncated": False}
    try:
        conn = ro_connect(state_path)
        out["total"] = conn.execute(
            """SELECT COALESCE(SUM(json_array_length(m.tool_calls)), 0) FROM messages m JOIN sessions s ON s.id = m.session_id
                WHERE m.role='assistant' AND m.tool_calls IS NOT NULL AND length(m.tool_calls) > 10
                  AND s.started_at >= ? AND s.started_at <= ?""",
            (cutoff_ts, now_ts),
        ).fetchone()[0]
        out["messages"] = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) FROM sessions WHERE started_at >= ? AND started_at <= ?",
            (cutoff_ts, now_ts),
        ).fetchone()[0]
        sums = conn.execute(
            """SELECT COALESCE(SUM(input_tokens),0) tin, COALESCE(SUM(output_tokens),0) tout,
                      COALESCE(SUM(estimated_cost_usd),0) cost
                 FROM sessions WHERE started_at >= ? AND started_at <= ?""",
            (cutoff_ts, now_ts),
        ).fetchone()
        out["tokens_in"] = sums["tin"]
        out["tokens_out"] = sums["tout"]
        out["est_cost"] = round(sums["cost"], 4)
        if _RATES or _COST_MULT != 1.0:
            total = 0.0
            for r in conn.execute(
                """SELECT model, input_tokens, output_tokens, cache_read_tokens,
                          cache_write_tokens, reasoning_tokens, estimated_cost_usd
                     FROM sessions WHERE started_at >= ? AND started_at <= ?""",
                (cutoff_ts, now_ts),
            ):
                total += session_cost_values(r["model"], r["input_tokens"], r["output_tokens"],
                                             r["cache_read_tokens"], r["cache_write_tokens"],
                                             r["reasoning_tokens"], r["estimated_cost_usd"])
            out["est_cost"] = round(total, 4)
        out["sessions"] = []
        for r in conn.execute(
            """SELECT s.title, s.model, COALESCE(s.message_count,0) messages,
                      COALESCE(SUM(json_array_length(m.tool_calls)),0) calls,
                      COALESCE(s.input_tokens,0) tin, COALESCE(s.output_tokens,0) tout,
                      COALESCE(s.cache_read_tokens,0) tread, COALESCE(s.cache_write_tokens,0) twrite,
                      COALESCE(s.reasoning_tokens,0) treason, COALESCE(s.estimated_cost_usd,0) cost
                 FROM sessions s
                 LEFT JOIN messages m ON m.session_id = s.id AND m.role='assistant'
                      AND m.tool_calls IS NOT NULL AND length(m.tool_calls) > 10
                WHERE s.started_at >= ? AND s.started_at <= ?
                GROUP BY s.id""",
            (cutoff_ts, now_ts),
        ):
            out["sessions"].append({
                "title": r["title"] or "",
                "messages": r["messages"],
                "calls": r["calls"],
                "tokens_in": r["tin"],
                "tokens_out": r["tout"],
                "est_cost": round(session_cost_values(r["model"], r["tin"], r["tout"], r["tread"],
                                                      r["twrite"], r["treason"], r["cost"]), 4),
            })
        rows = conn.execute(
            """SELECT m.id, m.timestamp ts, m.tool_calls tc, m.session_id sid, s.model, s.title
                 FROM messages m JOIN sessions s ON s.id = m.session_id
                WHERE m.role='assistant' AND m.tool_calls IS NOT NULL AND length(m.tool_calls) > 10
                  AND s.started_at >= ? AND s.started_at <= ?
                ORDER BY m.timestamp DESC LIMIT ?""",
            (cutoff_ts, now_ts, limit),
        ).fetchall()
        session_ids = list({r["sid"] for r in rows})
        results = {}
        if session_ids:
            qmarks = ",".join("?" * len(session_ids))
            for r in conn.execute(
                f"""SELECT tool_call_id, timestamp ts, content, tool_name
                      FROM messages WHERE role='tool' AND session_id IN ({qmarks})""",
                session_ids,
            ):
                if r["tool_call_id"] and r["tool_call_id"] not in results:
                    results[r["tool_call_id"]] = r
        for r in rows:
            try:
                calls = json.loads(r["tc"])
            except Exception:
                continue
            for c in calls:
                fn = (c.get("function") or {}).get("name", "")
                args = (c.get("function") or {}).get("arguments") or ""
                cid = c.get("call_id") or c.get("id")
                res = results.get(cid)
                content = res["content"] if res else None
                out["calls"].append({
                    "tool": res["tool_name"] if res and res["tool_name"] else fn,
                    "summary": short_summary(content),
                    "ts": r["ts"],
                    "dur": round(res["ts"] - r["ts"], 1) if res and res["ts"] and res["ts"] > r["ts"] else None,
                    "tin": len(args) // 4,          # estimate: payload chars / 4
                    "tout": (len(content) // 4) if content else 0,
                    "model": r["model"],
                    "failed": is_failed(content),
                    "sid": r["sid"],
                    "call": cid,
                    "session_title": r["title"],
                    "profile": profile,
                })
        conn.close()
    except sqlite3.Error as e:
        out["error"] = str(e)
    out["truncated"] = out["total"] > len(out["calls"])
    return out


def load_tool_message(state_path, sid, call):
    """Full raw content of one tool result message."""
    try:
        conn = ro_connect(state_path)
        row = conn.execute(
            """SELECT content, tool_name, timestamp FROM messages
                WHERE role='tool' AND session_id = ? AND tool_call_id = ? LIMIT 1""",
            (sid, call),
        ).fetchone()
        conn.close()
        if not row:
            return {"error": "message not found"}
        return {"content": row["content"] or "", "tool": row["tool_name"], "ts": row["timestamp"]}
    except sqlite3.Error as e:
        return {"error": str(e)}


def build_payload(range_days, home, date_str=None):
    cutoff_ts, now_ts, granularity, window_label = compute_window(range_days, date_str)

    profile_dbs = find_profile_dbs(home)
    profiles = []
    schema_versions = {}
    totals = {
        "sessions": 0, "tool_calls": 0, "api_calls": 0, "tokens": 0,
        "est_cost": 0.0, "failed": 0, "tool_results": 0, "active_profiles": 0,
        "cron": {"total": 0, "completed": 0, "failed": 0, "missed": 0, "cancelled": 0},
    }
    for name, state, cron in profile_dbs:
        p = load_profile(name, state, cron, cutoff_ts, now_ts)
        if p["status"] == "active":
            totals["active_profiles"] += 1
        for k in ("sessions", "tool_calls", "api_calls", "tokens", "failed", "tool_results"):
            totals[k] += p[k]
        totals["est_cost"] += p["est_cost"]
        for k in ("total", "completed", "failed", "missed", "cancelled"):
            totals["cron"][k] += p["cron"][k]
        schema_versions[name] = p.get("schema_version")
        profiles.append(p)
    totals["success_rate"] = 1.0 - totals["failed"] / totals["tool_results"] if totals["tool_results"] else 1.0

    # timeline
    activity, bucket_list = load_activity(
        [p["name"] for p in profiles], profile_dbs, cutoff_ts, now_ts, granularity
    )

    # global model & tool rollups, expensive sessions, needs-attention
    models, tools = {}, {}
    expensive, attention = [], []
    for p in profiles:
        for m in p["models"]:
            e = models.setdefault(m["model"], {"runs": 0, "cost": 0.0, "profiles": {}})
            e["runs"] += m["calls"]
            e["cost"] += m["cost"]
            e["profiles"][p["name"]] = m["calls"]
        for t in p["tools"]:
            tools[t["tool"]] = tools.get(t["tool"], 0) + t["count"]
        # expensive sessions come from per-profile query below
    # expensive sessions: reuse a dedicated query per profile db
    for name, state, _ in profile_dbs:
        try:
            conn = ro_connect(state)
            if _RATES or _COST_MULT != 1.0:
                for r in conn.execute(
                    f"""SELECT title, model, {TOKEN_SUM} tokens, estimated_cost_usd cost, started_at,
                               input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens
                          FROM sessions WHERE started_at >= ? AND started_at <= ?""",
                    (cutoff_ts, now_ts),
                ):
                    c = session_cost_values(r["model"], r["input_tokens"], r["output_tokens"],
                                            r["cache_read_tokens"], r["cache_write_tokens"],
                                            r["reasoning_tokens"], r["cost"])
                    if c > 0:
                        expensive.append({
                            "profile": name, "title": r["title"], "model": r["model"],
                            "tokens": r["tokens"], "cost": round(c, 4), "started_at": r["started_at"],
                        })
            else:
                for r in conn.execute(
                    f"""SELECT title, model, {TOKEN_SUM} tokens, estimated_cost_usd cost, started_at
                          FROM sessions WHERE started_at >= ? AND started_at <= ? AND estimated_cost_usd > 0
                         ORDER BY estimated_cost_usd DESC LIMIT 12""",
                    (cutoff_ts, now_ts),
                ):
                    expensive.append({
                        "profile": name, "title": r["title"], "model": r["model"],
                        "tokens": r["tokens"], "cost": round(r["cost"] or 0, 4), "started_at": r["started_at"],
                    })
            conn.close()
        except Exception:
            pass
    expensive.sort(key=lambda x: x["cost"], reverse=True)
    expensive = expensive[:15]

    for p in profiles:
        for fs in p["fail_sessions"]:
            attention.append({
                "profile": p["name"], "title": fs["title"], "session_id": fs["session_id"],
                "model": fs["model"], "started_at": fs["started_at"], "failures": fs["failures"],
            })
        if p["status"] == "down":
            attention.append({
                "profile": p["name"], "title": "No activity", "session_id": None,
                "model": None, "started_at": None, "failures": 0, "down": p["status_detail"],
                "last_seen": p.get("last_seen_any"),
            })
    attention.sort(key=lambda x: (-x["failures"], x["profile"]))
    attention = attention[:15]

    models_sorted = sorted(models.items(), key=lambda kv: -kv[1]["cost"])
    tools_sorted = sorted(tools.items(), key=lambda kv: -kv[1])

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "range_days": range_days,
        "date": date_str or None,
        "window_start": cutoff_ts,
        "window_end": now_ts,
        "window_label": fmt_window(cutoff_ts, now_ts),
        "cutoff_ts": cutoff_ts,
        "now_ts": now_ts,
        "granularity": granularity,
        "totals": totals,
        "schema": {
            "supported": SUPPORTED_SCHEMA,
            "profiles": schema_versions,
            "mismatch": [n for n, v in schema_versions.items() if v is not None and v != SUPPORTED_SCHEMA],
        },
        "cost": {
            "mode": "custom" if (_RATES or _COST_MULT != 1.0) else "stored",
            "multiplier": _COST_MULT,
            "rates": _RATES,
        },
        "profiles": sorted(profiles, key=lambda p: ({"active": 0, "idle": 1, "down": 2}.get(p["status"], 3), -p["est_cost"])),
        "models": [{"model": k, **v} for k, v in models_sorted],
        "tools": [{"tool": k, "count": v} for k, v in tools_sorted],
        "expensive": expensive,
        "attention": attention,
        "activity": activity,
        "buckets": bucket_list,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):
        if self.path.startswith("/api/"):
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            params = {}
            for p in q.split("&"):
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k] = unquote(v)
            try:
                rd = int(params.get("range", "7"))
            except Exception:
                rd = 7
            if rd not in (1, 2, 7, 30, 0):
                rd = 7
            ds = params.get("date", "")
            if ds and not (len(ds) == 10 and ds[4] == "-" and ds[7] == "-" and ds.replace("-", "").isdigit()):
                ds = ""
            if self.path.startswith("/api/profilefiles"):
                profile = params.get("profile", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
                    self.send_json({"error": "bad profile"}, 400)
                    return
                self.send_json(list_profile_files(HERMES_HOME, profile))
            elif self.path.startswith("/api/profilefile"):
                profile = params.get("profile", "")
                rel = params.get("file", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
                    self.send_json({"error": "bad profile"}, 400)
                    return
                if not rel or len(rel) > 500:
                    self.send_json({"error": "bad file"}, 400)
                    return
                self.send_json(read_profile_file(HERMES_HOME, profile, rel))
            elif self.path.startswith("/api/toolmessage"):
                profile = params.get("profile", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
                    self.send_json({"error": "bad profile"}, 400)
                    return
                dbs = dict((n, s) for n, s, _ in find_profile_dbs(HERMES_HOME))
                if profile not in dbs:
                    self.send_json({"error": "unknown profile"}, 404)
                    return
                sid = params.get("sid", "")
                call = params.get("call", "")
                if not sid or not call:
                    self.send_json({"error": "missing sid/call"}, 400)
                    return
                self.send_json(load_tool_message(dbs[profile], sid, call))
            elif self.path.startswith("/api/toolcalls"):
                profile = params.get("profile", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
                    self.send_json({"error": "bad profile"}, 400)
                    return
                cutoff, now, _, wlabel = compute_window(rd, ds)
                if profile == "all":
                    merged = {"profile": "all", "calls": [], "total": 0, "messages": 0,
                              "truncated": False, "window_label": wlabel}
                    for name, state, _ in find_profile_dbs(HERMES_HOME):
                        sub = load_tool_calls(name, state, cutoff, now)
                        merged["calls"].extend(sub["calls"])
                        merged["total"] += sub["total"]
                        merged["messages"] += sub.get("messages", 0)
                        merged["tokens_in"] = merged.get("tokens_in", 0) + sub.get("tokens_in", 0)
                        merged["tokens_out"] = merged.get("tokens_out", 0) + sub.get("tokens_out", 0)
                        merged["est_cost"] = round(merged.get("est_cost", 0) + sub.get("est_cost", 0), 4)
                        for s in sub.get("sessions", []):
                            merged.setdefault("sessions", []).append({
                                **s, "title": f"{name} · {s['title']}".strip(" ·"),
                            })
                        merged["truncated"] = merged["truncated"] or sub["truncated"]
                    for c in merged["calls"]:
                        bot = c.get("profile", "?")
                        title = c.get("session_title") or ""
                        c["session_title"] = f"{bot} · {title}".strip(" ·")
                    merged["calls"].sort(key=lambda c: c["ts"], reverse=True)
                    self.send_json(merged)
                    return
                dbs = dict((n, s) for n, s, _ in find_profile_dbs(HERMES_HOME))
                if profile not in dbs:
                    self.send_json({"error": "unknown profile"}, 404)
                    return
                payload = load_tool_calls(profile, dbs[profile], cutoff, now)
                payload["window_label"] = wlabel
                self.send_json(payload)
            elif self.path.startswith("/api/data"):
                payload = build_payload(rd, HERMES_HOME, ds)
                self.send_json(payload)
            else:
                self.send_json({"error": "not found"}, 404)
            return
        if self.path in ("/", "/index.html"):
            here = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(here, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    global HERMES_HOME
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--home", default=HERMES_HOME)
    args = ap.parse_args()
    HERMES_HOME = args.home
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        bound = args.port
    except OSError as e:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        bound = srv.server_address[1]
        print(f"port {args.port} busy ({e.strerror or e}); serving on {bound}", flush=True)
    print(f"Hermes Watchtower: http://127.0.0.1:{bound}  (home={HERMES_HOME})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
