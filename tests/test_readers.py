import json
import sqlite3

from conftest import T0, claude_line

from kvleak import load
from kvleak.cli import load_all, main
from kvleak.readers import default_roots, read_codex, read_gemini, read_opencode
from kvleak.sources import detect_format, load_many


def _codex_lines():
    ts = lambda s: (T0.replace(microsecond=0)).isoformat().replace("+00:00", "Z")  # noqa: E731
    info1 = {"total_token_usage": {"input_tokens": 16652, "cached_input_tokens": 12160, "cache_write_input_tokens": 0,
                                   "output_tokens": 275, "reasoning_output_tokens": 0, "total_tokens": 16927},
             "last_token_usage": {"input_tokens": 16652, "cached_input_tokens": 12160, "cache_write_input_tokens": 0,
                                  "output_tokens": 275, "reasoning_output_tokens": 0, "total_tokens": 16927}}
    info2 = {"total_token_usage": {"input_tokens": 41188, "cached_input_tokens": 24320, "cache_write_input_tokens": 100,
                                   "output_tokens": 411, "reasoning_output_tokens": 0, "total_tokens": 41599}}
    return [
        {"timestamp": ts(0), "type": "session_meta", "payload": {"id": "0199-abc", "cwd": "/home/me/src/demo-app", "cli_version": "0.162.0"}},
        {"timestamp": ts(1), "type": "turn_context", "payload": {"model": "gpt-5.6-sol", "cwd": "/home/me/src/demo-app"}},
        {"timestamp": ts(2), "type": "event_msg", "payload": {"type": "token_count", "info": None}},
        {"timestamp": ts(3), "type": "event_msg", "payload": {"type": "token_count", "info": info1}},
        {"timestamp": ts(4), "type": "event_msg", "payload": {"type": "token_count", "info": info1}},  # rate-limit twin
        {"timestamp": ts(5), "type": "event_msg", "payload": {"type": "token_count", "info": info2}},  # cumulative only
    ]


def test_codex_rollout(tmp_path):
    p = tmp_path / "sessions" / "2026" / "10" / "20" / "rollout-2026-10-20T15-07-44-0199-abc.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("\n".join(json.dumps(x) for x in _codex_lines()) + "\n")
    assert detect_format(str(p)) == "codex"
    t = read_codex(str(p))
    assert t.source == "codex" and t.session_id == "0199-abc" and t.project == "demo-app" and not t.is_subagent
    assert len(t.steps) == 2
    a, b = t.steps
    assert (a.input, a.read, a.write_5m, a.output, a.model) == (16652 - 12160, 12160, 0, 275, "gpt-5.6-sol")
    # second call = delta of the cumulative totals: input 24536 incl. 12160 cached + 100 written
    assert (b.input, b.read, b.write_5m, b.output) == (24536 - 12160 - 100, 12160, 100, 136)
    assert load(str(p)).steps == t.steps


def test_gemini_json_and_jsonl(tmp_path):
    chats = tmp_path / "tmp" / "abc123hash" / "chats"
    chats.mkdir(parents=True)
    msgs = [
        {"id": "u1", "type": "user", "timestamp": "2026-10-01T12:00:00Z"},
        {"id": "g1", "type": "gemini", "model": "gemini-3.6-pro", "timestamp": "2026-10-01T12:00:05Z",
         "tokens": {"input": 10_000, "cached": 8_000, "output": 300, "thoughts": 200, "tool": 0, "total": 10_500}},
        {"id": "i1", "type": "info"},
    ]
    doc = chats / "session-2026-10-01.json"
    doc.write_text(json.dumps({"sessionId": "sess-g", "projectHash": "abc123hash", "startTime": "2026-10-01T12:00:00Z", "messages": msgs}))
    assert detect_format(str(doc)) == "gemini"
    t = read_gemini(str(doc))
    assert t.source == "gemini" and t.session_id == "sess-g" and len(t.steps) == 1
    s = t.steps[0]
    assert (s.input, s.read, s.output, s.model) == (2_000, 8_000, 500, "gemini-3.6-pro")
    lines = [{"sessionId": "sess-h", "startTime": "2026-10-01T12:00:00Z", "projectHash": "abc123hash"}, {"$set": {"x": 1}}] + msgs
    jl = chats / "session-2026-10-02.jsonl"
    jl.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    t2 = read_gemini(str(jl))
    assert t2.session_id == "sess-h" and len(t2.steps) == 1 and t2.steps[0].read == 8_000
    assert detect_format(str(tmp_path / "tmp" / "logs.json")) == "skip"


def _opencode_db(path, v2=False):
    con = sqlite3.connect(str(path))
    con.execute("create table session (id text, parent_id text, directory text, title text, time_created integer)")
    con.execute("insert into session values ('s1', NULL, '/home/me/src/demo-app', 'fix', 1)")
    con.execute("insert into session values ('s2', 's1', '/home/me/src/demo-app', 'child', 2)")
    tok = {"input": 1_000, "output": 200, "reasoning": 50, "cache": {"read": 50_000, "write": 3_000}}
    ts = int(T0.timestamp() * 1000)
    if v2:
        con.execute("create table session_v2 (id text, parent_id text, directory text, title text)")
        con.execute("insert into session_v2 values ('s1', NULL, '/home/me/src/demo-app', 'fix')")
        con.execute("insert into session_v2 values ('s2', 's1', '/home/me/src/demo-app', 'child')")
        con.execute("create table session_message (session_id text, id text, type text, seq integer, time_created integer, data blob)")
        con.execute("insert into session_message values ('s1', 'm1', 'user', 0, ?, ?)", (ts, json.dumps({"x": 1})))
        con.execute("insert into session_message values ('s1', 'm2', 'assistant', 1, ?, ?)",
                    (ts + 1000, json.dumps({"model": {"id": "claude-opus-5-5", "providerID": "anthropic"}, "tokens": tok, "cost": 0.1})))
        con.execute("insert into session_message values ('s2', 'm3', 'assistant', 0, ?, ?)",
                    (ts + 2000, json.dumps({"model": {"id": "claude-sonnet-5", "providerID": "anthropic"}, "tokens": tok})))
    else:
        con.execute("create table message (id text, session_id text, time_created integer, time_updated integer, data text)")
        con.execute("insert into message values ('m1', 's1', ?, ?, ?)", (ts, ts, json.dumps({"role": "user"})))
        con.execute("insert into message values ('m2', 's1', ?, ?, ?)",
                    (ts + 1000, ts, json.dumps({"role": "assistant", "modelID": "claude-opus-5-5", "providerID": "anthropic", "tokens": tok})))
        con.execute("insert into message values ('m3', 's2', ?, ?, ?)",
                    (ts + 2000, ts, json.dumps({"role": "assistant", "modelID": "claude-sonnet-5", "tokens": tok})))
    con.commit()
    con.close()


def test_opencode_sqlite_1x_and_2x(tmp_path):
    for v2 in (False, True):
        db = tmp_path / ("v2" if v2 else "v1") / "opencode.db"
        db.parent.mkdir()
        _opencode_db(db, v2)
        assert detect_format(str(db)) == "opencode"
        ts = {t.session_id: t for t in read_opencode(str(db))}
        assert set(ts) == {"s1", "s2"}
        assert ts["s1"].project == "demo-app" and not ts["s1"].is_subagent and ts["s2"].is_subagent
        s = ts["s1"].steps[0]
        assert (s.input, s.read, s.write_5m, s.output, s.model) == (1_000, 50_000, 3_000, 250, "claude-opus-5-5")
        assert s.ts == T0.replace(microsecond=0) or abs((s.ts - T0).total_seconds()) < 2
        assert len(load_many(str(db))) == 2
    (tmp_path / "other.db").write_bytes(b"not a database")
    assert detect_format(str(tmp_path / "other.db")) == "skip"


def test_default_roots_and_audit_across_tools(tmp_path, monkeypatch, capsys, write_jsonl):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini"))
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "opencode"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    write_jsonl([claude_line(i, i * 30, read=100_000 if i else 0, w1h=0 if i else 100_000) for i in range(6)])
    p = tmp_path / "codex" / "sessions" / "2026" / "10" / "20" / "rollout-x.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("\n".join(json.dumps(x) for x in _codex_lines()) + "\n")
    (tmp_path / "opencode").mkdir()
    _opencode_db(tmp_path / "opencode" / "opencode.db")
    roots = default_roots()
    assert str(tmp_path / "projects") in roots and str(p.parents[3]) in roots and str(tmp_path / "opencode") in roots
    ts = load_all(roots, "auto", None, None)
    assert sorted({t.source for t in ts}) == ["claude", "codex", "opencode"]
    assert main(["audit", "--no-color", "--path", str(tmp_path / "opencode"), "--path", str(p)]) == 0
    out = capsys.readouterr().out
    assert "Output spend" in out and "whole bill" in out
    assert main(["whatif", "--no-color", "--path", str(tmp_path / "projects"), "--path", str(p)]) == 0
    out = capsys.readouterr().out
    assert "Bill =" in out and "rtk" in out and "kvleak tune --apply" in out
