import io
import json

from conftest import T0, claude_line

from kvleak import hooks
from kvleak.cli import main
from kvleak.diet import CHAIN_MIN, read_diet
from kvleak.refine import Rule, apply, block_re, probe, propose, remove, render


def _payload(stdout, stderr="", sid="sess-h", tid="toolu_1"):
    return {"session_id": sid, "tool_name": "Bash", "tool_use_id": tid, "tool_input": {"command": "pytest -q"},
            "tool_response": {"stdout": stdout, "stderr": stderr, "interrupted": False, "isImage": False}}


def test_condense_keeps_head_tail_and_error_lines():
    lines = ["ok %d" % i for i in range(300)] + ["Traceback (most recent call last):", "ValueError: boom"] + ["tail %d" % i for i in range(300)]
    view, stats = hooks.condense("\n".join(lines), 3000, "/tmp/full.txt")
    assert len(view) <= 3200
    assert view.startswith("ok 0\n") and view.endswith("tail 299")
    assert "ValueError: boom" in view and "Traceback" in view
    assert "/tmp/full.txt" in view and "omitted" in view
    assert stats["flagged"] >= 2 and stats["omitted"] > 300
    short, st = hooks.condense("tiny", 3000)
    assert short == "tiny" and st["omitted"] == 0


def test_condense_collapses_repeats_and_progress_bars():
    text = "\n".join(["same line"] * 50 + ["10%\r50%\r100% done"] + ["x%d" % i for i in range(2000)])
    view, _ = hooks.condense(text, 2000)
    assert "same line   (×50)" in view
    assert "100% done" in view and "\r" not in view


def test_post_tool_archives_and_condenses(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert hooks.hook_post_tool(_payload("short"), 8000) is None
    out = hooks.hook_post_tool(_payload("\n".join("row %d" % i for i in range(5000)), "warn: x"), 4000)
    upd = out["hookSpecificOutput"]["updatedToolOutput"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert upd["interrupted"] is False and upd["isImage"] is False
    assert len(upd["stdout"]) < 4000 and "row 0" in upd["stdout"] and "row 4999" in upd["stdout"]
    full = tmp_path / "kvleak" / "outputs" / "sess-h" / "toolu_1.txt"
    assert full.exists() and "row 4999" in full.read_text() and full.read_text().startswith("$ pytest -q")
    assert str(full) in upd["stdout"]
    assert hooks.hook_post_tool({"tool_name": "Read", "tool_response": {"x": "y" * 99999}}, 10) is None


def _session_lines():
    lines = []
    user = {"type": "user", "sessionId": "sess-h", "timestamp": T0.isoformat(), "message": {"role": "user", "content": "Fix the tests"}}
    lines.append(user)
    for i in range(4):
        a = claude_line(i, 10 * i, read=20000, inp=100, model="claude-opus-5-5")
        a["message"]["content"] = [{"type": "tool_use", "id": "toolu_%d" % i, "name": "Bash", "input": {"command": "pytest -k t%d" % i}}]
        lines.append(a)
        lines.append({"type": "user", "sessionId": "sess-h", "timestamp": T0.isoformat(),
                      "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_%d" % i,
                                                               "content": "FAILED t%d - AssertionError needle%d" % (i, i)}]}})
    lines.append({"type": "user", "sessionId": "sess-h", "timestamp": T0.isoformat(), "message": {"role": "user", "content": "now the docs"}})
    return lines


def test_archive_recall_and_session_start(tmp_path, write_jsonl, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    path = write_jsonl(_session_lines(), name="sess-h.jsonl")
    hooks.hook_pre_compact({"session_id": "sess-h", "transcript_path": str(path), "trigger": "auto"})
    hist, added = hooks.archive("sess-h", str(path))
    assert added == 0  # second run archives nothing new
    records = hooks.load_history("sess-h")
    assert [r["kind"] for r in records][:3] == ["user", "call", "result"]
    assert sum(r["kind"] == "result" for r in records) == 4
    hooks.hook_post_compact({"session_id": "sess-h", "compact_summary": "we fixed t0..t3"})
    assert (tmp_path / "kvleak" / "history" / "sess-h" / "summary-1.md").read_text() == "we fixed t0..t3"

    inject = hooks.hook_session_start({"session_id": "sess-h", "source": "compact"})["hookSpecificOutput"]["additionalContext"]
    assert "kvleak recall" in inject and "Fix the tests" in inject and "[4 calls: Bash×4]" in inject
    assert hooks.hook_session_start({"session_id": "sess-h", "source": "startup"}) is None
    assert hooks.hook_session_start({"session_id": "nope", "source": "compact"}) is None

    found = hooks.recall("sess-h", "needle2")
    assert "#" in found and "needle2" in found and "needle1" not in found
    assert hooks.recall("sess-h", show=records[2]["i"]).endswith("needle0")
    assert "no match" in hooks.recall("sess-h", "zzz-absent")
    assert hooks.resolve_session("sess") == "sess-h" and hooks.resolve_session(None) == "sess-h"


def test_hook_cli_never_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert main(["hook", "post-tool"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s", "transcript_path": "/nonexistent"})))
    assert main(["hook", "pre-compact"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_payload("x" * 20000))))
    assert main(["hook", "post-tool", "--max-chars", "5000"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["hookSpecificOutput"]["updatedToolOutput"]["stdout"]) < 5000


def test_hooks_install_uninstall(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "mine.sh"}]}]}}))
    assert main(["hooks", "--install", "--max-chars", "6000"]) == 0
    assert main(["hooks", "--install"]) == 0  # idempotent
    data = json.loads(settings.read_text())
    assert len(data["hooks"]["PostToolUse"]) == 2 and data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "mine.sh"
    kv = data["hooks"]["PostToolUse"][1]
    assert kv["matcher"] == "Bash" and kv["hooks"][0]["command"].endswith("hook post-tool --max-chars 8000")
    assert data["hooks"]["SessionStart"][0]["matcher"] == "compact"
    assert set(data["hooks"]) == {"PostToolUse", "PreCompact", "PostCompact", "SessionStart"}
    assert main(["hooks"]) == 0 and "PreCompact" in capsys.readouterr().out
    assert main(["hooks", "--uninstall"]) == 0
    data = json.loads(settings.read_text())
    assert data["hooks"] == {"PostToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "mine.sh"}]}]}
    assert main(["recall"]) == 1  # nothing archived


# ----------------------------------------------------------------------- chains


def _chain_session(n_mech, thinking=0, text=""):
    lines = [{"type": "user", "sessionId": "s", "timestamp": T0.isoformat(), "message": {"role": "user", "content": "go"}}]
    for i in range(n_mech):
        a = claude_line(i, 10 * i, read=100_000, inp=100, model="claude-opus-5-5")
        a["message"]["usage"]["output_tokens_details"] = {"thinking_tokens": thinking}
        a["message"]["content"] = [{"type": "tool_use", "id": "t%d" % i, "name": "Bash", "input": {"command": "ls dir%d" % i}}]
        if text:
            a["message"]["content"].insert(0, {"type": "text", "text": text})
        lines.append(a)
        lines.append({"type": "user", "sessionId": "s", "timestamp": T0.isoformat(),
                      "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t%d" % i, "content": "a\nb"}]}})
    return lines


def test_mechanical_chains(write_jsonl):
    d = read_diet(str(write_jsonl(_chain_session(5), name="c1.jsonl")))
    assert len(d.call_list) == 5 and all(c.mechanical for c in d.call_list)
    assert len(d.chains) == 1 and d.chains[0].calls == 5 and d.chains[0].context == 100_100
    assert d.chains[0].labels[:2] == ["ls dir0", "ls dir1"]
    # (calls − 1) re-reads of a 100k context at the Opus 5.5 effective read price
    assert 0.1 < d.chains[0].saving_usd < 0.3
    # real decisions (thinking) between steps break the chain
    d2 = read_diet(str(write_jsonl(_chain_session(5, thinking=5000), name="c2.jsonl")))
    assert not d2.chains
    d3 = read_diet(str(write_jsonl(_chain_session(CHAIN_MIN - 1), name="c3.jsonl")))
    assert not d3.chains


# ----------------------------------------------------------------------- refine


def test_refine_rules_block_and_probe(tmp_path):
    rules = [Rule("shell-output", "Pipe it.", "270 results", 32.0, "bash_result_tokens_per_result"),
             Rule("batch-steps", "Batch it.", "493 chains", 160.0, "chain_call_share")]
    block = render(rules, "30d")
    assert block.startswith("<!-- kvleak:begin") and block.rstrip().endswith("<!-- kvleak:end -->")
    md = tmp_path / "CLAUDE.md"
    md.write_text("# mine\n\nkeep this\n")
    apply(md, block)
    text = md.read_text()
    assert text.startswith("# mine\n\nkeep this\n\n<!-- kvleak:begin") and "- Pipe it. _(kvleak: 270 results)_" in text
    apply(md, render(rules[:1], "7d"))  # replaces, does not duplicate
    assert md.read_text().count("kvleak:begin") == 1 and "Batch it." not in md.read_text()
    assert (tmp_path / "CLAUDE.md.kvleak.bak").exists()
    assert remove(md) and md.read_text() == "# mine\n\nkeep this\n" and not block_re().search(md.read_text())
    assert not remove(md)

    state = {"rules": [{"id": r.id, "metric": r.metric} for r in rules],
             "baseline": {"bash_result_tokens_per_result": 1000.0, "chain_call_share": 0.20}}
    contrasts = probe(state, {"bash_result_tokens_per_result": 600.0, "chain_call_share": 0.25})
    assert contrasts[0].verdict.startswith("kept") and "worse" in contrasts[1].verdict


def test_refine_gating_and_cli(write_jsonl, tmp_path, monkeypatch, capsys):
    from kvleak.diet import aggregate
    from kvleak.sources import read_claude

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    paths = [write_jsonl(_chain_session(12), name="r%d.jsonl" % k) for k in range(3)]
    agg = aggregate([read_claude(str(p)) for p in paths])
    assert propose(agg, 0.0, min_usd=1000).__len__() == 0  # nothing clears a $1000 bar
    rules = propose(agg, 0.0, min_usd=0.1)
    assert [r.id for r in rules][:1] == ["batch-steps"]

    target = tmp_path / "CLAUDE.md"
    root = str(paths[0].parent)
    assert main(["refine", "--path", root, "--min-usd", "0.1", "--no-color"]) == 0
    assert "batch-steps" in capsys.readouterr().out and not target.exists()
    assert main(["refine", "--path", root, "--min-usd", "0.1", "--apply", "--file", str(target), "--no-color"]) == 0
    assert "kvleak:begin" in target.read_text()
    assert main(["refine", "--path", root, "--probe", "--no-color"]) == 0  # too few sessions since: says so, exit 0
    assert "probe needs 3" in capsys.readouterr().out
    assert main(["refine", "--remove", "--file", str(target)]) == 0 and "kvleak" not in target.read_text()
