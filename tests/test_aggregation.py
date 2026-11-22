#!/usr/bin/env python3
"""Regression tests for the Hermes Operate dashboard aggregation.

Run:  python3 tests/test_aggregation.py

Builds a synthetic Hermes home in a temp dir and asserts aggregation,
failure detection, whitelist/security, and schema-guard behavior.
No third-party dependencies (stdlib only).
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("server", os.path.join(ROOT, "server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

NOW = time.time()
DAY = 86400
checks = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def make_db(path, schema_version=33):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE schema_version (version INTEGER)")
    c.execute("INSERT INTO schema_version VALUES (?)", (schema_version,))
    c.execute("""CREATE TABLE sessions (
        id TEXT PRIMARY KEY, started_at REAL, last_activity_at REAL, title TEXT, model TEXT,
        input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
        cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
        reasoning_tokens INTEGER DEFAULT 0, estimated_cost_usd REAL DEFAULT 0,
        tool_call_count INTEGER DEFAULT 0, message_count INTEGER DEFAULT 0,
        api_call_count INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,
        tool_calls TEXT, tool_call_id TEXT, tool_name TEXT, timestamp REAL)""")
    c.execute("""CREATE TABLE session_model_usage (
        session_id TEXT, model TEXT, api_call_count INTEGER DEFAULT 0, estimated_cost_usd REAL DEFAULT 0)""")
    conn.commit()
    return conn


def add_session(conn, sid, days_ago, cost=1.0, tool_calls=0, msgs=5, model="deepseek/deepseek-v4-flash"):
    conn.cursor().execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, NOW - days_ago * DAY, NOW - days_ago * DAY + 100, f"session {days_ago}", model,
         1000, 500, 200, 50, 10, cost, tool_calls, msgs, 2))
    return sid


def add_assistant_call(conn, sid, call_id, ts, tool="terminal", args='{"cmd": "ls"}'):
    calls = json.dumps([{"id": call_id, "call_id": call_id, "type": "function",
                         "function": {"name": tool, "arguments": args}}])
    conn.cursor().execute(
        "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, tool_name, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        (sid, "assistant", None, calls, None, None, ts))


def add_tool_result(conn, sid, call_id, ts, content, tool="terminal"):
    conn.cursor().execute(
        "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, tool_name, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        (sid, "tool", content, None, call_id, tool, ts))


def build_home(tmp):
    home = os.path.join(tmp, "hermes_home")
    os.makedirs(os.path.join(home, "profiles", "testbot", "skills", "foo"), exist_ok=True)

    d = make_db(os.path.join(home, "state.db"))
    add_session(d, "s1", 1, 0.5, tool_calls=1, msgs=4)
    add_assistant_call(d, "s1", "call_d1", NOW - DAY + 10)
    add_tool_result(d, "s1", "call_d1", NOW - DAY + 11, '{"output": "ok"}EXIT_CODE=0')
    d.commit()
    d.close()

    t = make_db(os.path.join(home, "profiles", "testbot", "state.db"))
    add_session(t, "t1", 0, 1.0, tool_calls=3, msgs=10)
    add_session(t, "t2", 2, 2.0, tool_calls=1, msgs=6)
    add_assistant_call(t, "t1", "call_1", NOW - 1000)
    add_tool_result(t, "t1", "call_1", NOW - 999, '{"success": true, "error": null}')
    add_assistant_call(t, "t1", "call_2", NOW - 800, tool="read_file")
    add_tool_result(t, "t1", "call_2", NOW - 799, '{"success": false, "error": "boom"}', tool="read_file")
    add_assistant_call(t, "t1", "call_3", NOW - 600, tool="patch")
    add_tool_result(t, "t1", "call_3", NOW - 599, '{"status": "error", "output": "x"}', tool="patch")
    add_assistant_call(t, "t2", "call_4", NOW - 2 * DAY + 10)
    add_tool_result(t, "t2", "call_4", NOW - 2 * DAY + 11, "some text EXIT_CODE=1")
    add_assistant_call(t, "t2", "call_5", NOW - 2 * DAY + 20, tool="skill_view", args='{"name": "foo"}')
    add_tool_result(t, "t2", "call_5", NOW - 2 * DAY + 21, '{"ok": true}', tool="skill_view")
    t.cursor().executemany(
        "INSERT INTO session_model_usage VALUES (?,?,?,?)",
        [("t1", "deepseek/deepseek-v4-flash", 5, 1.0), ("t2", "deepseek/deepseek-v4-flash", 1, 0.5)])
    t.commit()
    t.close()

    with open(os.path.join(home, "profiles", "testbot", "SOUL.md"), "w") as f:
        f.write("# Testbot Soul\npersona")
    with open(os.path.join(home, "profiles", "testbot", "skills", "foo", "SKILL.md"), "w") as f:
        f.write("---\nname: foo\n---\n# Foo skill\n")
    with open(os.path.join(home, "profiles", "testbot", ".env"), "w") as f:
        f.write("SECRET=shhh\n")
    return home


def main():
    # tests must be deterministic regardless of a local config.json
    saved_rates, saved_mult = server._RATES, server._COST_MULT
    server._RATES, server._COST_MULT = {}, 1.0
    with tempfile.TemporaryDirectory() as tmp:
        home = build_home(tmp)
        p = server.build_payload(7, home)
        t = p["totals"]
        check("totals.sessions == 3", t["sessions"] == 3, str(t["sessions"]))
        check("totals.est_cost == 3.5", abs(t["est_cost"] - 3.5) < 1e-9, str(t["est_cost"]))
        check("totals.failed == 3", t["failed"] == 3, str(t["failed"]))
        check("totals.tool_results == 6", t["tool_results"] == 6, str(t["tool_results"]))
        check("success_rate == 0.5", abs(t["success_rate"] - 0.5) < 1e-9, str(t["success_rate"]))
        check("schema.mismatch empty", p["schema"]["mismatch"] == [], str(p["schema"]))
        tb = next(x for x in p["profiles"] if x["name"] == "testbot")
        check("testbot failed == 3", tb["failed"] == 3, str(tb["failed"]))

        calls = server.load_tool_calls("testbot", os.path.join(home, "profiles", "testbot", "state.db"), 0, NOW + 1)
        check("toolcalls rows == 5", len(calls["calls"]) == 5, str(len(calls["calls"])))
        check("toolcalls failed == 3", sum(1 for c in calls["calls"] if c["failed"]) == 3)
        check("toolcalls profile field", all(c["profile"] == "testbot" for c in calls["calls"]))
        check("toolcalls session_title", any(c["session_title"] == "session 0" for c in calls["calls"]))
        dur = next(c["dur"] for c in calls["calls"] if c["call"] == "call_1")
        check("duration ~1s", dur is not None and abs(dur - 1.0) < 0.01, str(dur))

        files = server.list_profile_files(home, "testbot")["files"]
        rels = {f["rel"] for f in files}
        check("files includes SOUL", "SOUL.md" in rels)
        check("files includes skill", any(r.endswith("SKILL.md") for r in rels))
        check("files excludes .env", ".env" not in rels)
        merged = server.list_profile_files(home, "all")["files"]
        check("all files bot-prefixed", any(f["rel"].startswith("testbot/") for f in merged))

        check("read SOUL ok", "persona" in server.read_profile_file(home, "testbot", "SOUL.md").get("content", ""))
        check("read .env rejected", "whitelist" in server.read_profile_file(home, "testbot", ".env").get("error", ""))
        check("traversal rejected", "whitelist" in server.read_profile_file(home, "testbot", "../.env").get("error", ""))

        check("is_failed success:false", server.is_failed('{"success": false}'))
        check("is_failed error nonnull", server.is_failed('{"error": "x"}'))
        check("is_failed status error", server.is_failed('{"status": "error"}'))
        check("is_failed exit code", server.is_failed("x EXIT_CODE=2 y"))
        check("is_failed error null false", not server.is_failed('{"error": null}'))
        check("is_failed empty false", not server.is_failed(""))

        sql = server.build_failure_sql([], ["boom%100"])
        conn = sqlite3.connect(f"file:{os.path.join(home, 'profiles', 'testbot', 'state.db')}?mode=ro", uri=True)
        n = conn.execute("SELECT COUNT(*) FROM messages m WHERE role='tool' AND " + sql).fetchone()[0]
        conn.close()
        check("extra marker SQL literal match", n == 0, str(n))  # no 'boom%100' literal in fixture

        os.environ["HO_FAIL_MARKERS"] = "success_false"
        check("fail_marker_config respects env", server.fail_marker_config() == ["success_false"])
        del os.environ["HO_FAIL_MARKERS"]
        os.environ["HO_EXTRA_FAIL_MARKERS"] = "boom|oops"
        check("extra_fail_markers parses", server.extra_fail_markers() == ["boom", "oops"])
        del os.environ["HO_EXTRA_FAIL_MARKERS"]
        check("fail_marker_config default", server.fail_marker_config() == server._DEFAULT_FAIL_KINDS)

        # custom rate recompute + calibration multiplier
        rates = {"deepseek/deepseek-v4-flash": {"input": 0.09, "output": 0.18, "cache_read": 0.018}}
        server._RATES = rates
        c = server.session_cost_values("deepseek/deepseek-v4-flash", 1000, 500, 200, 50, 10, 999.0)
        check("custom rate recompute", abs(c - 0.0001854) < 1e-9, str(c))
        server._COST_MULT = 2.0
        c2 = server.session_cost_values("deepseek/deepseek-v4-flash", 1000, 500, 200, 50, 10, 999.0)
        check("multiplier applied", abs(c2 - 0.0003708) < 1e-9, str(c2))
        server._COST_MULT = 1.0
        p3 = server.build_payload(7, home)
        check("recomputed totals", abs(p3["totals"]["est_cost"] - 0.0005562) < 1e-6, str(p3["totals"]["est_cost"]))
        check("custom mode reported", p3["cost"]["mode"] == "custom")
        server._RATES = {}

        bad = os.path.join(home, "profiles", "oldbot")
        os.makedirs(bad)
        b = make_db(os.path.join(bad, "state.db"), schema_version=99)
        add_session(b, "o1", 3, 0.1)
        b.close()
        p2 = server.build_payload(7, home)
        check("schema mismatch flagged", "oldbot" in p2["schema"]["mismatch"], str(p2["schema"]["mismatch"]))
        ob = next(x for x in p2["profiles"] if x["name"] == "oldbot")
        check("oldbot schema_warning", "schema v99" in ob.get("schema_warning", ""), str(ob.get("schema_warning")))

    failed = [n for n, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
